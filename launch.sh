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

# If we're running on a Raspberry Pi, make sure this venv environment is using Python 3.13 or later
if [[ $(uname -m) == "armv7l" || $(uname -m) == "aarch64" ]]; then
    # Check if the project has Python 3.13+ configured
    if ! $UVCmd python pin --resolved 2>/dev/null | grep -q "^3\.1[3-9]\|^3\.[2-9][0-9]\|^[4-9]"; then
        echo "Error: This project requires Python 3.13 or later to be configured on Raspberry Pi."
        echo "Run 'uv python pin 3.13' to pin Python 3.13 to this project."
        exit 1
    fi
fi


# Make sure we're up to date
$UVCmd sync 

# Run the script 
$UVCmd run $ScriptName 