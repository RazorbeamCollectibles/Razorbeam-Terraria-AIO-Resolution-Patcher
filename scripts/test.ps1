param([string]$TerrariaExe = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $projectRoot 'app'
$env:RAZORBEAM_TEST_STATE = Join-Path $projectRoot '.build\test-state'
$env:RAZORBEAM_TEST_SCHEMES = Join-Path $projectRoot '.build\test-schemes'
$env:RAZORBEAM_TERRARIA_CONFIG = Join-Path $projectRoot '.build\test-config.json'
if ($TerrariaExe) { $env:TERRARIA_TEST_EXE = $TerrariaExe }
python -m unittest discover -s (Join-Path $projectRoot 'tests') -v
exit $LASTEXITCODE
