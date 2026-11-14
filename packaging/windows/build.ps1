#Requires -Version 7.2
<#
.SYNOPSIS
Build the QEcho Windows x64 installer using Python 3.12, uv and Inno Setup 6.5+.
.DESCRIPTION
By default, builds an unsigned preview from the project version in pyproject.toml.
Use -Release with a trusted external -SigningScript after checking out the matching
vMAJOR.MINOR.PATCH tag in a clean working tree. The installer and its update manifest
are written to dist/windows/release/<version>. Publication is a separate action.
The signer receives -FilePath, signs in place using SHA-256 and a trusted timestamp,
and fails on any signing error. Keep the signing script and credentials outside the repo.
Use -TestSigning only on an isolated Windows CI runner.
.PARAMETER Version
Override the version of a preview or test build. A release build requires a matching tag
and project version.
.PARAMETER IsccPath
Path to ISCC.exe. The default looks on PATH and in the standard installation directories.
.EXAMPLE
pwsh -File packaging/windows/build.ps1
.EXAMPLE
pwsh -File packaging/windows/build.ps1 -Release -SigningScript C:/signing/sign-qecho.ps1
#>
[CmdletBinding(DefaultParameterSetName = 'Preview')]
param(
    [string] $Version,
    [string] $IsccPath,
    [Parameter(Mandatory, ParameterSetName = 'Release')] [switch] $Release,
    [Parameter(Mandatory, ParameterSetName = 'Release')] [string] $SigningScript,
    [Parameter(Mandatory, ParameterSetName = 'Test')] [switch] $TestSigning
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
if (-not $IsWindows -or [System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne 'X64') {
    throw 'Build QEcho on x64 Windows.'
}

function Invoke-Native {
    param([string] $Command, [string[]] $Arguments)
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command failed with exit code $LASTEXITCODE."
    }
}

function Get-VerifiedSignature {
    param([string] $Path, [switch] $RequireTimestamp)
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate) {
        throw "Authenticode verification failed for $([IO.Path]::GetFileName($Path)): $($signature.Status)."
    }
    if ($RequireTimestamp -and $null -eq $signature.TimeStamperCertificate) {
        throw "A trusted timestamp is required for $([IO.Path]::GetFileName($Path))."
    }
    return $signature
}

if (-not $IsccPath) {
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) {
        $IsccPath = $command.Source
    } else {
        $candidates = @(
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "$env:ProgramFiles\Inno Setup 7\ISCC.exe",
            "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe"
        )
        $IsccPath = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    }
}
if (-not $IsccPath -or -not (Test-Path -LiteralPath $IsccPath -PathType Leaf)) {
    throw 'Install Inno Setup 6.5+ or pass -IsccPath with the path to ISCC.exe.'
}
$IsccPath = (Resolve-Path -LiteralPath $IsccPath).Path

$repo = (Resolve-Path "$PSScriptRoot/../..").Path
$work = $null
Push-Location -LiteralPath $repo
try {
    $config = Get-Content pyproject.toml -Raw
    $versionMatch = [regex]::Match($config, '(?m)^version\s*=\s*"([^"]+)"\s*$')
    if (-not $versionMatch.Success) { throw 'Version is missing from pyproject.toml.' }
    $projectVersion = $versionMatch.Groups[1].Value
    if (-not $Version) { $Version = $projectVersion }
    if ($Version -cnotmatch '^(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})$' -or
        ($Version.Split('.') | Where-Object { [int] $_ -gt 65535 })) {
        throw 'Use a MAJOR.MINOR.PATCH version with components between 0 and 65535.'
    }

    $channel = 'preview'
    if ($Release) {
        if ($Version -cne $projectVersion) { throw 'Release version must match pyproject.toml.' }
        $tagCommit = Invoke-Native git @('rev-parse', '--verify', "refs/tags/v$Version^{commit}")
        $headCommit = Invoke-Native git @('rev-parse', 'HEAD')
        if ($tagCommit -cne $headCommit -or (Invoke-Native git @('status', '--porcelain'))) {
            throw "Check out the original v$Version tag in a clean working tree."
        }
        $SigningScript = (Resolve-Path -LiteralPath $SigningScript).Path
        $channel = 'release'
    } elseif ($TestSigning) {
        if ($env:GITHUB_ACTIONS -cne 'true' -or -not $env:QECHO_TEST_SIGNER_THUMBPRINT) {
            throw 'Test signing requires the isolated GitHub Actions signer.'
        }
        $SigningScript = Join-Path $PSScriptRoot 'sign-test.ps1'
        $channel = 'test'
    }

    $output = Join-Path $repo "dist/windows/$channel/$Version"
    $work = Join-Path ([IO.Path]::GetTempPath()) "QEcho-build-$([Guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Path $work | Out-Null
    $versionFile = Join-Path $work 'version.txt'
    Set-Content -LiteralPath $versionFile -Value $Version -Encoding utf8NoBOM
    $env:QECHO_BUILD_VERSION_FILE = $versionFile

    Invoke-Native uv @('sync', '--locked', '--no-dev', '--python', '3.12')
    Invoke-Native uv @('run', '--no-sync', '--with', 'pyinstaller==6.22.3', 'python', '-m', 'PyInstaller',
        '--noconfirm', '--workpath', "$work/pyinstaller", '--distpath', "$work/frozen", "$PSScriptRoot/QEcho.spec")

    $bundle = Join-Path $work 'frozen/QEcho'
    $client = Join-Path $bundle 'QEcho.exe'
    $compilerArgs = @("/DAppVersion=$Version", "/DBundleDir=$bundle", "/O$work")
    if ($SigningScript) {
        & $SigningScript -FilePath $client
        $clientSignature = Get-VerifiedSignature $client -RequireTimestamp:$Release
        # ISCC substitutes $q for quotes and $f for its current file path.
        $pwsh = Join-Path $PSHOME 'pwsh.exe'
        $signTool = ('-sQEchoReleaseSigner=$q{0}$q -NoProfile -NonInteractive -File $q{1}$q -FilePath $f' -f
            $pwsh.Replace('$', '$$'), $SigningScript.Replace('$', '$$'))
        $compilerArgs += @('/DSignBuild=1', $signTool)
    }
    $compilerArgs += Join-Path $PSScriptRoot 'installer.iss'
    Invoke-Native $IsccPath $compilerArgs

    $setupName = "QEcho-Setup-$Version-x64.exe"
    $setupPath = Join-Path $work $setupName
    if (-not (Test-Path -LiteralPath $setupPath -PathType Leaf)) { throw 'The compiled installer is missing.' }
    if ($SigningScript) {
        $setupSignature = Get-VerifiedSignature $setupPath -RequireTimestamp:$Release
        if ($clientSignature.SignerCertificate.Thumbprint -cne $setupSignature.SignerCertificate.Thumbprint) {
            throw 'The client and installer must use the same signing certificate.'
        }
    }

    New-Item -ItemType Directory -Path $output -Force | Out-Null
    Copy-Item -LiteralPath $setupPath -Destination $output -Force
    if ($SigningScript) {
        [ordered] @{
            version = $Version
            size = (Get-Item -LiteralPath $setupPath).Length
            sha256 = (Get-FileHash -LiteralPath $setupPath -Algorithm SHA256).Hash.ToLowerInvariant()
        } | ConvertTo-Json -Compress | Set-Content -LiteralPath "$output/windows-x64.json" -Encoding utf8NoBOM
    }
    Write-Host "Installer ready at $output/$setupName"
} finally {
    Remove-Item Env:QECHO_BUILD_VERSION_FILE -ErrorAction SilentlyContinue
    if ($work -and (Test-Path -LiteralPath $work)) {
        Remove-Item -LiteralPath $work -Recurse -Force
    }
    Pop-Location
}
