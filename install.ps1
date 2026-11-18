param(
    [ValidateSet('opencode','claude','codex')][string]$Target = 'opencode',
    [switch]$Replace
)
$ErrorActionPreference = 'Stop'
$installArgs = @((Join-Path $PSScriptRoot 'tools/pipeline.py'), 'install', '--target', $Target)
if ($Replace) { $installArgs += '--replace' }
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 @installArgs
} else {
    & python @installArgs
}
exit $LASTEXITCODE
