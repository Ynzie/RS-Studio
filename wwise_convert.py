#!/usr/bin/env python3
"""
wwise_convert.py - convert a .wav into Rocksmith .wem files using a locally
installed Wwise 2013 and Custom Song Toolkit's shipped Wwise template.

Replicates RocksmithToolkitLib's Wwise.Wav2Wem flow:
  1. find WwiseCLI.exe under WWISEROOT / Audiokinetic
  2. extract CST's "Wwise2013.tar.bz2" template (lives in the CST install dir)
  3. drop Audio.wav + Audio_preview.wav into Template/Originals/SFX
  4. patch the work unit's quality factor placeholders
  5. run: WwiseCLI.exe Template.wproj -GenerateSoundBanks -Platform Windows
         -Language English(US) -NoWwiseDat -ClearAudioFileCache -Save
  6. copy the generated .wem(s) out of Template/.cache/Windows/SFX

Everything proprietary (WwiseCLI, the template) is read from the user's own
install - nothing is bundled or redistributed.
"""
import os
import shutil
import subprocess
import tarfile


def find_wwise_cli():
    """Locate WwiseCLI.exe. Prefers x64, falls back to Win32."""
    roots = []
    env = os.environ.get("WWISEROOT")
    if env:
        roots.append(env)
    roots += [r"C:\Program Files (x86)\Audiokinetic", r"C:\Program Files\Audiokinetic"]
    found = []
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            if "WwiseCLI.exe" in files:
                found.append(os.path.join(dirpath, "WwiseCLI.exe"))
    if not found:
        raise FileNotFoundError(
            "Could not find WwiseCLI.exe. Install Wwise 2013.2.x, or set the WWISEROOT "
            "environment variable to your Wwise folder.")
    x64 = [f for f in found if "Authoring\\x64" in f or "Authoring/x64" in f]
    return x64[0] if x64 else found[0]


def find_cst_template(cst_hint=None):
    """Find CST's Wwise2013.tar.bz2 (shipped in the CST install dir).
    cst_hint may be: the file itself, a folder, packer.exe's path, or None."""
    # direct hit: hint is the template file
    if cst_hint and os.path.isfile(cst_hint) and cst_hint.lower().endswith(".tar.bz2"):
        return cst_hint

    bases = []
    if cst_hint:
        bases.append(cst_hint if os.path.isdir(cst_hint) else os.path.dirname(cst_hint))
    here = os.path.dirname(os.path.abspath(__file__))
    home = os.path.expanduser("~")
    bases += [os.environ.get("CST_PATH", ""), here,
              r"C:\Program Files (x86)\Custom Song Toolkit",
              r"C:\Program Files\Custom Song Toolkit",
              os.path.join(home, "Downloads"),
              os.path.join(home, "Desktop"),
              home]
    seen = set()
    for base in bases:
        if not base or base in seen or not os.path.isdir(base):
            continue
        seen.add(base)
        for dirpath, _dirs, files in os.walk(base):
            for f in files:
                if f.lower() == "wwise2013.tar.bz2":
                    return os.path.join(dirpath, f)
    raise FileNotFoundError(
        "Could not find CST's 'Wwise2013.tar.bz2' template. It ships inside the Custom "
        "Song Toolkit folder (look in a 'Template' or root CST subfolder). Point the app "
        "at your CST folder with the CST box, or set the CST_PATH environment variable.")


def _make_preview_wav(wav_path, out_path, ffmpeg, start=30.0, dur=30.0, log=print):
    """Create a ~30s preview clip. Falls back to a copy if ffmpeg missing."""
    if not ffmpeg:
        shutil.copy2(wav_path, out_path)
        return
    r = subprocess.run([ffmpeg, "-y", "-ss", str(start), "-t", str(dur), "-i", wav_path,
                        "-ar", "48000", "-ac", "2", "-sample_fmt", "s16", out_path],
                       capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(out_path):
        log("  (preview clip fell back to full-length copy)")
        shutil.copy2(wav_path, out_path)


def _downgrade_wem(src_path, dest_path):
    """Rocksmith 2014 requires the wem 'version' field (uint32 at byte offset 40)
    to be 3. Wwise 2013 writes a higher value that the game rejects (audio engine
    stalls). Patch it, exactly like CST's OggFile.DowngradeWemVersion. PC = LE."""
    import struct
    with open(src_path, "rb") as f:
        data = bytearray(f.read())
    if len(data) >= 44:
        ver = struct.unpack_from("<I", data, 40)[0]
        if ver != 3:
            struct.pack_into("<I", data, 40, 3)
    with open(dest_path, "wb") as f:
        f.write(data)


def wav_to_wem(wav_path, dest_wem, work_root, cst_hint=None, ffmpeg=None,
               quality=4, log=print):
    """Convert wav_path -> dest_wem (and dest_*_preview.wem). Returns
    (wem_path, preview_wem_path)."""
    cli = find_wwise_cli()
    log(f"  Wwise: {cli}")
    template_pkg = find_cst_template(cst_hint)
    log(f"  Wwise template: {os.path.basename(template_pkg)}")

    template_dir = os.path.join(work_root, "Template")
    if os.path.isdir(template_dir):
        shutil.rmtree(template_dir, ignore_errors=True)
    with tarfile.open(template_pkg, "r:bz2") as tar:
        tar.extractall(work_root)
    if not os.path.isdir(template_dir):
        # archive may extract into a differently-named folder
        subs = [d for d in os.listdir(work_root)
                if os.path.isdir(os.path.join(work_root, d)) and d.lower().startswith("template")]
        if subs:
            template_dir = os.path.join(work_root, subs[0])

    # patch work unit quality placeholders (%QF1% main, %QF2% preview)
    wu = os.path.join(template_dir, "Interactive Music Hierarchy", "Default Work Unit.wwu")
    if os.path.isfile(wu):
        txt = open(wu, encoding="utf-8", errors="replace").read()
        txt = txt.replace("%QF1%", str(quality)).replace("%QF2%", "4")
        open(wu, "w", encoding="utf-8").write(txt)

    # fresh Originals/SFX and .cache
    sfx = os.path.join(template_dir, "Originals", "SFX")
    cache = os.path.join(template_dir, ".cache", "Windows", "SFX")
    gen = os.path.join(template_dir, "GeneratedSoundBanks")
    for d in (os.path.join(template_dir, "Originals"), os.path.join(template_dir, ".cache"), gen):
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
    os.makedirs(sfx, exist_ok=True)
    os.makedirs(cache, exist_ok=True)
    for vc in os.listdir(template_dir):
        if vc.startswith("Template.") and vc.endswith(".validationcache"):
            os.remove(os.path.join(template_dir, vc))

    # drop in audio + preview
    shutil.copy2(wav_path, os.path.join(sfx, "Audio.wav"))
    _make_preview_wav(wav_path, os.path.join(sfx, "Audio_preview.wav"), ffmpeg, log=log)

    wproj = os.path.join(template_dir, "Template.wproj")
    args = [cli, wproj, "-GenerateSoundBanks", "-Platform", "Windows",
            "-Language", "English(US)", "-NoWwiseDat", "-ClearAudioFileCache", "-Save"]
    log("  running WwiseCLI -GenerateSoundBanks ...")
    r = subprocess.run(args, capture_output=True, text=True, timeout=900)
    out = (r.stdout or "") + (r.stderr or "")
    if "Error: Project migration needed" in out:
        # WwiseCLI sometimes needs a second pass to migrate the 2013 project
        log("  (project migration - retrying once)")
        r = subprocess.run(args, capture_output=True, text=True, timeout=900)
        out = (r.stdout or "") + (r.stderr or "")

    wems = [f for f in os.listdir(cache) if f.lower().endswith(".wem")] if os.path.isdir(cache) else []
    if not wems:
        tail = "\n".join(out.strip().splitlines()[-15:])
        raise RuntimeError("WwiseCLI produced no .wem files.\n" + tail)

    preview_wem = dest_wem.rsplit(".", 1)[0] + "_preview.wem"
    # Wwise names cached wems by hash but preview clips contain "_preview_".
    # If that tag is missing (older Wwise), fall back to size: preview is shorter.
    prev_src = next((f for f in wems if "_preview_" in f.lower()), None)
    if prev_src is None and len(wems) >= 2:
        by_size = sorted(wems, key=lambda f: os.path.getsize(os.path.join(cache, f)))
        prev_src = by_size[0]          # smallest = preview clip
        main_src = by_size[-1]         # largest = full song
    else:
        main_src = next((f for f in wems if f != prev_src), wems[0])

    # CRITICAL: patch the wem version field to 3 or Rocksmith rejects the audio
    # (symptom: audio cuts out when the song is highlighted in-game).
    _downgrade_wem(os.path.join(cache, main_src), dest_wem)
    if prev_src:
        _downgrade_wem(os.path.join(cache, prev_src), preview_wem)
    else:
        _downgrade_wem(os.path.join(cache, main_src), preview_wem)
    log(f"  wem ready (version-patched): {os.path.basename(dest_wem)}")
    return dest_wem, preview_wem
