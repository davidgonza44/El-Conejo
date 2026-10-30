[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Manifest
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$temporaryRoot = Join-Path $repositoryRoot '.tmp'
$manifestPath = [IO.Path]::GetFullPath($Manifest)
$workspace = [IO.Path]::GetDirectoryName($manifestPath)
$workspaceName = [IO.Path]::GetFileName($workspace)

function Assert-NoLink([string]$Path) {
    $candidate = $Path
    while ($candidate.StartsWith($repositoryRoot, [StringComparison]::OrdinalIgnoreCase)) {
        if (Test-Path -LiteralPath $candidate) {
            if ((Get-Item -LiteralPath $candidate -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'No se permiten enlaces ni redirecciones para detener el laboratorio.'
            }
        }
        if ($candidate -eq $repositoryRoot) { break }
        $candidate = [IO.Path]::GetDirectoryName($candidate)
    }
}

function Assert-SamePath([string]$Actual, [string]$Expected) {
    if ([IO.Path]::GetFullPath($Actual) -ne [IO.Path]::GetFullPath($Expected)) {
        throw 'Los metadatos no corresponden al laboratorio local esperado.'
    }
}

function Docker-Lines([string[]]$Arguments) {
    $lines = @(& docker @Arguments)
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo comprobar la identidad Docker; no se detendra ningun servicio.' }
    return @($lines | Where-Object { $_ -and $_.Trim() })
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker no esta disponible.' }
if ([IO.Path]::GetDirectoryName($workspace) -ne $temporaryRoot -or
    $workspaceName -notmatch '^real2026-docker-([a-f0-9]{8})$' -or
    [IO.Path]::GetFileName($manifestPath) -ne 'manifest.json') {
    throw 'Se requiere manifest.json dentro de .tmp/real2026-docker-<identidad>.'
}
$suffix = $Matches[1]
Assert-NoLink $manifestPath
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw 'El manifiesto no existe.' }
$data = [IO.File]::ReadAllText($manifestPath) | ConvertFrom-Json
$project = 'conejo-real2026-' + $suffix
$expectedEnvFile = Join-Path $workspace 'runtime.env'
$expectedCompose = Join-Path $repositoryRoot 'compose.real2026-test.yaml'
if ($data.format -ne 'conejo-real2026-docker-v1' -or $data.project -ne $project -or
    $data.environment_id -notmatch ('^' + $suffix + '[a-f0-9]{24}$') -or
    $data.image -ne ('conejo-real2026:' + $suffix) -or
    $data.port -lt 1024 -or $data.port -gt 65535 -or $data.port -in @(5000, 5001) -or
    $data.origin -ne ('http://127.0.0.1:' + $data.port)) {
    throw 'Identidad o puerto incompatibles; se rechaza detener servicios.'
}
Assert-SamePath $data.root_path $repositoryRoot
Assert-SamePath $data.env_file $expectedEnvFile
Assert-SamePath $data.compose_file $expectedCompose
Assert-SamePath $data.source_dir (Join-Path $workspace 'paquete_importacion')
Assert-NoLink $expectedEnvFile
Assert-NoLink $expectedCompose
if (-not (Test-Path -LiteralPath $expectedEnvFile -PathType Leaf)) { throw 'Falta el entorno privado original; no se reconstruira.' }

# Validate only public identity entries; secrets are never printed or copied.
$identityValues = @{}
foreach ($line in [IO.File]::ReadLines($expectedEnvFile)) {
    if ($line -match '^(COMPOSE_PROJECT_NAME|ENVIRONMENT_ID|DB_NAME|APP_PORT|LAB_IMAGE|DOCKER_ENV_KIND|REAL2026_ISOLATED)=(.*)$') {
        if ($identityValues.ContainsKey($Matches[1])) { throw 'Identidad duplicada en el entorno; no se detendra nada.' }
        $identityValues[$Matches[1]] = $Matches[2]
    }
}
if ($identityValues.COMPOSE_PROJECT_NAME -ne $project -or
    $identityValues.ENVIRONMENT_ID -ne $data.environment_id -or
    $identityValues.DB_NAME -ne ('conejo_disposable_real2026_' + $suffix) -or
    $identityValues.APP_PORT -ne [string]$data.port -or
    $identityValues.LAB_IMAGE -ne $data.image -or
    $identityValues.DOCKER_ENV_KIND -ne 'disposable' -or $identityValues.REAL2026_ISOLATED -ne '1') {
    throw 'El entorno no coincide con el manifiesto privado; no se detendra nada.'
}
$ids = @(Docker-Lines @('container', 'ls', '-a', '-q', '--filter', ('label=com.docker.compose.project=' + $project)))
foreach ($id in $ids) {
    $facts = @(Docker-Lines @('container', 'inspect', '--format',
        '{{.Name}}|{{json .Config.Labels}}', $id))
    if ($facts.Count -ne 1) { throw 'Identidad ambigua de contenedor; no se detendra nada.' }
    $parts = $facts[0] -split '\|', 2
    if ($parts.Count -ne 2) { throw 'Identidad incompleta de contenedor; no se detendra nada.' }
    $labels = $parts[1] | ConvertFrom-Json
    if ($labels.'com.docker.compose.project' -ne $project -or
        $labels.'com.docker.compose.service' -notin @('db', 'app', 'init') -or
        $labels.'com.conejo.purpose' -ne 'historical2026-isolated' -or
        $labels.'com.conejo.environment' -ne $data.environment_id -or
        $parts[0] -notmatch ('^/' + [regex]::Escape($project) + '[-_]')) {
        throw 'Un contenedor no corresponde al laboratorio esperado; no se detendra nada.'
    }
}
$compose = @('compose', '--env-file', $expectedEnvFile, '-p', $project, '-f', $expectedCompose)
& docker @compose stop app db
if ($LASTEXITCODE -ne 0) { throw 'Docker no pudo detener todos los servicios del laboratorio.' }
Write-Host ('Solo el laboratorio ' + $project + ' quedo detenido.')
Write-Host 'Se conservaron contenedores, imagen, red, volumenes y archivos para revision.'
