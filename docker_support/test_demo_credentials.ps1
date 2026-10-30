# Synthetic test environment only; Docker and clipboard are replaced by test doubles.
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$fixture = Join-Path $root ('.tmp/dpapi-test-' + [Guid]::NewGuid().ToString('N'))
$fixtureScripts = Join-Path $fixture 'docker_support'
$null = New-Item -ItemType Directory -Path $fixtureScripts
foreach ($file in @('provision_demo_credentials.ps1','provision_demo_accounts.py','copy_demo_password.ps1')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $file) -Destination $fixtureScripts
}
$fixtureEnv = Join-Path $fixture '.env.demo'
$baseline = @'
APP_PORT=5001
DEMO_MODE=true
DOCKER_ENV_KIND=demo
DOCKER_APP_ENV=demo
DB_NAME=conejo_demo_12345678
ENVIRONMENT_ID=00000000000000000000000000000001
ADMIN_USERNAME=admin_fixture
ADMIN_PASSWORD=Aa1!SyntheticFixtureOnly
'@
[IO.File]::WriteAllText($fixtureEnv, $baseline, [Text.UTF8Encoding]::new($false))
function docker {
    $received = (($input | Out-String).Trim() | ConvertFrom-Json)
    if ($received.environment_id -ne '00000000000000000000000000000001') { throw 'Wrong synthetic target' }
    if ($received.action -eq 'provision') {
        $keys = @($received.passwords.PSObject.Properties.Value)
        if ($keys.Count -ne 4 -or @($keys | Select-Object -Unique).Count -ne 4) { throw 'Duplicate passwords' }
        foreach ($key in $keys) { if ($key.Length -ne 68) { throw 'Password length' } }
    }
    $global:LASTEXITCODE = 0
    'PASS: DEMO accounts verified; no credentials logged.'
}
function Set-Clipboard {
    param($Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { throw 'Empty secret' }
    $global:DemoClipboardTestCalls += 1
}
$null = & (Join-Path $fixtureScripts 'provision_demo_credentials.ps1')
$first = [IO.File]::ReadAllText($fixtureEnv)
if (($first -split '\r?\n' | Where-Object { $_ -match '^DEMO_.*_PASSWORD_DPAPI=' }).Count -ne 5) { throw 'Missing DPAPI secrets' }
if ($first -match '(?m)^DEMO_(INVENTARIO|VENDEDOR|SOPORTE)_PASSWORD=') { throw 'Plaintext secret persisted' }
$null = & (Join-Path $fixtureScripts 'provision_demo_credentials.ps1')
if ([IO.File]::ReadAllText($fixtureEnv) -cne $first) { throw 'Retry changed secrets' }
$global:DemoClipboardTestCalls = 0
foreach ($role in @('admin','inventario','asistente','vendedor','soporte_ti')) {
    $null = & (Join-Path $fixtureScripts 'copy_demo_password.ps1') -Rol $role
}
if ($global:DemoClipboardTestCalls -ne 5) { throw 'Copy flow failed' }
$invalid = [regex]::Replace($first, '(?m)^DEMO_VENDEDOR_PASSWORD_DPAPI=[^\r\n]*', 'DEMO_VENDEDOR_PASSWORD_DPAPI=invalid')
[IO.File]::WriteAllText($fixtureEnv, $invalid, [Text.UTF8Encoding]::new($false))
$rejected = $false
try { $null = & (Join-Path $fixtureScripts 'copy_demo_password.ps1') -Rol vendedor }
catch { $rejected = $_.Exception.Message -like '*No se puede descifrar*' }
if (-not $rejected -or $global:DemoClipboardTestCalls -ne 5) { throw 'Invalid cipher did not fail closed' }
Write-Output 'PASS: fresh DPAPI generation, idempotency, five copy flows and corrupt-cipher rejection; no real Docker/clipboard used.'
