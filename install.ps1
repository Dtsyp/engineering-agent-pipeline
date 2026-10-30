# Windows + WSL2: invoke the Unix installer inside Ubuntu.
param([ValidateSet('opencode','claude','codex')][string]$Target = 'opencode')
$ErrorActionPreference = 'Stop'
$RootWin = (Resolve-Path $PSScriptRoot).Path
$RootLinux = (& wsl.exe -e wslpath -u $RootWin).Trim()
if ($LASTEXITCODE -ne 0 -or -not $RootLinux) { throw 'Cannot resolve repository path inside WSL' }
& wsl.exe -e python3 "$RootLinux/tools/pipeline.py" install --target $Target
if ($LASTEXITCODE -ne 0) { throw 'WSL installation failed' }
