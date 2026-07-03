# Run case_001 full flow, then open key result images in demo order.
# Usage (from repo root):
#   .\scripts\run_case001_and_show_results.ps1
# Optional:
#   .\scripts\run_case001_and_show_results.ps1 -SkipRun
#   .\scripts\run_case001_and_show_results.ps1 -SecondsBetween 6

param(
    [int]$SecondsBetween = 4,
    [switch]$SkipRun
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path $PSScriptRoot -Parent
Set-Location $RepoRoot

$DebugDir = Join-Path $RepoRoot "workspace\debug"

# Demo order (ASCII labels only - avoids PS encoding issues on Windows)
$ShowFiles = @(
    @{ Path = "case10_assembly_drawing_tp_marked.png"; Label = "1/7 locator TP green circle" },
    @{ Path = "case10_assembly_largest_ic_box.png"; Label = "2/7 locator largest IC red box" },
    @{ Path = "case10_largest_ic_box.png"; Label = "3/7 board largest IC red box" },
    @{ Path = "step02_locator_front_anchor.png"; Label = "4/7 step02 locator anchor" },
    @{ Path = "step02_board_front_anchor.png"; Label = "5/7 step02 board anchor" },
    @{ Path = "case12_board_approx_overlay_opencv.png"; Label = "6/7 case12 align overlay" },
    @{ Path = "step08_final_tp.png"; Label = "7/7 final TP marker" },
    @{ Path = "step08_result.json"; Label = "final pixel JSON" }
)

function Show-ResultArtifacts {
    Write-Host ""
    Write-Host "========== Opening demo artifacts ==========" -ForegroundColor Cyan
    $opened = 0
    foreach ($item in $ShowFiles) {
        $full = Join-Path $DebugDir $item.Path
        if (-not (Test-Path $full)) {
            Write-Host "[skip] missing: $($item.Path)" -ForegroundColor Yellow
            continue
        }
        Write-Host "[open] $($item.Label) -> $($item.Path)" -ForegroundColor Green
        Start-Process $full
        $opened++
        if ($SecondsBetween -gt 0) {
            Start-Sleep -Seconds $SecondsBetween
        }
    }
    if ($opened -eq 0) {
        Write-Host "No demo files found under $DebugDir" -ForegroundColor Red
        return 1
    }
    Write-Host ""
    Write-Host "Opened $opened file(s)." -ForegroundColor Cyan
    return 0
}

if (-not $SkipRun) {
    Write-Host "========== Running case_001_tp12_front ==========" -ForegroundColor Cyan
    if (Test-Path ".\.venv\Scripts\Activate.ps1") {
        . .\.venv\Scripts\Activate.ps1
    }
    Remove-Item -Recurse -Force workspace\debug, workspace\runs -ErrorAction SilentlyContinue
    python -m agent run data/cases/case_001_tp12_front/task.yaml --name case_001_demo
    $exit = $LASTEXITCODE
    if ($exit -ne 0) {
        Write-Host "Agent exited with code $exit; skip opening images." -ForegroundColor Red
        exit $exit
    }
    Write-Host "Agent finished (finish-tool-called expected)." -ForegroundColor Green
}

Show-ResultArtifacts | Out-Null
