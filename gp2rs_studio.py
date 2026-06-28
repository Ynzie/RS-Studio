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
    # Sliders (volume, BPM scale, waveform zoom) — dark groove + accent handle to
    # match the app instead of the default washed-out clam look.
    for _sc in ("Horizontal.TScale", "Vertical.TScale"):
        style.configure(_sc, background=COL["accent"], troughcolor=COL["field"],
                        borderwidth=0, lightcolor=COL["accent"], darkcolor=COL["accent"])
        style.map(_sc, background=[("active", COL["accent_hi"]), ("disabled", COL["border"])])
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

def _app_dir():
    """Directory that contains the running exe (or script in dev mode)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def _get_tools_dir():
    """Returns (and creates) the tools/ folder next to the executable."""
    d = os.path.join(_app_dir(), "tools")
    os.makedirs(d, exist_ok=True)
    return d

def _find_exe(name):
    # 1. tools/ folder next to exe (always right location in both dev & frozen)
    tp = os.path.join(_app_dir(), "tools", f"{name}.exe")
    if os.path.exists(tp): return tp
    # 2. PATH
    p = shutil.which(name) or shutil.which(f"{name}.exe")
    if p: return p
    # 3. App directory itself (legacy location)
    lp = os.path.join(_app_dir(), f"{name}.exe")
    if os.path.exists(lp): return lp
    return None

def _auto_download_tools(log_fn=None):
    """Silently download missing tools into tools/ on first run. No popups."""
    def _log(msg):
        if log_fn:
            try: log_fn(msg)
            except Exception: pass

    tools = _get_tools_dir()

    try:
        if not _find_exe("yt-dlp"):
            _log("  [tools] Downloading yt-dlp…")
            urllib.request.urlretrieve(
                "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
                os.path.join(tools, "yt-dlp.exe"))
            _log("  [tools] ✓ yt-dlp ready")
    except Exception as e:
        _log(f"  [tools] yt-dlp download failed: {e}")

    _FFMPEG_EXES = {"ffmpeg.exe", "ffprobe.exe", "ffplay.exe"}
    try:
        if not _find_exe("ffmpeg") or not _find_exe("ffplay"):
            _log("  [tools] Downloading ffmpeg suite (may take a minute)…")
            zip_path = os.path.join(tools, "_ffmpeg_tmp.zip")
            urllib.request.urlretrieve(
                "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
                zip_path)
            _log("  [tools] Extracting ffmpeg…")
            extracted = []
            with zipfile.ZipFile(zip_path, 'r') as z:
                for info in z.infolist():
                    bn = os.path.basename(info.filename)
                    if bn in _FFMPEG_EXES:
                        dest = os.path.join(tools, bn)
                        with z.open(info) as zf, open(dest, "wb") as f:
                            shutil.copyfileobj(zf, f)
                        extracted.append(bn)
            try: os.remove(zip_path)
            except Exception: pass
            _log(f"  [tools] Extracted from zip: {', '.join(extracted) or 'nothing'}")
            # ffplay not in that zip — grab it from the gyan.dev essentials build
            if not _find_exe("ffplay"):
                _log("  [tools] ffplay missing from zip — fetching from alternate source…")
                zip_path2 = os.path.join(tools, "_ffmpeg2_tmp.zip")
                urllib.request.urlretrieve(
                    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
                    zip_path2)
                with zipfile.ZipFile(zip_path2, 'r') as z2:
                    for info in z2.infolist():
                        bn = os.path.basename(info.filename)
                        if bn in _FFMPEG_EXES and not os.path.exists(os.path.join(tools, bn)):
                            with z2.open(info) as zf, open(os.path.join(tools, bn), "wb") as f:
                                shutil.copyfileobj(zf, f)
                            _log(f"  [tools] extracted {bn} from gyan.dev")
                try: os.remove(zip_path2)
                except Exception: pass
            _log("  [tools] ✓ ffmpeg suite ready")
    except Exception as e:
        _log(f"  [tools] ffmpeg download failed: {e}")

    try:
        if not _find_exe("ddc") and not _find_exe("ddc64"):
            _log("  [tools] Downloading DDC…")
            _ddc_dest = os.path.join(tools, "ddc.exe")
            for _u in [
                "https://github.com/rscustom/rocksmith-custom-song-toolkit/raw/master/Third-party%20Apps/ddc/ddc.exe",
                "https://github.com/iminashi/DDCImprover/raw/master/DDCImprover.Core/ddc.exe",
            ]:
                try:
                    urllib.request.urlretrieve(_u, _ddc_dest)
                    if os.path.getsize(_ddc_dest) > 10000: _log("  [tools] ✓ DDC ready"); break
                    else: os.remove(_ddc_dest)
                except Exception: pass
            else:
                _log("  [tools] DDC auto-download failed — place ddc.exe in tools/ manually")
    except Exception as e:
        _log(f"  [tools] DDC download failed: {e}")

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
    # Strip encoder-padding/silence before music starts, then add our leadin.
    # -60dB threshold is conservative and won't cut quiet intros.
    ss = "silenceremove=start_periods=1:start_threshold=-60dB:start_duration=0.05,"
    af = f"{ss}adelay={delay_ms}|{delay_ms},aresample=resampler=soxr" if delay_ms > 0 else f"{ss}aresample=resampler=soxr"
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

def _find_ddc_support(ddc_path, packer_path, app_dir):
    """Locate DDC's ramp-up model (.xml) and config (.cfg). These ship in the
    toolkit's 'ddc' folder; DDC fails silently without them. Returns
    (ramp_xml, cfg) absolute paths, or (None, None) if not found."""
    import glob as _glob
    cand_dirs = []
    if ddc_path:
        cand_dirs += [os.path.dirname(ddc_path),
                      os.path.join(os.path.dirname(ddc_path), "ddc")]
    if packer_path:
        _tk = os.path.dirname(packer_path)
        cand_dirs += [os.path.join(_tk, "ddc"), _tk]
    if app_dir:
        cand_dirs += [os.path.join(app_dir, "ddc"),
                      os.path.join(app_dir, "tools", "ddc"),
                      os.path.join(app_dir, "tools"), app_dir]
    seen = set()
    for d in cand_dirs:
        if not d or d in seen or not os.path.isdir(d):
            continue
        seen.add(d)
        xmls = _glob.glob(os.path.join(d, "*.xml"))
        cfgs = _glob.glob(os.path.join(d, "*.cfg"))
        if not xmls or not cfgs:
            continue
        def _pick(paths, *keywords):
            for kw in keywords:
                for p in paths:
                    if kw in os.path.basename(p).lower():
                        return p
            return paths[0]
        ramp = _pick(xmls, "ramp", "default")
        cfg  = _pick(cfgs, "default")
        return ramp, cfg
    return None, None


def _apply_ddc(o, xml_files, proj_dir, log):
    """Add Dynamic Difficulty (beginner-friendly ramped levels) to each instrument
    arrangement using the EXACT CLI the RocksmithToolkit uses:
      ddc.exe "file.xml" -l <phraseLen> -s N -m "ramp.xml" -c "cfg.cfg" -p Y -t N
    DDC overwrites the XML in place (-p Y) and rewrites phrase maxDifficulty to
    match the levels it generates. Writes _ddc_debug.txt and verifies the level
    count actually increased so we never silently ship a single-difficulty chart."""
    ddc_path = _find_exe("ddc") or _find_exe("ddc64")
    if not ddc_path and getattr(o, "packer_path", ""):
        _tk = os.path.dirname(o.packer_path)
        for _dn in (os.path.join("ddc", "ddc.exe"), "ddc.exe", "ddc64.exe"):
            _dc = os.path.join(_tk, _dn)
            if os.path.isfile(_dc):
                ddc_path = _dc
                break
    diag = ["DDC DEBUG LOG", f"ddc.exe: {ddc_path}"]
    if not ddc_path:
        log("  [DDC] ddc.exe not found — Dynamic Difficulty skipped (chart stays single-difficulty).")
        return
    app_dir = os.path.dirname(os.path.abspath(__file__))
    ramp, cfg = _find_ddc_support(ddc_path, getattr(o, "packer_path", ""), app_dir)
    diag += [f"ramp model: {ramp}", f"config:     {cfg}"]
    if not ramp or not cfg:
        log("  [DDC] DDC support files (ramp-up .xml + .cfg) not found next to ddc.exe or the toolkit.")
        log("  [DDC]   Copy the toolkit's 'ddc' folder (ddc_default.xml + ddc_default.cfg) next to ddc.exe.")
        log("  [DDC]   Dynamic Difficulty skipped — chart stays single-difficulty for now.")
        try:
            with open(os.path.join(proj_dir, "_ddc_debug.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(diag) + "\n\nSupport files missing — DD not applied.\n")
        except Exception:
            pass
        return
    phrase_len = int(getattr(o, "ddc_phraselength", 256) or 256)
    log("  [DDC] Applying Dynamic Difficulty (beginner-friendly ramped levels)...")
    ok_count = 0
    for xml_file in xml_files:
        fdir, fname = os.path.dirname(xml_file), os.path.basename(xml_file)
        cmd = [ddc_path, fname, "-l", str(phrase_len), "-s", "N",
               "-m", ramp, "-c", cfg, "-p", "Y", "-t", "N"]
        diag.append("\n" + "=" * 60 + f"\nCMD: {' '.join(cmd)}\n(cwd={fdir})")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=900,
                               creationflags=CREATE_NO_WINDOW, cwd=fdir)
            out = ((r.stdout or "") + (r.stderr or "")).strip()
            diag.append(f"exit={r.returncode}\n{out}")
            lv = 0
            try:
                with open(xml_file, "r", encoding="utf-8", errors="replace") as _xf:
                    _m = re.search(r'<levels count="(\d+)"', _xf.read())
                lv = int(_m.group(1)) if _m else 0
            except Exception:
                pass
            diag.append(f"levels after DDC: {lv}")
            if r.returncode == 0 and lv > 1:
                ok_count += 1
                log(f"  [DDC] ✓ {fname}: {lv} difficulty levels")
            else:
                log(f"  [DDC] ⚠ {fname}: rc={r.returncode}, levels={lv} (see _ddc_debug.txt)")
        except Exception as e:
            diag.append(f"EXCEPTION: {e}")
            log(f"  [DDC] ⚠ error on {fname}: {e}")
    try:
        with open(os.path.join(proj_dir, "_ddc_debug.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(diag))
    except Exception:
        pass
    if ok_count == 0:
        log("  [DDC] No arrangements got DD — chart stays single-difficulty. See _ddc_debug.txt for why.")


def _run_packer(o, proj_dir, key, tmpl, result, log):
    packer = o.packer_path or _find_packer()
    if not packer: return log("  [psarc] packer.exe not found - skipping build.")
    try:
        os.makedirs(os.path.join(os.environ.get("TEMP",""), "tmp"), exist_ok=True)
    except Exception: pass
    psarc_out = os.path.join(proj_dir, f"{key}_p.psarc")
    cmd = [packer, "-b", f"-t={tmpl}", f"-o={psarc_out}", "-f=Pc", "-v=RS2014"]
    log(f"  [psarc] cmd: {' '.join(cmd)}")

    # Full diagnostic log written next to the project so nothing is lost/truncated.
    diag_path = os.path.join(proj_dir, "_packer_debug.txt")
    diag_lines = []
    def _diag(s=""):
        diag_lines.append(str(s))

    # ── Snapshot of what the packer actually has to work with ──────────────
    _diag("=" * 70)
    _diag("RS STUDIO PACKER DEBUG LOG")
    _diag("packer.exe : " + str(packer))
    _diag("project dir: " + str(proj_dir))
    _diag("template   : " + str(tmpl))
    _diag("output     : " + str(psarc_out))
    _diag("command    : " + " ".join(cmd))
    _diag("-" * 70)
    _diag("PROJECT FOLDER CONTENTS (name — size in bytes):")
    try:
        for fn in sorted(os.listdir(proj_dir)):
            fp = os.path.join(proj_dir, fn)
            try: sz = os.path.getsize(fp)
            except Exception: sz = -1
            _diag(f"  {fn} — {sz}")
    except Exception as e:
        _diag("  (could not list folder: %s)" % e)
    # Cross-check: do the asset files referenced in the .dlc.xml actually exist?
    _diag("-" * 70)
    _diag("ASSET REFERENCE CHECK (files named inside the .dlc.xml):")
    try:
        import re as _re_dbg
        with open(tmpl, "r", encoding="utf-8") as _tf:
            _txml = _tf.read()
        _refs = set()
        for _tag in ("AlbumArtPath", "OggPath", "OggPreviewPath"):
            _m = _re_dbg.search(rf"<{_tag}>(.*?)</{_tag}>", _txml)
            if _m and _m.group(1).strip(): _refs.add(_m.group(1).strip())
        for _m in _re_dbg.finditer(r"<g:File>(.*?)</g:File>", _txml):
            if _m.group(1).strip(): _refs.add(_m.group(1).strip())
        for _ref in sorted(_refs):
            _exists = os.path.isfile(os.path.join(proj_dir, _ref))
            _diag(f"  {'OK ' if _exists else 'MISSING'}  {_ref}")
            if not _exists:
                _diag(f"      ^^ referenced in .dlc.xml but NOT found in project folder")
    except Exception as e:
        _diag("  (could not parse template: %s)" % e)
    _diag("=" * 70)

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, creationflags=CREATE_NO_WINDOW, cwd=proj_dir)
        combined = ((r.stdout or "") + (r.stderr or "")).strip()
        result["packer_output"] = combined
        _diag("PACKER EXIT CODE: %s" % r.returncode)
        _diag("PACKER STDOUT:"); _diag(r.stdout or "(empty)")
        _diag("PACKER STDERR:"); _diag(r.stderr or "(empty)")
        # Log the FULL output to the console too (not truncated) so it shows in the build window.
        for line in combined.splitlines():
            if line.strip(): log(f"  [psarc] {line.rstrip()}")
        if r.returncode == 0 and os.path.exists(psarc_out):
            log(f"  [psarc] BUILT: {os.path.basename(psarc_out)}")
            result["psarc"] = psarc_out
        else:
            log(f"  [psarc] packer.exe exited rc={r.returncode}, file exists={os.path.exists(psarc_out)}")
            result["packer_failed"] = True
            # On failure, capture packer's own usage text so we can confirm the
            # exact CLI flags THIS packer.exe build supports (versions differ).
            try:
                h = subprocess.run([packer, "--help"], capture_output=True, text=True,
                                   timeout=30, creationflags=CREATE_NO_WINDOW)
                _diag("-" * 70)
                _diag("PACKER --help OUTPUT:")
                _diag((h.stdout or "") + (h.stderr or "") or "(no help text)")
            except Exception as he:
                _diag("(packer --help failed: %s)" % he)
    except Exception as e:
        log(f"  [psarc] exception: {e}")
        _diag("PYTHON EXCEPTION running packer: %s" % e)
        result["packer_failed"] = True

    # Always write the debug log.
    try:
        with open(diag_path, "w", encoding="utf-8") as _df:
            _df.write("\n".join(diag_lines))
        log(f"  [psarc] Full debug log written: {diag_path}")
    except Exception as e:
        log(f"  [psarc] (could not write debug log: {e})")

def _write_dds_bgra(img_path, out_path):
    """Write a 512x512 uncompressed BGRA DDS — Pillow only, no ffmpeg needed."""
    import struct
    from PIL import Image as _PI
    img = _PI.open(img_path).convert("RGBA").resize((512, 512))
    r, g, b, a = img.split()
    bgra = _PI.merge("RGBA", (b, g, r, a))
    raw = bgra.tobytes()
    w = h = 512
    hdr  = b"DDS "
    hdr += struct.pack("<I", 124)                     # dwSize
    hdr += struct.pack("<IIIII", 0x0010100F, h, w, w * 4, 0)  # flags,h,w,pitch,depth
    hdr += struct.pack("<I", 0) + b"\x00" * 44        # mipCount + reserved
    # DDPIXELFORMAT — 8 uint32s = 32 bytes; FourCC=0 (uncompressed BGRA)
    hdr += struct.pack("<IIIIIIII",
        32, 0x41, 0, 32,
        0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
    hdr += struct.pack("<IIIII", 0x1000, 0, 0, 0, 0)  # DDSCAPS
    assert len(hdr) == 128
    with open(out_path, "wb") as f:
        f.write(hdr)
        f.write(raw)


def _prepare_art_for_packer(src_art, proj_dir, key, log):
    """Convert album art to 512x512 uncompressed BGRA DDS for packer.exe."""
    dds_out = os.path.join(proj_dir, f"{key}_art.dds")
    try:
        _write_dds_bgra(src_art, dds_out)
        if os.path.getsize(dds_out) > 1000:
            log(f"  [art] DDS ready: {os.path.basename(dds_out)}")
            return dds_out
    except Exception as e:
        log(f"  [art] DDS write failed ({e}), using original")
    return src_art


def build_project(o, log=print):
    gp = gp2rs.GPSong(o.gp_path)
    key = make_dlc_key(o.artist, o.title)
    _folder = getattr(o, "song_folder", "") or key
    proj_dir = os.path.join(o.out_dir, _folder)
    os.makedirs(proj_dir, exist_ok=True)
    log(f"Building Project: {proj_dir}")
    xml_files_for_ddc = []
    result = {"proj_dir": proj_dir, "psarc": None, "packer_failed": False, "packer_output": ""}
    audio_path = ensure_wav(o.audio_path, proj_dir, log, leadin=o.leadin) if o.audio_path else None

    # Compute bpm_scale using only bars that actually contain notes.
    # Plain extract_gp_total_seconds includes trailing empty bars (very common in GP files),
    # which would give a falsely long GP duration and wrong scale.
    # get_gp_content_duration stops at the last bar with real notes.
    bpm_scale = getattr(o, "bpm_scale", 1.0)
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
        _lrc_off = getattr(o, "lrc_offset", 0.0)
        # Prefer .lrc over .txt — Whisper may have finished after vars_ was snapshotted
        _eff_lyrics = o.lyrics_path
        if not _eff_lyrics.endswith(".lrc"):
            _adj_lrc = os.path.splitext(_eff_lyrics)[0] + ".lrc"
            if os.path.isfile(_adj_lrc):
                _eff_lyrics = _adj_lrc
                log(f"  [vocals] Using adjacent LRC: {os.path.basename(_adj_lrc)}")
        vxml = gp2rs.lrc_to_vocals(_eff_lyrics, o.leadin, lrc_offset=_lrc_off) if _eff_lyrics.endswith(".lrc") else gp2rs.lyrics_txt_to_vocals(_eff_lyrics, gp, o.leadin)
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
        _apply_ddc(o, xml_files_for_ddc, proj_dir, log)
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


def _load_logo(size=80):
    """Load rs_studio.png from same dir as exe/script, return PhotoImage or None."""
    try:
        from PIL import Image, ImageTk
        import sys as _sys_l
        _base = os.path.dirname(getattr(_sys_l, "executable", __file__))
        _png = os.path.join(_base, "rs_studio.png")
        if not os.path.isfile(_png):
            _png = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rs_studio.png")
        if not os.path.isfile(_png):
            return None
        img = Image.open(_png).convert("RGBA")
        img = img.resize((size, size), Image.LANCZOS)
        return ImageTk.PhotoImage(img)
    except Exception:
        return None

def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk, colorchooser

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

    vars_ = {k: tk.StringVar() for k in ["gp", "lyrics", "audio", "preview_audio", "art", "out", "psarc_out", "title", "artist", "album", "year", "leadin", "volume", "audio_url", "packer", "cst", "slop_url", "slop_exe", "slop_plugin_dir", "appid", "scroll_speed", "pitch", "bpm", "bpm_factor", "lrc_offset"]}
    vars_["leadin"].set("5.0"); vars_["volume"].set("-8.0"); vars_["year"].set("2026"); vars_["bpm"].set("120"); vars_["bpm_factor"].set("1.000"); vars_["lrc_offset"].set("0.0")
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

    # Tools are downloaded on the startup screen (see _show_setup_screen below)

    track_rows = []; current_page = tk.StringVar(value="main")
    main_vid_state = {"process": None, "url": None, "thumb_obj": None}
    _gp_ref = [None]   # mutable slot so Sync Tuner can read the loaded GP object

    root.columnconfigure(1, weight=1); root.rowconfigure(0, weight=1)

    # ══════════════════════════════════════════════════════════════════════
    # DEPENDENCY MANAGER
    # ══════════════════════════════════════════════════════════════════════
    def ensure_dependencies_then_run(callback):
        """Run callback once all tools are ready. If still downloading, poll silently."""
        needed = ("ffmpeg", "ffplay", "yt-dlp")
        if all(_find_exe(t) for t in needed):
            if callback: callback()
            return
        # Tools are being downloaded in the background — wait and retry
        log("  [tools] Tools still downloading, please wait a moment…")
        def _poll():
            if all(_find_exe(t) for t in needed):
                if callback: callback()
            else:
                root.after(2000, _poll)
        root.after(2000, _poll)

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
    nav_label(sidebar, 4, "🎯", "Sync", "sync")
    nav_label(sidebar, 5, "◐", "Theme", "theme"); nav_label(sidebar, 6, "≡", "Log", "log")
    nav_label(sidebar, 7, "?", "Help", "help")

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
        # Stop sync page playback when leaving
        try:
            if current_page.get() == "sync" and name != "sync":
                _sync_play_stop_if_running()
        except (NameError, Exception):
            pass
        for pg in pages.values(): pg.pack_forget()
        if name in pages: pages[name].pack(fill="both", expand=True)
        current_page.set(name)
        refresh_theme(root, ttk)
        # Player bar only makes sense on the main page
        try:
            if name == "main":
                player_bar.grid(row=1, column=0, columnspan=2, sticky="ew")
            else:
                player_bar.grid_remove()
        except Exception:
            pass
        # Auto-load sync page when switching to it
        if name == "sync":
            try:
                root.after(100, _sync_load)
            except (NameError, Exception):
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
        vars_["album"].set("Single")
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
            # All set — go to Sync tab to verify before building
            show_page("sync")
            root.after(200, _sv_load)

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
            _next_btn.configure(text="Sync & Verify  →")
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
            "lyrics":        vars_["lyrics"].get() or "",
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

    def _show_setup_screen():
        """First-time setup: download missing tools with progress bar, then show normal splash."""
        for w in startup_overlay.winfo_children():
            w.destroy()
        startup_overlay.place(x=0, y=0, relwidth=1, relheight=1)
        startup_overlay.lift()

        outer = tk.Frame(startup_overlay, bg=COL["surface"])
        outer.place(relx=0.5, rely=0.5, anchor="center")

        logo_row = tk.Frame(outer, bg=COL["surface"])
        logo_row.pack(pady=(0, 4))
        _setup_logo = _load_logo(100)
        if _setup_logo:
            _sl = tk.Label(logo_row, image=_setup_logo, bg=COL["surface"]); _sl.image = _setup_logo; _sl.pack(side="left", padx=(0,12), anchor="center")
            tk.Label(logo_row, text="STUDIO", font=("Segoe UI", 38, "bold"), fg=COL["fg"], bg=COL["surface"]).pack(side="left", anchor="center")
        else:
            tk.Label(logo_row, text="RS", font=("Segoe UI Black", 38), fg=COL["accent"], bg=COL["surface"]).pack(side="left")
            tk.Label(logo_row, text=" STUDIO", font=("Segoe UI", 38, "bold"), fg=COL["fg"], bg=COL["surface"]).pack(side="left")
        tk.Label(outer, text="First Time Setup  —  Downloading required tools",
                 font=("Segoe UI", 11), fg=COL["muted"], bg=COL["surface"]).pack(pady=(0, 24))

        tools_frame = tk.Frame(outer, bg=COL["surface"])
        tools_frame.pack(fill="x", pady=(0, 16), padx=8)

        def _whisper_ok():
            import importlib.util
            return (importlib.util.find_spec("stable_whisper") is not None or
                    importlib.util.find_spec("faster_whisper") is not None)

        _TOOL_ROWS = [
            ("ytdlp",   "yt-dlp",          lambda: bool(_find_exe("yt-dlp"))),
            ("ffmpeg",  "ffmpeg + ffplay",  lambda: bool(_find_exe("ffmpeg") and _find_exe("ffplay"))),
            ("ddc",     "DDC",              lambda: bool(_find_exe("ddc") or _find_exe("ddc64"))),
            ("whisper", "AI Timestamps",    _whisper_ok),
        ]
        tv, tl = {}, {}
        for key, label, _ in _TOOL_ROWS:
            tv[key] = tk.StringVar()
            row = tk.Frame(tools_frame, bg=COL["surface"])
            row.pack(fill="x", pady=3)
            tl[key] = tk.Label(row, textvariable=tv[key], font=("Segoe UI", 10),
                                fg=COL["muted"], bg=COL["surface"], anchor="w", width=36)
            tl[key].pack(side="left")

        status_var = tk.StringVar(value="Starting…")
        tk.Label(outer, textvariable=status_var, font=("Segoe UI", 9),
                 fg=COL["dim"], bg=COL["surface"]).pack()
        pb = ttk.Progressbar(outer, mode="determinate", length=400, maximum=100)
        pb.pack(pady=(6, 0))

        # Initialise labels
        for key, label, is_done in _TOOL_ROWS:
            if is_done():
                tv[key].set(f"✓  {label}")
                tl[key].configure(fg=COL["ok"])
            else:
                tv[key].set(f"⏳  {label}")

        def _mark_ok(key, label):
            root.after(0, lambda: tv[key].set(f"✓  {label}"))
            root.after(0, lambda: tl[key].configure(fg=COL["ok"]))

        def _mark_skip(key, label):
            root.after(0, lambda: tv[key].set(f"~  {label}  (skipped)"))

        def _mark_err(key, label):
            root.after(0, lambda: tv[key].set(f"✗  {label}  (failed)"))
            root.after(0, lambda: tl[key].configure(fg=COL["warn"]))

        def _set_status(msg):
            root.after(0, lambda: status_var.set(msg))

        def _set_pb(val):
            root.after(0, lambda: pb.configure(value=val))

        def _worker():
            tools = _get_tools_dir()
            _FFMPEG_EXES = {"ffmpeg.exe", "ffprobe.exe", "ffplay.exe"}

            # --- yt-dlp ---
            if not _find_exe("yt-dlp"):
                root.after(0, lambda: tv["ytdlp"].set("⬇  yt-dlp…"))
                _set_status("Downloading yt-dlp…")
                try:
                    urllib.request.urlretrieve(
                        "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
                        os.path.join(tools, "yt-dlp.exe"))
                    _mark_ok("ytdlp", "yt-dlp")
                    log("  [setup] ✓ yt-dlp")
                except Exception as e:
                    _mark_err("ytdlp", "yt-dlp")
                    log(f"  [setup] yt-dlp failed: {e}")
            _set_pb(30)

            # --- ffmpeg + ffplay ---
            if not _find_exe("ffmpeg") or not _find_exe("ffplay"):
                root.after(0, lambda: tv["ffmpeg"].set("⬇  ffmpeg + ffplay  (large file)…"))
                _set_status("Downloading ffmpeg…")
                try:
                    zip_path = os.path.join(tools, "_ffmpeg_tmp.zip")
                    urllib.request.urlretrieve(
                        "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
                        zip_path)
                    _set_status("Extracting ffmpeg…")
                    _set_pb(55)
                    with zipfile.ZipFile(zip_path, "r") as z:
                        for info in z.infolist():
                            bn = os.path.basename(info.filename)
                            if bn in _FFMPEG_EXES:
                                with z.open(info) as zf, open(os.path.join(tools, bn), "wb") as f:
                                    shutil.copyfileobj(zf, f)
                    try: os.remove(zip_path)
                    except Exception: pass
                    # Fallback for ffplay if not in that zip
                    if not _find_exe("ffplay"):
                        _set_status("Fetching ffplay from alternate source…")
                        zip2 = os.path.join(tools, "_ff2_tmp.zip")
                        urllib.request.urlretrieve(
                            "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip", zip2)
                        with zipfile.ZipFile(zip2, "r") as z2:
                            for info in z2.infolist():
                                bn = os.path.basename(info.filename)
                                if bn in _FFMPEG_EXES and not os.path.exists(os.path.join(tools, bn)):
                                    with z2.open(info) as zf, open(os.path.join(tools, bn), "wb") as f:
                                        shutil.copyfileobj(zf, f)
                        try: os.remove(zip2)
                        except Exception: pass
                    _mark_ok("ffmpeg", "ffmpeg + ffplay")
                    log("  [setup] ✓ ffmpeg + ffplay")
                except Exception as e:
                    _mark_err("ffmpeg", "ffmpeg + ffplay")
                    log(f"  [setup] ffmpeg failed: {e}")
            _set_pb(80)

            # --- DDC ---
            def _find_ddc_anywhere():
                found = _find_exe("ddc") or _find_exe("ddc64")
                if found: return found
                packer_path = load_settings().get("packer", "")
                if packer_path and os.path.isfile(packer_path):
                    for _r, _d, _f in os.walk(os.path.dirname(packer_path)):
                        if os.path.relpath(_r, os.path.dirname(packer_path)).count(os.sep) > 2: _d[:] = []; continue
                        for _fn in ("ddc.exe", "ddc64.exe"):
                            if _fn in _f:
                                _found = os.path.join(_r, _fn)
                                try: shutil.copy2(_found, os.path.join(tools, _fn))
                                except Exception: pass
                                return _found
                return None

            if not _find_ddc_anywhere():
                root.after(0, lambda: tv["ddc"].set("⬇  DDC…"))
                _set_status("Downloading DDC…")
                _ddc_dest = os.path.join(tools, "ddc.exe")
                _ddc_ok = False
                for _u in [
                    "https://github.com/rscustom/rocksmith-custom-song-toolkit/raw/master/Third-party%20Apps/ddc/ddc.exe",
                    "https://github.com/iminashi/DDCImprover/raw/master/DDCImprover.Core/ddc.exe",
                ]:
                    try:
                        urllib.request.urlretrieve(_u, _ddc_dest)
                        if os.path.exists(_ddc_dest) and os.path.getsize(_ddc_dest) > 10000: _ddc_ok = True; break
                        elif os.path.exists(_ddc_dest): os.remove(_ddc_dest)
                    except Exception: pass
                if _ddc_ok:
                    _mark_ok("ddc", "DDC"); log("  [setup] ✓ DDC")
                else:
                    _mark_err("ddc", "DDC")
                    log("  [setup] DDC auto-download failed.")
                    root.after(500, lambda: messagebox.showwarning(
                        "DDC Not Found",
                        "DDC could not be downloaded automatically.\n\n"
                        "To fix:\n"
                        "1. Open your RocksmithToolkit folder\n"
                        "2. Find ddc.exe in Third-party Apps\\ddc\\\n"
                        "3. Copy it into the  tools\\  folder next to RS STUDIO.exe\n\n"
                        "Then restart RS Studio."))
            else:
                _mark_ok("ddc", "DDC"); log("  [setup] ✓ DDC found")
            _set_pb(85)

            # --- stable-ts + faster-whisper ---
            if not _whisper_ok():
                root.after(0, lambda: tv["whisper"].set("⬇  AI Timestamps…"))
                _set_status("Installing stable-ts + faster-whisper…")
                try:
                    import sys as _sys2
                    subprocess.run([_sys2.executable, "-m", "pip", "install", "--quiet",
                                    "stable-ts", "faster-whisper"],
                                   check=False, creationflags=CREATE_NO_WINDOW)
                    import importlib as _il2
                    _il2.invalidate_caches()
                    for _k in list(_sys2.modules.keys()):
                        if "stable_whisper" in _k or "faster_whisper" in _k: del _sys2.modules[_k]
                    if _whisper_ok():
                        _mark_ok("whisper", "AI Timestamps"); log("  [setup] ✓ stable-ts + faster-whisper")
                    else:
                        _mark_err("whisper", "AI Timestamps")
                        log("  [setup] stable-ts install failed — run: pip install stable-ts faster-whisper")
                except Exception as _we:
                    _mark_err("whisper", "AI Timestamps"); log(f"  [setup] stable-ts install error: {_we}")
            _set_pb(100)

            _set_status("✓ All done!")
            _overlay_shown[0] = False
            root.after(1000, _show_startup_overlay)

        threading.Thread(target=_worker, daemon=True).start()

    _overlay_shown = [False]   # guard — only show once per session
    _yt_meta_cache = [{}]      # stores last YouTube artist/title for lyrics fetch

    def _show_startup_overlay():
        """After Effects-style startup overlay: large logo, two-column layout."""
        if _overlay_shown[0]:
            return
        _overlay_shown[0] = True
        # First-time setup: show setup screen if any required tool is missing
        def _whisper_installed():
            import importlib.util
            return (importlib.util.find_spec("stable_whisper") is not None or
                    importlib.util.find_spec("faster_whisper") is not None)
        if not (_find_exe("yt-dlp") and _find_exe("ffmpeg") and _find_exe("ffplay")) \
                or not _whisper_installed():
            _show_setup_screen()
            return

        for w in startup_overlay.winfo_children():
            w.destroy()
        startup_overlay.place(x=0, y=0, relwidth=1, relheight=1)
        startup_overlay.lift()

        outer = tk.Frame(startup_overlay, bg=COL["surface"])
        outer.place(relx=0.5, rely=0.5, anchor="center")

        # ── Logo ────────────────────────────────────────────────────────────
        logo_row = tk.Frame(outer, bg=COL["surface"])
        logo_row.pack(pady=(0, 2))
        _splash_logo = _load_logo(120)
        if _splash_logo:
            _spl = tk.Label(logo_row, image=_splash_logo, bg=COL["surface"]); _spl.image = _splash_logo; _spl.pack(side="left", padx=(0,14), anchor="center")
            tk.Label(logo_row, text="STUDIO", font=("Segoe UI", 44, "bold"),
                     fg=COL["fg"], bg=COL["surface"]).pack(side="left", anchor="center")
        else:
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
            startup_overlay.place_forget(); player_bar.grid()

        def _open_existing():
            gp = filedialog.askopenfilename(
                title="Open GP File",
                filetypes=[("Guitar Pro", "*.gp *.gp5 *.gp4 *.gp3 *.gpx"),
                           ("All files", "*.*")])
            if not gp:
                return
            startup_overlay.place_forget(); player_bar.grid()
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
                      lrc=entry.get("lyrics",""),
                      out=entry.get("out","")):
                if not g or not os.path.exists(g):
                    return messagebox.showwarning("File not found",
                        f"GP file not found:\n{g}")
                startup_overlay.place_forget(); player_bar.grid()
                # Restore previously saved file paths before load_gp runs
                if a    and os.path.exists(a):    vars_["art"].set(a)
                if lrc  and os.path.exists(lrc):  vars_["lyrics"].set(lrc)
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

    # ── Lyrics fetch + AI Timestamp buttons (below lyrics field) ─────
    _lyr_btn_var = tk.StringVar(value="🎵 Get Lyrics & Timestamps")

    def _do_fetch_lyrics():
        """Search lrclib.net for synced (LRC) or plain lyrics using artist+title."""
        # Prefer YouTube-sourced artist/title — more reliable than GP-derived field values
        _yt = _yt_meta_cache[0]
        artist = (vars_["artist"].get().strip() or _yt.get("artist", "")).strip()
        title  = (vars_["title"].get().strip()  or _yt.get("title",  "")).strip()
        if not artist or not title:
            return messagebox.showerror("Missing info",
                "Fill in Artist and Title fields before fetching lyrics.")
        out_dir = vars_["out"].get().strip()
        if not out_dir:
            return messagebox.showerror("Missing info",
                "Set a Project Folder first so lyrics can be saved there.")

        _lyr_btn_var.set("⏳ Searching…")
        _lyr_btn.configure(state="disabled")

        def _worker():
            import urllib.request, urllib.parse, json, unicodedata
            def _slug(s):
                s = unicodedata.normalize("NFKD", s)
                s = s.encode("ascii", "ignore").decode()
                return s.strip()

            base = "https://lrclib.net/api"
            found_lrc  = None
            found_plain = None

            # ── lrclib.net (synced LRC preferred) ──────────────────
            def _lrc_pick(hits_list):
                _s, _p = None, None
                for h in hits_list:
                    if h.get("syncedLyrics") and not _s: _s = h["syncedLyrics"]
                    if h.get("plainLyrics")  and not _p: _p = h["plainLyrics"]
                return _s, _p

            def _fuzzy_score(search_title, hit_title):
                """Score based on word overlap — prefix match handles GP truncations (deca→decay)."""
                sw = _slug(search_title).lower().split()
                hw = set(_slug(hit_title).lower().split())
                score = 0
                for w in sw:
                    if w in hw or any(h.startswith(w) or w.startswith(h) for h in hw):
                        score += 1
                return score

            try:
                # Pass 1 — exact artist + title
                q1 = urllib.parse.urlencode({
                    "artist_name": _slug(artist),
                    "track_name":  _slug(title),
                })
                with urllib.request.urlopen(base + "/search?" + q1, timeout=15) as r:
                    found_lrc, found_plain = _lrc_pick(json.loads(r.read()))

                # Pass 2 — keyword: artist + title
                if not found_lrc and not found_plain:
                    q2 = urllib.parse.urlencode({"q": _slug(artist) + " " + _slug(title)})
                    with urllib.request.urlopen(base + "/search?" + q2, timeout=15) as r:
                        found_lrc, found_plain = _lrc_pick(json.loads(r.read()))

                # Pass 3 — artist + raw YouTube video title (strips "[Official Video]" etc.)
                _raw = _yt_meta_cache[0].get("raw_title", "")
                import re as _re_lrc
                _clean_raw = _re_lrc.sub(r"[\[\(][^\]\)]*[\]\)]", "", _raw).strip(" -|")
                if not found_lrc and not found_plain and _clean_raw and _clean_raw != title:
                    q3 = urllib.parse.urlencode({"q": _slug(artist) + " " + _slug(_clean_raw)})
                    with urllib.request.urlopen(base + "/search?" + q3, timeout=15) as r:
                        found_lrc, found_plain = _lrc_pick(json.loads(r.read()))
                    if found_lrc or found_plain:
                        log("  [lyrics] matched via raw video title: " + _clean_raw)

                # Pass 4 — artist-only search, pick best fuzzy title match
                if not found_lrc and not found_plain:
                    q4 = urllib.parse.urlencode({"q": _slug(artist)})
                    with urllib.request.urlopen(base + "/search?" + q4, timeout=15) as r:
                        all_hits = json.loads(r.read())
                    best_score, best_hit = 0, None
                    for h in all_hits:
                        sc = _fuzzy_score(title, h.get("trackName", ""))
                        if sc > best_score:
                            best_score, best_hit = sc, h
                    if best_hit and best_score >= 2:
                        found_lrc, found_plain = _lrc_pick([best_hit])
                        log("  [lyrics] fuzzy match: " + best_hit.get("trackName",""))

            except Exception as _lrc_e:
                log("  [lyrics] lrclib.net failed: " + str(_lrc_e) + " — trying lyrics.ovh")

            # ── lyrics.ovh fallback (plain text) ────────────────────────
            if not found_lrc and not found_plain:
                try:
                    _ovh_url = ("https://api.lyrics.ovh/v1/"
                                + urllib.parse.quote(_slug(artist)) + "/"
                                + urllib.parse.quote(_slug(title)))
                    with urllib.request.urlopen(_ovh_url, timeout=15) as r:
                        _ovh_data = json.loads(r.read())
                    if _ovh_data.get("lyrics"):
                        found_plain = _ovh_data["lyrics"]
                        log("  [lyrics] Got plain lyrics from lyrics.ovh")
                except Exception as _ovh_e:
                    log("  [lyrics] lyrics.ovh also failed: " + str(_ovh_e))

            if not found_lrc and not found_plain:
                root.after(0, lambda: messagebox.showinfo(
                    "Not found",
                    "No lyrics found for:\n" + artist + " — " + title + "\n\n"
                    "Both lrclib.net and lyrics.ovh were tried.\n"
                    "Paste lyrics into a .txt file and set it in the Lyrics field."))
                root.after(0, lambda: _lyr_btn_var.set("🔍 Fetch Lyrics"))
                root.after(0, lambda: _lyr_btn.configure(state="normal"))
                return

            # Save to per-song subfolder. IMPORTANT: reuse the folder name that the
            # auto-download step already locked in (_sv["song_key"]). The iTunes
            # title-correction fires AFTER that folder is created and changes `title`,
            # so recomputing the slug here used to scatter the .lrc into a second,
            # differently-named folder than the audio + the build folder.
            def _sfn(s): return re.sub(r"[^\w\- ]", "", s).strip().replace(" ", "_")[:40]
            _sk = (_sv.get("song_key") or "").strip() or (_sfn(artist) + "_" + _sfn(title))
            song_dir = os.path.join(out_dir, _sk)
            os.makedirs(song_dir, exist_ok=True)

            if found_lrc:
                dest = os.path.join(song_dir, _sk + ".lrc")
                with open(dest, "w", encoding="utf-8") as f:
                    f.write(found_lrc)
                root.after(0, lambda d=dest: vars_["lyrics"].set(d))
                root.after(0, lambda: _lyr_btn_var.set("✓ Synced LRC — done!"))
                root.after(0, lambda: _lyr_btn.configure(state="normal"))
                log("  [lyrics] ✓ Synced LRC saved → " + dest)
            else:
                # Plain text only — save it then auto-run Whisper
                dest = os.path.join(song_dir, _sk + "_lyrics.txt")
                with open(dest, "w", encoding="utf-8") as f:
                    f.write(found_plain)
                root.after(0, lambda d=dest: vars_["lyrics"].set(d))
                log("  [lyrics] Plain lyrics saved → " + dest)
                root.after(0, lambda: _lyr_btn_var.set("🎙 Adding timestamps…"))
                root.after(200, _do_ai_timestamps)   # auto-chain into Whisper

        threading.Thread(target=_worker, daemon=True).start()

    _lyr_btn = tk.Button(files_left, textvariable=_lyr_btn_var,
                         font=("Segoe UI", 8), relief="flat", bd=0, padx=8, pady=3,
                         cursor="hand2", bg=COL["card2"], fg=COL["muted"],
                         activebackground=COL["border_hi"], command=_do_fetch_lyrics)
    _lyr_btn.grid(row=10, column=1, sticky="w", padx=(16, 0), pady=(0, 2))

    _ts_btn_var = tk.StringVar(value="🎙 Get AI Timestamps")

    def _do_ai_timestamps():
        """Run faster-whisper on current audio, align with existing lyrics, write .lrc."""
        # Prevent the startup splash from appearing mid-install
        _overlay_shown[0] = True
        try:
            startup_overlay.place_forget(); player_bar.grid()
        except Exception:
            pass

        audio_path  = vars_["audio"].get()
        lyrics_path = vars_["lyrics"].get()
        if not audio_path or not os.path.isfile(audio_path):
            return messagebox.showerror("No Audio", "Set an audio (.wav/.mp3/.ogg) file first.")
        _audio_exts = ('.wav', '.mp3', '.ogg', '.flac', '.m4a', '.aac', '.opus')
        if not audio_path.lower().endswith(_audio_exts):
            return messagebox.showerror(
                "Wrong file type",
                f"The AUDIO field points to a non-audio file:\n{audio_path}\n\n"
                "Set it to a .wav, .mp3, or .ogg file.")
        if not lyrics_path or not os.path.isfile(lyrics_path):
            return messagebox.showerror("No Lyrics", "Set a lyrics (.txt) file first.")

        _ts_btn_var.set("⏳ Working…")
        _ts_btn.configure(state="disabled")

        def _status(msg):
            root.after(0, lambda m=msg: _ts_btn_var.set(m))
            root.after(0, lambda m=msg: log("  [whisper] " + m))

        def _worker():
            try:
                # ── NEW: route through the demucs/energy-VAD engine ───────────
                # lyric_sync isolates vocals (demucs), times the lyric LINES from
                # the stem's phrase onsets (energy VAD — no CUDA), and only falls
                # back to whisper if VAD can't run. It keeps the real lyric words
                # and writes the .lrc itself.
                import importlib as _il3
                _il3.invalidate_caches()
                try:
                    import lyric_sync as _ls
                    _il3.reload(_ls)
                except Exception as _imp:
                    root.after(0, lambda: messagebox.showerror("Engine missing",
                        f"lyric_sync.py / lyrics_align.py not found next to the app:\n{_imp}"))
                    root.after(0, lambda: _ts_btn_var.set("\U0001f3a4 Get AI Timestamps"))
                    root.after(0, lambda: _ts_btn.configure(state="normal"))
                    return
                _wd = os.path.join(os.path.dirname(lyrics_path) or ".", "_align_work")
                _res = _ls.align_lyrics(audio_path, lyrics_path, _wd, log, _status)
                if not _res:
                    root.after(0, lambda: messagebox.showerror("No timestamps",
                        "Could not time the lyrics — the vocals may be too unclear, "
                        "or no lyric lines were found in the .txt file."))
                    root.after(0, lambda: _ts_btn_var.set("\U0001f3a4 Get AI Timestamps"))
                    root.after(0, lambda: _ts_btn.configure(state="normal"))
                    return
                _lrc_dest = _res["lrc"]; _n = _res["lines"]; _src = _res.get("source", "?")
                root.after(0, lambda: vars_["lyrics"].set(_lrc_dest))
                root.after(0, lambda: _ts_btn_var.set(f"✓ {_n} lines timestamped ({_src})"))
                root.after(0, lambda: _ts_btn.configure(state="normal"))
                log(f"  [align] ✓ Saved {_lrc_dest} ({_n} lines, source={_src})")
                return
                # ─── legacy in-process stable-whisper path (kept as reference,
                #     unreachable after the return above) ───────────────────────
                import re as _re3
                _status("Loading aligner…")
                _st = None
                try:
                    import stable_whisper as _st
                except ImportError:
                    _status("Installing stable-ts…")
                    try:
                        import sys as _sys3
                        subprocess.run([_sys3.executable, "-m", "pip", "install", "--quiet",
                                        "stable-ts", "faster-whisper"],
                                       check=False, creationflags=CREATE_NO_WINDOW)
                    except Exception as _ie:
                        root.after(0, lambda: messagebox.showerror("Install failed", f"pip install stable-ts failed:\n{_ie}"))
                        root.after(0, lambda: _ts_btn_var.set("\U0001f3a4 Get AI Timestamps"))
                        root.after(0, lambda: _ts_btn.configure(state="normal"))
                        return
                    import importlib as _il3, sys as _sys3
                    _il3.invalidate_caches()
                    for _k in list(_sys3.modules.keys()):
                        if "stable_whisper" in _k: del _sys3.modules[_k]
                    try:
                        import stable_whisper as _st
                    except ImportError:
                        root.after(0, lambda: messagebox.showwarning("Not installed yet",
                            "stable-ts not found after install.\nClose and reopen RS Studio, then try again."))
                        root.after(0, lambda: _ts_btn_var.set("\U0001f3a4 Get AI Timestamps"))
                        root.after(0, lambda: _ts_btn.configure(state="normal"))
                        return

                with open(lyrics_path, "r", encoding="utf-8") as _lf:
                    raw_lines = _lf.readlines()
                _meta_pat = _re3.compile(r"^(source|songwriters?|writers?|composers?|lyrics?)\s*[:\.]", _re3.IGNORECASE)
                _tag_pat = _re3.compile(r"^\[.{0,40}\]$")
                plain_lines = [_l.strip() for _l in raw_lines
                               if _l.strip() and not _tag_pat.match(_l.strip()) and not _meta_pat.match(_l.strip())]

                if not plain_lines:
                    root.after(0, lambda: messagebox.showerror("Empty lyrics", "No lyric lines found in the .txt file."))
                    root.after(0, lambda: _ts_btn_var.set("\U0001f3a4 Get AI Timestamps"))
                    root.after(0, lambda: _ts_btn.configure(state="normal"))
                    return

                log(f"  [align] {len(plain_lines)} lyric lines to align")
                model_dir = os.path.join(_get_tools_dir(), "whisper_models")
                os.makedirs(model_dir, exist_ok=True)
                _status("Loading model…")
                try:
                    _model = _st.load_faster_whisper("small", device="cuda", compute_type="float16", download_root=model_dir)
                    log("  [align] GPU CUDA float16")
                except Exception:
                    try:
                        _model = _st.load_faster_whisper("small", device="cuda", compute_type="int8", download_root=model_dir)
                        log("  [align] GPU CUDA int8")
                    except Exception:
                        _model = _st.load_faster_whisper("small", device="cpu", compute_type="int8", download_root=model_dir)
                        log("  [align] CPU fallback")

                # ── Step 1: forced align to get coarse segment timing ──────────────
                _status("Aligning lyrics to audio…")
                result = _model.align(audio_path, "\n".join(plain_lines), language="en")

                # ── Step 2: also transcribe to get fine word timestamps ───────────────
                _status("Refining word timestamps…")
                try:
                    t_result = _model.transcribe(
                        audio_path, language="en", word_timestamps=True,
                        initial_prompt="\n".join(plain_lines[:10]),
                        vad_filter=True)
                    t_segs = t_result.segments if hasattr(t_result, "segments") else []
                    t_words = []
                    for _seg in t_segs:
                        for _w in (_seg.words or []):
                            _wt = getattr(_w, "start", None)
                            _ww = getattr(_w, "word", "").strip()
                            if _wt is not None and _ww:
                                t_words.append((_wt, _ww))
                    log(f"  [align] transcription gave {len(t_words)} words")
                except Exception as _te:
                    t_words = []
                    log(f"  [align] transcription step failed ({_te}), using alignment only")

                # ── Step 3: extract word timestamps from alignment result ─────────────
                a_segs = result.segments if hasattr(result, "segments") else []
                a_words = []
                for _seg in a_segs:
                    for _w in (_seg.words or []):
                        _wt = getattr(_w, "start", None)
                        _ww = getattr(_w, "word", "").strip()
                        if _wt is not None and _ww:
                            a_words.append((_wt, _ww))
                log(f"  [align] alignment gave {len(a_words)} words")

                # Prefer transcription words (more accurate timing); fall back to alignment
                all_words = t_words if len(t_words) >= len(a_words) * 0.6 else a_words
                log(f"  [align] using {'transcription' if all_words is t_words else 'alignment'} timestamps")

                if not all_words:
                    root.after(0, lambda: messagebox.showerror("No timestamps",
                        "Could not get word timestamps — vocals may be too unclear."))
                    root.after(0, lambda: _ts_btn_var.set("\U0001f3a4 Get AI Timestamps"))
                    root.after(0, lambda: _ts_btn.configure(state="normal"))
                    return

                # ── Step 4: anchor each lyric line to the audio ───────────────────────
                # Match each lyric line to the word stream by CONTENT with a forward-only
                # sliding window, instead of mapping stable-ts segments to lines by blind
                # index. Index mapping silently drifts whenever the number of segments
                # differs from the number of lyric lines (very common), which is what
                # made later lines progressively wrong. Content matching against the
                # VAD-filtered transcription words also pins the first line to the real
                # vocal onset, so long instrumental intros no longer drag everything early.
                def _cw(s): return _re3.sub(r"[^a-z0-9]", " ", s.lower()).split()
                flat = [(t, _cw(w)[0]) for (t, w) in all_words if _cw(w)]
                lrc_pairs = []
                search_from = 0
                last_t = 0.0
                for line in plain_lines:
                    lw = _cw(line)
                    if not lw:
                        lrc_pairs.append((last_t, line))
                        continue
                    n_match = min(4, len(lw))
                    window_end = min(len(flat), search_from + max(80, len(lw) * 14))
                    best_score, best_pos = -1, search_from
                    for i in range(search_from, window_end):
                        sc = sum(1 for j in range(n_match)
                                 if i + j < len(flat) and flat[i + j][1] == lw[j])
                        if sc > best_score:
                            best_score, best_pos = sc, i
                            if sc == n_match:
                                break
                    t_hit = flat[best_pos][0] if best_pos < len(flat) else last_t
                    if t_hit < last_t:          # keep cues non-decreasing (monotonic)
                        t_hit = last_t
                    last_t = t_hit
                    lrc_pairs.append((t_hit, line))
                    if best_score > 0:          # only advance the window on a real match
                        search_from = best_pos + max(1, len(lw) - 1)
                log(f"  [align] anchored {len(lrc_pairs)} lines to {len(flat)} aligned words")

                if not lrc_pairs:
                    root.after(0, lambda: messagebox.showerror("No match", "Alignment returned no results."))
                    root.after(0, lambda: _ts_btn_var.set("\U0001f3a4 Get AI Timestamps"))
                    root.after(0, lambda: _ts_btn.configure(state="normal"))
                    return

                lrc_dest = os.path.splitext(lyrics_path)[0] + ".lrc"
                with open(lrc_dest, "w", encoding="utf-8") as _of:
                    for (t, txt) in lrc_pairs:
                        t2 = max(0.0, t); m2 = int(t2) // 60; s2 = t2 - m2 * 60
                        _of.write(f"[{m2:02d}:{s2:05.2f}]{txt}\n")

                root.after(0, lambda: vars_["lyrics"].set(lrc_dest))
                root.after(0, lambda: _ts_btn_var.set(f"\u2713 {len(lrc_pairs)} lines timestamped"))
                root.after(0, lambda: _ts_btn.configure(state="normal"))
                log(f"  [align] \u2713 Saved {lrc_dest} ({len(lrc_pairs)} lines)")

            except Exception as _e:
                import traceback as _tb2
                root.after(0, lambda: messagebox.showerror("Alignment error", str(_e)))
                root.after(0, lambda: _ts_btn_var.set("\U0001f3a4 Get AI Timestamps"))
                root.after(0, lambda: _ts_btn.configure(state="normal"))
                log(f"  [align] Error: {_e}\n{_tb2.format_exc()}")

        threading.Thread(target=_worker, daemon=True).start()



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
    col_label(meta_card, "SONG DELAY (seconds)", 5, 0)
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

    styled(tk.Label(meta_card, text="Blank gap before the song starts (Rocksmith needs ~5s to load). Fine-tune in Sync & Verify.", font=("Segoe UI", 8), justify="left", wraplength=300), fg="dim", bg="card").grid(row=7, column=0, sticky="w", pady=(2, 0))
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
    page_header(slop_page, "Slopsmith  —  Sync Tuner")

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

    def _on_audio_or_gp_changed(*_):
        """Auto-reload the Sync tab if it's currently visible and audio/gp just changed."""
        try:
            if current_page.get() == "sync":
                root.after(400, _sv_load)
        except Exception:
            pass
    vars_["audio"].trace_add("write", _on_audio_or_gp_changed)
    vars_["gp"].trace_add("write", _on_audio_or_gp_changed)

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
                            text="🎯  Sync & Verify →",
                            font=("Segoe UI Semibold", 11), cursor="hand2",
                            command=lambda: [show_page("sync"), root.after(200, _sv_load)],
                            relief="flat", bd=0, padx=22, pady=12, fg="white")
    styled(btn_rebuild, bg="accent", activebackground="accent_hi")
    btn_rebuild.pack(side="left", anchor="center")

    # Secondary "build directly without sync check" button
    btn_build_direct = tk.Button(rebuild_frame,
                                 text="⚡ Build",
                                 font=("Segoe UI", 9), cursor="hand2",
                                 command=lambda: do_build(),
                                 relief="flat", bd=0, padx=12, pady=12, fg=COL["fg"])
    styled(btn_build_direct, bg="card2", activebackground="card")
    btn_build_direct.pack(side="left", anchor="center", padx=(6, 0))

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

    def _slop_embed_launch(relaunch=False):
        exe = vars_["slop_exe"].get().strip()
        if not exe or not os.path.isfile(exe):
            exe = _find_slopsmith_exe()
            if exe: vars_["slop_exe"].set(exe)
            else:
                _slop_embed_status.set("Slopsmith not found — set path in Settings.")
                return
        # Kill existing Slopsmith so it rescans fresh on relaunch
        if relaunch:
            old_pid = _slop_embed_state.get("pid", 0)
            _slop_embed_state["_keep_alive"] = False
            _slop_embed_state["hwnd"] = 0
            _slop_embed_state["pid"] = 0
            if old_pid:
                try:
                    subprocess.run(["taskkill", "/PID", str(old_pid), "/F"],
                                   capture_output=True, creationflags=CREATE_NO_WINDOW)
                except Exception: pass
        _slop_embed_status.set("Launching Slopsmith…")
        _slop_embed_state["_embed_retries"] = 0

        def _do_launch():
            try:
                proc = subprocess.Popen([exe])
                _slop_embed_state["pid"] = proc.pid
                # Give Electron app time to fully start (6s first launch, 3s relaunch)
                wait_ms = 3500 if relaunch else 6000
                root.after(wait_ms, _slop_try_embed)
            except Exception as ex:
                root.after(0, lambda: _slop_embed_status.set(f"Launch failed: {ex}"))

        # Run the launch in a thread so any OS delay doesn't block the UI
        threading.Thread(target=_do_launch, daemon=True).start()

    def _slop_try_embed():
        import ctypes
        pid = _slop_embed_state.get("pid", 0)
        hwnd = [0]

        def _get_pid(h):
            b = ctypes.c_ulong(); ctypes.windll.user32.GetWindowThreadProcessId(h, ctypes.byref(b)); return b.value

        def _cb(h, _):
            if not ctypes.windll.user32.IsWindowVisible(h): return True
            buf = ctypes.create_unicode_buffer(512)
            ctypes.windll.user32.GetWindowTextW(h, buf, 512)
            if pid and _get_pid(h) == pid: hwnd[0] = h; return False
            if "slopsmith" in buf.value.lower(): hwnd[0] = h; return False
            return True

        PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
        ctypes.windll.user32.EnumWindows(PROC(_cb), 0)

        if not hwnd[0]:
            r = _slop_embed_state.get("_embed_retries", 0)
            if r < 30:
                _slop_embed_state["_embed_retries"] = r + 1
                _slop_embed_status.set(f"Waiting for Slopsmith window… ({r+1}/30)")
                root.after(2000, _slop_try_embed)
            else:
                _slop_embed_status.set("Couldn’t find Slopsmith window. Try clicking  ↺ Re-embed.")
            return

        h = hwnd[0]
        _slop_embed_state["hwnd"] = h
        _slop_embed_state["_embed_retries"] = 0

        SW_HIDE = 0; SW_SHOW = 5
        GWL_STYLE = -16; WS_CAPTION = 0x00C00000; WS_THICKFRAME = 0x00040000
        WS_CHILD = 0x40000000; WS_VISIBLE = 0x10000000

        # Hide window immediately so it doesn't flash on screen
        ctypes.windll.user32.ShowWindow(h, SW_HIDE)

        # Strip title bar / resize border, reparent, position — all while hidden
        ctypes.windll.user32.SetWindowLongW(h, GWL_STYLE,
            WS_CHILD | WS_VISIBLE)
        parent_hwnd = slop_embed_canvas.winfo_id()
        ctypes.windll.user32.SetParent(h, parent_hwnd)
        cw = slop_embed_canvas.winfo_width() or 1200
        ch = slop_embed_canvas.winfo_height() or 700
        ctypes.windll.user32.MoveWindow(h, 0, 0, cw, ch, False)

        # Now show it in-place — no external flash
        ctypes.windll.user32.ShowWindow(h, SW_SHOW)
        ctypes.windll.user32.UpdateWindow(h)

        _slop_embed_status.set("✓ Slopsmith embedded.")
        try: _slop_placeholder.place_forget()
        except Exception: pass
        root.after(0, lambda: btn_slop_detach.configure(state="normal"))
        root.after(0, lambda: btn_slop_embed.configure(text="↺ Re-embed", state="normal"))
        root.after(400, lambda: ctypes.windll.user32.SetFocus(h))
        slop_embed_canvas.bind("<Button-1>", lambda e: ctypes.windll.user32.SetFocus(h), add="+")
        _slop_embed_state["_keep_alive"] = True
        _slop_keep_embedded()

    def _slop_keep_embedded():
        if not _slop_embed_state.get("_keep_alive"): return
        h = _slop_embed_state.get("hwnd", 0)
        if not h: return
        import ctypes, ctypes.wintypes
        try:
            parent_hwnd = slop_embed_canvas.winfo_id()
            cw = slop_embed_canvas.winfo_width() or 1200
            ch = slop_embed_canvas.winfo_height() or 700
            if ctypes.windll.user32.GetParent(h) != parent_hwnd:
                GWL_STYLE = -16; WS_CHILD = 0x40000000; WS_VISIBLE = 0x10000000
                ctypes.windll.user32.SetParent(h, parent_hwnd)
                ctypes.windll.user32.SetWindowLongW(h, GWL_STYLE, WS_CHILD | WS_VISIBLE)
                ctypes.windll.user32.MoveWindow(h, 0, 0, cw, ch, True)
            else:
                rect = ctypes.wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(h, ctypes.byref(rect))
                if (rect.right-rect.left) != cw or (rect.bottom-rect.top) != ch:
                    ctypes.windll.user32.MoveWindow(h, 0, 0, cw, ch, True)
        except Exception: pass
        root.after(3000, _slop_keep_embedded)

    def _slop_on_resize(event):
        h = _slop_embed_state.get("hwnd", 0)
        if not h: return
        import ctypes
        try: ctypes.windll.user32.MoveWindow(h, 0, 0, event.width, event.height, True)
        except Exception: pass

    def _slop_detach():
        import ctypes
        _slop_embed_state["_keep_alive"] = False
        h = _slop_embed_state.get("hwnd", 0)
        if h:
            ctypes.windll.user32.SetParent(h, 0)
            ctypes.windll.user32.SetWindowLongW(h, -16, 0x00CF0000 | 0x10000000)
            ctypes.windll.user32.ShowWindow(h, 9)
            _slop_embed_state["hwnd"] = 0; _slop_embed_state["pid"] = 0
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

    # ══════════════════════════════════════════════════════════════════════
    # PAGE: SYNC & VERIFY
    # ══════════════════════════════════════════════════════════════════════
    # ══════════════════════════════════════════════════════════════════════
    # PAGE: SYNC & VERIFY  (tab viewer, lyrics, track select, BPM factor)
    # ══════════════════════════════════════════════════════════════════════
    sync_page = styled(tk.Frame(page_container), bg="surface")
    pages["sync"] = sync_page
    page_header(sync_page, "Sync & Verify  —  Guitar Tab Preview")

    # ── shared state ─────────────────────────────────────────────────────
    _sv = {
        "gp":           None,       # loaded GPSong object
        "track_idx":    0,
        "notes":        [],         # list of (time, string_num, fret)
        "bar_times":    [],         # (t, quarters, bpm) per bar
        "lrc":          [],         # (time, text) pairs from .lrc
        "audio_dur":    0.0,
        "n_strings":    6,
        "proc":         None,       # ffplay process
        "t0":           None,       # monotonic() at play start
        "offset":       0.0,        # seconds when paused/seeked
        "after_id":     None,
        "loading":      False,
        "track_leadins": {},        # {ti: float} per-track leadin overrides
        "vol":           70,        # ffplay volume 0-100
    }

    def _sv_get_leadin(ti=None):
        """Return leadin for a track — per-track override if set, else global value."""
        if ti is None:
            ti = _sv["track_idx"]
        v = _sv["track_leadins"].get(ti)
        if v is None:
            try: v = float(vars_["leadin"].get() or 5)
            except: v = 5.0
        return max(0.0, v)

    sync_status_var = tk.StringVar(value="Load a GP file and audio on the Main tab, then switch here to verify sync.")

    def _sv_set_status(msg):
        try: sync_status_var.set(msg)
        except Exception: pass

    def _sv_elapsed():
        if _sv["t0"] is not None:
            return time.monotonic() - _sv["t0"] + _sv["offset"]
        return _sv["offset"]

    # ── top control bar ───────────────────────────────────────────────────
    sv_ctrl = styled(tk.Frame(sync_page), bg="surface")
    sv_ctrl.pack(fill="x", padx=28, pady=(0, 6))

    styled(tk.Label(sv_ctrl, text="Track:", font=("Segoe UI", 9)), fg="muted", bg="surface").pack(side="left")
    sv_track_var = tk.StringVar(value="(none)")
    sv_track_cb = ttk.Combobox(sv_ctrl, textvariable=sv_track_var, state="readonly", width=32, font=("Segoe UI", 9))
    sv_track_cb.pack(side="left", padx=(4, 8))

    # per-track leadin display (inline with track selector)
    sv_leadin_var = tk.StringVar(value="5.00")
    styled(tk.Label(sv_ctrl, text="Leadin:", font=("Segoe UI", 9)), fg="muted", bg="surface").pack(side="left", padx=(4, 2))
    styled(tk.Label(sv_ctrl, textvariable=sv_leadin_var,
                    font=("Segoe UI Semibold", 10), width=6),
           fg="accent", bg="surface").pack(side="left")
    styled(tk.Label(sv_ctrl, text="s  (min 5.0)", font=("Segoe UI", 8)), fg="dim", bg="surface").pack(side="left", padx=(0, 16))

    styled(tk.Label(sv_ctrl, text="BPM scale:", font=("Segoe UI", 9)), fg="muted", bg="surface").pack(side="left")
    sv_bpm_entry = styled(tk.Entry(sv_ctrl, textvariable=vars_["bpm_factor"],
                                   width=7, relief="flat", font=("Segoe UI", 10),
                                   bd=4, highlightthickness=0),
                          bg="field", fg="fg", insertbackground="accent_hi")
    sv_bpm_entry.pack(side="left", padx=(4, 4))

    sv_bpm_lbl = styled(tk.Label(sv_ctrl, text="×1.000  (drag slider to fine-tune)", font=("Segoe UI", 8)),
                        fg="dim", bg="surface")
    sv_bpm_lbl.pack(side="left", padx=(2, 10))

    sv_bpm_scale = tk.Scale(sv_ctrl, from_=0.850, to=1.150, resolution=0.001,
                             orient="horizontal", length=200,
                             bg=COL["surface"], fg=COL["fg"],
                             troughcolor=COL["card"], highlightthickness=0,
                             showvalue=False, command=lambda v: vars_["bpm_factor"].set(f"{float(v):.3f}"))
    sv_bpm_scale.set(1.0)
    sv_bpm_scale.pack(side="left")

    def _sv_bpm_entry_changed(*_):
        try:
            v = float(vars_["bpm_factor"].get())
            sv_bpm_scale.set(v)
            sv_bpm_lbl.configure(text=f"×{v:.3f}")
            _sv_reparse_notes()
        except ValueError:
            pass
    vars_["bpm_factor"].trace_add("write", _sv_bpm_entry_changed)

    # ── lyrics strip ──────────────────────────────────────────────────────
    sv_lyric_var = tk.StringVar(value="")
    sv_lyric_lbl = styled(tk.Label(sync_page, textvariable=sv_lyric_var,
                                   font=("Segoe UI Semibold", 11), wraplength=1200),
                          fg="accent", bg="surface")
    sv_lyric_lbl.pack(anchor="center", pady=(0, 4))

    # ── scrubber / timeline ───────────────────────────────────────────────
    sv_scrub = tk.Canvas(sync_page, height=44, bg=COL["card2"], highlightthickness=0, cursor="hand2")
    sv_scrub.pack(fill="x", padx=28, pady=(0, 2))

    def _sv_draw_scrubber():
        sv_scrub.delete("all")
        cw = sv_scrub.winfo_width() or 900
        ch = sv_scrub.winfo_height() or 44
        dur = _sv["audio_dur"]
        elapsed = max(0.0, min(_sv_elapsed(), dur)) if dur > 0 else 0.0
        ld  = _sv_get_leadin()
        PAD = 64
        MID = ch // 2          # vertical centre
        THICK = 8              # track bar height
        T0 = MID - THICK // 2
        T1 = MID + THICK // 2
        track_w = cw - PAD * 2

        # click-hint label when no audio loaded
        if dur <= 0:
            audio_set = bool(vars_["audio"].get() and os.path.isfile(vars_["audio"].get()))
            msg = ("Audio found — click  Reload  to refresh this tab"
                   if audio_set else
                   "No audio loaded — auto-fetch audio on the Main tab first")
            sv_scrub.create_text(cw // 2, MID, text=msg,
                                 fill=COL["accent"] if audio_set else COL["dim"],
                                 font=("Segoe UI", 9), anchor="center")
            if audio_set:
                root.after(100, _sv_load)
            return

        # track background
        sv_scrub.create_rectangle(PAD, T0, cw - PAD, T1,
                                  fill=COL["border"], outline="")

        # leadin zone (dark tint on the track bar)
        if ld > 0:
            ld_x = PAD + (ld / dur) * track_w
            ld_x = max(PAD, min(ld_x, cw - PAD))
            sv_scrub.create_rectangle(PAD, T0, ld_x, T1, fill="#2a1800", outline="")
            sv_scrub.create_line(ld_x, MID - 10, ld_x, MID + 10, fill="#7a4800", width=1)

        # progress fill
        frac   = elapsed / dur
        fill_x = PAD + frac * track_w
        sv_scrub.create_rectangle(PAD, T0, fill_x, T1, fill=COL["accent"], outline="")

        # playhead dot
        DOT = 10
        sv_scrub.create_oval(fill_x - DOT, MID - DOT, fill_x + DOT, MID + DOT,
                             fill=COL["accent_hi"], outline=COL["fg"], width=1)

        def _fmt(s):
            s = max(0, int(s)); return f"{s//60}:{s%60:02d}"
        sv_scrub.create_text(PAD - 6, MID, text=_fmt(elapsed), anchor="e",
                             fill=COL["fg"], font=("Segoe UI", 9, "bold"))
        sv_scrub.create_text(cw - PAD + 6, MID, text=_fmt(dur), anchor="w",
                             fill=COL["dim"], font=("Segoe UI", 8))

    _scrub_drag = {"was_playing": False}

    def _sv_scrub_press(event):
        _scrub_drag["was_playing"] = _sv["t0"] is not None
        if _scrub_drag["was_playing"]: _sv_stop()

    def _sv_scrub_move(event):
        dur = _sv["audio_dur"]
        if dur <= 0: return
        cw  = max(sv_scrub.winfo_width(), 1)
        PAD = 64
        frac = max(0.0, min(1.0, (event.x - PAD) / max(1, cw - PAD * 2)))
        _sv["offset"] = frac * dur
        _sv_draw_scrubber()
        _sv_draw_tab()

    def _sv_scrub_release(event):
        _sv_scrub_move(event)
        if _scrub_drag["was_playing"]: root.after(60, _sv_play)

    sv_scrub.bind("<Button-1>",       _sv_scrub_press)
    sv_scrub.bind("<B1-Motion>",      _sv_scrub_move)
    sv_scrub.bind("<ButtonRelease-1>", _sv_scrub_release)
    sv_scrub.bind("<Configure>",  lambda e: _sv_draw_scrubber())

    # ── tab canvas ────────────────────────────────────────────────────────
    TAB_H    = 175
    TAB_PAD  = 22   # top/bottom padding inside canvas
    VIEW_SEC = 14.0  # seconds visible
    AHEAD    = 0.3   # fraction of canvas width = playhead position

    sv_tab = tk.Canvas(sync_page, height=TAB_H, bg="#070c15", highlightthickness=0, cursor="crosshair")
    sv_tab.pack(fill="x", padx=28, pady=(0, 6))

    def _sv_string_ys(canvas_h=None, n=None):
        h = canvas_h or sv_tab.winfo_height() or TAB_H
        n = n or _sv["n_strings"] or 6
        pad = TAB_PAD
        span = h - pad * 2
        gap  = span / max(1, n - 1)
        return [pad + i * gap for i in range(n)]  # index 0 = high e (top)

    def _sv_draw_tab():
        sv_tab.delete("all")
        cw = sv_tab.winfo_width() or 900
        ch = sv_tab.winfo_height() or TAB_H
        n  = _sv["n_strings"]
        ys = _sv_string_ys(ch, n)
        px = cw * AHEAD  # playhead x
        cur = _sv_elapsed()
        pps = cw / VIEW_SEC  # pixels per second
        t_left  = cur - px / pps
        t_right = cur + (cw - px) / pps

        # String labels (e B G D A E  etc.)
        STD_NAMES = ["e","B","G","D","A","E"]
        str_labels = STD_NAMES[:n] if n <= 6 else [str(i) for i in range(n)]

        # Draw string lines
        for i, y in enumerate(ys):
            lw = 1 if i > 0 else 1
            sv_tab.create_line(0, y, cw, y, fill="#2a2a44", width=lw, tags="strings")
            sv_tab.create_text(10, y, text=str_labels[i], fill="#445566",
                               font=("Courier", 8), anchor="center", tags="strings")

        # Song-start line (right edge of leadin zone)
        ld = _sv_get_leadin()
        ld_x = px + (ld - cur) * pps
        if 0 < ld_x < cw:
            sv_tab.create_line(ld_x, 0, ld_x, ch, fill="#5a3a00", width=1, dash=(4, 3))
            sv_tab.create_text(ld_x + 3, 8, text="song start", anchor="w",
                               fill="#7a5010", font=("Segoe UI", 8))

        # Playhead
        sv_tab.create_line(px, 4, px, ch-4, fill=COL["accent"], width=2)
        sv_tab.create_rectangle(px-1, 0, px+1, ch, fill=COL["accent"], outline="")

        # Draw notes
        notes = _sv["notes"]
        if not notes:
            sv_tab.create_text(cw//2, ch//2, text="No notes loaded — click  Reload  to parse GP file",
                               fill="#445566", font=("Segoe UI", 10))
            return

        for (t, s, fret) in notes:
            if t < t_left - 0.5 or t > t_right + 0.5:
                continue
            s = max(0, min(s, n - 1))  # clamp instead of skip
            x = px + (t - cur) * pps
            y = ys[s]
            played = t < cur
            fg_col = "#3a3a5a" if played else COL["accent"]
            sv_tab.create_rectangle(x-10, y-9, x+10, y+9,
                                    fill="#0a0f20" if played else "#0d1830",
                                    outline=fg_col if not played else "#2a2a44", width=1)
            sv_tab.create_text(x, y, text=str(fret),
                               fill=fg_col, font=("Courier", 9, "bold"))

    def _sv_update_lyric():
        if not _sv["lrc"]:
            sv_lyric_var.set("")
            return
        cur = _sv_elapsed()
        best = ""
        for (t, txt) in _sv["lrc"]:
            if t <= cur:
                best = txt
        sv_lyric_var.set(best)

    def _sv_tick():
        if _sv["t0"] is not None:
            _sv_draw_tab()
            _sv_draw_scrubber()
            _sv_update_lyric()
            dur = _sv["audio_dur"]
            if dur > 0 and _sv_elapsed() >= dur:
                _sv["t0"] = None
                _sv["offset"] = 0.0
                _sv["proc"] = None
                try: sv_play_btn.configure(text="▶  Play")
                except Exception: pass
                _sv_draw_scrubber()
                return
            _sv["after_id"] = root.after(80, _sv_tick)

    # ── canvas events ─────────────────────────────────────────────────────
    _tab_drag = {"was_playing": False, "anchor_x": 0, "anchor_t": 0.0}

    def _sv_tab_press(event):
        _tab_drag["was_playing"] = _sv["t0"] is not None
        _tab_drag["anchor_x"] = event.x
        _tab_drag["anchor_t"] = _sv_elapsed()
        if _tab_drag["was_playing"]: _sv_stop()

    def _sv_tab_move(event):
        dur = _sv["audio_dur"]
        if dur <= 0: return
        cw  = max(sv_tab.winfo_width(), 1)
        pps = cw / VIEW_SEC
        t   = _tab_drag["anchor_t"] + (event.x - _tab_drag["anchor_x"]) / pps
        t   = max(0.0, min(t, dur))
        _sv["offset"] = t
        _sv_draw_scrubber()
        _sv_draw_tab()

    def _sv_tab_release(event):
        _sv_tab_move(event)
        if _tab_drag["was_playing"]: root.after(60, _sv_play)

    sv_tab.bind("<Button-1>",       _sv_tab_press)
    sv_tab.bind("<B1-Motion>",      _sv_tab_move)
    sv_tab.bind("<ButtonRelease-1>", _sv_tab_release)
    sv_tab.bind("<Configure>", lambda e: _sv_draw_tab())

    # ── note/bar parsing ──────────────────────────────────────────────────
    def _sv_reparse_notes():
        gp = _sv.get("gp")
        if not gp: return
        ti    = _sv["track_idx"]
        ld    = _sv_get_leadin(ti)
        bfs   = float(vars_["bpm_factor"].get() or 1.0)
        try:
            beats, bar_times = gp.track_beats(ti, ld, bpm_scale=bfs)
            notes = []
            tie_count = 0
            for b in beats:
                for n in b.notes:
                    if not n.tie_dest:
                        notes.append((b.start, n.string, n.fret))
                    else:
                        tie_count += 1
            _sv["notes"]     = notes
            _sv["bar_times"] = bar_times
            n_str = _sv["n_strings"]
            out_of_range = sum(1 for (_, s, _) in notes if s < 0 or s >= n_str)
            _sv_set_status(
                f"Track {ti}: {len(beats)} beats · {len(notes)} notes "
                f"({tie_count} ties skipped · {out_of_range} out-of-range) · "
                f"n_strings={n_str} · leadin={ld:.2f}s")
            _sv_draw_tab()
        except Exception as ex:
            import traceback as _tb
            _sv_set_status(f"Parse error track {ti}: {ex} — {_tb.format_exc().splitlines()[-1]}")

    def _sv_load():
        if _sv["loading"]: return
        gp_path    = vars_["gp"].get()
        audio_path = vars_["audio"].get()
        lrc_path   = vars_["lyrics"].get()
        if not gp_path or not os.path.isfile(gp_path):
            _sv_set_status("No GP file loaded — set one on the Main tab first.")
            return
        _sv["loading"] = True
        _sv_set_status("Loading GP file…")

        def _worker():
            try:
                gp = gp2rs.GPSong(gp_path)
                track_names = gp.track_names()
                _sv["gp"] = gp
                _sv["n_strings"] = len(gp.tuning(_sv["track_idx"]))
                dur = 0.0
                if audio_path and os.path.isfile(audio_path):
                    dur = get_wav_duration(audio_path) or 0.0
                _sv["audio_dur"] = dur
                # Parse .lrc / .txt into (time, text) pairs
                lrc = []
                if lrc_path and os.path.isfile(lrc_path):
                    try:
                        import re as _re
                        with open(lrc_path, "r", encoding="utf-8") as f:
                            raw_lines = f.readlines()
                        for line in raw_lines:
                            m = _re.match(r"\[(\d+):(\d+\.\d+)\](.*)", line.strip())
                            if m:
                                t = int(m.group(1)) * 60 + float(m.group(2))
                                try:
                                    t += float(vars_["lrc_offset"].get() or 0.0)
                                except Exception:
                                    pass
                                lrc.append((max(0.0, t), m.group(3).strip()))
                        lrc.sort(key=lambda x: x[0])
                        # No timestamps found — spread lines evenly across the song
                        if not lrc:
                            plain = [l.strip() for l in raw_lines
                                     if l.strip() and not _re.match(r"^\[.{0,40}\]$", l.strip())]
                            _d = dur if dur > 0 else 240.0
                            try: leadin = float(vars_["leadin"].get() or 5)
                            except: leadin = 5.0
                            usable = max(10.0, _d - leadin - 5.0)
                            step = max(1.0, usable / max(1, len(plain)))
                            for i, txt in enumerate(plain):
                                lrc.append((leadin + i * step, txt))
                    except Exception: pass
                _sv["lrc"] = lrc
                # Auto-select: prefer track named "lead", else first guitar/bass
                lead_ti = None; first_guitar_ti = None
                for _ti in range(len(gp.tracks)):
                    k = gp.track_kind(_ti)
                    if k in ("guitar", "bass"):
                        if first_guitar_ti is None:
                            first_guitar_ti = _ti
                        if "lead" in track_names[_ti].lower() and lead_ti is None:
                            lead_ti = _ti
                best_ti = lead_ti if lead_ti is not None else (first_guitar_ti if first_guitar_ti is not None else 0)
                _sv["track_idx"] = best_ti
                _sv["n_strings"] = len(gp.tuning(best_ti))
                # Populate track dropdown — show kind tag after name
                def _tagged(i, nm):
                    k = gp.track_kind(i)
                    tag = {"guitar": " [guitar]", "bass": " [bass]"}.get(k, "")
                    return nm + tag
                display_names = [_tagged(i, nm) for i, nm in enumerate(track_names)]
                _sv["_display_names"] = display_names
                root.after(0, lambda: sv_track_cb.configure(values=display_names))
                if display_names:
                    root.after(0, lambda: sv_track_var.set(display_names[_sv["track_idx"]]))
                _ld_init = _sv_get_leadin(best_ti)
                root.after(0, lambda: sv_leadin_var.set(f"{_ld_init:.2f}"))
                root.after(0, _sv_reparse_notes)
                nd = len(gp.track_beats(_sv["track_idx"],
                                        _sv_get_leadin(_sv["track_idx"]))[0])
                msg = (f"Loaded — {len(gp.masterbars)} bars · {nd} beats · "
                       f"audio={dur:.1f}s · {len(lrc)} lyric lines")
                root.after(0, lambda: _sv_set_status(msg))
            except Exception as ex:
                root.after(0, lambda: _sv_set_status(f"Error: {ex}"))
            finally:
                _sv["loading"] = False

        threading.Thread(target=_worker, daemon=True).start()

    def _sv_on_track_change(event=None):
        names = list(sv_track_cb["values"])
        sel   = sv_track_var.get()
        if sel in names:
            ti = names.index(sel)
            _sv["track_idx"] = ti
            gp = _sv.get("gp")
            if gp:
                _sv["n_strings"] = len(gp.tuning(ti))
            sv_leadin_var.set(f"{_sv_get_leadin(ti):.2f}")
            _sv_reparse_notes()
    sv_track_cb.bind("<<ComboboxSelected>>", _sv_on_track_change)

    # ── per-track leadin nudge ────────────────────────────────────────────
    sv_nudge_row = styled(tk.Frame(sync_page), bg="surface")
    sv_nudge_row.pack(anchor="center", pady=(2, 8))

    styled(tk.Label(sv_nudge_row, text="Track leadin:", font=("Segoe UI", 9)),
           fg="muted", bg="surface").pack(side="left", padx=(0, 8))

    for (_d, _lbl) in [(-0.5,"◀ 0.5s"), (-0.1,"◀ 0.1s"), (-0.05,"◀ 0.05s")]:
        tk.Button(sv_nudge_row, text=_lbl, font=("Segoe UI", 9), relief="flat", bd=0,
                  padx=8, pady=4, cursor="hand2",
                  bg=COL["card"], fg=COL["fg"], activebackground=COL["card2"],
                  command=lambda d=_d: _sv_nudge(d)).pack(side="left", padx=2)

    styled(tk.Label(sv_nudge_row, textvariable=sv_leadin_var,
                    font=("Segoe UI Semibold", 11), width=7),
           fg="accent", bg="surface").pack(side="left", padx=8)

    for (_d, _lbl) in [(0.05,"0.05s ▶"), (0.1,"0.1s ▶"), (0.5,"0.5s ▶")]:
        tk.Button(sv_nudge_row, text=_lbl, font=("Segoe UI", 9), relief="flat", bd=0,
                  padx=8, pady=4, cursor="hand2",
                  bg=COL["card"], fg=COL["fg"], activebackground=COL["card2"],
                  command=lambda d=_d: _sv_nudge(d)).pack(side="left", padx=2)

    # "Apply to build" writes this track's leadin back to the global build field
    def _sv_apply_leadin():
        ti = _sv["track_idx"]
        v = _sv_get_leadin(ti)
        vars_["leadin"].set(f"{v:.2f}")
        _sv_set_status(f"Leadin {v:.2f}s applied to build settings.")
    tk.Button(sv_nudge_row, text="Apply to build →", font=("Segoe UI", 9),
              relief="flat", bd=0, padx=10, pady=4, cursor="hand2",
              bg=COL["accent"], fg="#ffffff", activebackground=COL["accent_hi"],
              command=_sv_apply_leadin).pack(side="left", padx=(16, 2))

    def _sv_nudge(delta):
        ti = _sv["track_idx"]
        cur = _sv_get_leadin(ti)
        nv = max(0.0, round(cur + delta, 3))
        _sv["track_leadins"][ti] = nv
        sv_leadin_var.set(f"{nv:.2f}")
        _sv_reparse_notes()

    # ── lyrics offset nudge ──────────────────────────────────────────────
    sv_lrc_row = styled(tk.Frame(sync_page), bg="surface")
    sv_lrc_row.pack(anchor="center", pady=(0, 6))

    styled(tk.Label(sv_lrc_row, text="Lyrics offset:", font=("Segoe UI", 9)),
           fg="muted", bg="surface").pack(side="left", padx=(0, 8))

    def _sv_lrc_nudge(delta):
        cur = float(vars_["lrc_offset"].get() or 0.0)
        nv = round(cur + delta, 2)
        vars_["lrc_offset"].set(f"{nv:.2f}")
        sign = "+" if nv >= 0 else ""
        sv_lrc_val_var.set(f"{sign}{nv:.2f}")
        # Re-parse LRC with new offset so the preview updates immediately
        _sv_reload_lrc()

    def _sv_reload_lrc():
        """Re-read the LRC file applying current lrc_offset, refresh Sync tab display."""
        lrc_path = vars_["lyrics"].get()
        if not lrc_path or not os.path.isfile(lrc_path):
            return
        import re as _re
        lrc = []
        try:
            with open(lrc_path, "r", encoding="utf-8") as f:
                raw_lines = f.readlines()
            for line in raw_lines:
                m = _re.match(r"\[(\d+):(\d+\.\d+)\](.*)", line.strip())
                if m:
                    t = int(m.group(1)) * 60 + float(m.group(2))
                    try:
                        t += float(vars_["lrc_offset"].get() or 0.0)
                    except Exception:
                        pass
                    lrc.append((max(0.0, t), m.group(3).strip()))
            lrc.sort(key=lambda x: x[0])
            # Plain-text fallback: spread lines evenly (same as main load path)
            if not lrc:
                plain = [l.strip() for l in raw_lines
                         if l.strip() and not _re.match(r"^\[.{0,40}\]$", l.strip())]
                _d = _sv["audio_dur"] if _sv["audio_dur"] > 0 else 240.0
                try: leadin = float(vars_["leadin"].get() or 5)
                except: leadin = 5.0
                lrc_off = 0.0
                try: lrc_off = float(vars_["lrc_offset"].get() or 0.0)
                except: pass
                usable = max(10.0, _d - leadin - 5.0)
                step = max(1.0, usable / max(1, len(plain)))
                for i, txt in enumerate(plain):
                    lrc.append((max(0.0, leadin + i * step + lrc_off), txt))
            if lrc:  # only overwrite if we actually parsed something
                _sv["lrc"] = lrc
            _sv_update_lyric()
        except Exception:
            pass

    for (_d, _lbl) in [(-1.0, "◀ 1s"), (-0.1, "◀ 0.1s"), (-0.05, "◀ 0.05s")]:
        tk.Button(sv_lrc_row, text=_lbl, font=("Segoe UI", 9), relief="flat", bd=0,
                  padx=8, pady=4, cursor="hand2",
                  bg=COL["card"], fg=COL["fg"], activebackground=COL["card2"],
                  command=lambda d=_d: _sv_lrc_nudge(d)).pack(side="left", padx=2)

    sv_lrc_val_var = tk.StringVar(value="+0.00")
    styled(tk.Label(sv_lrc_row, textvariable=sv_lrc_val_var,
                    font=("Segoe UI Semibold", 11), width=7),
           fg="accent", bg="surface").pack(side="left", padx=8)

    for (_d, _lbl) in [(0.05, "0.05s ▶"), (0.1, "0.1s ▶"), (1.0, "1s ▶")]:
        tk.Button(sv_lrc_row, text=_lbl, font=("Segoe UI", 9), relief="flat", bd=0,
                  padx=8, pady=4, cursor="hand2",
                  bg=COL["card"], fg=COL["fg"], activebackground=COL["card2"],
                  command=lambda d=_d: _sv_lrc_nudge(d)).pack(side="left", padx=2)

    def _sv_reset_lrc():
        vars_["lrc_offset"].set("0.00")
        sv_lrc_val_var.set("+0.00")
        _sv_reload_lrc()

    tk.Button(sv_lrc_row, text="↺ Reset", font=("Segoe UI", 9), relief="flat", bd=0,
              padx=10, pady=4, cursor="hand2",
              bg=COL["card2"], fg=COL["muted"], activebackground=COL["border_hi"],
              command=_sv_reset_lrc).pack(side="left", padx=(16, 2))

    # ── action row ────────────────────────────────────────────────────────
    sv_act = styled(tk.Frame(sync_page), bg="surface")
    sv_act.pack(anchor="center", pady=(0, 8))

    def _sv_play():
        audio_path = vars_["audio"].get()
        if not audio_path or not os.path.isfile(audio_path):
            _sv_set_status("No audio file."); return

        # Find ffplay — check PATH, then next to ffmpeg.exe, then app folder
        ffplay = _find_exe("ffplay")
        if not ffplay:
            ffmpeg_path = _find_exe("ffmpeg")
            if ffmpeg_path:
                candidate = os.path.join(os.path.dirname(ffmpeg_path), "ffplay.exe")
                if os.path.isfile(candidate):
                    ffplay = candidate
        if not ffplay:
            here = os.path.dirname(os.path.abspath(__file__))
            candidate = os.path.join(here, "ffplay.exe")
            if os.path.isfile(candidate):
                ffplay = candidate

        if not ffplay:
            _sv_set_status("⚠ ffplay not found — downloading now…")
            ensure_dependencies_then_run(_sv_play)
            return

        off = _sv["offset"]
        vol = int(_sv.get("vol", 70))
        p = subprocess.Popen(
            [ffplay, "-nodisp", "-autoexit", "-volume", str(vol), "-ss", str(off), audio_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW)
        _sv["proc"] = p
        _sv["t0"]   = time.monotonic()
        try: sv_play_btn.configure(text="⏹  Stop")
        except Exception: pass
        _sv_tick()

    def _sv_stop():
        p = _sv.get("proc")
        if p:
            try: subprocess.run(["taskkill","/F","/T","/PID",str(p.pid)],
                                capture_output=True, creationflags=CREATE_NO_WINDOW)
            except Exception: pass
        _sv["proc"] = None
        _sv["t0"]   = None
        if _sv.get("after_id"):
            root.after_cancel(_sv["after_id"]); _sv["after_id"] = None
        try: sv_play_btn.configure(text="▶  Play")
        except Exception: pass

    def _sv_play_stop():
        if _sv["t0"] is not None: _sv_stop()
        else: _sv_play()

    def _sync_play_stop_if_running():
        if _sv["t0"] is not None: _sv_stop()

    sv_play_btn = tk.Button(sv_act, text="▶  Play", font=("Segoe UI Semibold", 10),
                            relief="flat", bd=0, padx=14, pady=8, cursor="hand2",
                            bg=COL["card"], fg=COL["fg"], activebackground=COL["card2"],
                            command=_sv_play_stop)
    sv_play_btn.pack(side="left", padx=(0, 6))

    tk.Button(sv_act, text="⟳  Reload", font=("Segoe UI", 10),
              relief="flat", bd=0, padx=14, pady=8, cursor="hand2",
              bg=COL["card"], fg=COL["fg"], activebackground=COL["card2"],
              command=_sv_load).pack(side="left", padx=(0, 6))

    # Volume slider
    styled(tk.Label(sv_act, text="🔊", font=("Segoe UI", 11)), bg="surface").pack(side="left", padx=(12, 2))
    _sv_vol_var = tk.IntVar(value=70)
    _vol_pending = [None]   # pending throttled-restart timer id
    def _vol_restart_now():
        _vol_pending[0] = None
        if _sv["t0"] is not None:               # only if something is playing
            off = _sv_elapsed()
            _sv_stop()
            _sv["offset"] = off
            root.after(15, _sv_play)
    def _on_vol_change(*_):
        # ffplay's volume is fixed at launch, so a live preview means relaunching.
        # Throttle that to ~once every 250ms while dragging: the volume tracks the
        # slider without the every-pixel thrash that froze playback before.
        _sv["vol"] = int(float(_sv_vol_var.get()))
        if _sv["t0"] is None or _vol_pending[0] is not None:
            return                               # not playing, or a restart is queued
        _vol_pending[0] = root.after(250, _vol_restart_now)
    def _apply_vol(*_):
        # On release, apply the final value immediately (cancel any queued restart).
        if _vol_pending[0] is not None:
            try: root.after_cancel(_vol_pending[0])
            except Exception: pass
            _vol_pending[0] = None
        _vol_restart_now()
    sv_vol_scale = ttk.Scale(sv_act, from_=0, to=100, orient="horizontal",
                             variable=_sv_vol_var, length=120, command=_on_vol_change)
    sv_vol_scale.bind("<ButtonRelease-1>", _apply_vol)
    sv_vol_scale.pack(side="left", padx=(0, 12))

    tk.Button(sv_act, text="✓  Build & Export PSARC",
              font=("Segoe UI Semibold", 10), relief="flat", bd=0,
              padx=14, pady=8, cursor="hand2",
              fg="white", bg=COL["accent"], activebackground=COL["accent_hi"],
              command=lambda: do_build()).pack(side="left", padx=(0, 6))

    def _sv_redownload_ffplay():
        """Extract ffplay.exe from the already-downloaded ffmpeg zip, or re-download."""
        here = os.path.dirname(os.path.abspath(__file__))
        ffplay_dest = os.path.join(here, "ffplay.exe")
        _sv_set_status("Re-downloading ffplay.exe…")
        def _dl():
            try:
                import urllib.request, zipfile, shutil as _sh
                zip_url = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/"
                           "ffmpeg-master-latest-win64-gpl.zip")
                zip_path = os.path.join(here, "_ffplay_tmp.zip")
                urllib.request.urlretrieve(zip_url, zip_path)
                with zipfile.ZipFile(zip_path, "r") as z:
                    for info in z.infolist():
                        if info.filename.endswith("ffplay.exe"):
                            with z.open(info) as zf, open(ffplay_dest, "wb") as f:
                                _sh.copyfileobj(zf, f)
                            break
                try: os.remove(zip_path)
                except Exception: pass
                if os.path.isfile(ffplay_dest):
                    root.after(0, lambda: _sv_set_status("✓ ffplay.exe installed — click Play to try."))
                else:
                    root.after(0, lambda: _sv_set_status("⚠ ffplay.exe not found in zip — unexpected zip structure."))
            except Exception as ex:
                root.after(0, lambda: _sv_set_status(f"Download failed: {ex}"))
        threading.Thread(target=_dl, daemon=True).start()

    tk.Button(sv_act, text="⬇ Get ffplay", font=("Segoe UI", 9), relief="flat", bd=0,
              padx=10, pady=8, cursor="hand2",
              bg=COL["card2"], fg=COL["dim"], activebackground=COL["card"],
              command=_sv_redownload_ffplay).pack(side="left")

    styled(tk.Label(sync_page, textvariable=sync_status_var, font=("Segoe UI", 9),
                    wraplength=1100, justify="left"),
           fg="muted", bg="surface").pack(anchor="w", padx=28, pady=(0, 6))

    styled(tk.Label(sync_page,
        text="TIP: BPM scale < 1.0 slows the note chart (use if notes run ahead of audio). "
             "Click the tab canvas to seek. Leadin nudge shifts the whole chart left/right.",
        font=("Segoe UI", 8), wraplength=1100, justify="left"),
        fg="dim", bg="surface").pack(anchor="w", padx=28)

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
        "RS Studio starts with a Guitar Pro (.gp) file that contains the song's notes and tempo map.",
        "",
        "• Click  + Start New Project  on the splash screen, or drag a .gp file directly onto the window.",
        "• RS Studio reads the title, artist, BPM, and track layout from the file automatically.",
        "• Non-guitar tracks (drums, saxophone, piano, etc.) are filtered out — only guitar and bass appear.",
    ])

    _help_section("Step 2 — Auto-Fetch Audio & Artwork", [
        "You need an audio file and album art to build a CDLC. RS Studio can grab both automatically.",
        "",
        "• Paste a YouTube URL into the Audio URL field and click  Auto-Fetch.",
        "• RS Studio downloads the audio, reads the video title to correct artist/song info, fetches album art from iTunes, and fetches synced lyrics — all without any extra clicks.",
        "• If the wrong video is found, paste a direct YouTube link before clicking Auto-Fetch.",
        "• You can also drag your own audio file (.wav/.ogg/.mp3) and album art image onto the fields.",
    ])

    _help_section("Step 3 — Lyrics & Timestamps", [
        "RS Studio can generate synced lyrics for your CDLC automatically using AI.",
        "",
        "• After Auto-Fetch, lyrics are downloaded from lrclib.net and AI timestamps are generated with Whisper — no clicking needed.",
        "• If timestamps feel slightly early or late, use the  Lyrics offset  slider in Sync & Verify to nudge them.",
        "• Drag left (−) to make lyrics appear earlier, drag right (+) to delay them. Click  ↺  to reset.",
        "• To re-run AI timestamps manually, click  🎙 Get Lyrics & Timestamps  on the Main page.",
    ])

    _help_section("Step 4 — Review Settings (Main Page)", [
        "Before building, check these key settings:",
        "",
        "• Title / Artist / Album / Year — filled automatically. Edit if anything looks wrong.",
        "• Song Delay (seconds) — silent gap before notes start. Default 5s is fine for most songs.",
        "• Volume — negative dB value. Default -8 dB. Lower numbers = quieter in-game.",
        "• Arrangements — check which tracks are set to Lead, Rhythm, or Bass. Uncheck any you don't want.",
        "• Output Folder — where project files are saved.",
    ])

    _help_section("Step 5 — Tune Timing (Sync & Verify)", [
        "If notes feel early or late when playing, use Sync & Verify to fix it.",
        "",
        "• The tab canvas shows your notes scrolling against the audio in real time — press  ▶ Play  to start.",
        "• Click or drag the timeline to seek. The scrubber at the top shows your position in the song.",
        "• Use  Track leadin  nudge buttons (◀ / ▶) to shift the entire note chart earlier or later.",
        "• Click  Apply to build →  when the timing feels right — this saves the leadin to your build settings.",
        "• Use the  Lyrics offset  slider to shift lyrics independently of the notes.",
        "• BPM scale stretches or compresses the note chart if the GP file tempo doesn't match the audio.",
    ])

    _help_section("Step 6 — Build the CDLC", [
        "Click  ✓ Build & Export PSARC  in the Sync & Verify page, or  ⚡ Build  on the Main page.",
        "",
        "• RS Studio converts audio, generates chart XML, and packages everything into a .psarc file.",
        "• Progress is shown in a popup window while the build runs.",
        "• When done, find your .psarc in the Output Folder you set on the Main page.",
    ])

    _help_section("Step 7 — Install in Rocksmith", [
        "Copy the .psarc file into your Rocksmith DLC folder.",
        "",
        "• Default Rocksmith DLC path:  Steam\\steamapps\\common\\Rocksmith2014\\dlc\\",
        "• Launch Rocksmith 2014 Remastered — the song appears in Learn a Song.",
        "• If it doesn't show up, make sure you have the CDLC enabler (D3DX9 patch) installed.",
    ])

    _help_section("Tips & Troubleshooting", [
        "• No audio after Auto-Fetch? Make sure yt-dlp.exe and ffmpeg.exe are in the same folder as the app.",
        "• Build failed? Check the Log page for the exact error. Common causes: missing packer path, bad audio format, or no arrangements selected.",
        "• Lyrics not showing in-game? Make sure the .lrc file path is set in the Lyrics field on the Main page.",
        "• Wrong song fetched from YouTube? Paste a direct YouTube link into Audio URL before clicking Auto-Fetch.",
        "• Volume too loud in-game? Lower the Volume field (e.g. -12 instead of -8) and rebuild.",
        "• AI timestamps require faster-whisper — RS Studio installs it automatically on first use.",
        "• Change theme colors anytime via the  ◐ Theme  tab — your accent color is saved between sessions.",
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
    _inspector_ref = [None]  # track open inspector modal

    def _show_media_inspector():
        # Prevent double-open — bring existing one to front if alive
        if _inspector_ref[0] is not None:
            try:
                if _inspector_ref[0].winfo_exists():
                    _inspector_ref[0].lift()
                    _inspector_ref[0].focus_set()
                    return
            except Exception:
                pass
            _inspector_ref[0] = None

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
        _inspector_ref[0] = modal

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
            _inspector_ref[0] = None
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
                    "title":  v_title,   # raw video title e.g. "THE PERFUME OF DECAY [OFFICIAL VIDEO]"
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
                                def _fetch_art_itunes(a=artist, t=title, d=song_dir, snap=img):
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
                # Always update album — fall back to "Single" rather than leaving "Single"
                vars_["album"].set(ym["album"] if ym.get("album") else "Single")
                if ym.get("year"):
                    vars_["year"].set(ym["year"])
                elif vars_["year"].get() in ("", _this_year):
                    pass  # keep current-year default rather than clearing
                # Only fill artist/title if not already set — keeps lrclib search clean
                # Derive clean artist + title from YouTube metadata.
                # YouTube Music structured fields are often wrong/truncated for regular YT videos,
                # so we also parse the raw video title and pick whichever version is longer.
                import re as _re_t
                _raw_vt   = ym.get("title", "")
                _clean_vt = _re_t.sub(r"[\[\(][^\]\)]*[\]\)]", "", _raw_vt).strip(" -|")
                _vt_artist, _vt_title = "", ""
                if " - " in _clean_vt:
                    _vt_artist, _vt_title = [p.strip() for p in _clean_vt.split(" - ", 1)]

                # Pick the LONGEST across: YouTube Music track tag, video-title parse, album-extracted title
                _ym_artist = (ym.get("artist") or "").strip()
                _ym_track  = (ym.get("track")  or "").strip()
                _ym_album  = (ym.get("album")  or "").strip()
                # Album often contains full title e.g. "The Perfume of Decay - Single" → extract it
                _album_title = _re_t.sub(
                    r'\s*[-–]\s*(Single|EP|Album|Deluxe.*|Remaster.*|Live.*|Acoustic.*)$',
                    '', _ym_album, flags=_re_t.IGNORECASE).strip()
                _yt_artist = (_ym_artist if len(_ym_artist) >= len(_vt_artist) else _vt_artist).title()
                _title_candidates = [c for c in [_ym_track, _vt_title, _album_title] if c]
                _yt_title = (max(_title_candidates, key=len) if _title_candidates else "").title()
                if not _yt_artist: _yt_artist = (_ym_artist or _vt_artist).title()
                if not _yt_title:  _yt_title  = (_ym_track  or _vt_title ).title()

                # Always update UI fields from YouTube — more reliable than GP file metadata
                if _yt_artist: vars_["artist"].set(_yt_artist)
                if _yt_title:  vars_["title"].set(_yt_title)

                # Cache for lrclib search — always use YouTube-sourced values, not GP metadata
                _yt_meta_cache[0] = {"artist": _yt_artist, "title": _yt_title}

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

        # Per-song subfolder derived from artist + title
        def _safe_fn(s): return re.sub(r'[^\w\- ]', '', s).strip().replace(' ', '_')[:40]
        # Prefer YouTube-sourced values (set by _confirm before _trigger_automagic)
        # over GP file metadata which is often truncated or wrong
        _yt_c = _yt_meta_cache[0]
        _sk_artist = (vars_["artist"].get().strip() or _yt_c.get("artist", "")).strip() or "Unknown"
        _sk_title  = (vars_["title"].get().strip()  or _yt_c.get("title",  "")).strip()  or "Unknown"
        song_key  = f"{_safe_fn(_sk_artist)}_{_safe_fn(_sk_title)}"
        _sv["song_key"] = song_key
        song_dir  = os.path.join(out_dir, song_key)
        os.makedirs(song_dir, exist_ok=True)

        # Auto-fetch album art from iTunes if not already set
        def _fetch_art():
            if vars_["art"].get() and os.path.isfile(vars_["art"].get()):
                return  # already have art
            try:
                import urllib.request, urllib.parse, json as _j
                _q  = urllib.parse.quote(f"{_sk_artist} {_sk_title}")
                _au = f"https://itunes.apple.com/search?term={_q}&media=music&limit=1&entity=song"
                with urllib.request.urlopen(_au, timeout=10) as _r:
                    _res = _j.loads(_r.read())
                if _res.get("results"):
                    _hit = _res["results"][0]
                    _thumb = _hit.get("artworkUrl100", "")
                    if _thumb:
                        # Upgrade to 600x600
                        _hires = _thumb.replace("100x100bb", "600x600bb")
                        _art_path = os.path.join(song_dir, "cover.jpg")
                        urllib.request.urlretrieve(_hires, _art_path)
                        root.after(0, lambda p=_art_path: vars_["art"].set(p))
                        log("  [art] ✓ Cover art downloaded from iTunes")
                    # Auto-correct title from iTunes — GP files are often truncated/wrong.
                    # iTunes trackName is authoritative for commercial releases.
                    _itunes_track = _hit.get("trackName", "").strip()
                    if _itunes_track:
                        _cur_title = vars_["title"].get().strip()
                        if not _cur_title or len(_itunes_track) > len(_cur_title):
                            root.after(0, lambda t=_itunes_track: vars_["title"].set(t))
                            log("  [art] ✓ Title corrected from iTunes: " + _itunes_track)
                    # Auto-fill artist from iTunes if blank
                    _itunes_artist = _hit.get("artistName", "").strip()
                    if _itunes_artist:
                        _cur_artist = vars_["artist"].get().strip()
                        if not _cur_artist:
                            root.after(0, lambda a=_itunes_artist: vars_["artist"].set(a))
                            log("  [art] ✓ Artist from iTunes: " + _itunes_artist)
                    # Auto-fill album name if still blank or generic.
                    # Strip trailing "- Single" / "- EP" — iTunes marks standalone
                    # releases this way but the in-game field should show the real album.
                    import re as _re_alb
                    _itunes_album = _hit.get("collectionName", "").strip()
                    _itunes_album = _re_alb.sub(
                        r'\s*[-–]\s*(Single|EP|Deluxe.*|Remaster.*|Live.*|Acoustic.*)$',
                        "", _itunes_album, flags=_re_alb.IGNORECASE).strip()
                    if _itunes_album:
                        _cur_album = vars_["album"].get().strip()
                        _cur_clean  = _re_alb.sub(
                            r'\s*[-–]\s*(Single|EP)$', "", _cur_album,
                            flags=_re_alb.IGNORECASE).strip()
                        if not _cur_clean or _cur_clean.lower() in ("single", "unknown", ""):
                            root.after(0, lambda a=_itunes_album: vars_["album"].set(a))
                            log("  [art] ✓ Album: " + _itunes_album)
            except Exception as _ae:
                log("  [art] Art fetch failed: " + str(_ae))
        threading.Thread(target=_fetch_art, daemon=True).start()

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
        title_var2  = tk.StringVar(value="⬇  Downloading Audio")
        tk.Label(dl_card, textvariable=title_var2, font=("Segoe UI Semibold", 12),
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
                dest_dir = song_dir  # already created above with correct name
                out_tmpl = os.path.join(dest_dir, f"{song_key}.%(ext)s")
                # Remove stale audio files from previous fetch of this song
                for f in os.listdir(dest_dir):
                    if f.startswith(song_key) and any(f.endswith(e) for e in (".wav",".mp3",".m4a",".webm",".opus")):
                        try: os.remove(os.path.join(dest_dir, f))
                        except Exception: pass

                _ff = _find_exe("ffmpeg")

                # Always update yt-dlp before downloading — YouTube breaks old versions frequently
                root.after(0, lambda: _safe_status("Updating yt-dlp…"))
                try:
                    subprocess.run([ytdlp, "-U"], capture_output=True,
                                   creationflags=CREATE_NO_WINDOW, timeout=90)
                except Exception:
                    pass

                def _run_ytdlp(client, fmt=None):
                    c = [ytdlp, "-x", "--no-playlist", "--no-warnings"]
                    if fmt:
                        c += ["-f", fmt]
                    if client:
                        c += ["--extractor-args", f"youtube:player_client={client}"]
                    if _ff:
                        c += ["--ffmpeg-location", os.path.dirname(_ff)]
                    c += ["-o", out_tmpl, confirmed_url]
                    return subprocess.run(c, capture_output=True,
                                          creationflags=CREATE_NO_WINDOW, timeout=300)

                # Try strategies in order until one succeeds
                _strategies = [
                    ("ios",          "bestaudio/best"),
                    ("web",          "bestaudio/best"),
                    ("mweb",         "bestaudio/best"),
                    (None,           "bestaudio/best"),
                    (None,           None),
                ]
                proc = None
                for _client, _fmt in _strategies:
                    root.after(0, lambda c=_client: _safe_status(
                        f"Downloading audio ({c or 'default'} client)…"))
                    proc = _run_ytdlp(_client, _fmt)
                    _stderr_txt = (proc.stderr or b"").decode("utf-8", errors="replace")
                    if proc.returncode == 0:
                        log(f"  [fetch] yt-dlp succeeded (client={_client}, fmt={_fmt})")
                        break
                    log(f"  [fetch] Strategy client={_client} fmt={_fmt} failed: "
                        f"{_stderr_txt[-200:]}")

                # Find the downloaded audio file. Restrict to AUDIO extensions only:
                # the song folder also holds .lrc/.txt/.dlc.xml/.dds, and a loose
                # "any song_key* file" match grabs the .lrc as if it were the audio
                # (it sorts before .opus), which then breaks the previewer and build.
                _AUD_EXTS = (".wav", ".mp3", ".m4a", ".webm", ".opus", ".ogg", ".flac", ".aac")
                raw_audio = None
                for f in sorted(os.listdir(dest_dir)):
                    if f.startswith(song_key) and f.lower().endswith(".wav"):
                        raw_audio = os.path.join(dest_dir, f); break
                if not raw_audio:
                    for f in sorted(os.listdir(dest_dir)):
                        if f.startswith(song_key) and f.lower().endswith(_AUD_EXTS):
                            raw_audio = os.path.join(dest_dir, f); break

                if not raw_audio or not os.path.exists(raw_audio):
                    err_detail = (proc.stderr or b"").decode("utf-8", errors="replace")[-600:]
                    log(f"  [fetch] yt-dlp stderr: {err_detail}")
                    any_audio = None
                    for f in sorted(os.listdir(dest_dir)):
                        if f.startswith(song_key) and f.lower().endswith(_AUD_EXTS):
                            any_audio = os.path.join(dest_dir, f); break
                    if any_audio and ffmpeg:
                        root.after(0, lambda: _safe_status("Converting audio to WAV..."))
                        wav_out = os.path.join(dest_dir, f"{song_key}.wav")
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

                # Convert to WAV if needed (Rocksmith requires PCM WAV)
                if raw_audio and not raw_audio.lower().endswith(".wav") and ffmpeg:
                    root.after(0, lambda: _safe_status("Converting to WAV…"))
                    wav_out = os.path.join(dest_dir, f"{song_key}.wav")
                    conv = subprocess.run(
                        [ffmpeg, "-y", "-i", raw_audio, "-ar", "48000",
                         "-ac", "2", "-sample_fmt", "s16", wav_out],
                        capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=120)
                    if os.path.exists(wav_out):
                        raw_audio = wav_out
                        log(f"  [fetch] Converted to WAV: {wav_out}")
                    else:
                        log(f"  [fetch] WAV conversion failed, using original: {raw_audio}")

                root.after(0, lambda: vars_["audio"].set(raw_audio))
                log(f"  [fetch] Audio saved: {raw_audio}")

                # Generate preview clip
                if ffmpeg:
                    root.after(0, lambda: _safe_status("Generating preview clip…"))
                    try:
                        prev_path = os.path.join(dest_dir, f"{song_key}_preview.wav")
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
                    # Use the longest/best title across: UI field (may be iTunes-corrected by now),
                    # YouTube cache. Longer string wins — GP metadata is often truncated.
                    _yt_cache = _yt_meta_cache[0]
                    _ui_artist = vars_["artist"].get().strip()
                    _ui_title  = vars_["title"].get().strip()
                    _yt_artist = _yt_cache.get("artist", "")
                    _yt_title  = _yt_cache.get("title",  "")
                    o_artist = (_ui_artist if len(_ui_artist) >= len(_yt_artist) else _yt_artist).strip()
                    o_title  = (_ui_title  if len(_ui_title)  >= len(_yt_title)  else _yt_title ).strip()
                    root.after(0, lambda: _safe_status("Fetching lyrics…"))
                    log(f"  [fetch] Looking up lyrics: artist='{o_artist}' title='{o_title}'")
                    _LRCLIB_HDR = {"User-Agent": "gp2rs-studio/2.0"}
                    synced_lrc = None
                    _saved_txt_path = None   # track plain-text save so Whisper trigger is reliable

                    import difflib as _dl
                    def _fuzzy(a, b):
                        """0-1 similarity ignoring case/punctuation."""
                        def _n(s): return re.sub(r"[^a-z0-9 ]", "", s.lower())
                        return _dl.SequenceMatcher(None, _n(a), _n(b)).ratio()

                    # Version qualifiers that indicate a different recording
                    _VERSION_TAGS = re.compile(
                        r'(redux|remix|remixed|live|acoustic|demo|instrumental|'
                        r'cover|karaoke|remaster|remastered|radio.?edit|edit|'
                        r'extended|stripped|version|ver\.?)', re.IGNORECASE)

                    def _has_version_tag(s):
                        return bool(_VERSION_TAGS.search(s))

                    def _lrc_best_match(results, artist, title):
                        """Pick the best synced-lyrics result.
                        Name similarity is the primary key; penalize results with
                        version qualifiers (Redux, Remix, Live, etc.) not present
                        in the search title; prefer more lines on ties."""
                        search_has_tag = _has_version_tag(title)
                        best_score, best_item = 0.0, None
                        for item in results:
                            if not item.get("syncedLyrics"):
                                continue
                            track_name = item.get("trackName", "")
                            sc = (_fuzzy(item.get("artistName",""), artist) +
                                  _fuzzy(track_name, title))
                            # Penalise results that add a version qualifier the search
                            # title doesn't have — they're a different recording.
                            if not search_has_tag and _has_version_tag(track_name):
                                sc *= 0.6
                            cur_lines  = len((best_item or {}).get("syncedLyrics","").splitlines()) if best_item else 0
                            this_lines = len(item["syncedLyrics"].splitlines())
                            if sc > best_score + 0.1:
                                best_score, best_item = sc, item
                            elif sc >= best_score - 0.1 and this_lines > cur_lines:
                                best_score, best_item = sc, item
                        return best_item, best_score

                    # 1) Try exact-match endpoint first
                    try:
                        lrc_url = (
                            f"https://lrclib.net/api/get"
                            f"?artist_name={urllib.parse.quote(o_artist)}"
                            f"&track_name={urllib.parse.quote(o_title)}"
                        )
                        req = urllib.request.Request(lrc_url, headers=_LRCLIB_HDR)
                        with urllib.request.urlopen(req, timeout=10) as r:
                            d = json.loads(r.read().decode("utf-8"))
                        if d.get("syncedLyrics"):
                            synced_lrc = d["syncedLyrics"]
                            _exact_lines = len(synced_lrc.splitlines())
                            log(f"  [fetch] Lyrics found via exact-match ({_exact_lines} lines)")
                        else:
                            log("  [fetch] Exact match found but no synced lyrics — trying search…")
                    except Exception as _e1:
                        log(f"  [fetch] Exact-match failed ({_e1}) — trying search…")

                    # 2) Search lrclib — always run to find the most complete version.
                    # Also try title-only when artist may be wrong (bad yt-dlp metadata).
                    _exact_lines = len(synced_lrc.splitlines()) if synced_lrc else 0
                    def _lrc_search(q):
                        url = f"https://lrclib.net/api/search?q={urllib.parse.quote(q)}"
                        req = urllib.request.Request(url, headers=_LRCLIB_HDR)
                        with urllib.request.urlopen(req, timeout=10) as r:
                            return json.loads(r.read().decode("utf-8"))

                    try:
                        # Try artist+title first
                        results = _lrc_search(f"{o_artist} {o_title}")
                        log(f"  [fetch] Search (artist+title) returned {len(results)} result(s)")
                        # If 0 results, retry with title only (artist metadata may be wrong)
                        if not results:
                            results = _lrc_search(o_title)
                            log(f"  [fetch] Search (title-only) returned {len(results)} result(s)")
                        best_item, best_score = _lrc_best_match(results, o_artist, o_title)
                        if best_item and best_score >= 0.3:  # lower threshold for title-only fallback
                            _search_lines = len(best_item["syncedLyrics"].splitlines())
                            if _search_lines > _exact_lines:
                                synced_lrc = best_item["syncedLyrics"]
                                log(f"  [fetch] Using search result ({_search_lines} lines, score={best_score:.2f}): "
                                    f"'{best_item.get('artistName','')} – {best_item.get('trackName','')}'")
                            else:
                                log(f"  [fetch] Keeping exact-match ({_exact_lines} lines ≥ search {_search_lines})")
                        else:
                            log("  [fetch] No usable match in lrclib search")
                    except Exception as _e2:
                        log(f"  [fetch] lrclib search failed: {_e2}")

                    # 3) Fallback: Genius.com (plain text, no timestamps)
                    if not synced_lrc:
                        try:
                            log("  [fetch] Trying Genius.com for plain lyrics…")
                            _GEN_HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                            genius_q = urllib.parse.quote(f"{o_artist} {o_title}")
                            search_url = f"https://genius.com/api/search/multi?q={genius_q}&per_page=5"
                            req_g = urllib.request.Request(search_url, headers=_GEN_HDR)
                            with urllib.request.urlopen(req_g, timeout=10) as rg:
                                gdata = json.loads(rg.read().decode("utf-8"))
                            song_url = None
                            best_g_score = 0.0
                            for section in gdata.get("response", {}).get("sections", []):
                                if section.get("type") == "song":
                                    for hit in section.get("hits", []):
                                        r = hit.get("result", {})
                                        sc = (_fuzzy(r.get("primary_artist",{}).get("name",""), o_artist) +
                                              _fuzzy(r.get("title",""), o_title))
                                        if sc > best_g_score:
                                            best_g_score = sc
                                            song_url = r.get("url")
                            if song_url and best_g_score < 0.5:
                                log(f"  [fetch] Genius best match score too low ({best_g_score:.2f}), skipping")
                                song_url = None
                            if song_url:
                                log(f"  [fetch] Genius match: {song_url}")
                                req_p = urllib.request.Request(song_url, headers=_GEN_HDR)
                                with urllib.request.urlopen(req_p, timeout=15) as rp:
                                    html = rp.read().decode("utf-8", errors="replace")
                                import re as _re
                                import html as _html
                                # Extract full lyric containers by tracking div depth
                                # (simple regex stops at first nested </div>)
                                def _extract_containers(h):
                                    parts = []
                                    for m in _re.finditer(r'data-lyrics-container="true"[^>]*>', h):
                                        start = m.end(); depth = 1; i = start
                                        while i < len(h) and depth > 0:
                                            o = h.find('<div', i)
                                            c = h.find('</div>', i)
                                            if c < 0: break
                                            if 0 <= o < c:
                                                depth += 1; i = o + 4
                                            else:
                                                depth -= 1
                                                if depth == 0: parts.append(h[start:c]); break
                                                i = c + 6
                                    return parts
                                blocks = _extract_containers(html)
                                if blocks:
                                    lines = []
                                    for block in blocks:
                                        t = _re.sub(r'<br\s*/?>', '\n', block)
                                        t = _re.sub(r'<[^>]+>', '', t)
                                        t = _html.unescape(t)
                                        for ln in t.splitlines():
                                            ln = ln.strip()
                                            if not ln or _re.match(r'^\[.{1,40}\]$', ln):
                                                continue
                                            if _re.match(r'^\d+\s+Contributor', ln):
                                                continue
                                            lines.append(ln)
                                    plain = '\n'.join(lines)
                                    if plain.strip():
                                        txt_dest = os.path.join(dest_dir, f"{song_key}.txt")
                                        with open(txt_dest, "w", encoding="utf-8") as f:
                                            f.write(plain)
                                        _saved_txt_path = txt_dest   # remember for Whisper trigger
                                        root.after(0, lambda p=txt_dest: vars_["lyrics"].set(p))
                                        log(f"  [fetch] Genius plain lyrics saved: {txt_dest}")
                                    else:
                                        log("  [fetch] Genius page parsed but no lyrics text found")
                                else:
                                    log("  [fetch] Genius page structure not recognized")
                            else:
                                log("  [fetch] Genius: no song match found")
                        except Exception as _eg:
                            log(f"  [fetch] Genius fallback failed: {_eg}")

                    if synced_lrc:
                        lrc_dest = os.path.join(dest_dir, f"{song_key}.lrc")
                        with open(lrc_dest, "w", encoding="utf-8") as f:
                            f.write(synced_lrc)
                        root.after(0, lambda p=lrc_dest: vars_["lyrics"].set(p))
                        log(f"  [fetch] Synced lyrics saved: {lrc_dest}")
                    else:
                        log("  [fetch] No synced lyrics found — will auto-timestamp with Whisper if plain lyrics were saved")
                except Exception as le:
                    log(f"  [fetch] Lyrics fetch failed: {le}")

                root.after(0, _update_player_ui)
                root.after(0, lambda: log("  [fetch] Auto-fetch complete."))

                # If we only got plain text lyrics, auto-run Whisper to timestamp them.
                # Determine the best txt path: prefer what Genius saved, fall back to
                # whatever is already in vars_["lyrics"] if it's a .txt.
                _existing_lyr = vars_["lyrics"].get()
                _txt_for_whisper = None
                if _saved_txt_path and os.path.isfile(_saved_txt_path):
                    _txt_for_whisper = _saved_txt_path
                elif not synced_lrc and _existing_lyr and _existing_lyr.endswith(".txt") and os.path.isfile(_existing_lyr):
                    _txt_for_whisper = _existing_lyr

                if _txt_for_whisper:
                    log("  [fetch] Plain lyrics detected — auto-starting Whisper timestamping…")
                    _twp = _txt_for_whisper
                    root.after(0, lambda: _safe_status("✓ Done! AI timestamping running in background…"))
                    root.after(1200, _close_overlay)
                    def _launch_whisper():
                        vars_["lyrics"].set(_twp)
                        _do_ai_timestamps()
                    root.after(1500, _launch_whisper)
                else:
                    root.after(0, lambda: _safe_status("✓ Done!"))
                    root.after(800, _close_overlay)
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
    player_bar.grid_remove()  # hidden until startup overlay dismissed
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
        startup_overlay.place_forget(); player_bar.grid()
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
        title2, artist2 = gp.title.strip(), gp.artist.strip()
        m = re.match(r"^(.*)\s+by\s+(.+)$", title2, re.I)
        if m: title2, artist2 = m.group(1).strip(), m.group(2).strip()
        # Normalize casing — GP files often store metadata in ALL CAPS
        if title2  == title2.upper():  title2  = title2.title()
        if artist2 == artist2.upper(): artist2 = artist2.title()
        vars_["title"].set(title2); vars_["artist"].set(artist2)
        gp_album = gp.album.strip()
        vars_["album"].set(gp_album if gp_album else "Single")
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
                        row["var_arr"].set("Skip")
            else:
                _used_arrs[a] = True

    # ── DO BUILD ──────────────────────────────────────────────────────────────────
    def do_build():
        if not vars_["gp"].get() or not os.path.isfile(vars_["gp"].get()):
            return messagebox.showwarning("Missing GP file", "Load a Guitar Pro file first.")
        if not vars_["audio"].get() or not os.path.isfile(vars_["audio"].get()):
            return messagebox.showwarning("Missing audio", "Load an audio file first.")

        bld_win = tk.Toplevel(root)
        bld_win.title("Building CDLC…")
        bld_win.geometry("460x130")
        bld_win.resizable(False, False)
        bld_win.configure(bg=COL["surface"])
        bld_win.grab_set()
        bld_win.transient(root)

        bld_status_var = tk.StringVar(value="Starting build…")
        styled(tk.Label(bld_win, textvariable=bld_status_var,
                        font=("Segoe UI", 10), wraplength=420, justify="left"),
               bg="surface", fg="fg").pack(expand=True, fill="both", padx=24, pady=28)

        def _close_bld():
            try:
                bld_win.grab_release()
                bld_win.destroy()
            except Exception:
                pass

        btn_rebuild.configure(state="disabled", text="\u23f3  Building\u2026")

        def worker():
            try:
                o = SimpleNamespace(
                    gp_path       = vars_["gp"].get(),
                    audio_path    = vars_["audio"].get(),
                    preview_path  = vars_["preview_audio"].get(),
                    lyrics_path   = vars_["lyrics"].get(),
                    art_path      = vars_["art"].get(),
                    out_dir       = vars_["out"].get(),
                    psarc_out     = vars_["psarc_out"].get(),
                    title         = vars_["title"].get(),
                    artist        = vars_["artist"].get(),
                    album         = vars_["album"].get(),
                    year          = vars_["year"].get(),
                    leadin        = float(vars_["leadin"].get() or 5),
                    volume        = float(vars_["volume"].get() or -8),
                    packer_path   = vars_["packer"].get(),
                    cst_dir       = vars_["cst"].get(),
                    appid         = vars_["appid"].get().strip() or None,
                    scroll_speed  = float(vars_["scroll_speed"].get() or 1.4),
                    pitch         = float(vars_["pitch"].get() or 0),
                    bpm_factor    = float(vars_["bpm_factor"].get() or 1.0),
                    lrc_offset    = float(vars_["lrc_offset"].get() or 0.0),
                    song_folder   = _sv.get("song_key", ""),
                    make_psarc    = bool(psarc_var.get()),
                    use_ddc       = bool(use_ddc.get()),
                    tracks        = [{"index":      r["index"],
                                      "include":    r["var_include"].get(),
                                      "arr":        r["var_arr"].get(),
                                      "tone_label": r["var_tone"].get()}
                                     for r in track_rows],
                )
                def _prog(msg):
                    root.after(0, lambda m=msg: bld_status_var.set(m))
                    root.after(0, lambda m=msg: log("  " + m))
                result = build_project(o, _prog)
                # Copy PSARC to the user-chosen output folder if set
                _psarc_src = (result or {}).get("psarc", "")
                _psarc_dest_dir = vars_["psarc_out"].get().strip() or vars_["out"].get().strip()
                if _psarc_src and os.path.isfile(_psarc_src) and _psarc_dest_dir:
                    os.makedirs(_psarc_dest_dir, exist_ok=True)
                    _psarc_dest = os.path.join(_psarc_dest_dir, os.path.basename(_psarc_src))
                    try:
                        if os.path.abspath(_psarc_src) != os.path.abspath(_psarc_dest):
                            shutil.copy2(_psarc_src, _psarc_dest)
                        root.after(0, lambda d=_psarc_dest: log(f"  [psarc] Copied to: {d}"))
                    except Exception as _ce:
                        root.after(0, lambda e=_ce: log(f"  [psarc] Copy failed: {e}"))
                _save_project_to_history(
                    vars_["title"].get(), vars_["artist"].get(), vars_["gp"].get(),
                    _psarc_src or None, vars_["art"].get() or None)
                root.after(0, lambda: bld_status_var.set("✓ Build complete!"))
                root.after(1500, _close_bld)
            except Exception as e:
                _tb = traceback.format_exc()
                root.after(0, lambda: log("BUILD ERROR: " + str(e)))
                root.after(0, lambda: log(_tb))
                # Surface the crash location in the popup so it can be read/screenshotted
                # without digging through the Log page.
                _loc = ""
                try:
                    _frames = [ln.strip() for ln in _tb.splitlines() if ln.strip().startswith("File ")]
                    if _frames: _loc = "\n\nCrash location:\n" + _frames[-1]
                except Exception:
                    pass
                root.after(0, lambda m=(str(e) + _loc): messagebox.showerror("Build failed", m))
                root.after(0, _close_bld)
            finally:
                root.after(0, lambda: btn_rebuild.configure(
                    state="normal", text="🎯  Sync & Verify →"))
        threading.Thread(target=worker, daemon=True).start()

    def _on_app_close():
        procs = []
        if _sv.get("proc"): procs.append(_sv["proc"])
        for p in (_media_procs.get("audio"), _media_procs.get("video"),
                  main_vid_state.get("process"), _wf_state.get("proc")):
            if p: procs.append(p)
        for p in procs:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                               capture_output=True, creationflags=CREATE_NO_WINDOW)
            except Exception:
                try: p.kill()
                except Exception: pass
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", _on_app_close)
    show_page("main")
    root.after(120, _show_startup_overlay)
    root.mainloop()

if __name__ == "__main__":
    run_gui()
