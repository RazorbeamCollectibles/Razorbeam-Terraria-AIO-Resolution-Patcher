import os
from pathlib import Path

root = Path(SPECPATH).parent
onefile = os.environ.get('RAZORBEAM_BUILD_ONEFILE') == '1'
icon_file = root / 'app/assets/aiorp_icon.ico'
a = Analysis(
    [str(root / 'app' / 'app.py')],
    pathex=[str(root / 'app')],
    binaries=[],
    datas=[(str(root / 'app/engine/PatchEngine.exe'), 'engine'),
           (str(root / 'app/engine/Mono.Cecil.dll'), 'engine'),
           (str(root / 'app/assets'), 'assets'),
           (str(root / 'app/tmod/RazorbeamDisplay.tmod'), 'tmod'),
           (str(root / 'THIRD_PARTY_NOTICES.md'), '.'),
           (str(root / 'licenses'), 'licenses')],
    hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[],
    noarchive=False, optimize=0,
)

# This is a Widgets application. Avoid pulling in Qt Quick / Virtual Keyboard /
# PDF through optional plugins, and avoid bundling OS-provided ICU/API-set DLLs.
def keep_binary(entry):
    name = entry[0].replace('\\', '/').lower()
    base = name.rsplit('/', 1)[-1]
    if base.startswith(('qt6qml', 'qt6quick', 'qt6virtualkeyboard', 'qt6pdf')):
        return False
    if '/platforminputcontexts/' in name or '/tls/' in name or base == 'qpdf.dll':
        return False
    if base == 'icuuc.dll' or base.startswith('icudt'):
        return False
    return True

a.binaries = [entry for entry in a.binaries if keep_binary(entry)]
pyz = PYZ(a.pure)
if onefile:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name='RazorbeamTerrariaPatcher',
              debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
              console=False, disable_windowed_traceback=False, icon=str(icon_file))
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='RazorbeamTerrariaPatcher',
              debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
              console=False, disable_windowed_traceback=False, contents_directory='_internal',
              icon=str(icon_file))
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='RazorbeamTerrariaPatcher')
