#!/usr/bin/env python3
"""
align_audio.py - Auto-detect the correct lead-in offset by comparing a
Guitar Pro file's note-onset rhythm against an actual audio file.

HOW IT WORKS
------------
1. Reads the GP file and builds a "click track" - a sparse impulse train
   at every note onset time, starting at t=0 (no lead-in assumed).
2. Decodes the audio file to a raw PCM envelope using ffmpeg.
3. Computes the onset strength envelope of the audio (energy per frame).
4. Cross-correlates the GP click track against the audio onset envelope.
5. The lag of the best correlation peak = how far into the audio the chart's
   bar 1 lands = the lead-in offset you should put in RS Studio.

USAGE
-----
  python align_audio.py song.gp audio.mp3
  python align_audio.py song.gp audio.mp3 --track 0
  python align_audio.py song.gp audio.mp3 --max-offset 30
  python align_audio.py song.gp audio.mp3 --out result.json

Requires: ffmpeg on PATH (or next to this script), numpy.
No ML, no Librosa, no internet.
"""
import argparse
import json
import math
import os
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

import gp2rs  # noqa: E402

SR = 22050      # analysis sample rate (low res is fine; we only need onsets)
HOP = 512       # hop size in samples -> ~23ms per frame at 22050 Hz
FRAME_RATE = SR / HOP   # frames per second (~43 fps)


# ----------------------------------------------------------------- ffmpeg helpers

def _find_ffmpeg():
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


def _decode_audio_mono(path, ffmpeg, sr=SR):
    """Decode audio to 16-bit mono PCM at `sr` Hz via ffmpeg pipe.
    Returns numpy float32 array in [-1, 1]."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error",
           "-i", path,
           "-ac", "1", "-ar", str(sr),
           "-f", "s16le", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=300)
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(
            f"ffmpeg failed to decode audio:\n{r.stderr.decode(errors='replace')[-400:]}")
    raw = r.stdout
    n = len(raw) // 2
    samples = np.frombuffer(raw[:n * 2], dtype="<i2").astype(np.float32) / 32768.0
    return samples


# ----------------------------------------------------------------- onset envelope

def _onset_envelope(samples, sr=SR, hop=HOP):
    """Compute a simple onset strength envelope.
    Uses half-wave rectified spectral flux: sum of positive differences in
    per-band energy between consecutive frames. Very fast, no Librosa needed."""
    # STFT-lite: we just need spectral energy per band per frame.
    # Use short windows and accumulate power.
    win = hop * 2
    n_frames = (len(samples) - win) // hop
    if n_frames <= 0:
        return np.zeros(1, dtype=np.float32)

    # Hann window
    hann = np.hanning(win).astype(np.float32)

    # Build spectral magnitude frames (rfft)
    prev_mag = None
    env = np.zeros(n_frames, dtype=np.float32)
    for i in range(n_frames):
        frame = samples[i * hop: i * hop + win] * hann
        mag = np.abs(np.fft.rfft(frame))
        if prev_mag is not None:
            diff = mag - prev_mag
            diff[diff < 0] = 0   # half-wave rectify
            env[i] = float(diff.sum())
        prev_mag = mag

    # Normalize to [0, 1]
    mx = float(env.max())
    if mx > 0:
        env /= mx
    return env


# ----------------------------------------------------------------- GP click track

def _gp_click_track(gp, track_indices, n_frames, fps):
    """Build a sparse click impulse train from all note onset times in the GP
    file (leadin=0 so bar 1 starts at t=0). Returns float32 array of length
    n_frames."""
    click = np.zeros(n_frames, dtype=np.float32)
    bar_starts = None  # lazily pull from first track
    for ti in track_indices:
        beats, bar_data = gp.track_beats(ti, leadin=0.0)
        if bar_starts is None:
            bar_starts = [t for t, _, _ in bar_data]
        for b in beats:
            if b.notes:
                fi = int(round(b.start * fps))
                if 0 <= fi < n_frames:
                    click[fi] = min(1.0, click[fi] + 1.0)
    # also add bar-line impulses at half strength (helps on sparse sections)
    if bar_starts:
        for t in bar_starts:
            fi = int(round(t * fps))
            if 0 <= fi < n_frames:
                click[fi] = max(click[fi], 0.4)
    # normalize
    mx = float(click.max())
    if mx > 0:
        click /= mx
    return click


# ----------------------------------------------------------------- cross-correlation

def _find_offset(click, audio_env, fps, max_offset_sec=60.0):
    """Cross-correlate click (GP chart) against audio_env (song) to find
    the lag (in seconds) at which they best align.
    Returns (offset_seconds, confidence_0_to_1, debug_dict)."""
    max_lag = min(int(max_offset_sec * fps), len(audio_env) - len(click))
    if max_lag <= 0:
        return 0.0, 0.0, {}

    # We only search positive lags (chart starts after audio start) up to
    # max_offset_sec. Also try a small negative window in case the audio
    # has less intro than expected.
    neg_lag = min(int(5 * fps), len(click) // 4)  # up to 5s before audio start

    # FFT-based cross-correlation for the candidate window
    n = len(audio_env) + neg_lag
    N = 1
    while N < n:
        N <<= 1
    N <<= 1   # zero-pad

    A = np.fft.rfft(audio_env, n=N)
    C = np.fft.rfft(click, n=N)
    xcorr = np.fft.irfft(A * np.conj(C), n=N)

    # Slice out the lags we care about: [-neg_lag, max_lag]
    # negative lags wrap around to the end of the irfft output
    pos_part = xcorr[:max_lag + 1]                    # lags 0 .. max_lag
    neg_part = xcorr[N - neg_lag:][::-1]              # lags -neg_lag .. -1

    combined_xcorr = np.concatenate([neg_part, pos_part])
    lag_frames = np.arange(-neg_lag, max_lag + 1)

    best_i = int(np.argmax(combined_xcorr))
    best_lag = int(lag_frames[best_i])
    best_val = float(combined_xcorr[best_i])
    mean_val = float(combined_xcorr.mean())
    std_val = float(combined_xcorr.std())
    # Z-score as confidence proxy (how many SDs above the mean)
    z = (best_val - mean_val) / (std_val + 1e-9)
    # normalise to [0,1]: z > 8 -> very confident, < 2 -> noisy
    confidence = min(1.0, max(0.0, (z - 2.0) / 6.0))

    offset_sec = best_lag / fps

    # Find the 2nd-best peak (at least 1s away) to measure peak isolation
    exclusion = int(fps)
    masked = combined_xcorr.copy()
    masked[max(0, best_i - exclusion): best_i + exclusion] = -1e9
    second_val = float(masked.max())
    peak_ratio = best_val / (second_val + 1e-9) if second_val > 0 else 999.0

    debug = {
        "best_lag_frames": best_lag,
        "offset_sec": offset_sec,
        "confidence": confidence,
        "z_score": z,
        "peak_ratio": peak_ratio,
        "second_best": second_val,
    }
    return offset_sec, confidence, debug


# ----------------------------------------------------------------- main

def detect_offset(gp_path, audio_path, track_indices=None,
                  max_offset_sec=60.0, verbose=True):
    """Full pipeline. Returns dict with 'offset_sec', 'confidence', etc."""
    if not _HAS_NUMPY:
        raise ImportError(
            "numpy is required for audio alignment. Install it:\n"
            "  pip install numpy")

    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        raise FileNotFoundError(
            "ffmpeg not found. Put ffmpeg.exe next to this script or install it on PATH.")

    if verbose:
        print(f"GP file:    {os.path.basename(gp_path)}")
        print(f"Audio file: {os.path.basename(audio_path)}")

    gp = gp2rs.GPSong(gp_path)

    # choose tracks
    if track_indices is None:
        track_indices = [i for i in range(len(gp.tracks))
                         if gp.track_kind(i) is not None]
    if not track_indices:
        raise ValueError("No playable guitar/bass tracks found in GP file.")
    if verbose:
        names = [gp.tracks[i].findtext("Name") or f"Track {i}" for i in track_indices]
        print(f"Using tracks: {', '.join(names)}")

    # decode audio
    if verbose:
        print("Decoding audio... ", end="", flush=True)
    audio = _decode_audio_mono(audio_path, ffmpeg)
    dur_sec = len(audio) / SR
    if verbose:
        print(f"{dur_sec:.1f}s")

    # build onset envelope from audio
    if verbose:
        print("Computing onset envelope...", end="", flush=True)
    env = _onset_envelope(audio)
    if verbose:
        print(f" {len(env)} frames @ {FRAME_RATE:.1f} fps")

    # build GP chart body length (leadin=0)
    _, chart_end = gp.bar_times(0.0)
    chart_frames = int(math.ceil(chart_end * FRAME_RATE)) + HOP
    if verbose:
        print(f"Chart body: {chart_end:.1f}s")

    # build GP click track (same frame count as audio, or chart length, whichever shorter)
    n_frames = min(len(env), chart_frames)
    if verbose:
        print("Building GP click track...", end="", flush=True)
    click = _gp_click_track(gp, track_indices, n_frames, FRAME_RATE)
    if verbose:
        print(f" {int(click.sum())} note events")

    # cross-correlate
    if verbose:
        print("Cross-correlating...", end="", flush=True)
    offset, confidence, debug = _find_offset(click, env, FRAME_RATE, max_offset_sec)
    if verbose:
        print(" done")

    # round to nearest 0.05s (RS lead-in doesn't need sub-50ms precision)
    offset_rounded = round(max(0.0, offset) / 0.05) * 0.05

    result = {
        "offset_sec": offset_rounded,
        "raw_offset_sec": offset,
        "confidence": confidence,
        "z_score": debug.get("z_score", 0),
        "peak_ratio": debug.get("peak_ratio", 0),
        "audio_duration_sec": dur_sec,
        "chart_body_sec": chart_end,
        "tracks_used": track_indices,
        "gp_file": gp_path,
        "audio_file": audio_path,
    }

    if verbose:
        print()
        print("=" * 55)
        print(f"  Detected lead-in offset : {offset_rounded:6.2f} seconds")
        print(f"  Confidence              : {confidence * 100:5.1f}%")
        print(f"  Z-score                 : {debug.get('z_score', 0):.1f}")
        print(f"  Peak ratio (>1.5 = good): {debug.get('peak_ratio', 0):.2f}")
        print("=" * 55)
        print()
        if confidence < 0.3:
            print("WARNING: low confidence. Possible causes:")
            print("  - GP tempo map doesn't match the recording (run diagnose_timing.py)")
            print("  - Audio is very short or has heavy intro effects/noise")
            print("  - Try --max-offset with a larger value if the intro is long")
            print("  - Double-check the result by ear before building.")
        elif confidence < 0.6:
            print("NOTE: medium confidence. Check the result with the 'Align' button")
            print("in RS Studio and confirm by ear.")
        else:
            print(f"Good confidence! Set lead-in to {offset_rounded:g}s in RS Studio.")

    return result


def main():
    ap = argparse.ArgumentParser(
        description="Auto-detect RS lead-in offset from a GP file + audio file.")
    ap.add_argument("gpfile", help=".gp file")
    ap.add_argument("audiofile", help="audio file (.mp3/.wav/.ogg/.flac)")
    ap.add_argument("--track", type=int, default=None,
                    help="GP track index to use (default: all guitar/bass tracks)")
    ap.add_argument("--max-offset", type=float, default=60.0,
                    help="max lead-in to search for in seconds (default 60)")
    ap.add_argument("--out", default=None,
                    help="save result as JSON to this path")
    args = ap.parse_args()

    tracks = [args.track] if args.track is not None else None
    result = detect_offset(args.gpfile, args.audiofile,
                           track_indices=tracks,
                           max_offset_sec=args.max_offset,
                           verbose=True)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        print(f"Result saved to {args.out}")
    return result


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"\nERROR: {e}")
        try:
            input("\nPress Enter to close...")
        except EOFError:
            pass
        sys.exit(1)
