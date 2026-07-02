#!/usr/bin/env python3
"""
tone_designer.py — Tone preview / recommend / compare / import for RS Studio.

Kept in its own module (linked from gp2rs_studio.py) so the tone work doesn't
bloat the main 7k-line GUI file.

What it does
------------
* recommend_tones()      : suggest good tone presets for a track by name/kind.
* tone_summary()         : human-readable amp/cab/pedal + gain/EQ rundown.
* tone_character()       : normalised (gain, bass, mid, treble, presence) 0..1.
* synth_riff_wav()       : render a short representative riff (Karplus-Strong),
                           or the song's own notes if supplied — "mix a MIDI
                           with the tone" so you hear the tone on real phrasing.
* render_tone_preview()  : push that riff through an ffmpeg gain/EQ/drive chain
                           derived from the tone's knob values. This is an
                           APPROXIMATION, not an exact Rocksmith amp render.
* import_tone_file()     : load a DLC-Builder .json / Rocksmith .tone2014.xml
                           tone into the preset format.
* build_tone_tab()       : build the in-app Tone page (called by the studio).

Nothing here needs torch/CUDA. numpy is used for synthesis if present; if it
isn't, synthesis is skipped and the UI still works for recommend/summary/import.
"""
import os
import re
import json
import wave
import struct
import math
import subprocess
import threading

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# ───────────────────────────── knob / character ───────────────────────────
def _amp_knobs(preset):
    """Return the Amp KnobValues dict for a preset (TONE_PRESETS value)."""
    try:
        return preset["GearList"]["Amp"]["KnobValues"] or {}
    except Exception:
        return {}


def _amp_key(preset):
    try:
        return preset["GearList"]["Amp"].get("Key", "")
    except Exception:
        return ""


def _cab_key(preset):
    try:
        return preset["GearList"]["Cabinet"].get("Key", "")
    except Exception:
        return ""


def _pedal_keys(preset):
    """Any non Amp/Cabinet gear slots (pedals/rack), as a list of keys."""
    out = []
    try:
        gl = preset["GearList"]
        for slot, gear in gl.items():
            if slot in ("Amp", "Cabinet") or not isinstance(gear, dict):
                continue
            k = gear.get("Key")
            if k:
                out.append(k)
    except Exception:
        pass
    return out


def tone_character(preset):
    """Normalised 0..1 dict: gain, bass, mid, treble, presence.

    Matches knob names by suffix so it works across amps (TW40, JCM800,
    OrangeAD50, bass CH350B, …)."""
    kv = _amp_knobs(preset)
    vals = {"gain": None, "bass": None, "mid": None, "treble": None, "presence": None}
    for k, v in kv.items():
        try:
            fv = float(v)
        except Exception:
            continue
        kl = k.lower()
        if kl.endswith("gain"):
            vals["gain"] = fv
        elif kl.endswith("bass"):
            vals["bass"] = fv
        elif kl.endswith("mid"):
            vals["mid"] = fv
        elif kl.endswith("treble"):
            vals["treble"] = fv
        elif kl.endswith("pres") or kl.endswith("presence"):
            vals["presence"] = fv
    # normalise: knob values are ~0..100 (some bass bands are -ve); clamp to 0..1
    def _n(x, default=0.5):
        if x is None:
            return default
        return max(0.0, min(1.0, x / 100.0))
    return {k: _n(v) for k, v in vals.items()}


def tone_summary(label, preset):
    """Multi-line human-readable description of a tone."""
    ch = tone_character(preset)
    amp = _amp_key(preset).replace("Amp_", "").replace("Bass_Amp_", "")
    cab = _cab_key(preset).replace("Cab_", "").replace("Bass_Cab_", "")
    peds = _pedal_keys(preset)
    descs = preset.get("ToneDescriptors", [])
    desc_txt = ", ".join(re.sub(r"^\$\[\d+\]", "", d) for d in descs) or "—"
    lines = [
        f"{label}",
        f"  Amp:      {amp or '—'}",
        f"  Cabinet:  {cab or '—'}",
        f"  Pedals:   {', '.join(p.replace('Pedals_', '') for p in peds) if peds else 'none'}",
        f"  Class:    {desc_txt}",
        "",
        f"  Gain      {_bar(ch['gain'])}",
        f"  Bass      {_bar(ch['bass'])}",
        f"  Mid       {_bar(ch['mid'])}",
        f"  Treble    {_bar(ch['treble'])}",
        f"  Presence  {_bar(ch['presence'])}",
    ]
    vol = preset.get("Volume")
    if vol is not None:
        lines.append(f"  Volume    {vol} dB")
    return "\n".join(lines)


def _bar(x, width=14):
    n = int(round(max(0.0, min(1.0, x)) * width))
    return "[" + "█" * n + "·" * (width - n) + f"] {int(round(x*100)):>3d}"


# ───────────────────────────── recommendations ────────────────────────────
def recommend_tones(track_name, kind, presets):
    """Return [(label, reason), …] ordered best-first for this track.

    `presets` is the TONE_PRESETS dict. Uses track-name keywords + the
    arrangement kind ('guitar'/'bass'), then falls back to gain profile."""
    name = (track_name or "").lower()
    labels = list(presets.keys())
    is_bass = (kind == "bass")
    bass_labels = [l for l in labels if l.lower().startswith("bass")
                   or "bass" in _amp_key(presets[l]).lower()]
    gtr_labels = [l for l in labels if l not in bass_labels]

    pool = bass_labels if is_bass else gtr_labels
    if not pool:
        pool = labels

    scored = []
    for l in pool:
        ch = tone_character(presets[l])
        score = 0.0
        reason = []
        gain = ch["gain"]
        if is_bass:
            score += 2; reason.append("bass amp")
        else:
            if any(k in name for k in ("clean", "acoustic", "nylon", "spanish")):
                if gain < 0.35: score += 3; reason.append("clean match")
                else: score -= 1
            if any(k in name for k in ("dist", "metal", "heavy", "lead", "solo")):
                if gain > 0.6: score += 3; reason.append("high-gain match")
                else: score -= 0.5
            if any(k in name for k in ("crunch", "rhythm", "overdrive", "drive")):
                if 0.35 <= gain <= 0.7: score += 3; reason.append("crunch match")
            if "electric" in name and gain >= 0.4:
                score += 1; reason.append("electric")
        scored.append((score, l, ", ".join(reason) or "general fit"))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [(l, r) for _s, l, r in scored]


# ─────────────────────── built-in extra tone library ──────────────────────
def builtin_library(base):
    """Return extra ready-to-use tones, each CLONED from a verified base preset
    (so amp/cab keys + knob names stay valid in a real build) with knob tweaks
    and new descriptors. `base` is the studio's TONE_PRESETS dict.

    Variations are only created for base tones that exist, so this is safe even
    if the base set changes."""
    import copy
    out = {}

    def _set_knobs(preset, **suffix_vals):
        kv = preset["GearList"]["Amp"]["KnobValues"]
        for suf, val in suffix_vals.items():
            for kk in list(kv.keys()):
                if kk.lower().endswith(suf.lower()):
                    kv[kk] = val

    def variant(src, new_name, descs, **suffix_vals):
        if src not in base:
            return
        p = copy.deepcopy(base[src])
        _set_knobs(p, **suffix_vals)
        p["Key"] = p["Name"] = new_name
        p["ToneDescriptors"] = descs
        out[new_name] = p

    CLEAN = ["$[35720]CLEAN"]
    CRUNCH = ["$[35716]CRUNCH"]
    DIST = ["$[35722]DISTORTION"]
    LEAD = ["$[35722]DISTORTION", "$[35723]LEAD"]
    BASS = ["$[35715]BASS"]

    # Clean family (from "Clean - Twin")
    variant("Clean - Twin", "Clean - Twin Bright", CLEAN,
            Gain=14, Bass=58, Mid=52, Treble=92, Pres=82)
    variant("Clean - Twin", "Clean - Twin Warm", CLEAN,
            Gain=22, Bass=92, Mid=72, Treble=42, Pres=38)
    variant("Clean - Twin", "Clean - Edge of Breakup", CRUNCH,
            Gain=40, Bass=70, Mid=66, Treble=70, Pres=55)

    # Crunch family (from "Crunch - Orange")
    variant("Crunch - Orange", "Crunch - Orange Low", CRUNCH,
            Gain=42, Bass=68, Mid=60, Treble=60)
    variant("Crunch - Orange", "Crunch - Orange Hot", CRUNCH,
            Gain=80, Bass=72, Mid=74, Treble=70)

    # High-gain family (from "Distortion - JCM800")
    variant("Distortion - JCM800", "Rhythm - JCM800 Tight", DIST,
            Gain=62, Bass=58, Mid=72, Treble=68, Pres=45)
    variant("Distortion - JCM800", "Lead - JCM800 Hot", LEAD,
            Gain=94, Bass=70, Mid=80, Treble=74, Pres=64)

    # Bass family (from "Bass - Rock")
    variant("Bass - Rock", "Bass - Clean", BASS, Gain=28)
    variant("Bass - Rock", "Bass - Driven", BASS, Gain=72)

    return out


# ───────────────────────────── synthesis ──────────────────────────────────
def _midi_to_freq(m):
    return 440.0 * (2.0 ** ((m - 69) / 12.0))


def _karplus(freq, dur, sr, decay=0.9965):
    """One Karplus-Strong plucked note as float samples (smoothed, less fizzy)."""
    try:
        import numpy as np
        N = max(2, int(sr / max(20.0, min(freq, sr / 2.5))))
        # Pluck excitation: filtered noise (a real pick is not white noise) so
        # the result reads as a string, not hiss.
        exc = np.random.uniform(-1.0, 1.0, N)
        exc = np.convolve(exc, np.ones(3) / 3.0, mode="same")  # soften
        total = int(dur * sr)
        out = np.empty(total, dtype=np.float64)
        b = exc.astype(np.float64).copy()
        idx = 0
        for i in range(total):
            cur = b[idx]
            nxt = b[(idx + 1) % N]
            out[i] = cur
            b[idx] = decay * 0.5 * (cur + nxt)
            idx = (idx + 1) % N
        # pluck attack + natural decay envelope
        env = np.ones(total)
        atk = min(total, int(0.004 * sr))
        if atk > 1:
            env[:atk] = np.linspace(0.0, 1.0, atk)
        env *= np.linspace(1.0, 0.0, total) ** 0.5
        return out * env
    except Exception:
        return None


def _default_riff(is_bass):
    """A short, musical phrase as (start_sec, midi, dur) — palm-muted-ish chug
    plus a couple of higher notes so tone differences are obvious."""
    if is_bass:
        roots = [28, 28, 31, 33, 28, 28, 35, 33]  # E A-ish walk
    else:
        roots = [40, 40, 47, 45, 40, 40, 52, 50]  # E-based riff
    step = 0.32
    return [(i * step, m, step * 1.05) for i, m in enumerate(roots)]


def synth_riff_wav(out_path, is_bass=False, notes=None, sr=22050, seconds=3.2):
    """Render a riff to a mono 16-bit WAV. If `notes` (list of (t, midi)) is
    given (e.g. the song's own track), use those; else a built-in riff.
    Returns out_path on success, or None if numpy is unavailable."""
    try:
        import numpy as np
    except Exception:
        return None

    if notes:
        ev = []
        notes = sorted(notes, key=lambda x: x[0])
        t0 = notes[0][0]
        for j, (t, m) in enumerate(notes):
            if t - t0 > seconds:
                break
            m = int(m)
            if m < 24 or m > 96:          # ignore garbage pitches
                continue
            nt = notes[j + 1][0] if j + 1 < len(notes) else t + 0.4
            ev.append((t - t0, m, max(0.14, min(0.55, nt - t))))
        if len(ev) < 3:                   # too sparse to be a useful preview
            ev = _default_riff(is_bass)
    else:
        ev = _default_riff(is_bass)

    total = int((max(s + d for s, _m, d in ev) + 0.3) * sr)
    mix = np.zeros(total, dtype=np.float64)
    for (s, m, d) in ev:
        note = _karplus(_midi_to_freq(m), d, sr)
        if note is None:
            return None
        i0 = int(s * sr)
        i1 = min(total, i0 + len(note))
        mix[i0:i1] += note[: i1 - i0]
    peak = float(np.max(np.abs(mix))) or 1.0
    mix = (mix / peak) * 0.85
    pcm = (mix * 32767).astype("<i2")
    with wave.open(out_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return out_path


def _tone_filter_chain(preset, is_bass=False):
    """ffmpeg -af chain approximating the tone from its knob character.

    Tuned to sound musical rather than harsh: moderate drive, gentle clipping,
    EQ shelves, and a top-end roll-off so high-gain tones don't fizz into noise.
    Still an approximation — not a real amp render."""
    ch = tone_character(preset)
    gain = ch["gain"]
    pre_db = gain * 12.0                       # drive (was too hot before)
    bass_db = (ch["bass"] - 0.5) * 12.0
    mid_db = (ch["mid"] - 0.5) * 10.0
    treb_db = (ch["treble"] - 0.5) * 12.0
    pres_db = (ch["presence"] - 0.5) * 8.0
    chain = ["highpass=f=" + ("35" if is_bass else "75")]   # kill rumble
    if pre_db > 0.5:
        chain.append(f"volume={pre_db:.1f}dB")
    chain.append(f"bass=g={bass_db:.1f}")
    chain.append(f"equalizer=f={'500' if is_bass else '900'}:t=q:w=1.1:g={mid_db:.1f}")
    chain.append(f"treble=g={treb_db:.1f}")
    chain.append(f"equalizer=f=4200:t=q:w=2:g={pres_db:.1f}")
    # gentle soft-clip: higher gain clips a little harder, but never brutally
    limit = max(0.45, 0.92 - gain * 0.35)
    chain.append(f"alimiter=level_in=1:limit={limit:.2f}:level=disabled")
    # tame fizz on driven tones
    if gain > 0.45:
        roll = int(7500 - (gain - 0.45) * 4000)   # 7.5k..5.3k
        chain.append(f"lowpass=f={max(4500, roll)}")
    chain.append("volume=2dB")                     # makeup
    return ",".join(chain)


def render_tone_preview(preset, riff_wav, out_wav, ffmpeg, is_bass=False):
    """Apply the tone's filter chain to riff_wav -> out_wav via ffmpeg."""
    if not (ffmpeg and os.path.isfile(riff_wav)):
        return None
    af = _tone_filter_chain(preset, is_bass=is_bass)
    try:
        r = subprocess.run([ffmpeg, "-y", "-i", riff_wav, "-af", af, out_wav],
                           capture_output=True, creationflags=CREATE_NO_WINDOW,
                           timeout=60)
        if r.returncode == 0 and os.path.isfile(out_wav):
            return out_wav
    except Exception:
        pass
    return None


# ───────────────────────────── import tones ───────────────────────────────
def import_tone_file(path):
    """Load a tone from .json (DLC Builder / Tone2014) or .tone2014.xml.
    Returns (label, preset_dict) in TONE_PRESETS format, or (None, None)."""
    if not (path and os.path.isfile(path)):
        return None, None
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".json":
            return _import_json(path)
        if ext in (".xml", ".tone2014"):
            return _import_xml(path)
        # try json first, then xml
        try:
            return _import_json(path)
        except Exception:
            return _import_xml(path)
    except Exception:
        return None, None


def _norm_preset(d, fallback_name):
    """Coerce a tone dict into the TONE_PRESETS value shape."""
    name = (d.get("Name") or d.get("Key") or fallback_name or "Imported Tone")
    key = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_") or "imported"
    d = dict(d)
    d.setdefault("GearList", d.get("GearList", {}))
    d["Key"] = key
    d["Name"] = name
    d.setdefault("IsCustom", True)
    d.setdefault("NameSeparator", " - ")
    d.setdefault("ToneDescriptors", d.get("ToneDescriptors", []))
    d.setdefault("Volume", d.get("Volume", "-18.0"))
    return str(name), d


def _import_json(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Accept: a bare tone dict, or {"Tones":[...]}, or a list.
    if isinstance(data, dict) and "GearList" in data:
        return _norm_preset(data, os.path.splitext(os.path.basename(path))[0])
    if isinstance(data, dict) and "Tones" in data and data["Tones"]:
        return _norm_preset(data["Tones"][0], os.path.basename(path))
    if isinstance(data, list) and data:
        return _norm_preset(data[0], os.path.basename(path))
    raise ValueError("Unrecognised tone JSON")


def _import_xml(path):
    import xml.etree.ElementTree as ET
    tree = ET.parse(path)
    root = tree.getroot()

    def _txt(el, tag):
        for c in el.iter():
            if c.tag.split("}")[-1] == tag:
                return c.text or ""
        return ""

    def _gear(el, slot):
        for c in el.iter():
            if c.tag.split("}")[-1] == slot:
                key = _txt(c, "Key")
                knobs = {}
                for kv in c.iter():
                    if kv.tag.split("}")[-1] == "KnobValues":
                        for entry in kv:
                            kk = _txt(entry, "Key")
                            vv = _txt(entry, "Value")
                            if kk:
                                try:
                                    knobs[kk] = float(vv)
                                except Exception:
                                    pass
                return {"Key": key, "KnobValues": knobs,
                        "Type": "Amps" if slot == "Amp" else "Cabinets"}
        return None
    gl = {}
    for slot in ("Amp", "Cabinet"):
        g = _gear(root, slot)
        if g:
            gl[slot] = g
    name = _txt(root, "Name") or os.path.splitext(os.path.basename(path))[0]
    return _norm_preset({"GearList": gl, "Name": name,
                         "Volume": _txt(root, "Volume") or "-18.0"}, name)


# ─────────────────────────── audio playback ───────────────────────────────
class _Player:
    def __init__(self):
        self.proc = None

    def play(self, wav, ffplay):
        self.stop()
        if not (ffplay and wav and os.path.isfile(wav)):
            return False
        try:
            self.proc = subprocess.Popen(
                [ffplay, "-autoexit", "-nodisp", "-loglevel", "quiet", wav],
                creationflags=CREATE_NO_WINDOW)
            return True
        except Exception:
            self.proc = None
            return False

    def stop(self):
        if self.proc:
            try:
                self.proc.terminate()
            except Exception:
                pass
            self.proc = None


# ───────────────────────────── the GUI tab ────────────────────────────────
def build_tone_tab(parent, ctx):
    """Build the Tone Designer page inside `parent`.

    ctx keys:
      tk, ttk            : tkinter modules
      COL                : colour dict
      fonts              : optional (family) — we just use Segoe UI
      TONE_PRESETS       : dict (mutated in place when importing)
      get_tracks         : () -> [ {name, kind, arr, tone} ]   (current project)
      get_notes          : (is_bass) -> [(t, midi), …] or None (song phrasing)
      find_exe           : (name) -> path or None  (ffmpeg / ffplay)
      work_dir           : writable dir for preview wavs
      log                : (msg) -> None
      on_import          : (label, preset) -> None  (register into studio)
      filedialog         : tkinter.filedialog
      messagebox         : tkinter.messagebox
    """
    tk = ctx["tk"]; COL = ctx["COL"]
    TONE_PRESETS = ctx["TONE_PRESETS"]
    log = ctx.get("log", print)
    find_exe = ctx["find_exe"]
    work_dir = ctx.get("work_dir") or os.path.join(os.path.expanduser("~"), ".rsstudio_tones")
    os.makedirs(work_dir, exist_ok=True)
    player = _Player()
    state = {"riff_gtr": None, "riff_bass": None}

    # Merge the built-in extra tones (cloned from verified base presets) so the
    # picker has real variety, and register them into the studio's pickers too.
    try:
        for _lbl, _preset in builtin_library(TONE_PRESETS).items():
            if _lbl not in TONE_PRESETS:
                TONE_PRESETS[_lbl] = _preset
                try:
                    ctx.get("on_import", lambda *a: None)(_lbl, _preset)
                except Exception:
                    pass
    except Exception as _le:
        log(f"  [tone] library merge failed: {_le}")

    def C(name, default="#cccccc"):
        return COL.get(name, default)

    root_f = tk.Frame(parent, bg=C("surface"))
    root_f.pack(fill="both", expand=True)

    tk.Label(root_f, text="Tone Designer", font=("Segoe UI Semibold", 16),
             bg=C("surface"), fg=C("fg")).pack(anchor="w", padx=28, pady=(22, 0))
    tk.Label(root_f,
             text="Preview, compare and import tones. Previews push a short riff "
                  "(or your song's own notes) through an approximate amp/EQ — a "
                  "rough idea of the tone, not an exact Rocksmith render.",
             font=("Segoe UI", 9), bg=C("surface"), fg=C("muted"),
             wraplength=820, justify="left").pack(anchor="w", padx=28, pady=(2, 12))

    body = tk.Frame(root_f, bg=C("surface"))
    body.pack(fill="both", expand=True, padx=28, pady=(0, 16))

    # left: track recommendations + tone list ; right: summary + controls
    left = tk.Frame(body, bg=C("card"), highlightthickness=1,
                    highlightbackground=C("border"))
    left.pack(side="left", fill="both", expand=True, padx=(0, 10))
    right = tk.Frame(body, bg=C("card"), highlightthickness=1,
                     highlightbackground=C("border"), width=380)
    right.pack(side="left", fill="both", expand=False)
    right.pack_propagate(False)

    tk.Label(left, text="TONES", font=("Segoe UI Semibold", 9),
             bg=C("card"), fg=C("accent")).pack(anchor="w", padx=12, pady=(10, 4))

    rec_var = tk.StringVar(value="")
    tk.Label(left, textvariable=rec_var, font=("Segoe UI", 8),
             bg=C("card"), fg=C("muted"), justify="left",
             wraplength=360).pack(anchor="w", padx=12)

    listbox = tk.Listbox(left, height=14, bg=C("field"), fg=C("fg"),
                         selectbackground=C("accent"), relief="flat",
                         highlightthickness=0, font=("Segoe UI", 10),
                         activestyle="none")
    listbox.pack(fill="both", expand=True, padx=12, pady=10)

    def _refresh_list(select=None):
        listbox.delete(0, "end")
        for lbl in TONE_PRESETS.keys():
            listbox.insert("end", lbl)
        if select and select in TONE_PRESETS:
            i = list(TONE_PRESETS.keys()).index(select)
            listbox.selection_clear(0, "end")
            listbox.selection_set(i)
            listbox.see(i)

    # right: summary
    tk.Label(right, text="DETAILS", font=("Segoe UI Semibold", 9),
             bg=C("card"), fg=C("accent")).pack(anchor="w", padx=12, pady=(10, 4))
    summary = tk.Label(right, text="Select a tone…", font=("Consolas", 9),
                       bg=C("card"), fg=C("fg"), justify="left", anchor="nw")
    summary.pack(fill="both", expand=True, padx=12, pady=(0, 8))

    is_bass_var = tk.BooleanVar(value=False)
    tk.Checkbutton(right, text="Preview as bass", variable=is_bass_var,
                   bg=C("card"), fg=C("fg"), selectcolor=C("field"),
                   activebackground=C("card"), relief="flat", bd=0,
                   font=("Segoe UI", 9)).pack(anchor="w", padx=12)

    status_var = tk.StringVar(value="")
    tk.Label(right, textvariable=status_var, font=("Segoe UI", 8),
             bg=C("card"), fg=C("muted"), wraplength=340,
             justify="left").pack(anchor="w", padx=12, pady=(6, 0))

    def _selected_label():
        sel = listbox.curselection()
        if not sel:
            return None
        return listbox.get(sel[0])

    def _show_summary(*_):
        lbl = _selected_label()
        if not lbl or lbl not in TONE_PRESETS:
            return
        summary.config(text=tone_summary(lbl, TONE_PRESETS[lbl]))
    listbox.bind("<<ListboxSelect>>", _show_summary)

    def _ensure_riff(is_bass):
        key = "riff_bass" if is_bass else "riff_gtr"
        if state.get(key) and os.path.isfile(state[key]):
            return state[key]
        notes = None
        try:
            getn = ctx.get("get_notes")
            if getn:
                notes = getn(is_bass)
        except Exception:
            notes = None
        out = os.path.join(work_dir, f"riff_{'bass' if is_bass else 'gtr'}.wav")
        res = synth_riff_wav(out, is_bass=is_bass, notes=notes)
        state[key] = res
        return res

    def _preview():
        lbl = _selected_label()
        if not lbl:
            return status_var.set("Pick a tone first.")
        ffmpeg = find_exe("ffmpeg"); ffplay = find_exe("ffplay")
        if not ffmpeg or not ffplay:
            return status_var.set("ffmpeg/ffplay not found — use Auto-Fetch on Main "
                                  "to download them, or add ffplay.exe next to the app.")
        status_var.set("Rendering preview…")

        def _work():
            try:
                is_bass = is_bass_var.get()
                riff = _ensure_riff(is_bass)
                if not riff:
                    return _set("numpy not available — can't synthesise the riff. "
                                "Install numpy in the app's Python.")
                out = os.path.join(work_dir, "preview.wav")
                res = render_tone_preview(TONE_PRESETS[lbl], riff, out, ffmpeg,
                                          is_bass=is_bass)
                if not res:
                    return _set("Render failed (see Log).")
                player.play(res, ffplay)
                _set(f"▶ Playing approx. preview of: {lbl}")
            except Exception as e:
                log(f"  [tone] preview error: {e}")
                _set(f"Preview error: {e}")
        threading.Thread(target=_work, daemon=True).start()

    def _set(msg):
        try:
            parent.after(0, lambda: status_var.set(msg))
        except Exception:
            status_var.set(msg)

    # A/B compare: remember an A pick, compare to current
    ab = {"a": None}

    def _mark_a():
        lbl = _selected_label()
        if lbl:
            ab["a"] = lbl
            status_var.set(f"A = {lbl}.  Pick another tone and hit 'Play A/B'.")

    def _play_ab():
        b = _selected_label()
        a = ab.get("a")
        if not a or not b:
            return status_var.set("Set A (button), then select B and Play A/B.")
        ffmpeg = find_exe("ffmpeg"); ffplay = find_exe("ffplay")
        if not ffmpeg or not ffplay:
            return status_var.set("ffmpeg/ffplay not found.")

        def _work():
            try:
                is_bass = is_bass_var.get()
                riff = _ensure_riff(is_bass)
                if not riff:
                    return _set("numpy not available for synthesis.")
                pa = render_tone_preview(TONE_PRESETS[a], riff,
                                         os.path.join(work_dir, "a.wav"), ffmpeg,
                                         is_bass=is_bass)
                pb = render_tone_preview(TONE_PRESETS[b], riff,
                                         os.path.join(work_dir, "b.wav"), ffmpeg,
                                         is_bass=is_bass)
                if pa:
                    _set(f"▶ A: {a}"); player.play(pa, ffplay)
                    import time; time.sleep(_dur(pa) + 0.4)
                if pb:
                    _set(f"▶ B: {b}"); player.play(pb, ffplay)
            except Exception as e:
                _set(f"A/B error: {e}")
        threading.Thread(target=_work, daemon=True).start()

    def _dur(wav):
        try:
            with wave.open(wav, "rb") as w:
                return w.getnframes() / float(w.getframerate())
        except Exception:
            return 3.0

    def _import():
        fd = ctx["filedialog"]; mb = ctx["messagebox"]
        path = fd.askopenfilename(
            title="Import tone",
            filetypes=[("Tone files", "*.json *.xml *.tone2014.xml"),
                       ("All files", "*.*")])
        if not path:
            return
        lbl, preset = import_tone_file(path)
        if not preset:
            return mb.showerror("Import failed",
                                "Couldn't read that tone file.\nSupported: DLC "
                                "Builder .json and Rocksmith .tone2014.xml")
        TONE_PRESETS[lbl] = preset
        try:
            ctx.get("on_import", lambda *a: None)(lbl, preset)
        except Exception as e:
            log(f"  [tone] on_import hook failed: {e}")
        _refresh_list(select=lbl)
        _show_summary()
        status_var.set(f"✓ Imported '{lbl}' — now available in the tone pickers.")
        log(f"  [tone] imported tone: {lbl}")

    # buttons
    btns = tk.Frame(right, bg=C("card"))
    btns.pack(fill="x", padx=12, pady=12)

    def _btn(text, cmd, accent=False):
        b = tk.Button(btns, text=text, command=cmd, relief="flat", bd=0,
                      cursor="hand2", padx=12, pady=7, font=("Segoe UI Semibold", 9),
                      fg="white" if accent else C("fg"),
                      bg=C("accent") if accent else C("card2"),
                      activebackground=C("accent_hi") if accent else C("border_hi"))
        b.pack(fill="x", pady=3)
        return b

    _btn("▶  Preview tone", _preview, accent=True)
    _btn("■  Stop", lambda: (player.stop(), status_var.set("Stopped.")))
    _btn("Set as A", _mark_a)
    _btn("▶  Play A / B", _play_ab)
    _btn("⬇  Import tone…", _import)

    # recommendations from the current project's tracks
    def _load_recs():
        try:
            tracks = ctx.get("get_tracks", lambda: [])() or []
        except Exception:
            tracks = []
        if not tracks:
            rec_var.set("Load a project on the Main tab to see per-track "
                        "recommendations. You can still preview/import below.")
            return
        out = []
        for t in tracks:
            recs = recommend_tones(t.get("name", ""), t.get("kind", "guitar"),
                                   TONE_PRESETS)
            top = recs[0][0] if recs else "—"
            out.append(f"• {t.get('name','?')} ({t.get('arr','?')}): "
                       f"suggest “{top}”")
        rec_var.set("Recommended:\n" + "\n".join(out))

    _refresh_list()
    _load_recs()
    # expose a refresh so the studio can call it when a project loads
    ctx["_refresh"] = lambda: (_refresh_list(), _load_recs())
    return root_f
