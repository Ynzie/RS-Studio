#!/usr/bin/env python3
"""
lyrics_align.py — vocal isolation + phrase/word timing helpers.

This module is the low-level engine used by lyric_sync.py. It deliberately keeps
its heavy dependencies (demucs / whisperx / faster-whisper) OUT of process: they
are run through a real CPython interpreter found on the system, so everything
still works inside a frozen PyInstaller .exe (where torch/ML wheels aren't
bundled).

Public surface used by lyric_sync.py:
    real_python()                       -> path to an ML-capable python, or None
    demucs_available()                  -> bool
    isolate_vocals(audio, work, log)    -> stem wav path (or original audio)
    vocal_phrase_onsets(wav, log)       -> [start_seconds, ...]   (energy VAD)
    assign_lines_to_onsets(lines, ons)  -> [(time, text), ...]
    whisperx_available()                -> bool
    _transcribe_whisperx(wav, work, log)-> segments or None
    transcribe_faster_subprocess(...)   -> segments or None
    any_whisper_available()             -> bool
    align(audio, work, log)             -> segments or None
"""
import os
import sys
import json
import shutil
import subprocess

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

_PROBE_CACHE = {}


def _noop(*_a, **_k):
    pass


# ───────────────────────── interpreter discovery ──────────────────────────
def _candidate_pythons():
    """Yield candidate python executables, best (ML-capable) first."""
    seen = set()

    def _add(p):
        if p and p not in seen and os.path.isfile(p):
            seen.add(p)
            return p
        return None

    cands = []
    # 1. The current interpreter — but only if we're NOT frozen (a frozen .exe's
    #    sys.executable is the .exe itself, which can't run ML scripts).
    if not getattr(sys, "frozen", False):
        c = _add(sys.executable)
        if c:
            cands.append(c)

    # 2. py launcher with explicit versions (Windows), newest usable first.
    if os.name == "nt":
        for ver in ("3.13", "3.12", "3.11", "3.10"):
            try:
                out = subprocess.run(["py", f"-{ver}", "-c", "import sys;print(sys.executable)"],
                                     capture_output=True, text=True, timeout=8,
                                     creationflags=CREATE_NO_WINDOW)
                p = (out.stdout or "").strip()
                # skip the Microsoft Store shim (WindowsApps) — it can't run ML.
                if p and "WindowsApps" not in p:
                    c = _add(p)
                    if c:
                        cands.append(c)
            except Exception:
                pass

    # 3. PATH lookups.
    for name in ("python3.13", "python3.12", "python3.11", "python3.10",
                 "python3", "python"):
        p = shutil.which(name)
        if p and "WindowsApps" not in p:
            c = _add(p)
            if c:
                cands.append(c)

    return cands


def real_python():
    """Return a CPython interpreter that can run the ML stack, or None.

    Prefers 3.10–3.13 (torch/whisper wheels exist); never returns a frozen .exe
    or the Store shim. Result is cached."""
    if "real" in _PROBE_CACHE:
        return _PROBE_CACHE["real"]
    chosen = None
    for p in _candidate_pythons():
        try:
            out = subprocess.run(
                [p, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
                capture_output=True, text=True, timeout=8,
                creationflags=CREATE_NO_WINDOW)
            ver = (out.stdout or "").strip()
            if not ver:
                continue
            major, minor = (int(x) for x in ver.split(".")[:2])
            if major == 3 and 10 <= minor <= 13:
                chosen = p
                break
            if chosen is None:               # remember a fallback (e.g. 3.9/3.14)
                chosen = p
        except Exception:
            continue
    _PROBE_CACHE["real"] = chosen
    return chosen


def _ml_python():
    """Alias kept for callers that expect the ML interpreter."""
    return real_python()


def _have(module, py=None):
    """Is `module` importable in interpreter `py` (default: real_python())?"""
    py = py or real_python()
    if not py:
        return False
    key = (py, module)
    if key in _PROBE_CACHE:
        return _PROBE_CACHE[key]
    try:
        out = subprocess.run(
            [py, "-c", f"import importlib.util,sys;"
                       f"sys.exit(0 if importlib.util.find_spec('{module}') else 1)"],
            capture_output=True, timeout=12, creationflags=CREATE_NO_WINDOW)
        ok = (out.returncode == 0)
    except Exception:
        ok = False
    _PROBE_CACHE[key] = ok
    return ok


# ───────────────────────────── demucs ─────────────────────────────────────
def demucs_available():
    return _have("demucs")


def isolate_vocals(audio_path, work_dir, log=print):
    """Run demucs (htdemucs, 2-stem) to isolate the vocal track. Returns the path
    to the vocal stem WAV, or the original audio if demucs isn't available / fails.
    Runs in an external interpreter so it works from a frozen build."""
    if not (audio_path and os.path.isfile(audio_path)):
        return audio_path
    py = real_python()
    if not py or not demucs_available():
        log("  [align] demucs not available — using full mix.")
        return audio_path
    os.makedirs(work_dir, exist_ok=True)
    out_root = os.path.join(work_dir, "demucs")
    try:
        cmd = [py, "-m", "demucs", "--two-stems", "vocals",
               "-n", "htdemucs", "-o", out_root, audio_path]
        log("  [align] isolating vocals with demucs… (first run downloads the model)")
        r = subprocess.run(cmd, capture_output=True, text=True,
                           creationflags=CREATE_NO_WINDOW)
        if r.returncode != 0:
            log(f"  [align] demucs failed: {(r.stderr or '')[-300:]}")
            return audio_path
    except Exception as e:
        log(f"  [align] demucs error: {e}")
        return audio_path
    # demucs writes <out_root>/<model>/<track>/vocals.wav
    base = os.path.splitext(os.path.basename(audio_path))[0]
    for root, _dirs, files in os.walk(out_root):
        for f in files:
            if f.lower() == "vocals.wav" and base in root:
                stem = os.path.join(root, f)
                log(f"  [align] vocal stem ready: {os.path.basename(stem)}")
                return stem
    # fallback: any vocals.wav produced
    for root, _dirs, files in os.walk(out_root):
        for f in files:
            if f.lower() == "vocals.wav":
                return os.path.join(root, f)
    log("  [align] demucs produced no vocals.wav — using full mix.")
    return audio_path


# ─────────────────────────── energy VAD ───────────────────────────────────
def vocal_phrase_onsets(wav_path, log=print):
    """Detect where SINGING starts in an isolated vocal stem using short-time RMS
    energy — stdlib only (wave + audioop), no torch/whisper/CUDA. Returns a list
    of phrase START times (seconds). The idea: the stem is mostly silent except
    when there are vocals, so each burst of energy is a sung phrase."""
    import wave
    import contextlib
    try:
        import audioop
    except Exception:
        log("  [align] audioop unavailable — can't do energy VAD.")
        return []
    HOP = 0.05  # 50 ms analysis windows
    energies = []
    try:
        with contextlib.closing(wave.open(wav_path, "rb")) as wf:
            sr, sw, ch = wf.getframerate(), wf.getsampwidth(), wf.getnchannels()
            win = max(1, int(sr * HOP))
            while True:
                frames = wf.readframes(win)
                if not frames:
                    break
                if ch == 2:
                    frames = audioop.tomono(frames, sw, 0.5, 0.5)
                try:
                    energies.append(audioop.rms(frames, sw))
                except Exception:
                    energies.append(0)
    except Exception as e:
        log(f"  [align] energy VAD read failed: {e}")
        return []
    if not energies:
        return []
    peak = max(energies)
    if peak <= 0:
        return []
    floor = sorted(energies)[len(energies) // 2]            # median ≈ noise floor
    thr = max(peak * 0.06, floor * 2.2, 1.0)                # voiced threshold
    voiced = [e > thr for e in energies]
    n = len(voiced)
    GAP = int(0.35 / HOP)      # merge silences shorter than 350 ms into one phrase
    MINLEN = int(0.18 / HOP)   # ignore blips shorter than 180 ms
    onsets, i = [], 0
    while i < n:
        if voiced[i]:
            start = i
            j, gap = i, 0
            while j < n:
                if voiced[j]:
                    gap = 0
                else:
                    gap += 1
                    if gap > GAP:
                        break
                j += 1
            if (j - gap) - start >= MINLEN:
                onsets.append(round(start * HOP, 3))
            i = j
        else:
            i += 1
    log(f"  [align] energy VAD: {len(onsets)} vocal phrases detected")
    return onsets


def assign_lines_to_onsets(lines, onsets, log=print):
    """Lay lyric LINES onto detected vocal phrase onsets, in order.
    Returns [(time, text), ...].

    The detected phrase count (N) rarely equals the lyric-line count (M) —
    breaths, instrument bleed and "oohs" add extra onsets, while held notes or
    fast back-to-back lines merge into one. A naive even-spread (line i -> onset
    i*(N-1)/(M-1)) therefore drifts: a single spurious onset near the start
    shifts every following line.

    Instead we anchor BOTH ends to something real:
      * each line's position is its cumulative WORD fraction through the song
        (long lines take more time, so the next line lands later), and
      * when there are at least as many phrases as lines, every line SNAPS to a
        real detected onset (an actual vocal start), so extra breath-onsets are
        absorbed rather than cascading. When there are fewer phrases than lines,
        we interpolate between real onsets by word fraction.
    Errors stay local instead of accumulating. You still drag to fine-tune."""
    lines = [l for l in lines if l.strip()]
    onsets = sorted(float(o) for o in onsets)
    if not lines or not onsets:
        return None
    M, N = len(lines), len(onsets)
    weights = [max(1, len(l.split())) for l in lines]
    W = float(sum(weights)) or 1.0

    pairs, last = [], -1.0
    if N >= M:
        # Snap each line to the real onset nearest its word fraction. Enforce
        # strictly increasing onset indices so two lines never grab the same one.
        cum, prev_idx = 0.0, -1
        for i, line in enumerate(lines):
            idx = int(round((cum / W) * (N - 1)))
            idx = max(0, min(N - 1, idx))
            if idx <= prev_idx:
                idx = min(N - 1, prev_idx + 1)
            prev_idx = idx
            t = onsets[idx]
            if t < last:
                t = last
            last = t
            cum += weights[i]
            pairs.append((round(t, 3), line))
    else:
        # Fewer phrases than lines: several lines share a phrase. Place each at a
        # word-weighted point between its onset and the next so they don't stack.
        cum = 0.0
        for i, line in enumerate(lines):
            pos = (cum / W) * (N - 1)
            lo = max(0, min(N - 1, int(pos)))
            hi = min(N - 1, lo + 1)
            t = onsets[lo] + (onsets[hi] - onsets[lo]) * (pos - lo)
            if t <= last:
                t = last + 0.05
            last = t
            cum += weights[i]
            pairs.append((round(t, 3), line))
    log(f"  [align] mapped {M} lines onto {N} phrases "
        f"({'snap-to-onset' if N >= M else 'interpolated'}, word-weighted)")
    return pairs


# ───────────────────────────── whisper ────────────────────────────────────
def whisperx_available():
    return _have("whisperx")


def any_whisper_available():
    return _have("whisperx") or _have("faster_whisper") or _have("stable_whisper")


def _transcribe_whisperx(wav_path, work_dir, log=print, language=None):
    """Transcribe via WhisperX in an external interpreter. Returns segments as
    [[(word, start, end), ...], ...] or None."""
    py = real_python()
    if not py or not _have("whisperx", py):
        return None
    lang = language or "en"
    script = (
        "import sys, json, whisperx\n"
        "wav = sys.argv[1]\n"
        "dev = 'cpu'\n"
        "try:\n"
        "    import torch\n"
        "    dev = 'cuda' if torch.cuda.is_available() else 'cpu'\n"
        "except Exception:\n"
        "    dev = 'cpu'\n"
        "ct = 'float16' if dev == 'cuda' else 'int8'\n"
        "model = whisperx.load_model('small', dev, compute_type=ct, language=%r)\n"
        "audio = whisperx.load_audio(wav)\n"
        "res = model.transcribe(audio, batch_size=8)\n"
        "try:\n"
        "    a, meta = whisperx.load_align_model(language_code=res['language'], device=dev)\n"
        "    res = whisperx.align(res['segments'], a, meta, audio, dev, return_char_alignments=False)\n"
        "except Exception:\n"
        "    pass\n"
        "segs = []\n"
        "for s in res.get('segments', []):\n"
        "    ws = s.get('words') or []\n"
        "    seg = [(w.get('word','').strip(), float(w.get('start', s.get('start',0))), "
        "float(w.get('end', s.get('end',0)))) for w in ws if w.get('word')]\n"
        "    if not seg and s.get('text'):\n"
        "        seg = [(s['text'].strip(), float(s.get('start',0)), float(s.get('end',0)))]\n"
        "    if seg: segs.append(seg)\n"
        "print('@@J@@' + json.dumps(segs))\n"
    ) % (lang,)
    try:
        r = subprocess.run([py, "-c", script, wav_path],
                           capture_output=True, text=True,
                           creationflags=CREATE_NO_WINDOW)
        return _parse_segs(r.stdout, log)
    except Exception as e:
        log(f"  [align] whisperx subprocess failed: {e}")
        return None


def transcribe_faster_subprocess(wav_path, log=print, language="en", model="base"):
    """Transcribe via faster-whisper in an external interpreter. Returns segments
    as [[(word, start, end), ...], ...] or None."""
    py = real_python()
    if not py or not _have("faster_whisper", py):
        return None
    script = (
        "import sys, json\n"
        "from faster_whisper import WhisperModel\n"
        "wav = sys.argv[1]\n"
        "dev, ct = 'cpu', 'int8'\n"
        "try:\n"
        "    import torch\n"
        "    if torch.cuda.is_available(): dev, ct = 'cuda', 'float16'\n"
        "except Exception:\n"
        "    pass\n"
        "try:\n"
        "    m = WhisperModel(%r, device=dev, compute_type=ct)\n"
        "except Exception:\n"
        "    m = WhisperModel(%r, device='cpu', compute_type='int8')\n"
        "segs_it, _info = m.transcribe(wav, language=%r, word_timestamps=True)\n"
        "segs = []\n"
        "for s in segs_it:\n"
        "    ws = getattr(s, 'words', None) or []\n"
        "    seg = [(w.word.strip(), float(w.start), float(w.end)) for w in ws if (w.word or '').strip()]\n"
        "    if not seg and (s.text or '').strip():\n"
        "        seg = [(s.text.strip(), float(s.start), float(s.end))]\n"
        "    if seg: segs.append(seg)\n"
        "print('@@J@@' + json.dumps(segs))\n"
    ) % (model, model, language)
    try:
        r = subprocess.run([py, "-c", script, wav_path],
                           capture_output=True, text=True,
                           creationflags=CREATE_NO_WINDOW)
        return _parse_segs(r.stdout, log)
    except Exception as e:
        log(f"  [align] faster-whisper subprocess failed: {e}")
        return None


def _parse_segs(stdout, log=print):
    """Pull the @@J@@<json> payload out of subprocess stdout."""
    if not stdout:
        return None
    for line in stdout.splitlines():
        if line.startswith("@@J@@"):
            try:
                data = json.loads(line[5:])
                # JSON tuples come back as lists — normalise to tuples.
                return [[tuple(w) for w in seg] for seg in data] or None
            except Exception as e:
                log(f"  [align] couldn't parse transcription JSON: {e}")
                return None
    return None


def align(audio_path, work_dir, log=print, language=None):
    """Full pipeline. Returns segments [[(word,start,end),...],...] or None."""
    if not any_whisper_available():
        log("  [align] no whisperx / faster-whisper installed — skipping word alignment.")
        return None
    os.makedirs(work_dir, exist_ok=True)
    stem = isolate_vocals(audio_path, work_dir, log)
    segs = None
    if whisperx_available():
        try:
            segs = _transcribe_whisperx(stem, work_dir, log, language=language)
        except Exception as e:
            log(f"  [align] whisperx error: {e}")
    if not segs:
        segs = transcribe_faster_subprocess(stem, log, language=language or "en")
    if not segs and stem != audio_path:
        # last resort: try the raw mix
        if whisperx_available():
            segs = _transcribe_whisperx(audio_path, work_dir, log, language=language)
        if not segs:
            segs = transcribe_faster_subprocess(audio_path, log, language=language or "en")
    return segs or None


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python lyrics_align.py <audio.wav>")
        sys.exit(2)
    a = sys.argv[1]
    wd = os.path.join(os.path.dirname(a) or ".", "_align_work")
    stem = isolate_vocals(a, wd, print)
    ons = vocal_phrase_onsets(stem, print)
    print("onsets:", ons[:20], "..." if len(ons) > 20 else "")
