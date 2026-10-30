# Consulta local: DPAPI del usuario Windows; nunca imprime la contraseña.
param([Parameter(Mandatory=$true)][ValidateSet('admin','inventario','asistente','vendedor','soporte_ti')][string]$Rol)
$ErrorActionPreference = 'Stop'
if ([Environment]::OSVersion.Platform -ne 'Win32NT') { throw 'DPAPI requiere Windows.' }
$values = @{}
foreach ($line in [IO.File]::ReadAllLines((Join-Path $PSScriptRoot '../.env.demo'))) {
    if ($line -match '^([A-Z_]+)=(.*)$') { $values[$Matches[1]] = $Matches[2] }
}
if ($values.APP_PORT -ne '5001' -or $values.DEMO_MODE -ne 'true' -or
    $values.DOCKER_ENV_KIND -ne 'demo' -or $values.DB_NAME -notmatch '^conejo_demo_[a-z0-9_]{8,32}$') {
    throw 'Solo DEMO 5001.'
}
$suffix = @{admin='ADMIN';inventario='INVENTARIO';asistente='ASISTENTE';vendedor='VENDEDOR';soporte_ti='SOPORTE'}[$Rol]
try {
    # No -Key/-SecureKey: Windows DPAPI CurrentUser, never LocalMachine.
    $secure = ConvertTo-SecureString $values[('DEMO_' + $suffix + '_PASSWORD_DPAPI')]
} catch {
    throw 'No se puede descifrar con este perfil/equipo. Genere credenciales nuevas en otra instalacion DEMO; no copie secretos DPAPI.'
}
Set-Clipboard -Value ([Net.NetworkCredential]::new('', $secure).Password)
Write-Output 'Clave DEMO copiada. Pegala en http://localhost:5001/login. Limpia el portapapeles al terminar.'
