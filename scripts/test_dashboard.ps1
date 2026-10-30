# Pruebas del dashboard real de reportes (vistas web + endpoints consumidos).
# Requiere el servidor corriendo (python run.py) y los usuarios semilla:
# admin/admin123, inventario1/inventario123, vendedor1/vendedor123.
$ErrorActionPreference = "Continue"
$base = "http://localhost:5000"
$python = ".\venv\Scripts\python.exe"
$script:fallos = 0

function Invoke-Page {
    param($Url, $Session, [switch]$NoRedirect, $Method = "GET", $Body)
    $params = @{ Method = $Method; Uri = "$base$Url"; TimeoutSec = 25; UseBasicParsing = $true }
    if ($Session) { $params.WebSession = $Session }
    if ($NoRedirect) { $params.MaximumRedirection = 0 }
    if ($null -ne $Body) {
        $params.ContentType = "application/json"
        $params.Body = ($Body | ConvertTo-Json -Depth 5)
    }
    try {
        $r = Invoke-WebRequest @params
        return @{ code = [int]$r.StatusCode; content = [string]$r.Content; location = [string]$r.Headers["Location"] }
    } catch {
        $resp = $_.Exception.Response
        $code = if ($resp) { [int]$resp.StatusCode } else { -1 }
        $loc = ""
        if ($resp) { try { $loc = [string]$resp.Headers["Location"] } catch { } }
        $content = ""
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $content = $_.ErrorDetails.Message }
        return @{ code = $code; content = $content; location = $loc }
    }
}

function New-LoginSession {
    param($Identifier, $Password)
    $s = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    $r = Invoke-Page -Url "/api/auth/login" -Method POST -Body @{ identifier = $Identifier; password = $Password } -Session $s
    if ($r.code -ne 200) { Write-Host "[SETUP-ERROR] login $Identifier -> HTTP $($r.code)"; $script:fallos++ }
    return $s
}

function Check {
    param($Name, $Expected, $Actual, $Extra = "")
    if ($Expected -eq $Actual) { Write-Host "[OK] $Name -> $Actual $Extra" }
    else { Write-Host "[FALLO] $Name -> esperado $Expected, obtenido $Actual $Extra"; $script:fallos++ }
}

function Get-Json {
    param($Content)
    try { return $Content | ConvertFrom-Json } catch { return $null }
}

# Snapshot de datos operativos (test 19)
$countsCode = @"
import sys
sys.path.insert(0, '.')
from app import create_app
from app.extensions import db
from sqlalchemy import text
app = create_app()
with app.app_context():
    for t in ('products', 'categories', 'stock_movements', 'delivery_notes', 'delivery_note_items', 'users'):
        n = db.session.execute(text(f'SELECT COUNT(*) FROM {t}')).scalar()
        print(f'{t}={n}')
"@
$before = (($countsCode | & $python -) | Where-Object { $_ -match '=' }) -join ';'

# ---------------------------------------------------------------------------
# 1-4: control de acceso a /dashboard
# ---------------------------------------------------------------------------
Write-Host "=== ACCESO A /dashboard ==="
$r = Invoke-Page -Url "/dashboard" -NoRedirect
Check "1. sin login -> 302 a /login" $true (($r.code -eq 302) -and ($r.location -like "*/login*")) "(Location=$($r.location))"

$vendedor = New-LoginSession "vendedor1" "vendedor123"
$r = Invoke-Page -Url "/dashboard" -Session $vendedor -NoRedirect
Check "2. vendedor -> 302 a /access-denied" $true (($r.code -eq 302) -and ($r.location -like "*/access-denied*")) "(Location=$($r.location))"

$admin = New-LoginSession "admin" "admin123"
$r = Invoke-Page -Url "/dashboard" -Session $admin
Check "3a. admin -> 200" 200 $r.code
Check "3b. contiene titulo Dashboard de reportes" $true ($r.content -like "*Dashboard de reportes*")
Check "3c. contiene sidebar y navbar (nombre y rol)" $true (($r.content -like "*Ferreter*El Conejo*") -and ($r.content -like "*Cerrar sesi*"))
Check "3d. Tabler y Chart.js por CDN" $true (($r.content -like "*cdn.jsdelivr.net/npm/@tabler/core*") -and ($r.content -like "*cdn.jsdelivr.net/npm/chart.js*"))
Check "3e. contiene 6 canvas de graficos" $true (($r.content -like "*chart-entries-exits*") -and ($r.content -like "*chart-stock-minimum*") -and ($r.content -like "*chart-top-exits*") -and ($r.content -like "*chart-notes-period*") -and ($r.content -like "*chart-top-delivered*") -and ($r.content -like "*chart-notes-user*"))
Check "3f. contiene filtros" $true (($r.content -like "*f-date-from*") -and ($r.content -like "*f-days*") -and ($r.content -like "*f-multiplier*") -and ($r.content -like "*f-limit*"))
Check "3g. no expone secretos" $true (($r.content -notlike "*GOOGLE_CLIENT_SECRET*") -and ($r.content -notlike "*MAIL_PASSWORD*") -and ($r.content -notlike "*password_hash*"))

$inventario = New-LoginSession "inventario1" "inventario123"
$r = Invoke-Page -Url "/dashboard" -Session $inventario
Check "4. inventario -> 200" 200 $r.code

$r = Invoke-Page -Url "/static/js/dashboard.js"
Check "estaticos-a. dashboard.js servido" 200 $r.code
$r = Invoke-Page -Url "/static/css/dashboard.css"
Check "estaticos-b. dashboard.css servido" 200 $r.code

# ---------------------------------------------------------------------------
# 5-12: endpoints que consume cada widget (con sesion admin)
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== ENDPOINTS DE WIDGETS ==="
$r = Invoke-Page -Url "/api/reports/dashboard-summary" -Session $admin
$d = Get-Json $r.content
Check "5. KPIs: dashboard-summary -> 200 con campos" $true (($r.code -eq 200) -and ($null -ne $d.total_products) -and ($null -ne $d.average_amount_issued_delivery_notes))

$r = Invoke-Page -Url "/api/reports/entries-vs-exits" -Session $admin
$d = Get-Json $r.content
Check "6. grafico entradas vs salidas -> 200 con items" $true (($r.code -eq 200) -and ($null -ne $d.items))

$r = Invoke-Page -Url "/api/reports/stock-vs-minimum" -Session $admin
$d = Get-Json $r.content
Check "7. grafico stock vs minimo -> 200 con items" $true (($r.code -eq 200) -and ($null -ne $d.items))

$r = Invoke-Page -Url "/api/reports/top-products-by-exits?limit=10" -Session $admin
$d = Get-Json $r.content
Check "8. grafico top salidas -> 200 con items" $true (($r.code -eq 200) -and ($null -ne $d.items))

$r = Invoke-Page -Url "/api/reports/delivery-notes-by-period" -Session $admin
$d = Get-Json $r.content
Check "9. grafico notas por periodo -> 200 con items" $true (($r.code -eq 200) -and ($null -ne $d.items))

$r = Invoke-Page -Url "/api/reports/top-delivered-products?limit=10" -Session $admin
$d = Get-Json $r.content
Check "10. grafico mas entregados -> 200 con items" $true (($r.code -eq 200) -and ($null -ne $d.items))

$r = Invoke-Page -Url "/api/reports/delivery-notes-by-user" -Session $admin
$d = Get-Json $r.content
Check "11. grafico notas por usuario -> 200 con items" $true (($r.code -eq 200) -and ($null -ne $d.items))

$r = Invoke-Page -Url "/api/reports/low-stock-products" -Session $admin
$d1 = Get-Json $r.content
$r2 = Invoke-Page -Url "/api/reports/products-without-movement?days=30" -Session $admin
$d2 = Get-Json $r2.content
$r3 = Invoke-Page -Url "/api/reports/excess-stock-products?multiplier=3" -Session $admin
$d3 = Get-Json $r3.content
$r4 = Invoke-Page -Url "/api/reports/inventory-adjustments" -Session $admin
$d4 = Get-Json $r4.content
Check "12. tablas: low-stock, sin movimiento, exceso, ajustes -> 200" $true (($r.code -eq 200) -and ($null -ne $d1.items) -and ($null -ne $d2.items) -and ($null -ne $d3.items) -and ($null -ne $d4.items))

# ---------------------------------------------------------------------------
# 13-16: filtros
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== FILTROS ==="
$r = Invoke-Page -Url "/api/reports/entries-vs-exits?date_from=2020-01-01&date_to=2030-12-31" -Session $admin
Check "13a. date_from/date_to validos -> 200" 200 $r.code
$r = Invoke-Page -Url "/api/reports/entries-vs-exits?date_from=fecha-mala" -Session $admin
$d = Get-Json $r.content
Check "13b. fecha invalida -> 400 con error JSON" $true (($r.code -eq 400) -and ($null -ne $d.error))
$r = Invoke-Page -Url "/api/reports/entries-vs-exits?date_from=2030-01-01&date_to=2020-01-01" -Session $admin
Check "13c. rango invertido -> 400" 400 $r.code

$r = Invoke-Page -Url "/api/reports/products-without-movement?days=7" -Session $admin
$d = Get-Json $r.content
Check "14a. days=7 -> 200 y eco days=7" $true (($r.code -eq 200) -and ($d.days -eq 7))
$r = Invoke-Page -Url "/api/reports/products-without-movement?days=abc" -Session $admin
Check "14b. days invalido -> 400" 400 $r.code

$r = Invoke-Page -Url "/api/reports/excess-stock-products?multiplier=2" -Session $admin
$d = Get-Json $r.content
Check "15a. multiplier=2 -> 200 y eco multiplier=2" $true (($r.code -eq 200) -and ($d.multiplier -eq 2))
$r = Invoke-Page -Url "/api/reports/excess-stock-products?multiplier=0.5" -Session $admin
Check "15b. multiplier invalido -> 400" 400 $r.code

$r = Invoke-Page -Url "/api/reports/top-products-by-exits?limit=3" -Session $admin
$d = Get-Json $r.content
Check "16a. limit=3 -> 200, count <= 3" $true (($r.code -eq 200) -and ($d.limit -eq 3) -and ($d.count -le 3))
$r = Invoke-Page -Url "/api/reports/top-products-by-exits?limit=0" -Session $admin
Check "16b. limit invalido -> 400" 400 $r.code

# ---------------------------------------------------------------------------
# 17: sin datos no rompe
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== SIN DATOS ==="
$r = Invoke-Page -Url "/api/reports/entries-vs-exits?date_from=2099-01-01&date_to=2099-12-31" -Session $admin
$d = Get-Json $r.content
Check "17a. rango futuro -> 200 con items=[] y count=0" $true (($r.code -eq 200) -and ($d.count -eq 0))
$r = Invoke-Page -Url "/dashboard" -Session $admin
Check "17b. la pagina maneja 'Sin datos' (widget-msg presente)" $true ($r.content -like "*widget-msg*")

# ---------------------------------------------------------------------------
# 18: /api/reports sigue devolviendo JSON puro (401/403 sin redirect)
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== /api/reports SIGUE JSON ==="
$r = Invoke-Page -Url "/api/reports/dashboard-summary" -NoRedirect
Check "18a. sin sesion -> JSON 401 sin redirect" $true (($r.code -eq 401) -and ($r.content -like "*{*error*"))
$r = Invoke-Page -Url "/api/reports/dashboard-summary" -Session $vendedor -NoRedirect
Check "18b. vendedor -> JSON 403" $true (($r.code -eq 403) -and ($r.content -like "*{*error*"))

# ---------------------------------------------------------------------------
# 19: datos operativos sin cambios
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "=== DATOS OPERATIVOS ==="
$after = (($countsCode | & $python -) | Where-Object { $_ -match '=' }) -join ';'
Check "19. productos/stock/notas/usuarios sin cambios" $before $after

Write-Host ""
if ($script:fallos -eq 0) { Write-Host "RESULTADO: TODAS LAS PRUEBAS PASARON" }
else { Write-Host "RESULTADO: $($script:fallos) PRUEBAS FALLARON" }
