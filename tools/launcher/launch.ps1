<#
  MT5 intel launcher.

  Brings up the whole read-only research console from one double-click:
    - the Next.js dashboard (the UI you browse),
    - the MT5 watch loop (feeds the Live tab),
    - one diagnostic run so the Latest tab has fresh numbers.

  It also heals the errors that actually happen on this machine: a missing venv,
  missing node_modules, a busy port, a corrupt .next cache, and the case where the
  console is already running. Everything is logged to logs/launcher.log, which is
  rotated once it passes 2 MB (3 backups kept) so the logs folder stays bounded.
  The dashboard's own output is capped the same way while it runs - the launcher
  reads the dashboard's streams itself, so it can trim dashboard.log and
  dashboard.err.log mid-session, not just at launch. After the dashboard is up it
  warms every tab so the first click is not a cold compile.

  Options:
    -Port <n>        preferred dashboard port (default 52577; falls through if busy)
    -Stop            stop everything this launcher started, then exit
    -Repair          heal everything at once: stop the console, clear the .next
                     cache, reinstall the Python and Node deps, and start on a
                     fresh port
    -NoWatcher       don't start the MT5 watch loop
    -NoDiagnostic    don't run a diagnostic pass
    -NoBrowser       don't open the browser
    -Reinstall       force reinstall of the Python venv deps and node_modules
#>

[CmdletBinding()]
param(
  [int]$Port = 52577,
  [switch]$Stop,
  [switch]$NoWatcher,
  [switch]$NoDiagnostic,
  [switch]$NoBrowser,
  [switch]$Reinstall,
  [switch]$Repair
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = (Resolve-Path (Join-Path $ScriptDir '..\..')).Path
$DashboardDir = Join-Path $Root 'dashboard'
$LogDir = Join-Path $Root 'logs'
$PidFile = Join-Path $LogDir 'launcher-pids.json'
$EnvFile = Join-Path $Root '.env'
$EnvExample = Join-Path $Root '.env.example'
$VenvPython = Join-Path $Root '.venv\Scripts\python.exe'
$NextCache = Join-Path $DashboardDir '.next'
$Logo = Join-Path $Root 'assets\mt5-intel.ico'

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir 'launcher.log'

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
try { $Host.UI.RawUI.WindowTitle = 'MT5 intel - research console' } catch {}

function Write-Line {
  param([string]$Text = '', [string]$Color = 'Gray')
  $stamp = (Get-Date).ToString('HH:mm:ss')
  Write-Host ("[{0}] {1}" -f $stamp, $Text) -ForegroundColor $Color
  try {
    Add-Content -Path $LogFile -Value ("[{0}] {1}" -f (Get-Date).ToString('s'), $Text) -ErrorAction SilentlyContinue
  } catch {}
}

function Write-Banner {
  Write-Host ''
  Write-Host '  MT5 intel - read-only research console' -ForegroundColor White
  Write-Host '  It places no orders. This window is the control panel.' -ForegroundColor DarkGray
  Write-Host ('  ' + ('-' * 56)) -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------
function Test-PortInUse {
  param([int]$PortNumber)
  $client = New-Object System.Net.Sockets.TcpClient
  try {
    $async = $client.BeginConnect('127.0.0.1', $PortNumber, $null, $null)
    if ($async.AsyncWaitHandle.WaitOne(400)) {
      try { $client.EndConnect($async); return $true } catch { return $false }
    }
    return $false
  } catch {
    return $false
  } finally {
    try { $client.Close() } catch {}
  }
}

function Get-HttpStatus {
  param([string]$Url)
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try {
    return [int](Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 4).StatusCode
  } catch {
    return 0
  } finally {
    $ErrorActionPreference = $prev
  }
}

function Test-IsOurDashboard {
  param([int]$PortNumber)
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try {
    $page = Invoke-WebRequest -Uri ("http://localhost:{0}/" -f $PortNumber) -UseBasicParsing -TimeoutSec 4
    return ($page.Content -match 'MT5 intel')
  } catch {
    return $false
  } finally {
    $ErrorActionPreference = $prev
  }
}

function Test-PythonImport {
  param([string]$Module)
  if (-not (Test-Path $VenvPython)) { return $false }
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try {
    & $VenvPython -c "import $Module" *> $null
    return ($LASTEXITCODE -eq 0)
  } catch {
    return $false
  } finally {
    $ErrorActionPreference = $prev
  }
}

function Stop-Tree {
  param([int]$ProcId)
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { & taskkill.exe /PID $ProcId /T /F *> $null } catch {} finally { $ErrorActionPreference = $prev }
}

function Save-Pids {
  param([object[]]$Items)
  try {
    # -InputObject keeps it an array even when only one process is tracked.
    $json = ConvertTo-Json -InputObject @($Items | Where-Object { $_ }) -Depth 4
    # UTF-8 without a BOM, so other tools can read the file directly.
    [System.IO.File]::WriteAllText($PidFile, $json, (New-Object System.Text.UTF8Encoding($false)))
  } catch {}
}

function Load-Pids {
  if (-not (Test-Path $PidFile)) { return @() }
  try { return @(Get-Content $PidFile -Raw | ConvertFrom-Json) } catch { return @() }
}

function Stop-Tracked {
  param([switch]$Quiet)

  $stopped = 0
  foreach ($item in (Load-Pids)) {
    if (-not $item) { continue }
    $procId = 0
    if ($item.PSObject.Properties.Name -contains 'pid') { $procId = [int]$item.pid }
    if ($procId -le 0) { continue }
    if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
      Stop-Tree -ProcId $procId
      $stopped++
      if (-not $Quiet) { Write-Line ("Stopped {0} (pid {1})" -f $item.name, $procId) 'DarkGray' }
    }
  }
  Remove-Item $PidFile -Force -ErrorAction SilentlyContinue

  # Backstop: a dashboard still listening on one of our ports but not tracked.
  # Get-NetTCPConnection may not exist on every Windows build, so this is best effort.
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try {
    foreach ($p in $Port..($Port + 5)) {
      if (Test-IsOurDashboard $p) {
        $conn = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue |
          Select-Object -First 1
        if ($conn -and $conn.OwningProcess) {
          Stop-Tree -ProcId ([int]$conn.OwningProcess)
          $stopped++
          if (-not $Quiet) { Write-Line ("Stopped dashboard on port {0} (pid {1})" -f $p, $conn.OwningProcess) 'DarkGray' }
        }
      }
    }
  } catch {
    if (-not $Quiet) { Write-Line 'Could not scan ports for a leftover dashboard.' 'DarkGray' }
  } finally {
    $ErrorActionPreference = $prev
  }
  return $stopped
}

# ---------------------------------------------------------------------------
# Log hygiene: cap and rotate so logs/ cannot grow without bound over many runs
# ---------------------------------------------------------------------------
$LogMaxBytes = 2MB
$LogKeep = 3
# Lower the cap (e.g. for testing) by setting MT5_LOG_CAP_BYTES to a byte count.
if ($env:MT5_LOG_CAP_BYTES -and [long]$env:MT5_LOG_CAP_BYTES -gt 0) { $LogMaxBytes = [long]$env:MT5_LOG_CAP_BYTES }

function Rotate-Log {
  param([string]$Path, [long]$MaxBytes = $LogMaxBytes, [int]$Keep = $LogKeep)
  if (-not (Test-Path $Path)) { return }
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try {
    if ((Get-Item $Path).Length -lt $MaxBytes) { return }
    # Shift .(Keep-1) -> .Keep, ..., .1 -> .2, then current -> .1. Delete the oldest.
    for ($k = $Keep - 1; $k -ge 1; $k--) {
      $src = "$Path.$k"
      $dst = "$Path." + ($k + 1)
      if (Test-Path $src) {
        if (Test-Path $dst) { Remove-Item $dst -Force -ErrorAction SilentlyContinue }
        Move-Item -Force $src $dst
      }
    }
    Move-Item -Force $Path "$Path.1"
  } catch {
    # A locked or unreadable log is not worth failing the launch over.
  } finally {
    $ErrorActionPreference = $prev
  }
}

function Reset-RunLog {
  # Per-run logs start empty so they never accumulate across launches.
  param([string]$Path)
  Remove-Item $Path -Force -ErrorAction SilentlyContinue
}

function Format-Size {
  # Invariant formatting: this text is persisted in the log, so it must not vary by locale.
  param([long]$Bytes)
  $inv = [System.Globalization.CultureInfo]::InvariantCulture
  if ($Bytes -lt 1KB) { return "$Bytes B" }
  if ($Bytes -lt 1MB) { return [string]::Format($inv, '{0:N1} kB', $Bytes / 1KB) }
  return [string]::Format($inv, '{0:N2} MB', $Bytes / 1MB)
}

function Trim-Log {
  # Rewrite a log down to its newest half. This is only safe on a file the launcher
  # owns; Windows refuses to trim a file another process holds open.
  param([string]$Path, [long]$MaxBytes = $LogMaxBytes)
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try {
    if (-not (Test-Path $Path)) { return }
    $text = [System.IO.File]::ReadAllText($Path)
    $keep = [int][Math]::Max(0, $MaxBytes / 2)
    $start = [Math]::Max(0, $text.Length - $keep)
    $tail = $text.Substring($start)
    # Start at a line boundary so no half line survives the trim.
    $nl = $tail.IndexOf("`n")
    if ($nl -ge 0) { $tail = $tail.Substring($nl + 1) }
    $header = '[trimmed to the newest ~{0} of a capped log at {1}]{2}' -f (Format-Size $keep), (Get-Date).ToString('s'), [Environment]::NewLine
    [System.IO.File]::WriteAllText($Path, $header + $tail, (New-Object System.Text.UTF8Encoding($false)))
  } catch {
    # A failed trim must not stop the console.
  } finally {
    $ErrorActionPreference = $prev
  }
}

function Write-CappedLog {
  # Append buffered lines, then trim if the file passed the cap. Because the launcher
  # owns these files (it reads the dashboard's streams itself), it can trim them
  # mid-session - something it cannot do to a file Start-Process opened for a child.
  param([string]$Path, [string[]]$Lines, [long]$MaxBytes = $LogMaxBytes)
  if (-not $Lines -or $Lines.Count -eq 0) { return }
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try {
    $block = ($Lines -join [Environment]::NewLine) + [Environment]::NewLine
    [System.IO.File]::AppendAllText($Path, $block, (New-Object System.Text.UTF8Encoding($false)))
    if ((Get-Item $Path).Length -gt $MaxBytes) { Trim-Log -Path $Path -MaxBytes $MaxBytes }
  } catch {
  } finally {
    $ErrorActionPreference = $prev
  }
}

function Receive-Lines {
  # Non-blocking drain: pull every complete line the reader has, return the next task.
  param([System.IO.StreamReader]$Reader, $Task, $Buffer)
  while ($Task -and $Task.IsCompleted) {
    $line = $Task.Result
    if ($null -eq $line) { return $null }   # the stream closed with the process
    $Buffer.Add($line)
    try { $Task = $Reader.ReadLineAsync() } catch { return $null }
  }
  return $Task
}

function Start-DashboardProcess {
  # Start the dashboard with the launcher reading its output, so the log files stay
  # trimmable. (Start-Process -Redirect* hands the child a file that stays locked.)
  param([int]$PortNumber)
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $env:ComSpec
  $psi.Arguments = ('/c npm run dev -- -p {0}' -f $PortNumber)
  $psi.WorkingDirectory = $DashboardDir
  $psi.UseShellExecute = $false
  $psi.CreateNoWindow = $false
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  try { $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
  try { $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8 } catch {}
  $proc = New-Object System.Diagnostics.Process
  $proc.StartInfo = $psi
  [void]$proc.Start()
  return $proc
}

function New-DashboardState {
  param($Process, [string]$OutLog, [string]$ErrLog)
  $state = [pscustomobject]@{
    Process   = $Process
    OutLog    = $OutLog
    ErrLog    = $ErrLog
    OutTask   = $null
    ErrTask   = $null
    OutBuffer = (New-Object 'System.Collections.Generic.List[string]')
    ErrBuffer = (New-Object 'System.Collections.Generic.List[string]')
  }
  try { $state.OutTask = $Process.StandardOutput.ReadLineAsync() } catch {}
  try { $state.ErrTask = $Process.StandardError.ReadLineAsync() } catch {}
  return $state
}

function Drain-Dashboard {
  # Collect whatever the dashboard has written and write it out under the cap.
  # Called often: the OS pipe buffer is small, so an undrained stream blocks the child.
  param($State)
  if (-not $State -or -not $State.Process) { return }
  try {
    $State.OutTask = Receive-Lines -Reader $State.Process.StandardOutput -Task $State.OutTask -Buffer $State.OutBuffer
    $State.ErrTask = Receive-Lines -Reader $State.Process.StandardError -Task $State.ErrTask -Buffer $State.ErrBuffer
  } catch {}
  Write-CappedLog -Path $State.OutLog -Lines $State.OutBuffer
  Write-CappedLog -Path $State.ErrLog -Lines $State.ErrBuffer
  $State.OutBuffer.Clear(); $State.ErrBuffer.Clear()
}

# ---------------------------------------------------------------------------
# Warm the dashboard routes so the first click on a tab is not a cold compile
# ---------------------------------------------------------------------------
function Warm-Routes {
  param([string]$BaseUrl, $Dashboard)
  $routes = @('live', 'research', 'replays', 'runs', 'health')
  Write-Line 'Warming the dashboard tabs so the first click is instant...' 'DarkGray'
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try {
    foreach ($r in $routes) {
      $u = "{0}{1}" -f $BaseUrl, $r
      $sw = [System.Diagnostics.Stopwatch]::StartNew()
      try {
        Drain-Dashboard -State $Dashboard
        # A tab compiles on first request; allow generous time before giving up.
        $null = Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 90
        Write-Line ("  {0} ready ({1:N1}s)" -f $u, $sw.Elapsed.TotalSeconds) 'DarkGray'
      } catch {
        Write-Line ("  {0} did not warm up; it will compile on first visit." -f $u) 'DarkGray'
      } finally {
        Drain-Dashboard -State $Dashboard
        $sw.Stop()
      }
    }
  } finally {
    $ErrorActionPreference = $prev
  }
}

# Rotate the append-only launcher log before this run adds to it.
Rotate-Log $LogFile

# ---------------------------------------------------------------------------
# -Stop
# ---------------------------------------------------------------------------
if ($Stop) {
  Write-Banner
  Write-Line 'Stopping the MT5 intel console...' 'Cyan'
  $n = Stop-Tracked
  if ($n -eq 0) { Write-Line 'Nothing was running.' 'DarkGray' } else { Write-Line ("Done - {0} process(es) stopped." -f $n) 'Green' }
  exit 0
}

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
Write-Banner

$node = (Get-Command node.exe -ErrorAction SilentlyContinue)
$npmCmd = (Get-Command npm.cmd -ErrorAction SilentlyContinue)
if (-not $node -or -not $npmCmd) {
  Write-Line 'Node.js is not on PATH, so the dashboard cannot start.' 'Red'
  Write-Line 'Install the LTS build from https://nodejs.org and run this again.' 'Yellow'
  exit 3
}
Write-Line ("Node {0}  -  npm {1}" -f (& node --version), (& npm.cmd --version)) 'DarkGray'

$nodeMajor = [int]((& node --version).TrimStart('v').Split('.')[0])
if ($nodeMajor -lt 20) {
  Write-Line ("Node {0} is older than the 20.9 that Next.js 16 needs; the dashboard may fail to start." -f $nodeMajor) 'Yellow'
  Write-Line 'Install a current LTS from https://nodejs.org.' 'Yellow'
}

# .env presence (never read or print its values)
if (-not (Test-Path $EnvFile)) {
  if (Test-Path $EnvExample) {
    Copy-Item $EnvExample $EnvFile
    Write-Line 'Created .env from .env.example.' 'Yellow'
    Write-Line 'Fill in MT5_LOGIN, MT5_PASSWORD (investor password) and MT5_SERVER, then run again.' 'Yellow'
  } else {
    Write-Line '.env is missing and there is no .env.example to copy.' 'Red'
  }
}

# ---------------------------------------------------------------------------
# -Repair: stop the console and clear the slate before the installs below
# ---------------------------------------------------------------------------
if ($Repair) {
  Write-Line 'Repair: stopping anything running and clearing the build cache...' 'Cyan'
  $stopped = Stop-Tracked -Quiet
  if ($stopped -gt 0) { Write-Line ("  stopped {0} process(es)" -f $stopped) 'DarkGray' }
  if (Test-Path $NextCache) {
    Remove-Item $NextCache -Recurse -Force -ErrorAction SilentlyContinue
    Write-Line '  removed the Next.js build cache (.next)' 'DarkGray'
  }
  Write-Line '  reinstalling Python and Node dependencies...' 'DarkGray'
}

# Python venv
if (-not (Test-Path $VenvPython)) {
  Write-Line 'No .venv found - creating one...' 'Yellow'
  $pyLauncher = (Get-Command py.exe -ErrorAction SilentlyContinue)
  $sysPython = (Get-Command python.exe -ErrorAction SilentlyContinue)
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try {
    if ($pyLauncher) { & $pyLauncher.Source -3 -m venv (Join-Path $Root '.venv') *> $null }
    elseif ($sysPython) { & $sysPython.Source -m venv (Join-Path $Root '.venv') *> $null }
  } finally { $ErrorActionPreference = $prev }
  if (Test-Path $VenvPython) { Write-Line 'Virtual environment created.' 'Green' }
  else { Write-Line 'Could not create the virtual environment; install Python 3.11+ from python.org.' 'Red' }
}

# Python deps
$needPyInstall = $Reinstall -or $Repair -or -not (Test-PythonImport 'MetaTrader5') -or -not (Test-PythonImport 'dotenv') -or -not (Test-PythonImport 'numpy')
if ((Test-Path $VenvPython) -and $needPyInstall) {
  Write-Line 'Installing/repairing Python dependencies (this can take a minute)...' 'Yellow'
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  Push-Location $Root
  try {
    & $VenvPython -m pip install --disable-pip-version-check --quiet --upgrade pip *> $null
    & $VenvPython -m pip install --disable-pip-version-check --quiet -e '.[dev]'
    if ($LASTEXITCODE -ne 0) { Write-Line 'pip install reported a problem; the dashboard still works, MT5 features may not.' 'Yellow' }
  } finally {
    Pop-Location
    $ErrorActionPreference = $prev
  }
}

# Node deps
$npmInstallNeeded = $Reinstall -or $Repair -or -not (Test-Path (Join-Path $DashboardDir 'node_modules\next'))
if ($npmInstallNeeded) {
  Write-Line 'Installing dashboard dependencies (this can take a minute)...' 'Yellow'
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  Push-Location $DashboardDir
  try {
    & $npmCmd.Source install --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { Write-Line 'npm install failed; check your connection and try again.' 'Red' }
  } finally {
    Pop-Location
    $ErrorActionPreference = $prev
  }
}

# ---------------------------------------------------------------------------
# Choose a port: reuse our dashboard if it is already up, otherwise the first free one
# ---------------------------------------------------------------------------
$chosen = 0
$alreadyRunning = $false
for ($i = 0; $i -lt 6; $i++) {
  $candidate = $Port + $i
  # -Repair always starts fresh, so it never adopts a console that is already listening.
  if (-not $Repair -and (Test-IsOurDashboard $candidate)) { $chosen = $candidate; $alreadyRunning = $true; break }
  if (-not (Test-PortInUse $candidate)) { $chosen = $candidate; break }
}
if ($chosen -eq 0) {
  Write-Line ("Ports {0}-{1} are all busy; close whatever is using them and run this again." -f $Port, ($Port + 5)) 'Red'
  exit 4
}

$dashProc = $null
$watchProc = $null
$diagProc = $null
$startedDashboard = $false

# ---------------------------------------------------------------------------
# Start the dashboard (with healing retries)
# ---------------------------------------------------------------------------
if ($alreadyRunning) {
  # Already up: just focus it. Starting a second watcher would double-journal the same bars.
  Write-Line ("The console is already running on port {0}." -f $chosen) 'Green'
  $url = "http://localhost:$chosen/"
  if (-not $NoBrowser) {
    Write-Line ("Opening {0}" -f $url) 'Cyan'
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { Start-Process $url } catch { Write-Line "Could not open the browser; open the link above manually." 'Yellow' }
    finally { $ErrorActionPreference = $prev }
  }
  Write-Line 'Nothing to start. Use Stop MT5 Intel.bat to shut it down.' 'DarkGray'
  exit 0
} else {
  $outLog = Join-Path $LogDir 'dashboard.log'
  $errLog = Join-Path $LogDir 'dashboard.err.log'
  $dash = $null
  $attempt = 0
  while ($attempt -lt 3 -and -not $startedDashboard) {
    $attempt++
    Reset-RunLog $outLog
    Reset-RunLog $errLog
    Write-Line ("Starting the dashboard on port {0} (attempt {1})..." -f $chosen, $attempt) 'Cyan'
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try {
      # The launcher owns the dashboard's output (rather than letting Start-Process
      # hand the child a locked file) so it can cap those logs while the session runs.
      $dashProc = Start-DashboardProcess -PortNumber $chosen
      $dash = New-DashboardState -Process $dashProc -OutLog $outLog -ErrLog $errLog
    } catch {
      Write-Line ("Could not launch the dashboard: {0}" -f $_.Exception.Message) 'Red'
      $dashProc = $null
    } finally { $ErrorActionPreference = $prev }

    if (-not $dashProc) { break }

    $deadline = (Get-Date).AddSeconds(180)
    $state = 'timeout'
    while ((Get-Date) -lt $deadline) {
      Drain-Dashboard -State $dash
      if ($dashProc.HasExited) { $state = 'exited'; break }
      if ((Get-HttpStatus ("http://localhost:{0}/" -f $chosen)) -eq 200) { $state = 'ready'; break }
      Start-Sleep -Milliseconds 500
    }
    Drain-Dashboard -State $dash

    if ($state -eq 'ready') { $startedDashboard = $true; break }

    $errText = if (Test-Path $errLog) { [string](Get-Content $errLog -Raw) } else { '' }
    Write-Line ("The dashboard did not come up ({0})." -f $state) 'Yellow'

    if ($dashProc -and -not $dashProc.HasExited) { Stop-Tree -ProcId $dashProc.Id }
    try { $dashProc.Dispose() } catch {}
    $dashProc = $null
    $dash = $null

    if ($errText -match 'EADDRINUSE|address already in use|Port .* is in use') {
      Write-Line 'The port was taken by another program - trying the next one.' 'Yellow'
      for ($j = $chosen + 1; $j -le ($Port + 9); $j++) {
        if (-not (Test-PortInUse $j)) { $chosen = $j; break }
      }
      continue
    }
    if ($errText -match 'Cannot find module|Module not found|ERR_MODULE_NOT_FOUND') {
      Write-Line 'A dashboard dependency is missing - reinstalling...' 'Yellow'
      $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
      Push-Location $DashboardDir
      try { & $npmCmd.Source install --no-audit --no-fund } finally { Pop-Location; $ErrorActionPreference = $prev }
      continue
    }
    if (Test-Path $NextCache) {
      Write-Line 'Clearing the build cache (.next) and retrying once...' 'Yellow'
      Remove-Item $NextCache -Recurse -Force -ErrorAction SilentlyContinue
      continue
    }
    break
  }

  if (-not $startedDashboard) {
    Write-Line 'The dashboard could not be started. Details in logs\dashboard.err.log.' 'Red'
    exit 1
  }
}

# ---------------------------------------------------------------------------
# Start the MT5 watch loop (best effort - the dashboard works without it)
# ---------------------------------------------------------------------------
$tracked = @()
if ($startedDashboard) { $tracked += [pscustomobject]@{ name = 'dashboard'; pid = $dashProc.Id } }

if (-not $NoWatcher) {
  if (-not (Test-Path $VenvPython)) {
    Write-Line 'Skipping the MT5 watcher: the Python venv is not available.' 'Yellow'
  } elseif (-not (Test-PythonImport 'MetaTrader5')) {
    Write-Line 'Skipping the MT5 watcher: the MetaTrader5 package is not installed.' 'Yellow'
  } else {
    Write-Line 'Starting the MT5 watch loop (feeds the Live tab)...' 'Cyan'
    $watchOut = Join-Path $LogDir 'watcher.log'
    $watchErr = Join-Path $LogDir 'watcher.err.log'
    Reset-RunLog $watchOut
    Reset-RunLog $watchErr
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try {
      # -u keeps stdout unbuffered so the log is useful while it runs
      $watchProc = Start-Process -FilePath $VenvPython -ArgumentList @('-u', '-m', 'mt5_intel.watch') `
        -WorkingDirectory $Root -PassThru -NoNewWindow `
        -RedirectStandardOutput $watchOut -RedirectStandardError $watchErr
      $tracked += [pscustomobject]@{ name = 'watcher'; pid = $watchProc.Id }
    } catch {
      Write-Line ("Could not start the watcher: {0}" -f $_.Exception.Message) 'Yellow'
    } finally { $ErrorActionPreference = $prev }

    if ($watchProc) {
      # Keep draining the dashboard while the watcher settles.
      for ($s = 0; $s -lt 12; $s++) { Drain-Dashboard -State $dash; Start-Sleep -Milliseconds 500 }
      if ($watchProc.HasExited) {
        $watchErrText = if (Test-Path $watchErr) { [string](Get-Content $watchErr -Raw) } else { '' }
        if ($watchErrText -match 'Config error|not installed') {
          Write-Line 'The watcher stopped: check MT5_LOGIN / MT5_SERVER in .env, and that the MT5 terminal is open.' 'Yellow'
        } else {
          Write-Line 'The watcher stopped early - see logs\watcher.err.log. The dashboard is unaffected.' 'Yellow'
        }
      } else {
        Write-Line 'Watch loop running.' 'Green'
      }
    }
  }
}

# ---------------------------------------------------------------------------
# One diagnostic pass so the Latest tab is fresh (fire and forget)
# ---------------------------------------------------------------------------
if (-not $NoDiagnostic -and (Test-Path $VenvPython) -and (Test-PythonImport 'MetaTrader5')) {
  Write-Line 'Running one diagnostic pass in the background (about a minute)...' 'Cyan'
  $diagOut = Join-Path $LogDir 'diagnostic.log'
  $diagErr = Join-Path $LogDir 'diagnostic.err.log'
  Reset-RunLog $diagOut
  Reset-RunLog $diagErr
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try {
    $diagProc = Start-Process -FilePath $VenvPython -ArgumentList @('-u', '-m', 'mt5_intel') `
      -WorkingDirectory $Root -PassThru -NoNewWindow `
      -RedirectStandardOutput $diagOut -RedirectStandardError $diagErr
    $tracked += [pscustomobject]@{ name = 'diagnostic'; pid = $diagProc.Id }
  } catch {
    Write-Line ("Could not start the diagnostic: {0}" -f $_.Exception.Message) 'Yellow'
  } finally { $ErrorActionPreference = $prev }
}

if ($tracked.Count -gt 0) { Save-Pids -Items $tracked }

# ---------------------------------------------------------------------------
# Open the browser
# ---------------------------------------------------------------------------
$url = "http://localhost:$chosen/"
if (-not $NoBrowser) {
  Write-Line ("Opening {0}" -f $url) 'Cyan'
  $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { Start-Process $url } catch { Write-Line 'Could not open the browser automatically; open the link above manually.' 'Yellow' }
  finally { $ErrorActionPreference = $prev }
}

# Warm the tabs after the landing page is open: the user reads it while the rest compile.
if ($startedDashboard) { Warm-Routes -BaseUrl $url -Dashboard $dash }

Write-Host ''
if ($startedDashboard) {
  Write-Line 'Ready.' 'Green'
  Write-Line ("  Dashboard : {0}" -f $url) 'White'
  Write-Line ("  Live      : {0}live" -f $url) 'White'
  Write-Line ("  Research  : {0}research" -f $url) 'White'
  Write-Line ("  Replays   : {0}replays" -f $url) 'White'
  Write-Line ("  Logs      : {0}" -f $LogDir) 'DarkGray'
  Write-Host ''
  Write-Line 'Press Ctrl+C or close this window to stop the console.' 'DarkGray'
  try {
    # Stay resident and keep the dashboard's logs capped for as long as it runs.
    while (-not $dashProc.HasExited) {
      Drain-Dashboard -State $dash
      Start-Sleep -Milliseconds 500
    }
    Drain-Dashboard -State $dash
  } catch {
    # Ctrl+C while waiting lands here; fall through to cleanup.
  } finally {
    Write-Host ''
    Write-Line 'Shutting down...' 'Cyan'
    foreach ($item in $tracked) { if ($item.name -ne 'dashboard') { Stop-Tree -ProcId ([int]$item.pid) } }
    Stop-Tree -ProcId ([int]$dashProc.Id)
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Write-Line 'Stopped.' 'Green'
  }
} else {
  Write-Line 'The dashboard was not started; see the messages above for the reason.' 'Yellow'
}

exit 0
