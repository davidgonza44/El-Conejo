# Synthetic-only physical C: -> D: test. No Python, database, network or scheduler.
$ErrorActionPreference = 'Stop'
$token = 'synthetic_transport_' + [DateTime]::UtcNow.ToString('yyyyMMdd_HHmmss') + '_' + [Guid]::NewGuid().ToString('N').Substring(0,8)
$exports = Join-Path 'C:\InventarioExports' $token
$inbox = Join-Path 'D:\InventarioInbox' $token
$reportFolder = Join-Path (Join-Path $PSScriptRoot '..\tmp') $token
foreach ($directory in @($exports, $inbox, $reportFolder)) {
    if (Test-Path -LiteralPath $directory) { throw 'Refusing to reuse an existing directory' }
    New-Item -ItemType Directory -Path $directory | Out-Null
}
$results = @()
foreach ($case in @(@{Rows=20000; Slot='0800'}, @{Rows=50000; Slot='1200'})) {
    $name = 'inventario_2031-01-01_' + $case.Slot + '.csv'
    $source = Join-Path $exports $name
    $stage = $source + '.tmp'
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $writer = New-Object System.IO.StreamWriter($stage, $false, $utf8)
    try {
        $writer.WriteLine('product_code;quantity')
        for ($i=1; $i -le $case.Rows; $i++) {
            $writer.WriteLine('SYN-BENCH-' + $i.ToString('D8') + ';' + (10 + $i % 201).ToString())
        }
        $writer.Flush()
        $writer.BaseStream.Flush($true)
    } finally { $writer.Dispose() }
    [System.IO.File]::Move($stage, $source)
    $destination = Join-Path $inbox $name
    $partial = $destination + '.tmp'
    $read = [System.IO.File]::OpenRead($source)
    $write = [System.IO.File]::Open($partial, [System.IO.FileMode]::CreateNew)
    $stopwatch = [Diagnostics.Stopwatch]::StartNew()
    try {
        $buffer = New-Object byte[] 65536
        $first = $read.Read($buffer,0,$buffer.Length)
        $write.Write($buffer,0,$first)
        $write.Flush($true)
        if (Test-Path -LiteralPath $destination) { throw 'Partial final file exposed' }
        $read.CopyTo($write)
        $write.Flush($true)
    } finally { $read.Dispose(); $write.Dispose() }
    $sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
    $copiedHash = (Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($sourceHash -ne $copiedHash) { throw 'Transfer SHA-256 mismatch' }
    [System.IO.File]::Move($partial,$destination)
    $stopwatch.Stop()
    $results += [pscustomobject]@{Rows=$case.Rows; Name=$name; Source=$source; Destination=$destination;
        Bytes=(Get-Item -LiteralPath $destination).Length; SHA256=$copiedHash;
        TransferSeconds=$stopwatch.Elapsed.TotalSeconds; PartialNotExposed=$true}
}
$report = Join-Path $reportFolder 'results.json'
[pscustomobject]@{SyntheticOnly=$true; Exports=$exports; Inbox=$inbox; Cases=$results} |
    ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $report -Encoding UTF8
Write-Output "PASS: physical synthetic atomic publication and SHA-256 verified C: -> D:"
Write-Output "RESULT: $report"
