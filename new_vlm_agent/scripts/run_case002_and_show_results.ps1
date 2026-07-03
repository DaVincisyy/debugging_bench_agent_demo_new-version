# Run case_002 (Step3+ fast path), then open key images for demo recording.
# Usage (from repo root):
#   .\scripts\run_case002_and_show_results.ps1
# Optional:
#   .\scripts\run_case002_and_show_results.ps1 -SkipRun
#   .\scripts\run_case002_and_show_results.ps1 -SecondsBetween 6

param(
    [int]$SecondsBetween = 4,
    [switch]$SkipRun
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path $PSScriptRoot -Parent
Set-Location $RepoRoot

$DebugDir = Join-Path $RepoRoot "workspace\debug"
$CaseDir = Join-Path $RepoRoot "data\cases\case_002_tp332_front"

# Demo order for case_002 (pre-marked Step2 -> Part D -> finish)
$ShowFiles = @(
    @{ Path = (Join-Path $CaseDir "位号图7.29_01(4).png"); Label = "1/6 input locator (green TP + red IC)" },
    @{ Path = (Join-Path $CaseDir "largest_chip_marked_pins_included_v11.png"); Label = "2/6 input board (red IC)" },
    @{ Path = (Join-Path $DebugDir "step02_locator_front_anchor.png"); Label = "3/6 step02 locator anchor" },
    @{ Path = (Join-Path $DebugDir "step02_board_front_anchor.png"); Label = "4/6 step02 board anchor" },
    @{ Path = (Join-Path $DebugDir "case12_board_approx_overlay_opencv.png"); Label = "5/6 case12 align overlay" },
    @{ Path = (Join-Path $DebugDir "step08_final_tp.png"); Label = "6/6 final TP marker" },
    @{ Path = (Join-Path $DebugDir "step08_result.json"); Label = "final pixel JSON" }
)

function Show-ResultArtifacts {
    Write-Host ""
    Write-Host "========== Opening case_002 demo artifacts ==========" -ForegroundColor Cyan
    $opened = 0
    foreach ($item in $ShowFiles) {
        $full = $item.Path
        if (-not (Test-Path $full)) {
            $name = Split-Path $full -Leaf
            Write-Host "[skip] missing: $name" -ForegroundColor Yellow
            continue
        }
        Write-Host "[open] $($item.Label) -> $(Split-Path $full -Leaf)" -ForegroundColor Green
        Start-Process $full
        $opened++
        if ($SecondsBetween -gt 0) {
            Start-Sleep -Seconds $SecondsBetween
        }
    }
    if ($opened -eq 0) {
        Write-Host "No demo files found." -ForegroundColor Red
        return 1
    }
    Write-Host ""
    Write-Host "Opened $opened file(s)." -ForegroundColor Cyan
    return 0
}

if (-not $SkipRun) {
    Write-Host "========== Running case_002_tp332_front ==========" -ForegroundColor Cyan
    if (Test-Path ".\.venv\Scripts\Activate.ps1") {
        . .\.venv\Scripts\Activate.ps1
    }
    Remove-Item -Recurse -Force workspace\debug, workspace\runs -ErrorAction SilentlyContinue
    python -m agent run data/cases/case_002_tp332_front/task.yaml --name case_002_demo
    $exit = $LASTEXITCODE
    if ($exit -ne 0) {
        Write-Host "Agent exited with code $exit; skip opening images." -ForegroundColor Red
        exit $exit
    }
    Write-Host "Agent finished (finish-tool-called expected)." -ForegroundColor Green
}

Show-ResultArtifacts | Out-Null
