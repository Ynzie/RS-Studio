#!/usr/bin/env python3
"""
wwise_convert.py - convert a .wav into a Rocksmith .wem using the user's modern
Wwise (2019/2021/2022/2023) installation, the same way DLC Builder does it.

This replaces the old CST 'Wwise2013.tar.bz2' path. It uses:
  * WwiseConsole.exe  (modern Wwise CLI)
  * DLC Builder's own Wwise project templates (wwise_templates/wwise20XX.zip),
    which RS Studio ships alongside the app.
No Custom Song Toolkit / Wwise2013 template required.

Flow (mirrors Rocksmith2014.Audio.Wwise.convertToWem):
  1. find WwiseConsole.exe under Audiokinetic\\Wwise 20XX\\Authoring\\x64\\Release\\bin
  2. pick the matching template zip by Wwise major version
  3. extract template, drop the wav into Originals/SFX/Audio.wav
  4. WwiseConsole generate-soundbank "Template.wproj" --platform Windows
        --language "English(US)" --no-decode --quiet
  5. copy the generated .wem out of .cache/Windows/SFX and patch its version
     field to 3 (Rocksmith requirement)
"""
import os
import re
import sys
import glob
import shutil
import struct
import zipfile
import tempfile
import subprocess

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _wwise_roots():
    roots = []
    env = os.environ.get("WWISEROOT")
    if env:
        roots.append(env)
    for pf in (os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
               os.environ.get("ProgramFiles", r"C:\Program Files")):
        ak = os.path.join(pf, "Audiokinetic")
        if os.path.isdir(ak):
            for d in os.listdir(ak):
                if re.search(r"20(19|21|22|23)", d):
                    roots.append(os.path.join(ak, d))
    return roots


def find_wwise_console():
    """Locate WwiseConsole.exe (modern Wwise). Returns (path, major_version_int)."""
    cands = []
    for root in _wwise_roots():
        p = os.path.join(root, "Authoring", "x64", "Release", "bin", "WwiseConsole.exe")
        if os.path.isfile(p):
            cands.append(p)
        # walk as a fallback
        if os.path.isdir(root):
            for dp, _d, files in os.walk(root):
                if "WwiseConsole.exe" in files:
                    cands.append(os.path.join(dp, "WwiseConsole.exe"))
    cands = list(dict.fromkeys(cands))
    if not cands:
        raise FileNotFoundError(
            "Could not find WwiseConsole.exe. Install Wwise 2019/2021/2022/2023 "
            "(or set WWISEROOT). Your WwiseCLI is the old 2013-style tool; the "
            "modern WwiseConsole.exe lives in the same Authoring\\x64\\Release\\bin folder.")
    path = cands[0]
    m = re.search(r"20(19|21|22|23)", path)
    return (path, int("20" + m.group(1))) if m else (path, 2021)


def _template_zip(major):
    """Find the bundled wwise20XX.zip template for this Wwise major version."""
    name = f"wwise{major}.zip"
    bases = []
    try:
        bases.append(os.path.dirname(os.path.abspath(__file__)))
    except Exception:
        pass
    try:
        bases.append(os.path.dirname(os.path.abspath(sys.argv[0])))
    except Exception:
        pass
    if getattr(sys, "frozen", False):
        bases.append(getattr(sys, "_MEIPASS", ""))
        bases.append(os.path.dirname(sys.executable))
        bases.append(os.path.dirname(os.path.dirname(sys.executable)))
    bases.append(os.getcwd())
    for b in bases:
        if not b:
            continue
        for cand in (os.path.join(b, "wwise_templates", name),
                     os.path.join(b, name)):
            if os.path.isfile(cand):
                return cand
    # fall back to any available template (closest)
    for b in bases:
        d = os.path.join(b, "wwise_templates")
        if os.path.isdir(d):
            zips = sorted(glob.glob(os.path.join(d, "wwise20*.zip")))
            if zips:
                return zips[-1]
    raise FileNotFoundError(
        f"Could not find a Wwise template ({name}). Expected in a 'wwise_templates' "
        "folder next to the app.")


def _fix_header(path):
    """Patch the wem 'version' field (uint32 @ offset 40) to 3 - Rocksmith 2014
    rejects higher values (audio stalls in-game)."""
    with open(path, "r+b") as f:
        f.seek(40)
        f.write(struct.pack("<I", 3))


def _make_preview_wav(wav_path, out_path, ffmpeg, start=30.0, dur=30.0, log=print):
    if not ffmpeg:
        shutil.copy2(wav_path, out_path); return
    r = subprocess.run([ffmpeg, "-y", "-ss", str(start), "-t", str(dur), "-i", wav_path,
                        "-ar", "48000", "-ac", "2", "-sample_fmt", "s16", out_path],
                       capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    if r.returncode != 0 or not os.path.exists(out_path):
        log("  (preview clip fell back to full-length copy)")
        shutil.copy2(wav_path, out_path)


def _convert_one(console, template_zip, src_wav, dest_wem, log=print):
    """Convert one wav -> wem via WwiseConsole + the template. Returns dest_wem."""
    tmp = tempfile.mkdtemp(prefix="rswwise_")
    try:
        with zipfile.ZipFile(template_zip) as z:
            z.extractall(tmp)
        sfx = os.path.join(tmp, "Originals", "SFX")
        os.makedirs(sfx, exist_ok=True)
        shutil.copy2(src_wav, os.path.join(sfx, "Audio.wav"))
        wproj = os.path.join(tmp, "Template.wproj")
        args = [console, "generate-soundbank", wproj, "--platform", "Windows",
                "--language", "English(US)", "--no-decode", "--quiet"]
        r = subprocess.run(args, capture_output=True, text=True, timeout=900,
                           creationflags=CREATE_NO_WINDOW)
        cache = os.path.join(tmp, ".cache", "Windows", "SFX")
        wems = glob.glob(os.path.join(cache, "*.wem")) if os.path.isdir(cache) else []
        if not wems:
            tail = "\n".join(((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-15:])
            raise RuntimeError(f"WwiseConsole produced no .wem (exit {r.returncode}).\n{tail}")
        shutil.copy2(wems[0], dest_wem)
        _fix_header(dest_wem)
        return dest_wem
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def wav_to_wem(wav_path, dest_wem, work_root, cst_hint=None, ffmpeg=None,
               quality=4, log=print):
    """Convert wav_path -> dest_wem (+ a *_preview.wem). Returns
    (wem_path, preview_wem_path). cst_hint is ignored (kept for call-compat)."""
    console, major = find_wwise_console()
    log(f"  Wwise: {console} (v{major})")
    template_zip = _template_zip(major)
    log(f"  Wwise template: {os.path.basename(template_zip)}")

    # main song
    _convert_one(console, template_zip, wav_path, dest_wem, log=log)
    log(f"  wem ready: {os.path.basename(dest_wem)}")

    # preview clip
    preview_wem = dest_wem.rsplit(".", 1)[0] + "_preview.wem"
    prev_wav = os.path.join(work_root, "_preview_src.wav")
    _make_preview_wav(wav_path, prev_wav, ffmpeg, log=log)
    try:
        _convert_one(console, template_zip, prev_wav, preview_wem, log=log)
        log(f"  preview wem ready: {os.path.basename(preview_wem)}")
    except Exception as e:
        log(f"  (preview wem failed: {e}; reusing main wem)")
        shutil.copy2(dest_wem, preview_wem)
    finally:
        try: os.remove(prev_wav)
        except Exception: pass
    return dest_wem, preview_wem
