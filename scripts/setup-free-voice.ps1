<#
.SYNOPSIS
  Make Hermes Agent the brain of an installed Personal Jarvis.

.DESCRIPTION
  User -> Jarvis (voice, UI) -> Hermes Agent -> Hermes picks the model
  (local Qwen, local Gemma, a free cloud model) -> Hermes tools -> Jarvis.

  1. Turns on Hermes's local API server (API_SERVER_ENABLED, plus a random
     API_SERVER_KEY when none is set) in Hermes's own .env. The key never
     leaves that file: Jarvis reads it there.
  2. Starts `hermes gateway` when the API server is not answering.
  3. Starts Jarvis when it is not running.
  4. Runs `jarvis system free-voice`, which makes Hermes the brain, hands
     missions to Hermes, switches Jarvis's own model routing off, blocks paid
     providers, and asks Hermes once per -CheckModel so the report shows which
     model Hermes really ran.
  5. Adds a Startup entry so the Hermes gateway comes up after a reboot
     (skip with -NoAtLogon).

  Run it in PowerShell:
    irm https://raw.githubusercontent.com/Robee99/PersonalJarvis/jarvis/local-qwen-build/scripts/setup-free-voice.ps1 | iex
  Every step prints what it did; nothing is deleted.
#>
param(
    [string]$HermesHome = (Join-Path $env:LOCALAPPDATA "hermes"),
    [int]$HermesPort = 8642,
    [string[]]$CheckModel = @("local-qwen::qwen", "local-gemma::gemma-4-12b-qat"),
    [string]$JarvisDir = (Join-Path $env:LOCALAPPDATA "Programs\Personal Jarvis"),
    [switch]$NoAtLogon
)

$ErrorActionPreference = "Continue"

function Say([string]$Status, [string]$Text) { Write-Host ("{0,-5} {1}" -f $Status, $Text) }

function Wait-Url([string]$Url, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try { Invoke-RestMethod -Uri $Url -TimeoutSec 3 | Out-Null; return $true } catch { Start-Sleep -Seconds 2 }
    }
    return $false
}

# --- 1. Hermes API server switched on in Hermes's own .env -----------------
$envFile = Join-Path $HermesHome ".env"
if (-not (Test-Path $HermesHome)) {
    Say "FAIL" "No Hermes Agent at $HermesHome (pass -HermesHome)"
    return
}
$lines = @()
if (Test-Path $envFile) { $lines = @(Get-Content -Path $envFile) }
$changed = $false
if (-not ($lines | Where-Object { $_ -match '^\s*API_SERVER_ENABLED\s*=\s*true' })) {
    $lines += "API_SERVER_ENABLED=true"; $changed = $true
}
if (-not ($lines | Where-Object { $_ -match '^\s*API_SERVER_KEY\s*=\s*\S' })) {
    $bytes = New-Object byte[] 24
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $lines += "API_SERVER_KEY=" + [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
    $changed = $true
}
if ($changed) {
    if (Test-Path $envFile) { Copy-Item $envFile "$envFile.bak-jarvis" -Force }
    # No byte-order mark: Hermes's .env reader would glue it to the first key.
    [System.IO.File]::WriteAllLines($envFile, [string[]]$lines, (New-Object System.Text.UTF8Encoding($false)))
    Say "SET" "Hermes API server switched on in $envFile (backup .env.bak-jarvis)"
} else { Say "OK" "Hermes API server already switched on" }

# --- 2. Hermes gateway running ----------------------------------------------
$hermesUrl = "http://127.0.0.1:$HermesPort"
$hermes = (Get-Command hermes -ErrorAction SilentlyContinue).Source
$gatewayCmd = $null
if ($hermes) { $gatewayCmd = "start `"`" /min `"$hermes`" gateway" }
if (Wait-Url "$hermesUrl/health" 2) {
    if ($changed -and $hermes) {
        Say "INFO" "Restart the Hermes gateway once so it reads the new API server settings"
    } else { Say "OK" "Hermes API server answering on port $HermesPort" }
} elseif ($hermes) {
    Start-Process -FilePath $hermes -ArgumentList "gateway" -WindowStyle Minimized
    if (Wait-Url "$hermesUrl/health" 60) { Say "SET" "Started the Hermes gateway" }
    else { Say "FAIL" "Started 'hermes gateway' but its API server does not answer on port $HermesPort" }
} else {
    Say "FAIL" "The 'hermes' command is not on PATH, so the gateway cannot start"
}

# --- 3. Jarvis running ------------------------------------------------------
$cli = Join-Path $JarvisDir "jarvis.exe"
$gui = Join-Path $JarvisDir "PersonalJarvis.exe"
if (-not (Test-Path $cli)) {
    Say "FAIL" "Jarvis is not installed in $JarvisDir (install the newest installer first)"
    return
}
$session = Join-Path $env:LOCALAPPDATA "Jarvis\session.json"
if (-not (Test-Path $session)) {
    Start-Process -FilePath $gui
    $deadline = (Get-Date).AddSeconds(120)
    while (-not (Test-Path $session) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 2 }
    Start-Sleep -Seconds 20
    Say "SET" "Started Jarvis"
} else { Say "OK" "Jarvis is running" }

# --- 4. Configure through the app ------------------------------------------
$cliArgs = @("system", "free-voice", "--hermes", $hermesUrl)
foreach ($model in $CheckModel) { if ($model) { $cliArgs += @("--check-model", $model) } }
& $cli @cliArgs

# --- 5. Hermes gateway after a reboot ----------------------------------------
if (-not $NoAtLogon -and $gatewayCmd) {
    $startup = [Environment]::GetFolderPath("Startup")
    if ($startup) {
        $file = Join-Path $startup "jarvis-hermes-gateway.cmd"
        Set-Content -Path $file -Value @("@echo off", $gatewayCmd) -Encoding ASCII
        Say "SET" "Hermes gateway starts at logon ($file)"
    }
}
