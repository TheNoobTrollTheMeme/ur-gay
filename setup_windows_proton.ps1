# Windows-side AI development, keeping the Raspberry Pi as the hardware controller.
# Run in PowerShell on the PC while Proton VPN is connected.
$ErrorActionPreference = "Stop"
$repo = "https://github.com/TheNoobTrollTheMeme/ur-gay.git"
$branch = "codex-pi-safe-setup"
$workspace = Join-Path $HOME "robot-ai-windows"
Write-Host "=== Robot AI setup: Windows + Proton VPN ==="
Write-Host "No Pi configuration or motor commands will be changed."
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { throw "Git missing, and winget not available. Install Git for Windows." }
  winget install --id Git.Git --exact --accept-package-agreements --accept-source-agreements
  $env:PATH += ";$env:ProgramFiles\Git\cmd"
}
# npm is an officially supported Codex installation path, avoiding the PowerShell installer.
if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
  Write-Host "Installing Node.js LTS (includes npm)..."
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { throw "Node.js/npm missing and winget unavailable. Install Node.js LTS from nodejs.org." }
  winget install --id OpenJS.NodeJS.LTS --exact --accept-package-agreements --accept-source-agreements
  if ($LASTEXITCODE -ne 0) { throw "Node.js installation failed." }
  $env:PATH = "$env:ProgramFiles\nodejs;$env:APPDATA\npm;" + $env:PATH
}
if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw "npm missing. Restart PowerShell and rerun this installer." }
if (-not (Get-Command codex.cmd -ErrorAction SilentlyContinue) -and -not (Get-Command codex.exe -ErrorAction SilentlyContinue)) {
  Write-Host "Installing Codex using npm..."
  & npm.cmd install --global @openai/codex
  if ($LASTEXITCODE -ne 0) { throw "npm Codex installation failed." }
  $env:PATH = "$env:APPDATA\npm;" + $env:PATH
}
if (-not (Get-Command codex -ErrorAction SilentlyContinue)) { throw "Codex not found. Restart PowerShell and rerun." }
if (Test-Path (Join-Path $workspace ".git")) {
  Push-Location $workspace
  try {
    $dirty = git status --porcelain
    if ($LASTEXITCODE -ne 0) { throw "Could not inspect existing workspace." }
    if ($dirty) { throw "Workspace has local changes. Refusing to overwrite them. Inspect $workspace." }
    $current = (git branch --show-current).Trim()
    if ($current -ne $branch) { throw "Existing checkout on $current, expected $branch. Leaving intact." }
    git pull --ff-only origin $branch
    if ($LASTEXITCODE -ne 0) { throw "Git pull failed." }
  } finally { Pop-Location }
} elseif (Test-Path $workspace) {
  throw "Path already exists: $workspace. Refusing to overwrite."
} else {
  git clone --branch $branch --single-branch $repo $workspace
  if ($LASTEXITCODE -ne 0) { throw "Git clone failed." }
}
Write-Host "Codex sign-in may open your browser. Stay connected to Proton VPN."
codex login status *> $null
if ($LASTEXITCODE -ne 0) {
  codex login
  if ($LASTEXITCODE -ne 0) { throw "Codex sign-in failed." }
}
$task = @'
Read AGENTS.md and all existing motion/servo scripts. Explain what the existing M and P sequences do and which hardware details cannot be inferred from code. Work ONLY in this isolated Windows checkout, which has no robot servo connection. Implement and test a fail-closed simulated safety supervisor: fault-latched safe stops, timeout/stale-feedback detection, tracking error, load/current and heat checks only when confirmed supported, mock telemetry, simulated collision checking with uncalibrated geometry clearly flagged, two user-recorded waypoints preserved, and a readable SAFETY_STATUS.md report. Never use real hardware or servo connections; do not deploy or run motor-control scripts, do not modify calibration or push to main. Make tests simulation-only.
'@
Set-Location $workspace
Write-Host "Running Codex safety implementation on Windows (no robot attached)..."
codex exec --full-auto $task
if ($LASTEXITCODE -ne 0) { throw "Codex task failed; inspect $workspace and retry manually." }
Write-Host "Done. Review SAFETY_STATUS.md and git diff. Nothing deployed to Pi."
Write-Host "For future work: cd '$workspace'; codex"
