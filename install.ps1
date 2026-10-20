# jlc-pcb-layout installer for Windows (PowerShell 5.1 compatible)
# Usage: powershell -ExecutionPolicy Bypass -File .\install.ps1
param(
    [string]$CodexSkills     = "$env:USERPROFILE\.codex\skills",
    [string]$WorkbuddySkills = "$env:USERPROFILE\.workbuddy\skills"
)

$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
if ([string]::IsNullOrEmpty($root)) { $root = (Get-Location).Path }

$src  = Join-Path $root "jlc-pcb-layout"
$name = "jlc-pcb-layout"

Write-Host ""
Write-Host "=== jlc-pcb-layout installer ===" -ForegroundColor Cyan
Write-Host "source: $src"

if (-not (Test-Path (Join-Path $src "SKILL.md"))) {
    Write-Host "ERROR: SKILL.md not found under $src" -ForegroundColor Red
    Write-Host "Run this script from the directory that contains the jlc-pcb-layout folder."
    exit 1
}

# --- validate frontmatter of the source SKILL.md ---
$head = Get-Content (Join-Path $src "SKILL.md") -TotalCount 1
if ($head.Trim() -ne "---") {
    Write-Host "ERROR: SKILL.md does not start with YAML frontmatter delimiter" -ForegroundColor Red
    exit 1
}

$targets = @()
if ($CodexSkills)     { $targets += $CodexSkills }
if ($WorkbuddySkills) { $targets += $WorkbuddySkills }

$installed = @()
foreach ($t in $targets) {
    if (-not (Test-Path $t)) {
        New-Item -ItemType Directory -Path $t -Force | Out-Null
    }
    $dest = Join-Path $t $name
    if (Test-Path $dest) {
        Remove-Item -Recurse -Force $dest
        Write-Host "removed old: $dest"
    }
    Copy-Item -Recurse -Force $src $dest

    # strip any stray bytecode cache
    Get-ChildItem -Path $dest -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

    $skillMd = Join-Path $dest "SKILL.md"
    if (Test-Path $skillMd) {
        $count = (Get-ChildItem -Path $dest -Recurse -File).Count
        Write-Host ("installed: {0}  ({1} files)" -f $dest, $count) -ForegroundColor Green
        $installed += $dest
    } else {
        Write-Host "ERROR: copy failed, SKILL.md missing at $dest" -ForegroundColor Red
        exit 1
    }
}

Write-Host ""
Write-Host "=== verify ===" -ForegroundColor Cyan
foreach ($d in $installed) {
    $txt = Get-Content (Join-Path $d "SKILL.md") -Raw
    $hasName = $txt -match '(?m)^name:\s*jlc-pcb-layout\s*$'
    $hasDesc = $txt -match '(?m)^description:\s*\S'
    Write-Host ("  {0}" -f $d)
    Write-Host ("    frontmatter name : {0}" -f $hasName)
    Write-Host ("    frontmatter desc : {0}" -f $hasDesc)
    if (-not ($hasName -and $hasDesc)) {
        Write-Host "    WARNING: frontmatter incomplete, Codex may not load this skill" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "NEXT STEP: fully quit Codex (exit the process, not just close the window), then reopen it."
Write-Host 'Then in any chat type:  $jlc-pcb-layout   or just ask to lay out a PCB in EasyEDA Pro.'
Write-Host ""
Write-Host "Self-test:"
Write-Host ('  cd "' + $installed[0] + '"')
Write-Host '  python scripts/rules.py width --current 3 --copper 1oz'
Write-Host '  python scripts/check.py examples/snapshot.demo.json --min-severity WARN'
Write-Host ""
