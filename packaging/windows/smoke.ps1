#Requires -Version 7.2
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $Version,
    [string] $UpgradeVersion
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$root = (Resolve-Path "$PSScriptRoot/../..").Path
$testRoot = Join-Path $env:RUNNER_TEMP "QEcho smoke $([Guid]::NewGuid().ToString('N'))"
$installDir = Join-Path $testRoot 'Application'
$dataDir = Join-Path $testRoot 'User data'
$qechoExe = Join-Path $installDir 'QEcho.exe'
$config = Join-Path $dataDir 'qecho.toml'
$db = Join-Path $dataDir '.data/qecho.sqlite3'
$artifactDir = if ($UpgradeVersion) { "$root/dist/windows/test" } else { "$root/dist/windows/preview" }
$silent = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-'

function Invoke-TestProcess {
    param([string] $Path, [string] $Arguments, [int] $TimeoutSeconds = 90)
    $process = Start-Process -FilePath $Path -ArgumentList $Arguments -PassThru
    try {
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $process.Kill($true)
            throw "Process timed out: $([IO.Path]::GetFileName($Path))."
        }
        if ($process.ExitCode -ne 0) {
            throw "$([IO.Path]::GetFileName($Path)) failed with exit code $($process.ExitCode)."
        }
    } finally {
        $process.Dispose()
    }
}

function Test-InstalledClient {
    param([string] $ExpectedVersion)
    $resultPath = Join-Path $dataDir 'smoke-result.json'
    Remove-Item -LiteralPath $resultPath -ErrorAction SilentlyContinue
    Invoke-TestProcess $qechoExe ('--smoke-test --data-dir "{0}"' -f $dataDir)
    $result = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
    if ($result.health -cne 'ok' -or $result.version -cne $ExpectedVersion -or
        -not (Test-Path -LiteralPath $config) -or -not (Test-Path -LiteralPath $db)) {
        throw "Installed QEcho $ExpectedVersion failed its runtime smoke test."
    }
    Write-Host "QEcho $ExpectedVersion served /healthz and initialized its configuration and database."
}

Push-Location -LiteralPath $root
try {
    New-Item -ItemType Directory -Path $testRoot | Out-Null
    $setup = Join-Path $artifactDir "$Version/QEcho-Setup-$Version-x64.exe"
    Invoke-TestProcess $setup ('{0} /DIR="{1}"' -f $silent, $installDir)
    Test-InstalledClient $Version
    Add-Content -LiteralPath $config -Value '# Preserved user customization' -Encoding utf8NoBOM
    $configHash = (Get-FileHash -LiteralPath $config).Hash

    if ($UpgradeVersion) {
        $upgrade = Join-Path $artifactDir "$UpgradeVersion/QEcho-Setup-$UpgradeVersion-x64.exe"
        $manifest = Get-Content -LiteralPath "$artifactDir/$UpgradeVersion/windows-x64.json" -Raw | ConvertFrom-Json
        if ($manifest.version -cne $UpgradeVersion -or $manifest.size -ne (Get-Item -LiteralPath $upgrade).Length -or
            $manifest.sha256 -cne (Get-FileHash -LiteralPath $upgrade -Algorithm SHA256).Hash.ToLowerInvariant()) {
            throw 'The upgrade manifest does not match the signed installer.'
        }
        $uninstaller = Get-ChildItem -LiteralPath $installDir -Filter 'unins*.exe' | Select-Object -First 1
        if ((Get-AuthenticodeSignature -LiteralPath $uninstaller.FullName).Status -ne 'Valid') {
            throw 'The installed uninstaller is not signed by a trusted certificate.'
        }

        @'
import sqlite3
import sys
from pathlib import Path

from qecho.windows_update import verify_installer_signature

verify_installer_signature(Path(sys.argv[1]), Path(sys.argv[2]))
with sqlite3.connect(sys.argv[3]) as connection:
    connection.execute("INSERT INTO seen_events VALUES ('windows-smoke-preserved-event')")
'@ | uv run --no-sync python - $upgrade $qechoExe $db
        if ($LASTEXITCODE -ne 0) { throw 'Authenticode verifier or SQLite seed failed.' }

        # A short-lived process exercises the installer's wait without starting real QQ services.
        $launcher = Start-Process -FilePath (Join-Path $PSHOME 'pwsh.exe') -ArgumentList '-NoProfile -Command Start-Sleep -Seconds 12' -PassThru
        try {
            Invoke-TestProcess $upgrade "$silent /QECHOUPDATE /QECHOPID=$($launcher.Id) /NORESTARTAPPLICATIONS"
            if (-not $launcher.HasExited) { throw 'Installer did not wait for the previous launcher.' }
        } finally {
            $launcher.Dispose()
        }

        Test-InstalledClient $UpgradeVersion
        @'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as connection:
    record = connection.execute(
        "SELECT 1 FROM seen_events WHERE event_key = ?", ("windows-smoke-preserved-event",)
    ).fetchone()
if record != (1,):
    raise SystemExit("Upgrade erased the persisted SQLite record.")
'@ | uv run --no-sync python - $db
        if ($LASTEXITCODE -ne 0) { throw 'Upgraded database preservation check failed.' }
        Write-Host "Signed upgrade $Version -> $UpgradeVersion preserved the existing database."
    }

    if ((Get-FileHash -LiteralPath $config).Hash -cne $configHash) {
        throw 'Upgrade overwrote the user configuration.'
    }
    $uninstaller = Get-ChildItem -LiteralPath $installDir -Filter 'unins*.exe' | Select-Object -First 1
    Invoke-TestProcess $uninstaller.FullName '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART'
    if ((Test-Path -LiteralPath $qechoExe) -or (Get-FileHash -LiteralPath $config).Hash -cne $configHash -or
        -not (Test-Path -LiteralPath $db)) {
        throw 'Uninstall did not remove the program and preserve the user data.'
    }
    Write-Host 'Uninstall removed the program and kept user data.'
} finally {
    Pop-Location
    Remove-Item -LiteralPath $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}
