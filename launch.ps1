<#
=======================================================
Application Launcher

Requires Python and UV to be installed
=======================================================
#>

# Get the directory containing this script
$HomeDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ScriptName = "main.py"

# Find 'uv' in PATH
$UVCmd = Get-Command uv -ErrorAction SilentlyContinue

if (-not $UVCmd) {
    Write-Host "Error: 'uv' command not found in PATH. Please install UV or ensure it is in your PATH."
    exit 1
}

Set-Location $HomeDir

# Make sure we're up to date
& $UVCmd.Path sync

# Run the script
& $UVCmd.Path run $ScriptName