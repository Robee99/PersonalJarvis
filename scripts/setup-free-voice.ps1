<#
.SYNOPSIS
  Turn an installed Personal Jarvis into a free, full-time voice agent.

.DESCRIPTION
  1. Starts the free Step 3.7 Flash gateway (Nous) when it is not running.
  2. Starts a llama-server with the local Gemma 12B GGUF when one is found.
  3. Starts Jarvis when it is not running.
  4. Runs `jarvis system free-voice`, which sets Pipeline voice, the Step brain,
     Gemma as the local deep tier, a route policy that blocks paid providers,
     free speech-to-text/text-to-speech and the PC-control MCP servers, all
     through the app's own settings API.
  5. Adds a Startup entry so both servers come up after a reboot
     (skip with -NoAtLogon).

  Run it in PowerShell:
    irm https://raw.githubusercontent.com/Robee99/PersonalJarvis/jarvis/local-qwen-build/scripts/setup-free-voice.ps1 | iex
  Every step prints what it did; nothing is deleted.
#>
param(
    [string]$GatewayScript = "C:\paperclip\tools\nous-free-proxy.mjs",
    [int]$GatewayPort = 11436,
    [int]$LocalPort = 11438,
    [string]$GemmaPath = "",
    [string]$LlamaServer = "",
    [string]$JarvisDir = (Join-Path $env:LOCALAPPDATA "Programs\Personal Jarvis"),
    [switch]$NoAtLogon
)

$ErrorActionPreference = "Continue"

function Say([string]$Status, [string]$Text) { Write-Host ("{0,-5} {1}" -f $Status, $Text) }

function Test-Port([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $wait = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        return ($wait.AsyncWaitHandle.WaitOne(800) -and $client.Connected)
    } catch { return $false } finally { $client.Close() }
}

function Wait-Url([string]$Url, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try { Invoke-RestMethod -Uri $Url -TimeoutSec 3 | Out-Null; return $true } catch { Start-Sleep -Seconds 2 }
    }
    return $false
}

# --- 1. Free Step 3.7 Flash gateway ---------------------------------------
$gatewayCmd = $null
if (Test-Port $GatewayPort) {
    Say "OK" "Step gateway already running on port $GatewayPort"
} elseif (Test-Path $GatewayScript) {
    $node = (Get-Command node -ErrorAction SilentlyContinue).Source
    if ($node) {
        Start-Process -FilePath $node -ArgumentList "`"$GatewayScript`"" -WindowStyle Hidden
        $gatewayCmd = "start `"`" /min `"$node`" `"$GatewayScript`""
        if (Wait-Url "http://127.0.0.1:$GatewayPort/v1/models" 20) { Say "SET" "Started the Step gateway" }
        else { Say "FAIL" "Started the Step gateway but it does not answer on port $GatewayPort" }
    } else { Say "FAIL" "Node.js not found, so the Step gateway cannot start" }
} else {
    Say "FAIL" "No Step gateway at $GatewayScript (pass -GatewayScript)"
}
if (-not $gatewayCmd -and (Test-Path $GatewayScript)) {
    $node = (Get-Command node -ErrorAction SilentlyContinue).Source
    if ($node) { $gatewayCmd = "start `"`" /min `"$node`" `"$GatewayScript`"" }
}

# --- 2. Gemma on llama-server ---------------------------------------------
$localServer = $null
$llamaCmd = $null
if (-not $GemmaPath) {
    $hub = Join-Path $env:USERPROFILE ".cache\huggingface\hub"
    $GemmaPath = Get-ChildItem -Path $hub -Recurse -Filter "*.gguf" -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match "gemma" -and $_.Name -match "12b" -and $_.Name -notmatch "mtp|mmproj|draft" } |
        Sort-Object { $_.Name -notmatch "qat" }, Length |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $LlamaServer) {
    $LlamaServer = (Get-Command llama-server -ErrorAction SilentlyContinue).Source
    if (-not $LlamaServer) {
        $LlamaServer = Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA "hermes") -Recurse -Filter "llama-server.exe" -ErrorAction SilentlyContinue |
            Select-Object -First 1 -ExpandProperty FullName
    }
}
if (Test-Port $LocalPort) {
    $localServer = "http://127.0.0.1:$LocalPort"
    Say "OK" "Local model server already running on port $LocalPort"
} elseif ($GemmaPath -and $LlamaServer) {
    foreach ($layers in @(99, 24, 0)) {
        $serverArgs = "-m `"$GemmaPath`" --host 127.0.0.1 --port $LocalPort -c 16384 -ngl $layers --jinja"
        $proc = Start-Process -FilePath $LlamaServer -ArgumentList $serverArgs -WindowStyle Hidden -PassThru
        if (Wait-Url "http://127.0.0.1:$LocalPort/v1/models" 180) {
            $localServer = "http://127.0.0.1:$LocalPort"
            $llamaCmd = "start `"`" /min `"$LlamaServer`" $serverArgs"
            Say "SET" "Gemma running on port $LocalPort ($layers GPU layers): $GemmaPath"
            break
        }
        if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
        Say "INFO" "Gemma did not start with $layers GPU layers, trying fewer"
    }
    if (-not $localServer) { Say "FAIL" "Gemma did not start (not enough memory?)" }
} else {
    if (-not $GemmaPath) { Say "SKIP" "No Gemma 12B .gguf found (pass -GemmaPath)" }
    if (-not $LlamaServer) { Say "SKIP" "No llama-server.exe found (pass -LlamaServer)" }
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
$cliArgs = @("system", "free-voice", "--gateway", "http://127.0.0.1:$GatewayPort")
if ($localServer) { $cliArgs += @("--local-server", $localServer) }
& $cli @cliArgs

# --- 5. Start both servers after a reboot ----------------------------------
if (-not $NoAtLogon) {
    $startup = [Environment]::GetFolderPath("Startup")
    if ($startup -and ($gatewayCmd -or $llamaCmd)) {
        $file = Join-Path $startup "jarvis-free-servers.cmd"
        $lines = @("@echo off")
        if ($gatewayCmd) { $lines += $gatewayCmd }
        if ($llamaCmd) { $lines += $llamaCmd }
        Set-Content -Path $file -Value $lines -Encoding ASCII
        Say "SET" "Servers start at logon ($file)"
    }
}
