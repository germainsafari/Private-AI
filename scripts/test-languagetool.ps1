# Integration smoke tests: local Docker LanguageTool + public Cloudflare URL.
# Usage: .\scripts\test-languagetool.ps1
# Optional: .\scripts\test-languagetool.ps1 -PublicUrl "https://....trycloudflare.com"

param(
    [string]$LocalUrl = "http://127.0.0.1:8081",
    [string]$PublicUrl = "https://berry-wow-reflected-templates.trycloudflare.com",
    [string]$Language = "en-US",
    [string]$TextWithError = "This is an test.",
    [string]$TextClean = "Hello world."
)

function Test-LanguageTool {
    param([string]$Name, [string]$BaseUrl, [string]$Text, [bool]$ExpectMatch)

    $endpoint = "$BaseUrl/v2/check"
    Write-Host "`n=== $Name ===" -ForegroundColor Cyan
    Write-Host "POST $endpoint"

    try {
        $json = curl.exe -s -m 30 -d "language=$Language" -d "text=$Text" $endpoint
        if (-not $json) {
            Write-Host "FAIL: empty response" -ForegroundColor Red
            return $false
        }
        $data = $json | ConvertFrom-Json
        if ($data.software.name -ne "LanguageTool") {
            Write-Host "FAIL: unexpected software name" -ForegroundColor Red
            return $false
        }
        $count = @($data.matches).Count
        if ($ExpectMatch -and $count -lt 1) {
            Write-Host "FAIL: expected >=1 match, got $count" -ForegroundColor Red
            return $false
        }
        if (-not $ExpectMatch -and $count -gt 0) {
            Write-Host "WARN: expected no matches, got $count (may still be OK)" -ForegroundColor Yellow
        }
        Write-Host "OK: LanguageTool $($data.software.version), matches=$count" -ForegroundColor Green
        if ($count -ge 1) {
            Write-Host "  first: $($data.matches[0].shortMessage) -> $($data.matches[0].replacements[0].value)"
        }
        return $true
    }
    catch {
        Write-Host "FAIL: $_" -ForegroundColor Red
        return $false
    }
}

$ok = $true
$ok = (Test-LanguageTool "Local (Docker)" $LocalUrl $TextWithError $true) -and $ok
$ok = (Test-LanguageTool "Local clean text" $LocalUrl $TextClean $false) -and $ok
$ok = (Test-LanguageTool "Public (Vercel path)" $PublicUrl $TextWithError $true) -and $ok
$ok = (Test-LanguageTool "Public clean text" $PublicUrl $TextClean $false) -and $ok

if ($ok) {
    Write-Host "`nAll checks passed." -ForegroundColor Green
    exit 0
}
Write-Host "`nSome checks failed. Is Docker container 'languagetool' up? Is cloudflared tunnel running?" -ForegroundColor Red
exit 1
