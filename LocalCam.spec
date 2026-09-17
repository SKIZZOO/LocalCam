# PyInstaller build specification for LocalCam.
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPEC).resolve().parent
hiddenimports = collect_submodules('core')

datas = [(str(ROOT / 'web'), 'web')]

analysis = Analysis(
    ['app.py'],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, analysis.binaries, analysis.datas, [], name='LocalCam', console=True)
coll = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name='LocalCam')

analysis_service = Analysis(
    ['service.py'], pathex=[str(ROOT)], binaries=[], datas=datas,
    hiddenimports=hiddenimports + ['win32serviceutil', 'win32service', 'win32event', 'servicemanager'],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False,
)
pyz_service = PYZ(analysis_service.pure)
exe_service = EXE(pyz_service, analysis_service.scripts, analysis_service.binaries, analysis_service.datas, [], name='LocalCamService', console=True)
coll_service = COLLECT(exe_service, analysis_service.binaries, analysis_service.datas, strip=False, upx=False, name='LocalCamService')
