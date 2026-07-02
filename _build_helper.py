#!/usr/bin/env python3
"""
_build_helper.py - Builds RS STUDIO.exe with PyInstaller.

Called by build_exe.bat. Kept as a real, permanent file (not generated on the
fly) because the previous approach - generating this script into a temp file
via a batch `echo` block - turned out to be fragile: caret-escaping bugs and
a temp file that occasionally failed to (re)write cleanly both caused stale
or missing builds that were very hard to diagnose. A plain .py file sitting
here removes both failure modes entirely.
"""
import os
import sys

import PyInstaller.__main__

# Folder this script lives in - always the RS Studio project root, regardless
# of where python.exe or the current working directory happen to be.
here = os.path.dirname(os.path.abspath(__file__)) + os.sep

args = [
    '--onefile',
    '--windowed',
    '--noconfirm',
    '--clean',
    '--name=RS STUDIO',
    '--add-data=' + here + 'gp2rs.py' + os.pathsep + '.',
    '--add-data=' + here + 'gp2rs_studio.py' + os.pathsep + '.',
    '--hidden-import=gp2rs',
    '--hidden-import=PIL',
    '--hidden-import=PIL.Image',
    '--hidden-import=PIL.ImageTk',
    '--collect-submodules=PIL',
    '--hidden-import=guitarpro',
    '--collect-submodules=guitarpro',
]

# Heavy ML / data libraries must NOT be bundled. RS Studio runs them via an
# external Python. Bundling them bloats the exe and pulls broken data files
# such as sklearn estimator.css which crashes startup via the nltk hook.
args += ['--exclude-module=' + m for m in [
    'nltk', 'sklearn', 'scipy', 'torch', 'torchaudio', 'torchvision',
    'tensorflow', 'tensorflow_hub', 'whisperx', 'demucs', 'stable_whisper',
    'faster_whisper', 'basic_pitch', 'transformers', 'pandas', 'matplotlib',
    'numba', 'llvmlite', 'onnxruntime', 'playwright', 'sympy', 'tensorboard',
    'scikit_learn', 'joblib', 'threadpoolctl',
]]

# Optional modules - include if present
for mod_file, mod_name in [
    ('wwise_convert.py', 'wwise_convert'),
    ('tone_designer.py', 'tone_designer'),
    ('gp_convert.py', 'gp_convert'),
    ('lyric_sync.py', 'lyric_sync'),
    ('lyrics_align.py', 'lyrics_align'),
    ('lyric_font.py', 'lyric_font'),
    ('cst_template.py', 'cst_template'),
    ('pdf2gp.py', 'pdf2gp'),
    ('staffdet.py', 'staffdet'),
    ('tabrec.py', 'tabrec'),
]:
    path = here + mod_file
    if os.path.exists(path):
        args += ['--add-data=' + path + os.pathsep + '.', '--hidden-import=' + mod_name]

# PDF-to-GP support - bundle its template data files and the extra heavy
# libs it needs, only if pdf2gp.py is actually present.
print('DEBUG here =', repr(here))
print('DEBUG pdf2gp.py exists:', os.path.exists(here + 'pdf2gp.py'), here + 'pdf2gp.py')
if os.path.exists(here + 'pdf2gp.py'):
    args += ['--collect-all=cv2', '--hidden-import=pikepdf', '--collect-submodules=pikepdf',
             '--hidden-import=pdfplumber', '--collect-submodules=pdfplumber',
             '--collect-submodules=pdfminer']
    for npz in ('digit_templates.npz', 'sig_templates.npz'):
        npz_path = here + npz
        print('DEBUG', npz_path, 'exists:', os.path.exists(npz_path))
        if os.path.exists(npz_path):
            args += ['--add-data=' + npz_path + os.pathsep + '.']
            print('DEBUG added --add-data for', npz)

# Wwise project templates (DLC Builder's own - for wav->wem without CST)
_wt = here + 'wwise_templates'
if os.path.isdir(_wt):
    args += ['--add-data=' + _wt + os.pathsep + 'wwise_templates']

# Optional bundled binaries
for fname in ['rs_studio.ico', 'rs_studio.png', 'ffmpeg.exe', 'ffplay.exe', 'yt-dlp.exe']:
    fpath = here + fname
    if os.path.exists(fpath):
        if fname.endswith('.ico'):
            args += ['--icon=' + fpath, '--add-data=' + fpath + os.pathsep + '.']
        elif fname.endswith('.png'):
            args += ['--add-data=' + fpath + os.pathsep + '.']
        else:
            args += ['--add-binary=' + fpath + os.pathsep + '.']

args += [
    '--distpath=' + here + 'dist',
    '--workpath=' + here + 'build',
    '--specpath=' + here,
    here + 'gp2rs_studio.py',
]

print('PyInstaller args:')
for a in args:
    print(' ', a)

PyInstaller.__main__.run(args)
