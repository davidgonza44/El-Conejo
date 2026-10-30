# Creates only the named isolated Phase E containers. Never starts Compose/DEMO.
param([ValidateSet('start','test','serve','new','stop')][string]$Mode = 'start',
      [ValidateSet('test_reconciliation_automatic_sqlite.py','test_reconciliation_core.py',
       'test_reconciliation_file_bridge.py','test_reconciliation_intake.py',
       'test_reconciliation_automatic_mariadb.py','test_phase_e_reconciliation.py')][string[]]$Suites)
$ErrorActionPreference = 'Stop'
$TaskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Evidence = Join-Path $TaskRoot '.tmp/phase-e-20261009'
$DbContainer = 'conejo-phase-e-db-ui-20261009'
$UiContainer = 'conejo-phase-e-jury-20261009'
$Network = 'conejo-phase-e-access-20261009'
$Volume = 'conejo-phase-e-data-20261009'
$TestContainer = 'conejo-phase-e-checks-20261009'
$AppImage = 'conejo-portable:demo-delivery-security-expanded-0007-20261009'
$DataDir = '/app/tmp/reconciliation-server-phase-e/data'
# Public synthetic fixture password; neither a DEMO nor a user credential.
$FixturePassword = 'phase-e-synthetic-isolated-only'
New-Item -ItemType Directory -Path $Evidence -Force | Out-Null

function Invoke-DockerChecked([string[]]$Arguments) {
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker operation failed: $($Arguments[0])" }
}
function Test-PhaseEDatabase {
    $PreviousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        foreach ($Attempt in 1..40) {
            & docker exec $DbContainer mariadb-admin --protocol=tcp --host=127.0.0.1 --connect-timeout=1 `
                --port=3407 --user=root "--password=$FixturePassword" ping --silent *> $null
            if ($LASTEXITCODE -eq 0) { return }
            Start-Sleep -Milliseconds 250
        }
        throw 'La base sintetica no esta lista; ninguna suite se ejecutara.'
    } finally { $ErrorActionPreference = $PreviousPreference }
}
function Get-AppArguments {
    return @('--network', "container:$DbContainer", '--user', '0', '--entrypoint', 'python',
        '--mount', "type=volume,source=$Volume,target=/app/tmp",
        '-e', 'PYTHON_DOTENV_DISABLED=1', '-e', 'PYTHONDONTWRITEBYTECODE=1', '-e', 'APP_ENV=testing',
        '-e', 'RUN_RECONCILIATION_MARIADB_TESTS=1', '-e', 'TEST_MARIADB_USER=root',
        '-e', "TEST_MARIADB_PASSWORD=$FixturePassword", '-e', 'TEST_MARIADB_PORT=3407',
        '-e', 'TEST_MARIADB_HOST=127.0.0.1', '-e', "TEST_MARIADB_DATADIR=$DataDir")
}

switch ($Mode) {
    'start' {
        $Names = & docker ps -a --format '{{.Names}}'
        $Networks = & docker network ls --format '{{.Name}}'
        if ($Network -notin $Networks) { Invoke-DockerChecked @('network', 'create', $Network) }
        if ($DbContainer -in $Names) {
            Invoke-DockerChecked @('start',$DbContainer)
            $Attached = & docker inspect $DbContainer --format '{{range $key,$value := .NetworkSettings.Networks}}{{$key}} {{end}}'
            if ($Network -notin $Attached.Trim().Split(' ')) { Invoke-DockerChecked @('network','connect',$Network,$DbContainer) }
            Write-Output 'Phase E database retained; no duplicate data.'; break
        }
        Invoke-DockerChecked @('run', '-d', '--name', $DbContainer, '--network', $Network,
            '-p', '127.0.0.1:5066:5066', '-e', "MARIADB_ROOT_PASSWORD=$FixturePassword",
            '--mount', "type=volume,source=$Volume,target=/app/tmp", 'mariadb:10.11.14',
            '--port=3407', '--bind-address=127.0.0.1', "--datadir=$DataDir", '--skip-log-bin')
        Write-Output 'New isolated database starting. UI port 5066; database not published.'
    }
    'test' {
        Test-PhaseEDatabase
        & py -3.11 -B (Join-Path $PSScriptRoot 'package_phase_e_fixture.py')
        if ($LASTEXITCODE -ne 0) { throw 'Allowlisted packaging failed' }
        $BaseArguments = Get-AppArguments
        $Names = & docker ps -a --format '{{.Names}}'
        if ($TestContainer -notin $Names) {
            Invoke-DockerChecked (@('run','-d','--name',$TestContainer) + $BaseArguments +
                @($AppImage,'-c','import time; time.sleep(86400)'))
        } else { Invoke-DockerChecked @('restart',$TestContainer) }
        Invoke-DockerChecked @('cp', (Join-Path $Evidence 'source.tar'), "${TestContainer}:/phase-e-source.tar")
        Invoke-DockerChecked @('exec','--user','0',$TestContainer,'tar','-xf','/phase-e-source.tar','-C','/app')
        if (-not $Suites) { $Suites = @('test_reconciliation_automatic_sqlite.py','test_reconciliation_core.py',
            'test_reconciliation_file_bridge.py','test_reconciliation_intake.py',
            'test_reconciliation_automatic_mariadb.py','test_phase_e_reconciliation.py') }
        foreach ($Suite in $Suites) {
            $ErrorActionPreference = 'Continue'
            & docker exec --user 0 $TestContainer sh -c "python -B -W ignore::DeprecationWarning scripts/$Suite 2>&1" |
                Tee-Object -FilePath (Join-Path $Evidence "$Suite.txt")
            $ErrorActionPreference = 'Stop'
            if ($LASTEXITCODE -ne 0) { throw "Failed suite: $Suite" }
        }
    }
    'serve' {
        Test-PhaseEDatabase
        $Names = & docker ps -a --format '{{.Names}}'
        if ($UiContainer -in $Names) { Invoke-DockerChecked @('start',$UiContainer); Write-Output 'Phase E UI retained; use its browser controls.'; break }
        $BaseArguments = Get-AppArguments
        & py -3.11 -B (Join-Path $PSScriptRoot 'package_phase_e_fixture.py')
        if ($LASTEXITCODE -ne 0) { throw 'Allowlisted packaging failed' }
        Invoke-DockerChecked (@('create','--name',$UiContainer) + $BaseArguments +
            @('--entrypoint','sh',$AppImage,'-c','tar -xf /phase-e-source.tar -C /app && exec python -B scripts/serve_phase_e_reconciliation.py'))
        Invoke-DockerChecked @('cp', (Join-Path $Evidence 'source.tar'), "${UiContainer}:/phase-e-source.tar")
        Invoke-DockerChecked @('start',$UiContainer)
        Write-Output 'Simulacion aislada: http://127.0.0.1:5066/fase-e'
    }
    'new' {
        Test-PhaseEDatabase
        Invoke-DockerChecked @('exec',$UiContainer,'python','-B','scripts/serve_phase_e_reconciliation.py','--prepare-new')
        Invoke-DockerChecked @('restart',$UiContainer)
        Write-Output 'New synthetic scenario selected. Previous schema and evidence retained.'
    }
    'stop' {
        # Names are literals for THIS fixture; retain containers/data for audit.
        foreach ($Name in @($UiContainer,$TestContainer,$DbContainer)) {
            $Names = & docker ps -a --format '{{.Names}}'
            if ($Name -in $Names) { Invoke-DockerChecked @('stop',$Name) }
        }
    }
}
