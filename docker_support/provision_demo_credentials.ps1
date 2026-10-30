# Run in the Windows profile that will operate this DEMO. No LocalMachine scope.
$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne 'Win32NT') { throw 'DPAPI requiere Windows.' }
$root = Split-Path $PSScriptRoot -Parent
$envPath = Join-Path $root '.env.demo'
$values = @{}
foreach ($line in [IO.File]::ReadAllLines($envPath)) {
    if ($line -match '^([A-Z_]+)=(.*)$') { $values[$Matches[1]] = $Matches[2] }
}
if ($values.APP_PORT -ne '5001' -or $values.DEMO_MODE -ne 'true' -or
    $values.DOCKER_ENV_KIND -ne 'demo' -or $values.DOCKER_APP_ENV -ne 'demo' -or
    $values.DB_NAME -notmatch '^conejo_demo_[a-z0-9_]{8,32}$') {
    throw 'Solo se permite una instalacion DEMO explicita en 5001.'
}
$source = [IO.File]::ReadAllBytes((Join-Path $PSScriptRoot 'provision_demo_accounts.py'))
$encodedSource = [Convert]::ToBase64String($source)
$runtime = "exec(__import__('base64').b64decode('$encodedSource'))"
$payload = @{action='preflight'; environment_id=$values.ENVIRONMENT_ID;
    admin_username=$values.ADMIN_USERNAME; admin_password=$values.ADMIN_PASSWORD}

function Invoke-DemoProvision {
    param($InputPayload)
    # Source is non-secret; credentials go through stdin, not command arguments.
    $result = ($InputPayload | ConvertTo-Json -Depth 4 -Compress) |
        docker compose --env-file $envPath -f (Join-Path $root 'compose.yaml') exec -T app python -c $runtime 2>$null
    if ($LASTEXITCODE -ne 0 -or $result -notcontains 'PASS: DEMO accounts verified; no credentials logged.') {
        throw 'Provision DEMO rechazada; no se muestran salidas potencialmente sensibles.'
    }
}

Invoke-DemoProvision $payload
$passwords = @{}
$newLines = [Collections.Generic.List[string]]::new()
$suffixes = @{inventario='INVENTARIO'; asistente='ASISTENTE'; vendedor='VENDEDOR'; soporte_ti='SOPORTE'}
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try {
    foreach ($role in @('inventario','asistente','vendedor','soporte_ti')) {
        $key = 'DEMO_' + $suffixes[$role] + '_PASSWORD_DPAPI'
        if ($values.ContainsKey($key)) {
            try { $secure = ConvertTo-SecureString $values[$key] }
            catch { throw 'DPAPI pertenece a otro perfil/equipo o no es valido. Cree una instalacion nueva; no copie secretos.' }
            $passwords[$role] = [Net.NetworkCredential]::new('', $secure).Password
        } else {
            $bytes = New-Object byte[] 48
            $rng.GetBytes($bytes)
            $passwords[$role] = 'Aa1!' + [Convert]::ToBase64String($bytes)
            $secure = ConvertTo-SecureString $passwords[$role] -AsPlainText -Force
            # Without -Key/-SecureKey on Windows this is DPAPI CurrentUser.
            $newLines.Add($key + '=' + (ConvertFrom-SecureString $secure))
        }
    }
} finally { $rng.Dispose() }
if (-not $values.ContainsKey('DEMO_ADMIN_PASSWORD_DPAPI')) {
    $secure = ConvertTo-SecureString $values.ADMIN_PASSWORD -AsPlainText -Force
    $newLines.Add('DEMO_ADMIN_PASSWORD_DPAPI=' + (ConvertFrom-SecureString $secure))
} else {
    try { $secure = ConvertTo-SecureString $values.DEMO_ADMIN_PASSWORD_DPAPI }
    catch { throw 'No se puede abrir la copia DPAPI del administrador con este perfil.' }
    if ([Net.NetworkCredential]::new('', $secure).Password -cne $values.ADMIN_PASSWORD) {
        throw 'La copia DPAPI del administrador no coincide. No se modifican cuentas.'
    }
}
if ($newLines.Count) {
    # Save ciphertext BEFORE creating accounts. A retry reuses the saved secrets.
    [IO.File]::AppendAllText($envPath, "`n# DEMO credentials: DPAPI CurrentUser; do not transfer profiles/machines.`n" +
        ($newLines -join "`n") + "`n", [Text.UTF8Encoding]::new($false))
}
$payload.action = 'provision'
$payload.passwords = $passwords
Invoke-DemoProvision $payload
Write-Output 'PASS: cuentas DEMO listas, incluida demo_asistente; claves DPAPI CurrentUser en .env.demo, sin imprimirlas.'
