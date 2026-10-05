# Runs the SMG2 Online server in this window (Ctrl+C to stop).
# Use this on the PC that hosts; players then join with this PC's address.
#
# Settings (port, player limit, shared world, what is shared) live in
# server\server-settings.ini. The options below override that file for one run.
param(
    [int]$Port,
    [int]$MaxPlayers,
    # Name of the shared world to use (each has its own saved progress)
    [string]$World,
    # Start this world over (the old progress is kept as a .old file)
    [switch]$Fresh
)

$ErrorActionPreference = "Stop"
$base = $PSScriptRoot

$python = Get-Command py -ErrorAction SilentlyContinue
$pyArgs = @("-3")
if (-not $python) { $python = Get-Command python -ErrorAction SilentlyContinue; $pyArgs = @() }
if (-not $python) { throw "Python 3 is needed to run the server (https://www.python.org/downloads/)." }

$serverArgs = @("$base\server\smg2_server.py")
if ($PSBoundParameters.ContainsKey("Port")) { $serverArgs += @("--port", $Port) }
if ($PSBoundParameters.ContainsKey("MaxPlayers")) { $serverArgs += @("--max-players", $MaxPlayers) }
if ($World) { $serverArgs += @("--world", $World) }
if ($Fresh) { $serverArgs += "--fresh" }
& $python.Source @pyArgs @serverArgs
