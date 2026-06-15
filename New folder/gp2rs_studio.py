#!/usr/bin/env python3
"""
gp2rs_studio.py - GP2Rocksmith Studio

A dark, one-window GUI that turns a Guitar Pro 7/8 (.gp) file into a
ready-to-build Rocksmith DLC Builder project: arrangement XMLs
(Lead/Rhythm/Bass), vocals from a lyrics file, audio + album art copied in,
a tone preset chosen per track, and a project.rs2dlc that opens directly in
DLC Builder.

Run:  python gp2rs_studio.py     (or build an exe with build_exe.bat)
Requires gp2rs.py in the same folder. No third-party libraries.
"""
import json
import os
import random
import re
import shutil
import sys
import threading
import traceback
import urllib.request
import uuid
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gp2rs  # noqa: E402
import subprocess  # noqa: E402
try:
    import cst_template
except Exception:
    cst_template = None
try:
    import wwise_convert
except Exception:
    wwise_convert = None


def _find_packer():
    """Locate CST's packer.exe via env var, common install dirs, or PATH."""
    env = os.environ.get("CST_PATH", "")
    for base in filter(None, [env,
                              r"C:\Program Files (x86)\Custom Song Toolkit",
                              r"C:\Program Files\Custom Song Toolkit",
                              os.path.expanduser("~")]):
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            if "packer.exe" in files:
                return os.path.join(root, "packer.exe")
    return None


def preset_to_tone2014(preset, key):
    gl = {}
    for slot, ped in preset["GearList"].items():
        gl[slot] = {"Type": ped["Type"], "KnobValues": ped.get("KnobValues", {}),
                    "Key": ped["Key"], "Category": ped.get("Category"),
                    "Skin": ped.get("Skin"), "SkinIndex": ped.get("SkinIndex")}
    return {"GearList": gl, "IsCustom": True, "Key": key, "Name": key,
            "NameSeparator": " - ", "SortOrder": 0,
            "ToneDescriptors": preset.get("ToneDescriptors", []),
            "Volume": preset.get("Volume", "-18")}

# Tone presets built from Rocksmith DLC Builder's gear database (MIT licensed,
# github.com/iminashi/Rocksmith2014.NET).
TONE_PRESETS = json.loads(r'''{"Clean - Twin": {"GearList": {"Amp": {"Type": "Amps", "KnobValues": {"Amp_TW40_Gain": 18, "Amp_TW40_Bass": 80, "Amp_TW40_Mid": 80, "Amp_TW40_Treble": 80, "Amp_TW40_Pres": 69}, "Key": "Amp_TW40"}, "Cabinet": {"Type": "Cabinets", "KnobValues": {}, "Key": "Cab_TW410C_57_Cone"}}, "ToneDescriptors": ["$[35720]CLEAN"], "NameSeparator": " - ", "IsCustom": true, "Volume": "-18.0", "Key": "clean_twin", "Name": "clean_twin"}, "Clean - Solid State": {"GearList": {"Amp": {"Type": "Amps", "KnobValues": {"Amp_CS90_Gain": 2, "Amp_CS90_Bass": 60, "Amp_CS90_Mid": 60, "Amp_CS90_Treble": 70}, "Key": "Amp_CS90"}, "Cabinet": {"Type": "Cabinets", "KnobValues": {}, "Key": "Cab_CA112C_57_Cone"}}, "ToneDescriptors": ["$[35720]CLEAN"], "NameSeparator": " - ", "IsCustom": true, "Volume": "-18.0", "Key": "clean_ss", "Name": "clean_ss"}, "Clean - Jazz": {"GearList": {"Amp": {"Type": "Amps", "KnobValues": {"Amp_GibsonGA79_Gain": 25, "Amp_GibsonGA79_Bass": 75, "Amp_GibsonGA79_Treble": 66}, "Key": "Amp_GibsonGA79"}, "Cabinet": {"Type": "Cabinets", "KnobValues": {}, "Key": "Cab_CA215C_57_Cone"}}, "ToneDescriptors": ["$[35720]CLEAN"], "NameSeparator": " - ", "IsCustom": true, "Volume": "-18.0", "Key": "clean_jazz", "Name": "clean_jazz"}, "Crunch - JTM45": {"GearList": {"Amp": {"Type": "Amps", "KnobValues": {"Amp_MarshallJTM45_Gain": 55, "Amp_MarshallJTM45_Bass": 75, "Amp_MarshallJTM45_Mid": 66, "Amp_MarshallJTM45_Treble": 66, "Amp_MarshallJTM45_Pres": 30}, "Key": "Amp_MarshallJTM45"}, "Cabinet": {"Type": "Cabinets", "KnobValues": {}, "Key": "Cab_Marshall1960TV_57_Cone"}}, "ToneDescriptors": ["$[35716]CRUNCH"], "NameSeparator": " - ", "IsCustom": true, "Volume": "-18.0", "Key": "crunch_jtm", "Name": "crunch_jtm"}, "Crunch - Orange": {"GearList": {"Amp": {"Type": "Amps", "KnobValues": {"Amp_OrangeAD50_Gain": 60, "Amp_OrangeAD50_Bass": 75, "Amp_OrangeAD50_Treble": 66, "Amp_OrangeAD50_Mid": 66}, "Key": "Amp_OrangeAD50"}, "Cabinet": {"Type": "Cabinets", "KnobValues": {}, "Key": "Cab_OrangePPC212OB_57_Cone"}}, "ToneDescriptors": ["$[35716]CRUNCH"], "NameSeparator": " - ", "IsCustom": true, "Volume": "-18.0", "Key": "crunch_orange", "Name": "crunch_orange"}, "Distortion - JCM800": {"GearList": {"Amp": {"Type": "Amps", "KnobValues": {"Amp_MarshallJCM800_Gain": 80, "Amp_MarshallJCM800_Bass": 75, "Amp_MarshallJCM800_Mid": 66, "Amp_MarshallJCM800_Treble": 66, "Amp_MarshallJCM800_Pres": 30}, "Key": "Amp_MarshallJCM800"}, "Cabinet": {"Type": "Cabinets", "KnobValues": {}, "Key": "Cab_Marshall1960a_57_Cone"}}, "ToneDescriptors": ["$[35722]DISTORTION", "$[35723]LEAD"], "NameSeparator": " - ", "IsCustom": true, "Volume": "-18.0", "Key": "dist_jcm", "Name": "dist_jcm"}, "Distortion - Rockerverb": {"GearList": {"Amp": {"Type": "Amps", "KnobValues": {"Amp_OrangeRockerverb_Gain": 85, "Amp_OrangeRockerverb_Bass": 75, "Amp_OrangeRockerverb_Mid": 66, "Amp_OrangeRockerverb_Treble": 66}, "Key": "Amp_OrangeRockerverb"}, "Cabinet": {"Type": "Cabinets", "KnobValues": {}, "Key": "Cab_OrangePPC412_57_Cone"}}, "ToneDescriptors": ["$[35722]DISTORTION"], "NameSeparator": " - ", "IsCustom": true, "Volume": "-18.0", "Key": "dist_rocker", "Name": "dist_rocker"}, "High Gain - Modern": {"GearList": {"Amp": {"Type": "Amps", "KnobValues": {"Amp_HG500_Gain": 95, "Amp_HG500_Bass": 65, "Amp_HG500_Mid": 44, "Amp_HG500_MidFreq": 38, "Amp_HG500_Treble": 86}, "Key": "Amp_HG500"}, "Cabinet": {"Type": "Cabinets", "KnobValues": {}, "Key": "Cab_HG215C_57_Cone"}}, "ToneDescriptors": ["$[35721]HIGH GAIN", "$[35722]DISTORTION"], "NameSeparator": " - ", "IsCustom": true, "Volume": "-18.0", "Key": "higain", "Name": "higain"}, "Lead - Boosted": {"GearList": {"Amp": {"Type": "Amps", "KnobValues": {"Amp_CA100_Gain": 90, "Amp_CA100_Bass": 55, "Amp_CA100_Mid": 85, "Amp_CA100_Treble": 100, "Amp_CA100_Pres": 30}, "Key": "Amp_CA100"}, "Cabinet": {"Type": "Cabinets", "KnobValues": {}, "Key": "Cab_CA412C_57_Cone"}}, "ToneDescriptors": ["$[35723]LEAD", "$[35722]DISTORTION"], "NameSeparator": " - ", "IsCustom": true, "Volume": "-18.0", "Key": "lead_boost", "Name": "lead_boost"}, "Bass - Clean DI": {"GearList": {"Amp": {"Type": "Amps", "KnobValues": {"Bass_Amp_EdenWT550_Gain": 12, "Bass_Amp_EdenWT550_Bass": 4, "Bass_Amp_EdenWT550_Lo": -9, "Bass_Amp_EdenWT550_LoFreq": 220, "Bass_Amp_EdenWT550_Mid": 4, "Bass_Amp_EdenWT550_MidFreq": 1200, "Bass_Amp_EdenWT550_Hi": -4, "Bass_Amp_EdenWT550_HiFreq": 1.6, "Bass_Amp_EdenWT550_Treble": 6, "Bass_Amp_EdenWT550_Enhance": 35}, "Key": "Bass_Amp_EdenWT550"}, "Cabinet": {"Type": "Cabinets", "KnobValues": {}, "Key": "Bass_Cab_CA1510BC_57_Cone"}}, "ToneDescriptors": ["$[35715]BASS"], "NameSeparator": " - ", "IsCustom": true, "Volume": "-20.0", "Key": "bass_clean", "Name": "bass_clean"}, "Bass - Rock": {"GearList": {"Amp": {"Type": "Amps", "KnobValues": {"Bass_Amp_CH350B_Gain": 45, "Bass_Amp_CH350B_Bass": 6, "Bass_Amp_CH350B_Treble": 88, "Bass_Amp_CH350B_30": 6, "Bass_Amp_CH350B_90": -4, "Bass_Amp_CH350B_250": -6, "Bass_Amp_CH350B_800": 3, "Bass_Amp_CH350B_2500": -5, "Bass_Amp_CH350B_7500": 4, "Bass_Amp_CH350B_15000": 6}, "Key": "Bass_Amp_CH350B"}, "Cabinet": {"Type": "Cabinets", "KnobValues": {}, "Key": "Bass_Cab_TW215BC_57_Cone"}}, "ToneDescriptors": ["$[35715]BASS"], "NameSeparator": " - ", "IsCustom": true, "Volume": "-20.0", "Key": "bass_rock", "Name": "bass_rock"}, "Bass - Fuzz": {"GearList": {"Amp": {"Type": "Amps", "KnobValues": {"Bass_Amp_OrangeAD200B_Gain": 88, "Bass_Amp_OrangeAD200B_Bass": 75, "Bass_Amp_OrangeAD200B_Mid": 66, "Bass_Amp_OrangeAD200B_Treble": 50}, "Key": "Bass_Amp_OrangeAD200B"}, "Cabinet": {"Type": "Cabinets", "KnobValues": {}, "Key": "Bass_Cab_TW215BC_57_Cone"}}, "ToneDescriptors": ["$[35756]FUZZ", "$[35715]BASS"], "NameSeparator": " - ", "IsCustom": true, "Volume": "-20.0", "Key": "bass_fuzz", "Name": "bass_fuzz"}}''')
PRESET_LABELS = list(TONE_PRESETS)
GUITAR_PRESETS = [p for p in PRESET_LABELS if not p.startswith("Bass")]
BASS_PRESETS = [p for p in PRESET_LABELS if p.startswith("Bass")]

ARR_ENUM = {"Lead": (0, 1), "Rhythm": (2, 2), "Bass": (3, 4)}  # Name, RouteMask


def auto_preset(name):
    n = name.lower()
    if "bass" in n:
        return "Bass - Rock"
    if "clean" in n:
        return "Clean - Twin"
    if "acoustic" in n or "nylon" in n:
        return "Clean - Jazz"
    if "overdrive" in n or "crunch" in n:
        return "Crunch - Orange"
    if "distortion" in n or "lead" in n or "dist" in n:
        return "Distortion - JCM800"
    return "Crunch - JTM45"


COL = {"bg": "#1b1d23", "panel": "#23262e", "panel2": "#2b2f39", "fg": "#e6e8ee",
       "muted": "#9aa0ad", "accent": "#e0592a", "accent_hi": "#ff6f3c",
       "field": "#2f333d", "border": "#3a3f4b", "ok": "#4caf72"}


def apply_dark_theme(root, ttk):
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    root.configure(bg=COL["bg"])
    style.configure(".", background=COL["bg"], foreground=COL["fg"],
                    fieldbackground=COL["field"], bordercolor=COL["border"],
                    font=("Segoe UI", 10))
    style.configure("TFrame", background=COL["bg"])
    style.configure("Card.TFrame", background=COL["panel"])
    style.configure("TLabel", background=COL["bg"], foreground=COL["fg"])
    style.configure("Card.TLabel", background=COL["panel"], foreground=COL["fg"])
    style.configure("Muted.TLabel", background=COL["panel"], foreground=COL["muted"])
    style.configure("Head.TLabel", background=COL["bg"], foreground=COL["accent_hi"],
                    font=("Segoe UI Semibold", 11))
    style.configure("TEntry", fieldbackground=COL["field"], foreground=COL["fg"],
                    insertcolor=COL["fg"], borderwidth=1, relief="flat", padding=5)
    style.map("TEntry", bordercolor=[("focus", COL["accent"])])
    style.configure("TButton", background=COL["panel2"], foreground=COL["fg"],
                    borderwidth=0, focusthickness=0, padding=(10, 5))
    style.map("TButton", background=[("active", COL["border"])])
    style.configure("Accent.TButton", background=COL["accent"], foreground="white",
                    font=("Segoe UI Semibold", 11), padding=(16, 9))
    style.map("Accent.TButton", background=[("active", COL["accent_hi"])])
    style.configure("TCheckbutton", background=COL["panel"], foreground=COL["fg"])
    style.map("TCheckbutton", background=[("active", COL["panel"])])
    style.configure("TCombobox", fieldbackground=COL["field"], background=COL["panel2"],
                    foreground=COL["fg"], arrowcolor=COL["fg"], borderwidth=1, padding=4)
    style.map("TCombobox", fieldbackground=[("readonly", COL["field"])],
              foreground=[("readonly", COL["fg"])])
    root.option_add("*TCombobox*Listbox.background", COL["panel2"])
    root.option_add("*TCombobox*Listbox.foreground", COL["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", COL["accent"])
    return style


def make_dlc_key(artist, title):
    key = re.sub(r"[^A-Za-z0-9]", "", (artist + title))[:30]
    if not key or not key[0].isalpha():
        key = "Song" + key
    return key


def sort_value(s):
    s = s.strip()
    return s[4:] if s.lower().startswith("the ") else s


def download_audio(url, dest_dir, log):
    name = os.path.basename(url.split("?")[0]) or "audio_download"
    if "." not in name:
        name += ".bin"
    dest = os.path.join(dest_dir, name)
    log("Downloading audio from URL...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        if r.headers.get_content_type().startswith(("text/", "application/json")):
            raise ValueError(
                "That URL returned a web page, not an audio file. Use a DIRECT link to a "
                ".wav/.ogg/.mp3/.flac file (right-click the file > copy link).")
        shutil.copyfileobj(r, f)
    log(f"  saved {name} ({os.path.getsize(dest) // 1024} KB)")
    return dest


def _find_ffmpeg():
    """Find ffmpeg.exe: env, PATH, common spots, or next to the app."""
    import shutil as _sh
    p = _sh.which("ffmpeg") or _sh.which("ffmpeg.exe")
    if p:
        return p
    here = os.path.dirname(os.path.abspath(__file__))
    for base in [here, os.environ.get("FFMPEG_PATH", ""),
                 r"C:\ffmpeg\bin", r"C:\Program Files\ffmpeg\bin"]:
        if base and os.path.isfile(os.path.join(base, "ffmpeg.exe")):
            return os.path.join(base, "ffmpeg.exe")
    return None


WWISE_OK_EXT = (".wav", ".ogg", ".flac")


def _reveal_in_explorer(filepath):
    """Open the OS file browser with the given file selected/highlighted."""
    try:
        filepath = os.path.abspath(filepath)
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", filepath])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", filepath])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(filepath)])
    except Exception:
        pass


def _probe_sample_rate(ffmpeg, path):
    """Return the source sample rate in Hz, or None if unreadable (parses ffmpeg -i)."""
    try:
        r = subprocess.run([ffmpeg, "-i", path], capture_output=True, text=True, timeout=60)
        m = re.search(r"(\d+)\s*Hz", (r.stderr or ""))
        return int(m.group(1)) if m else None
    except Exception:
        return None


def ensure_wav(audio_path, dest_dir, log):
    """Rocksmith/Wwise require audio at EXACTLY 48000 Hz, 16-bit PCM. Anything
    above 48 kHz breaks playback in-game, so always normalize: probe the rate and
    force a clean 48k/16-bit/stereo wav unless the source is already exactly that."""
    ff = _find_ffmpeg()
    ext = os.path.splitext(audio_path)[1].lower()
    rate = _probe_sample_rate(ff, audio_path) if ff else None

    if ext == ".wav" and rate == 48000:
        log(f"  audio already 48kHz wav: {os.path.basename(audio_path)}")
        return audio_path

    if not ff:
        raise ValueError(
            "Rocksmith needs audio at exactly 48 kHz and I need ffmpeg to resample it. "
            "Put ffmpeg.exe next to this app (or on your PATH). "
            f"(Your file is {ext}{f', {rate} Hz' if rate else ''}.)")

    if rate and rate > 48000:
        log(f"  source is {rate} Hz (>48kHz) - resampling down to 48kHz for Rocksmith...")
    elif rate and rate != 48000:
        log(f"  source is {rate} Hz - resampling to 48kHz...")
    else:
        log("  normalizing audio to 48kHz/16-bit wav...")

    out = os.path.join(dest_dir, os.path.splitext(os.path.basename(audio_path))[0] + "_48k.wav")
    r = subprocess.run([ff, "-y", "-i", audio_path, "-ar", "48000", "-ac", "2",
                        "-sample_fmt", "s16", "-af", "aresample=resampler=soxr", out],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not os.path.exists(out):
        r = subprocess.run([ff, "-y", "-i", audio_path, "-ar", "48000", "-ac", "2",
                            "-sample_fmt", "s16", out],
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0 or not os.path.exists(out):
            tail = (r.stderr or "").strip().splitlines()[-3:]
            raise ValueError("ffmpeg conversion failed:\n" + "\n".join(tail))
    out_rate = _probe_sample_rate(ff, out)
    log(f"  wav ready at {out_rate or 48000} Hz: {os.path.basename(out)}")
    return out


def _run_packer(o, proj_dir, key, tmpl, result, log):
    packer = o.packer_path or _find_packer()
    if not packer and getattr(o, "cst_dir", None) and os.path.isdir(o.cst_dir):
        for dp, _d, fs in os.walk(o.cst_dir):
            if "packer.exe" in fs:
                packer = os.path.join(dp, "packer.exe")
                break
    if not packer:
        log("  [psarc] packer.exe not found - template written, but skipping build.")
        log(f"          run manually: packer.exe -b -t \"{tmpl}\" -o out_p.psarc -f Pc -v RS2014")
        return
    psarc_out = os.path.join(proj_dir, f"{key}_p.psarc")
    cmd = [packer, "-b", f"-t={tmpl}", f"-o={psarc_out}", "-f=Pc", "-v=RS2014"]
    log("  [psarc] running packer.exe -b ...")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        out = (r.stdout or "") + (r.stderr or "")
        result["packer_output"] = out.strip()
        logpath = os.path.join(proj_dir, "packer_output.txt")
        with open(logpath, "w", encoding="utf-8") as lf:
            lf.write("COMMAND: " + " ".join(cmd) + "\n\n" + out)
        result["packer_log"] = logpath
        for line in out.strip().splitlines()[-12:]:
            log("    | " + line)
        # CST appends _p to the name; accept either
        produced = psarc_out if os.path.exists(psarc_out) else (
            psarc_out.replace("_p.psarc", "_p.psarc"))
        if r.returncode == 0 and os.path.exists(produced):
            log(f"  [psarc] BUILT: {os.path.basename(produced)}")
            result["psarc"] = produced
        else:
            log("  [psarc] packer did not produce a psarc; full output in packer_output.txt")
            result["packer_failed"] = True
    except Exception as e:
        log(f"  [psarc] failed to run packer: {e}")
        result["packer_failed"] = True
        result["packer_output"] = str(e)


def build_project(o, log=print):
    gp = gp2rs.GPSong(o.gp_path)
    key = make_dlc_key(o.artist, o.title)
    proj_dir = os.path.join(o.out_dir, key)
    os.makedirs(proj_dir, exist_ok=True)
    log(f"Project folder: {proj_dir}")
    result = {"proj_dir": proj_dir, "psarc": None, "packer_failed": False,
              "packer_output": "", "packer_log": None}

    audio_path = o.audio_path
    if (not audio_path) and o.audio_url:
        audio_path = download_audio(o.audio_url, proj_dir, log)
    if audio_path:
        audio_path = ensure_wav(audio_path, proj_dir, log)

    arrangements, tones, used = [], [], set()
    cst_arrs, cst_tones = [], []  # for optional .psarc build
    ROUTE = {"Lead": "Lead", "Rhythm": "Rhythm", "Bass": "Bass"}
    ATYPE = {"Lead": "Guitar", "Rhythm": "Guitar", "Bass": "Bass"}
    args = SimpleNamespace(arr="Lead", leadin=o.leadin, title=o.title,
                           artist=o.artist, album=o.album, year=o.year)
    avg_tempo = 120
    for trk in o.tracks:
        if not trk["include"] or trk["arr"] == "Skip":
            continue
        args.arr = trk["arr"]
        xml, n_notes, n_single, n_chord = gp2rs.make_arrangement(gp, trk["index"], args)
        xml_name = f"{key}_{trk['arr'].lower()}.xml"
        xml_full = os.path.join(proj_dir, xml_name)
        with open(xml_full, "w", encoding="utf-8") as f:
            f.write(xml)
        log(f"  {trk['arr']}: {n_notes} notes ({n_single} single + {n_chord} chord) -> {xml_name}")

        tone_key = f"{key.lower()}_{trk['arr'].lower()}"
        if tone_key not in used:
            t = json.loads(json.dumps(TONE_PRESETS[trk["tone_label"]]))
            t["Key"] = t["Name"] = tone_key
            tones.append(t)
            cst_tones.append(preset_to_tone2014(TONE_PRESETS[trk["tone_label"]], tone_key))
            used.add(tone_key)
            log(f"      tone: {trk['tone_label']} ({t['GearList']['Amp']['Key']})")

        tun, _ = gp.effective_tuning(trk["index"])
        ref = gp2rs.STD_BASS if trk["arr"] == "Bass" else gp2rs.STD_TUNING
        offs = [tun[i] - ref[i] for i in range(min(len(ref), len(tun)))]
        offs += [0] * (6 - len(offs))
        name_enum, route = ARR_ENUM[trk["arr"]]
        mid = random.randint(1, 2**31 - 1)
        arrangements.append({"Case": "Instrumental", "Fields": [{
            "XML": xml_name, "Name": name_enum, "RouteMask": route, "Priority": 0,
            "ScrollSpeed": 1.3, "BassPicked": False, "Tuning": offs, "TuningPitch": 440.0,
            "BaseTone": tone_key, "Tones": [],
            "MasterID": mid, "PersistentID": str(uuid.uuid4())}]})
        cst_arrs.append(dict(
            arr_name=trk["arr"], arr_type=ATYPE[trk["arr"]], route_mask=ROUTE[trk["arr"]],
            xml_path=xml_full, master_id=mid, id=uuid.uuid4(), tone_key=tone_key,
            tuning=offs, tuning_pitch=440.0, tuning_name="Custom", scroll=13))

    vocals_name = None
    if o.lyrics_path:
        if o.lyrics_path.lower().endswith(".lrc"):
            vxml = gp2rs.lrc_to_vocals(o.lyrics_path)
        else:
            vxml = gp2rs.lyrics_txt_to_vocals(o.lyrics_path, gp, o.leadin)
        vname = f"{key}_vocals.xml"
        vocals_name = vname
        with open(os.path.join(proj_dir, vname), "w", encoding="utf-8") as f:
            f.write(vxml)
        log(f"  Vocals: {vname}")
        arrangements.append({"Case": "Vocals", "Fields": [{
            "XML": vname, "Japanese": False,
            "MasterID": random.randint(1, 2**31 - 1), "PersistentID": str(uuid.uuid4())}]})

    audio_rel = ""
    if audio_path:
        audio_rel = os.path.basename(audio_path)
        dst = os.path.join(proj_dir, audio_rel)
        if os.path.abspath(audio_path) != os.path.abspath(dst):
            shutil.copy2(audio_path, dst)
        log(f"  Audio: {audio_rel}")
    art_rel = ""
    if o.art_path:
        art_rel = os.path.basename(o.art_path)
        dst = os.path.join(proj_dir, art_rel)
        if os.path.abspath(o.art_path) != os.path.abspath(dst):
            shutil.copy2(o.art_path, dst)
        log(f"  Album art: {art_rel}")
    elif getattr(o, "make_psarc", False):
        # CST's FixPaths does Path.Combine(dir, AlbumArtPath) with NO null guard,
        # so a missing art path throws 'path2 null'. Always provide a real cover.
        art_rel = "cover.png"
        _write_default_cover(os.path.join(proj_dir, art_rel), o.title, o.artist, log)

    stem, ext = os.path.splitext(audio_rel) if audio_rel else ("preview", ".wav")
    project = {
        "Version": "1", "DLCKey": key,
        "ArtistName": {"Value": o.artist, "SortValue": sort_value(o.artist)},
        "Title": {"Value": o.title, "SortValue": sort_value(o.title)},
        "AlbumName": {"Value": o.album, "SortValue": sort_value(o.album)},
        "Year": int(o.year) if str(o.year).isdigit() else 2026,
        "AlbumArtFile": art_rel,
        "AudioFile": {"Path": audio_rel, "Volume": float(o.volume)},
        "AudioPreviewFile": {"Path": f"{stem}_preview{ext}" if audio_rel else "",
                             "Volume": float(o.volume)},
        "Arrangements": arrangements, "Tones": tones}
    proj_path = os.path.join(proj_dir, f"{key}.rs2dlc")
    with open(proj_path, "w", encoding="utf-8") as f:
        json.dump(project, f, indent=2)
    log(f"  Project file: {os.path.basename(proj_path)}")

    if getattr(o, "make_psarc", False):
        if cst_template is None:
            log("  [psarc] cst_template.py not found - skipping .psarc build.")
        else:
            # packer.exe needs the audio as .wem (it does NOT run Wwise itself).
            wem_rel = audio_rel
            preview_rel = (f"{stem}_preview{ext}" if audio_rel else "")
            if audio_path and wwise_convert is not None:
                try:
                    dest_wem = os.path.join(proj_dir, f"{stem}.wem")
                    wem, prev = wwise_convert.wav_to_wem(
                        os.path.join(proj_dir, audio_rel), dest_wem,
                        work_root=proj_dir,
                        cst_hint=(getattr(o, "cst_dir", None) or o.packer_path or None),
                        ffmpeg=_find_ffmpeg(), log=log)
                    wem_rel = os.path.basename(wem)
                    preview_rel = os.path.basename(prev)
                except Exception as e:
                    log(f"  [psarc] Wwise conversion failed: {e}")
                    result["packer_failed"] = True
                    result["packer_output"] = "Wwise wav->wem step failed:\n" + str(e)
                    wem_rel = None
            elif audio_path and wwise_convert is None:
                log("  [psarc] wwise_convert.py missing - cannot make .wem.")
                wem_rel = None

            if wem_rel is None and audio_path:
                log("  [psarc] no .wem audio - skipping packer build.")
            else:
                tmpl = cst_template.build_dlc_xml(
                    dict(dlc_key=key, title=o.title, artist=o.artist, album=o.album,
                         year=o.year, avg_tempo=avg_tempo,
                         art_path=art_rel or "", audio_path=wem_rel or "",
                         audio_preview_path=preview_rel,
                         vocals_xml=vocals_name,
                         volume=float(o.volume), preview_volume=float(o.volume)),
                    cst_arrs, cst_tones,
                    os.path.join(proj_dir, f"{key}.dlc.xml"))
                log(f"  [psarc] template: {os.path.basename(tmpl)}")
                _run_packer(o, proj_dir, key, tmpl, result, log)

    with open(os.path.join(proj_dir, "README.txt"), "w", encoding="utf-8") as f:
        f.write(f"NEXT STEPS (DLC Builder)\n========================\n"
                f"1. Open {key}.rs2dlc in Rocksmith DLC Builder.\n"
                f"2. Audio: use the volume-calculate wand + 'Create preview audio'.\n"
                f"3. The chart expects {o.leadin:g}s of silence before bar 1; pad the\n"
                f"   audio in DLC Builder if it starts immediately.\n"
                f"4. Tones are starting points - tweak in the Tones tab.\n"
                f"5. Validate, generate DD, Build.\n")
    log("\nDONE.")
    return result


def lookup_metadata(artist, title, log=print):
    """Query the free iTunes Search API for album + year. Returns dict or None."""
    import json as _j
    import urllib.parse
    import urllib.request
    term = urllib.parse.quote(f"{artist} {title}".strip())
    url = f"https://itunes.apple.com/search?term={term}&entity=song&limit=5"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = _j.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        log(f"  metadata lookup failed: {e}")
        return None
    results = data.get("results", [])
    if not results:
        return None
    # prefer a result whose track name loosely matches
    tl = title.lower().strip()
    best = None
    for res in results:
        if tl and tl in (res.get("trackName", "").lower()):
            best = res
            break
    best = best or results[0]
    year = ""
    if best.get("releaseDate"):
        year = best["releaseDate"][:4]
    return {"album": best.get("collectionName", ""), "year": year,
            "artist": best.get("artistName", ""), "art": best.get("artworkUrl100", "")}


def _write_default_cover(path, title, artist, log=print):
    """Write a valid 256x256 PNG with no third-party libs (raw zlib+struct).
    Solid dark background with a diagonal accent - just needs to be a real image."""
    import struct
    import zlib
    W = H = 256
    raw = bytearray()
    for y in range(H):
        raw.append(0)  # filter type 0 per scanline
        for x in range(W):
            # dark charcoal with a subtle orange diagonal band
            band = abs((x + y) % 96 - 48) < 6
            if band:
                raw += bytes((224, 89, 42))
            else:
                v = 27 + (x * 7 + y * 5) % 12
                raw += bytes((v, v + 2, v + 6))

    def chunk(typ, data):
        c = struct.pack(">I", len(data)) + typ + data
        return c + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)  # 8-bit RGB
    idat = zlib.compress(bytes(raw), 9)
    png = sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)
    log(f"  Album art: {os.path.basename(path)} (auto-generated placeholder)")


def _show_error_copyable(root, tk, ttk, title, message):
    """Popup with selectable text + a Copy button so errors are easy to share."""
    win = tk.Toplevel(root)
    win.title(title)
    win.configure(bg=COL["bg"])
    win.geometry("640x420")
    ttk.Label(win, text=title, style="Head.TLabel").pack(anchor="w", padx=14, pady=(12, 6))
    txt = tk.Text(win, wrap="word", bg=COL["panel2"], fg=COL["fg"],
                  insertbackground=COL["fg"], relief="flat", padx=10, pady=10,
                  font=("Consolas", 9))
    txt.insert("1.0", message)
    txt.configure(state="disabled")
    txt.pack(fill="both", expand=True, padx=14, pady=6)

    def copy():
        root.clipboard_clear()
        root.clipboard_append(message)

    bar = ttk.Frame(win)
    bar.pack(fill="x", padx=14, pady=(0, 12))
    ttk.Button(bar, text="Copy to clipboard", command=copy).pack(side="left")
    ttk.Button(bar, text="Close", command=win.destroy).pack(side="right")


def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("RS STUDIO")
    root.geometry("1200x900")
    root.minsize(900, 700)
    # app icon (looks for rs_studio.ico / rs_studio.png next to the script or in _MEIPASS)
    try:
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        ico = os.path.join(base, "rs_studio.ico")
        png = os.path.join(base, "rs_studio.png")
        if sys.platform.startswith("win") and os.path.exists(ico):
            root.iconbitmap(ico)
        elif os.path.exists(png):
            root.iconphoto(True, tk.PhotoImage(file=png))
    except Exception:
        pass
    try:
        root.state("zoomed")  # Windows: start maximized
    except Exception:
        try:
            root.attributes("-zoomed", True)
        except Exception:
            pass
    apply_dark_theme(root, ttk)

    outer = ttk.Frame(root, padding=14)
    outer.pack(fill="both", expand=True)

    def card(title):
        ttk.Label(outer, text=title, style="Head.TLabel").pack(anchor="w", pady=(8, 4))
        c = ttk.Frame(outer, style="Card.TFrame", padding=12)
        c.pack(fill="x")
        return c

    vars_ = {k: tk.StringVar() for k in
             ["gp", "lyrics", "audio", "audio_url", "art", "out",
              "title", "artist", "album", "year", "leadin", "volume"]}
    vars_["leadin"].set("10.0")
    vars_["volume"].set("-8.0")
    vars_["year"].set("2026")
    track_rows = []

    files = card("Files")
    files.columnconfigure(1, weight=1)

    def browse(var, kinds, save_dir=False):
        def cb():
            p = (filedialog.askdirectory(title="Choose output folder") if save_dir
                 else filedialog.askopenfilename(filetypes=kinds))
            if p:
                var.set(p)
                if var is vars_["gp"]:
                    load_gp(p)
        return cb

    def frow(parent, r, label, var, kinds, save_dir=False, muted=False):
        st = "Muted.TLabel" if muted else "Card.TLabel"
        ttk.Label(parent, text=label, style=st, width=15).grid(
            row=r, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(parent, textvariable=var).grid(row=r, column=1, sticky="ew", padx=4, pady=4)
        if kinds is not None:
            ttk.Button(parent, text="Browse", command=browse(var, kinds, save_dir)).grid(
                row=r, column=2, padx=4, pady=4)

    frow(files, 0, "Guitar Pro (.gp)", vars_["gp"], [("Guitar Pro 7/8", "*.gp")])
    frow(files, 1, "Lyrics (optional)", vars_["lyrics"],
         [("Lyrics", "*.txt *.lrc"), ("All files", "*.*")])
    frow(files, 2, "Audio file (opt.)", vars_["audio"],
         [("Audio", "*.wav *.ogg *.mp3 *.flac *.wem"), ("All files", "*.*")])
    frow(files, 3, "...or audio URL", vars_["audio_url"], None, muted=True)
    ttk.Label(files, text="direct file link", style="Muted.TLabel").grid(row=3, column=2, padx=4)
    frow(files, 4, "Album art (opt.)", vars_["art"],
         [("Images", "*.png *.jpg *.jpeg"), ("All files", "*.*")])
    frow(files, 5, "Output folder", vars_["out"], [], save_dir=True)

    meta = card("Song info")
    for i in range(4):
        meta.columnconfigure(i * 2 + 1, weight=1)
    for i, (label, kk) in enumerate(
            [("Title", "title"), ("Artist", "artist"), ("Album", "album"),
             ("Year", "year"), ("Lead-in (sec)", "leadin"), ("Volume (dB)", "volume")]):
        r, c = divmod(i, 2)
        ttk.Label(meta, text=label, style="Card.TLabel", width=14).grid(
            row=r, column=c * 2, sticky="w", padx=4, pady=4)
        ttk.Entry(meta, textvariable=vars_[kk]).grid(
            row=r, column=c * 2 + 1, sticky="ew", padx=4, pady=4)

    ttk.Label(outer, text="Arrangements & tones", style="Head.TLabel").pack(
        anchor="w", pady=(8, 4))
    arr_card = ttk.Frame(outer, style="Card.TFrame", padding=12)
    arr_card.pack(fill="x")
    arr_hint = ttk.Label(arr_card, text="Pick a .gp file to load its tracks.",
                         style="Muted.TLabel")
    arr_hint.pack(anchor="w")
    arr_grid = ttk.Frame(arr_card, style="Card.TFrame")
    arr_grid.pack(fill="x", pady=(6, 0))

    ttk.Label(outer, text="Log", style="Head.TLabel").pack(anchor="w", pady=(8, 4))
    log_text = tk.Text(outer, height=8, wrap="word", bg=COL["panel2"], fg=COL["fg"],
                       insertbackground=COL["fg"], relief="flat", padx=8, pady=8,
                       state="disabled", font=("Consolas", 9))
    log_text.pack(fill="both", expand=True)

    def log(msg):
        log_text.configure(state="normal")
        log_text.insert("end", str(msg) + "\n")
        log_text.see("end")
        log_text.configure(state="disabled")
        root.update_idletasks()

    def load_gp(path):
        for w in arr_grid.winfo_children():
            w.destroy()
        track_rows.clear()
        try:
            gp = gp2rs.GPSong(path)
        except Exception as e:
            messagebox.showerror("Can't read file", f"Not a valid GP7/8 .gp file:\n{e}")
            return
        title, artist = gp.title, gp.artist
        m = re.match(r"^(.*)\s+by\s+(.+)$", title, re.I)
        if m:
            title, artist = m.group(1).strip(), m.group(2).strip()
        vars_["title"].set(title)
        vars_["artist"].set(artist)
        vars_["album"].set(gp.album)
        if not vars_["out"].get():
            vars_["out"].set(os.path.dirname(os.path.abspath(path)))
        # auto-fetch album/year from the internet if missing
        if artist and title and not gp.album:
            def fetch():
                meta = lookup_metadata(artist, title, log=log)
                if meta:
                    def apply():
                        if meta.get("album") and not vars_["album"].get():
                            vars_["album"].set(meta["album"])
                        if meta.get("year"):
                            vars_["year"].set(meta["year"])
                        log(f"  found: {meta.get('album','?')} ({meta.get('year','?')})")
                    root.after(0, apply)
            threading.Thread(target=fetch, daemon=True).start()

        for c, h in enumerate(["Use", "Track", "Arrangement", "Tone preset"]):
            ttk.Label(arr_grid, text=h, style="Muted.TLabel").grid(
                row=0, column=c, sticky="w", padx=6, pady=(0, 4))
        guitars = 0
        for i in range(len(gp.tracks)):
            kind = gp.track_kind(i)
            if kind is None:
                continue
            name = " ".join((gp.tracks[i].findtext("Name") or f"Track {i}").split())
            preset = auto_preset(name)
            if kind == "bass":
                arr, tones = "Bass", BASS_PRESETS
                if preset not in tones:
                    preset = "Bass - Rock"
            else:
                arr, tones = ("Lead" if guitars == 0 else "Rhythm"), GUITAR_PRESETS
                if preset not in tones:
                    preset = "Distortion - JCM800" if arr == "Lead" else "Crunch - JTM45"
                guitars += 1
            row = {"index": i, "var_include": tk.BooleanVar(value=True),
                   "var_arr": tk.StringVar(value=arr),
                   "var_tone": tk.StringVar(value=preset)}
            r = len(track_rows) + 1
            ttk.Checkbutton(arr_grid, variable=row["var_include"]).grid(
                row=r, column=0, padx=6, pady=3)
            ttk.Label(arr_grid, text=f"{name}   (tuning {gp.tuning(i)})",
                      style="Card.TLabel").grid(row=r, column=1, sticky="w", padx=6, pady=3)
            ttk.Combobox(arr_grid, textvariable=row["var_arr"], width=10, state="readonly",
                         values=["Lead", "Rhythm", "Bass", "Skip"]).grid(
                row=r, column=2, padx=6, pady=3)
            ttk.Combobox(arr_grid, textvariable=row["var_tone"], width=22, state="readonly",
                         values=tones).grid(row=r, column=3, padx=6, pady=3)
            track_rows.append(row)
        arr_hint.configure(text=f"Loaded {os.path.basename(path)} - "
                                f"{len(track_rows)} playable track(s).")
        log(f"Loaded: {os.path.basename(path)} - {len(track_rows)} track(s)")

    def do_build():
        if not vars_["gp"].get():
            return messagebox.showwarning("Missing", "Pick a .gp file first.")
        if not vars_["out"].get():
            return messagebox.showwarning("Missing", "Pick an output folder.")
        arrs = [r["var_arr"].get() for r in track_rows
                if r["var_include"].get() and r["var_arr"].get() != "Skip"]
        if len(arrs) != len(set(arrs)):
            return messagebox.showwarning(
                "Duplicate arrangement",
                "Two tracks share an arrangement type. Make them unique (or Skip one).")
        build_btn.configure(state="disabled", text="Building...")
        o = SimpleNamespace(
            gp_path=vars_["gp"].get(), lyrics_path=vars_["lyrics"].get() or None,
            audio_path=vars_["audio"].get() or None,
            audio_url=vars_["audio_url"].get().strip() or None,
            art_path=vars_["art"].get() or None, out_dir=vars_["out"].get(),
            title=vars_["title"].get() or "Unknown", artist=vars_["artist"].get() or "Unknown",
            album=vars_["album"].get(), year=vars_["year"].get(),
            leadin=float(vars_["leadin"].get() or 10), volume=float(vars_["volume"].get() or -8),
            make_psarc=bool(psarc_var.get()), packer_path=(vars_["packer"].get() or None),
            cst_dir=(vars_["cst"].get() or None),
            tracks=[{"index": r["index"], "include": r["var_include"].get(),
                     "arr": r["var_arr"].get(), "tone_label": r["var_tone"].get()}
                    for r in track_rows])

        def worker():
            try:
                res = build_project(o, log=log)
                pdir = res["proj_dir"]
                if res.get("psarc"):
                    _reveal_in_explorer(res["psarc"])
                    root.after(0, lambda: messagebox.showinfo(
                        "Done - .psarc built!",
                        f"Success!\n\n{res['psarc']}\n\nDrop it in your Rocksmith dlc folder."))
                elif res.get("packer_failed"):
                    tail = "\n".join((res.get("packer_output") or "").splitlines()[-15:])
                    msg = ("The project files were created, but packer.exe couldn't build "
                           "the .psarc.\n\nThis is the error to send back:\n\n" + tail +
                           f"\n\n(Full output saved to packer_output.txt in {pdir})")
                    root.after(0, lambda: _show_error_copyable(
                        root, tk, ttk, "psarc build failed", msg))
                else:
                    root.after(0, lambda: messagebox.showinfo(
                        "Done", f"Project built:\n{pdir}\n\nOpen the .rs2dlc in DLC Builder "
                        "to finish, or tick the .psarc box. See README.txt."))
            except Exception as e:
                root.after(0, lambda: log("ERROR: " + str(e)))
                root.after(0, lambda: log(traceback.format_exc()))
                root.after(0, lambda: _show_error_copyable(
                    root, tk, ttk, "Build failed", str(e)))
            finally:
                root.after(0, lambda: build_btn.configure(
                    state="normal", text="Build DLC Builder Project"))
        threading.Thread(target=worker, daemon=True).start()

    vars_["packer"] = tk.StringVar()
    psarc_var = tk.BooleanVar(value=True)
    psarc_card = ttk.Frame(outer, style="Card.TFrame", padding=12)
    psarc_card.pack(fill="x", pady=(8, 0))
    psarc_card.columnconfigure(1, weight=1)
    ttk.Checkbutton(psarc_card, variable=psarc_var,
                    text="Also build .psarc with CST + Wwise 2013").grid(
        row=0, column=0, columnspan=3, sticky="w", padx=4, pady=2)
    ttk.Label(psarc_card, text="packer.exe", style="Muted.TLabel", width=15).grid(
        row=1, column=0, sticky="w", padx=4, pady=4)
    ttk.Entry(psarc_card, textvariable=vars_["packer"]).grid(
        row=1, column=1, sticky="ew", padx=4, pady=4)
    ttk.Button(psarc_card, text="Browse", command=browse(
        vars_["packer"], [("packer.exe", "packer.exe"), ("All", "*.*")])).grid(
        row=1, column=2, padx=4, pady=4)
    vars_["cst"] = tk.StringVar()
    ttk.Label(psarc_card, text="CST folder", style="Muted.TLabel", width=15).grid(
        row=2, column=0, sticky="w", padx=4, pady=4)
    ttk.Entry(psarc_card, textvariable=vars_["cst"]).grid(
        row=2, column=1, sticky="ew", padx=4, pady=4)
    ttk.Button(psarc_card, text="Browse",
               command=browse(vars_["cst"], [], save_dir=True)).grid(
        row=2, column=2, padx=4, pady=4)
    ttk.Label(psarc_card,
              text="Leave blank to auto-find. CST folder holds packer.exe + Wwise2013.tar.bz2.",
              style="Muted.TLabel").grid(row=3, column=0, columnspan=3, sticky="w", padx=4)

    build_btn = ttk.Button(outer, text="Build DLC Builder Project",
                           style="Accent.TButton", command=do_build)
    build_btn.pack(pady=12)
    root.mainloop()


if __name__ == "__main__":
    run_gui()
