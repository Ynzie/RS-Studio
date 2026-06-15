#!/usr/bin/env python3
"""
gp2rs_studio.py - GP2Rocksmith Studio (Spotify-Vibe Edition)

A dark, sleek GUI that turns a Guitar Pro 7/8 (.gp) file into a
ready-to-build Rocksmith DLC Builder project.
Features cinematic embedded video previews, zero-click auto-fetching,
and 100% automated dependency management.
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
import urllib.parse
import uuid
import zipfile
import io
import time
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

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    # Attempt silent background install of Pillow so art features work
    def _try_install_pillow():
        try:
            import subprocess as _sp, sys as _sys
            _sp.run([_sys.executable, "-m", "pip", "install", "Pillow", "--quiet",
                     "--break-system-packages"],
                    capture_output=True, timeout=60)
            # Reload attempt — if it works HAS_PIL will be True next launch
        except Exception:
            pass
    import threading as _th
    _th.Thread(target=_try_install_pillow, daemon=True).start()

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

def _config_path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "RS Studio")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "settings.json")

def load_settings():
    try:
        with open(_config_path(), "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return {}

def save_settings(d):
    try:
        existing = load_settings()
        existing.update(d)
        with open(_config_path(), "w", encoding="utf-8") as f: json.dump(existing, f, indent=2)
    except Exception: pass

# ─── COLOR PALETTES ─────────────────────────────────────────────────────────

DARK_MODE_COL = {
    "bg": "#000000", "surface": "#121212", "card": "#181818", "card2": "#282828",
    "field": "#2a2a2a", "border": "#282828", "border_hi": "#404040", "fg": "#ffffff",
    "muted": "#b3b3b3", "dim": "#535353", "ok": "#1db954", "warn": "#e8a52a",
    "panel": "#181818", "panel2": "#282828"
}

LIGHT_MODE_COL = {
    "bg": "#e5e5e5", "surface": "#f3f3f3", "card": "#ffffff", "card2": "#eaeaea",
    "field": "#ffffff", "border": "#d9d9d9", "border_hi": "#b3b3b3", "fg": "#121212",
    "muted": "#535353", "dim": "#a7a7a7", "ok": "#1db954", "warn": "#e8a52a",
    "panel": "#ffffff", "panel2": "#eaeaea"
}

COL = DARK_MODE_COL.copy()
COL.update({"accent": "#1db954", "accent_hi": "#1ed760", "accent_glow": "#1db95433"})

_saved = load_settings()
save_settings({"light_mode": False})
if _saved.get("accent") and _saved.get("accent_hi"):
    COL["accent"] = _saved["accent"]; COL["accent_hi"] = _saved["accent_hi"]

# ─── DYNAMIC THEME ENGINE ───────────────────────────────────────────────────

_STYLED_WIDGETS = []

def styled(widget, **color_keys):
    _STYLED_WIDGETS.append((widget, color_keys))
    cfg = {k: COL[v] for k, v in color_keys.items() if v in COL}
    if cfg: widget.configure(**cfg)
    return widget

def refresh_theme(root, ttk):
    for w, kwargs in _STYLED_WIDGETS:
        try: w.configure(**{k: COL[v] for k, v in kwargs.items() if v in COL})
        except Exception: pass
    apply_ttk_theme(root, ttk)

def apply_ttk_theme(root, ttk):
    style = ttk.Style(root)
    try: style.theme_use("clam")
    except Exception: pass
    root.configure(bg=COL["bg"])
    base_font = ("Segoe UI", 10); small_font = ("Segoe UI", 9)
    style.configure(".", background=COL["surface"], foreground=COL["fg"], fieldbackground=COL["field"], bordercolor=COL["border"], font=base_font, relief="flat", borderwidth=0)
    style.configure("TScrollbar", background=COL["card"], troughcolor=COL["bg"], borderwidth=0, arrowsize=12)
    style.map("TScrollbar", background=[("active", COL["border_hi"])])
    style.configure("TCombobox", fieldbackground=COL["field"], background=COL["card2"], foreground=COL["fg"], arrowcolor=COL["muted"], borderwidth=0, padding=(8, 5))
    style.map("TCombobox", fieldbackground=[("readonly", COL["field"])], foreground=[("readonly", COL["fg"])], arrowcolor=[("active", COL["accent_hi"])])
    style.configure("TCheckbutton", background=COL["card"], foreground=COL["fg"], focusthickness=0)
    style.map("TCheckbutton", background=[("active", COL["card"])])
    root.option_add("*TCombobox*Listbox.background", COL["card2"])
    root.option_add("*TCombobox*Listbox.foreground", COL["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", COL["accent"])

# ─── CORE LOGIC ────────────────────────────────────────────────────────────

TONE_PRESETS = json.loads(r'''{"Clean - Twin": {"GearList": {"Amp": {"Type": "Amps", "KnobValues": {"Amp_TW40_Gain": 18, "Amp_TW40_Bass": 80, "Amp_TW40_Mid": 80, "Amp_TW40_Treble": 80, "Amp_TW40_Pres": 69}, "Key": "Amp_TW40"}, "Cabinet": {"Type": "Cabinets", "KnobValues": {}, "Key": "Cab_TW410C_57_Cone"}}, "ToneDescriptors": ["$[35720]CLEAN"], "NameSeparator": " - ", "IsCustom": true, "Volume": "-18.0", "Key": "clean_twin", "Name": "clean_twin"}, "Crunch - Orange": {"GearList": {"Amp": {"Type": "Amps", "KnobValues": {"Amp_OrangeAD50_Gain": 60, "Amp_OrangeAD50_Bass": 75, "Amp_OrangeAD50_Treble": 66, "Amp_OrangeAD50_Mid": 66}, "Key": "Amp_OrangeAD50"}, "Cabinet": {"Type": "Cabinets", "KnobValues": {}, "Key": "Cab_OrangePPC212OB_57_Cone"}}, "ToneDescriptors": ["$[35716]CRUNCH"], "NameSeparator": " - ", "IsCustom": true, "Volume": "-18.0", "Key": "crunch_orange", "Name": "crunch_orange"}, "Distortion - JCM800": {"GearList": {"Amp": {"Type": "Amps", "KnobValues": {"Amp_MarshallJCM800_Gain": 80, "Amp_MarshallJCM800_Bass": 75, "Amp_MarshallJCM800_Mid": 66, "Amp_MarshallJCM800_Treble": 66, "Amp_MarshallJCM800_Pres": 30}, "Key": "Amp_MarshallJCM800"}, "Cabinet": {"Type": "Cabinets", "KnobValues": {}, "Key": "Cab_Marshall1960a_57_Cone"}}, "ToneDescriptors": ["$[35722]DISTORTION", "$[35723]LEAD"], "NameSeparator": " - ", "IsCustom": true, "Volume": "-18.0", "Key": "dist_jcm", "Name": "dist_jcm"}, "Bass - Rock": {"GearList": {"Amp": {"Type": "Amps", "KnobValues": {"Bass_Amp_CH350B_Gain": 45, "Bass_Amp_CH350B_Bass": 6, "Bass_Amp_CH350B_Treble": 88, "Bass_Amp_CH350B_30": 6, "Bass_Amp_CH350B_90": -4, "Bass_Amp_CH350B_250": -6, "Bass_Amp_CH350B_800": 3, "Bass_Amp_CH350B_2500": -5, "Bass_Amp_CH350B_7500": 4, "Bass_Amp_CH350B_15000": 6}, "Key": "Bass_Amp_CH350B"}, "Cabinet": {"Type": "Cabinets", "KnobValues": {}, "Key": "Bass_Cab_TW215BC_57_Cone"}}, "ToneDescriptors": ["$[35715]BASS"], "NameSeparator": " - ", "IsCustom": true, "Volume": "-20.0", "Key": "bass_rock", "Name": "bass_rock"}}''')
PRESET_LABELS = list(TONE_PRESETS)
GUITAR_PRESETS = [p for p in PRESET_LABELS if not p.startswith("Bass")]
BASS_PRESETS = [p for p in PRESET_LABELS if p.startswith("Bass")]
ARR_ENUM = {"Lead": (0, 1), "Rhythm": (2, 2), "Bass": (3, 4)}

def auto_preset(name):
    n = name.lower()
    if "bass" in n: return "Bass - Rock"
    if "clean" in n or "acoustic" in n: return "Clean - Twin"
    if "overdrive" in n or "crunch" in n: return "Crunch - Orange"
    return "Distortion - JCM800"

# ── FIX 1: Missing helper functions (were called but never defined) ──────────

def sort_value(s):
    """Strip leading 'The '/'A ' for Rocksmith sort fields."""
    s = s.strip()
    for prefix in ("The ", "the ", "A ", "a "):
        if s.startswith(prefix):
            return s[len(prefix):]
    return s

def clean_title_for_api(s):
    """Remove clutter from GP title/artist strings before a YouTube search."""
    s = re.sub(r"\(.*?\)|\[.*?\]", "", s)
    s = re.sub(r"[^A-Za-z0-9 '&]+", " ", s)
    return " ".join(s.split()).strip()

def detect_gp_bpm(path):
    """
    Best-effort extraction of the initial tempo (BPM) from a Guitar Pro 7/8
    .gp file. GP7/8 files are zip archives containing a score.gpif XML where
    the MasterTrack lists Tempo automations as e.g. <Value>120 2</Value>.
    Returns a float BPM, or None if it can't be determined.
    """
    try:
        with zipfile.ZipFile(path) as z:
            gpif_name = next((n for n in z.namelist() if n.lower().endswith("score.gpif")), None)
            if not gpif_name:
                return None
            data = z.read(gpif_name)
        import xml.etree.ElementTree as ET
        root_el = ET.fromstring(data)
        for automation in root_el.iter("Automation"):
            type_el = automation.find("Type")
            if type_el is None or (type_el.text or "").strip() != "Tempo":
                continue
            val_el = automation.find("Value")
            if val_el is None or not (val_el.text or "").strip():
                continue
            parts = re.split(r"[,\s]+", val_el.text.strip())
            try:
                bpm = float(parts[0])
            except (ValueError, IndexError):
                continue
            if 20.0 <= bpm <= 400.0:
                return bpm
        return None
    except Exception:
        return None


def extract_gp_tempo_map(path):
    """
    Return a list of (beat_position, bpm) tuples sorted by beat_position,
    extracted from the MasterTrack Automation elements in a GP7/8 .gp file.
    beat_position is the GP 'linear beat' position (integer ticks where 1 beat = 960 ticks typically).
    Also returns the tick resolution (LinearData denominator) so callers can convert to seconds.
    Returns ([(pos, bpm), ...], ticks_per_beat) or ([], 960) on failure.
    """
    try:
        import xml.etree.ElementTree as ET
        with zipfile.ZipFile(path) as z:
            gpif_name = next((n for n in z.namelist() if n.lower().endswith("score.gpif")), None)
            if not gpif_name:
                return [], 960
            data = z.read(gpif_name)
        root_el = ET.fromstring(data)
        # GP stores tick resolution in Score > Properties > GuitarProDivisions or just uses 960
        ticks_per_beat = 960
        events = []
        for automation in root_el.iter("Automation"):
            type_el = automation.find("Type")
            if type_el is None or (type_el.text or "").strip() != "Tempo":
                continue
            val_el  = automation.find("Value")
            pos_el  = automation.find("Position")
            lin_el  = automation.find("Linear")  # "1" means interpolated ramp
            if val_el is None:
                continue
            parts = re.split(r"[,\s]+", (val_el.text or "").strip())
            try:
                bpm = float(parts[0])
            except (ValueError, IndexError):
                continue
            if not (20.0 <= bpm <= 400.0):
                continue
            try:
                pos = int((pos_el.text or "0").strip()) if pos_el is not None else 0
            except ValueError:
                pos = 0
            events.append((pos, bpm))
        events.sort(key=lambda x: x[0])
        if not events:
            return [], ticks_per_beat
        return events, ticks_per_beat
    except Exception:
        return [], 960


def tempo_map_to_seconds(events, ticks_per_beat):
    """
    Convert a list of (tick_pos, bpm) events into (seconds, bpm) events.
    Returns list of (abs_seconds, bpm) sorted by time.
    """
    if not events:
        return []
    result = []
    cur_time = 0.0
    cur_tick = 0
    cur_bpm  = events[0][1]
    for tick, bpm in events:
        if tick > cur_tick:
            elapsed_beats = (tick - cur_tick) / ticks_per_beat
            cur_time += elapsed_beats * (60.0 / cur_bpm)
        result.append((cur_time, bpm))
        cur_tick = tick
        cur_bpm  = bpm
    return result


def extract_gp_note_onsets(path, track_index=0):
    """
    Extract absolute note onset times (in seconds) for the given track from a GP7/8 file.
    Uses the GP tempo map to convert beat positions to seconds.
    Returns a sorted list of float seconds, or [] on failure.
    """
    try:
        import xml.etree.ElementTree as ET
        with zipfile.ZipFile(path) as z:
            gpif_name = next((n for n in z.namelist() if n.lower().endswith("score.gpif")), None)
            if not gpif_name:
                return []
            data = z.read(gpif_name)
        root_el = ET.fromstring(data)

        # Build tempo map: list of (abs_tick, bpm)
        tempo_events, tpb = extract_gp_tempo_map(path)
        if not tempo_events:
            tempo_events = [(0, 120)]
        sec_events = tempo_map_to_seconds(tempo_events, tpb)

        def tick_to_sec(tick):
            """Convert an absolute tick to seconds using the tempo map."""
            t = 0.0
            prev_tick = 0; prev_sec = 0.0; prev_bpm = sec_events[0][1] if sec_events else 120.0
            for abs_sec, bpm in sec_events:
                # find which segment this tick falls in
                pass
            # simpler linear search
            cur_sec = 0.0; cur_tick = 0; cur_bpm = tempo_events[0][1]
            for ev_tick, ev_bpm in tempo_events:
                if tick <= ev_tick:
                    break
                elapsed = (ev_tick - cur_tick) / tpb * (60.0 / cur_bpm)
                cur_sec += elapsed
                cur_tick = ev_tick
                cur_bpm  = ev_bpm
            remaining = (tick - cur_tick) / tpb * (60.0 / cur_bpm)
            return cur_sec + remaining

        # Walk MasterBars → Bars → Beats → Notes for the given track
        master_bars = list(root_el.iter("MasterBar"))
        bars_el     = root_el.find(".//Bars")
        beats_el    = root_el.find(".//Beats")

        if bars_el is None or beats_el is None:
            return []

        bar_map  = {b.get("id"): b for b in bars_el.findall("Bar")}
        beat_map = {b.get("id"): b for b in beats_el.findall("Beat")}

        onsets = []
        cur_tick = 0

        for mb in master_bars:
            # Each MasterBar has a <Bars> child listing one bar id per track
            mb_bars = mb.find("Bars")
            if mb_bars is None:
                # advance by time sig
                num = int((mb.findtext("Time/Numerator") or "4"))
                den = int((mb.findtext("Time/Denominator") or "4"))
                cur_tick += int(tpb * 4 * num / den)
                continue

            bar_ids = (mb_bars.text or "").split()
            if track_index >= len(bar_ids):
                num = int((mb.findtext("Time/Numerator") or "4"))
                den = int((mb.findtext("Time/Denominator") or "4"))
                cur_tick += int(tpb * 4 * num / den)
                continue

            bar_id  = bar_ids[track_index]
            bar_el  = bar_map.get(bar_id)
            if bar_el is None:
                num = int((mb.findtext("Time/Numerator") or "4"))
                den = int((mb.findtext("Time/Denominator") or "4"))
                cur_tick += int(tpb * 4 * num / den)
                continue

            beat_ids = (bar_el.findtext("Beats") or "").split()
            beat_tick = cur_tick
            for bid in beat_ids:
                beat = beat_map.get(bid)
                if beat is None:
                    continue
                # Duration denominator: 4=quarter, 8=eighth etc
                try:
                    dur_val  = int(beat.findtext("Duration") or "4")
                    dur_ticks = int(tpb * 4 / dur_val)
                except (ValueError, ZeroDivisionError):
                    dur_ticks = tpb

                notes_el = beat.find("Notes")
                if notes_el is not None and (notes_el.text or "").strip():
                    onsets.append(tick_to_sec(beat_tick))

                beat_tick += dur_ticks

            # advance master cursor by bar length
            num = int((mb.findtext("Time/Numerator") or "4"))
            den = int((mb.findtext("Time/Denominator") or "4"))
            cur_tick += int(tpb * 4 * num / den)

        return sorted(set(round(t, 4) for t in onsets))
    except Exception:
        return []


def stretch_audio_to_gp(input_path, output_path, tempo_events_sec, audio_duration, log=print):
    """
    Time-stretch input audio so that the GP tempo map aligns with the audio timeline.
    Uses ffmpeg atempo filter with per-segment speed ratios.

    tempo_events_sec: list of (abs_seconds_in_audio, target_bpm) — the points where
                      the GP says the tempo changes, mapped to their expected audio positions.
    audio_duration:   total audio length in seconds.
    
    Strategy: each segment between tempo events is stretched independently so its
    duration matches what the GP score expects for that many bars at that BPM.
    We approximate by computing the ratio of (GP expected duration) / (audio segment duration).
    atempo range is 0.5–100.0; values outside that are chained.
    """
    ff = _find_exe("ffmpeg")
    if not ff:
        raise RuntimeError("ffmpeg not found")

    if not tempo_events_sec or len(tempo_events_sec) < 2:
        # Single BPM — straightforward whole-file stretch not needed (lead-in handles offset)
        # Just copy the file
        import shutil as _sh
        _sh.copy2(input_path, output_path)
        log("  [stretch] Single-tempo file — no stretch needed, copied as-is.")
        return output_path

    # Build a flat atempo filter chain.
    # For multi-tempo songs we do a single global atempo approximation:
    # find the median ratio across all segments and apply it.
    # A true per-segment stretch requires splitting + reassembling which is complex;
    # the single-ratio approach handles the common case of slightly wrong constant BPM.
    # If the GP has genuine tempo changes, the beat grid overlay is the better guide.

    # Compute GP total duration vs audio duration
    # GP duration = sum of (beats_in_segment / bpm * 60) for each segment
    gp_total = 0.0
    for i, (t_sec, bpm) in enumerate(tempo_events_sec):
        next_t = tempo_events_sec[i + 1][0] if i + 1 < len(tempo_events_sec) else audio_duration
        seg_audio_dur = next_t - t_sec
        # how long GP expects this segment (same audio beats, but maybe different BPM)
        # Since we don't have beat counts per segment from here, use the ratio approach:
        # ratio = audio_bpm / gp_bpm  (if audio plays faster than GP expects, slow it down)
        gp_total += seg_audio_dur  # placeholder — refined below

    # Better: compute the ratio segment by segment and build a weighted average
    ratios = []
    weights = []
    ref_bpm = tempo_events_sec[0][1]
    for i, (t_sec, bpm) in enumerate(tempo_events_sec):
        next_t = tempo_events_sec[i + 1][0] if i + 1 < len(tempo_events_sec) else audio_duration
        seg_dur = next_t - t_sec
        if seg_dur <= 0:
            continue
        # ratio > 1 means speed up audio (audio is slower than GP expects)
        # ratio < 1 means slow down audio
        ratio = bpm / ref_bpm  # relative to first segment
        ratios.append(ratio)
        weights.append(seg_dur)

    if not ratios:
        import shutil as _sh
        _sh.copy2(input_path, output_path)
        return output_path

    # Weighted average ratio
    avg_ratio = sum(r * w for r, w in zip(ratios, weights)) / sum(weights)
    avg_ratio = max(0.25, min(4.0, avg_ratio))

    def _atempo_chain(ratio):
        """Build chained atempo filters for ratios outside 0.5–2.0."""
        filters = []
        r = ratio
        while r > 2.0:
            filters.append("atempo=2.0")
            r /= 2.0
        while r < 0.5:
            filters.append("atempo=0.5")
            r *= 2.0
        filters.append(f"atempo={r:.6f}")
        return ",".join(filters)

    af = _atempo_chain(avg_ratio)
    log(f"  [stretch] ratio={avg_ratio:.4f}  filter: {af}")

    r = subprocess.run(
        [ff, "-y", "-i", input_path, "-af", af,
         "-ar", "48000", "-ac", "2", "-sample_fmt", "s16", output_path],
        capture_output=True, text=True, timeout=300,
        creationflags=CREATE_NO_WINDOW)

    if r.returncode != 0 or not os.path.exists(output_path):
        raise RuntimeError(f"ffmpeg stretch failed:\n{r.stderr[-400:]}")

    log(f"  [stretch] Written: {os.path.basename(output_path)}")
    return output_path

def _find_slopsmith_exe():
    """Best-effort search for the Slopsmith Desktop app executable on Windows."""
    bases = [os.environ.get("LOCALAPPDATA", ""), os.environ.get("ProgramFiles", ""),
             os.environ.get("ProgramFiles(x86)", "")]
    subpaths = [
        r"Programs\slopsmith-desktop\Slopsmith.exe",
        r"Programs\Slopsmith\Slopsmith.exe",
        r"slopsmith-desktop\Slopsmith.exe",
        r"Slopsmith\Slopsmith.exe",
        r"Slopsmith Desktop\Slopsmith.exe",
        r"Slopsmith\Slopsmith Desktop.exe",
    ]
    for base in filter(None, bases):
        for sub in subpaths:
            p = os.path.join(base, sub)
            if os.path.isfile(p):
                return p
    return _find_exe("Slopsmith") or _find_exe("slopsmith-desktop")

def preset_to_tone2014(preset_dict, tone_key):
    """Convert a TONE_PRESETS entry into the lightweight dict CST expects."""
    return {
        "Key": tone_key,
        "Name": tone_key,
        "Volume": preset_dict.get("Volume", "-18.0"),
        "GearList": preset_dict.get("GearList", {}),
        "ToneDescriptors": preset_dict.get("ToneDescriptors", []),
        "IsCustom": True,
    }

# ────────────────────────────────────────────────────────────────────────────

def make_dlc_key(artist, title):
    key = re.sub(r"[^A-Za-z0-9]", "", (artist + title))[:30]
    return key if (key and key[0].isalpha()) else "Song" + key

def _find_packer(extra_hint=None):
    """Search common locations for packer.exe, return path or None."""
    here = os.path.dirname(os.path.abspath(__file__))
    search_bases = [
        extra_hint or "",
        os.environ.get("CST_PATH", ""),
        here,  # same folder as the script
        os.path.join(here, "CST"),
        os.path.join(here, "toolkit"),
        r"C:\Program Files (x86)\Custom Song Toolkit",
        r"C:\Program Files\Custom Song Toolkit",
        r"C:\CST",
        r"C:\CustomSongToolkit",
        os.path.join(os.path.expanduser("~"), "CST"),
        os.path.join(os.path.expanduser("~"), "Custom Song Toolkit"),
        os.path.expanduser("~"),
    ]
    seen = set()
    for base in filter(None, search_bases):
        base = os.path.normpath(base)
        if base in seen or not os.path.exists(base): continue
        seen.add(base)
        # Direct file check first (fast)
        direct = os.path.join(base, "packer.exe")
        if os.path.isfile(direct): return direct
        # Walk up to 3 levels deep (avoid full home-dir crawl)
        try:
            for root_dir, dirs, files in os.walk(base):
                depth = root_dir[len(base):].count(os.sep)
                if depth > 3:
                    dirs[:] = []
                    continue
                if "packer.exe" in files:
                    return os.path.join(root_dir, "packer.exe")
        except Exception:
            continue
    return None

def _find_exe(name):
    p = shutil.which(name) or shutil.which(f"{name}.exe")
    if p: return p
    here = os.path.dirname(os.path.abspath(__file__))
    if os.path.exists(os.path.join(here, f"{name}.exe")): return os.path.join(here, f"{name}.exe")
    return None

def _probe_sample_rate(ffmpeg, path):
    try:
        r = subprocess.run([ffmpeg, "-i", path], capture_output=True, text=True, timeout=60, creationflags=CREATE_NO_WINDOW)
        m = re.search(r"(\d+)\s*Hz", (r.stderr or ""))
        return int(m.group(1)) if m else None
    except Exception: return None

def generate_preview_audio(ffmpeg, input_wav, dest_dir, log):
    out_prev = os.path.join(dest_dir, os.path.splitext(os.path.basename(input_wav))[0] + "_preview.wav")
    if os.path.exists(out_prev): return out_prev
    try:
        r = subprocess.run([ffmpeg, "-i", input_wav], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
        m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", r.stderr)
        start_sec = 30.0
        if m:
            h, mins, s = m.groups()
            start_sec = max(0, (int(h)*3600 + int(mins)*60 + float(s)) * 0.3)
        af = "afade=t=in:ss=0:d=2,afade=t=out:st=28:d=2"
        subprocess.run([ffmpeg, "-y", "-i", input_wav, "-ss", str(start_sec), "-t", "30", "-ar", "48000", "-ac", "2", "-sample_fmt", "s16", "-af", af, out_prev], capture_output=True, creationflags=CREATE_NO_WINDOW)
        if os.path.exists(out_prev): return out_prev
    except Exception: pass
    return None

def ensure_wav(audio_path, dest_dir, log, leadin=0.0):
    ff = _find_exe("ffmpeg")
    ext = os.path.splitext(audio_path)[1].lower()
    rate = _probe_sample_rate(ff, audio_path) if ff else None
    if ext == ".wav" and rate == 48000 and leadin <= 0: return audio_path
    if not ff: raise ValueError("Requires ffmpeg.exe for audio normalization.")
    out = os.path.join(dest_dir, os.path.splitext(os.path.basename(audio_path))[0] + "_48k.wav")
    delay_ms = int(round(max(0.0, leadin) * 1000))
    af = f"adelay={delay_ms}|{delay_ms},aresample=resampler=soxr" if delay_ms > 0 else "aresample=resampler=soxr"
    r = subprocess.run([ff, "-y", "-i", audio_path, "-ar", "48000", "-ac", "2", "-sample_fmt", "s16", "-af", af, out], capture_output=True, text=True, timeout=300, creationflags=CREATE_NO_WINDOW)
    if r.returncode != 0 or not os.path.exists(out):
        af2 = f"adelay={delay_ms}|{delay_ms}" if delay_ms > 0 else "anull"
        subprocess.run([ff, "-y", "-i", audio_path, "-ar", "48000", "-ac", "2", "-sample_fmt", "s16", "-af", af2, out], capture_output=True, creationflags=CREATE_NO_WINDOW)
    return out

def fetch_media_pack(artist, title, dest_dir, confirmed_url, log):
    results = {"audio": None, "preview": None, "art": None, "lyrics": None, "album": "", "year": ""}
    def _norm(s): return re.sub(r"[^a-z0-9]+", "", s.lower())
    # Strip leading punctuation from title/artist before searching (e.g. ".CoDa." -> "CoDa")
    def _clean_search(s): return re.sub(r"^[^a-zA-Z0-9]+|[^a-zA-Z0-9]+$", "", s).strip()
    tl_norm, al_norm = _norm(title), _norm(artist)
    search_title = _clean_search(title)
    search_artist = _clean_search(artist)

    def _strip_album_junk(s):
        s = re.sub(r"\s*[\-\(]\s*(Single|EP|Live|Acoustic|Deluxe(\s+Edition)?|Remastered?(\s+Edition)?|\d{4}\s+Remaster)[\)\s]*$", "", s, flags=re.IGNORECASE)
        return s.strip()

    log(f"Fetching metadata & art for '{artist} - {title}'...")

    def _try_itunes():
        term = urllib.parse.quote(f"{search_artist} {search_title}".strip())
        req = urllib.request.Request(
            f"https://itunes.apple.com/search?term={term}&entity=song&limit=10",
            headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        best = None
        for res in data.get("results", []):
            tn = _norm(res.get("trackName", "")); an = _norm(res.get("artistName", ""))
            score = (2 if tl_norm and tl_norm in tn else 0) + (1 if al_norm and al_norm in an else 0)
            if score == 0 and search_title.lower() in res.get("trackName","").lower():
                score = 1
            if score > 0 and (best is None or score > best[0]):
                best = (score, res)
        if not best:
            return False
        res = best[1]
        results["album"] = _strip_album_junk(res.get("collectionName", ""))
        results["year"] = str(res.get("releaseDate", ""))[:4]
        art_url = res.get("artworkUrl100", "").replace("100x100bb","600x600bb").replace("100x100","600x600")
        if art_url:
            art_dest = os.path.join(dest_dir, "cover.jpg")
            with urllib.request.urlopen(urllib.request.Request(art_url, headers={"User-Agent":"Mozilla/5.0"}), timeout=10) as img_r, open(art_dest,"wb") as f:
                shutil.copyfileobj(img_r, f)
            results["art"] = art_dest
        log(f"  iTunes: {results['album']} ({results['year']})")
        return True

    def _try_lastfm():
        """Last.fm open API — no API key needed for track.getInfo."""
        try:
            q = urllib.parse.quote(f"{search_artist}")
            t = urllib.parse.quote(f"{search_title}")
            url = f"https://ws.audioscrobbler.com/2.0/?method=track.getInfo&api_key=b25b959554ed76058ac220b7b2e0a026&artist={q}&track={t}&format=json"
            req = urllib.request.Request(url, headers={"User-Agent": "gp2rs/2.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode("utf-8"))
            track = data.get("track", {})
            if not track: return False
            alb = track.get("album", {})
            if alb:
                results["album"] = _strip_album_junk(alb.get("title", ""))
                # Get year from wiki published date if available
                wiki = track.get("wiki", {})
                pub = wiki.get("published", "")
                if pub:
                    # Last.fm format: "12 Jan 2021, 00:00" — grab 4-digit year
                    m = re.search(r"\b(19|20)\d{2}\b", pub)
                    if m: results["year"] = m.group(0)
                # Also try release date from track itself
                if not results["year"]:
                    rd = track.get("releasedate", "")
                    if rd:
                        m2 = re.search(r"\b(19|20)\d{2}\b", rd)
                        if m2: results["year"] = m2.group(0)
                # Grab album art from Last.fm image array
                images = alb.get("image", [])
                for img in reversed(images):
                    img_url = img.get("#text", "")
                    if img_url and not results["art"]:
                        try:
                            art_dest = os.path.join(dest_dir, "cover.jpg")
                            with urllib.request.urlopen(urllib.request.Request(img_url, headers={"User-Agent":"gp2rs/2.0"}), timeout=10) as ir, open(art_dest,"wb") as f:
                                shutil.copyfileobj(ir, f)
                            results["art"] = art_dest
                        except Exception: pass
                        break
                log(f"  Last.fm: {results['album']} ({results['year']})")
                return bool(results["album"])
        except Exception as e:
            log(f"  Last.fm error: {e}")
        return False

    try:
        found = _try_itunes()
        if not found:
            log("  iTunes: no match, trying Last.fm...")
            found = _try_lastfm()
        if not found:
            log("  No album metadata found — you can enter it manually.")
    except Exception as e:
        log(f"  Metadata fetch error: {e}")
        try: _try_lastfm()
        except Exception: pass
    try:
        log("Fetching synced lyrics...")
        url = f"https://lrclib.net/api/get?artist_name={urllib.parse.quote(artist)}&track_name={urllib.parse.quote(title)}"
        req = urllib.request.Request(url, headers={"User-Agent": "gp2rs-studio/2.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            lrc_data = json.loads(r.read().decode("utf-8"))
            if lrc_data.get("syncedLyrics"):
                lrc_dest = os.path.join(dest_dir, "lyrics.lrc")
                with open(lrc_dest, "w", encoding="utf-8") as f: f.write(lrc_data["syncedLyrics"])
                results["lyrics"] = lrc_dest
    except Exception: log("  No synced lyrics found.")
    ytdlp = _find_exe("yt-dlp")
    if ytdlp and confirmed_url:
        try:
            log("Downloading confirmed audio...")
            out_tmpl = os.path.join(dest_dir, "ytdlp_audio.%(ext)s")
            try:
                for f in os.listdir(dest_dir):
                    if f.startswith("ytdlp_audio"):
                        try: os.remove(os.path.join(dest_dir, f))
                        except Exception: pass
            except Exception: pass
            subprocess.run([ytdlp, "-x", "--audio-format", "wav", "-o", out_tmpl, confirmed_url], capture_output=True, creationflags=CREATE_NO_WINDOW)
            for f in os.listdir(dest_dir):
                if f.startswith("ytdlp_audio") and f.endswith(".wav"):
                    raw_audio = os.path.join(dest_dir, f)
                    wav = ensure_wav(raw_audio, dest_dir, log, leadin=0.0)
                    results["audio"] = wav
                    ff = _find_exe("ffmpeg")
                    if ff: results["preview"] = generate_preview_audio(ff, wav, dest_dir, log)
                    break
        except Exception as e: log(f"  Audio fetch failed: {e}")
    return results

def _run_packer(o, proj_dir, key, tmpl, result, log):
    packer = o.packer_path or _find_packer()
    if not packer: return log("  [psarc] packer.exe not found - skipping build.")
    psarc_out = os.path.join(proj_dir, f"{key}_p.psarc")
    cmd = [packer, "-b", f"-t={tmpl}", f"-o={psarc_out}", "-f=Pc", "-v=RS2014"]
    log(f"  [psarc] cmd: {' '.join(cmd)}")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, creationflags=CREATE_NO_WINDOW)
        combined = ((r.stdout or "") + (r.stderr or "")).strip()
        result["packer_output"] = combined
        # Always log the output so we can diagnose
        for line in combined.splitlines()[:30]:
            if line.strip(): log(f"  [psarc] {line.rstrip()}")
        if r.returncode == 0 and os.path.exists(psarc_out):
            log(f"  [psarc] BUILT: {os.path.basename(psarc_out)}")
            result["psarc"] = psarc_out
        else:
            log(f"  [psarc] packer.exe exited rc={r.returncode}, file exists={os.path.exists(psarc_out)}")
            result["packer_failed"] = True
    except Exception as e:
        log(f"  [psarc] exception: {e}")
        result["packer_failed"] = True

def _prepare_art_for_packer(src_art, proj_dir, key, log):
    """
    Convert album art to a 512x512 DDS file that packer.exe can read directly,
    bypassing its broken temp-directory DDS conversion.
    
    Priority: ffmpeg DDS > PIL resize jpg > original file
    Returns absolute path to the best available art file.
    """
    dds_out = os.path.join(proj_dir, f"{key}_art.dds")
    jpg_out  = os.path.join(proj_dir, f"{key}_art512.jpg")

    # 1. Try ffmpeg → DDS (best: packer reads it directly, no temp conversion needed)
    ff = _find_exe("ffmpeg")
    if ff:
        try:
            r = subprocess.run(
                [ff, "-y", "-i", src_art,
                 "-vf", "scale=512:512:force_original_aspect_ratio=decrease,pad=512:512:(ow-iw)/2:(oh-ih)/2",
                 "-pix_fmt", "bgra", dds_out],
                capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW
            )
            if r.returncode == 0 and os.path.exists(dds_out) and os.path.getsize(dds_out) > 1000:
                log(f"  [art] converted to DDS: {os.path.basename(dds_out)}")
                return dds_out
            else:
                log(f"  [art] ffmpeg DDS failed (rc={r.returncode}), trying JPEG resize")
        except Exception as e:
            log(f"  [art] ffmpeg DDS exception: {e}")

    # 2. Try PIL → 512x512 JPEG
    try:
        from PIL import Image as _PILImg
        img = _PILImg.open(src_art).convert("RGB")
        img = img.resize((512, 512), _PILImg.Resampling.LANCZOS)
        img.save(jpg_out, "JPEG", quality=95)
        log(f"  [art] resized to 512x512 JPEG: {os.path.basename(jpg_out)}")
        return jpg_out
    except Exception as e:
        log(f"  [art] PIL resize failed ({e}), using original")

    # 3. Fallback: original file as-is (packer may still fail but we tried)
    return src_art


def build_project(o, log=print):
    gp = gp2rs.GPSong(o.gp_path)
    key = make_dlc_key(o.artist, o.title)
    proj_dir = os.path.join(o.out_dir, key)
    os.makedirs(proj_dir, exist_ok=True)
    log(f"Building Project: {proj_dir}")
    xml_files_for_ddc = []
    result = {"proj_dir": proj_dir, "psarc": None, "packer_failed": False, "packer_output": ""}
    audio_path = ensure_wav(o.audio_path, proj_dir, log, leadin=o.leadin) if o.audio_path else None
    arrangements, tones, used = [], [], set()
    cst_arrs, cst_tones = [], []
    args = SimpleNamespace(arr="Lead", leadin=o.leadin, title=o.title, artist=o.artist, album=o.album, year=o.year)
    for trk in o.tracks:
        if not trk["include"] or trk["arr"] == "Skip": continue
        args.arr = trk["arr"]
        xml, _, _, _ = gp2rs.make_arrangement(gp, trk["index"], args)
        xml_name = f"{key}_{trk['arr'].lower()}.xml"
        xml_full = os.path.join(proj_dir, xml_name)
        with open(xml_full, "w", encoding="utf-8") as f: f.write(xml)
        xml_files_for_ddc.append(xml_full)
        tone_key = f"{key.lower()}_{trk['arr'].lower()}"
        if tone_key not in used:
            t = json.loads(json.dumps(TONE_PRESETS[trk["tone_label"]]))
            t["Key"] = t["Name"] = tone_key
            tones.append(t)
            cst_tones.append(preset_to_tone2014(TONE_PRESETS[trk["tone_label"]], tone_key))
            used.add(tone_key)
        tun, _ = gp.effective_tuning(trk["index"])
        ref = gp2rs.STD_BASS if trk["arr"] == "Bass" else gp2rs.STD_TUNING
        offs = [tun[i] - ref[i] for i in range(min(len(ref), len(tun)))]
        offs += [0] * (6 - len(offs))
        arrangements.append({
            "Case": "Instrumental",
            "Fields": [{
                "XML": xml_name, "Name": ARR_ENUM[trk["arr"]][0], "RouteMask": ARR_ENUM[trk["arr"]][1],
                "ScrollSpeed": float(o.scroll_speed), "TuningPitch": float(o.pitch),
                "Tuning": offs, "BaseTone": tone_key, "Tones": [],
                "MasterID": random.randint(1, 2**31 - 1), "PersistentID": str(uuid.uuid4())
            }]
        })
        cst_arrs.append(dict(arr_name=trk["arr"], arr_type={"Lead": "Guitar", "Rhythm": "Guitar", "Bass": "Bass"}[trk["arr"]], route_mask=trk["arr"], xml_path=xml_full, master_id=random.randint(1, 2**31 - 1), id=uuid.uuid4(), tone_key=tone_key, tuning=offs, tuning_pitch=float(o.pitch), tuning_name="Custom", scroll=int(round(float(o.scroll_speed)*10))))
    vocals_name = None
    if o.lyrics_path:
        vxml = gp2rs.lrc_to_vocals(o.lyrics_path, o.leadin) if o.lyrics_path.endswith(".lrc") else gp2rs.lyrics_txt_to_vocals(o.lyrics_path, gp, o.leadin)
        vname = f"{key}_vocals.xml"
        vocals_name = vname
        with open(os.path.join(proj_dir, vname), "w", encoding="utf-8") as f: f.write(vxml)
        arrangements.append({"Case": "Vocals", "Fields": [{"XML": vname, "Japanese": False, "MasterID": random.randint(1, 2**31 - 1), "PersistentID": str(uuid.uuid4())}]})
    audio_rel, preview_rel, art_rel = "", "", ""
    if audio_path:
        audio_rel = os.path.basename(audio_path)
        if os.path.abspath(audio_path) != os.path.abspath(os.path.join(proj_dir, audio_rel)): shutil.copy2(audio_path, os.path.join(proj_dir, audio_rel))
        if o.preview_path:
            preview_rel = os.path.basename(o.preview_path)
            if os.path.abspath(o.preview_path) != os.path.abspath(os.path.join(proj_dir, preview_rel)): shutil.copy2(o.preview_path, os.path.join(proj_dir, preview_rel))
    if o.art_path:
        art_rel = os.path.basename(o.art_path)
        if os.path.abspath(o.art_path) != os.path.abspath(os.path.join(proj_dir, art_rel)): shutil.copy2(o.art_path, os.path.join(proj_dir, art_rel))
    project = {
        "Version": "1", "DLCKey": key, "AppId": str(o.appid),
        "ArtistName": {"Value": o.artist, "SortValue": sort_value(o.artist)},
        "Title": {"Value": o.title, "SortValue": sort_value(o.title)},
        "AlbumName": {"Value": o.album, "SortValue": sort_value(o.album)},
        "Year": int(o.year) if str(o.year).isdigit() else 2026, "AlbumArtFile": art_rel,
        "AudioFile": {"Path": audio_rel, "Volume": float(o.volume)},
        "AudioPreviewFile": {"Path": preview_rel, "Volume": float(o.volume)},
        "Arrangements": arrangements, "Tones": tones
    }
    proj_path = os.path.join(proj_dir, f"{key}.rs2dlc")
    with open(proj_path, "w", encoding="utf-8") as f: json.dump(project, f, indent=2)
    if getattr(o, "use_ddc", False):
        ddc_path = _find_exe("ddc") or _find_exe("ddc64")
        if ddc_path:
            log("  [DDC] Applying Dynamic Difficulty to arrangements...")
            for xml_file in xml_files_for_ddc:
                try: subprocess.run([ddc_path, "-m", "4", "-p", "m", xml_file], creationflags=CREATE_NO_WINDOW)
                except Exception as e: log(f"  [DDC Error] on {os.path.basename(xml_file)}: {e}")
        else:
            log("  [DDC] Note: ddc.exe not found in app folder. Open .rs2dlc in DLC Builder to apply DDC.")
    if getattr(o, "make_psarc", False):
        if cst_template is not None:
            # Full Wwise + CST path
            wem_rel = audio_rel
            if audio_path and wwise_convert is not None:
                try:
                    dest_wem = os.path.join(proj_dir, os.path.splitext(audio_rel)[0] + ".wem")
                    wem, prev = wwise_convert.wav_to_wem(os.path.join(proj_dir, audio_rel), dest_wem, work_root=proj_dir, cst_hint=o.cst_dir or o.packer_path or None, ffmpeg=_find_exe("ffmpeg"), log=log)
                    wem_rel = os.path.basename(wem); preview_rel = os.path.basename(prev)
                except Exception as e:
                    log(f"  [psarc] Wwise conversion failed: {e}"); wem_rel = None
            if wem_rel:
                # Pre-convert art to DDS so packer doesn't try its own broken temp-dir conversion.
                # Packer's DDS conversion writes to AppData\Local\Temp\tmp\ which often
                # doesn't exist, causing "Could not find file" errors.
                art_abs = ""
                if art_rel:
                    src_art = os.path.join(proj_dir, art_rel)
                    if os.path.exists(src_art):
                        art_abs = _prepare_art_for_packer(src_art, proj_dir, key, log)
                    else:
                        art_abs = src_art

                tmpl = cst_template.build_dlc_xml(dict(
                    dlc_key=key, title=o.title, artist=o.artist, album=o.album,
                    year=int(o.year) if str(o.year).isdigit() else 2026,
                    avg_tempo=int(round(getattr(o, "bpm", 120) or 120)), art_path=art_abs,
                    audio_path=wem_rel or "", audio_preview_path=preview_rel,
                    vocals_xml=vocals_name, volume=float(o.volume),
                    preview_volume=float(o.volume)
                ), cst_arrs, cst_tones, os.path.join(proj_dir, f"{key}.dlc.xml"))
                _run_packer(o, proj_dir, key, tmpl, result, log)
            # If packer failed, log a clear hint
            if result.get("packer_failed"):
                log("  [psarc] Build failed. The .rs2dlc and .dlc.xml are in the project folder.")
                log("  [psarc] Open the .rs2dlc in RocksmithToolkitGUI to build manually if needed.")
        else:
            log("  [psarc] cst_template.py not found — PSARC skipped.")
            log("  [psarc] Place cst_template.py next to gp2rs_studio.py to enable auto-build.")
    log(f"\nDONE. Project saved to {proj_dir}")
    return result


# ─── GUI ────────────────────────────────────────────────────────────────────

def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk, colorchooser

    root = tk.Tk(); root.title("RS STUDIO"); root.geometry("1280x850"); root.minsize(1000, 700)

    try:
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        ico = os.path.join(base, "rs_studio.ico")
        if sys.platform == "win32":
            import ctypes
            myappid = 'ynzie.rsstudio.app.final'
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
        if os.path.exists(ico): root.iconbitmap(default=ico)
    except Exception: pass

    apply_ttk_theme(root, ttk)

    vars_ = {k: tk.StringVar() for k in ["gp", "lyrics", "audio", "preview_audio", "art", "out", "title", "artist", "album", "year", "leadin", "volume", "audio_url", "packer", "cst", "slop_url", "slop_exe", "slop_plugin_dir", "appid", "scroll_speed", "pitch", "bpm"]}
    vars_["leadin"].set("5.0"); vars_["volume"].set("-8.0"); vars_["year"].set("2026"); vars_["bpm"].set("120")
    vars_["slop_url"].set("http://localhost:8000")
    vars_["appid"].set("248750"); vars_["scroll_speed"].set("1.3"); vars_["pitch"].set("440.0")
    # Restore persisted paths from settings
    _s = load_settings()
    for _k in ("packer", "cst", "out", "slop_url", "slop_exe", "slop_plugin_dir", "appid", "scroll_speed", "pitch", "volume", "leadin"):
        if _s.get(_k): vars_[_k].set(_s[_k])

    psarc_var = tk.BooleanVar(value=True); use_ddc = tk.BooleanVar(value=True)
    # Auto-probe for packer.exe if not saved yet
    def _probe_packer_async():
        if not vars_["packer"].get():
            found = _find_packer()
            if found:
                root.after(0, lambda: vars_["packer"].set(found))
                root.after(0, lambda: log(f"  [packer] auto-found: {found}"))
    threading.Thread(target=_probe_packer_async, daemon=True).start()

    # Auto-probe for Slopsmith Desktop (.exe) if not saved yet
    def _probe_slop_async():
        if not vars_["slop_exe"].get():
            found = _find_slopsmith_exe()
            if found:
                root.after(0, lambda: vars_["slop_exe"].set(found))
                root.after(0, lambda: log(f"  [slopsmith] auto-found: {found}"))
    threading.Thread(target=_probe_slop_async, daemon=True).start()

    track_rows = []; current_page = tk.StringVar(value="main")
    main_vid_state = {"process": None, "url": None, "thumb_obj": None}
    _gp_ref = [None]   # mutable slot so Sync Tuner can read the loaded GP object

    root.columnconfigure(1, weight=1); root.rowconfigure(0, weight=1)

    # ══════════════════════════════════════════════════════════════════════
    # DEPENDENCY MANAGER
    # ══════════════════════════════════════════════════════════════════════
    def ensure_dependencies_then_run(callback):
        missing = []
        if not _find_exe("ffmpeg"): missing.append("ffmpeg")
        if not _find_exe("ffplay"): missing.append("ffplay")
        if not _find_exe("yt-dlp"): missing.append("yt-dlp")

        if not missing:
            if callback: callback()
            return

        root.update_idletasks()
        rx2 = root.winfo_rootx(); ry2 = root.winfo_rooty()
        rw2 = root.winfo_width();  rh2 = root.winfo_height()

        dep_backdrop = tk.Toplevel(root)
        dep_backdrop.overrideredirect(True)
        dep_backdrop.geometry(f"{rw2}x{rh2}+{rx2}+{ry2}")
        dep_backdrop.configure(bg="#000000")
        dep_backdrop.attributes("-alpha", 0.72)
        dep_backdrop.transient(root)
        dep_backdrop.lift()

        dl_win = tk.Toplevel(root)
        dl_win.title("Download Tools")
        dl_win.transient(root)
        dl_win.overrideredirect(True)
        w, h = 460, 220
        x = rx2 + (rw2 // 2) - (w // 2)
        y = ry2 + (rh2 // 2) - (h // 2)
        dl_win.geometry(f"{w}x{h}+{x}+{y}")
        dl_win.configure(bg=COL["card"], highlightthickness=1, highlightbackground=COL["border_hi"])
        dl_win.lift()
        dl_win.grab_set()

        styled(tk.Frame(dl_win, height=4), bg="accent").pack(fill="x")
        styled(tk.Label(dl_win, text="Missing Required Tools", font=("Segoe UI Semibold", 13)), fg="warn", bg="card").pack(pady=(24, 8))
        styled(tk.Label(dl_win, text="RS Studio needs yt-dlp and ffmpeg to fetch media.", font=("Segoe UI", 9)), fg="muted", bg="card").pack(pady=(0, 16))
        status_lbl = styled(tk.Label(dl_win, text="Initializing...", font=("Segoe UI Semibold", 9)), fg="accent", bg="card")
        status_lbl.pack(pady=(0, 16))

        def _destroy_overlay():
            try: dep_backdrop.destroy()
            except Exception: pass

        def _dl_worker():
            try:
                here = os.path.dirname(os.path.abspath(__file__))
                if not _find_exe("yt-dlp"):
                    root.after(0, lambda: status_lbl.configure(text="Downloading yt-dlp... (1/2)"))
                    urllib.request.urlretrieve("https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe", os.path.join(here, "yt-dlp.exe"))
                if not _find_exe("ffmpeg") or not _find_exe("ffplay"):
                    root.after(0, lambda: status_lbl.configure(text="Downloading ffmpeg suite... (2/2) [Large file]"))
                    zip_path = os.path.join(here, "ffmpeg.zip")
                    urllib.request.urlretrieve("https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip", zip_path)
                    root.after(0, lambda: status_lbl.configure(text="Extracting ffmpeg..."))
                    with zipfile.ZipFile(zip_path, 'r') as z:
                        for info in z.infolist():
                            if info.filename.endswith("ffmpeg.exe"):
                                with z.open(info) as zf, open(os.path.join(here, "ffmpeg.exe"), 'wb') as f: shutil.copyfileobj(zf, f)
                            elif info.filename.endswith("ffplay.exe"):
                                with z.open(info) as zf, open(os.path.join(here, "ffplay.exe"), 'wb') as f: shutil.copyfileobj(zf, f)
                    try:
                        if os.path.exists(zip_path): os.remove(zip_path)
                    except Exception: pass
                root.after(0, lambda: status_lbl.configure(text="✓ All tools installed!", fg=COL["ok"]))
                root.after(800, dl_win.destroy)
                root.after(900, _destroy_overlay)
                if callback: root.after(1000, callback)
            except Exception as e:
                root.after(0, lambda: status_lbl.configure(text=f"✗ Failed: {e}", fg=COL["warn"]))
                root.after(0, lambda: btn_retry.pack(pady=(0, 10)))

        btn_retry = tk.Button(dl_win, text="Retry Download", font=("Segoe UI", 9), cursor="hand2",
                              command=lambda: [btn_retry.pack_forget(), threading.Thread(target=_dl_worker, daemon=True).start()])
        styled(btn_retry, bg="card2", fg="fg", relief="flat", bd=0)
        threading.Thread(target=_dl_worker, daemon=True).start()

    # ══════════════════════════════════════════════════════════════════════
    # SIDEBAR
    # ══════════════════════════════════════════════════════════════════════
    sidebar = tk.Frame(root, width=240)
    styled(sidebar, bg="bg"); sidebar.grid(row=0, column=0, sticky="nsew"); sidebar.grid_propagate(False); sidebar.columnconfigure(0, weight=1)

    logo_frame = styled(tk.Frame(sidebar), bg="bg"); logo_frame.grid(row=0, column=0, sticky="ew", padx=20, pady=(28, 0))
    styled(tk.Label(logo_frame, text="RS", font=("Segoe UI Black", 28)), fg="accent", bg="bg").pack(side="left")
    styled(tk.Label(logo_frame, text=" STUDIO", font=("Segoe UI", 18, "bold")), fg="fg", bg="bg").pack(side="left")
    styled(tk.Label(sidebar, text="Automated CDLC Pipeline", font=("Segoe UI", 8)), fg="muted", bg="bg").grid(row=1, column=0, sticky="w", padx=22, pady=(2, 24))
    styled(tk.Frame(sidebar, height=1), bg="border").grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 20))

    def nav_label(parent, row, icon, text, target_page):
        f = styled(tk.Frame(parent, cursor="hand2"), bg="bg"); f.grid(row=row, column=0, sticky="ew", padx=16, pady=2)
        icon_lbl = styled(tk.Label(f, text=icon, font=("Segoe UI", 12), width=2, cursor="hand2"), fg="accent_hi", bg="bg"); icon_lbl.pack(side="left")
        text_lbl = styled(tk.Label(f, text=text, font=("Segoe UI Semibold", 10), cursor="hand2"), fg="fg", bg="bg"); text_lbl.pack(side="left", padx=(6, 0))
        def _enter(_e=None): f.configure(bg=COL["card"]); icon_lbl.configure(bg=COL["card"]); text_lbl.configure(bg=COL["card"])
        def _leave(_e=None):
            if current_page.get() != target_page: f.configure(bg=COL["bg"]); icon_lbl.configure(bg=COL["bg"]); text_lbl.configure(bg=COL["bg"])
        for w in (f, icon_lbl, text_lbl): w.bind("<Enter>", _enter); w.bind("<Leave>", _leave); w.bind("<Button-1>", lambda e: show_page(target_page))
        return f

    nav_label(sidebar, 3, "⬡", "Main", "main"); nav_label(sidebar, 4, "↻", "Slopsmith", "slopsmith")
    nav_label(sidebar, 5, "◐", "Theme", "theme"); nav_label(sidebar, 6, "≡", "Log", "log")

    sidebar.rowconfigure(7, weight=1)
    btn_frame = styled(tk.Frame(sidebar), bg="bg"); btn_frame.grid(row=8, column=0, sticky="ew", padx=14, pady=(0, 20)); btn_frame.columnconfigure(0, weight=1)
    build_btn = tk.Button(btn_frame, text="▶  Create CDLC", fg="white", activeforeground="white", relief="flat", bd=0, font=("Segoe UI Semibold", 11), cursor="hand2", pady=12, command=lambda: do_build())
    styled(build_btn, bg="accent", activebackground="accent_hi"); build_btn.pack(fill="x")

    page_container = styled(tk.Frame(root), bg="surface")
    page_container.grid(row=0, column=1, sticky="nsew"); page_container.rowconfigure(0, weight=1); page_container.columnconfigure(0, weight=1)
    pages = {}

    def show_page(name):
        for pg in pages.values(): pg.pack_forget()
        if name in pages: pages[name].pack(fill="both", expand=True)
        current_page.set(name)
        refresh_theme(root, ttk)

    def _persist_settings(*_):
        save_settings({k: vars_[k].get() for k in ("packer","cst","out","slop_url","slop_exe","slop_plugin_dir","appid","scroll_speed","pitch","volume","leadin")})
    for _pk in ("packer","cst","out","slop_url","slop_exe","slop_plugin_dir","appid","scroll_speed","pitch","volume","leadin"):
        vars_[_pk].trace_add("write", _persist_settings)

    def page_header(parent, text):
        hdr = styled(tk.Frame(parent), bg="bg"); hdr.pack(fill="x")
        inner = styled(tk.Frame(hdr), bg="bg"); inner.pack(fill="x", padx=32, pady=(28, 20))
        styled(tk.Label(inner, text=text, font=("Segoe UI Semibold", 16)), fg="fg", bg="bg").pack(side="left")
        styled(tk.Frame(hdr, height=1), bg="border").pack(fill="x")
        return hdr

    # ══════════════════════════════════════════════════════════════════════
    # PAGE: MAIN
    # ══════════════════════════════════════════════════════════════════════
    main_page = styled(tk.Frame(page_container), bg="surface"); pages["main"] = main_page
    page_header(main_page, "Project Configuration")

    mw = tk.Frame(main_page, bg=COL["surface"]); mw.pack(fill="both", expand=True)
    canvas = styled(tk.Canvas(mw, highlightthickness=0, bd=0), bg="surface")
    vsb = ttk.Scrollbar(mw, orient="vertical", command=canvas.yview); canvas.configure(yscrollcommand=vsb.set)
    canvas.pack(side="left", fill="both", expand=True); vsb.pack(side="right", fill="y")
    scroll_frame = styled(tk.Frame(canvas), bg="surface"); scroll_win = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
    scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=(0, 0, e.width, e.height)))
    canvas.bind("<Configure>", lambda e: canvas.itemconfig(scroll_win, width=e.width))

    def _mouse_scroll(e):
        if canvas.yview()[0] == 0.0 and e.delta > 0: return
        canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
    canvas.bind_all("<MouseWheel>", _mouse_scroll)

    content = styled(tk.Frame(scroll_frame), bg="surface"); content.pack(fill="both", expand=True, padx=28, pady=20)

    def make_card(parent, pady=(0, 24)):
        outer = styled(tk.Frame(parent, highlightthickness=1), bg="card", highlightbackground="border"); outer.pack(fill="x", pady=pady)
        inner = styled(tk.Frame(outer), bg="card"); inner.pack(fill="both", expand=True, padx=16, pady=16)
        return inner

    def section_header(parent, text, pady=(10, 8)):
        f = styled(tk.Frame(parent), bg="surface"); f.pack(fill="x", pady=pady)
        styled(tk.Label(f, text=text, font=("Segoe UI Semibold", 12)), fg="fg", bg="surface").pack(side="left")
        styled(tk.Frame(f, height=1), bg="border").pack(side="left", fill="x", expand=True, padx=(12, 0), pady=6)
        return f

    def browse(var, kinds, save_dir=False, after_cb=None):
        def cb():
            p = filedialog.askdirectory() if save_dir else filedialog.askopenfilename(filetypes=kinds)
            if p: var.set(p); (after_cb(p) if after_cb else None)
        return cb

    def file_field(parent, row, col, var, kinds, save_dir=False, after_cb=None, padx=(0, 0), pady=(0, 8), colspan=1):
        ef = styled(tk.Frame(parent, highlightthickness=1), bg="field", highlightbackground="border")
        ef.grid(row=row, column=col, columnspan=colspan, sticky="ew", pady=pady, padx=padx, ipady=1)
        e = styled(tk.Entry(ef, textvariable=var, relief="flat", font=("Segoe UI", 10), bd=4, highlightthickness=0), bg="field", fg="fg", insertbackground="accent_hi")
        e.pack(side="left", fill="x", expand=True)
        ttk.Button(ef, text="⋯", command=browse(var, kinds, save_dir, after_cb), style="Inline.TButton").pack(side="right", padx=2)
        e.bind("<FocusIn>", lambda ev: ef.config(highlightbackground=COL["accent"])); e.bind("<FocusOut>", lambda ev: ef.config(highlightbackground=COL["border"]))

    def plain_field(parent, row, col, var, padx=(0, 0), pady=(0, 8), width=None):
        ef = styled(tk.Frame(parent, highlightthickness=1), bg="field", highlightbackground="border")
        ef.grid(row=row, column=col, sticky="ew", pady=pady, padx=padx, ipady=1)
        e = styled(tk.Entry(ef, textvariable=var, relief="flat", font=("Segoe UI", 10), bd=4, highlightthickness=0, width=width if width else 0), bg="field", fg="fg", insertbackground="accent_hi")
        e.pack(fill="x", expand=True)
        e.bind("<FocusIn>", lambda ev: ef.config(highlightbackground=COL["accent"])); e.bind("<FocusOut>", lambda ev: ef.config(highlightbackground=COL["border"]))

    def col_label(parent, text, row, col, padx=(0, 0)):
        styled(tk.Label(parent, text=text, font=("Segoe UI", 8)), fg="muted", bg="card").grid(row=row, column=col, sticky="w", padx=padx, pady=(6, 2))

    # --- SECTION 1: FILES ---
    section_header(content, "Files")
    files_card = make_card(content)
    files_split = tk.Frame(files_card, bg=COL["card"]); files_split.pack(fill="both", expand=True)
    files_left = tk.Frame(files_split, bg=COL["card"]); files_left.pack(side="left", fill="both", expand=True)
    files_right = tk.Frame(files_split, bg=COL["card"]); files_right.pack(side="right", fill="y", padx=(20, 0))
    files_left.columnconfigure(1, weight=1)

    def _after_gp(p): load_gp(p)

    col_label(files_left, "GUITAR PRO FILE", 0, 0)
    file_field(files_left, 1, 0, vars_["gp"], [("Guitar Pro", "*.gp")], after_cb=_after_gp, colspan=2)

    col_label(files_left, "AUDIO URL (YouTube / Soundcloud etc.)", 2, 0)
    url_row = styled(tk.Frame(files_left), bg="card"); url_row.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 8))
    url_ef = styled(tk.Frame(url_row, highlightthickness=1), bg="field", highlightbackground="border"); url_ef.pack(side="left", fill="x", expand=True, ipady=1)
    url_e = styled(tk.Entry(url_ef, textvariable=vars_["audio_url"], relief="flat", font=("Segoe UI", 10), bd=4, highlightthickness=0), bg="field", fg="fg", insertbackground="accent_hi"); url_e.pack(fill="x", expand=True)
    url_e.bind("<FocusIn>", lambda ev: url_ef.config(highlightbackground=COL["accent"])); url_e.bind("<FocusOut>", lambda ev: url_ef.config(highlightbackground=COL["border"]))

    fetch_btn = tk.Button(url_row, text="⚡ Auto-Fetch Media", fg="white", relief="flat", bd=0, font=("Segoe UI Semibold", 9), cursor="hand2", padx=10, pady=4, command=lambda: ensure_dependencies_then_run(_show_media_inspector))
    styled(fetch_btn, bg="accent", activebackground="accent_hi"); fetch_btn.pack(side="right", padx=(8, 0))

    col_label(files_left, "OUTPUT FOLDER", 4, 0); col_label(files_left, "AUDIO FILE (.wav / .mp3 / .ogg)", 4, 1, padx=(16, 0))
    file_field(files_left, 5, 0, vars_["out"], [], save_dir=True)
    file_field(files_left, 5, 1, vars_["audio"], [("Audio", "*.wav *.ogg *.mp3 *.flac")], padx=(16, 0))

    col_label(files_left, "ALBUM ART (optional)", 6, 0); col_label(files_left, "LYRICS (.lrc or .txt)", 6, 1, padx=(16, 0))
    file_field(files_left, 7, 0, vars_["art"], [("Images", "*.png *.jpg *.jpeg")])
    file_field(files_left, 7, 1, vars_["lyrics"], [("Lyrics", "*.txt *.lrc")], padx=(16, 0))

    # Video preview panel (top-right)
    main_vid_container = styled(tk.Frame(files_right, highlightthickness=1, width=320, height=180), bg="field", highlightbackground="border")
    main_vid_container.pack(anchor="n"); main_vid_container.pack_propagate(False)
    main_thumb_lbl = styled(tk.Label(main_vid_container, text="No Media Loaded", font=("Segoe UI", 10)), bg="field", fg="dim")
    main_thumb_lbl.pack(expand=True, fill="both")

    def _toggle_main_play():
        if not main_vid_state["url"]: return
        if main_vid_state["process"] or _media_procs.get("video"):
            _stop_all_media()
            main_vid_state["process"] = None
            main_thumb_lbl.pack(expand=True, fill="both")
            return
        # Stop audio before starting video
        _stop_all_media()
        main_vid_play_btn.configure(text="Loading stream...")
        def _play():
                try:
                    ffplay, ytdlp = _find_exe("ffplay"), _find_exe("yt-dlp")
                    res = subprocess.run([ytdlp, "-f", "best[height<=480]/best", "-g", "--no-warnings", main_vid_state["url"]], capture_output=True, text=True, timeout=20, creationflags=CREATE_NO_WINDOW)
                    stream_url = res.stdout.strip()
                    if stream_url:
                        vid_title = f"RS_Main_Preview_{random.randint(10000, 99999)}"
                        # Launch ffplay off-screen so it never flashes at centre
                        cmd = [ffplay, "-window_title", vid_title, "-noborder",
                               "-x", "320", "-y", "180", "-left", "-9999", "-top", "-9999",
                               "-autoexit", "-volume", "50", stream_url]
                        proc = subprocess.Popen(cmd, creationflags=CREATE_NO_WINDOW)
                        main_vid_state["process"] = proc
                        _media_procs["video"] = proc
                        if sys.platform == "win32":
                            import ctypes
                            hwnd = 0
                            for _ in range(80):
                                hwnd = ctypes.windll.user32.FindWindowW(None, vid_title)
                                if hwnd: break
                                time.sleep(0.05)
                            if hwnd:
                                parent_hwnd = main_vid_container.winfo_id()
                                # Remove title/border, make it a child window
                                GWL_STYLE = -16
                                WS_CHILD = 0x40000000
                                ctypes.windll.user32.SetParent(hwnd, parent_hwnd)
                                ctypes.windll.user32.SetWindowLongW(hwnd, GWL_STYLE, WS_CHILD)
                                SWP_SHOWWINDOW = 0x0040
                                ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 320, 180, SWP_SHOWWINDOW)
                                root.after(0, lambda: main_thumb_lbl.pack_forget())
                                root.after(0, lambda: main_vid_play_btn.configure(text="⏹ Stop Video"))
                            else:
                                root.after(0, lambda: main_vid_play_btn.configure(text="⏹ Stop Video"))
                        else:
                            root.after(0, lambda: main_vid_play_btn.configure(text="⏹ Stop Video"))
                        proc.wait()
                        main_vid_state["process"] = None
                        _media_procs["video"] = None
                        root.after(0, lambda: main_thumb_lbl.pack(expand=True, fill="both"))
                        root.after(0, lambda: main_vid_play_btn.configure(text="▶ Play Video Preview"))
                except Exception:
                    main_vid_state["process"] = None
                    _media_procs["video"] = None
                    root.after(0, lambda: main_vid_play_btn.configure(text="▶ Play Video Preview"))
        threading.Thread(target=_play, daemon=True).start()

    main_vid_play_btn = tk.Button(files_right, text="▶ Play Video Preview", font=("Segoe UI Semibold", 9), cursor="hand2", command=_toggle_main_play, relief="flat", bd=0, padx=16, pady=6, state="disabled")
    styled(main_vid_play_btn, bg="card2", fg="dim", activebackground="border_hi"); main_vid_play_btn.pack(fill="x", pady=(12, 0))

    # --- SECTION 2: SONG INFO ---
    section_header(content, "Song Info")
    meta_card = make_card(content)
    meta_card.columnconfigure(0, weight=1); meta_card.columnconfigure(1, weight=1)

    col_label(meta_card, "TITLE", 0, 0); col_label(meta_card, "ARTIST", 0, 1, padx=(12, 0))
    col_label(meta_card, "YEAR", 0, 2, padx=(12, 0)); col_label(meta_card, "VOLUME (dB)", 0, 3, padx=(12, 0))
    plain_field(meta_card, 1, 0, vars_["title"]); plain_field(meta_card, 1, 1, vars_["artist"], padx=(12, 0))
    plain_field(meta_card, 1, 2, vars_["year"], padx=(12, 0), width=6); plain_field(meta_card, 1, 3, vars_["volume"], padx=(12, 0), width=7)

    col_label(meta_card, "ALBUM", 2, 0); plain_field(meta_card, 3, 0, vars_["album"])

    styled(tk.Frame(meta_card, height=1), bg="border").grid(row=4, column=0, columnspan=4, sticky="ew", pady=(6, 10))
    col_label(meta_card, "LEAD-IN TO ADD (seconds)", 5, 0)
    col_label(meta_card, "SONG TEMPO (BPM)", 5, 1, padx=(12, 0))
    plain_field(meta_card, 6, 0, vars_["leadin"], width=9)

    bpm_row = styled(tk.Frame(meta_card), bg="card")
    bpm_row.grid(row=6, column=1, sticky="w", padx=(12, 0), pady=(0, 8))
    ef_bpm = styled(tk.Frame(bpm_row, highlightthickness=1), bg="field", highlightbackground="border")
    ef_bpm.pack(side="left", ipady=1)
    e_bpm = styled(tk.Entry(ef_bpm, textvariable=vars_["bpm"], relief="flat", font=("Segoe UI", 10), bd=4, highlightthickness=0, width=9), bg="field", fg="fg", insertbackground="accent_hi")
    e_bpm.pack(side="left")
    e_bpm.bind("<FocusIn>", lambda ev: ef_bpm.config(highlightbackground=COL["accent"])); e_bpm.bind("<FocusOut>", lambda ev: ef_bpm.config(highlightbackground=COL["border"]))
    btn_bpm_redetect = tk.Button(bpm_row, text="↺", font=("Segoe UI", 9), cursor="hand2",
                                  command=lambda: _redetect_bpm(), relief="flat", bd=0, padx=6, pady=2)
    styled(btn_bpm_redetect, bg="card2", fg="muted", activebackground="border_hi")
    btn_bpm_redetect.pack(side="left", padx=(4, 0))

    styled(tk.Label(meta_card, text="RS Studio adds this silence so bar 1 lands perfectly. Use Slopsmith to find exact drift.", font=("Segoe UI", 8), justify="left", wraplength=300), fg="dim", bg="card").grid(row=7, column=0, sticky="w", pady=(2, 0))
    styled(tk.Label(meta_card, text="Auto-detected from the GP file's tempo map. Edit if it's wrong, or click ↺ to re-detect.", font=("Segoe UI", 8), justify="left", wraplength=300), fg="dim", bg="card").grid(row=7, column=1, sticky="w", padx=(12, 0), pady=(2, 0))

    # --- SECTION 3: ARRANGEMENTS ---
    section_header(content, "Arrangements & Tones")
    arr_card = make_card(content)
    arr_hint = styled(tk.Label(arr_card, text="Load a .gp file above to see tracks.", font=("Segoe UI", 9)), fg="muted", bg="card"); arr_hint.pack(anchor="w")
    arr_grid = styled(tk.Frame(arr_card), bg="card"); arr_grid.pack(fill="x", pady=(8, 0))

    # --- SECTION 4: DLC CONFIGURATION ---
    section_header(content, "DLC Output Configuration")
    cfg_card = make_card(content)
    cfg_f = styled(tk.Frame(cfg_card), bg="card"); cfg_f.pack(fill="x")

    styled(tk.Label(cfg_f, text="APP ID", font=("Segoe UI", 8)), fg="muted", bg="card").pack(side="left")
    ae = styled(tk.Entry(cfg_f, textvariable=vars_["appid"], font=("Segoe UI", 10), width=10, relief="flat"), bg="field", fg="fg", insertbackground="accent_hi")
    ae.pack(side="left", padx=(6, 20))

    styled(tk.Label(cfg_f, text="GLOBAL SCROLL SPEED", font=("Segoe UI", 8)), fg="muted", bg="card").pack(side="left")
    se = styled(tk.Entry(cfg_f, textvariable=vars_["scroll_speed"], font=("Segoe UI", 10), width=5, relief="flat"), bg="field", fg="fg", insertbackground="accent_hi")
    se.pack(side="left", padx=(6, 20))

    styled(tk.Label(cfg_f, text="PITCH (Hz)", font=("Segoe UI", 8)), fg="muted", bg="card").pack(side="left")
    pe = styled(tk.Entry(cfg_f, textvariable=vars_["pitch"], font=("Segoe UI", 10), width=6, relief="flat"), bg="field", fg="fg", insertbackground="accent_hi")
    pe.pack(side="left", padx=(6, 20))

    ttk.Checkbutton(cfg_f, text="Enable Dynamic Difficulty (DDC)", variable=use_ddc).pack(side="left")

    # --- SECTION 5: PACKER ---
    section_header(content, "Advanced Packer Settings")
    out_card = make_card(content)
    psarc_row = styled(tk.Frame(out_card), bg="card"); psarc_row.pack(fill="x", pady=(0, 6))
    ttk.Checkbutton(psarc_row, variable=psarc_var, text="Build final .psarc with CST + Wwise 2013", style="TCheckbutton").pack(side="left")
    pf = styled(tk.Frame(out_card), bg="card"); pf.pack(fill="x", pady=(4, 0))
    pf.columnconfigure(1, weight=1); pf.columnconfigure(3, weight=1)
    for ci, (lbl, vk, sd) in enumerate([("packer.exe", "packer", False), ("CST folder", "cst", True)]):
        col = ci * 2
        styled(tk.Label(pf, text=lbl.upper(), font=("Segoe UI", 8)), fg="muted", bg="card").grid(row=0, column=col, sticky="w", padx=(0 if col == 0 else 16, 0), pady=(0, 2))
        ef = styled(tk.Frame(pf, highlightthickness=1), bg="field", highlightbackground="border")
        ef.grid(row=1, column=col, sticky="ew", pady=(0, 4), padx=(0 if col == 0 else 16, 0), ipady=1)
        styled(tk.Entry(ef, textvariable=vars_[vk], relief="flat", font=("Segoe UI", 10), bd=4, highlightthickness=0), bg="field", fg="fg", insertbackground="accent_hi").pack(side="left", fill="x", expand=True)
        ttk.Button(ef, text="⋯", command=browse(vars_[vk], [] if sd else [("packer.exe", "packer.exe"), ("All", "*.*")], save_dir=sd), style="Inline.TButton").pack(side="right", padx=2)

    # ══════════════════════════════════════════════════════════════════════
    # PAGE: SYNC TUNER
    # ══════════════════════════════════════════════════════════════════════
    slop_page = styled(tk.Frame(page_container), bg="surface")
    pages["slopsmith"] = slop_page
    page_header(slop_page, "Sync Tuner")

    # ── waveform state ────────────────────────────────────────────────────
    _wf_state = {
        "raw": None, "sr": 2000, "duration": 0.0, "position": 0.0,
        "marked": None, "proc": None, "playing": False,
        "wall_start": 0.0, "play_offset": 0.0, "width": 0,
        "tick_id": None, "view_start": 0.0, "view_dur": 0.0,
        "note_onsets": [],   # list of float seconds from GP note extraction
        "show_notes": True,  # toggle for the tab marker overlay
    }

    # ── outer layout ──────────────────────────────────────────────────────
    sync_body = styled(tk.Frame(slop_page), bg="surface")
    sync_body.pack(fill="both", expand=True, padx=28, pady=16)
    sync_body.columnconfigure(0, weight=1)
    sync_body.columnconfigure(1, weight=0)
    sync_body.rowconfigure(1, weight=1)

    # ── STEP BANNER ───────────────────────────────────────────────────────
    banner = styled(tk.Frame(sync_body), bg="card")
    banner.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0,12))
    banner.columnconfigure((0,1,2,3), weight=1)

    def _step_col(parent, col, num, title, desc):
        f = styled(tk.Frame(parent), bg="card"); f.grid(row=0, column=col, padx=2, pady=10, sticky="ew")
        styled(tk.Label(f, text=num, font=("Segoe UI Black", 20)),
               fg="accent", bg="card").pack(anchor="w", padx=12)
        styled(tk.Label(f, text=title, font=("Segoe UI Semibold", 10)),
               fg="fg", bg="card").pack(anchor="w", padx=12)
        styled(tk.Label(f, text=desc, font=("Segoe UI", 8), wraplength=280, justify="left"),
               fg="muted", bg="card").pack(anchor="w", padx=12, pady=(2,8))

    _step_col(banner, 0, "①", "Load Waveform",
              "Click 'Load Waveform' below. Your audio appears as a pink wave.")
    _step_col(banner, 1, "②", "Find the first beat",
              "Click anywhere on the wave to jump to that spot. Hit Play. Listen for the very first note.")
    _step_col(banner, 2, "③", "Mark it",
              "The moment you hear the first note — click 'Mark & Set Lead-In'. Done. No other steps.")
    _step_col(banner, 3, "④", "Rebuild",
              "Click 'Rebuild CDLC'. The guitar chart now lines up with the audio.")

    # dividers between steps
    for c in [1,2,3]:
        styled(tk.Frame(banner, width=1), bg="border").grid(row=0, column=c, sticky="ns", padx=0)

    # ── WAVEFORM CANVAS ───────────────────────────────────────────────────
    WF_H = 200
    wf_border = styled(tk.Frame(sync_body, highlightthickness=1),
                       bg="card", highlightbackground="border")
    wf_border.grid(row=1, column=0, sticky="nsew", pady=(0,10))
    wf_canvas = tk.Canvas(wf_border, height=WF_H, bg=COL["card"],
                          highlightthickness=0, cursor="crosshair")
    wf_canvas.pack(fill="both", expand=True)

    # ── TIMESCALE / ZOOM BAR (slower timescale for fine sync work) ────────
    wf_zoom_bar = styled(tk.Frame(wf_border), bg="card")
    wf_zoom_bar.pack(fill="x", side="bottom", padx=8, pady=(0,6))

    styled(tk.Label(wf_zoom_bar, text="TIMESCALE", font=("Segoe UI", 7)),
           fg="muted", bg="card").pack(side="left", padx=(2,8))

    def _wf_fmt_t(t):
        vd = _wf_state["view_dur"]
        if vd <= 2: return f"{t:.3f}s"
        if vd <= 20: return f"{t:.2f}s"
        if vd <= 120: return f"{t:.1f}s"
        return f"{t:.0f}s"

    def _visible_peaks(w):
        """Compute (min, max) peak pairs for the current zoomed/panned view window."""
        raw = _wf_state.get("raw")
        if not raw: return []
        sr = _wf_state["sr"]; total = len(raw)
        vs = _wf_state["view_start"]; vd = _wf_state["view_dur"]
        start_idx = max(0, min(total, int(vs * sr)))
        end_idx = max(start_idx, min(total, int((vs + vd) * sr)))
        n = max(1, end_idx - start_idx)
        cols = max(1, w)
        peaks = []
        for c in range(cols):
            st = start_idx + (c * n) // cols
            en = start_idx + ((c + 1) * n) // cols
            if en <= st:
                en = min(total, st + 1)
            seg = raw[st:en]
            if seg:
                peaks.append((min(seg) / 32768.0, max(seg) / 32768.0))
            else:
                peaks.append((0.0, 0.0))
        return peaks

    def _wf_redraw(_e=None):
        w = wf_canvas.winfo_width(); h = WF_H
        if w < 2: return
        _wf_state["width"] = w
        wf_canvas.delete("all")
        wf_canvas.configure(bg=COL["card"])
        if not _wf_state.get("raw"):
            wf_canvas.create_text(w//2, h//2,
                text="① Click  Load Waveform  to begin",
                fill=COL["dim"], font=("Segoe UI", 13))
            return
        peaks = _visible_peaks(w)
        mid = h // 2
        n = len(peaks); col_w = max(1.0, w / n) if n else w
        for i, (lo, hi) in enumerate(peaks):
            x = i * col_w
            y_hi = mid - int(hi * mid * 0.92)
            y_lo = mid - int(lo * mid * 0.92)
            if y_hi == y_lo: y_lo = y_hi + 1
            wf_canvas.create_rectangle(x, y_hi, x+col_w, y_lo,
                                       fill=COL["accent"], outline="", width=0)
        wf_canvas.create_line(0, mid, w, mid, fill=COL["border"], width=1)
        vs = _wf_state["view_start"]; vd = _wf_state["view_dur"]

        # ── BEAT GRID OVERLAY ─────────────────────────────────────────────
        # Draw vertical bar lines based on current BPM + lead-in so the user
        # can see drift anywhere in the song (not just at bar 1).
        try:
            bpm = float(vars_["bpm"].get() or 0)
            leadin = float(vars_["leadin"].get() or 0)
        except ValueError:
            bpm = 0; leadin = 0
        if bpm > 0 and vd > 0:
            beat_sec = 60.0 / bpm          # seconds per beat
            bar_sec  = beat_sec * 4        # seconds per bar (4/4)
            # choose grid resolution: show beats when zoomed in, bars when zoomed out
            grid_sec = beat_sec if vd <= 8 else bar_sec
            # first grid line that falls inside the view window
            if grid_sec > 0:
                dur = _wf_state["duration"]
                # bar/beat index of the leftmost visible time
                idx_start = int((vs - leadin) / grid_sec)
                idx = max(0, idx_start - 1)
                while True:
                    t = leadin + idx * grid_sec
                    if t > dur or (t - vs) / vd > 1.05:
                        break
                    if t >= vs - grid_sec:
                        gx = int((t - vs) / vd * w)
                        is_bar = (abs(idx * grid_sec - round(idx * grid_sec / bar_sec) * bar_sec) < 0.001) if beat_sec != bar_sec else True
                        line_col  = "#3a3a3a" if is_bar else "#2a2a2a"
                        label_col = COL["dim"] if is_bar else "#2e2e2e"
                        wf_canvas.create_line(gx, 0, gx, h, fill=line_col, width=1)
                        if is_bar and vd <= 60:
                            bar_num = round(idx * grid_sec / bar_sec) + 1
                            wf_canvas.create_text(gx + 3, 6, anchor="nw",
                                text=f"{'B' if vd > 8 else 'b'}{bar_num}",
                                fill=label_col, font=("Consolas", 7))
                    idx += 1
        # ─────────────────────────────────────────────────────────────────

        # ── TAB NOTE ONSET MARKERS ────────────────────────────────────────
        # Tiny cyan ticks at the top showing where the GP says each note falls.
        # Use lead-in offset so they align with the beat grid.
        if _wf_state.get("show_notes") and _wf_state.get("note_onsets") and vd > 0:
            try:
                leadin_n = float(vars_["leadin"].get() or 0)
            except ValueError:
                leadin_n = 0.0
            tick_h = max(6, h // 6)
            for onset in _wf_state["note_onsets"]:
                t = onset + leadin_n
                if t < vs or t > vs + vd:
                    continue
                nx = int((t - vs) / vd * w)
                wf_canvas.create_line(nx, 0, nx, tick_h,
                                      fill="#00e5ff", width=1)
        # ─────────────────────────────────────────────────────────────────

        mk = _wf_state["marked"]
        if mk is not None and vd > 0 and vs <= mk <= vs + vd:
            mx = int((mk - vs) / vd * w)
            wf_canvas.create_line(mx-1, 0, mx-1, h, fill="#000000", width=3)
            wf_canvas.create_line(mx, 0, mx, h, fill=COL["ok"], width=3)
            wf_canvas.create_rectangle(mx+4, 4, mx+120, 26,
                                       fill="#000000", outline="", stipple="gray50")
            wf_canvas.create_text(mx+6, 14, anchor="w",
                text=f"Bar 1  {mk:.3f}s", fill=COL["ok"],
                font=("Segoe UI Semibold", 9))
        pos = _wf_state["position"]
        if vd > 0 and vs <= pos <= vs + vd:
            px = int((pos - vs) / vd * w)
            wf_canvas.create_line(px, 0, px, h, fill="#ffffff", width=2)
        # time labels at bottom reflect the visible (zoomed) window
        for frac in [0, 0.25, 0.5, 0.75, 1.0]:
            t = vs + frac * vd; tx = int(frac * w)
            wf_canvas.create_text(max(2, min(tx, w-2)), h-4,
                text=_wf_fmt_t(t), fill=COL["dim"],
                font=("Consolas", 7), anchor="s")
        try:
            wf_view_lbl.configure(text=f"{_wf_fmt_t(vs)} – {_wf_fmt_t(vs+vd)}  (window {vd:.2f}s)")
        except Exception: pass

    _wf_pan_setting = [False]

    def _update_pan_scale():
        dur = _wf_state["duration"]; vd = _wf_state["view_dur"]
        max_start = max(0.0, dur - vd)
        try:
            if max_start > 0.0005:
                wf_pan_scale.configure(to=max_start)
                wf_pan_scale.state(["!disabled"])
            else:
                wf_pan_scale.configure(to=0.0001)
                wf_pan_scale.state(["disabled"])
            _wf_pan_setting[0] = True
            wf_pan_var.set(_wf_state["view_start"])
            _wf_pan_setting[0] = False
        except Exception: pass

    def _wf_set_view(start, dur):
        full = _wf_state["duration"] or dur
        dur = max(0.2, min(dur, full if full > 0 else dur))
        start = max(0.0, min(start, max(0.0, full - dur)))
        _wf_state["view_start"] = start
        _wf_state["view_dur"] = dur
        _update_pan_scale()
        _wf_redraw()

    def _wf_zoom(factor):
        """factor < 1 zooms in (shows less time / 'slower' scrub), > 1 zooms out."""
        full = _wf_state["duration"]
        if full <= 0: return
        center = _wf_state["view_start"] + _wf_state["view_dur"] / 2.0
        new_dur = _wf_state["view_dur"] * factor
        _wf_set_view(center - new_dur / 2.0, new_dur)

    def _wf_preset_view(secs):
        if secs is None:
            _wf_set_view(0.0, _wf_state["duration"] or 1.0)
        else:
            center = _wf_state["view_start"] + _wf_state["view_dur"] / 2.0
            _wf_set_view(center - secs / 2.0, float(secs))

    def _wf_pan_changed(val):
        if _wf_pan_setting[0]: return
        try: v = float(val)
        except Exception: return
        _wf_state["view_start"] = v
        _wf_redraw()

    tk.Button(wf_zoom_bar, text="🔍−", font=("Segoe UI Semibold", 9), cursor="hand2",
              command=lambda: _wf_zoom(2.0), relief="flat", bd=0, padx=10, pady=3,
              bg=COL["card2"], fg=COL["fg"], activebackground=COL["border_hi"]).pack(side="left", padx=(0,2))
    tk.Button(wf_zoom_bar, text="🔍+", font=("Segoe UI Semibold", 9), cursor="hand2",
              command=lambda: _wf_zoom(0.5), relief="flat", bd=0, padx=10, pady=3,
              bg=COL["card2"], fg=COL["fg"], activebackground=COL["border_hi"]).pack(side="left", padx=(0,8))

    for _lbl, _secs in [("Fit", None), ("30s", 30), ("10s", 10), ("5s", 5), ("2s", 2), ("1s", 1), ("0.5s", 0.5)]:
        tk.Button(wf_zoom_bar, text=_lbl, font=("Segoe UI", 8), cursor="hand2",
                  command=lambda s=_secs: _wf_preset_view(s), relief="flat", bd=0, padx=8, pady=3,
                  bg=COL["card2"], fg=COL["muted"], activebackground=COL["border_hi"]).pack(side="left", padx=(0,3))

    wf_pan_var = tk.DoubleVar(value=0.0)
    wf_pan_scale = ttk.Scale(wf_zoom_bar, from_=0, to=0.0001, orient="horizontal",
                             variable=wf_pan_var, command=_wf_pan_changed)
    wf_pan_scale.state(["disabled"])
    wf_pan_scale.pack(side="left", fill="x", expand=True, padx=(12,8))

    wf_view_lbl = styled(tk.Label(wf_zoom_bar, text="Load a waveform to begin", font=("Consolas", 8)),
                         fg="dim", bg="card")
    wf_view_lbl.pack(side="right", padx=(8,2))

    wf_canvas.bind("<Configure>", _wf_redraw)

    def _get_dur(path):
        ff = _find_exe("ffmpeg")
        if not ff: return 0.0
        try:
            r = subprocess.run([ff, "-i", path], capture_output=True, text=True,
                               timeout=10, creationflags=CREATE_NO_WINDOW)
            m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", r.stderr)
            if m:
                h2, mn, s2 = m.groups()
                return int(h2)*3600 + int(mn)*60 + float(s2)
        except Exception: pass
        return 0.0

    def _load_waveform():
        path = vars_["audio"].get().strip()
        if not path or not os.path.exists(path):
            return messagebox.showwarning("No Audio",
                "Go to the Main page and set an audio file first.")
        ff = _find_exe("ffmpeg")
        if not ff:
            return messagebox.showwarning("No ffmpeg",
                "ffmpeg not found. Use Auto-Fetch Media on the Main page to install it.")
        btn_load_wf.configure(text="Loading…", state="disabled")
        status_var.set("Analysing audio…")
        def _worker():
            try:
                dur = _get_dur(path)
                _wf_state["duration"] = dur
                SR = 2000  # higher SR kept in "raw" so zoom/pan works
                r = subprocess.run(
                    [ff, "-y", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                    capture_output=True, timeout=120, creationflags=CREATE_NO_WINDOW)
                import struct
                raw_bytes = r.stdout; n_s = len(raw_bytes)//2
                vals = list(struct.unpack(f"<{n_s}h", raw_bytes[:n_s*2]))
                # FIX: store into "raw" (what _wf_redraw / _visible_peaks reads)
                _wf_state["raw"] = vals
                _wf_state["sr"]  = SR
                # initialise view to show entire song
                _wf_set_view(0.0, dur if dur > 0 else 1.0)
                root.after(0, _wf_redraw)
                root.after(0, lambda: status_var.set(
                    "② Click the waveform to seek, then hit Play. "
                    "When you hear beat 1, click  Mark & Set Lead-In."))
                root.after(0, lambda: btn_load_wf.configure(text="↺ Reload", state="normal"))
            except Exception as e:
                root.after(0, lambda: status_var.set(f"Error: {e}"))
                root.after(0, lambda: btn_load_wf.configure(text="Load Waveform", state="normal"))
        threading.Thread(target=_worker, daemon=True).start()

    # click to seek
    def _wf_click(event):
        dur = _wf_state["duration"]; w = wf_canvas.winfo_width()
        if dur <= 0 or w <= 0: return
        t = max(0.0, min(dur, event.x / w * dur))
        _wf_state["position"] = t; _wf_state["play_offset"] = t
        sync_pos_var.set(f"{t:.3f}s")
        _wf_redraw()
        if _wf_state["playing"]:
            _wf_stop()
            _wf_state["tick_id"] = root.after(100, _wf_play)

    wf_canvas.bind("<Button-1>", _wf_click)

    # playback
    def _wf_play():
        path = vars_["audio"].get().strip()
        if not path or not os.path.exists(path):
            return messagebox.showwarning("No Audio", "Set audio on Main page.")
        ffplay = _find_exe("ffplay")
        if not ffplay:
            return messagebox.showwarning("No ffplay", "ffplay not found.")
        _wf_stop()
        offset = _wf_state["play_offset"]
        proc = subprocess.Popen(
            [ffplay, "-nodisp", "-autoexit", "-ss", str(offset), "-volume", "80", path],
            creationflags=CREATE_NO_WINDOW)
        _wf_state.update(proc=proc, playing=True, wall_start=time.time())
        try: btn_wf_play.configure(text="⏸ Pause")
        except Exception: pass
        if _wf_state["tick_id"] is not None:
            try: root.after_cancel(_wf_state["tick_id"])
            except Exception: pass
        _wf_state["tick_id"] = root.after(40, _wf_tick)
        def _watch():
            proc.wait()
            _wf_state["playing"] = False; _wf_state["proc"] = None
            _wf_state["tick_id"] = None
            root.after(0, lambda: btn_wf_play.configure(text="▶ Play")
                       if btn_wf_play.winfo_exists() else None)
        threading.Thread(target=_watch, daemon=True).start()

    def _wf_stop():
        _wf_state["playing"] = False
        if _wf_state["tick_id"] is not None:
            try: root.after_cancel(_wf_state["tick_id"])
            except Exception: pass
            _wf_state["tick_id"] = None
        if _wf_state["proc"]:
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID",
                                    str(_wf_state["proc"].pid)],
                                   creationflags=CREATE_NO_WINDOW)
                else: _wf_state["proc"].kill()
            except Exception: pass
            _wf_state["proc"] = None
        _wf_state["play_offset"] = _wf_state["position"]
        try: btn_wf_play.configure(text="▶ Play")
        except Exception: pass

    def _wf_toggle():
        if _wf_state["playing"]: _wf_stop()
        else: _wf_play()

    def _wf_tick():
        if not _wf_state["playing"]:
            _wf_state["tick_id"] = None
            return
        pos = _wf_state["play_offset"] + (time.time() - _wf_state["wall_start"])
        dur = _wf_state["duration"]
        pos = min(pos, dur) if dur > 0 else pos
        _wf_state["position"] = pos
        try: sync_pos_var.set(f"{pos:.3f}s")
        except Exception: pass
        _wf_redraw()
        if _wf_state["playing"]:
            _wf_state["tick_id"] = root.after(40, _wf_tick)
        else:
            _wf_state["tick_id"] = None

    # ── THE ONE BUTTON: mark + set lead-in in one click ──────────────────
    def _mark_and_set():
        t = round(_wf_state["position"], 3)
        _wf_state["marked"] = t
        vars_["leadin"].set(str(t))
        _wf_redraw()
        status_var.set(
            f"✓ Lead-In set to {t}s — now click  Rebuild CDLC  below.")
        log(f"  [sync] Lead-In set to {t}s")

    def _wf_nudge(delta):
        mk = _wf_state.get("marked")
        if mk is not None:
            new_mk = round(max(0.0, mk + delta), 3)
            _wf_state["marked"] = new_mk
            vars_["leadin"].set(str(new_mk))
            try: sync_pos_var.set(f"{new_mk:.3f}s (mark)")
            except Exception: pass
            _wf_redraw()
        else:
            # nudge leadin directly if no mark set yet
            try:
                cur = float(vars_["leadin"].get() or 0)
                vars_["leadin"].set(str(round(max(0.0, cur + delta), 3)))
            except ValueError: pass

    # ── RIGHT: track selector ─────────────────────────────────────────────
    track_panel = styled(tk.Frame(sync_body, width=200, highlightthickness=1),
                         bg="card", highlightbackground="border")
    track_panel.grid(row=1, column=1, sticky="nsew", padx=(10,0), pady=(0,10))
    track_panel.pack_propagate(False)

    styled(tk.Label(track_panel, text="GP TRACKS",
                    font=("Segoe UI Semibold", 8)),
           fg="muted", bg="card").pack(anchor="w", padx=12, pady=(10,4))
    styled(tk.Frame(track_panel, height=1), bg="border").pack(fill="x")
    styled(tk.Label(track_panel,
                    text="Select which track\nthe Lead-In applies to",
                    font=("Segoe UI", 8), justify="center"),
           fg="dim", bg="card").pack(pady=(6,4))

    _active_track_var = tk.StringVar(value="")
    _track_btn_refs = []

    def _build_track_panel():
        for w in list(track_panel.winfo_children())[3:]:
            w.destroy()
        _track_btn_refs.clear()
        if not track_rows:
            styled(tk.Label(track_panel, text="Load a GP file\non Main page",
                            font=("Segoe UI", 8), justify="center"),
                   fg="dim", bg="card").pack(expand=True)
            return
        for row in track_rows:
            arr = row["var_arr"].get()
            name = arr
            try:
                idx = track_rows.index(row)
                lbl_w = arr_grid.grid_slaves(row=idx+1, column=1)
                if lbl_w: name = lbl_w[0].cget("text").split("  (")[0].strip()
            except Exception: pass
            label = f"{arr}\n{name}"
            btn = tk.Button(track_panel, text=label,
                            font=("Segoe UI", 8), cursor="hand2",
                            relief="flat", bd=0, anchor="w",
                            padx=12, pady=7, wraplength=175, justify="left")
            def _sel(b=btn, v=f"{arr} — {name}"):
                _active_track_var.set(v)
                for bw, _ in _track_btn_refs:
                    bw.configure(bg=COL["card"], fg=COL["muted"])
                b.configure(bg=COL["accent"], fg="#ffffff")
            btn.configure(command=_sel, bg=COL["card"], fg=COL["muted"],
                          activebackground=COL["card2"])
            btn.pack(fill="x")
            styled(tk.Frame(track_panel, height=1), bg="border").pack(fill="x")
            _track_btn_refs.append((btn, label))
        if _track_btn_refs:
            _track_btn_refs[0][0].invoke()

    # ── BPM FINE-TUNE (live beat grid update) ────────────────────────────
    bpm_tune_frame = styled(tk.Frame(sync_body), bg="surface")
    bpm_tune_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0,4))

    # Status label spans full width above buttons
    status_var = tk.StringVar(value="Click  Load Waveform  to start.")
    styled(tk.Label(bpm_tune_frame, textvariable=status_var,
                    font=("Segoe UI", 9), anchor="w"),
           fg="accent", bg="surface").pack(fill="x", pady=(0,6))

    bpm_ctrl_row = styled(tk.Frame(bpm_tune_frame), bg="surface")
    bpm_ctrl_row.pack(fill="x", pady=(0,4))

    styled(tk.Label(bpm_ctrl_row, text="BPM  (tweak until grid lines snap to transients):",
                    font=("Segoe UI", 8)),
           fg="muted", bg="surface").pack(side="left", padx=(0,8))

    def _bpm_nudge(delta):
        try:
            cur = float(vars_["bpm"].get() or 120)
            vars_["bpm"].set(f"{max(20.0, min(400.0, round(cur + delta, 3)))}")
            _wf_redraw()
        except ValueError:
            pass

    for lbl, d in [("−1",  -1.0), ("−0.1", -0.1), ("−0.01", -0.01),
                   ("+0.01", 0.01), ("+0.1",  0.1), ("+1",    1.0)]:
        fc = COL["warn"] if d < 0 else COL["ok"]
        tk.Button(bpm_ctrl_row, text=lbl, font=("Segoe UI", 8), cursor="hand2",
                  command=lambda dv=d: _bpm_nudge(dv),
                  relief="flat", bd=0, padx=10, pady=4,
                  bg=COL["card2"], fg=fc,
                  activebackground=COL["border_hi"]).pack(side="left", padx=(0,3))

    bpm_ef = styled(tk.Frame(bpm_ctrl_row, highlightthickness=1),
                    bg="field", highlightbackground="border")
    bpm_ef.pack(side="left", padx=(8, 4), ipady=1)
    bpm_entry_sync = styled(tk.Entry(bpm_ef, textvariable=vars_["bpm"],
                                     font=("Consolas", 10), relief="flat",
                                     bd=3, highlightthickness=0, width=7),
                            bg="field", fg="fg", insertbackground="accent_hi")
    bpm_entry_sync.pack()
    bpm_entry_sync.bind("<Return>", lambda e: _wf_redraw())
    bpm_entry_sync.bind("<FocusOut>", lambda e: _wf_redraw())
    bpm_entry_sync.bind("<FocusIn>",
        lambda e: bpm_ef.config(highlightbackground=COL["accent"]))
    bpm_entry_sync.bind("<FocusOut>",
        lambda e: (bpm_ef.config(highlightbackground=COL["border"]), _wf_redraw()))

    styled(tk.Label(bpm_ctrl_row, text="BPM", font=("Segoe UI", 8)),
           fg="dim", bg="surface").pack(side="left", padx=(2, 16))

    styled(tk.Label(bpm_ctrl_row,
                    text="Grid = bars  |  zoom ≤ 8s shows beats",
                    font=("Segoe UI", 7)),
           fg="dim", bg="surface").pack(side="left")

    # ── TRANSPORT BUTTONS ─────────────────────────────────────────────────
    transport_frame = styled(tk.Frame(sync_body), bg="surface")
    transport_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0,4))

    btn_row = styled(tk.Frame(transport_frame), bg="surface")
    btn_row.pack(fill="x")

    btn_load_wf = tk.Button(btn_row, text="Load Waveform",
                            font=("Segoe UI Semibold", 9), cursor="hand2",
                            command=_load_waveform, relief="flat", bd=0, padx=14, pady=8)
    styled(btn_load_wf, bg="card2", fg="fg", activebackground="border_hi")
    btn_load_wf.pack(side="left", padx=(0,6))

    btn_wf_play = tk.Button(btn_row, text="▶ Play",
                            font=("Segoe UI Semibold", 9), cursor="hand2",
                            command=_wf_toggle, relief="flat", bd=0,
                            padx=18, pady=8, fg="white")
    styled(btn_wf_play, bg="accent", activebackground="accent_hi")
    btn_wf_play.pack(side="left", padx=(0,4))

    tk.Button(btn_row, text="⏹", font=("Segoe UI", 10), cursor="hand2",
              command=_wf_stop, relief="flat", bd=0, padx=10, pady=8,
              bg=COL["card2"], fg=COL["warn"],
              activebackground=COL["border_hi"]).pack(side="left", padx=(0,16))

    btn_mark = tk.Button(btn_row, text="🎯  Mark & Set Lead-In",
                         font=("Segoe UI Semibold", 10), cursor="hand2",
                         command=_mark_and_set, relief="flat", bd=0,
                         padx=20, pady=8, fg="white")
    btn_mark.configure(bg=COL["ok"], activebackground=COL["ok"])
    btn_mark.pack(side="left", padx=(0,20))

    # position readout
    sync_pos_var = tk.StringVar(value="0.000s")
    styled(tk.Label(btn_row, textvariable=sync_pos_var,
                    font=("Consolas", 13, "bold")),
           fg="fg", bg="surface").pack(side="left")

    # ── GP OVERLAY + STRETCH ROW ──────────────────────────────────────────
    gp_row = styled(tk.Frame(transport_frame), bg="surface")
    gp_row.pack(fill="x", pady=(6, 0))

    _note_overlay_var = tk.BooleanVar(value=True)

    def _load_gp_notes():
        gp_path = vars_["gp"].get().strip()
        if not gp_path or not os.path.exists(gp_path):
            return messagebox.showwarning("No GP file", "Load a Guitar Pro file on the Main page first.")
        # pick which track index to extract (use the first included Lead track)
        track_idx = 0
        for row in track_rows:
            if row["var_include"].get() and row["var_arr"].get() in ("Lead", "Rhythm"):
                track_idx = row["index"]
                break
        btn_gp_notes.configure(text="Extracting…", state="disabled")
        status_var.set("Reading note positions from GP file…")
        def _worker():
            onsets = extract_gp_note_onsets(gp_path, track_idx)
            _wf_state["note_onsets"] = onsets
            root.after(0, _wf_redraw)
            root.after(0, lambda: btn_gp_notes.configure(
                text=f"↺ GP Notes  ({len(onsets)} events)", state="normal"))
            root.after(0, lambda: status_var.set(
                f"✓ {len(onsets)} note events loaded — cyan ticks show tab positions on waveform."))
            log(f"  [notes] {len(onsets)} note onsets extracted from GP (track {track_idx})")
        threading.Thread(target=_worker, daemon=True).start()

    btn_gp_notes = tk.Button(gp_row, text="📄  Load GP Notes onto Waveform",
                             font=("Segoe UI", 9), cursor="hand2",
                             command=_load_gp_notes, relief="flat", bd=0, padx=12, pady=6)
    styled(btn_gp_notes, bg="card2", fg="fg", activebackground="border_hi")
    btn_gp_notes.pack(side="left", padx=(0, 6))

    def _toggle_note_overlay():
        _wf_state["show_notes"] = _note_overlay_var.get()
        _wf_redraw()

    ttk.Checkbutton(gp_row, text="Show tab markers",
                    variable=_note_overlay_var,
                    command=_toggle_note_overlay).pack(side="left", padx=(0, 20))

    def _do_stretch():
        audio_path = vars_["audio"].get().strip()
        gp_path    = vars_["gp"].get().strip()
        if not audio_path or not os.path.exists(audio_path):
            return messagebox.showwarning("No Audio", "Set an audio file on the Main page first.")
        if not gp_path or not os.path.exists(gp_path):
            return messagebox.showwarning("No GP file", "Load a Guitar Pro file first.")
        out_dir = vars_["out"].get().strip() or os.path.dirname(os.path.abspath(audio_path))
        base    = os.path.splitext(os.path.basename(audio_path))[0]
        out_path = os.path.join(out_dir, base + "_stretched.wav")

        btn_stretch.configure(text="Stretching…", state="disabled")
        status_var.set("Stretching audio to match GP tempo map…")

        def _worker():
            try:
                tempo_events, tpb = extract_gp_tempo_map(gp_path)
                sec_events = tempo_map_to_seconds(tempo_events, tpb)
                dur = _wf_state["duration"] or _get_dur(audio_path)
                stretch_audio_to_gp(audio_path, out_path, sec_events, dur, log)
                # Load the stretched file as the new audio and reload waveform
                root.after(0, lambda: vars_["audio"].set(out_path))
                root.after(100, _load_waveform)
                root.after(0, lambda: btn_stretch.configure(
                    text="✓ Stretched — waveform reloading…", state="normal"))
                root.after(0, lambda: status_var.set(
                    f"✓ Stretched audio saved: {os.path.basename(out_path)}  — waveform reloading."))
                log(f"  [stretch] Done → {out_path}")
            except Exception as e:
                log(f"  [stretch] Error: {e}")
                root.after(0, lambda: status_var.set(f"✗ Stretch failed: {e}"))
                root.after(0, lambda: btn_stretch.configure(
                    text="⟳ Stretch Audio to GP", state="normal"))
        threading.Thread(target=_worker, daemon=True).start()

    btn_stretch = tk.Button(gp_row, text="⟳  Stretch Audio to GP",
                            font=("Segoe UI Semibold", 9), cursor="hand2",
                            command=_do_stretch, relief="flat", bd=0, padx=14, pady=6,
                            fg="white")
    styled(btn_stretch, bg="accent", activebackground="accent_hi")
    btn_stretch.pack(side="left", padx=(0, 8))

    styled(tk.Label(gp_row,
                    text="Warps audio duration to match GP tempo — saves as _stretched.wav",
                    font=("Segoe UI", 7)),
           fg="dim", bg="surface").pack(side="left")

    # ── NUDGE + LEAD-IN + REBUILD ─────────────────────────────────────────
    bottom_frame = styled(tk.Frame(sync_body), bg="surface")
    bottom_frame.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0,4))

    styled(tk.Label(bottom_frame,
                    text="Fine nudge  (if it's still slightly off after rebuilding):",
                    font=("Segoe UI", 8)),
           fg="muted", bg="surface").pack(side="left", padx=(0,8))

    for lbl, d in [("−0.5s",-0.5),("−0.1s",-0.1),("−0.01s",-0.01),
                   ("+0.01s",0.01),("+0.1s",0.1),("+0.5s",0.5)]:
        fc = COL["warn"] if d < 0 else COL["ok"]
        tk.Button(bottom_frame, text=lbl, font=("Segoe UI", 8), cursor="hand2",
                  command=lambda dv=d: _wf_nudge(dv),
                  relief="flat", bd=0, padx=10, pady=5,
                  bg=COL["card2"], fg=fc,
                  activebackground=COL["border_hi"]).pack(side="left", padx=(0,3))

    rebuild_frame = styled(tk.Frame(sync_body), bg="surface")
    rebuild_frame.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(8,0))

    li_box = styled(tk.Frame(rebuild_frame), bg="card")
    li_box.pack(side="left", padx=(0,16))
    styled(tk.Label(li_box, text="LEAD-IN", font=("Segoe UI", 7)),
           fg="muted", bg="card").pack(anchor="w", padx=10, pady=(6,0))
    styled(tk.Label(li_box, textvariable=vars_["leadin"],
                    font=("Consolas", 20, "bold")),
           fg="accent", bg="card").pack(anchor="w", padx=10)
    styled(tk.Label(li_box, text="seconds", font=("Segoe UI", 7)),
           fg="dim", bg="card").pack(anchor="w", padx=10, pady=(0,6))

    btn_rebuild = tk.Button(rebuild_frame,
                            text="▶  Rebuild CDLC with current Lead-In",
                            font=("Segoe UI Semibold", 11), cursor="hand2",
                            command=lambda: do_build(),
                            relief="flat", bd=0, padx=22, pady=12, fg="white")
    styled(btn_rebuild, bg="accent", activebackground="accent_hi")
    btn_rebuild.pack(side="left", anchor="center")

    # ── SLOPSMITH (collapsed by default) ─────────────────────────────────
    slop_collapsed = [True]
    slop_detail = styled(tk.Frame(sync_body), bg="surface")

    # FIX: define function first, then create the button that references it
    def _toggle_slop_detail():
        if slop_collapsed[0]:
            slop_detail.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(4,0))
            btn_slop_toggle.configure(text="▲ Slopsmith (advanced)")
            slop_collapsed[0] = False
        else:
            slop_detail.grid_remove()
            btn_slop_toggle.configure(text="▼ Slopsmith (advanced)")
            slop_collapsed[0] = True

    btn_slop_toggle = tk.Button(sync_body,
                                text="▼ Slopsmith (advanced)",
                                font=("Segoe UI", 8), cursor="hand2",
                                command=_toggle_slop_detail,
                                relief="flat", bd=0, padx=0, pady=4)
    styled(btn_slop_toggle, bg="surface", fg="dim", activebackground="surface")
    btn_slop_toggle.grid(row=6, column=0, columnspan=2, sticky="w", pady=(10,0))

    # Slopsmith detail content (hidden by default)
    slop_status_var = tk.StringVar(value="")
    slop_inner = styled(tk.Frame(slop_detail), bg="card"); slop_inner.pack(fill="x", pady=4)

    # ── Row 1: description ────────────────────────────────────────────────
    styled(tk.Label(slop_inner,
                    text="Slopsmith Desktop: browse for the .exe to launch it directly, "
                         "or use the Docker URL below to open it in your browser.",
                    font=("Segoe UI", 8), wraplength=800, justify="left"),
           fg="muted", bg="card").pack(anchor="w", padx=12, pady=(8,4))

    # ── Row 2: exe path + Launch button ──────────────────────────────────
    slop_exe_row = styled(tk.Frame(slop_inner), bg="card")
    slop_exe_row.pack(anchor="w", padx=12, fill="x", pady=(0,6))

    styled(tk.Label(slop_exe_row, text="EXE:", font=("Segoe UI", 8)),
           fg="muted", bg="card").pack(side="left")

    exe_ef = styled(tk.Frame(slop_exe_row, highlightthickness=1),
                    bg="field", highlightbackground="border")
    exe_ef.pack(side="left", padx=(6,4), ipady=1)
    styled(tk.Entry(exe_ef, textvariable=vars_["slop_exe"], font=("Segoe UI", 9),
                    relief="flat", bd=3, highlightthickness=0, width=38),
           bg="field", fg="fg", insertbackground="accent_hi").pack(side="left")

    def _browse_slop_exe():
        p = filedialog.askopenfilename(
            title="Select Slopsmith Desktop executable",
            filetypes=[("Executable", "*.exe"), ("All files", "*.*")])
        if p:
            vars_["slop_exe"].set(p)

    tk.Button(slop_exe_row, text="⋯", font=("Segoe UI", 9), cursor="hand2",
              command=_browse_slop_exe, relief="flat", bd=0, padx=8, pady=4,
              bg=COL["card2"], fg=COL["fg"],
              activebackground=COL["border_hi"]).pack(side="left", padx=(0,8))

    slop_launch_status_var = tk.StringVar(value="")

    def _launch_slop_exe():
        exe = vars_["slop_exe"].get().strip()
        if not exe:
            slop_launch_status_var.set("No exe selected — browse above or wait for auto-detect.")
            return
        if not os.path.isfile(exe):
            slop_launch_status_var.set(f"File not found: {exe}")
            return
        try:
            subprocess.Popen([exe], creationflags=CREATE_NO_WINDOW)
            slop_launch_status_var.set("✓ Launched Slopsmith Desktop")
            log(f"  [slopsmith] Launched: {exe}")
        except Exception as ex:
            slop_launch_status_var.set(f"✗ Launch failed: {ex}")
            log(f"  [slopsmith] Launch error: {ex}")

    tk.Button(slop_exe_row, text="▶  Launch Slopsmith Desktop",
              font=("Segoe UI Semibold", 9), cursor="hand2",
              command=_launch_slop_exe, relief="flat", bd=0,
              padx=14, pady=6, fg="white",
              bg=COL["accent"], activebackground=COL["accent_hi"]).pack(side="left")

    styled(tk.Label(slop_exe_row, textvariable=slop_launch_status_var,
                    font=("Segoe UI", 8)),
           fg="ok", bg="card").pack(side="left", padx=(10,0))

    # ── Row 3: divider ────────────────────────────────────────────────────
    styled(tk.Frame(slop_inner, height=1), bg="border").pack(fill="x", padx=12, pady=(4,6))

    # ── Row 4: Docker URL row ─────────────────────────────────────────────
    slop_url_row = styled(tk.Frame(slop_inner), bg="card"); slop_url_row.pack(anchor="w", padx=12, pady=(0,8))
    styled(tk.Label(slop_url_row, text="Docker URL:", font=("Segoe UI", 8)), fg="muted", bg="card").pack(side="left")
    url_ef2 = styled(tk.Frame(slop_url_row, highlightthickness=1),
                     bg="field", highlightbackground="border")
    url_ef2.pack(side="left", padx=(6,6), ipady=1)
    styled(tk.Entry(url_ef2, textvariable=vars_["slop_url"], font=("Segoe UI", 9),
                    relief="flat", bd=3, highlightthickness=0, width=24),
           bg="field", fg="fg", insertbackground="accent_hi").pack()

    def _ping_slop():
        url = vars_["slop_url"].get().rstrip("/")
        slop_status_var.set("Connecting…")
        def _w():
            try:
                req = urllib.request.Request(f"{url}/api/songs?limit=1",
                    headers={"Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=4) as r: r.read()
                root.after(0, lambda: slop_status_var.set("● Running"))
            except Exception:
                root.after(0, lambda: slop_status_var.set("○ Not reachable"))
        threading.Thread(target=_w, daemon=True).start()

    def _open_slop():
        import webbrowser
        url = vars_["slop_url"].get().strip() or "http://localhost:8000"
        webbrowser.open(url)

    tk.Button(slop_url_row, text="Test", font=("Segoe UI", 8), cursor="hand2",
              command=_ping_slop, relief="flat", bd=0, padx=8, pady=4,
              bg=COL["card2"], fg=COL["fg"],
              activebackground=COL["border_hi"]).pack(side="left", padx=(0,4))
    tk.Button(slop_url_row, text="Open in Browser ↗", font=("Segoe UI", 8),
              cursor="hand2", command=_open_slop, relief="flat", bd=0,
              padx=10, pady=4, fg="white",
              bg=COL["accent"], activebackground=COL["accent_hi"]).pack(side="left")
    styled(tk.Label(slop_url_row, textvariable=slop_status_var, font=("Segoe UI", 8)),
           fg="ok", bg="card").pack(side="left", padx=(10,0))

    # PAGE: LOG
    # ══════════════════════════════════════════════════════════════════════
    log_page = styled(tk.Frame(page_container), bg="surface"); pages["log"] = log_page
    page_header(log_page, "Event Log")
    log_content = styled(tk.Frame(log_page), bg="surface"); log_content.pack(fill="both", expand=True, padx=40, pady=28)
    log_text = styled(tk.Text(log_content, wrap="word", relief="flat", padx=16, pady=16, state="disabled", font=("Consolas", 9)), bg="field", fg="muted", insertbackground="accent_hi")
    log_text.pack(fill="both", expand=True, pady=(0, 12))

    def log(msg):
        log_text.configure(state="normal"); log_text.insert("end", str(msg) + "\n"); log_text.see("end"); log_text.configure(state="disabled")
        root.update_idletasks()

    # ══════════════════════════════════════════════════════════════════════
    # PAGE: THEME
    # ══════════════════════════════════════════════════════════════════════
    theme_page = styled(tk.Frame(page_container), bg="surface"); pages["theme"] = theme_page
    page_header(theme_page, "Theme & Appearance")
    th_content = styled(tk.Frame(theme_page), bg="surface"); th_content.pack(fill="both", expand=True, padx=40, pady=28)

    styled(tk.Label(th_content, text="Accent Color", font=("Segoe UI Semibold", 13)), fg="fg", bg="surface").pack(anchor="w", pady=(0, 4))
    preset_circles_row = styled(tk.Frame(th_content), bg="surface"); preset_circles_row.pack(fill="x", pady=(10, 20))

    _RADIAL_PRESETS = [
        ("#e8429a", "#ff6bbf", "Pink"), ("#e85c2a", "#ff7a47", "Orange"),
        ("#d8a13a", "#f5c869", "Gold"), ("#1db954", "#1ed760", "Green"),
        ("#2ab6c9", "#5be0f0", "Cyan"), ("#3a7fd8", "#6aa3ff", "Blue"),
        ("#8e5cf0", "#b48bff", "Violet"), ("#e8423f", "#ff6b66", "Red")
    ]

    for base, hi, name in _RADIAL_PRESETS:
        col_frame = styled(tk.Frame(preset_circles_row, cursor="hand2"), bg="surface"); col_frame.pack(side="left", padx=(0, 12))
        circle = tk.Label(col_frame, text="", bg=base, width=3, height=1, highlightthickness=3, highlightbackground=hi if base.lower() == COL["accent"].lower() else COL["border"]); circle.pack()
        nm_lbl = styled(tk.Label(col_frame, text=name, font=("Segoe UI", 8)), fg="muted", bg="surface"); nm_lbl.pack(pady=(4, 0))
        def _preset_click(b=base, h=hi, n=name, c=circle, l=nm_lbl):
            save_settings({"accent": b, "accent_hi": h}); COL["accent"] = b; COL["accent_hi"] = h; COL["accent_glow"] = b + "33"
            refresh_theme(root, ttk)
            for ch in preset_circles_row.winfo_children():
                try: ch.winfo_children()[0].configure(highlightbackground=COL["border"])
                except Exception: pass
            c.configure(highlightbackground=h)
            log(f"Theme Accent updated to {n}.")
        for w in (col_frame, circle, nm_lbl): w.bind("<Button-1>", lambda e, fn=_preset_click: fn())

    def _open_color_picker():
        result = colorchooser.askcolor(color=COL["accent"], title="Choose Accent Color", parent=root)
        if result and result[1]:
            picked = result[1].upper()
            r = int(picked[1:3], 16); g = int(picked[3:5], 16); b = int(picked[5:7], 16)
            hi_r = min(255, int(r + (255 - r) * 0.25)); hi_g = min(255, int(g + (255 - g) * 0.25)); hi_b = min(255, int(b + (255 - b) * 0.25))
            hi = f"#{hi_r:02X}{hi_g:02X}{hi_b:02X}"
            save_settings({"accent": picked, "accent_hi": hi}); COL["accent"] = picked; COL["accent_hi"] = hi; COL["accent_glow"] = picked + "33"
            refresh_theme(root, ttk)
            for ch in preset_circles_row.winfo_children():
                try: ch.winfo_children()[0].configure(highlightbackground=COL["border"])
                except Exception: pass
            log(f"Theme Accent updated to Custom ({picked}).")

    btn_custom_col = tk.Button(th_content, text="⊕  Open Custom Color Picker…", relief="flat", bd=0, font=("Segoe UI", 10), cursor="hand2", padx=16, pady=8, command=_open_color_picker)
    styled(btn_custom_col, bg="card2", fg="fg"); btn_custom_col.pack(anchor="w", pady=(0, 20))

    styled(tk.Frame(th_content, height=1), bg="border").pack(fill="x", pady=(10, 20))
    styled(tk.Label(th_content, text="Mode", font=("Segoe UI Semibold", 13)), fg="fg", bg="surface").pack(anchor="w", pady=(0, 4))
    mode_row = styled(tk.Frame(th_content), bg="surface"); mode_row.pack(fill="x", pady=(10, 8))

    def _make_mode_btn(parent, label, is_light, side):
        f = styled(tk.Frame(parent, highlightthickness=2, cursor="hand2"), bg="card", highlightbackground="accent" if bool(_saved.get("light_mode")) == is_light else "border")
        f.pack(side=side, padx=(0 if side == "left" else 12, 0))
        tk.Label(f, text=label, font=("Segoe UI Semibold", 10), bg="#e5e5e5" if is_light else "#0f1117", fg="#121212" if is_light else "#ffffff", width=12, pady=10).pack(padx=2, pady=2)
        def _select(e=None):
            save_settings({"light_mode": is_light}); COL.update(LIGHT_MODE_COL if is_light else DARK_MODE_COL)
            refresh_theme(root, ttk)
            for child in mode_row.winfo_children():
                try: child.configure(highlightbackground=COL["border"])
                except: pass
            f.configure(highlightbackground=COL["accent"])
            log(f"Theme Mode updated to {label}.")
        f.bind("<Button-1>", _select); f.winfo_children()[0].bind("<Button-1>", _select)
        return f
    _make_mode_btn(mode_row, "Dark Mode", False, "left"); _make_mode_btn(mode_row, "Light Mode", True, "left")

    # ══════════════════════════════════════════════════════════════════════
    # MEDIA INSPECTOR
    # ══════════════════════════════════════════════════════════════════════
    def _show_media_inspector():
        artist = vars_["artist"].get().strip()
        title  = vars_["title"].get().strip()
        out_dir = vars_["out"].get()
        audio_url = vars_["audio_url"].get().strip()

        if not out_dir:
            return messagebox.showerror("Missing Folder", "Please select an Output Folder first.")
        if not audio_url and (not artist or not title):
            return messagebox.showwarning("Missing Info", "Provide an Audio URL, or load a GP file to auto-search by Artist & Title.")

        # Realize root geometry before reading coords
        root.update_idletasks()
        rx = root.winfo_rootx(); ry = root.winfo_rooty()
        rw = root.winfo_width();  rh = root.winfo_height()

        # Semi-transparent black backdrop (sits behind the modal)
        backdrop = tk.Toplevel(root)
        backdrop.overrideredirect(True)
        backdrop.geometry(f"{rw}x{rh}+{rx}+{ry}")
        backdrop.configure(bg="#000000")
        backdrop.attributes("-alpha", 0.72)
        backdrop.transient(root)
        backdrop.lift()

        # Main modal dialog
        modal = tk.Toplevel(root)
        modal.transient(root)
        modal.configure(bg=COL["card"])
        modal.resizable(False, False)
        w, h = 840, 480
        x = rx + max(0, (rw - w) // 2)
        y = ry + max(0, (rh - h) // 2)
        modal.geometry(f"{w}x{h}+{x}+{y}")
        modal.lift()
        modal.focus_set()
        modal.grab_set()

        modal.title("Media Inspector")
        m_head = tk.Frame(modal, bg=COL["card2"]); m_head.pack(fill="x")
        tk.Label(m_head, text="🎬 Media Inspector", font=("Segoe UI Semibold", 13), fg=COL["fg"], bg=COL["card2"]).pack(side="left", padx=24, pady=16)

        modal_state = {"process": None, "video_url": None, "yt_meta": {}}

        def _close(_e=None):
            if modal_state["process"]:
                try:
                    if sys.platform == "win32":
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(modal_state["process"].pid)], creationflags=CREATE_NO_WINDOW)
                    else:
                        modal_state["process"].kill()
                except Exception: pass
            try: modal.grab_release()
            except Exception: pass
            try: modal.destroy()
            except Exception: pass
            try: backdrop.destroy()
            except Exception: pass

        # Escape key closes the inspector
        modal.bind("<Escape>", _close)

        btn_close = tk.Button(m_head, text="✕", font=("Segoe UI", 14), cursor="hand2", command=_close,
                              relief="flat", bd=0, bg=COL["card2"], fg=COL["muted"],
                              activebackground=COL["card2"], activeforeground=COL["fg"])
        btn_close.pack(side="right", padx=20)
        modal.protocol("WM_DELETE_WINDOW", _close)

        m_body = tk.Frame(modal, bg=COL["card"]); m_body.pack(fill="both", expand=True)
        m_body.pack_propagate(False)

        loading_frame = tk.Frame(m_body, bg=COL["card"]); loading_frame.pack(expand=True)
        loading_lbl = tk.Label(loading_frame, text="Searching for best match...", font=("Segoe UI", 11), fg=COL["muted"], bg=COL["card"])
        loading_lbl.pack()

        def fetch_info():
            ytdlp = _find_exe("yt-dlp")
            if not ytdlp:
                root.after(0, lambda: loading_lbl.configure(text="✗ yt-dlp not found. Run Auto-Fetch first.", fg=COL["warn"]))
                return
            target_url = audio_url
            if not target_url:
                root.after(0, lambda: loading_lbl.configure(text="Scraping YouTube..."))
                cl_title = clean_title_for_api(title)
                cl_artist = clean_title_for_api(artist)
                target_url = f"ytsearch1:{cl_artist} {cl_title} official audio"
            try:
                root.after(0, lambda: loading_lbl.configure(text="Extracting metadata..."))
                cmd = [ytdlp, "--dump-json", "--no-warnings", "--no-playlist", target_url]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=30, creationflags=CREATE_NO_WINDOW)
                lines = [l for l in res.stdout.split('\n') if l.strip().startswith('{')]
                if not lines:
                    raise Exception("No video data found. Try pasting a direct YouTube URL.")
                vdata = json.loads(lines[0])
                v_title = vdata.get("title", "Unknown Title")
                v_chan  = vdata.get("uploader", "Unknown Channel")
                v_dur   = vdata.get("duration_string", "0:00")
                # Pick highest-res thumbnail
                thumbs = vdata.get("thumbnails", [])
                v_thumb = thumbs[-1].get("url","") if thumbs else vdata.get("thumbnail","")
                modal_state["video_url"] = vdata.get("webpage_url", target_url)

                # Extract music metadata embedded by yt-dlp (YouTube Music tracks)
                yt_album  = vdata.get("album", "") or ""
                yt_artist = vdata.get("artist","") or vdata.get("uploader","") or ""
                yt_track  = vdata.get("track","") or ""
                yt_year_raw = vdata.get("release_year","") or ""
                if not yt_year_raw and vdata.get("release_date"):
                    yt_year_raw = str(vdata["release_date"])[:4]
                yt_year = str(yt_year_raw)[:4]

                # If no album from yt-dlp, try parsing description
                if not yt_album:
                    desc = vdata.get("description", "") or ""
                    m_alb = re.search(r"(?:album|from)[:\s]+([^\n\r]{3,60})", desc, re.IGNORECASE)
                    if m_alb:
                        yt_album = re.sub(r'[\'"]+', '', m_alb.group(1)).strip()

                modal_state["yt_meta"] = {
                    "album": yt_album.strip(),
                    "artist": yt_artist.strip(),
                    "track": yt_track.strip(),
                    "year": yt_year,
                }
                root.after(0, lambda: _build_inspector_ui(v_title, v_chan, v_dur, v_thumb))
            except Exception as e:
                root.after(0, lambda: loading_lbl.configure(text=f"✗ {e}", fg=COL["warn"]))

        def _build_inspector_ui(v_title, v_chan, v_dur, v_thumb):
            loading_frame.pack_forget()
            content_frame = tk.Frame(m_body, bg=COL["card"])
            content_frame.pack(fill="both", expand=True, padx=30, pady=30)

            left_frame = tk.Frame(content_frame, bg=COL["card"]); left_frame.pack(side="left", fill="y", padx=(0, 30))

            vid_frame = tk.Frame(left_frame, highlightthickness=1, bg=COL["field"], highlightbackground=COL["border"], width=320, height=180)
            vid_frame.pack(anchor="n"); vid_frame.pack_propagate(False)

            thumb_lbl = tk.Label(vid_frame, text="Loading Image...", font=("Segoe UI", 10), bg=COL["field"], fg=COL["dim"])
            thumb_lbl.pack(expand=True, fill="both")

            if v_thumb and HAS_PIL:
                def _dl_thumb():
                    try:
                        req = urllib.request.Request(v_thumb, headers={"User-Agent": "Mozilla/5.0"})
                        with urllib.request.urlopen(req, timeout=10) as r: raw = r.read()
                        img = Image.open(io.BytesIO(raw))
                        iw, ih = img.size; tr = 16/9; cr = iw/ih
                        if cr > tr:
                            nw = int(tr * ih); lft = (iw - nw)//2; img = img.crop((lft, 0, lft+nw, ih))
                        elif cr < tr:
                            nh = int(iw / tr); top = (ih - nh)//2; img = img.crop((0, top, iw, top+nh))
                        img = img.resize((320, 180), Image.Resampling.LANCZOS)
                        def apply_img():
                            photo = ImageTk.PhotoImage(img)
                            thumb_lbl.configure(image=photo, text=""); thumb_lbl.image = photo
                            main_vid_state["thumb_obj"] = photo
                            if main_thumb_lbl:
                                main_thumb_lbl.configure(image=photo, text="")
                            main_vid_play_btn.configure(state="normal", bg=COL["card2"], fg=COL["fg"])
                            # Save thumbnail as cover art if no art is set yet
                            if not vars_["art"].get().strip() and out_dir:
                                try:
                                    import io as _io
                                    thumb_art = os.path.join(out_dir, "cover_yt.jpg")
                                    img.save(thumb_art, "JPEG", quality=90)
                                    vars_["art"].set(thumb_art)
                                    log(f"  [art] saved YouTube thumbnail as cover art")
                                except Exception: pass
                        root.after(0, apply_img)
                    except Exception:
                        root.after(0, lambda: thumb_lbl.configure(text="No Image Available"))
                threading.Thread(target=_dl_thumb, daemon=True).start()

            right_frame = tk.Frame(content_frame, bg=COL["card"]); right_frame.pack(side="left", fill="both", expand=True)

            tk.Label(right_frame, text="BEST MATCH FOUND", font=("Segoe UI Semibold", 9), fg=COL["accent"], bg=COL["card"]).pack(anchor="w")
            tk.Label(right_frame, text=v_title, font=("Segoe UI Semibold", 16), wraplength=340, justify="left", fg=COL["fg"], bg=COL["card"]).pack(anchor="w", pady=(4, 16))

            meta_f = tk.Frame(right_frame, bg=COL["card"]); meta_f.pack(fill="x", anchor="w")
            tk.Label(meta_f, text="Channel: ", font=("Segoe UI Semibold", 11), fg=COL["dim"], bg=COL["card"]).grid(row=0, column=0, sticky="w")
            tk.Label(meta_f, text=v_chan, font=("Segoe UI", 11), fg=COL["muted"], bg=COL["card"]).grid(row=0, column=1, sticky="w")
            tk.Label(meta_f, text="Duration: ", font=("Segoe UI Semibold", 11), fg=COL["dim"], bg=COL["card"]).grid(row=1, column=0, sticky="w", pady=(4, 0))
            tk.Label(meta_f, text=v_dur, font=("Segoe UI", 11), fg=COL["muted"], bg=COL["card"]).grid(row=1, column=1, sticky="w", pady=(4, 0))
            ym = modal_state.get("yt_meta", {})
            if ym.get("album"):
                tk.Label(meta_f, text="Album: ", font=("Segoe UI Semibold", 11), fg=COL["dim"], bg=COL["card"]).grid(row=2, column=0, sticky="w", pady=(4,0))
                tk.Label(meta_f, text=ym["album"], font=("Segoe UI", 11), fg=COL["ok"], bg=COL["card"]).grid(row=2, column=1, sticky="w", pady=(4,0))

            ctrl_frame = tk.Frame(right_frame, bg=COL["card"]); ctrl_frame.pack(fill="x", side="bottom")

            def _toggle_play():
                if modal_state["process"]:
                    try:
                        if sys.platform == "win32":
                            subprocess.run(["taskkill", "/F", "/T", "/PID", str(modal_state["process"].pid)], creationflags=CREATE_NO_WINDOW)
                        else: modal_state["process"].kill()
                    except: pass
                    modal_state["process"] = None
                    btn_play.configure(text="▶ Play Video Preview")
                    thumb_lbl.pack(expand=True, fill="both")
                else:
                    btn_play.configure(text="Loading stream...")
                    def _play_thread():
                        try:
                            ffplay, ytdlp2 = _find_exe("ffplay"), _find_exe("yt-dlp")
                            res = subprocess.run([ytdlp2, "-f", "best[height<=480]/best", "-g", "--no-warnings", modal_state["video_url"]], capture_output=True, text=True, timeout=20, creationflags=CREATE_NO_WINDOW)
                            stream_url = res.stdout.strip()
                            if stream_url and ffplay:
                                vid_title = f"RS_Studio_Preview_{random.randint(10000,99999)}"
                                cmd2 = [ffplay, "-window_title", vid_title, "-noborder",
                                        "-x", "320", "-y", "180", "-left", "-9999", "-top", "-9999",
                                        "-autoexit", "-volume", "50", stream_url]
                                proc = subprocess.Popen(cmd2, creationflags=CREATE_NO_WINDOW)
                                modal_state["process"] = proc
                                if sys.platform == "win32":
                                    import ctypes
                                    hwnd = 0
                                    for _ in range(80):
                                        hwnd = ctypes.windll.user32.FindWindowW(None, vid_title)
                                        if hwnd: break
                                        time.sleep(0.05)
                                    if hwnd:
                                        GWL_STYLE = -16; WS_CHILD = 0x40000000; SWP_SHOWWINDOW = 0x0040
                                        ctypes.windll.user32.SetParent(hwnd, vid_frame.winfo_id())
                                        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_STYLE, WS_CHILD)
                                        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 320, 180, SWP_SHOWWINDOW)
                                        root.after(0, lambda: thumb_lbl.pack_forget())
                                        root.after(0, lambda: btn_play.configure(text="⏹ Stop Video"))
                                    else:
                                        root.after(0, lambda: btn_play.configure(text="⏹ Stop Video"))
                                else:
                                    root.after(0, lambda: btn_play.configure(text="⏹ Stop Video"))
                                proc.wait()
                                modal_state["process"] = None
                                root.after(0, lambda: thumb_lbl.pack(expand=True, fill="both"))
                                root.after(0, lambda: btn_play.configure(text="▶ Play Video Preview"))
                        except Exception:
                            root.after(0, lambda: btn_play.configure(text="▶ Play Video Preview"))
                    threading.Thread(target=_play_thread, daemon=True).start()

            def _confirm():
                v_url = modal_state["video_url"]
                vars_["audio_url"].set(v_url)
                main_vid_state["url"] = v_url
                # Pre-fill metadata from yt-dlp if fields are blank/default
                ym = modal_state.get("yt_meta", {})
                if ym.get("album") and vars_["album"].get() in ("", "Unknown Album"):
                    vars_["album"].set(ym["album"])
                if ym.get("year") and vars_["year"].get() in ("", "2026"):
                    vars_["year"].set(ym["year"])
                if ym.get("artist") and vars_["artist"].get() in ("", "Unknown"):
                    vars_["artist"].set(ym["artist"])
                if ym.get("track") and vars_["title"].get() in ("", "Unknown"):
                    vars_["title"].set(ym["track"])
                _close()
                _trigger_automagic(v_url)

            btn_cancel = tk.Button(ctrl_frame, text="Cancel", font=("Segoe UI", 10), cursor="hand2", command=_close,
                                   relief="flat", bd=0, padx=16, pady=8, bg=COL["card2"], fg=COL["fg"], activebackground=COL["border_hi"])
            btn_cancel.pack(side="left")
            btn_confirm = tk.Button(ctrl_frame, text="✓ Download & Use Audio", font=("Segoe UI Semibold", 10),
                                    fg="white" if COL["accent"] != "#ffffff" else "black", cursor="hand2",
                                    command=_confirm, relief="flat", bd=0, padx=16, pady=10,
                                    bg=COL["accent"], activebackground=COL["accent_hi"])
            btn_confirm.pack(side="right", fill="x", expand=True, padx=(12, 0))
            btn_play = tk.Button(left_frame, text="▶ Play Video Preview", font=("Segoe UI Semibold", 10),
                                 cursor="hand2", command=_toggle_play, relief="flat", bd=0, padx=16, pady=8,
                                 bg=COL["card2"], fg=COL["fg"], activebackground=COL["border_hi"])
            btn_play.pack(fill="x", pady=(12, 0))

        threading.Thread(target=fetch_info, daemon=True).start()

    # ══════════════════════════════════════════════════════════════════════
    # SPOTIFY BOTTOM PLAYER BAR
    # ══════════════════════════════════════════════════════════════════════
    player_bar = styled(tk.Frame(root, height=90), bg="card2")
    player_bar.grid(row=1, column=0, columnspan=2, sticky="ew")
    player_bar.grid_propagate(False)

    # NOT registered with styled() — we manage its look manually so refresh_theme
    # never overwrites the image we set in _update_player_ui
    art_lbl = tk.Label(player_bar, text="🎵", font=("Segoe UI", 24),
                       bg=COL["card2"], fg=COL["muted"])
    art_lbl.place(x=16, y=14, width=62, height=62)

    np_title = styled(tk.Label(player_bar, text="No Song Loaded", font=("Segoe UI Semibold", 11)), bg="card2", fg="fg")
    np_title.place(x=92, y=24)
    np_artist = styled(tk.Label(player_bar, text="Load a GP file to begin.", font=("Segoe UI", 9)), bg="card2", fg="muted")
    np_artist.place(x=92, y=46)

    center_frame = styled(tk.Frame(player_bar), bg="card2")
    center_frame.place(relx=0.5, y=45, anchor="center")

    # Shared media process tracker — keyed by "audio" or "video"
    _media_procs = {"audio": None, "video": None}

    def _stop_all_media():
        """Kill every active ffplay process."""
        for key, proc in list(_media_procs.items()):
            if proc:
                try:
                    if sys.platform == "win32":
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                       creationflags=CREATE_NO_WINDOW)
                    else:
                        proc.kill()
                except Exception: pass
                _media_procs[key] = None
        root.after(0, lambda: btn_playfull.configure(text="▶ Play Full Audio"))
        root.after(0, lambda: btn_playprev.configure(text="▶ Play 30s Preview"))
        root.after(0, lambda: main_vid_play_btn.configure(text="▶ Play Video Preview"))

    def _play_media(kind):
        path = vars_[kind].get()
        if not path or not os.path.exists(path):
            return messagebox.showwarning("Not Found", f"Could not find {kind}. Auto-fetch or browse first.")
        ffplay = _find_exe("ffplay")
        if not ffplay:
            try:
                if sys.platform == "win32": os.startfile(path)
                else: subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])
            except Exception as e: log(f"Playback failed: {e}")
            return
        # If already playing this kind, clicking again = stop
        if _media_procs["audio"]:
            _stop_all_media()
            return
        # Stop video too — only one thing plays at a time
        _stop_all_media()
        btn_playfull.configure(text="⏹ Stop" if kind == "audio" else "▶ Play Full Audio")
        btn_playprev.configure(text="⏹ Stop" if kind == "preview_audio" else "▶ Play 30s Preview")
        def _run():
            try:
                cmd = [ffplay, "-nodisp", "-autoexit", "-volume", "80", path]
                proc = subprocess.Popen(cmd, creationflags=CREATE_NO_WINDOW)
                _media_procs["audio"] = proc
                proc.wait()
            except Exception as e:
                root.after(0, lambda: log(f"Playback failed: {e}"))
            finally:
                _media_procs["audio"] = None
                root.after(0, lambda: btn_playfull.configure(text="▶ Play Full Audio"))
                root.after(0, lambda: btn_playprev.configure(text="▶ Play 30s Preview"))
        threading.Thread(target=_run, daemon=True).start()

    btn_playfull = tk.Button(center_frame, text="▶ Play Full Audio", command=lambda: _play_media("audio"), font=("Segoe UI Semibold", 9), cursor="hand2", padx=16, pady=6, bd=0, relief="flat")
    styled(btn_playfull, bg="surface", fg="fg", activebackground="border_hi"); btn_playfull.pack(side="left", padx=6)

    btn_playprev = tk.Button(center_frame, text="▶ Play 30s Preview", command=lambda: _play_media("preview_audio"), font=("Segoe UI Semibold", 9), cursor="hand2", padx=16, pady=6, bd=0, relief="flat")
    styled(btn_playprev, bg="surface", fg="fg", activebackground="border_hi"); btn_playprev.pack(side="left", padx=6)

    btn_stop_all = tk.Button(center_frame, text="⏹", command=_stop_all_media,
                             font=("Segoe UI Semibold", 11), cursor="hand2",
                             padx=10, pady=6, bd=0, relief="flat")
    styled(btn_stop_all, bg="surface", fg="warn", activebackground="border_hi")
    btn_stop_all.pack(side="left", padx=6)

    status_lbl = styled(tk.Label(player_bar, text="Ready", font=("Segoe UI Semibold", 9)), bg="card2", fg="muted")
    status_lbl.place(relx=0.98, y=45, anchor="e")

    _art_photo_ref = [None]  # module-level GC anchor separate from label

    def _update_player_ui():
        np_title.configure(text=vars_["title"].get() or "Unknown Title")
        np_artist.configure(text=vars_["artist"].get() or "Unknown Artist")
        art_path = vars_["art"].get().strip()
        if art_path and os.path.exists(art_path):
            if HAS_PIL:
                try:
                    img = Image.open(art_path).convert("RGB").resize((62, 62), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(img)
                    _art_photo_ref[0] = photo
                    art_lbl.configure(image=photo, text="", bg=COL["card2"])
                    art_lbl.image = photo
                    return  # success — exit early
                except Exception as e:
                    log(f"  [art] thumbnail error: {e}")
            # PIL unavailable or failed — show art file name as hint
            art_lbl.configure(image="", text="🖼", bg=COL["accent"],
                              font=("Segoe UI", 14))
            art_lbl.image = None
            _art_photo_ref[0] = None
        else:
            _art_photo_ref[0] = None
            art_lbl.configure(image="", text="🎵", bg=COL["card2"],
                              font=("Segoe UI", 24))
            art_lbl.image = None

    # Auto-refresh player bar whenever art/title/artist vars change
    def _on_art_change(*_):
        root.after(150, _update_player_ui)
    vars_["art"].trace_add("write", _on_art_change)
    vars_["title"].trace_add("write", lambda *_: root.after(50, _update_player_ui))
    vars_["artist"].trace_add("write", lambda *_: root.after(50, _update_player_ui))

    # ── AUTOMAGIC ────────────────────────────────────────────────────────
    def _trigger_automagic(confirmed_url=None):
        artist = vars_["artist"].get().strip(); title2 = vars_["title"].get().strip(); out_dir = vars_["out"].get()
        if not out_dir or not artist or not title2: return
        if confirmed_url:
            main_vid_state["url"] = confirmed_url
            root.after(0, lambda: main_vid_play_btn.configure(state="normal", bg=COL["card2"], fg=COL["fg"]))
        log("\n⚡ Starting Media Download & Data Extraction...")
        status_lbl.configure(text="⚡ Downloading...", fg=COL["accent"])
        def worker():
            res = fetch_media_pack(artist, title2, out_dir, confirmed_url, log)
            def apply():
                if res["art"]:     vars_["art"].set(res["art"])
                if res["lyrics"]:  vars_["lyrics"].set(res["lyrics"])
                if res["audio"]:   vars_["audio"].set(res["audio"])
                if res["preview"]: vars_["preview_audio"].set(res["preview"])
                # Guard: only write if we have data AND current value is still blank/default
                cur_album = vars_["album"].get().strip()
                cur_year  = vars_["year"].get().strip()
                # Write album if we got one and field is still blank/default
                if res["album"] and cur_album in ("", "Unknown Album"):
                    vars_["album"].set(res["album"])
                # Only write year if we actually got one (don't overwrite good data with blank)
                if res["year"]:
                    vars_["year"].set(res["year"])
                status_lbl.configure(text="✓ Ready", fg=COL["muted"])
                log("⚡ Auto-Fetch Complete.")
                # Delayed art update — give PIL time after the var write
                root.after(300, _update_player_ui)
            root.after(0, apply)
        threading.Thread(target=worker, daemon=True).start()

    # ── BPM auto-detect (Main page) ───────────────────────────────────────
    def _set_bpm_from_gp(path):
        bpm = detect_gp_bpm(path)
        if bpm:
            txt = f"{bpm:.3f}".rstrip("0").rstrip(".")
            vars_["bpm"].set(txt)
            log(f"  [tempo] Detected {txt} BPM from GP file")
            return True
        return False

    def _redetect_bpm():
        path = vars_["gp"].get().strip()
        if not path or not os.path.exists(path):
            return messagebox.showwarning("No GP file", "Load a Guitar Pro file first.")
        if not _set_bpm_from_gp(path):
            messagebox.showinfo("BPM", "Couldn't detect a tempo automatically — enter it manually.")

    # ── FIX 2: load_gp — defer media inspector so main thread stays free ─
    def load_gp(path):
        for w in arr_grid.winfo_children(): w.destroy()
        track_rows.clear()
        try: gp = gp2rs.GPSong(path)
        except Exception as e:
            return messagebox.showerror("Can't read file", f"Not a valid GP7/8 .gp file:\n{e}")
        _gp_ref[0] = gp   # store for Sync Tuner access

        title2, artist2 = gp.title, gp.artist
        m = re.match(r"^(.*)\s+by\s+(.+)$", title2, re.I)
        if m: title2, artist2 = m.group(1).strip(), m.group(2).strip()
        vars_["title"].set(title2); vars_["artist"].set(artist2)

        gp_album = gp.album.strip()
        vars_["album"].set(gp_album if gp_album else "Unknown Album")

        if not _set_bpm_from_gp(path):
            if not vars_["bpm"].get().strip():
                vars_["bpm"].set("120")
            log("  [tempo] Could not auto-detect BPM — edit the field manually if needed")

        if not vars_["out"].get():
            vars_["out"].set(os.path.dirname(os.path.abspath(path)))

        _update_player_ui()

        # ← deferred so tkinter finishes rendering the GP info first
        root.after(50, lambda: ensure_dependencies_then_run(_show_media_inspector))

        headers = ["", "Track", "Arrangement", "Tone preset"]
        widths  = [3, 28, 12, 22]
        for ci, (h, wd) in enumerate(zip(headers, widths)):
            styled(tk.Label(arr_grid, text=h.upper(), font=("Segoe UI", 8), width=wd, anchor="w"), fg="muted", bg="card").grid(row=0, column=ci, sticky="w", padx=(0 if ci == 0 else 8, 0), pady=(0, 6))

        guitars = 0
        for i in range(len(gp.tracks)):
            kind = gp.track_kind(i)
            if kind is None: continue
            name = " ".join((gp.tracks[i].findtext("Name") or f"Track {i}").split())
            preset = auto_preset(name)
            if kind == "bass":
                arr, tone_opts = "Bass", BASS_PRESETS
            else:
                arr, tone_opts = ("Lead" if guitars == 0 else "Rhythm"), GUITAR_PRESETS
                guitars += 1
            row = {"index": i, "var_include": tk.BooleanVar(value=True), "var_arr": tk.StringVar(value=arr), "var_tone": tk.StringVar(value=preset)}
            r = len(track_rows) + 1
            ttk.Checkbutton(arr_grid, variable=row["var_include"]).grid(row=r, column=0, padx=(0, 4), pady=4, sticky="w")
            styled(tk.Label(arr_grid, text=f"{name}  ({gp.tuning(i)})", font=("Segoe UI", 10), anchor="w"), fg="fg", bg="card").grid(row=r, column=1, sticky="w", padx=(0, 8), pady=4)
            ttk.Combobox(arr_grid, textvariable=row["var_arr"], width=10, state="readonly", values=["Lead", "Rhythm", "Bass", "Skip"]).grid(row=r, column=2, padx=(0, 8), pady=4, sticky="w")
            ttk.Combobox(arr_grid, textvariable=row["var_tone"], width=24, state="readonly", values=PRESET_LABELS).grid(row=r, column=3, pady=4, sticky="w")
            track_rows.append(row)

        arr_hint.configure(text=f"{os.path.basename(path)}  •  {len(track_rows)} playable track(s)")
        log(f"Loaded GP: {os.path.basename(path)}")
        # Refresh sync tuner track panel
        root.after(100, _build_track_panel)

    show_page("main")

    def do_build():
        if not vars_["gp"].get() or not vars_["out"].get():
            return messagebox.showwarning("Missing info", "Pick a GP file and Output Folder.")

        # ── Build progress modal ─────────────────────────────────────────
        root.update_idletasks()
        rx = root.winfo_rootx(); ry = root.winfo_rooty()
        rw = root.winfo_width();  rh = root.winfo_height()

        prog_backdrop = tk.Toplevel(root)
        prog_backdrop.overrideredirect(True)
        prog_backdrop.geometry(f"{rw}x{rh}+{rx}+{ry}")
        prog_backdrop.configure(bg="#000000")
        prog_backdrop.attributes("-alpha", 0.65)
        prog_backdrop.transient(root)
        prog_backdrop.lift()

        prog_win = tk.Toplevel(root)
        prog_win.title("Building CDLC")
        prog_win.transient(root)
        prog_win.configure(bg=COL["card"])
        prog_win.resizable(False, False)
        pw, ph = 480, 200
        prog_win.geometry(f"{pw}x{ph}+{rx+(rw-pw)//2}+{ry+(rh-ph)//2}")
        prog_win.lift()
        prog_win.grab_set()

        styled(tk.Frame(prog_win, height=4), bg="accent").pack(fill="x")
        styled(tk.Label(prog_win, text="Creating CDLC", font=("Segoe UI Semibold", 14)), fg="fg", bg="card").pack(pady=(20, 4))
        prog_step_var = tk.StringVar(value="Initializing…")
        styled(tk.Label(prog_win, textvariable=prog_step_var, font=("Segoe UI", 9)), fg="muted", bg="card").pack(pady=(0, 12))

        pbar = ttk.Progressbar(prog_win, mode="indeterminate", length=380)
        pbar.pack(pady=(0, 16))
        pbar.start(12)

        log_mini = styled(tk.Label(prog_win, text="", font=("Consolas", 8), anchor="w", justify="left"), fg="dim", bg="card")
        log_mini.pack(fill="x", padx=24)

        build_btn.configure(state="disabled", text="Building CDLC…")

        def _prog_log(msg):
            log(msg)
            short = msg.strip()[:72]
            root.after(0, lambda: log_mini.configure(text=short))
            root.after(0, lambda: prog_step_var.set(short[:60] + ("…" if len(short) > 60 else "")))

        def _close_prog():
            try: prog_win.grab_release()
            except Exception: pass
            try: prog_win.destroy()
            except Exception: pass
            try: prog_backdrop.destroy()
            except Exception: pass

        # Build stages for determinate feel — switch to determinate after parsing
        _stages = [
            "Parsing Guitar Pro file…",
            "Converting audio to 48kHz WAV…",
            "Writing arrangement XML…",
            "Applying tone presets…",
            "Writing .rs2dlc project…",
            "Running DDC (dynamic difficulty)…",
            "Packaging PSARC…",
            "Done!",
        ]
        _stage_idx = [0]
        def _advance_stage(label=None):
            txt = label or (_stages[_stage_idx[0]] if _stage_idx[0] < len(_stages) else "Finalizing…")
            _stage_idx[0] += 1
            root.after(0, lambda: prog_step_var.set(txt))

        o = SimpleNamespace(
            gp_path=vars_["gp"].get(), lyrics_path=vars_["lyrics"].get() or None,
            audio_path=vars_["audio"].get() or None, preview_path=vars_["preview_audio"].get() or None,
            art_path=vars_["art"].get() or None, out_dir=vars_["out"].get(),
            title=vars_["title"].get() or "Unknown", artist=vars_["artist"].get() or "Unknown",
            album=vars_["album"].get(), year=vars_["year"].get(),
            leadin=float(vars_["leadin"].get() or 10), volume=float(vars_["volume"].get() or -8),
            appid=vars_["appid"].get() or "248750", scroll_speed=vars_["scroll_speed"].get() or "1.3",
            pitch=vars_["pitch"].get() or "440.0", use_ddc=use_ddc.get(),
            bpm=float(vars_["bpm"].get() or 120),
            make_psarc=bool(psarc_var.get()), packer_path=vars_["packer"].get() or None,
            cst_dir=vars_["cst"].get() or None,
            tracks=[{"index": r["index"], "include": r["var_include"].get(), "arr": r["var_arr"].get(), "tone_label": r["var_tone"].get()} for r in track_rows])

        def worker():
            try:
                _advance_stage("Parsing Guitar Pro file…")
                res = build_project(o, log=_prog_log)
                pbar.stop()
                proj = res["proj_dir"]
                psarc = res.get("psarc")
                msg = "CDLC project created!\n\n\U0001f4c1 " + proj
                if psarc:
                    msg += "\n\n\U0001f4e6 PSARC: " + os.path.basename(psarc)
                elif o.make_psarc:
                    if res.get("packer_failed"):
                        out_snip = (res.get("packer_output","") or "")[:300]
                        msg += "\n\n\u26a0 PSARC build failed. Check the Log tab for full packer output."
                        if out_snip:
                            msg += f"\n\nPacker said:\n{out_snip}"
                    else:
                        msg += "\n\n\u26a0 PSARC skipped — packer.exe or Wwise not found.\nOpen the .rs2dlc in DLC Builder to finish."
                root.after(0, _close_prog)
                root.after(100, lambda: messagebox.showinfo("CDLC Created", msg))
            except Exception as e:
                pbar.stop()
                err = traceback.format_exc()
                log(f"Build failed: {e}\n{err}")
                root.after(0, _close_prog)
                root.after(100, lambda: messagebox.showerror("Build Failed", f"{e}"))
            finally:
                root.after(0, lambda: build_btn.configure(state="normal", text="▶  Create CDLC"))
        threading.Thread(target=worker, daemon=True).start()

    refresh_theme(root, ttk)
    root.mainloop()


if __name__ == "__main__":
    run_gui()