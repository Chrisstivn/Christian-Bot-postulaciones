param(
    [Parameter(Mandatory = $true)][string]$DocxPath,
    [Parameter(Mandatory = $true)][string]$PdfPath
)
$ErrorActionPreference = 'Stop'
$word = $null
$doc = $null
try {
    if (-not (Test-Path -LiteralPath $DocxPath -PathType Leaf)) {
        throw "No existe el DOCX: $DocxPath"
    }
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $doc = $word.Documents.Open($DocxPath, $false, $true)
    $doc.ExportAsFixedFormat($PdfPath, 17)
    if (-not (Test-Path -LiteralPath $PdfPath -PathType Leaf)) {
        throw "Word no genero el PDF: $PdfPath"
    }
}
finally {
    if ($null -ne $doc) {
        $doc.Close(0)
        [System.Runtime.Interopservices.Marshal]::ReleaseComObject($doc) | Out-Null
    }
    if ($null -ne $word) {
        $word.Quit()
        [System.Runtime.Interopservices.Marshal]::ReleaseComObject($word) | Out-Null
    }
}
