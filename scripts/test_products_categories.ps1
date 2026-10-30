# Pruebas del frontend web de productos y categorias (paginas + API consumida).
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

# Identificadores unicos de datos TEST para esta corrida
$suffix = Get-Date -Format "HHmmss"
$testCode = "TEST-WEB-$suffix"
$testCatName = "TEST WEB CAT $suffix"

# Snapshot de datos operativos (para test 19/20: nada queda alterado al final)
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
# 1-2: sin sesion -> redirige a /login
# ---------------------------------------------------------------------------
Write-Host "=== ACCESO SIN SESION ==="
$r = Invoke-Page -Url "/products" -NoRedirect
Check "1. /products sin login -> 302 a /login" $true (($r.code -eq 302) -and ($r.location -like "*/login*")) "(Location=$($r.location))"

$r = Invoke-Page -Url "/categories" -NoRedirect
Check "2. /categories sin login -> 302 a /login" $true (($r.code -eq 302) -and ($r.location -like "*/login*")) "(Location=$($r.location))"

# ---------------------------------------------------------------------------
# 3-7: acceso por rol
# ---------------------------------------------------------------------------
Write-Host "=== ACCESO POR ROL ==="
$admin = New-LoginSession "admin" "admin123"
$inventario = New-LoginSession "inventario1" "inventario123"
$vendedor = New-LoginSession "vendedor1" "vendedor123"

$r = Invoke-Page -Url "/products" -Session $admin
Check "3a. admin /products -> 200" 200 $r.code
Check "3b. admin ve boton Nuevo producto" $true ($r.content -like "*Nuevo producto*")

$r = Invoke-Page -Url "/categories" -Session $admin
Check "4a. admin /categories -> 200" 200 $r.code
Check "4b. admin ve boton Nueva categoria" $true ($r.content -like "*Nueva categor*")

$r = Invoke-Page -Url "/products" -Session $inventario
Check "5. inventario /products -> 200" 200 $r.code

$r = Invoke-Page -Url "/categories" -Session $inventario
Check "6. inventario /categories -> 200" 200 $r.code

# Vendedor: la API permite lectura (products:read y categorias autenticadas),
# asi que ve la pagina en modo solo lectura, sin botones de escritura.
$r = Invoke-Page -Url "/products" -Session $vendedor
Check "7a. vendedor /products -> 200 (solo lectura)" 200 $r.code
Check "7b. vendedor NO ve boton Nuevo producto" $false ($r.content -like "*Nuevo producto*")

$r = Invoke-Page -Url "/categories" -Session $vendedor
Check "7c. vendedor /categories -> 200 (solo lectura)" 200 $r.code
Check "7d. vendedor NO ve boton Nueva categoria" $false ($r.content -like "*Nueva categor*")

$r = Invoke-Page -Url "/api/products" -Session $vendedor -Method POST -Body @{ code = "X"; name = "X"; category_id = 1 }
Check "7e. vendedor POST /api/products -> 403" 403 $r.code

$r = Invoke-Page -Url "/api/categories" -Session $vendedor -Method POST -Body @{ name = "X" }
Check "7f. vendedor POST /api/categories -> 403" 403 $r.code

# ---------------------------------------------------------------------------
# 8-9: listados cargan
# ---------------------------------------------------------------------------
Write-Host "=== LISTADOS ==="
$r = Invoke-Page -Url "/api/products?include_inactive=1" -Session $admin
$json = Get-Json $r.content
Check "8. GET /api/products -> 200 con items" $true (($r.code -eq 200) -and ($null -ne $json.items)) "(count=$($json.count))"

$r = Invoke-Page -Url "/api/categories" -Session $admin
$json = Get-Json $r.content
Check "9. GET /api/categories -> 200 con items" $true (($r.code -eq 200) -and ($null -ne $json.items)) "(count=$($json.count))"
$firstCategoryId = if ($json.items.Count -gt 0) { $json.items[0].id } else { 1 }

# ---------------------------------------------------------------------------
# 16-18: CRUD de categoria (primero, para usarla en el producto TEST)
# ---------------------------------------------------------------------------
Write-Host "=== CRUD CATEGORIA ==="
$r = Invoke-Page -Url "/api/categories" -Session $admin -Method POST -Body @{ name = $testCatName; description = "Categoria de prueba web (TEST)" }
$json = Get-Json $r.content
Check "16. crear categoria TEST -> 201" 201 $r.code "(id=$($json.id))"
$testCatId = $json.id

$r = Invoke-Page -Url "/api/categories" -Session $admin -Method POST -Body @{ name = $testCatName }
Check "17. categoria duplicada -> 409" 409 $r.code

$r = Invoke-Page -Url "/api/categories/$testCatId" -Session $admin -Method PUT -Body @{ description = "Descripcion editada (TEST)" }
$json = Get-Json $r.content
Check "18. editar categoria -> 200" $true (($r.code -eq 200) -and ($json.description -eq "Descripcion editada (TEST)"))

# ---------------------------------------------------------------------------
# 10-15: CRUD de producto
# ---------------------------------------------------------------------------
Write-Host "=== CRUD PRODUCTO ==="
$productBody = @{
    code = $testCode; name = "Producto prueba web (TEST)"; description = "Creado por test_products_categories.ps1"
    category_id = $testCatId; unit = "unidad"; current_stock = 7; minimum_stock = 10
    purchase_price = 5.5; sale_price = 9.9
}
$r = Invoke-Page -Url "/api/products" -Session $admin -Method POST -Body $productBody
$json = Get-Json $r.content
Check "10a. crear producto TEST -> 201" 201 $r.code "(id=$($json.id))"
Check "10b. producto queda en bajo stock (7 <= 10)" $true ($json.is_low_stock -eq $true)
$testProductId = $json.id

$r = Invoke-Page -Url "/api/products" -Session $admin -Method POST -Body $productBody
Check "11. codigo duplicado -> 409" 409 $r.code

$r = Invoke-Page -Url "/api/products" -Session $admin -Method POST -Body @{ code = "$testCode-B"; name = "X"; category_id = 999999 }
Check "12. categoria inexistente -> 400" 400 $r.code

$r = Invoke-Page -Url "/api/products/$testProductId" -Session $admin -Method PUT -Body @{ name = "Producto prueba web EDITADO (TEST)"; sale_price = 12.5 }
$json = Get-Json $r.content
Check "13. editar producto -> 200" $true (($r.code -eq 200) -and ($json.name -like "*EDITADO*") -and ($json.sale_price -eq 12.5))

$r = Invoke-Page -Url "/api/products/$testProductId" -Session $admin -Method PUT -Body @{ current_stock = 999 }
Check "14. editar stock directo -> 400 (bloqueado)" 400 $r.code

$r = Invoke-Page -Url "/api/products/$testProductId" -Session $admin -Method DELETE
$json = Get-Json $r.content
Check "15a. desactivar producto -> 200" 200 $r.code
Check "15b. producto queda inactivo" $true ($json.product.is_active -eq $false)

$r = Invoke-Page -Url "/api/products/$testProductId" -Session $admin -Method PUT -Body @{ is_active = $true }
$json = Get-Json $r.content
Check "15c. reactivar producto -> 200 activo" $true (($r.code -eq 200) -and ($json.is_active -eq $true))

# Filtros que usa la pagina web
$r = Invoke-Page -Url "/api/products?search=$testCode" -Session $admin
$json = Get-Json $r.content
Check "8b. filtro search por codigo TEST" 1 $json.count

$r = Invoke-Page -Url "/api/products?category_id=$testCatId&low_stock=1" -Session $admin
$json = Get-Json $r.content
Check "8c. filtro categoria + bajo stock" 1 $json.count

# ---------------------------------------------------------------------------
# 19: limpieza de datos TEST (la categoria no se puede borrar con productos)
# ---------------------------------------------------------------------------
Write-Host "=== LIMPIEZA DATOS TEST ==="
$r = Invoke-Page -Url "/api/categories/$testCatId" -Session $admin -Method DELETE
Check "19a. borrar categoria con producto asociado -> 409" 409 $r.code

$cleanupCode = @"
import sys
sys.path.insert(0, '.')
from app import create_app
from app.extensions import db
from sqlalchemy import text
app = create_app()
with app.app_context():
    p = db.session.execute(text("DELETE FROM products WHERE code LIKE 'TEST-WEB-%'"))
    c = db.session.execute(text("DELETE FROM categories WHERE name LIKE 'TEST WEB CAT %'"))
    db.session.commit()
    print(f'productos_borrados={p.rowcount};categorias_borradas={c.rowcount}')
"@
$cleanResult = ($cleanupCode | & $python -) | Where-Object { $_ -match 'borrad' }
Write-Host "[INFO] limpieza: $cleanResult"

$after = (($countsCode | & $python -) | Where-Object { $_ -match '=' }) -join ';'
Check "19b. datos operativos identicos tras limpieza" $before $after

# ---------------------------------------------------------------------------
# 20-22: no regresion
# ---------------------------------------------------------------------------
Write-Host "=== NO REGRESION ==="
$r = Invoke-Page -Url "/dashboard" -Session $admin
Check "20. /dashboard sigue funcionando" $true (($r.code -eq 200) -and ($r.content -like "*Dashboard de reportes*"))

$r = Invoke-Page -Url "/login" -NoRedirect
Check "21a. /login sigue funcionando" 200 $r.code
$r = Invoke-Page -Url "/api/auth/me" -Session $admin
$json = Get-Json $r.content
Check "21b. /api/auth/me devuelve usuario" $true (($r.code -eq 200) -and ($json.username -eq "admin"))

$r = Invoke-Page -Url "/api/products"
Check "22a. /api/products sin sesion -> 401 JSON" $true (($r.code -eq 401) -and ($r.content -like "*error*"))
$r = Invoke-Page -Url "/api/reports/dashboard-summary" -Session $admin
$json = Get-Json $r.content
Check "22b. /api/reports/dashboard-summary sigue JSON" $true (($r.code -eq 200) -and ($null -ne $json))

# ---------------------------------------------------------------------------
Write-Host ""
if ($script:fallos -eq 0) { Write-Host "RESULTADO: TODAS LAS PRUEBAS PASARON" }
else { Write-Host "RESULTADO: $script:fallos PRUEBA(S) FALLARON" }
