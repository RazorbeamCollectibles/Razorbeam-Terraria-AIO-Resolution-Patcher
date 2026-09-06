param([switch]$OneFile)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$engineRoot = Join-Path $projectRoot 'app\engine'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath (Join-Path $engineRoot 'Mono.Cecil.dll'))) {
    throw 'Mono.Cecil.dll missing. Restore the pinned Mono.Cecil 0.11.6 net40 library.'
}
& $compiler /nologo /optimize+ /r:System.Web.Extensions.dll "/r:$engineRoot\Mono.Cecil.dll" "/out:$engineRoot\PatchEngine.exe" "$engineRoot\PatchEngine.cs"
if ($LASTEXITCODE -ne 0) { throw 'Patch engine compilation failed.' }
$distName = if ($OneFile) { 'standalone' } else { 'portable' }
$env:PYINSTALLER_CONFIG_DIR = Join-Path $projectRoot '.build\pyinstaller-cache'
$env:RAZORBEAM_BUILD_ONEFILE = if ($OneFile) { '1' } else { '0' }
$buildPython = (Get-Command python).Source
$pythonFolder = Split-Path -Parent $buildPython
$originalSearchPath = $env:PATH
# Unrelated native runtimes on PATH can provide incompatible ICU / CRT DLLs.
$env:PATH = "$pythonFolder;$env:WINDIR\System32;$env:WINDIR;$pythonFolder\Lib\site-packages\PySide6"
Push-Location $projectRoot
try {
    & $buildPython -m PyInstaller --noconfirm --clean --distpath ".build\$distName" --workpath ".build\work-$distName" scripts\RazorbeamTerrariaPatcher.spec
    if ($LASTEXITCODE -ne 0) { throw 'Application packaging failed.' }
} finally { Pop-Location; $env:PATH = $originalSearchPath }
