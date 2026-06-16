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


def get_wav_duration(path):
    """Return duration of an audio file in seconds using ffmpeg, or None."""
    ff = _find_exe("ffmpeg")
    if not ff or not path or not os.path.exists(path):
        return None
    try:
        r = subprocess.run([ff, "-i", path], capture_output=True, text=True,
                           timeout=10, creationflags=CREATE_NO_WINDOW)
        m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
        if m:
            h, mi, s = m.groups()
            return int(h) * 3600 + int(mi) * 60 + float(s)
    except Exception:
        pass
    return None


def extract_gp_total_seconds(path):
    """
    Returns the expected total song duration in seconds by walking MasterBars
    and applying the GP tempo map. Returns None on failure.
    """
    try:
        import xml.etree.ElementTree as ET
        with zipfile.ZipFile(path) as z:
            gpif_name = next((n for n in z.namelist() if n.lower().endswith("score.gpif")), None)
            if not gpif_name:
                return None
            data = z.read(gpif_name)
        root_el = ET.fromstring(data)

        tempo_events, tpb = extract_gp_tempo_map(path)
        if not tempo_events:
            tempo_events = [(0, 120)]

        # Count total ticks by summing every MasterBar's duration
        total_ticks = 0
        for mb in root_el.iter("MasterBar"):
            try:
                num = int(mb.findtext("Time/Numerator") or "4")
                den = int(mb.findtext("Time/Denominator") or "4")
            except ValueError:
                num, den = 4, 4
            total_ticks += int(tpb * 4 * num / den)

        if total_ticks <= 0:
            return None

        # Convert total ticks → seconds using the tempo map
        cur_sec  = 0.0
        cur_tick = 0
        cur_bpm  = tempo_events[0][1]
        for ev_tick, ev_bpm in tempo_events:
            if total_ticks <= ev_tick:
                break
            cur_sec  += (ev_tick - cur_tick) / tpb * (60.0 / cur_bpm)
            cur_tick  = ev_tick
            cur_bpm   = ev_bpm
        cur_sec += (total_ticks - cur_tick) / tpb * (60.0 / cur_bpm)
        return cur_sec
    except Exception:
        return None


def get_gp_content_duration(gp_path):
    """Like extract_gp_total_seconds but stops at the LAST masterbar that contains
    actual notes — trailing empty bars (common in GP files) are ignored.
    Returns seconds, or None on failure."""
    try:
        gp = gp2rs.GPSong(gp_path)
        last_content_bar = -1
        for mi, mb in enumerate(gp.masterbars):
            bar_ids = (mb.findtext("Bars") or "").split()
            has_note = False
            for bar_id in bar_ids:
                if has_note: break
                bar_el = gp.bars.get(bar_id)
                if not bar_el: continue
                for vid in (bar_el.findtext("Voices") or "").split():
                    if has_note: break
                    voice_el = gp.voices.get(vid)
                    if not voice_el: continue
                    for bid in (voice_el.findtext("Beats") or "").split():
                        beat_el = gp.beats.get(bid)
                        if beat_el and (beat_el.findtext("Notes") or "").strip():
                            has_note = True
                            break
            if has_note:
                last_content_bar = mi

        if last_content_bar < 0:
            return None
        bar_times_list, _ = gp.bar_times(0.0, bpm_scale=1.0)
        if last_content_bar < len(bar_times_list):
            t_start, quarters, bpm = bar_times_list[last_content_bar]
            return t_start + quarters * 60.0 / bpm
        return None
    except Exception:
        return None


def stretch_audio_to_gp(input_path, output_path, tempo_events_sec, audio_duration,
                        gp_expected_duration=None, log=print):
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
    import shutil as _sh
    ff = _find_exe("ffmpeg")
    if not ff:
        raise RuntimeError("ffmpeg not found")

    if audio_duration <= 0:
        raise RuntimeError("Audio duration unknown — load the waveform first.")

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

    # ── Determine stretch ratio ───────────────────────────────────────────
    # Best path: use the pre-computed GP song duration (from extract_gp_total_seconds).
    # This gives us the definitive ratio = gp_duration / audio_duration regardless
    # of how many tempo events exist.
    if gp_expected_duration and gp_expected_duration > 0:
        avg_ratio = gp_expected_duration / audio_duration
        log(f"  [stretch] GP expected={gp_expected_duration:.3f}s  audio={audio_duration:.3f}s  ratio={avg_ratio:.6f}")
    elif tempo_events_sec and len(tempo_events_sec) >= 2:
        # Fallback: weighted ratio across tempo segments relative to first BPM.
        # Less accurate but better than nothing for multi-tempo files without GP path.
        ratios = []
        weights = []
        ref_bpm = tempo_events_sec[0][1]
        for i, (t_sec, bpm) in enumerate(tempo_events_sec):
            next_t = tempo_events_sec[i + 1][0] if i + 1 < len(tempo_events_sec) else audio_duration
            seg_dur = next_t - t_sec
            if seg_dur <= 0:
                continue
            ratios.append(bpm / ref_bpm)
            weights.append(seg_dur)
        avg_ratio = sum(r * w for r, w in zip(ratios, weights)) / sum(weights) if ratios else 1.0
        log(f"  [stretch] (fallback ratio from tempo events) ratio={avg_ratio:.6f}")
    else:
        _sh.copy2(input_path, output_path)
        log("  [stretch] No GP duration or tempo events — copied as-is (nothing to stretch).")
        return output_path

    avg_ratio = max(0.25, min(4.0, avg_ratio))

    # If ratio is within 0.05% of 1.0, stretching would be inaudible — skip it
    if abs(avg_ratio - 1.0) < 0.0005:
        _sh.copy2(input_path, output_path)
        log(f"  [stretch] Ratio {avg_ratio:.6f} is within tolerance — copied as-is.")
        return output_path

    af = _atempo_chain(avg_ratio)
    log(f"  [stretch] Applying atempo: {af}")

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

    # Compute bpm_scale using only bars that actually contain notes.
    # Plain extract_gp_total_seconds includes trailing empty bars (very common in GP files),
    # which would give a falsely long GP duration and wrong scale.
    # get_gp_content_duration stops at the last bar with real notes.
    bpm_scale = 1.0
    if o.audio_path and os.path.exists(o.audio_path):
        gp_dur  = get_gp_content_duration(o.gp_path)
        aud_dur = get_wav_duration(o.audio_path)
        if gp_dur and aud_dur and aud_dur > 1.0:
            log(f"  [sync] GP content={gp_dur:.1f}s  Audio={aud_dur:.1f}s  (no BPM scaling applied)")

    arrangements, tones, used = [], [], set()
    cst_arrs, cst_tones = [], []
    # Pass audio_duration so make_arrangement can set SongLength to cover the full audio
    _audio_dur_for_xml = None
    if o.audio_path and os.path.exists(o.audio_path):
        _audio_dur_for_xml = get_wav_duration(o.audio_path)
    args = SimpleNamespace(arr="Lead", leadin=o.leadin, title=o.title, artist=o.artist,
                           album=o.album, year=o.year, bpm_scale=bpm_scale,
                           audio_duration=_audio_dur_for_xml)
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

    # DPI awareness — System DPI (1) avoids blur-scaling without breaking Win32 SetParent
    try:
        import ctypes as _ctypes_dpi
        _ctypes_dpi.windll.shcore.SetProcessDpiAwareness(1)   # system DPI aware
    except Exception:
        try:
            _ctypes_dpi.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    root = tk.Tk()
    root.title("RS STUDIO")
    root.minsize(1000, 700)
    root.state("zoomed")          # start maximised on Windows

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

    vars_ = {k: tk.StringVar() for k in ["gp", "lyrics", "audio", "preview_audio", "art", "out", "psarc_out", "title", "artist", "album", "year", "leadin", "volume", "audio_url", "packer", "cst", "slop_url", "slop_exe", "slop_plugin_dir", "appid", "scroll_speed", "pitch", "bpm"]}
    vars_["leadin"].set("5.0"); vars_["volume"].set("-8.0"); vars_["year"].set("2026"); vars_["bpm"].set("120")
    vars_["slop_url"].set("http://localhost:8000")
    vars_["appid"].set("248750"); vars_["scroll_speed"].set("1.3"); vars_["pitch"].set("440.0")
    # Restore persisted paths from settings
    _s = load_settings()
    for _k in ("packer", "cst", "out", "psarc_out", "slop_url", "slop_exe", "slop_plugin_dir", "appid", "scroll_speed", "pitch", "volume", "leadin"):
        if _s.get(_k): vars_[_k].set(_s[_k])
    # Default project folder to <exe dir>/RS Studio Projects if never set
    if not vars_["out"].get():
        _default_out = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "RS Studio Projects")
        os.makedirs(_default_out, exist_ok=True)
        vars_["out"].set(_default_out)

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

    nav_label(sidebar, 3, "⬡", "Main", "main")
    nav_label(sidebar, 5, "🎸", "Slopsmith", "slopsmith")
    nav_label(sidebar, 6, "◐", "Theme", "theme"); nav_label(sidebar, 7, "≡", "Log", "log")
    nav_label(sidebar, 8, "?", "Help", "help")

    sidebar.rowconfigure(9, weight=1)

    page_container = styled(tk.Frame(root), bg="surface")
    page_container.grid(row=0, column=1, sticky="nsew"); page_container.rowconfigure(0, weight=1); page_container.columnconfigure(0, weight=1)
    pages = {}

    def show_page(name):
        # Stop the audio editor's waveform player when navigating away
        try:
            if _wf_state.get("playing"):
                _wf_stop()
        except (NameError, Exception):
            pass
        for pg in pages.values(): pg.pack_forget()
        if name in pages: pages[name].pack(fill="both", expand=True)
        current_page.set(name)
        refresh_theme(root, ttk)
        # Player bar only makes sense on the main page (audio editor has its own controls)
        try:
            if name == "main":
                player_bar.grid(row=1, column=0, columnspan=2, sticky="ew")
            else:
                player_bar.grid_remove()
        except Exception:
            pass

    def _persist_settings(*_):
        save_settings({k: vars_[k].get() for k in ("packer","cst","out","psarc_out","slop_url","slop_exe","slop_plugin_dir","appid","scroll_speed","pitch","volume","leadin")})
    for _pk in ("packer","cst","out","psarc_out","slop_url","slop_exe","slop_plugin_dir","appid","scroll_speed","pitch","volume","leadin"):
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

    # Main header with inline "New Project" button
    _main_hdr = styled(tk.Frame(main_page), bg="bg"); _main_hdr.pack(fill="x")
    _main_inner = styled(tk.Frame(_main_hdr), bg="bg"); _main_inner.pack(fill="x", padx=32, pady=(28, 20))
    styled(tk.Label(_main_inner, text="Project Configuration", font=("Segoe UI Semibold", 16)),
           fg="fg", bg="bg").pack(side="left")

    def _do_new_project():
        _stop_all_media() if "_stop_all_media" in dir() else None
        for k in ["gp","lyrics","audio","preview_audio","art",
                  "title","artist","audio_url"]:
            vars_[k].set("")
        vars_["album"].set("Unknown Album")
        vars_["year"].set("2026")
        try:
            for w in arr_grid.winfo_children(): w.destroy()
            track_rows.clear()
        except Exception: pass
        try: _update_player_ui()
        except Exception: pass

    def _next_step():
        gp  = vars_["gp"].get().strip()
        aud = vars_["audio"].get().strip()
        if not gp or not os.path.exists(gp):
            # No GP — open file dialog to load one
            p = filedialog.askopenfilename(
                title="Open GP File",
                filetypes=[("Guitar Pro", "*.gp *.gp3 *.gp4 *.gp5 *.gpx *.gp7"), ("All files", "*.*")])
            if p:
                load_gp(p)
        elif not aud or not os.path.exists(aud):
            # GP loaded, no audio — fetch it
            ensure_dependencies_then_run(_show_media_inspector)
        else:
            # All set — kick off the build then go to Slopsmith
            do_build()

    _next_btn = tk.Button(_main_inner, text="Next  →", cursor="hand2",
                          font=("Segoe UI Semibold", 9), relief="flat", bd=0,
                          padx=14, pady=5, fg="white",
                          bg=COL["accent"], activebackground=COL["accent_hi"],
                          command=_next_step)
    _next_btn.pack(side="right", padx=(8, 0))

    tk.Button(_main_inner, text="＋  New Project", cursor="hand2",
              font=("Segoe UI Semibold", 9), relief="flat", bd=0,
              padx=14, pady=5,
              bg=COL["card2"], fg=COL["fg"],
              activebackground=COL["border_hi"],
              command=_do_new_project).pack(side="right")

    def _update_next_btn(*_):
        gp  = vars_["gp"].get().strip()
        aud = vars_["audio"].get().strip()
        if not gp or not os.path.exists(gp):
            _next_btn.configure(text="Load GP File  →")
        elif not aud or not os.path.exists(aud):
            _next_btn.configure(text="Fetch Audio  →")
        else:
            _next_btn.configure(text="Build & Test  →")
    for _pk in ("gp", "audio"):
        vars_[_pk].trace_add("write", _update_next_btn)

    styled(tk.Frame(_main_hdr, height=1), bg="border").pack(fill="x")

    # ── STARTUP / RECENT BUILDS OVERLAY ───────────────────────────────────
    startup_overlay = tk.Frame(main_page, bg=COL["surface"])

    def _get_recent_projects():
        return load_settings().get("recent_projects", [])

    def _save_project_to_history(title, artist, gp_path, psarc_path, art_path):
        import datetime
        entry = {
            "title":         title,
            "artist":        artist,
            "gp":            gp_path,
            "psarc":         psarc_path or "",
            "art":           art_path or "",
            "audio":         vars_["audio"].get() or "",
            "preview_audio": vars_["preview_audio"].get() or "",
            "out":           vars_["out"].get() or "",
            "ts":            datetime.datetime.now().strftime("%b %d %Y, %I:%M %p"),
        }
        existing = _get_recent_projects()
        # remove duplicates by gp path, keep newest
        existing = [e for e in existing if e.get("gp") != gp_path]
        existing.insert(0, entry)
        save_settings({"recent_projects": existing[:10]})

    def _get_pinned_gps():
        return load_settings().get("pinned_gps", [])

    def _toggle_pin(gp_path):
        pinned = load_settings().get("pinned_gps", [])
        if gp_path in pinned:
            pinned.remove(gp_path)
        else:
            pinned.insert(0, gp_path)
            pinned = pinned[:10]
        save_settings({"pinned_gps": pinned})
        _show_startup_overlay()

    def _show_startup_overlay():
        """After Effects-style startup overlay: large logo, two-column layout."""
        for w in startup_overlay.winfo_children():
            w.destroy()
        startup_overlay.place(x=0, y=0, relwidth=1, relheight=1)
        startup_overlay.lift()

        outer = tk.Frame(startup_overlay, bg=COL["surface"])
        outer.place(relx=0.5, rely=0.5, anchor="center")

        # ── Logo ────────────────────────────────────────────────────
        logo_row = tk.Frame(outer, bg=COL["surface"])
        logo_row.pack(pady=(0, 2))
        tk.Label(logo_row, text="RS", font=("Segoe UI Black", 44),
                 fg=COL["accent"], bg=COL["surface"]).pack(side="left")
        tk.Label(logo_row, text=" STUDIO", font=("Segoe UI", 44, "bold"),
                 fg=COL["fg"], bg=COL["surface"]).pack(side="left")
        tk.Label(outer, text="Automated CDLC Pipeline",
                 font=("Segoe UI", 11), fg=COL["muted"],
                 bg=COL["surface"]).pack(pady=(0, 30))

        # ── Two-column body ─────────────────────────────────────────
        body = tk.Frame(outer, bg=COL["surface"])
        body.pack(fill="x")

        # Left – action buttons (natural sizing, no pack_propagate)
        left_col = tk.Frame(body, bg=COL["surface"])
        left_col.pack(side="left", anchor="n", padx=(0, 0))

        def _new_project():
            startup_overlay.place_forget()

        def _open_existing():
            gp = filedialog.askopenfilename(
                title="Open GP File",
                filetypes=[("Guitar Pro", "*.gp *.gp5 *.gp4 *.gp3 *.gpx"),
                           ("All files", "*.*")])
            if not gp:
                return
            startup_overlay.place_forget()
            root.after(50, lambda: load_gp(gp))

        _bkw = dict(font=("Segoe UI Semibold", 11), cursor="hand2",
                    relief="flat", bd=0, pady=13)
        tk.Button(left_col, text="＋  Start New Project",
                  fg="white", bg=COL["accent"],
                  activebackground=COL["accent_hi"],
                  command=_new_project, **_bkw).pack(fill="x", pady=(0, 10), ipadx=26)
        tk.Button(left_col, text="📂  Open Existing Project",
                  fg=COL["fg"], bg=COL["card2"],
                  activebackground=COL["border_hi"],
                  command=_open_existing, **_bkw).pack(fill="x", ipadx=26)

        # Divider
        tk.Frame(body, width=1, bg=COL["border"]).pack(
            side="left", fill="y", padx=28)

        # Right – pinned + recent (natural sizing, no pack_propagate)
        right_col = tk.Frame(body, bg=COL["surface"])
        right_col.pack(side="left", anchor="n", fill="x", expand=True)

        pinned_gps     = _get_pinned_gps()
        all_recent     = _get_recent_projects()
        pinned_entries = [e for e in all_recent
                          if e.get("gp","") in pinned_gps][:5]
        recent_entries = [e for e in all_recent
                          if e.get("gp","") not in pinned_gps][:5]

        def _make_project_row(parent, entry, is_pinned):
            gp     = entry.get("gp","")
            title  = (entry.get("title","Unknown") or "Unknown")[:32]
            artist = (entry.get("artist","") or "")[:28]
            ts     = entry.get("ts","")
            psarc  = entry.get("psarc","")
            has_p  = bool(psarc and os.path.exists(psarc))

            row = tk.Frame(parent, bg=COL["surface"], cursor="hand2")
            row.pack(fill="x", pady=2)

            # Pin toggle
            pin_lbl = tk.Label(
                row,
                text="📌" if is_pinned else "·",
                font=("Segoe UI", 14 if is_pinned else 20),
                fg=COL["accent"] if is_pinned else COL["dim"],
                bg=COL["surface"], cursor="hand2", width=2, anchor="center")
            pin_lbl.pack(side="left", padx=(0, 8))

            # Text info
            info = tk.Frame(row, bg=COL["surface"])
            info.pack(side="left", fill="x", expand=True)

            name_str = title + (f"  —  {artist}" if artist else "")
            name_lbl = tk.Label(info, text=name_str,
                                font=("Segoe UI Semibold", 10),
                                fg=COL["fg"], bg=COL["surface"], anchor="w")
            name_lbl.pack(fill="x")

            meta_str = ts + ("  ✓ PSARC" if has_p else "")
            tk.Label(info, text=meta_str, font=("Segoe UI", 8),
                     fg=COL["dim"], bg=COL["surface"], anchor="w").pack(fill="x")

            # Open-in-Explorer icon (if psarc exists)
            if has_p:
                def _open_p(e=None, p=psarc):
                    try: subprocess.Popen(["explorer", "/select,",
                                          os.path.abspath(p)])
                    except Exception: pass
                ob = tk.Label(row, text="📂", font=("Segoe UI", 11),
                              fg=COL["muted"], bg=COL["surface"],
                              cursor="hand2")
                ob.pack(side="right", padx=(4, 0))
                ob.bind("<Button-1>", _open_p)

            # Click → load project (restore all saved paths so auto-fetch is skipped)
            def _load(e=None, g=gp,
                      a=entry.get("art",""),
                      aud=entry.get("audio",""),
                      prev=entry.get("preview_audio",""),
                      out=entry.get("out","")):
                if not g or not os.path.exists(g):
                    return messagebox.showwarning("File not found",
                        f"GP file not found:\n{g}")
                startup_overlay.place_forget()
                # Restore previously saved file paths before load_gp runs
                if a    and os.path.exists(a):    vars_["art"].set(a)
                if aud  and os.path.exists(aud):
                    vars_["audio"].set(aud)
                else:
                    # Fallback: scan GP folder and out folder for audio file
                    _search_dirs = [os.path.dirname(g)]
                    if out and os.path.isdir(out): _search_dirs.append(out)
                    for _sd in _search_dirs:
                        for _ext in (".ogg", ".wav", ".mp3", ".flac"):
                            _cands = [f for f in os.listdir(_sd) if f.lower().endswith(_ext)]
                            if _cands:
                                vars_["audio"].set(os.path.join(_sd, _cands[0]))
                                break
                        if vars_["audio"].get(): break
                if prev and os.path.exists(prev): vars_["preview_audio"].set(prev)
                if out  and os.path.isdir(out):   vars_["out"].set(out)
                root.after(50, lambda: load_gp(g, _keep_media=True))

            for w in (row, info, name_lbl):
                w.bind("<Button-1>", _load)

            # Pin toggle click
            def _pin_tog(e=None, g=gp):
                _toggle_pin(g)
            pin_lbl.bind("<Button-1>", _pin_tog)

            # Hover highlight
            all_w = [row, info, name_lbl]
            def _henter(e, ws=all_w):
                for w in ws: w.configure(bg=COL["card"])
            def _hleave(e, ws=all_w):
                for w in ws: w.configure(bg=COL["surface"])
            for w in all_w:
                w.bind("<Enter>", _henter); w.bind("<Leave>", _hleave)

        if pinned_entries:
            tk.Label(right_col, text="PINNED",
                     font=("Segoe UI Semibold", 8),
                     fg=COL["accent"], bg=COL["surface"],
                     anchor="w").pack(fill="x", pady=(0, 4))
            for e in pinned_entries:
                _make_project_row(right_col, e, is_pinned=True)
            if recent_entries:
                tk.Frame(right_col, height=1,
                         bg=COL["border"]).pack(fill="x", pady=10)

        if recent_entries:
            lbl = "RECENT" if pinned_entries else "RECENT PROJECTS"
            tk.Label(right_col, text=lbl,
                     font=("Segoe UI Semibold", 8),
                     fg=COL["dim"], bg=COL["surface"],
                     anchor="w").pack(fill="x", pady=(0, 4))
            for e in recent_entries:
                _make_project_row(right_col, e, is_pinned=False)

        if not pinned_entries and not recent_entries:
            tk.Label(right_col,
                     text="No recent projects yet.\nLoad a .gp file to get started.",
                     font=("Segoe UI", 10), fg=COL["dim"],
                     bg=COL["surface"], justify="left").pack(anchor="w")

    mw = tk.Frame(main_page, bg=COL["surface"]); mw.pack(fill="both", expand=True)
    canvas = styled(tk.Canvas(mw, highlightthickness=0, bd=0), bg="surface")
    vsb = ttk.Scrollbar(mw, orient="vertical", command=canvas.yview); canvas.configure(yscrollcommand=vsb.set)
    canvas.pack(side="left", fill="both", expand=True); vsb.pack(side="right", fill="y")
    scroll_frame = styled(tk.Frame(canvas), bg="surface"); scroll_win = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
    scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=(0, 0, e.width, e.height)))
    canvas.bind("<Configure>", lambda e: canvas.itemconfig(scroll_win, width=e.width))

    def _mouse_scroll(e):
        pg = current_page.get()
        if pg == "main":
            if canvas.yview()[0] == 0.0 and e.delta > 0: return
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        elif pg == "help":
            help_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
    root.bind_all("<MouseWheel>", _mouse_scroll)

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

    col_label(files_left, "PROJECT FOLDER  (xml, wav, etc.)", 4, 0); col_label(files_left, "AUDIO FILE (.wav / .mp3 / .ogg)", 4, 1, padx=(16, 0))
    file_field(files_left, 5, 0, vars_["out"], [], save_dir=True)
    file_field(files_left, 5, 1, vars_["audio"], [("Audio", "*.wav *.ogg *.mp3 *.flac")], padx=(16, 0))

    col_label(files_left, "PSARC OUTPUT FOLDER  (where finished .psarc files land — your Rocksmith DLC folder)", 6, 0)
    psarc_out_row = styled(tk.Frame(files_left), bg="card"); psarc_out_row.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(0, 8))
    ef_po = styled(tk.Frame(psarc_out_row, highlightthickness=1), bg="field", highlightbackground="border")
    ef_po.pack(side="left", fill="x", expand=True, ipady=1)
    e_po = styled(tk.Entry(ef_po, textvariable=vars_["psarc_out"], relief="flat", font=("Segoe UI", 10), bd=4, highlightthickness=0), bg="field", fg="fg", insertbackground="accent_hi")
    e_po.pack(fill="x", expand=True)
    e_po.bind("<FocusIn>", lambda ev: ef_po.config(highlightbackground=COL["accent"]))
    e_po.bind("<FocusOut>", lambda ev: ef_po.config(highlightbackground=COL["border"]))
    def _browse_psarc_out():
        p = filedialog.askdirectory(title="Select PSARC output / Rocksmith DLC folder")
        if p: vars_["psarc_out"].set(p)
    def _open_psarc_out():
        p = vars_["psarc_out"].get().strip()
        if p and os.path.isdir(p):
            subprocess.Popen(["explorer", p] if sys.platform == "win32" else ["xdg-open", p])
    tk.Button(psarc_out_row, text="⋯", font=("Segoe UI", 9), cursor="hand2",
              command=_browse_psarc_out, relief="flat", bd=0, padx=8, pady=4,
              bg=COL["card2"], fg=COL["fg"], activebackground=COL["border_hi"]).pack(side="left", padx=(4, 2))
    tk.Button(psarc_out_row, text="📂 Open Builds", font=("Segoe UI", 8), cursor="hand2",
              command=_open_psarc_out, relief="flat", bd=0, padx=10, pady=4,
              bg=COL["card2"], fg=COL["accent"], activebackground=COL["border_hi"]).pack(side="left", padx=(0, 2))
    styled(tk.Label(psarc_out_row,
                    text="Tip: set this to your Rocksmith\\dlc folder — finished PSARCs drop here automatically",
                    font=("Segoe UI", 7)),
           fg="dim", bg="card").pack(side="left", padx=(6, 0))

    col_label(files_left, "ALBUM ART (optional)", 8, 0); col_label(files_left, "LYRICS (.lrc or .txt)", 8, 1, padx=(16, 0))
    file_field(files_left, 9, 0, vars_["art"], [("Images", "*.png *.jpg *.jpeg")])
    file_field(files_left, 9, 1, vars_["lyrics"], [("Lyrics", "*.txt *.lrc")], padx=(16, 0))

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
    # PAGE: AUDIO EDITOR (formerly Slopsmith)
    # ══════════════════════════════════════════════════════════════════════
    slop_page = styled(tk.Frame(page_container), bg="surface")
    pages["audioeditor"] = slop_page
    page_header(slop_page, "Audio Editor  —  Sync Tuner")

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
            [ffplay, "-nodisp", "-autoexit", "-ss", str(offset), "-volume", str(_player_vol[0]), path],
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
           fg="dim", bg="surface").pack(side="left", padx=(2, 12))

    # Auto BPM: derive effective audio BPM from GP total duration vs audio duration
    _auto_bpm_status = tk.StringVar(value="")

    def _auto_bpm():
        gp_path    = vars_["gp"].get().strip()
        audio_path = vars_["audio"].get().strip()
        if not gp_path or not os.path.exists(gp_path):
            _auto_bpm_status.set("Load a GP file first")
            return
        audio_dur = _wf_state.get("duration", 0) or _get_dur(audio_path)
        if audio_dur <= 0:
            _auto_bpm_status.set("Load waveform first")
            return
        _auto_bpm_status.set("Computing…")
        def _worker():
            gp_dur = extract_gp_total_seconds(gp_path)
            if not gp_dur or gp_dur <= 0:
                root.after(0, lambda: _auto_bpm_status.set("Couldn't read GP duration"))
                return
            try:
                base_bpm = float(vars_["bpm"].get() or 120)
            except ValueError:
                base_bpm = 120.0
            effective_bpm = round(base_bpm * (gp_dur / audio_dur), 3)
            effective_bpm = max(20.0, min(400.0, effective_bpm))
            txt = f"{effective_bpm:.3f}".rstrip("0").rstrip(".")
            root.after(0, lambda: vars_["bpm"].set(txt))
            root.after(0, _wf_redraw)
            root.after(0, lambda: _auto_bpm_status.set(
                f"→ {txt} BPM  (GP {gp_dur:.1f}s / audio {audio_dur:.1f}s)"))
            log(f"  [auto-bpm] GP={gp_dur:.3f}s  audio={audio_dur:.3f}s  → {txt} BPM")
        threading.Thread(target=_worker, daemon=True).start()

    tk.Button(bpm_ctrl_row, text="Auto ↺", font=("Segoe UI", 8), cursor="hand2",
              command=_auto_bpm, relief="flat", bd=0, padx=10, pady=4,
              bg=COL["card2"], fg=COL["accent"],
              activebackground=COL["border_hi"]).pack(side="left", padx=(0, 6))

    styled(tk.Label(bpm_ctrl_row, textvariable=_auto_bpm_status, font=("Segoe UI", 7)),
           fg="dim", bg="surface").pack(side="left")

    styled(tk.Label(bpm_ctrl_row,
                    text="   Grid = bars  |  zoom ≤ 8s shows beats",
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

    # Show sync status: how much the note chart will be scaled at build time
    _sync_status_var = tk.StringVar(value="")
    _sync_lbl = styled(tk.Label(gp_row, textvariable=_sync_status_var,
                                font=("Segoe UI", 8)),
                       fg="muted", bg="surface")
    _sync_lbl.pack(side="left", padx=(0, 8))

    def _update_sync_status(*_):
        gp_path   = vars_["gp"].get().strip()
        aud_path  = vars_["audio"].get().strip()
        if not gp_path or not aud_path or not os.path.exists(aud_path):
            _sync_status_var.set("")
            return
        def _worker():
            gp_dur  = get_gp_content_duration(gp_path)  # ignores trailing empty bars
            aud_dur = get_wav_duration(aud_path)
            if gp_dur and aud_dur and aud_dur > 1.0:
                diff = abs(gp_dur - aud_dur)
                if diff < 2.0:
                    root.after(0, lambda: _sync_status_var.set(
                        f"✓ GP {gp_dur:.0f}s ≈ Audio {aud_dur:.0f}s — in sync"))
                    root.after(0, lambda: _sync_lbl.configure(fg=COL["ok"]))
                else:
                    scale = gp_dur / aud_dur
                    root.after(0, lambda: _sync_status_var.set(
                        f"GP content {gp_dur:.0f}s vs Audio {aud_dur:.0f}s → notes scaled ×{1/scale:.3f} at build"))
                    root.after(0, lambda: _sync_lbl.configure(fg=COL["warn"]))
        threading.Thread(target=_worker, daemon=True).start()

    vars_["audio"].trace_add("write", _update_sync_status)
    vars_["gp"].trace_add("write", _update_sync_status)

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
                            text="▶  Build CDLC",
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
    btn_slop_toggle.grid(row=6, column=0, columnspan=2, sticky="w", pady=(6,0))

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
            subprocess.Popen([exe])
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

    # ══════════════════════════════════════════════════════════════════════
    # PAGE: SLOPSMITH GAMEPLAY EMBED
    # ══════════════════════════════════════════════════════════════════════
    slop_embed_page = styled(tk.Frame(page_container), bg="surface")
    pages["slopsmith"] = slop_embed_page
    page_header(slop_embed_page, "Slopsmith  —  Verify & Test")

    _slop_embed_state = {"hwnd": 0, "pid": 0}

    slop_embed_body = styled(tk.Frame(slop_embed_page), bg="surface")
    slop_embed_body.pack(fill="both", expand=True, padx=28, pady=(12, 16))

    # ── toolbar ──────────────────────────────────────────────────────────
    slop_tb = styled(tk.Frame(slop_embed_body), bg="surface")
    slop_tb.pack(fill="x", pady=(0, 8))

    _slop_embed_status = tk.StringVar(value="Slopsmith not running — click Launch to start.")

    def _slop_embed_launch():
        exe = vars_["slop_exe"].get().strip()
        if not exe or not os.path.isfile(exe):
            exe = _find_slopsmith_exe()
            if exe:
                vars_["slop_exe"].set(exe)
            else:
                _slop_embed_status.set("Slopsmith not found — set path in Settings.")
                return
        _slop_embed_status.set("Launching Slopsmith…")
        try:
            proc = subprocess.Popen([exe])
            _slop_embed_state["pid"] = proc.pid
            # Give Electron extra time to fully render before we try to embed
            root.after(4000, _slop_try_embed)
        except Exception as ex:
            _slop_embed_status.set(f"Launch failed: {ex}")

    def _slop_try_embed():
        """Find the Slopsmith window and embed it into our canvas."""
        import ctypes
        pid = _slop_embed_state["pid"]
        hwnd = [0]

        def _get_hwnd_pid(h):
            pid_buf = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(h, ctypes.byref(pid_buf))
            return pid_buf.value

        def _enum_cb(h, _):
            if not ctypes.windll.user32.IsWindowVisible(h):
                return True
            buf = ctypes.create_unicode_buffer(512)
            ctypes.windll.user32.GetWindowTextW(h, buf, 512)
            title = buf.value
            title_lo = title.lower()
            # Match by pid first (most reliable), then by title keyword
            h_pid = _get_hwnd_pid(h)
            if pid and h_pid == pid:
                hwnd[0] = h
                return False
            if "slopsmith" in title_lo:
                hwnd[0] = h
                return False
            return True

        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool,
                                              ctypes.POINTER(ctypes.c_int),
                                              ctypes.POINTER(ctypes.c_int))
        ctypes.windll.user32.EnumWindows(EnumWindowsProc(_enum_cb), 0)

        if not hwnd[0]:
            # Try again in 1.5s — Slopsmith might still be loading
            if _slop_embed_state.get("_embed_retries", 0) < 20:
                _slop_embed_state["_embed_retries"] = _slop_embed_state.get("_embed_retries", 0) + 1
                retries = _slop_embed_state["_embed_retries"]
                _slop_embed_status.set(f"Waiting for Slopsmith window… ({retries}/20)")
                root.after(1500, _slop_try_embed)
            else:
                _slop_embed_status.set("Couldn’t embed — Slopsmith may already be open. Click Re-embed.")
            return

        _slop_embed_state["_embed_retries"] = 0
        _slop_embed_state["hwnd"] = hwnd[0]
        h = hwnd[0]
        parent_hwnd = slop_embed_canvas.winfo_id()

        GWL_STYLE  = -16
        WS_CHILD   = 0x40000000
        WS_VISIBLE = 0x10000000

        # Remove title bar, set as child
        ctypes.windll.user32.SetParent(h, parent_hwnd)
        ctypes.windll.user32.SetWindowLongW(h, GWL_STYLE, WS_CHILD | WS_VISIBLE)

        # Resize to fill canvas
        w  = slop_embed_canvas.winfo_width() or 1200
        hh = slop_embed_canvas.winfo_height() or 700
        ctypes.windll.user32.MoveWindow(h, 0, 0, w, hh, True)

        _slop_embed_status.set("✓ Slopsmith embedded — play to verify beat sync.")
        try: _slop_placeholder.place_forget()
        except Exception: pass
        root.after(0, lambda: btn_slop_detach.configure(state="normal"))
        root.after(0, lambda: btn_slop_embed.configure(text="↺ Re-embed", state="normal"))

        # Give Slopsmith keyboard focus immediately after embedding
        import ctypes as _ct
        def _give_focus(hwnd=h):
            try: _ct.windll.user32.SetFocus(hwnd)
            except Exception: pass
        root.after(300, _give_focus)

        # Clicking on the canvas area forwards keyboard focus to Slopsmith
        def _canvas_click_focus(event, hwnd=h):
            try: _ct.windll.user32.SetFocus(hwnd)
            except Exception: pass
        slop_embed_canvas.bind("<Button-1>", _canvas_click_focus, add="+")
        _slop_embed_state["_focus_fn"] = _canvas_click_focus

        # Electron tends to escape SetParent — run a keeper loop
        _slop_embed_state["_keep_alive"] = True
        _slop_keep_embedded()

    def _slop_keep_embedded():
        """Periodically re-assert SetParent so Electron can’t escape the canvas."""
        if not _slop_embed_state.get("_keep_alive"):
            return
        h = _slop_embed_state.get("hwnd", 0)
        if not h:
            return
        import ctypes, ctypes.wintypes
        try:
            parent_hwnd = slop_embed_canvas.winfo_id()
            cur_parent = ctypes.windll.user32.GetParent(h)
            cw = slop_embed_canvas.winfo_width() or 1200
            ch = slop_embed_canvas.winfo_height() or 700
            if cur_parent != parent_hwnd:
                GWL_STYLE  = -16
                WS_CHILD   = 0x40000000
                WS_VISIBLE = 0x10000000
                ctypes.windll.user32.SetParent(h, parent_hwnd)
                ctypes.windll.user32.SetWindowLongW(h, GWL_STYLE, WS_CHILD | WS_VISIBLE)
                ctypes.windll.user32.MoveWindow(h, 0, 0, cw, ch, True)
            else:
                rect = ctypes.wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(h, ctypes.byref(rect))
                if (rect.right - rect.left) != cw or (rect.bottom - rect.top) != ch:
                    ctypes.windll.user32.MoveWindow(h, 0, 0, cw, ch, True)
        except Exception:
            pass
        root.after(4000, _slop_keep_embedded)

    def _slop_on_resize(event):
        h = _slop_embed_state.get("hwnd", 0)
        if h:
            import ctypes
            try:
                ctypes.windll.user32.MoveWindow(h, 0, 0, event.width, event.height, True)
            except Exception:
                pass

    def _slop_detach():
        """Release Slopsmith back to its own window."""
        import ctypes
        _slop_embed_state["_keep_alive"] = False  # stop keeper loop
        h = _slop_embed_state.get("hwnd", 0)
        if h:
            GWL_STYLE           = -16
            WS_OVERLAPPEDWINDOW = 0x00CF0000
            WS_VISIBLE          = 0x10000000
            ctypes.windll.user32.SetParent(h, 0)
            ctypes.windll.user32.SetWindowLongW(h, GWL_STYLE, WS_OVERLAPPEDWINDOW | WS_VISIBLE)
            ctypes.windll.user32.ShowWindow(h, 9)  # SW_RESTORE
            _slop_embed_state["hwnd"] = 0
            _slop_embed_status.set("Slopsmith detached.")
            btn_slop_detach.configure(state="disabled")
            btn_slop_embed.configure(text="▶  Launch & Embed Slopsmith", state="normal")

    btn_slop_embed = tk.Button(slop_tb, text="▶  Launch & Embed Slopsmith",
                               font=("Segoe UI Semibold", 10), cursor="hand2",
                               command=_slop_embed_launch, relief="flat", bd=0,
                               padx=18, pady=8, fg="white",
                               bg=COL["ok"], activebackground=COL["ok"])
    btn_slop_embed.pack(side="left", padx=(0, 8))

    btn_slop_detach = tk.Button(slop_tb, text="⤢  Detach to Window",
                                font=("Segoe UI", 9), cursor="hand2",
                                command=_slop_detach, relief="flat", bd=0,
                                padx=12, pady=8, state="disabled",
                                bg=COL["card2"], fg=COL["fg"],
                                activebackground=COL["border_hi"])
    btn_slop_detach.pack(side="left", padx=(0, 8))

    def _slop_browse_exe():
        p = filedialog.askopenfilename(
            title="Find Slopsmith.exe",
            filetypes=[("Executable", "*.exe"), ("All files", "*.*")])
        if p:
            vars_["slop_exe"].set(p)
            _slop_embed_status.set(f"Exe set: {os.path.basename(p)} — click Launch & Embed.")

    tk.Button(slop_tb, text="📂 Set Exe",
              font=("Segoe UI", 8), cursor="hand2",
              command=_slop_browse_exe, relief="flat", bd=0,
              padx=10, pady=8,
              bg=COL["card2"], fg=COL["muted"],
              activebackground=COL["border_hi"]).pack(side="left", padx=(0, 4))

    def _slop_embed_running():
        """Try to embed Slopsmith if it's already open."""
        _slop_embed_state["_embed_retries"] = 0
        _slop_embed_status.set("Looking for running Slopsmith window…")
        root.after(100, _slop_try_embed)

    tk.Button(slop_tb, text="↺ Already Running",
              font=("Segoe UI", 8), cursor="hand2",
              command=_slop_embed_running, relief="flat", bd=0,
              padx=10, pady=8,
              bg=COL["card2"], fg=COL["muted"],
              activebackground=COL["border_hi"]).pack(side="left", padx=(0, 12))

    def _slop_list_windows():
        """Log all visible windows so user can find Slopsmith's actual title."""
        import ctypes, ctypes.wintypes
        wins = []
        def _cb(h, _):
            if not ctypes.windll.user32.IsWindowVisible(h): return True
            buf = ctypes.create_unicode_buffer(512)
            ctypes.windll.user32.GetWindowTextW(h, buf, 512)
            t = buf.value.strip()
            if not t: return True
            rect = ctypes.wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(h, ctypes.byref(rect))
            w = rect.right - rect.left; hh = rect.bottom - rect.top
            if w > 100 and hh > 100: wins.append((h, t, w, hh))
            return True
        P = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
        ctypes.windll.user32.EnumWindows(P(_cb), 0)
        log(f"  [windows] {len(wins)} visible windows:")
        for h, t, w, hh in sorted(wins, key=lambda x: -(x[2]*x[3]))[:30]:
            log(f"    {h:#010x}  {w}x{hh}  \"{t}\"")
        _slop_embed_status.set(f"Listed {len(wins)} windows in Log — find Slopsmith's title there, then use Embed by Title.")
        show_page("log")

    tk.Button(slop_tb, text="📋 List Windows",
              font=("Segoe UI", 8), cursor="hand2",
              command=_slop_list_windows, relief="flat", bd=0,
              padx=10, pady=8,
              bg=COL["card2"], fg=COL["muted"],
              activebackground=COL["border_hi"]).pack(side="left", padx=(0, 12))

    # ── Manual embed by title ─────────────────────────────────────────────
    slop_tb2 = styled(tk.Frame(slop_embed_body), bg="surface")
    slop_tb2.pack(fill="x", pady=(0, 6))

    styled(tk.Label(slop_tb2, text="Embed by title (if auto fails):",
                    font=("Segoe UI", 8)),
           fg="muted", bg="surface").pack(side="left", padx=(0, 6))

    _slop_manual_title = tk.StringVar(value="")
    _mt_ef = styled(tk.Frame(slop_tb2, highlightthickness=1), bg="field", highlightbackground="border")
    _mt_ef.pack(side="left", ipady=1)
    _mt_entry = styled(tk.Entry(_mt_ef, textvariable=_slop_manual_title,
                                font=("Segoe UI", 9), relief="flat",
                                bd=3, highlightthickness=0, width=22),
                       bg="field", fg="fg", insertbackground="accent_hi")
    _mt_entry.pack()
    _mt_entry.bind("<FocusIn>",  lambda e: _mt_ef.config(highlightbackground=COL["accent"]))
    _mt_entry.bind("<FocusOut>", lambda e: _mt_ef.config(highlightbackground=COL["border"]))

    def _slop_embed_by_title():
        import ctypes, ctypes.wintypes
        kw = _slop_manual_title.get().strip().lower()
        if not kw:
            _slop_embed_status.set("Enter part of the window title first.")
            return
        best_h = 0; best_area = 0
        def _cb(h, _):
            nonlocal best_h, best_area
            if not ctypes.windll.user32.IsWindowVisible(h): return True
            buf = ctypes.create_unicode_buffer(512)
            ctypes.windll.user32.GetWindowTextW(h, buf, 512)
            if kw not in buf.value.lower(): return True
            rect = ctypes.wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(h, ctypes.byref(rect))
            w = rect.right - rect.left; hh = rect.bottom - rect.top
            if w * hh > best_area and w > 100 and hh > 100:
                best_area = w * hh; best_h = h
            return True
        P = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
        ctypes.windll.user32.EnumWindows(P(_cb), 0)
        if best_h:
            log(f"  [slop-embed] Manual embed: HWND={best_h:#010x}")
            _do_embed(best_h)
        else:
            _slop_embed_status.set(f"No visible window matching \"{kw}\" — check Log for window list.")

    tk.Button(slop_tb2, text="Embed ↗", cursor="hand2",
              font=("Segoe UI Semibold", 9), relief="flat", bd=0,
              padx=12, pady=5, fg="white",
              bg=COL["accent"], activebackground=COL["accent_hi"],
              command=_slop_embed_by_title).pack(side="left", padx=(4, 12))

    styled(tk.Label(slop_tb2, textvariable=_slop_embed_status, font=("Segoe UI", 8)),
           fg="dim", bg="surface").pack(side="left")

    # ── embed canvas ──────────────────────────────────────────────────────
    slop_embed_canvas = styled(tk.Frame(slop_embed_body, bg="black",
                                        highlightthickness=1, highlightbackground=COL["border"]),
                               bg="surface")
    slop_embed_canvas.pack(fill="both", expand=True)
    slop_embed_canvas.bind("<Configure>", _slop_on_resize)

    # Placeholder text shown before Slopsmith is embedded
    _slop_placeholder = styled(tk.Label(slop_embed_canvas,
                                         text="▶  Launch & Embed Slopsmith to see the 3D highway here",
                                         font=("Segoe UI", 13)), fg="muted", bg="black")
    _slop_placeholder.place(relx=0.5, rely=0.5, anchor="center")

    # Auto-hide placeholder when embed is active
    def _slop_page_shown():
        if _slop_embed_state.get("hwnd", 0):
            _slop_placeholder.place_forget()
        else:
            _slop_placeholder.place(relx=0.5, rely=0.5, anchor="center")
    slop_embed_page.bind("<Visibility>", lambda e: root.after(100, _slop_page_shown))

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
    # PAGE: HELP
    # ══════════════════════════════════════════════════════════════════════
    help_page = styled(tk.Frame(page_container), bg="surface"); pages["help"] = help_page
    page_header(help_page, "How to Use RS Studio")

    help_scroll_outer = styled(tk.Frame(help_page), bg="surface")
    help_scroll_outer.pack(fill="both", expand=True)
    help_canvas = tk.Canvas(help_scroll_outer, highlightthickness=0, bd=0, bg=COL["surface"])
    help_vsb = ttk.Scrollbar(help_scroll_outer, orient="vertical", command=help_canvas.yview)
    help_canvas.configure(yscrollcommand=help_vsb.set)
    help_canvas.pack(side="left", fill="both", expand=True)
    help_vsb.pack(side="right", fill="y")
    help_inner = styled(tk.Frame(help_canvas), bg="surface")
    help_win = help_canvas.create_window((0, 0), window=help_inner, anchor="nw")
    help_inner.bind("<Configure>", lambda e: help_canvas.configure(
        scrollregion=(0, 0, e.width, e.height)))
    help_canvas.bind("<Configure>", lambda e: help_canvas.itemconfig(help_win, width=e.width))
    # Scroll handled by root-level _mouse_scroll which dispatches per page

    def _help_section(title, body_lines):
        sec = styled(tk.Frame(help_inner), bg="surface")
        sec.pack(fill="x", padx=48, pady=(0, 24))
        styled(tk.Label(sec, text=title, font=("Segoe UI Black", 13), anchor="w"),
               fg="accent", bg="surface").pack(fill="x", pady=(0, 6))
        styled(tk.Frame(sec, height=1), bg="border").pack(fill="x", pady=(0, 10))
        for line in body_lines:
            if line.startswith("•"):
                row = styled(tk.Frame(sec), bg="surface")
                row.pack(fill="x", pady=1)
                styled(tk.Label(row, text="•", font=("Segoe UI", 10), width=2, anchor="nw"),
                       fg="accent", bg="surface").pack(side="left", anchor="nw")
                styled(tk.Label(row, text=line[1:].strip(), font=("Segoe UI", 10),
                                wraplength=700, justify="left", anchor="nw"),
                       fg="fg", bg="surface").pack(side="left", fill="x", expand=True)
            elif line == "":
                tk.Frame(sec, height=6, bg=COL["surface"]).pack()
            else:
                styled(tk.Label(sec, text=line, font=("Segoe UI", 10),
                                wraplength=720, justify="left", anchor="w"),
                       fg="fg", bg="surface").pack(fill="x", pady=1)

    tk.Frame(help_inner, height=20, bg=COL["surface"]).pack()

    _help_section("Step 1 — Load a Guitar Pro File", [
        "RS Studio starts with a Guitar Pro (.gp / .gp5) file that contains your song's notes and tempo map.",
        "",
        "• Click  ＋ Start New Project  on the splash screen to get to the Main page.",
        "• Drag and drop a .gp file onto the GP File field, or click Browse to find it.",
        "• RS Studio will automatically read the title, artist, and BPM from the file.",
    ])

    _help_section("Step 2 — Auto-Fetch Audio & Artwork", [
        "You need an audio file (.ogg or .wav) and album art (.png / .jpg) to build a CDLC.",
        "",
        "• Click  Auto-Fetch  next to the Audio URL field. RS Studio will search YouTube for the song and download audio automatically using yt-dlp.",
        "• If the wrong song is found, paste a direct YouTube URL into the Audio URL box first.",
        "• Album art is fetched from MusicBrainz / Cover Art Archive automatically. You can also drag your own image onto the Art field.",
        "• The bottom player bar shows the loaded song — press  ▶  to preview the audio.",
    ])

    _help_section("Step 3 — Review Settings (Main Page)", [
        "Before building, check these key settings:",
        "",
        "• Title / Artist / Album / Year — filled automatically from the .gp file. Edit if needed.",
        "• Lead-in (seconds) — silent gap before notes start. Default 5s is fine for most songs.",
        "• Volume — negative dB value. Default -8 dB. Lower = quieter in-game.",
        "• Scroll Speed — how fast the note highway scrolls. Higher = faster. Tune in the Audio Editor.",
        "• Output Folder — where the project files are saved. Defaults to RS Studio Projects/ next to the app.",
        "• PSARC Output — optional: where the final .psarc file is copied after building.",
    ])

    _help_section("Step 4 — Tune Timing (Audio Editor)", [
        "If the notes feel early or late when you play, use the Audio Editor to fix sync.",
        "",
        "• The Sync Tuner shows BPM and a stretch slider. The GP file's tempo map drives note timing.",
        "• Use  Auto BPM  to detect BPM from the audio file if it differs from the GP file.",
        "• Stretch Audio to GP Length adjusts audio speed so it matches the note map exactly.",
        "• Click  ▶ Launch Desktop App  to open Slopsmith in your browser for a quick visual check.",
    ])

    _help_section("Step 5 — Build the CDLC", [
        "Click  ▶ Create CDLC  in the bottom-left of the sidebar.",
        "",
        "• RS Studio runs the full build pipeline: converts audio, generates .xml chart data, packages everything into a .psarc file.",
        "• Progress is logged on the Log page (≡ in the sidebar).",
        "• When done, a dialog shows the output path and Windows Explorer opens to the file.",
        "• The built project also appears in the splash screen's Recent Projects list next time.",
    ])

    _help_section("Step 6 — Verify in Slopsmith", [
        "Slopsmith is a Rocksmith-compatible gameplay engine you can use to see and hear your CDLC before loading it in the actual game. You need to build the CDLC (Step 5) first — Slopsmith reads the .psarc file.",
        "",
        "• Go to the  Slopsmith  tab in the sidebar.",
        "• Click  ▶ Launch & Embed Slopsmith — the app launches and embeds inside RS Studio after a few seconds.",
        "• In Slopsmith, open your .psarc file from the library.",
        "• Start a session and watch the 3D note highway. If notes are mis-timed, go back to the Audio Editor and adjust.",
        "• Click  ⤢ Detach to Window  to pop Slopsmith back out as its own window if needed.",
    ])

    _help_section("Step 7 — Install in Rocksmith", [
        "Once you're happy with the CDLC, copy the .psarc file into your Rocksmith DLC folder.",
        "",
        "• Default Rocksmith DLC path:  Steam\\steamapps\\common\\Rocksmith2014\\dlc\\",
        "• Launch Rocksmith 2014 Remastered — the song appears in Learn a Song.",
        "• If it doesn't show up, check that your game has the D3DX9 fix / CDLC enabler installed.",
    ])

    _help_section("Tips & Troubleshooting", [
        "• No audio after auto-fetch? Make sure yt-dlp.exe and ffmpeg.exe are in the same folder as the app.",
        "• Build failed? Check the Log page for the exact error. Common causes: missing packer.exe path, bad audio format, or GP file with unsupported features.",
        "• Slopsmith won't embed? Give it 10–20 seconds after clicking Launch. Click  ↺ Re-embed  if needed.",
        "• Wrong song fetched from YouTube? Paste a direct YouTube link into Audio URL before clicking Auto-Fetch.",
        "• Volume too loud in-game? Lower the Volume field (e.g. -12 instead of -8) and rebuild.",
        "• Change theme colors anytime via the  ◐ Theme  tab — your accent color is saved.",
    ])

    tk.Frame(help_inner, height=40, bg=COL["surface"]).pack()

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

        # Main modal dialog — frameless like the tools-download popup
        modal = tk.Toplevel(root)
        modal.transient(root)
        modal.overrideredirect(True)
        modal.configure(bg=COL["card"],
                        highlightthickness=1, highlightbackground=COL["border_hi"])
        w, h = 840, 480
        x = rx + max(0, (rw - w) // 2)
        y = ry + max(0, (rh - h) // 2)
        modal.geometry(f"{w}x{h}+{x}+{y}")
        modal.lift()
        modal.focus_set()
        modal.grab_set()

        # Keep modal centred on root when user moves/resizes the window
        _cfg_cbid = [None]
        def _reposition_inspector(_e=None):
            try:
                if not modal.winfo_exists():
                    return
                nx = root.winfo_rootx(); ny = root.winfo_rooty()
                nrw = root.winfo_width(); nrh = root.winfo_height()
                modal.geometry(f"{w}x{h}+{nx + max(0,(nrw-w)//2)}+{ny + max(0,(nrh-h)//2)}")
            except Exception: pass
        _cfg_cbid[0] = root.bind("<Configure>", _reposition_inspector, add="+")
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
            try:
                if _cfg_cbid[0]:
                    root.unbind("<Configure>", _cfg_cbid[0])
            except Exception: pass
            try: modal.grab_release()
            except Exception: pass
            try: modal.destroy()
            except Exception: pass
            # With overrideredirect=True, focus doesn't auto-return — force it back
            try: root.focus_force()
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
                def _dl_ytdlp_then_retry():
                    root.after(0, lambda: loading_lbl.configure(text="Downloading yt-dlp…"))
                    try:
                        here = os.path.dirname(os.path.abspath(__file__))
                        urllib.request.urlretrieve(
                            "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
                            os.path.join(here, "yt-dlp.exe"))
                        root.after(0, lambda: threading.Thread(target=fetch_info, daemon=True).start())
                    except Exception as ex:
                        root.after(0, lambda: loading_lbl.configure(
                            text=f"✗ yt-dlp download failed: {ex}", fg=COL["warn"]))
                root.after(0, lambda: loading_lbl.configure(
                    text="yt-dlp not found — downloading now…", fg=COL["accent"]))
                threading.Thread(target=_dl_ytdlp_then_retry, daemon=True).start()
                return
            target_url = audio_url
            if not target_url:
                cl_title = clean_title_for_api(title)
                cl_artist = clean_title_for_api(artist)
                # Try YouTube Music first (better album/year metadata), fall back to general YouTube
                root.after(0, lambda: loading_lbl.configure(text="Searching YouTube Music…"))
                ytm_url = f"ytmsearch1:{cl_artist} {cl_title}"
                yt_url  = f"ytsearch1:{cl_artist} {cl_title} lyric video"
                # Run yt-dlp with YouTube Music search; fall back to regular YouTube
                def _try_search(url):
                    cmd = [ytdlp, "--dump-json", "--no-warnings", "--no-playlist", url]
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, creationflags=CREATE_NO_WINDOW)
                    return [l for l in r.stdout.split('\n') if l.strip().startswith('{')]
                lines = _try_search(ytm_url)
                if not lines:
                    root.after(0, lambda: loading_lbl.configure(text="Trying YouTube…"))
                    lines = _try_search(yt_url)
                target_url = ytm_url  # used for webpage_url fallback
            else:
                root.after(0, lambda: loading_lbl.configure(text="Extracting metadata…"))
            try:
                if not target_url.startswith("ytm") and not target_url.startswith("yts"):
                    # Direct URL — run dump-json
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

                # Extract music metadata from yt-dlp (YouTube Music provides these)
                yt_album    = vdata.get("album", "") or ""
                yt_artist   = vdata.get("artist","") or vdata.get("uploader","") or ""
                yt_track    = vdata.get("track","") or ""
                yt_year_raw = vdata.get("release_year","") or ""
                if not yt_year_raw and vdata.get("release_date"):
                    yt_year_raw = str(vdata["release_date"])[:4]
                yt_year = str(yt_year_raw)[:4]

                # Try MusicBrainz for authoritative album + release year
                mb_album, mb_year = "", ""
                try:
                    root.after(0, lambda: loading_lbl.configure(text="Looking up release info…"))
                    mb_q = urllib.parse.quote(f'artist:"{artist}" recording:"{title}"')
                    mb_url = f"https://musicbrainz.org/ws/2/recording/?query={mb_q}&fmt=json&limit=5"
                    mb_req = urllib.request.Request(mb_url,
                        headers={"User-Agent": "gp2rs-studio/2.0 ( ynzerx@gmail.com )"})
                    with urllib.request.urlopen(mb_req, timeout=8) as r:
                        mb_data = json.loads(r.read().decode("utf-8"))
                    for rec in mb_data.get("recordings", []):
                        releases = rec.get("releases", [])
                        # Prefer official albums over singles/compilations
                        for rel in releases:
                            rg = rel.get("release-group", {})
                            rtype = rg.get("primary-type", "")
                            if rtype == "Album":
                                mb_album = rel.get("title", "")
                                mb_year  = (rel.get("date") or "")[:4]
                                break
                        if mb_album:
                            break
                        # Fallback: any release with a date
                        if not mb_album and releases:
                            mb_album = releases[0].get("title", "")
                            mb_year  = (releases[0].get("date") or "")[:4]
                            break
                except Exception:
                    pass  # MusicBrainz unreachable — use yt-dlp data

                # Merge: MusicBrainz wins if it found something
                final_album = mb_album or yt_album
                final_year  = mb_year  or yt_year

                modal_state["yt_meta"] = {
                    "album":  final_album.strip(),
                    "artist": yt_artist.strip(),
                    "track":  yt_track.strip(),
                    "year":   final_year,
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
                            # Fetch real album art from iTunes; fall back to YT thumbnail
                            if not vars_["art"].get().strip() and out_dir:
                                def _fetch_art_itunes(a=artist, t=title, d=out_dir, snap=img):
                                    def _simplify(s):
                                        return re.sub(r"[^a-z0-9]+", "", s.lower())
                                    try:
                                        term = urllib.parse.quote(f"{a} {t}")
                                        url  = (f"https://itunes.apple.com/search"
                                                f"?term={term}&entity=song&limit=5&media=music")
                                        req2 = urllib.request.Request(
                                            url, headers={"User-Agent": "gp2rs-studio/2.0"})
                                        with urllib.request.urlopen(req2, timeout=10) as r2:
                                            idata = json.loads(r2.read().decode("utf-8"))
                                        results = idata.get("results", [])
                                        art_url = ""
                                        tl_n = _simplify(t); al_n = _simplify(a)
                                        for res in results:
                                            tn = _simplify(res.get("trackName",""))
                                            an = _simplify(res.get("artistName",""))
                                            if (tl_n in tn or tn in tl_n) and (al_n in an or an in al_n):
                                                art_url = res.get("artworkUrl100","")
                                                break
                                        if not art_url and results:
                                            art_url = results[0].get("artworkUrl100","")
                                        if art_url:
                                            art_url = art_url.replace("100x100bb","600x600bb").replace("100x100","600x600")
                                            art_dest = os.path.join(d, "cover.jpg")
                                            req3 = urllib.request.Request(
                                                art_url, headers={"User-Agent": "Mozilla/5.0"})
                                            with urllib.request.urlopen(req3, timeout=10) as r3, \
                                                 open(art_dest, "wb") as f:
                                                f.write(r3.read())
                                            root.after(0, lambda p=art_dest: vars_["art"].set(p))
                                            root.after(0, lambda: log("  [art] ✓ iTunes album art downloaded"))
                                            return
                                    except Exception as ex:
                                        root.after(0, lambda: log(f"  [art] iTunes fetch failed: {ex}"))
                                    # Fallback: save YT thumbnail
                                    try:
                                        thumb_art = os.path.join(d, "cover_yt.jpg")
                                        snap.save(thumb_art, "JPEG", quality=90)
                                        root.after(0, lambda p=thumb_art: vars_["art"].set(p))
                                        root.after(0, lambda: log("  [art] saved YT thumbnail (iTunes not found)"))
                                    except Exception: pass
                                threading.Thread(target=_fetch_art_itunes, daemon=True).start()
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
                # Apply metadata from yt-dlp — always overwrite album/year since GP files
                # don't carry this info and yt-dlp (especially YouTube Music) is authoritative.
                import datetime as _dt2
                _this_year = str(_dt2.date.today().year)
                ym = modal_state.get("yt_meta", {})
                if ym.get("album"):
                    vars_["album"].set(ym["album"])
                if ym.get("year"):
                    vars_["year"].set(ym["year"])
                elif vars_["year"].get() in ("", _this_year):
                    pass  # keep current-year default rather than clearing
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

    # ── AUTOMAGIC DOWNLOAD ────────────────────────────────────────────────
    def _trigger_automagic(confirmed_url):
        """Download audio from confirmed_url via yt-dlp, set vars_["audio"] and preview."""
        out_dir = vars_["out"].get()
        if not out_dir or not os.path.isdir(out_dir):
            return messagebox.showerror("Missing Folder", "Please set an Output Folder before downloading.")

        ytdlp  = _find_exe("yt-dlp")
        ffmpeg = _find_exe("ffmpeg")
        if not ytdlp:
            return messagebox.showerror("Missing tool", "yt-dlp not found. Run Auto-Fetch again to re-download it.")

        # ── Progress card (no grab — download runs in background, user can switch windows) ──
        root.update_idletasks()
        rx = root.winfo_rootx(); ry = root.winfo_rooty()
        rw = root.winfo_width();  rh = root.winfo_height()

        dl_card = tk.Toplevel(root)
        dl_card.transient(root)
        dl_card.overrideredirect(True)
        dl_card.attributes("-topmost", True)
        dl_card.configure(bg=COL["card"], highlightthickness=1,
                          highlightbackground=COL["border_hi"])
        cw, ch = 400, 130
        dl_card.geometry(f"{cw}x{ch}+{rx+(rw-cw)//2}+{ry+(rh-ch)//2}")
        dl_card.lift()

        status_var2 = tk.StringVar(value="Starting download…")
        tk.Label(dl_card, text="⬇  Downloading Audio", font=("Segoe UI Semibold", 12),
                 bg=COL["card"], fg=COL["fg"]).pack(pady=(20, 6))
        tk.Label(dl_card, textvariable=status_var2, font=("Segoe UI", 9),
                 bg=COL["card"], fg=COL["muted"], wraplength=360).pack()

        def _safe_status(msg):
            """Update status only if the card still exists."""
            try:
                if dl_card.winfo_exists():
                    status_var2.set(msg)
            except Exception:
                pass

        def _close_overlay():
            try: dl_card.destroy()
            except Exception: pass

        def _dl_worker():
            try:
                dest_dir = out_dir
                out_tmpl = os.path.join(dest_dir, "ytdlp_audio.%(ext)s")
                # Remove stale ytdlp_audio.* files
                for f in os.listdir(dest_dir):
                    if f.startswith("ytdlp_audio"):
                        try: os.remove(os.path.join(dest_dir, f))
                        except Exception: pass

                root.after(0, lambda: _safe_status("Running yt-dlp…"))
                cmd = [ytdlp, "-x", "--audio-format", "wav",
                       "--no-playlist", "--no-warnings"]
                # Tell yt-dlp where bundled ffmpeg lives so wav conversion works
                _ff = _find_exe("ffmpeg")
                if _ff:
                    cmd += ["--ffmpeg-location", os.path.dirname(_ff)]
                cmd += ["-o", out_tmpl, confirmed_url]
                proc = subprocess.run(cmd, capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=300)

                # Find the downloaded file — prefer .wav, accept any ytdlp_audio file as fallback
                raw_audio = None
                for f in sorted(os.listdir(dest_dir)):
                    if f.startswith("ytdlp_audio") and f.endswith(".wav"):
                        raw_audio = os.path.join(dest_dir, f)
                        break
                if not raw_audio:
                    for f in sorted(os.listdir(dest_dir)):
                        if f.startswith("ytdlp_audio") and not f.endswith(".part"):
                            raw_audio = os.path.join(dest_dir, f)
                            break

                if not raw_audio or not os.path.exists(raw_audio):
                    err_detail = (proc.stderr or b"").decode("utf-8", errors="replace")[-600:]
                    log(f"  [fetch] yt-dlp stderr: {err_detail}")
                    # Check if yt-dlp saved a non-wav file and convert it
                    any_audio = None
                    for f in sorted(os.listdir(dest_dir)):
                        if f.startswith("ytdlp_audio") and not f.endswith(".part"):
                            any_audio = os.path.join(dest_dir, f)
                            break
                    if any_audio and ffmpeg:
                        root.after(0, lambda: _safe_status("Converting audio to WAV..."))
                        wav_out = os.path.join(dest_dir, "ytdlp_audio.wav")
                        conv = subprocess.run(
                            [ffmpeg, "-y", "-i", any_audio, "-ar", "48000",
                             "-ac", "2", "-sample_fmt", "s16", wav_out],
                            capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=120)
                        if os.path.exists(wav_out):
                            raw_audio = wav_out
                            log(f"  [fetch] Converted to WAV: {wav_out}")
                        else:
                            log(f"  [fetch] ffmpeg convert stderr: {conv.stderr.decode('utf-8','replace')[-300:]}")
                    if not raw_audio or not os.path.exists(raw_audio):
                        raise Exception(f"yt-dlp finished but no audio file found.\n{err_detail}")

                root.after(0, lambda: vars_["audio"].set(raw_audio))
                log(f"  [fetch] Audio saved: {raw_audio}")

                # Generate preview clip
                if ffmpeg:
                    root.after(0, lambda: _safe_status("Generating preview clip…"))
                    try:
                        prev_path = os.path.join(dest_dir, "ytdlp_preview.wav")
                        subprocess.run(
                            [ffmpeg, "-y", "-i", raw_audio,
                             "-ss", "60", "-t", "30",
                             "-acodec", "pcm_s16le", prev_path],
                            capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=60)
                        if os.path.exists(prev_path):
                            root.after(0, lambda: vars_["preview_audio"].set(prev_path))
                            log(f"  [fetch] Preview saved: {prev_path}")
                    except Exception as pe:
                        log(f"  [fetch] Preview generation failed: {pe}")

                # Fetch synced lyrics from lrclib.net
                try:
                    o_artist = vars_["artist"].get().strip()
                    o_title  = vars_["title"].get().strip()
                    root.after(0, lambda: _safe_status("Fetching lyrics…"))
                    lrc_url = (
                        f"https://lrclib.net/api/get"
                        f"?artist_name={urllib.parse.quote(o_artist)}"
                        f"&track_name={urllib.parse.quote(o_title)}"
                    )
                    req = urllib.request.Request(
                        lrc_url, headers={"User-Agent": "gp2rs-studio/2.0"})
                    with urllib.request.urlopen(req, timeout=10) as r:
                        lrc_data = json.loads(r.read().decode("utf-8"))
                    if lrc_data.get("syncedLyrics"):
                        lrc_dest = os.path.join(dest_dir, "lyrics.lrc")
                        with open(lrc_dest, "w", encoding="utf-8") as f:
                            f.write(lrc_data["syncedLyrics"])
                        root.after(0, lambda p=lrc_dest: vars_["lyrics"].set(p))
                        log(f"  [fetch] Synced lyrics saved: {lrc_dest}")
                    else:
                        log("  [fetch] No synced lyrics found on lrclib.net")
                except Exception as le:
                    log(f"  [fetch] Lyrics fetch failed: {le}")

                root.after(0, lambda: _safe_status("✓ Done!"))
                root.after(0, _update_player_ui)
                root.after(800, _close_overlay)
                root.after(0, lambda: log("  [fetch] Auto-fetch complete."))
            except Exception as e:
                root.after(0, lambda: _safe_status(f"✗ {e}"))
                root.after(0, lambda: log(f"  [fetch] Error: {e}"))
                root.after(3000, _close_overlay)

        threading.Thread(target=_dl_worker, daemon=True).start()

    # ══════════════════════════════════════════════════════════════════════
    # SPOTIFY BOTTOM PLAYER BAR
    # ══════════════════════════════════════════════════════════════════════
    player_bar = styled(tk.Frame(root, height=114), bg="card2")
    player_bar.grid(row=1, column=0, columnspan=2, sticky="ew")
    player_bar.grid_propagate(False)

    # ── Timeline scrubber ──────────────────────────────────────────────────
    tl_canvas = tk.Canvas(player_bar, height=22, bg=COL["card2"],
                          highlightthickness=0, cursor="hand2")
    tl_canvas.place(x=0, y=0, relwidth=1.0, height=22)
    # thin divider line below scrubber
    tl_div = tk.Frame(player_bar, height=1, bg=COL["border"])
    tl_div.place(x=0, y=22, relwidth=1.0, height=1)

    _tl = {"dur": 0.0, "t0": None, "offset": 0.0, "kind": None, "after_id": None}

    def _tl_get_duration(path):
        ffmpeg = _find_exe("ffmpeg")
        if not ffmpeg: return 0.0
        try:
            r = subprocess.run([ffmpeg, "-i", path], capture_output=True, text=True,
                               timeout=10, creationflags=CREATE_NO_WINDOW)
            m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", r.stderr)
            if m:
                h, mi, s = m.groups()
                return int(h)*3600 + int(mi)*60 + float(s)
        except Exception: pass
        return 0.0

    def _tl_fmt(secs):
        secs = max(0, int(secs))
        return f"{secs//60}:{secs%60:02d}"

    def _tl_draw():
        w = tl_canvas.winfo_width() or 1
        tl_canvas.delete("all")
        PAD = 52  # pixel room for time labels on each side
        dur = _tl["dur"]
        elapsed = 0.0
        if dur > 0:
            if _tl["t0"] is not None:
                elapsed = min(time.time() - _tl["t0"] + _tl["offset"], dur)
            else:
                elapsed = min(_tl["offset"], dur)  # show saved position when paused
        # Track background (lighter than card2 so it's visible)
        tl_canvas.create_rectangle(PAD, 8, w - PAD, 14,
                                   fill=COL["border_hi"], outline="", tags="track")
        if dur > 0:
            frac = elapsed / dur
            fill_x = PAD + frac * (w - PAD * 2)
            # Progress fill
            tl_canvas.create_rectangle(PAD, 8, fill_x, 14,
                                       fill=COL["accent"], outline="", tags="fill")
            # Playhead circle
            tl_canvas.create_oval(fill_x - 6, 5, fill_x + 6, 17,
                                  fill=COL["accent_hi"], outline="", tags="head")
        # Time labels
        tl_canvas.create_text(PAD - 4, 11, text=_tl_fmt(elapsed), anchor="e",
                              fill=COL["muted"], font=("Segoe UI", 8))
        tl_canvas.create_text(w - PAD + 4, 11, text=_tl_fmt(dur), anchor="w",
                              fill=COL["dim"], font=("Segoe UI", 8))

    def _tl_tick():
        if _tl["t0"] is not None:
            _tl_draw()
            _tl["after_id"] = root.after(250, _tl_tick)

    def _tl_seek(event):
        dur = _tl["dur"]
        if not dur: return
        w = tl_canvas.winfo_width()
        PAD = 52
        track_px = max(1, w - PAD * 2)
        frac = max(0.0, min(1.0, (event.x - PAD) / track_px))
        seek_to = frac * dur
        _tl["gen"] = _tl.get("gen", 0) + 1
        gen = _tl["gen"]
        _tl["offset"] = seek_to

        # Determine what audio file to use — fall back to audio var if kind not set
        kind = _tl.get("kind") or "audio"
        path = vars_.get(kind, tk.StringVar()).get() if kind else ""
        was_playing = bool(_media_procs.get("audio"))

        # Kill any running playback
        for key, proc in list(_media_procs.items()):
            if proc:
                try:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                   creationflags=CREATE_NO_WINDOW)
                except Exception: pass
                _media_procs[key] = None

        if was_playing and path and os.path.exists(path):
            # Was playing — restart at new position immediately
            _tl["t0"] = time.time()
            ffplay_ = _find_exe("ffplay")
            if ffplay_:
                def _rerun(fp=ffplay_, p=path, st=seek_to, g=gen):
                    cmd = [fp, "-nodisp", "-autoexit", "-ss", str(st),
                           "-volume", str(_player_vol[0]), p]
                    proc = subprocess.Popen(cmd, creationflags=CREATE_NO_WINDOW)
                    _media_procs["audio"] = proc
                    proc.wait()
                    _media_procs["audio"] = None
                    if _tl.get("gen") == g:
                        _tl["t0"] = None
                        root.after(0, _tl_draw)
                        root.after(0, lambda: btn_play_pause.configure(text="▶"))
                threading.Thread(target=_rerun, daemon=True).start()
                root.after(0, lambda: btn_play_pause.configure(text="⏸"))
        else:
            # Paused or stopped — just update the visual position, don't auto-play
            _tl["t0"] = None
        _tl_draw()

    tl_canvas.bind("<Button-1>", _tl_seek)
    # Draw empty bar once canvas is realized
    tl_canvas.bind("<Configure>", lambda e: _tl_draw())

    # NOT registered with styled() — we manage its look manually so refresh_theme
    # never overwrites the image we set in _update_player_ui
    art_lbl = tk.Label(player_bar, text="🎵", font=("Segoe UI", 24),
                       bg=COL["card2"], fg=COL["muted"])
    art_lbl.place(x=16, y=36, width=62, height=62)

    np_title = styled(tk.Label(player_bar, text="No Song Loaded", font=("Segoe UI Semibold", 11)), bg="card2", fg="fg")
    np_title.place(x=92, y=46)
    np_artist = styled(tk.Label(player_bar, text="Load a GP file to begin.", font=("Segoe UI", 9)), bg="card2", fg="muted")
    np_artist.place(x=92, y=68)

    center_frame = styled(tk.Frame(player_bar), bg="card2")
    center_frame.place(relx=0.5, y=67, anchor="center")

    # Shared media process tracker — keyed by "audio" or "video"
    _media_procs    = {"audio": None, "video": None}
    _player_vol     = [80]   # 0-100, used by all ffplay launches
    _ffplay_dl_lock = threading.Lock()  # prevents simultaneous ffplay download threads

    def _stop_audio_proc():
        """Kill just the audio ffplay process without resetting UI."""
        proc = _media_procs.get("audio")
        if proc:
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                   creationflags=CREATE_NO_WINDOW)
                else:
                    proc.kill()
            except Exception: pass
            _media_procs["audio"] = None

    def _stop_all_media():
        """Kill every active ffplay process and reset UI."""
        _stop_audio_proc()
        proc = _media_procs.get("video")
        if proc:
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                   creationflags=CREATE_NO_WINDOW)
                else:
                    proc.kill()
            except Exception: pass
            _media_procs["video"] = None
        try:
            root.after(0, lambda: main_vid_play_btn.configure(text="▶ Play Video Preview"))
        except Exception: pass
        _tl["t0"] = None
        _tl["offset"] = 0.0
        root.after(0, _tl_draw)
        root.after(0, lambda: btn_play_pause.configure(text="▶"))

    def _play_media(kind="audio"):
        """Play/pause toggle. kind='audio' plays the full track."""
        path = vars_[kind].get()
        if not path or not os.path.exists(path):
            return messagebox.showwarning("No Audio",
                "No audio file loaded. Load a .gp file and auto-fetch audio first.")
        ffplay = _find_exe("ffplay")
        if not ffplay:
            # ffplay missing — download silently; lock prevents parallel downloads
            def _silent_install():
                if not _ffplay_dl_lock.acquire(blocking=False):
                    root.after(0, lambda: log("  [ffplay] Download already in progress — press ▶ again when done"))
                    return
                try:
                    here = os.path.dirname(os.path.abspath(__file__))
                    zip_path = os.path.join(here, "_ffmpeg_tmp.zip")
                    root.after(0, lambda: log("  [ffplay] Downloading ffplay… (one-time, ~75 MB)"))
                    urllib.request.urlretrieve(
                        "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
                        "ffmpeg-master-latest-win64-gpl.zip", zip_path)
                    root.after(0, lambda: log("  [ffplay] Extracting ffplay…"))
                    with zipfile.ZipFile(zip_path, 'r') as z:
                        for info in z.infolist():
                            if info.filename.endswith("ffmpeg.exe"):
                                with z.open(info) as zf, \
                                     open(os.path.join(here, "ffmpeg.exe"), 'wb') as f:
                                    shutil.copyfileobj(zf, f)
                            elif info.filename.endswith("ffplay.exe"):
                                with z.open(info) as zf, \
                                     open(os.path.join(here, "ffplay.exe"), 'wb') as f:
                                    shutil.copyfileobj(zf, f)
                    try: os.remove(zip_path)
                    except Exception: pass
                    root.after(0, lambda: log("  [ffplay] ✓ Installed — press ▶ to play"))
                except Exception as ex:
                    root.after(0, lambda: log(f"  [ffplay] Download failed: {ex}"))
                finally:
                    _ffplay_dl_lock.release()
            threading.Thread(target=_silent_install, daemon=True).start()
            return

        # ── PAUSE: audio is running → freeze position, kill proc ──────────
        if _media_procs["audio"] and _tl.get("t0") is not None:
            elapsed = min(time.time() - _tl["t0"] + _tl["offset"], _tl["dur"] or 1e9)
            _tl["offset"] = elapsed
            _tl["t0"] = None
            _stop_audio_proc()
            root.after(0, lambda: btn_play_pause.configure(text="▶"))
            root.after(0, _tl_draw)
            return

        # ── PLAY / RESUME ─────────────────────────────────────────────────
        start_at = _tl["offset"] if (_tl.get("t0") is None and _tl["offset"] > 0
                                      and _tl.get("kind") == kind) else 0.0
        _stop_all_media()
        # Show loading state immediately so user knows the click registered
        root.after(0, lambda: btn_play_pause.configure(text="⌛"))
        _tl["kind"]   = kind
        _tl["offset"] = start_at
        _tl["t0"]     = time.time()
        # Bump generation so seek/volume threads don't clobber each other
        _tl["gen"] = _tl.get("gen", 0) + 1
        _run_gen = _tl["gen"]

        def _load_dur(p=path):
            d = _tl_get_duration(p)
            _tl["dur"] = d
            root.after(0, _tl_draw)
        threading.Thread(target=_load_dur, daemon=True).start()
        root.after(0, _tl_tick)

        def _run(st=start_at, p=path, g=_run_gen):
            try:
                cmd = [ffplay, "-nodisp", "-autoexit",
                       "-ss", str(st), "-volume", str(_player_vol[0]), p]
                proc = subprocess.Popen(cmd, creationflags=CREATE_NO_WINDOW)
                _media_procs["audio"] = proc
                # Process launched — audio is starting, switch to pause icon
                root.after(0, lambda: btn_play_pause.configure(text="⏸"))
                proc.wait()
            except Exception as e:
                root.after(0, lambda: log(f"Playback failed: {e}"))
            finally:
                _media_procs["audio"] = None
                # Only reset UI if no newer seek/volume-restart has taken over
                if _tl.get("gen") == g:
                    _tl["t0"] = None
                    _tl["offset"] = 0.0
                    root.after(0, _tl_draw)
                    root.after(0, lambda: btn_play_pause.configure(text="▶"))
        threading.Thread(target=_run, daemon=True).start()

    # ── Player controls ───────────────────────────────────────────────────
    # highlightthickness=0 removes the focus rectangle that causes the square
    _pbkw = dict(font=("Segoe UI", 13), cursor="hand2",
                 padx=16, pady=5, bd=0, relief="flat",
                 highlightthickness=0, highlightbackground=COL["card2"],
                 highlightcolor=COL["card2"])

    btn_play_pause = tk.Button(center_frame, text="▶",
                               command=lambda: _play_media("audio"), **_pbkw)
    styled(btn_play_pause, bg="surface", fg="fg", activebackground="border_hi")
    btn_play_pause.pack(side="left", padx=4)
    # Prevent Tk from drawing focus rectangle when button receives keyboard focus
    btn_play_pause.bind("<FocusIn>",  lambda e: btn_play_pause.configure(highlightthickness=0))
    btn_play_pause.bind("<FocusOut>", lambda e: btn_play_pause.configure(highlightthickness=0))

    btn_stop_all = tk.Button(center_frame, text="■",
                             command=_stop_all_media, **_pbkw)
    styled(btn_stop_all, bg="surface", fg="fg", activebackground="border_hi")
    btn_stop_all.pack(side="left", padx=4)
    btn_stop_all.bind("<FocusIn>",  lambda e: btn_stop_all.configure(highlightthickness=0))
    btn_stop_all.bind("<FocusOut>", lambda e: btn_stop_all.configure(highlightthickness=0))

    # ── Volume slider — click anywhere to jump, release restarts if playing ─
    def _vol_restart_if_playing():
        """Restart ffplay at current position so new volume takes effect."""
        dur = _tl.get("dur", 0)
        if not dur:
            return
        kind = _tl.get("kind") or "audio"
        path = vars_.get(kind, tk.StringVar()).get() if kind else ""
        if not path or not os.path.exists(path):
            return
        # Only restart if audio is actively playing
        if not _media_procs.get("audio"):
            return
        t0 = _tl.get("t0")
        elapsed = (min(time.time() - t0 + _tl["offset"], dur)
                   if t0 is not None else _tl["offset"])
        if elapsed <= 0:
            return
        _tl["offset"] = elapsed
        _tl["gen"] = _tl.get("gen", 0) + 1
        gen = _tl["gen"]
        _tl["t0"] = time.time()
        ffplay_ = _find_exe("ffplay")
        if not ffplay_:
            return
        _stop_audio_proc()
        def _vrestart(fp=ffplay_, p=path, st=elapsed, g=gen):
            cmd = [fp, "-nodisp", "-autoexit", "-ss", str(st),
                   "-volume", str(_player_vol[0]), p]
            proc = subprocess.Popen(cmd, creationflags=CREATE_NO_WINDOW)
            _media_procs["audio"] = proc
            proc.wait()
            _media_procs["audio"] = None
            if _tl.get("gen") == g:
                _tl["t0"] = None
                _tl["offset"] = 0.0
                root.after(0, _tl_draw)
                root.after(0, lambda: btn_play_pause.configure(text="▶"))
        threading.Thread(target=_vrestart, daemon=True).start()
        root.after(0, lambda: btn_play_pause.configure(text="⏸"))

    vol_frame = styled(tk.Frame(player_bar), bg="card2")
    vol_frame.place(relx=0.86, y=67, anchor="center")
    tk.Label(vol_frame, text="🔊", font=("Segoe UI", 10),
             bg=COL["card2"], fg=COL["muted"]).pack(side="left")
    vol_slider = tk.Scale(vol_frame, from_=0, to=100, orient="horizontal",
                          length=120, showvalue=False, resolution=1,
                          bg=COL["card2"], fg=COL["muted"],
                          troughcolor=COL["border_hi"],
                          activebackground=COL["accent"],
                          highlightthickness=0, bd=0, cursor="hand2",
                          command=lambda v: _player_vol.__setitem__(0, int(float(v))))
    vol_slider.set(80)
    vol_slider.pack(side="left", padx=(4, 0))

    # Click or drag anywhere on trough → jump/drag to that value immediately

    # Click or drag anywhere on trough → jump/drag to that value immediately
    def _vol_jump(e):
        pad = 8
        w = vol_slider.winfo_width() - pad * 2
        frac = max(0.0, min(1.0, (e.x - pad) / max(1, w)))
        vol_slider.set(int(frac * 100))
        # do NOT return "break" — lets Tk establish the mouse grab for dragging

    _vol_debounce_id = [None]

    def _vol_debounced_restart(e=None):
        if _vol_debounce_id[0]:
            try: root.after_cancel(_vol_debounce_id[0])
            except Exception: pass
        _vol_debounce_id[0] = root.after(280, _vol_restart_if_playing)

    vol_slider.bind("<Button-1>", _vol_jump)
    vol_slider.bind("<B1-Motion>", lambda e: (_vol_jump(e), _vol_debounced_restart(e)))
    vol_slider.bind("<ButtonRelease-1>", lambda e: _vol_debounced_restart(e))

    # status_lbl removed — no longer shown in player bar

    # ══ PLAYER UI UPDATE ════════════════════════════════════════════════════════════════════════════
    def _update_player_ui():
        """Refresh the bottom player bar based on the current audio file and metadata."""
        path = vars_["audio"].get().strip()
        if path and os.path.exists(path):
            dur = _tl_get_duration(path)
            _tl["dur"] = dur
            _tl["kind"] = "audio"
        else:
            _tl["dur"] = 0.0
            _tl["kind"] = None
        _tl["t0"] = None
        _tl["offset"] = 0.0
        _tl_draw()
        # Sync bottom bar text with current metadata
        t   = vars_["title"].get().strip()  or "No Song Loaded"
        a   = vars_["artist"].get().strip()
        alb = vars_["album"].get().strip()
        sub = " — ".join(filter(None, [a, alb])) or "Load a GP file to begin."
        np_title.configure(text=t)
        np_artist.configure(text=sub)
        # Load album art thumbnail
        art_path = vars_["art"].get().strip()
        if art_path and os.path.exists(art_path) and HAS_PIL:
            try:
                img = Image.open(art_path).convert("RGB")
                img = img.resize((62, 62), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                art_lbl.configure(image=photo, text="")
                art_lbl._photo = photo  # keep reference alive
            except Exception:
                art_lbl.configure(image="", text="🎵")
        else:
            art_lbl.configure(image="", text="🎵")

    # Refresh player bar whenever art path changes (e.g. after auto-fetch)
    vars_["art"].trace_add("write", lambda *_: root.after(50, _update_player_ui))

    # ══ BPM FROM GP ═════════════════════════════════════════════════════════════════════════════════
    def _set_bpm_from_gp(path):
        """Detect BPM from GP file and set vars_['bpm']. Returns True if successful."""
        bpm = detect_gp_bpm(path)
        if bpm:
            vars_["bpm"].set(str(round(bpm, 2)))
            return True
        return False

    # ══ BPM RE-DETECT ═══════════════════════════════════════════════════════════════════════════════
    def _redetect_bpm():
        path = vars_["gp"].get().strip()
        if not path or not os.path.exists(path):
            return messagebox.showwarning("No GP file", "Load a Guitar Pro file first.")
        if not _set_bpm_from_gp(path):
            messagebox.showinfo("BPM", "Couldn't detect a tempo automatically — enter it manually.")

    # ══ LOAD GP ════════════════════════════════════════════════════════════════════════════════════════
    def load_gp(path, _keep_media=False):
        vars_["gp"].set(path)
        startup_overlay.place_forget()
        for w in arr_grid.winfo_children(): w.destroy()
        track_rows.clear()
        if not _keep_media:
            # Fresh load — clear any leftover media from a previous project
            for _k in ("audio", "audio_url", "preview_audio", "art", "lyrics"):
                vars_[_k].set("")
        try:
            gp = gp2rs.GPSong(path)
        except Exception as e:
            return messagebox.showerror("Can't read file", f"Not a valid GP7/8 .gp file:\n{e}")
        _gp_ref[0] = gp
        title2, artist2 = gp.title, gp.artist
        m = re.match(r"^(.*)\s+by\s+(.+)$", title2, re.I)
        if m: title2, artist2 = m.group(1).strip(), m.group(2).strip()
        vars_["title"].set(title2); vars_["artist"].set(artist2)
        gp_album = gp.album.strip()
        vars_["album"].set(gp_album if gp_album else "Unknown Album")
        import datetime as _dt
        _cur_year = str(_dt.date.today().year)
        if not vars_["year"].get() or vars_["year"].get() == _cur_year:
            vars_["year"].set(_cur_year)
        if not _set_bpm_from_gp(path):
            if not vars_["bpm"].get().strip():
                vars_["bpm"].set("120")
            log("  [tempo] Could not auto-detect BPM — edit manually if needed")
        _save_project_to_history(
            vars_["title"].get(), vars_["artist"].get(), path,
            None, vars_["art"].get() or None)
        if not vars_["out"].get():
            vars_["out"].set(os.path.dirname(os.path.abspath(path)))
        _update_player_ui()
        _existing_audio = vars_["audio"].get().strip()
        if not _existing_audio or not os.path.exists(_existing_audio):
            root.after(300, _show_media_inspector)
        headers = ["", "Track", "Arrangement", "Tone preset"]
        widths   = [3, 28, 12, 22]
        for ci, (h, wd) in enumerate(zip(headers, widths)):
            styled(tk.Label(arr_grid, text=h, font=("Segoe UI Semibold", 8)),
                   fg="muted", bg="card").grid(row=0, column=ci, sticky="w",
                   padx=6, pady=(0, 4), ipadx=wd)
        guitars = 0
        for i in range(len(gp.tracks)):
            kind = gp.track_kind(i)
            if kind is None:
                continue
            name = " ".join((gp.tracks[i].findtext("Name") or f"Track {i}").split())
            tuning_str = gp.tuning(i)
            preset = auto_preset(name)
            if kind == "bass":
                arr, tones = "Bass", BASS_PRESETS
                if preset not in tones:
                    preset = "Bass - Rock"
            else:
                arr, tones = ("Lead" if guitars == 0 else "Rhythm"), GUITAR_PRESETS
                if preset not in tones:
                    preset = "Distortion - JCM800" if guitars == 0 else "Crunch - Orange"
                guitars += 1
            row = {"index": i, "var_include": tk.BooleanVar(value=True),
                   "var_arr": tk.StringVar(value=arr),
                   "var_tone": tk.StringVar(value=preset)}
            r = len(track_rows) + 1
            tk.Checkbutton(arr_grid, variable=row["var_include"],
                           bg=COL["card"], activebackground=COL["card"],
                           fg=COL["fg"], selectcolor=COL["field"],
                           relief="flat", bd=0).grid(row=r, column=0, padx=6, pady=3)
            styled(tk.Label(arr_grid, text=f"{name}  ({tuning_str})",
                            font=("Segoe UI", 9), anchor="w"),
                   fg="fg", bg="card").grid(row=r, column=1, sticky="w", padx=6, pady=3)
            ttk.Combobox(arr_grid, textvariable=row["var_arr"], width=9,
                         state="readonly",
                         values=["Lead", "Rhythm", "Bass", "Skip"]).grid(
                row=r, column=2, padx=6, pady=3)
            ttk.Combobox(arr_grid, textvariable=row["var_tone"], width=22,
                         state="readonly", values=tones).grid(
                row=r, column=3, padx=6, pady=3)
            track_rows.append(row)

        # Deduplicate arrangement types so no two active tracks share one
        _used_arrs = {}
        for row in track_rows:
            a = row["var_arr"].get()
            if a == "Skip":
                continue
            if a in _used_arrs:
                # Find the first free slot; if all three taken, set to Skip
                for _cand in ("Lead", "Rhythm", "Bass"):
                    if _cand not in _used_arrs:
                        row["var_arr"].set(_cand); a = _cand; break
                else:
                    row["var_arr"].set("Skip"); a = "Skip"
            if a != "Skip":
                _used_arrs[a] = True

        # Refresh the Audio Editor's right-hand track panel
        root.after(50, _build_track_panel)

    # BUILD
    def do_build():
        if not vars_["gp"].get():
            return messagebox.showwarning("Missing", "Pick a .gp file first.")
        if not vars_["out"].get():
            return messagebox.showwarning("Missing", "Pick a Project Folder first.")
        arrs = [r["var_arr"].get() for r in track_rows
                if r["var_include"].get() and r["var_arr"].get() != "Skip"]
        if len(arrs) != len(set(arrs)):
            return messagebox.showwarning(
                "Duplicate arrangement",
                "Two tracks share the same arrangement type.\n"
                "Make them unique or set one to Skip.")
        btn_rebuild.configure(state="disabled", text="Building\u2026")
        o = SimpleNamespace(
            gp_path       = vars_["gp"].get(),
            lyrics_path   = vars_["lyrics"].get() or None,
            audio_path    = vars_["audio"].get() or None,
            preview_path  = vars_["preview_audio"].get() or None,
            audio_url     = vars_["audio_url"].get().strip() or None,
            art_path      = vars_["art"].get() or None,
            out_dir       = vars_["out"].get(),
            psarc_dest    = vars_["psarc_out"].get().strip() or None,
            title         = vars_["title"].get() or "Unknown",
            artist        = vars_["artist"].get() or "Unknown",
            album         = vars_["album"].get(),
            year          = vars_["year"].get(),
            leadin        = float(vars_["leadin"].get() or 5),
            volume        = float(vars_["volume"].get() or -8),
            scroll_speed  = float(vars_["scroll_speed"].get() or 1.3),
            pitch         = float(vars_["pitch"].get() or 440.0),
            appid         = vars_["appid"].get() or "248750",
            make_psarc    = bool(psarc_var.get()),
            packer_path   = vars_["packer"].get() or None,
            cst_dir       = vars_["cst"].get() or None,
            tracks        = [
                {"index": r["index"], "include": r["var_include"].get(),
                 "arr": r["var_arr"].get(), "tone_label": r["var_tone"].get()}
                for r in track_rows
            ],
        )
        # ── Progress card ─────────────────────────────────────────────────────
        root.update_idletasks()
        rx = root.winfo_rootx(); ry = root.winfo_rooty()
        rw = root.winfo_width();  rh = root.winfo_height()
        bld_card = tk.Toplevel(root)
        bld_card.transient(root)
        bld_card.overrideredirect(True)
        bld_card.attributes("-topmost", True)
        bld_card.configure(bg=COL["card"], highlightthickness=1,
                           highlightbackground=COL["border_hi"])
        cw, ch = 440, 150
        bld_card.geometry(f"{cw}x{ch}+{rx+(rw-cw)//2}+{ry+(rh-ch)//2}")
        tk.Label(bld_card, text="\U0001f528  Building CDLC",
                 font=("Segoe UI Semibold", 13), bg=COL["card"], fg=COL["fg"]
                 ).pack(pady=(22, 6))
        bld_status_var = tk.StringVar(value="Preparing\u2026")
        tk.Label(bld_card, textvariable=bld_status_var, font=("Segoe UI", 9),
                 bg=COL["card"], fg=COL["muted"], wraplength=400).pack()
        try:
            pb = ttk.Progressbar(bld_card, mode="indeterminate", length=380)
            pb.pack(pady=(10, 0)); pb.start(10)
        except Exception: pb = None

        def _safe_bld(msg):
            try:
                if bld_card.winfo_exists(): bld_status_var.set(msg)
            except Exception: pass

        def _close_bld():
            try:
                if pb: pb.stop()
                bld_card.destroy()
            except Exception: pass

        def _bld_log(msg):
            log(msg)
            if msg.strip():
                root.after(0, lambda m=msg.strip(): _safe_bld(m[:90]))

        def worker():
            try:
                res   = build_project(o, log=_bld_log)
                pdir  = res["proj_dir"]
                psarc = res.get("psarc")
                if psarc and o.psarc_dest and os.path.isdir(o.psarc_dest):
                    import shutil as _sh
                    dest = os.path.join(o.psarc_dest, os.path.basename(psarc))
                    try:
                        _sh.copy2(psarc, dest)
                        log(f"  [psarc] Copied to PSARC folder: {dest}")
                        psarc = dest
                    except Exception as ex:
                        log(f"  [psarc] Copy to PSARC folder failed: {ex}")
                if psarc and os.path.exists(psarc):
                    _save_project_to_history(
                        o.title, o.artist, o.gp_path, psarc,
                        vars_["art"].get() or None)
                    try:
                        subprocess.Popen(
                            ["explorer", "/select,", os.path.abspath(psarc)],
                            creationflags=CREATE_NO_WINDOW)
                    except Exception:
                        pass
                    def _done(p=psarc):
                        _close_bld()
                        root.after(50, lambda: show_page("slopsmith"))
                        if not _slop_embed_state.get("hwnd"):
                            root.after(200, _slop_embed_launch)
                    root.after(0, _done)
                elif res.get("packer_failed"):
                    tail2 = "\n".join(
                        (res.get("packer_output") or "").splitlines()[-15:])
                    msg = (f"Project files created, but packer.exe failed.\n\n"
                           f"Error:\n{tail2}\n\n(Project: {pdir})")
                    root.after(0, lambda: _safe_bld("Packer failed \u2014 see Log"))
                    root.after(0, lambda: messagebox.showerror("psarc build failed", msg))
                    root.after(3000, _close_bld)
                else:
                    root.after(0, lambda: _safe_bld("Built (no .psarc) \u2014 check Log"))
                    root.after(3000, _close_bld)
            except Exception as e:
                root.after(0, lambda: log("BUILD ERROR: " + str(e)))
                root.after(0, lambda: log(traceback.format_exc()))
                root.after(0, lambda: messagebox.showerror("Build failed", str(e)))
                root.after(0, _close_bld)
            finally:
                root.after(0, lambda: btn_rebuild.configure(
                    state="normal", text="\u25b6  Build CDLC"))
        threading.Thread(target=worker, daemon=True).start()

    # Show the startup / recent-projects splash on launch
    show_page("main")
    root.after(120, _show_startup_overlay)
    root.mainloop()

if __name__ == "__main__":
    run_gui()
