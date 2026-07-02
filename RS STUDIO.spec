# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = [('C:\\Users\\ynzer\\Downloads\\RS Studio\\gp2rs.py', '.'), ('C:\\Users\\ynzer\\Downloads\\RS Studio\\gp2rs_studio.py', '.'), ('C:\\Users\\ynzer\\Downloads\\RS Studio\\wwise_convert.py', '.'), ('C:\\Users\\ynzer\\Downloads\\RS Studio\\tone_designer.py', '.'), ('C:\\Users\\ynzer\\Downloads\\RS Studio\\gp_convert.py', '.'), ('C:\\Users\\ynzer\\Downloads\\RS Studio\\lyric_sync.py', '.'), ('C:\\Users\\ynzer\\Downloads\\RS Studio\\lyrics_align.py', '.'), ('C:\\Users\\ynzer\\Downloads\\RS Studio\\lyric_font.py', '.'), ('C:\\Users\\ynzer\\Downloads\\RS Studio\\cst_template.py', '.'), ('C:\\Users\\ynzer\\Downloads\\RS Studio\\pdf2gp.py', '.'), ('C:\\Users\\ynzer\\Downloads\\RS Studio\\staffdet.py', '.'), ('C:\\Users\\ynzer\\Downloads\\RS Studio\\tabrec.py', '.'), ('C:\\Users\\ynzer\\Downloads\\RS Studio\\digit_templates.npz', '.'), ('C:\\Users\\ynzer\\Downloads\\RS Studio\\sig_templates.npz', '.'), ('C:\\Users\\ynzer\\Downloads\\RS Studio\\wwise_templates', 'wwise_templates'), ('C:\\Users\\ynzer\\Downloads\\RS Studio\\rs_studio.ico', '.'), ('C:\\Users\\ynzer\\Downloads\\RS Studio\\rs_studio.png', '.')]
binaries = []
hiddenimports = ['gp2rs', 'PIL', 'PIL.Image', 'PIL.ImageTk', 'guitarpro', 'wwise_convert', 'tone_designer', 'gp_convert', 'lyric_sync', 'lyrics_align', 'lyric_font', 'cst_template', 'pdf2gp', 'staffdet', 'tabrec', 'pikepdf', 'pdfplumber']
hiddenimports += collect_submodules('PIL')
hiddenimports += collect_submodules('guitarpro')
hiddenimports += collect_submodules('pikepdf')
hiddenimports += collect_submodules('pdfplumber')
hiddenimports += collect_submodules('pdfminer')
tmp_ret = collect_all('cv2')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['C:\\Users\\ynzer\\Downloads\\RS Studio\\gp2rs_studio.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['nltk', 'sklearn', 'scipy', 'torch', 'torchaudio', 'torchvision', 'tensorflow', 'tensorflow_hub', 'whisperx', 'demucs', 'stable_whisper', 'faster_whisper', 'basic_pitch', 'transformers', 'pandas', 'matplotlib', 'numba', 'llvmlite', 'onnxruntime', 'playwright', 'sympy', 'tensorboard', 'scikit_learn', 'joblib', 'threadpoolctl'],
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
    icon=['C:\\Users\\ynzer\\Downloads\\RS Studio\\rs_studio.ico'],
)
