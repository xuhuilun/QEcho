#Requires -Version 7.2
[CmdletBinding()]
param([Parameter(Mandatory)] [string] $FilePath)

$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -cne 'true' -or -not $env:QECHO_TEST_SIGNER_THUMBPRINT) {
    throw 'Missing isolated GitHub Actions test certificate.'
}
$certificate = Get-Item -LiteralPath "Cert:/CurrentUser/My/$env:QECHO_TEST_SIGNER_THUMBPRINT"
$result = Set-AuthenticodeSignature -LiteralPath $FilePath -Certificate $certificate -HashAlgorithm SHA256
if ($result.Status -ne 'Valid') { throw "Test signing failed: $($result.Status)." }
