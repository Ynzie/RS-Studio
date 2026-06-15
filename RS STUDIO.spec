# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['gp2rs', 'PIL', 'PIL.Image', 'PIL.ImageTk', 'cst_template', 'wwise_convert']
hiddenimports += collect_submodules('PIL')


a = Analysis(
    ['C:\\Users\\ynzer\\Downloads\\python thing\\gp2rs_studio.py'],
    pathex=[],
    binaries=[('C:\\Users\\ynzer\\Downloads\\python thing\\ffmpeg.exe', '.')],
    datas=[('C:\\Users\\ynzer\\Downloads\\python thing\\gp2rs.py', '.'), ('C:\\Users\\ynzer\\Downloads\\python thing\\gp2rs_studio.py', '.'), ('C:\\Users\\ynzer\\Downloads\\python thing\\cst_template.py', '.'), ('C:\\Users\\ynzer\\Downloads\\python thing\\wwise_convert.py', '.'), ('C:\\Users\\ynzer\\Downloads\\python thing\\rs_studio.ico', '.'), ('C:\\Users\\ynzer\\Downloads\\python thing\\rs_studio.png', '.')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='RS STUDIO',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['C:\\Users\\ynzer\\Downloads\\python thing\\rs_studio.ico'],
)
