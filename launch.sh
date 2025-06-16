#!/bin/bash
: '=======================================================
Application Launcher

Requires Python and UV to be installed
=========================================================='

#User parameters
HomeDir=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
ScriptName=main.py

# PytonCmd=/usr/local/bin/python3
UVCmd=`which uv`
# Check if UV is in the path
if [ -z "$UVCmd" ]; then
    echo "Error: 'uv' command not found in PATH. Please install UV or ensure it is in your PATH."
    exit 1
fi

cd $HomeDir

# Make sure we're up to date
$UVCmd sync 

# Run the script 
$UVCmd run $ScriptName 