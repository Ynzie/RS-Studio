#!/usr/bin/env python3
"""
lyric_sync.py — the lyric timing engine, lifted out of the GUI so it can be
iterated on without touching the 7,000-line gp2rs_studio.py.

One entry point:

    align_lyrics(audio_path, lyrics_path, work_dir, log=print, status=None)
        -> {"lrc": path, "words_json": path|None, "lines": int,
            "source": str, "first": float, "last": float}   (or None)

Pipeline (best → fallback, all automatic — no buttons):
  1. demucs isolates the vocal stem (via lyrics_align, a subprocess so it works
     in the frozen .exe). The stem is also copied next to the audio as
     <audio>_vocals.wav so you can hear exactly what was transcribed.
  2. PRIMARY: energy VAD on the stem finds where each sung phrase starts, and the
     real Genius/lrclib lyric LINES are laid onto those onsets in order. No
     torch/CUDA needed, and the wording is always correct.
  3. FALLBACK: WhisperX (subprocess) → external faster-whisper → in-process
     whisper transcribes the stem with word-level timing, and the real lyric
     lines are anchored to that word stream by CONTENT (forward sliding window)
     so wording stays correct and timing tracks the actual vocal.

Accuracy note: steps 1–3 use demucs + (optionally) WhisperX, which need a Python
3.10–3.13 interpreter. Without them this still runs, but only as well as
in-process whisper on the full mix.
"""
import os
import re

_META_PAT = re.compile(r"^(source|songwriters?|writers?|composers?|lyrics?)\s*[:\.]", re.IGNORECASE)
_TAG_PAT = re.compile(r"^\[.{0,40}\]$")


def _noop(*_a, **_k):
    pass


def read_plain_lines(lyrics_path):
    """Lyric lines with [tags] and 'Source:'/'Writers:' metadata stripped."""
    with open(lyrics_path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.readlines()
    out = []
    for ln in raw:
        s = ln.strip()
        # strip any leading LRC timestamp prefixes like [00:12.30] (one or more)
        s = re.sub(r"^(\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]\s*)+", "", s).strip()
        if not s or _TAG_PAT.match(s) or _META_PAT.match(s):
            continue
        out.append(s)
    return out


def _cw(s):
    return re.sub(r"[^a-z0-9]", " ", s.lower()).split()


def _words_via_whisperx(stem, work_dir, log):
    """[(start, word), ...] from WhisperX, or [] — also returns the segments."""
    try:
        import lyrics_align as la
    except Exception:
        return [], None
    if not getattr(la, "whisperx_available", lambda: False)():
        return [], None
    try:
        segs = la._transcribe_whisperx(stem, work_dir, log)
    except Exception as e:
        log(f"  [lyric_sync] whisperx failed: {e}")
        return [], None
    if not segs:
        return [], None
    words = [(float(s), w) for seg in segs for (w, s, e) in seg]
    return words, segs


def _words_via_inproc(wav, log, plain_lines=None):
    """In-process faster-whisper/stable-ts fallback. Tries each device FULLY
    (load AND transcribe) so a GPU that can load but can't run — e.g. a frozen
    build missing cublas64_12.dll — falls back to CPU instead of giving up."""
    import sys as _sys
    # In a frozen .exe this path is broken (no bundled cublas / VAD model) and the
    # slow CPU grind blocks the UI — so skip it entirely; the external subprocess
    # faster-whisper above is the real path for frozen builds.
    if getattr(_sys, "frozen", False):
        log("  [lyric_sync] skipping in-process whisper in frozen build (use external Python).")
        return []
    try:
        import stable_whisper as st
    except Exception:
        return []

    def _extract(result):
        out = []
        for seg in (getattr(result, "segments", None) or []):
            for w in (getattr(seg, "words", None) or []):
                t = getattr(w, "start", None)
                ww = (getattr(w, "word", "") or "").strip()
                if t is not None and ww:
                    out.append((float(t), ww))
        return out

    for dev, ct in (("cuda", "float16"), ("cuda", "int8"), ("cpu", "int8")):
        try:
            model = st.load_faster_whisper("base", device=dev, compute_type=ct)
        except Exception:
            continue
        gpu_dead = False
        # transcription first (better onset); VAD on then off (VAD model may be missing)
        for _vad in (True, False):
            try:
                tr = model.transcribe(wav, language="en", word_timestamps=True, vad_filter=_vad)
                words = _extract(tr)
                if words:
                    log(f"  [lyric_sync] in-process whisper {dev}/{ct} (vad={_vad}): {len(words)} words")
                    return words
            except Exception as e:
                es = str(e).lower()
                log(f"  [lyric_sync] {dev}/{ct} vad={_vad} failed: {str(e)[:90]}")
                if any(k in es for k in ("cublas", ".dll", "cuda", "cudnn", "out of memory")):
                    gpu_dead = True
                    break   # this device is unusable — skip to the next (CPU)
        if not gpu_dead and plain_lines:
            try:
                aw = _extract(model.align(wav, "\n".join(plain_lines), language="en"))
                if aw:
                    log(f"  [lyric_sync] forced-align {dev}/{ct}: {len(aw)} words")
                    return aw
            except Exception as e:
                log(f"  [lyric_sync] forced-align {dev}/{ct} failed: {str(e)[:90]}")
    return []


def anchor_lines(plain_lines, all_words):
    """Map each lyric line to a start time by matching its first words against the
    word stream with a forward-only sliding window (keeps cues monotonic)."""
    flat = [(t, _cw(w)[0]) for (t, w) in all_words if _cw(w)]
    pairs, search_from, last_t = [], 0, 0.0
    for line in plain_lines:
        lw = _cw(line)
        if not lw:
            pairs.append((last_t, line))
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
        if t_hit < last_t:
            t_hit = last_t
        last_t = t_hit
        pairs.append((t_hit, line))
        if best_score > 0:
            search_from = best_pos + max(1, len(lw) - 1)
    return pairs


def write_lrc(pairs, lrc_path):
    with open(lrc_path, "w", encoding="utf-8") as f:
        for (t, txt) in pairs:
            t2 = max(0.0, t); m = int(t2) // 60; s = t2 - m * 60
            f.write(f"[{m:02d}:{s:05.2f}]{txt}\n")
    return lrc_path


def align_lyrics(audio_path, lyrics_path, work_dir, log=print, status=None):
    status = status or _noop
    if not (audio_path and os.path.isfile(audio_path)):
        log("  [lyric_sync] no audio"); return None
    if not (lyrics_path and os.path.isfile(lyrics_path)):
        log("  [lyric_sync] no lyrics"); return None
    os.makedirs(work_dir, exist_ok=True)

    plain_lines = read_plain_lines(lyrics_path)
    if not plain_lines:
        log("  [lyric_sync] no lyric lines after cleaning"); return None
    log(f"  [lyric_sync] {len(plain_lines)} lyric lines")

    # 1. demucs vocal stem (visible copy next to the audio)
    align_audio = audio_path
    try:
        import lyrics_align as la
        if getattr(la, "demucs_available", lambda: False)():
            status("Isolating vocals (demucs)…")
            stem = la.isolate_vocals(audio_path, work_dir, log)
            if stem and stem != audio_path and os.path.isfile(stem):
                vis = os.path.splitext(audio_path)[0] + "_vocals.wav"
                try:
                    import shutil; shutil.copy2(stem, vis); align_audio = vis
                    log(f"  [lyric_sync] vocals stem → {os.path.basename(vis)}")
                except Exception:
                    align_audio = stem
        else:
            log("  [lyric_sync] demucs not installed — using full mix.")
    except Exception as e:
        log(f"  [lyric_sync] demucs step skipped: {e}")

    # 2. PRIMARY: time the lyric LINES from the vocal stem's phrase onsets
    # (energy VAD — no whisper/CUDA needed). The stem is silent except when
    # there's singing, so each energy burst is a sung phrase; we lay the real
    # Genius lines onto those onsets in order. Words stay correct; timing comes
    # straight from where the voice actually is.
    pairs = None
    source = "demucs-VAD"
    if align_audio != audio_path:            # we have an isolated vocal stem
        status("Detecting vocal phrases…")
        try:
            import lyrics_align as _lav
            onsets = _lav.vocal_phrase_onsets(align_audio, log)
            if onsets:
                pairs = _lav.assign_lines_to_onsets(plain_lines, onsets, log)
                if pairs:
                    log(f"  [lyric_sync] timed {len(pairs)} lines from {len(onsets)} vocal phrases.")
        except Exception as _ve:
            log(f"  [lyric_sync] energy-VAD failed: {_ve}")

    # 3. FALLBACK: if VAD couldn't run, time the lines via whisper word onsets
    # (WhisperX → external faster-whisper → in-process), still keeping the real
    # Genius words and only borrowing the timing.
    if not pairs:
        status("Transcribing vocals…")
        words, segs = _words_via_whisperx(align_audio, work_dir, log)
        source = "WhisperX"
        if not words:
            try:
                import lyrics_align as _la2
                _segs = _la2.transcribe_faster_subprocess(align_audio, log, language="en")
                if _segs:
                    words = [(float(s), w) for seg in _segs for (w, s, e) in seg]
                    source = "faster-whisper(external)"
            except Exception as _fe:
                log(f"  [lyric_sync] external faster-whisper failed: {_fe}")
        if not words:
            words = _words_via_inproc(align_audio, log, plain_lines)
            source = "in-process-whisper"
        if words:
            ws = [t for t, _w in words]
            log(f"  [lyric_sync] source={source} words={len(words)} "
                f"first={min(ws):.1f}s last={max(ws):.1f}s")
            status("Aligning lyric lines…")
            pairs = anchor_lines(plain_lines, words)

    if not pairs:
        log("  [lyric_sync] couldn't time the lyrics (no vocal phrases or words).")
        return None
    log(f"  [lyric_sync] source={source}, {len(pairs)} lines, stem="
        f"{'demucs' if align_audio != audio_path else 'full-mix'}")

    lrc_path = os.path.splitext(lyrics_path)[0] + ".lrc"
    write_lrc(pairs, lrc_path)
    words_json = None
    # We do NOT save a .words.json: it would hold WhisperX's transcribed (often
    # wrong) words, and the build + karaoke prefer it over the correct .lrc text.
    # The .lrc (real Genius/lrclib words) is the single source of truth. Also wipe
    # any stale words.json from a previous run so the build can't pick it up.
    try:
        _stale = os.path.splitext(lyrics_path)[0] + ".words.json"
        if os.path.isfile(_stale):
            os.remove(_stale)
    except Exception:
        pass

    lt = [t for t, _x in pairs]
    log(f"  [lyric_sync] ✓ {len(pairs)} lines → {os.path.basename(lrc_path)} "
        f"(first={min(lt):.1f}s last={max(lt):.1f}s)")
    return {"lrc": lrc_path, "words_json": words_json, "lines": len(pairs),
            "source": source, "first": min(lt), "last": max(lt)}


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("usage: python lyric_sync.py <audio> <lyrics.txt>")
        sys.exit(2)
    a, ly = sys.argv[1], sys.argv[2]
    r = align_lyrics(a, ly, os.path.join(os.path.dirname(ly), "_align_work"), print)
    print("RESULT:", r)
