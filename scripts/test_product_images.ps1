# Pruebas de imagenes de producto (upload/replace/delete + validaciones).
# Requiere el servidor corriendo (python run.py) y usuarios semilla.
$ErrorActionPreference = "Continue"
$base = "http://localhost:5000"
$python = ".\venv\Scripts\python.exe"
$script:fallos = 0

function Check {
    param($Name, $Expected, $Actual, $Extra = "")
    if ($Expected -eq $Actual) { Write-Host "[OK] $Name -> $Actual $Extra" }
    else { Write-Host "[FALLO] $Name -> esperado $Expected, obtenido $Actual $Extra"; $script:fallos++ }
}

function Get-Json { param($Content) try { return $Content | ConvertFrom-Json } catch { return $null } }

function Invoke-Api {
    param($Url, $Session, $Method = "GET", $Body, [switch]$NoRedirect)
    $params = @{ Method = $Method; Uri = "$base$Url"; TimeoutSec = 25; UseBasicParsing = $true }
    if ($Session) { $params.WebSession = $Session }
    if ($NoRedirect) { $params.MaximumRedirection = 0 }
    if ($null -ne $Body) { $params.ContentType = "application/json"; $params.Body = ($Body | ConvertTo-Json -Depth 5) }
    try {
        $r = Invoke-WebRequest @params
        return @{ code = [int]$r.StatusCode; content = [string]$r.Content }
    } catch {
        $resp = $_.Exception.Response
        $code = if ($resp) { [int]$resp.StatusCode } else { -1 }
        $content = ""
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $content = $_.ErrorDetails.Message }
        return @{ code = $code; content = $content }
    }
}

# Multipart manual (Windows PowerShell 5 no tiene -Form)
function Invoke-Upload {
    param($Url, $Session, $FilePath, $FieldName = "image", $Method = "POST")
    $boundary = [System.Guid]::NewGuid().ToString()
    $fileBytes = [System.IO.File]::ReadAllBytes($FilePath)
    $fileName = [System.IO.Path]::GetFileName($FilePath)
    $enc = [System.Text.Encoding]::GetEncoding("iso-8859-1")
    $head = "--$boundary`r`nContent-Disposition: form-data; name=`"$FieldName`"; filename=`"$fileName`"`r`nContent-Type: application/octet-stream`r`n`r`n"
    $tail = "`r`n--$boundary--`r`n"
    # MemoryStream para obtener un byte[] real (el operador + crea Object[])
    $ms = New-Object System.IO.MemoryStream
    $headBytes = $enc.GetBytes($head); $ms.Write($headBytes, 0, $headBytes.Length)
    $ms.Write($fileBytes, 0, $fileBytes.Length)
    $tailBytes = $enc.GetBytes($tail); $ms.Write($tailBytes, 0, $tailBytes.Length)
    $bodyBytes = $ms.ToArray()
    try {
        $r = Invoke-WebRequest -Uri "$base$Url" -Method $Method -WebSession $Session `
            -ContentType "multipart/form-data; boundary=$boundary" -Body $bodyBytes -TimeoutSec 30 -UseBasicParsing
        return @{ code = [int]$r.StatusCode; content = [string]$r.Content }
    } catch {
        $resp = $_.Exception.Response
        $code = if ($resp) { [int]$resp.StatusCode } else { -1 }
        $content = ""
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $content = $_.ErrorDetails.Message }
        return @{ code = $code; content = $content }
    }
}

function New-LoginSession {
    param($Identifier, $Password)
    $s = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    $r = Invoke-Api -Url "/api/auth/login" -Method POST -Body @{ identifier = $Identifier; password = $Password } -Session $s
    if ($r.code -ne 200) { Write-Host "[SETUP-ERROR] login $Identifier -> HTTP $($r.code)"; $script:fallos++ }
    return $s
}

# --- Archivos de prueba (PNG real 1x1, fake con extension png, y >2MB) ---
$tmp = Join-Path $env:TEMP "img_tests"
New-Item -ItemType Directory -Path $tmp -Force | Out-Null
$pngCode = @"
import base64, os
d = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==')
open(os.path.join(r'$tmp', 'valida.png'), 'wb').write(d)
open(os.path.join(r'$tmp', 'valida2.png'), 'wb').write(d)
open(os.path.join(r'$tmp', 'falsa.png'), 'wb').write(b'no soy una imagen para nada')
open(os.path.join(r'$tmp', 'script.exe.txt'), 'wb').write(b'MZ...')
open(os.path.join(r'$tmp', 'grande.png'), 'wb').write(b'\x89PNG\r\n\x1a\n' + b'0' * (3 * 1024 * 1024))
print('archivos listos')
"@
$pngCode | & $python - | Out-Null

$admin = New-LoginSession "admin" "admin123"
$inventario = New-LoginSession "inventario1" "inventario123"
$vendedor = New-LoginSession "vendedor1" "vendedor123"

# --- Producto TEST para las imagenes ---
$suffix = Get-Date -Format "HHmmss"
$r = Invoke-Api -Url "/api/categories" -Session $admin
$catId = (Get-Json $r.content).items[0].id
$r = Invoke-Api -Url "/api/products" -Session $admin -Method POST -Body @{
    code = "TEST-IMG-$suffix"; name = "Producto imagen (TEST)"; category_id = $catId
}
$prod = Get-Json $r.content
Check "setup. producto TEST creado" 201 $r.code "(id=$($prod.id))"
$prodId = $prod.id

Write-Host "=== SUBIDA DE IMAGEN ==="
$r = Invoke-Upload -Url "/api/products/$prodId/image" -Session $admin -FilePath (Join-Path $tmp "valida.png")
$json = Get-Json $r.content
Check "4. admin sube imagen valida -> 200 con image_url" $true (($r.code -eq 200) -and ($json.image_url -like "/media/products/*"))
$imageUrl1 = $json.image_url

$r = Invoke-Api -Url $imageUrl1 -Session $admin
Check "5. GET $imageUrl1 -> 200 (miniatura servida)" 200 $r.code

$r = Invoke-Api -Url $imageUrl1 -NoRedirect
Check "5b. imagen sin sesion -> 302 al login" $true (($r.code -eq 302) -or ($r.code -eq 401)) "(code=$($r.code))"

Write-Host "=== REEMPLAZO Y BORRADO ==="
$r = Invoke-Upload -Url "/api/products/$prodId/image" -Session $admin -FilePath (Join-Path $tmp "valida2.png")
$json = Get-Json $r.content
$imageUrl2 = $json.image_url
Check "6a. reemplazo -> 200 con nueva url distinta" $true (($r.code -eq 200) -and ($imageUrl2 -ne $imageUrl1))
$r = Invoke-Api -Url $imageUrl1 -Session $admin
Check "6b. imagen anterior borrada -> 404" 404 $r.code

$r = Invoke-Api -Url "/api/products/$prodId/image" -Session $admin -Method DELETE
$json = Get-Json $r.content
Check "7a. DELETE imagen -> 200 y product.image_url null" $true (($r.code -eq 200) -and ($null -eq $json.product.image_url))
$r = Invoke-Api -Url $imageUrl2 -Session $admin
Check "7b. archivo eliminado -> 404" 404 $r.code

Write-Host "=== VALIDACIONES ==="
$r = Invoke-Upload -Url "/api/products/$prodId/image" -Session $admin -FilePath (Join-Path $tmp "falsa.png")
Check "8a. contenido falso con extension png -> 400" 400 $r.code
$r = Invoke-Upload -Url "/api/products/$prodId/image" -Session $admin -FilePath (Join-Path $tmp "script.exe.txt")
Check "8b. extension no permitida -> 400" 400 $r.code
$r = Invoke-Upload -Url "/api/products/$prodId/image" -Session $admin -FilePath (Join-Path $tmp "grande.png")
Check "9. archivo de 3MB -> 400 (limite 2MB)" 400 $r.code
$r = Invoke-Upload -Url "/api/products/999999/image" -Session $admin -FilePath (Join-Path $tmp "valida.png")
Check "9b. producto inexistente -> 404" 404 $r.code

Write-Host "=== PERMISOS ==="
$r = Invoke-Upload -Url "/api/products/$prodId/image" -Session $vendedor -FilePath (Join-Path $tmp "valida.png")
Check "10. vendedor sube imagen -> 403" 403 $r.code
$r = Invoke-Upload -Url "/api/products/$prodId/image" -Session $inventario -FilePath (Join-Path $tmp "valida.png")
Check "12. inventario sube imagen -> 200" 200 $r.code

Write-Host "=== JSON PURO SIN CAMBIOS ==="
$r = Invoke-Api -Url "/api/products/$prodId" -Session $admin
$json = Get-Json $r.content
Check "16. GET /api/products/<id> sigue JSON con image_url" $true (($r.code -eq 200) -and ($json.PSObject.Properties.Name -contains "image_url"))

Write-Host "=== LIMPIEZA ==="
$r = Invoke-Api -Url "/api/products/$prodId/image" -Session $admin -Method DELETE
Check "limpieza-a. imagen final borrada" 200 $r.code
$cleanup = @"
import sys
sys.path.insert(0, '.')
from app import create_app
from app.extensions import db
from sqlalchemy import text
app = create_app()
with app.app_context():
    p = db.session.execute(text("DELETE FROM products WHERE code LIKE 'TEST-IMG-%'"))
    db.session.commit()
    print(f'productos_borrados={p.rowcount}')
"@
$cleanResult = ($cleanup | & $python -) | Where-Object { $_ -match 'borrad' }
Write-Host "[INFO] $cleanResult"
$leftover = Get-ChildItem "uploads\products" -File -ErrorAction SilentlyContinue | Measure-Object
Check "limpieza-b. uploads/products sin archivos residuales" 0 $leftover.Count
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
if ($script:fallos -eq 0) { Write-Host "RESULTADO: TODAS LAS PRUEBAS PASARON" }
else { Write-Host "RESULTADO: $script:fallos PRUEBA(S) FALLARON" }
