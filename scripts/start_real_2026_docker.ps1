[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 5026
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$ComposeFile = Join-Path $RepositoryRoot 'compose.real2026-test.yaml'
$TemporaryRoot = Join-Path $RepositoryRoot '.tmp'

function Assert-NoLink([string]$Path) {
    $candidate = [IO.Path]::GetFullPath($Path)
    while ($candidate.StartsWith($RepositoryRoot, [StringComparison]::OrdinalIgnoreCase)) {
        if (Test-Path -LiteralPath $candidate) {
            $item = Get-Item -LiteralPath $candidate -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'No se permiten enlaces ni redirecciones en las rutas del laboratorio.'
            }
        }
        if ($candidate -eq $RepositoryRoot) { break }
        $candidate = [IO.Path]::GetDirectoryName($candidate)
    }
}

function New-RandomBytes([int]$Count) {
    $bytes = New-Object byte[] $Count
    $random = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $random.GetBytes($bytes) } finally { $random.Dispose() }
    return ,$bytes
}

function New-HexSecret {
    return ([BitConverter]::ToString((New-RandomBytes 32))).Replace('-', '').ToLowerInvariant()
}

function Write-NewPrivateFile([string]$Path, [string]$Content) {
    $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try {
        $writer = New-Object IO.StreamWriter($stream, (New-Object Text.UTF8Encoding($false)))
        try { $writer.Write($Content) } finally { $writer.Dispose() }
    } finally { $stream.Dispose() }
}

function Invoke-Docker([string[]]$Arguments) {
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'Docker no completo la operacion del laboratorio; no se tocaron otros proyectos.' }
}

function Get-DockerLines([string[]]$Arguments) {
    $lines = @(& docker @Arguments)
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo verificar la identidad de los recursos Docker.' }
    return @($lines | Where-Object { $_ -and $_.Trim() })
}

function Assert-FreePort {
    if ($Port -in @(5000, 5001)) { throw 'Los puertos 5000 y 5001 estan excluidos.' }
    $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, $Port)
    try { $listener.Start() } catch { throw 'El puerto solicitado esta ocupado; no se detendra ningun servicio.' }
    finally { $listener.Stop() }
}

function Assert-NewDockerIdentity([string]$Project, [string]$Image) {
    $label = 'label=com.docker.compose.project=' + $Project
    foreach ($kind in @('container', 'volume', 'network')) {
        $listArgs = @($kind, 'ls', '-q', '--filter', $label)
        if ($kind -eq 'container') { $listArgs += '-a' }
        $items = @(Get-DockerLines $listArgs)
        if ($items.Count) { throw 'La identidad Docker ya existe; se requiere un laboratorio nuevo.' }
    }
    foreach ($kind in @('container', 'volume', 'network')) {
        $listArgs = @($kind, 'ls', '-q', '--filter', ('name=' + $Project))
        if ($kind -eq 'container') { $listArgs += '-a' }
        $items = @(Get-DockerLines $listArgs)
        if ($items.Count) { throw 'Existe un nombre Docker que coincide con el laboratorio; no se reutilizara.' }
    }
    $images = @(Get-DockerLines @('image', 'ls', '-q', '--filter', ('reference=' + $Image)))
    if ($images.Count) { throw 'La etiqueta de imagen del laboratorio ya existe; no se sobrescribira.' }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Se requiere Docker Desktop con contenedores Linux.' }
if (-not (Test-Path -LiteralPath $ComposeFile -PathType Leaf)) { throw 'No existe el manifiesto exclusivo del laboratorio 2026.' }
Assert-NoLink $TemporaryRoot
Assert-FreePort
$baseImage = @(& docker image inspect --format '{{.Id}}' conejo-portable:manual-cuts-0007-20261010)
if ($LASTEXITCODE -ne 0 -or ($baseImage -join '').Trim() -ne 'sha256:9b17cee61a868cbc14ac23859389de393dc5e844184ce60711ba51e17ddb03d5') {
    throw 'La imagen base local no coincide con el runtime revisado; no se construira el laboratorio.'
}
$environmentId = [Guid]::NewGuid().ToString('N')
$suffix = $environmentId.Substring(0, 8)
$project = 'conejo-real2026-' + $suffix
$labImage = 'conejo-real2026:' + $suffix
Assert-NewDockerIdentity $project $labImage

$workspace = Join-Path $TemporaryRoot ('real2026-docker-' + $suffix)
if (Test-Path -LiteralPath $workspace) { throw 'La carpeta de prueba ya existe; no se sobrescribira.' }
$sources = Join-Path $workspace 'paquete_importacion'
$originalRoot = Join-Path $TemporaryRoot 'fuentes_csv_2026_aportados'
$normalizedRoot = Join-Path $RepositoryRoot 'artifacts/empirical_2026'
$normalizedNames = @(
    'empirical_2026_build_report.json',
    'empirical_2026_cohort_manifest.csv',
    'empirical_2026_operational_calendar.csv',
    'empirical_2026_series_strict.csv',
    'empirical_2026_series_sensitivity.csv'
)
$originalNames = @(
    'productos_2026.csv', 'demanda_limpia_2026.csv',
    'movimientos_detalle_2026.csv', 'existencias_2026_final.csv', 'MANIFIESTO_SHA256.json'
)
foreach ($entry in @(@{ root = $normalizedRoot; names = $normalizedNames }, @{ root = $originalRoot; names = $originalNames })) {
    Assert-NoLink $entry.root
    foreach ($name in $entry.names) {
        $sourceFile = Join-Path $entry.root $name
        Assert-NoLink $sourceFile
        if (-not (Test-Path -LiteralPath $sourceFile -PathType Leaf)) { throw ('Falta una fuente requerida: ' + $name) }
    }
}

[void](New-Item -ItemType Directory -Path $workspace)
[void](New-Item -ItemType Directory -Path $sources)
# Restrict only this new directory; existing source files and permissions are untouched.
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().User
$access = New-Object Security.AccessControl.DirectorySecurity
$access.SetOwner($identity)
$access.SetAccessRuleProtection($true, $false)
$rule = New-Object Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
$access.AddAccessRule($rule)
Set-Acl -LiteralPath $workspace -AclObject $access
foreach ($entry in @(@{ root = $normalizedRoot; names = $normalizedNames }, @{ root = $originalRoot; names = $originalNames })) {
    foreach ($name in $entry.names) {
        [IO.File]::Copy((Join-Path $entry.root $name), (Join-Path $sources $name), $false)
        if ((Get-FileHash -LiteralPath (Join-Path $entry.root $name) -Algorithm SHA256).Hash -ne
            (Get-FileHash -LiteralPath (Join-Path $sources $name) -Algorithm SHA256).Hash) {
            throw 'La copia de una fuente no conserva su SHA-256; no se iniciara Docker.'
        }
    }
}

$username = 'admin_' + $suffix
$password = 'Lab!A7_' + (New-HexSecret)
$fernetKey = [Convert]::ToBase64String((New-RandomBytes 32)).Replace('+', '-').Replace('/', '_')
$envFile = Join-Path $workspace 'runtime.env'
$manifestFile = Join-Path $workspace 'manifest.json'
$accessFile = Join-Path $workspace 'acceso_temporal.txt'
$origin = 'http://127.0.0.1:' + $Port
$settings = [ordered]@{
    COMPOSE_PROJECT_NAME = $project
    ENVIRONMENT_ID = $environmentId
    DB_NAME = 'conejo_disposable_real2026_' + $suffix
    DB_USER = 'conejo_lab'
    DB_PASSWORD = New-HexSecret
    MARIADB_ROOT_PASSWORD = New-HexSecret
    SECRET_KEY = New-HexSecret
    SECRETS_ENCRYPTION_KEY = $fernetKey
    ADMIN_USERNAME = $username
    ADMIN_EMAIL = $username + '@example.invalid'
    ADMIN_PASSWORD = $password
    APP_PORT = [string]$Port
    APP_BASE_URL = 'http://localhost:' + $Port
    LAB_IMAGE = $labImage
    LAB_ENV_FILE = $envFile.Replace('\', '/')
    LAB_SUBNET = '10.253.' + [Convert]::ToInt32($suffix.Substring(0, 2), 16) + '.0/24'
    LAB_WEB_SUBNET = '10.254.' + [Convert]::ToInt32($suffix.Substring(0, 2), 16) + '.0/24'
    DB_HOST = 'db'
    DB_PORT = '3306'
    CONEJO_CONTAINER = '1'
    REAL2026_ISOLATED = '1'
    DOCKER_ENV_KIND = 'disposable'
    DEMO_MODE = 'false'
    APP_ENV = 'development'
    PYTHON_DOTENV_DISABLED = '1'
}
foreach ($entry in $settings.GetEnumerator()) {
    $override = [Environment]::GetEnvironmentVariable($entry.Key, 'Process')
    if ($override -and $override -ne [string]$entry.Value) {
        throw ('La terminal tiene una sobrescritura de ' + $entry.Key + '; use una terminal sin variables de otro entorno.')
    }
}
$environmentText = ($settings.GetEnumerator() | ForEach-Object { $_.Key + '=' + $_.Value }) -join "`n"
Write-NewPrivateFile $envFile ($environmentText + "`n")
$manifest = [ordered]@{
    format = 'conejo-real2026-docker-v1'
    project = $project
    environment_id = $environmentId
    port = $Port
    origin = $origin
    username = $username
    password = $password
    env_file = $envFile
    root_path = $RepositoryRoot
    compose_file = $ComposeFile
    source_dir = $sources
    image = $labImage
    created_at = [DateTimeOffset]::UtcNow.ToString('o')
}
Write-NewPrivateFile $manifestFile ($manifest | ConvertTo-Json)
Write-NewPrivateFile $accessFile ("URL: $origin/login`r`nUsuario: $username`r`nClave: $password`r`nSolo laboratorio desechable. No publicar este archivo.`r`n")

$compose = @('compose', '--env-file', $envFile, '-p', $project, '-f', $ComposeFile)
Invoke-Docker ($compose + @('build', 'app'))
Assert-FreePort
Invoke-Docker ($compose + @('up', '-d', '--no-build', '--no-deps', 'db'))
Invoke-Docker ($compose + @('run', '--rm', '--no-deps', 'init'))
Invoke-Docker ($compose + @('up', '-d', '--no-build', '--no-deps', '--wait', '--wait-timeout', '180', 'app'))
Write-Host ('Laboratorio listo: ' + $origin + '/historical-2026')
Write-Host ('Credenciales temporales privadas: ' + $accessFile)
Write-Host ('Metadatos para detener solo esta prueba: ' + $manifestFile)
Write-Host ('Archivos para importar desde el navegador: ' + $sources)
Write-Host 'No se iniciaron scheduler ni servicios de otros proyectos.'
