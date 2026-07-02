#!/usr/bin/env python3
"""
pdf2gp.py - Convert Ultimate-Guitar print-to-PDF tabs into Guitar Pro (.gp5)
plus a timestamped lyrics file (.lrc).

Usage:
    python3 pdf2gp.py song.pdf [more_tracks.pdf ...] [-o out.gp5]

Each PDF becomes one track in the output file. Lyrics (if present) are taken
from the first PDF that has them.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
from fractions import Fraction

import cv2

# Suppress the console window that Windows briefly flashes for every spawned
# subprocess when this runs inside a --windowed (no-console) app like RS
# Studio. OCR calls tesseract once per detected text row (tempo marks,
# section labels, lyric lines) across every system in the PDF, so without
# this a full song looked like it was popping dozens of windows in a loop.
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == 'win32' else 0
import numpy as np
import pikepdf
import pdfplumber
import guitarpro

import staffdet
import tabrec

NOTE_PC = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}


# ---------------------------------------------------------------- PDF layer
def extract_strips(pdf_path):
    """Embedded system-strip images, in reading order."""
    pk = pikepdf.open(pdf_path)
    strips = []
    for pi, page in enumerate(pk.pages):
        items = []
        for name, obj in page.images.items():
            img = pikepdf.PdfImage(obj)
            pil = img.as_pil_image().convert('L')
            arr = np.array(pil)
            items.append((name, arr))
        # reading order = order in resource dict (UG prints emit top-down)
        for name, arr in items:
            strips.append(arr)
    return strips


def extract_meta(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        txt = pdf.pages[0].extract_text() or ''
    lines = [l.strip() for l in txt.split('\n') if l.strip()]
    title, artist, tuning = '', '', []
    for i, l in enumerate(lines):
        m = re.match(r'Tuning:\s*([A-G][#b]?(?:\s+[A-G][#b]?)+)', l)
        if m:
            tuning = m.group(1).split()
            if i >= 2:
                title, artist = lines[i - 2], lines[i - 1]
            break
    if not title and len(lines) >= 3:
        title, artist = lines[1], lines[2]
    return title, artist, tuning


def tuning_to_midi(names):
    """Low->high note names to MIDI numbers (low->high)."""
    if not names:
        names = ['E', 'A', 'D', 'G']
    n = len(names)
    first_pc = NOTE_PC[names[0][0]] + (1 if '#' in names[0] else 0) - (1 if 'b' in names[0] else 0)
    base = 23 if n <= 5 else 40          # bass B0 region / guitar E2 region
    midi0 = base
    while midi0 % 12 != first_pc % 12:
        midi0 += 1
    out = [midi0]
    for nm in names[1:]:
        pc = NOTE_PC[nm[0]] + (1 if '#' in nm else 0) - (1 if 'b' in nm else 0)
        m = out[-1] + 1
        while m % 12 != pc % 12:
            m += 1
        out.append(m)
    return out


# ---------------------------------------------------------------- OCR utils
def _find_tesseract():
    import shutil
    p = shutil.which('tesseract')
    if p:
        return p
    here = os.path.dirname(os.path.abspath(__file__))
    for c in (os.path.join(here, 'tesseract.exe'),
              os.path.join(here, 'Tesseract-OCR', 'tesseract.exe'),
              r'C:\Program Files\Tesseract-OCR\tesseract.exe',
              r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
              os.path.expandvars(r'%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe'),
              '/usr/bin/tesseract', '/usr/local/bin/tesseract'):
        if os.path.isfile(c):
            return c
    return None


TESSERACT = _find_tesseract()
_warned_no_tess = [False]


def _lyric_lang():
    if TESSERACT is None:
        return 'eng'
    try:
        r = subprocess.run([TESSERACT, '--list-langs'], capture_output=True, text=True,
                           encoding='utf-8', errors='replace',
                           creationflags=_CREATE_NO_WINDOW)
        langs = (r.stdout or '').split()
        return 'spa' if 'spa' in langs else 'eng'
    except Exception:
        return 'eng'


LYRIC_LANG = None   # resolved lazily


def ocr_region(img, psm=7, tsv=False, lang=None):
    if TESSERACT is None:
        if not _warned_no_tess[0]:
            _warned_no_tess[0] = True
            print('NOTE: tesseract not found - skipping lyrics/section/tempo text.\n'
                  '      Install from https://github.com/UB-Mannheim/tesseract/wiki\n'
                  '      or put tesseract.exe next to pdf2gp.py')
        return ''
    g = cv2.resize(img, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
    g = cv2.copyMakeBorder(g, 16, 16, 16, 16, cv2.BORDER_CONSTANT, value=255)
    fn = tempfile.mktemp(suffix='.png')
    cv2.imwrite(fn, g)
    args = [TESSERACT, fn, 'stdout', '--psm', str(psm)]
    if lang:
        args += ['-l', lang]
    if tsv:
        args.append('tsv')
    r = subprocess.run(args, capture_output=True, text=True,
                       encoding='utf-8', errors='replace',
                       creationflags=_CREATE_NO_WINDOW)
    os.unlink(fn)
    return r.stdout or ''


def find_text_rows(bw, y_from, y_to, min_comps=2):
    """Rows of text-like components between y_from..y_to -> [(ytop,ybot)]"""
    band = np.zeros_like(bw)
    band[y_from:y_to, :] = bw[y_from:y_to, :]
    n, lab, stats, cent = cv2.connectedComponentsWithStats(band.astype(np.uint8), 8)
    rows = {}
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if 3 <= h <= 18 and area >= 6 and w <= 40:
            key = None
            for k in rows:
                if abs(k - (y + h)) <= 5:
                    key = k
                    break
            if key is None:
                rows[y + h] = []
                key = y + h
            rows[key].append((x, y, w, h))
    out = []
    for k, comps in rows.items():
        if len(comps) >= min_comps:
            ys = [c[1] for c in comps]
            hs = [c[1] + c[3] for c in comps]
            out.append((min(ys), max(hs)))
    return sorted(out)


# ---------------------------------------------------------------- strip rec
def recognize_strip(arr):
    """Full recognition of one system strip -> dict."""
    img = arr
    _, bw = cv2.threshold(img, 200, 1, cv2.THRESH_BINARY_INV)
    lines, ext = staffdet.find_staff_lines(bw)
    if len(lines) < 4:
        return None
    sp = (lines[-1] - lines[0]) / (len(lines) - 1)
    bars = [b[0] for b in staffdet.find_barlines(bw, lines, ext)]
    notes, graces = tabrec.extract_notes(img, bw, lines)
    nxs = [n['xc'] for n in notes]
    stems = tabrec.find_stems(bw, lines, nxs)
    stems = [s for s in stems
             if not any(abs(s['x'] - gx) <= 12 for gx in graces)]
    tuplets = tabrec.find_tuplets(img, bw, lines)
    rests = tabrec.find_rests(img, bw, lines, nxs)
    sigs = tabrec.find_time_sigs(img, bw, lines)

    h, w = bw.shape
    y_top, y_bot = int(lines[0]), int(lines[-1])

    # ---- above-staff text: section labels + tempo
    section, tempo = None, None
    rows = find_text_rows(bw, 0, max(0, y_top - 4))
    for (ry0, ry1) in rows:
        crop = img[max(0, ry0 - 2):ry1 + 2, :]
        txt = ocr_region(crop, psm=7).strip()
        m = re.search(r'=\s*(\d{2,3})\b', txt)
        # real tempo rows contain little besides the mark; measure-number rows are long
        if m and len(re.sub(r'[^0-9A-Za-z=]', '', txt)) <= 8:
            tempo = int(m.group(1))
        ms = re.search(r'(Post[- ]?Chorus|Pre[- ]?Chorus|Intro|Verse|Chorus|Bridge|'
                       r'Outro|Interlude|Solo|Break)\s*(\d*)',
                       txt, re.I)
        if ms:
            # x position: leftmost component in that row band
            band = bw[max(0, ry0 - 2):ry1 + 2, :]
            cols = np.where(band.sum(axis=0) > 0)[0]
            # label may share row w/ tempo glyphs; use section word's rough x via crop OCR boxes
            section = (ms.group(1).title() + ((' ' + ms.group(2)) if ms.group(2) else ''),
                       int(cols.min()) if len(cols) else 0)

    # ---- below-staff: lyric row = deepest text row below stems
    lyr_from = int(y_bot + 2.6 * sp)
    lyrics = []
    rows = find_text_rows(bw, lyr_from, h, min_comps=3)
    if rows:
        ry0, ry1 = rows[-1]
        if ry1 - ry0 >= 6:
            crop = img[max(0, ry0 - 3):min(h, ry1 + 4), :]
            global LYRIC_LANG
            if LYRIC_LANG is None:
                LYRIC_LANG = _lyric_lang()
            tsv = ocr_region(crop, psm=7, tsv=True, lang=LYRIC_LANG)
            toks = []
            for line in tsv.splitlines()[1:]:
                f = line.split('\t')
                if len(f) >= 12 and f[11].strip() and float(f[10]) > 30:
                    x = (int(f[6]) - 16) / 4.0
                    ww = int(f[8]) / 4.0
                    toks.append({'x': x, 'x2': x + ww, 'text': f[11].strip()})
            joined = ' '.join(t['text'] for t in toks).lower()
            if 'let ring' not in joined and 'p.m' not in joined:
                for t in toks:
                    t['text'] = clean_lyric_token(t['text'])
                    if t['text']:
                        lyrics.append(t)

    return {'lines': lines, 'sp': sp, 'bars': bars, 'notes': notes, 'stems': stems,
            'tuplets': tuplets, 'rests': rests, 'sigs': sigs,
            'section': section, 'tempo': tempo, 'lyrics': lyrics, 'width': w}


# ---------------------------------------------------------------- assembly
def stem_duration(stem):
    """-> (value, dotted) in GP terms (4=quarter...)"""
    if stem is None:
        return (1, False)                      # no stem: whole (fill measure)
    if stem['short']:
        return (2, stem['dot'])
    v = {0: 4, 1: 8, 2: 16, 3: 32}.get(stem['beams'], 4)
    return (v, stem['dot'])


def build_measures(strips_rec):
    """-> measures: [{'sig':(n,d)|None, 'beats':[...], 'section':str|None}], lyrics"""
    measures = []
    all_lyr = []
    cur_sig = None
    for rec in strips_rec:
        if rec is None:
            continue
        bars = rec['bars']
        if len(bars) < 2:
            continue
        # group notes into beat columns
        events = []                             # {'x','notes':[],'stem','rest'}
        for n_ in rec['notes']:
            ev = None
            for e in events:
                if abs(e['x'] - n_['xc']) <= 8:
                    ev = e
                    break
            if ev is None:
                ev = {'x': n_['xc'], 'notes': [], 'stem': None, 'rest': None}
                events.append(ev)
            ev['notes'].append(n_)
            ev['x'] = np.mean([m['xc'] for m in ev['notes']])
        for r_ in rec['rests']:
            events.append({'x': r_['x'], 'notes': [], 'stem': None, 'rest': r_})
        # attach stems; unattached stems = tie-continuation beats (hidden notes)
        for s_ in rec['stems']:
            best, bd = None, 12
            for e in events:
                d = abs(e['x'] - s_['x'])
                if d < bd and e['notes']:
                    bd, best = d, e
            if best is not None:
                best['stem'] = s_
            else:
                events.append({'x': s_['x'], 'notes': [], 'stem': s_,
                               'rest': None, 'tiecont': True})
        events.sort(key=lambda e: e['x'])
        # tuplet flags: mark 3 nearest events around each tuplet x
        for tx in rec['tuplets']:
            cands = sorted(events, key=lambda e: abs(e['x'] - tx))[:3]
            for c in cands:
                c['triplet'] = True
        # split into measures by barlines
        sig_for_next = {}
        for (sx, sn, sd) in rec['sigs']:
            if sx >= bars[-1] - 30:
                continue                        # courtesy sig at line end
            # applies to measure starting at nearest bar left of sx
            bi = max([i for i, b in enumerate(bars[:-1]) if b <= sx + 5], default=0)
            sig_for_next[bi] = (sn, sd)
        first_in_strip = True
        for bi in range(len(bars) - 1):
            x0, x1 = bars[bi], bars[bi + 1]
            if x1 - x0 < 25:
                continue
            mev = [e for e in events if x0 - 2 <= e['x'] < x1 - 2]
            sig = sig_for_next.get(bi)
            if sig:
                cur_sig = sig
            sec = None
            if rec['section'] and x0 - 20 <= rec['section'][1] < x1:
                sec = rec['section'][0]
            if rec['section'] and bi == 0 and rec['section'][1] < bars[0] + 30:
                sec = rec['section'][0]
            measures.append({'sig': cur_sig, 'events': mev, 'section': sec,
                             'x0': x0, 'x1': x1, 'strip': rec,
                             'tempo': rec['tempo'] if first_in_strip else None})
            first_in_strip = False
        for L in rec['lyrics']:
            all_lyr.append({**L, 'strip': rec})
    return measures, all_lyr


def fit_measure(meas):
    """Assign each event a Fraction duration (in quarter notes) that sums to sig."""
    n, d = meas['sig'] if meas['sig'] else (4, 4)
    target = Fraction(n * 4, d)
    evs = meas['events']
    if not evs:
        meas['beats'] = [{'rest': True, 'dur': target, 'notes': []}]
        return
    beats = []
    for e in evs:
        if e['rest'] is not None:
            v = Fraction(1) if e['rest']['kind'] == 'q' else Fraction(1, 2)
            if e['rest'].get('dot'):
                v *= Fraction(3, 2)
            beats.append({'rest': True, 'dur': v, 'notes': [], 'x': e['x']})
        else:
            val, dot = stem_duration(e['stem'])
            v = Fraction(4, val)
            if dot:
                v *= Fraction(3, 2)
            if e.get('triplet'):
                v *= Fraction(2, 3)
            beats.append({'rest': False, 'dur': v, 'notes': e['notes'], 'x': e['x'],
                          'nostem': e['stem'] is None,
                          'tiecont': bool(e.get('tiecont'))})
    total = sum(b['dur'] for b in beats)
    # events without stems (whole/half fill): distribute remaining time
    nostem = [b for b in beats if b.get('nostem')]
    if nostem and total != target:
        rem = target - sum(b['dur'] for b in beats if not b.get('nostem'))
        if rem > 0:
            share = rem / len(nostem)
            for b in nostem:
                b['dur'] = share
            total = target
    x0, x1 = meas['x0'], meas['x1']
    if total < target and beats:
        # trailing ring-out: big empty space after last beat -> extend with tied beats
        ppq0 = (x1 - x0 - 15.0) / float(target)
        last = beats[-1]
        trailing = x1 - last['x']
        if trailing > (float(last['dur']) * ppq0) * 1.8 and (target - total) >= Fraction(1, 2):
            rem = target - total
            for std in (Fraction(4), Fraction(3), Fraction(2), Fraction(3, 2),
                        Fraction(1), Fraction(3, 4), Fraction(1, 2), Fraction(1, 4),
                        Fraction(1, 8)):
                while rem >= std:
                    beats.append({'rest': last['rest'], 'dur': std,
                                  'notes': last['notes'], 'x': last['x'] + 1,
                                  'tiecont': not last['rest']})
                    rem -= std
            total = sum(b['dur'] for b in beats)
    if total != target and beats:
        orig = total
        # spacing-guided fixer: choose dur edits that best match engraved gaps
        gaps = []
        for i, b in enumerate(beats):
            nx = beats[i + 1]['x'] if i + 1 < len(beats) else x1
            gaps.append(max(4.0, nx - b['x']))
        for _ in range(12):
            total = sum(b['dur'] for b in beats)
            if total == target:
                break
            ppq = (x1 - x0 - 15.0) / float(target)   # px per quarter
            best = None                              # (err_gain, i, newdur)
            for i, b in enumerate(beats):
                ops = []
                if total < target:
                    ops = [b['dur'] * 2, b['dur'] * Fraction(3, 2), b['dur'] * 3]
                else:
                    ops = [b['dur'] / 2, b['dur'] * Fraction(2, 3)]
                for nd in ops:
                    if nd == b['dur'] or nd > target or nd < Fraction(1, 8):
                        continue
                    newtotal = total - b['dur'] + nd
                    # don't overshoot
                    if total < target and newtotal > target:
                        continue
                    if total > target and newtotal < target:
                        continue
                    err_now = abs(gaps[i] - float(b['dur']) * ppq)
                    err_new = abs(gaps[i] - float(nd) * ppq)
                    gain = err_now - err_new + 0.001 * float(nd - b['dur'])
                    if best is None or gain > best[0]:
                        best = (gain, i, nd)
            if best is None:
                break
            beats[best[1]]['dur'] = best[2]
        total = sum(b['dur'] for b in beats)
        if total != target:
            diff = target - total
            if diff > 0:
                beats[-1]['dur'] += diff
            else:
                need = -diff
                for b in reversed(beats):
                    cut = min(need, b['dur'] - Fraction(1, 8))
                    b['dur'] -= cut
                    need -= cut
                    if need <= 0:
                        break
        meas['warn'] = 'duration fit adjusted (%s -> %s)' % (orig, target)
    # snap every beat to a GP-representable duration; exact-fill with tied beats
    STD = sorted({Fraction(4, v) * m
                  for v in (1, 2, 4, 8, 16, 32, 64)
                  for m in (Fraction(1), Fraction(3, 2), Fraction(2, 3))},
                 reverse=True)
    for b in beats:
        if b['dur'] not in STD:
            b['dur'] = next((s for s in STD if s <= b['dur']), STD[-1])
    total = sum(b['dur'] for b in beats)
    if beats and total != target:
        if total < target:
            rem = target - total
            last = beats[-1]
            for std in STD:
                while rem >= std:
                    beats.append({'rest': last['rest'], 'dur': std,
                                  'notes': last['notes'], 'x': last.get('x', 0) + 1,
                                  'tiecont': not last['rest']})
                    rem -= std
        else:
            need = total - target
            for b in reversed(beats):
                while need > 0:
                    smaller = next((s for s in STD if s < b['dur']), None)
                    if smaller is None or b['dur'] - smaller > need:
                        break
                    need -= b['dur'] - smaller
                    b['dur'] = smaller
                if need <= 0:
                    break
    meas['beats'] = beats


def frac_to_gp(dur):
    """Fraction of quarters -> (value, isDotted, tuplet(en,tm)) best match."""
    table = []
    for val in (1, 2, 4, 8, 16, 32, 64):
        base = Fraction(4, val)
        table.append((base, val, False, (1, 1)))
        table.append((base * Fraction(3, 2), val, True, (1, 1)))
        table.append((base * Fraction(2, 3), val, False, (3, 2)))
    best = min(table, key=lambda t: abs(t[0] - dur))
    return best[1], best[2], best[3]


# ---------------------------------------------------------------- GP output
def build_song(track_data, title, artist, tempo):
    song = guitarpro.Song()
    song.title = title
    song.artist = artist
    if tempo:
        song.tempo = tempo
    else:
        song.tempo = 120
        print('WARNING: could not read a tempo mark from the PDF (OCR miss, or '
              'none printed above the staff) - defaulting to 120 BPM. This is '
              'almost certainly NOT the song\'s real tempo. Fix it in RS Studio '
              'before building: set the BPM field on the Main page, or load '
              'audio and use the "Auto ↺" BPM button on the Sync page to '
              'rescale it to match.')
    song.tracks = []
    nmeas = max(len(td['measures']) for td in track_data)
    # headers
    song.measureHeaders = []
    cur = (4, 4)
    ref = max(track_data, key=lambda td: len(td['measures']))['measures']
    for i in range(nmeas):
        h = guitarpro.MeasureHeader()
        h.number = i + 1
        if i < len(ref) and ref[i]['sig']:
            cur = ref[i]['sig']
        h.timeSignature.numerator = cur[0]
        h.timeSignature.denominator.value = cur[1]
        if i < len(ref) and ref[i].get('section'):
            h.marker = guitarpro.Marker(title=ref[i]['section'])
        song.measureHeaders.append(h)

    for ti, td in enumerate(track_data):
        track = guitarpro.Track(song)
        track.number = ti + 1
        track.name = td['name']
        track.offset = 0
        midis = td['tuning']                      # low->high
        track.strings = [guitarpro.GuitarString(i + 1, m)
                         for i, m in enumerate(reversed(midis))]
        is_bass = len(midis) <= 5 or 'bass' in td['name'].lower()
        track.channel = guitarpro.MidiChannel()
        track.channel.channel = ti * 2
        track.channel.effectChannel = ti * 2 + 1
        track.channel.instrument = 33 if is_bass else 25
        track.measures = []
        song.tracks.append(track)
        prev_frets = {}
        last_played = []
        cur_tempo = song.tempo
        pending_tempo = None
        tempo_done = (ti > 0)   # only emit tempo changes on first track
        for i in range(nmeas):
            header = song.measureHeaders[i]
            measure = guitarpro.Measure(track, header)
            voice = measure.voices[0]
            if i < len(td['measures']):
                m = td['measures'][i]
                if m.get('tempo') and m['tempo'] != cur_tempo and not tempo_done:
                    cur_tempo = m['tempo']
                    pending_tempo = cur_tempo
                for b in m['beats']:
                    beat = guitarpro.Beat(voice)
                    val, dot, tup = frac_to_gp(b['dur'])
                    beat.duration.value = val
                    beat.duration.isDotted = dot
                    if tup != (1, 1):
                        beat.duration.tuplet.enters, beat.duration.tuplet.times = tup
                    if pending_tempo:
                        mtc = guitarpro.MixTableChange()
                        mtc.tempo = guitarpro.MixTableItem(value=pending_tempo,
                                                           duration=0, allTracks=True)
                        beat.effect.mixTableChange = mtc
                        pending_tempo = None
                    notes_src = b['notes']
                    if b.get('tiecont') and not notes_src:
                        notes_src = [{'string': s, 'fret': f, 'dead': False,
                                      'tie': True, 'harm': False}
                                     for s, f in last_played]
                    if b['rest'] or not notes_src:
                        beat.status = guitarpro.BeatStatus.rest
                    else:
                        beat.status = guitarpro.BeatStatus.normal
                        played = []
                        for n_ in notes_src:
                            note = guitarpro.Note(beat)
                            note.string = n_['string']
                            if n_['dead']:
                                note.type = guitarpro.NoteType.dead
                                note.value = prev_frets.get(n_['string'], 0)
                            elif n_['tie']:
                                note.type = guitarpro.NoteType.tie
                                note.value = prev_frets.get(n_['string'], n_['fret'])
                            else:
                                note.type = guitarpro.NoteType.normal
                                note.value = n_['fret']
                                prev_frets[n_['string']] = n_['fret']
                            if n_.get('harm'):
                                note.effect.harmonic = guitarpro.NaturalHarmonic()
                            played.append((note.string, note.value))
                            beat.notes.append(note)
                        last_played = played
                    voice.beats.append(beat)
            if not voice.beats:
                beat = guitarpro.Beat(voice)
                beat.duration.value = 1
                beat.status = guitarpro.BeatStatus.rest
                voice.beats.append(beat)
            track.measures.append(measure)
    return song


# ---------------------------------------------------------------- lyrics/lrc
def time_of(measure_times, mi, frac_in_measure, spq):
    return measure_times[mi] + float(frac_in_measure) * spq


def compute_lyric_times(measures, lyrics, tempo):
    """Map each syllable to nearest beat time (honoring tempo changes)."""
    bpm = tempo or 120
    mt = [0.0]
    spqs = []
    for m in measures:
        if m.get('tempo'):
            bpm = m['tempo']
        spq = 60.0 / bpm
        spqs.append(spq)
        n, d = m['sig'] if m['sig'] else (4, 4)
        mt.append(mt[-1] + float(Fraction(n * 4, d)) * spq)
    # beat absolute times + x positions per strip
    beat_pts = []                                # (strip_id, x, t)
    for mi, m in enumerate(measures):
        t = mt[mi]
        spq = spqs[mi]
        for b in m['beats']:
            if 'x' in b:
                beat_pts.append((id(m['strip']), b['x'], t))
            t += float(b['dur']) * spq
    out = []
    for L in lyrics:
        sid = id(L['strip'])
        xc = (L['x'] + L['x2']) / 2.0
        cands = [(abs(x - xc), t) for s, x, t in beat_pts if s == sid]
        if not cands:
            continue
        cands.sort()
        out.append({'t': cands[0][1], 'text': L['text']})
    out.sort(key=lambda o: o['t'])
    return out


def _norm_word(w):
    import unicodedata
    w = unicodedata.normalize('NFD', w.lower())
    w = ''.join(c for c in w if unicodedata.category(c) != 'Mn')
    return re.sub(r"[^a-z0-9']+", '', w)


def fetch_reference_lyrics(title, artist):
    """Fetch plain lyrics from LRCLIB (open lyrics API, no key). None on failure."""
    import json
    import urllib.parse
    import urllib.request
    import difflib

    def get(url):
        req = urllib.request.Request(url, headers={'User-Agent': 'pdf2gp/1.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)

    try:
        q = urllib.parse.urlencode({'track_name': title, 'artist_name': artist})
        results = get('https://lrclib.net/api/search?' + q)
        if not results:
            q = urllib.parse.urlencode({'q': '%s %s' % (title, artist)})
            results = get('https://lrclib.net/api/search?' + q)
        best, bestscore = None, 0.0
        for d in results or []:
            if d.get('instrumental') or not d.get('plainLyrics'):
                continue
            s = (difflib.SequenceMatcher(None, _norm_word(title),
                                         _norm_word(d.get('trackName') or '')).ratio()
                 + difflib.SequenceMatcher(None, _norm_word(artist),
                                           _norm_word(d.get('artistName') or '')).ratio())
            if s > bestscore:
                bestscore, best = s, d
        if best and bestscore > 1.2:
            print('fetched lyrics from LRCLIB: %s - %s'
                  % (best.get('artistName'), best.get('trackName')))
            return best['plainLyrics']
        print('lyrics auto-fetch: no good match on LRCLIB')
    except Exception as e:
        print('lyrics auto-fetch failed (%s) - continuing without' % e)
    return None


def align_to_reference(timed, ref_text, gap=1.9):
    """Align OCR syllables (with times) to reference lyric lines.
    Returns [(t, line_text)] using reference text verbatim."""
    import difflib
    # reference lines: drop section headers like [Chorus], keep order
    ref_lines = [l.strip() for l in ref_text.splitlines()]
    ref_lines = [l for l in ref_lines if l and not re.match(r'^\[.*\]$', l)]
    ref_words = []                      # (line_idx, word)
    for li, l in enumerate(ref_lines):
        for w in l.split():
            n = _norm_word(w)
            if n:
                ref_words.append((li, n))
    # OCR words: join syllables (hyphen/melisma continuation) with first-syllable time
    ocr_words = []                      # (t, norm)
    cur = None
    prev_cont = False
    for s in timed:
        txt = s['text']
        is_dash = not txt.strip('-\u2013\u2014')
        if is_dash:
            prev_cont = True
            continue
        if cur is not None and (prev_cont or cur['raw'].endswith('-') or txt.startswith('-')):
            cur['raw'] = cur['raw'].rstrip('-') + txt.lstrip('-')
        else:
            if cur:
                ocr_words.append(cur)
            cur = {'t': s['t'], 'raw': txt}
        prev_cont = False
    if cur:
        ocr_words.append(cur)
    for w in ocr_words:
        w['n'] = _norm_word(w['raw'])
    ocr_words = [w for w in ocr_words if w['n']]
    if not ocr_words or not ref_words:
        return None
    # DP alignment (Needleman-Wunsch, similarity = difflib ratio)
    A = [w['n'] for w in ocr_words]
    B = [w for _, w in ref_words]
    la, lb = len(A), len(B)
    GAPP = -0.45
    import array
    score = [[0.0] * (lb + 1) for _ in range(la + 1)]
    back = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(1, la + 1):
        score[i][0] = i * GAPP; back[i][0] = 1
    for j in range(1, lb + 1):
        score[0][j] = j * GAPP; back[0][j] = 2
    for i in range(1, la + 1):
        ai = A[i - 1]
        row = score[i]; prow = score[i - 1]; brow = back[i]
        for j in range(1, lb + 1):
            r = difflib.SequenceMatcher(None, ai, B[j - 1]).ratio()
            m = prow[j - 1] + (r if r >= 0.5 else r - 0.6)
            d = prow[j] + GAPP
            g = row[j - 1] + GAPP
            best = max(m, d, g)
            row[j] = best
            brow[j] = 0 if best == m else (1 if best == d else 2)
    # traceback -> time for ref word j
    ref_time = {}
    i, j = la, lb
    while i > 0 or j > 0:
        b = back[i][j]
        if b == 0 and i > 0 and j > 0:
            ref_time[j - 1] = ocr_words[i - 1]['t']
            i -= 1; j -= 1
        elif b == 1 and i > 0:
            i -= 1
        else:
            j -= 1
    # line time = first matched word; interpolate missing lines
    line_t = {}
    for wi, (li, _) in enumerate(ref_words):
        if wi in ref_time and li not in line_t:
            line_t[li] = ref_time[wi]
    times = []
    known = sorted(line_t.items())
    if not known:
        return None
    for li in range(len(ref_lines)):
        if li in line_t:
            times.append(line_t[li])
        else:
            prevs = [(l, t) for l, t in known if l < li]
            nxts = [(l, t) for l, t in known if l > li]
            if prevs and nxts:
                (l0, t0), (l1, t1) = prevs[-1], nxts[0]
                times.append(t0 + (t1 - t0) * (li - l0) / (l1 - l0))
            elif prevs:
                times.append(prevs[-1][1] + 2.0 * (li - prevs[-1][0]))
            else:
                times.append(max(0.0, nxts[0][1] - 2.0 * (nxts[0][0] - li)))
    # enforce monotonic
    for k in range(1, len(times)):
        if times[k] < times[k - 1]:
            times[k] = times[k - 1] + 0.01
    return list(zip(times, ref_lines))


def write_lrc_ref(aligned, path, title, artist):
    with open(path, 'w', encoding='utf-8') as f:
        f.write('[ti:%s]\n[ar:%s]\n[re:pdf2gp]\n\n' % (title, artist))
        for t, line in aligned:
            f.write('[%02d:%05.2f]%s\n' % (int(t // 60), t % 60, line))
    return len(aligned)


LYRIC_OK = re.compile(r"[A-Za-z\u00c0-\u00ff\u00bf\u00a1'\u2019,.!?\"() \-\u2013\u2014]+")


def clean_lyric_token(text):
    """Strip OCR junk from a lyric syllable. Returns '' if nothing real is left."""
    text = text.replace('\u201c', '"').replace('\u201d', '"').replace('\u2018', "'")
    # pure short dash = melisma mark: keep
    if not text.strip('-\u2013\u2014') and 0 < len(text) <= 2:
        return text
    kept = ''.join(m.group(0) for m in LYRIC_OK.finditer(text))
    letters = sum(1 for ch in kept if ch.isalpha())
    if letters == 0:
        return ''
    # mostly-symbol tokens are OCR noise
    if letters < len(kept.strip()) / 2:
        return ''
    return kept.strip()


def write_lrc(timed, path, title, artist, gap=1.9):
    timed = [dict(s, text=clean_lyric_token(s['text'])) for s in timed]
    timed = [s for s in timed if s['text']]
    lines = []
    cur = None
    for s in timed:
        w = s['text']
        word_open = (cur and cur['words'] and cur['words'][-1].endswith('-')) \
            or w.startswith('-')
        if cur is None or ((s['t'] - cur['last']) > gap and not word_open):
            if cur:
                lines.append(cur)
            cur = {'t': s['t'], 'words': [], 'last': s['t']}
        cur['words'].append(w)
        cur['last'] = s['t']
    if cur:
        lines.append(cur)

    def join(words):
        out = ''
        glue = False
        for w in words:
            if not w.strip('-\u2013\u2014'):
                glue = True                 # melisma dash: word continues
                continue
            if glue or out.endswith('-') or w.startswith('-'):
                out = out.rstrip('-') + w.lstrip('-')
            else:
                out += (' ' if out else '') + w
            glue = False
        # syllable hyphens/dashes inside words are notation, not spelling
        out = re.sub(r'(?<=\w)\s*[-\u2013\u2014]+\s*(?=\w)', '', out)
        return out

    with open(path, 'w', encoding='utf-8') as f:
        f.write('[ti:%s]\n[ar:%s]\n[re:pdf2gp]\n\n' % (title, artist))
        for L in lines:
            t = L['t']
            f.write('[%02d:%05.2f]%s\n' % (int(t // 60), t % 60, join(L['words'])))
    return len(lines)


# ---------------------------------------------------------------- main
def convert(pdf_paths, out_path=None, lrc_path=None):
    first_meta = None
    track_data = []
    all_measures = None
    all_lyrics = None
    tempo = None
    for p in pdf_paths:
        title, artist, tuning = extract_meta(p)
        if first_meta is None:
            first_meta = (title, artist)
        strips = extract_strips(p)
        recs = [recognize_strip(a) for a in strips]
        recs = [r for r in recs if r]
        for r in recs:
            if r['tempo']:
                tempo = tempo or r['tempo']
        measures, lyr = build_measures(recs)
        for m in measures:
            fit_measure(m)
        warns = [(i + 1, m['warn']) for i, m in enumerate(measures) if m.get('warn')]
        nnotes = sum(len(b['notes']) for m in measures for b in m['beats'])
        print('%s: %d systems, %d measures, %d notes, %d lyric syllables'
              % (os.path.basename(p), len(recs), len(measures), nnotes, len(lyr)))
        for wn, wtext in warns:
            print('   warn measure %d: %s' % (wn, wtext))
        midis = tuning_to_midi(tuning)
        base = os.path.splitext(os.path.basename(p))[0].lower()
        name = None
        for key, label in (('bass', 'Bass'), ('lead', 'Lead'), ('rhythm', 'Rhythm'),
                           ('guitar', 'Guitar'), ('vocal', 'Vocals'), ('acoustic', 'Acoustic')):
            if key in base:
                name = label
                break
        if not name:
            name = 'Bass' if len(midis) <= 5 else 'Guitar'
        track_data.append({'measures': measures, 'tuning': midis, 'name': name})
        if lyr and (all_lyrics is None or len(lyr) > len(all_lyrics)):
            all_lyrics, all_measures = lyr, measures

    title, artist = first_meta
    if out_path and os.path.basename(out_path) == 'song.gp5':
        safe = re.sub(r'[\\/:*?"<>|]+', '', title).strip() or 'song'
        out_path = os.path.join(os.path.dirname(out_path), safe + '.gp5')
    base = out_path or (re.sub(r'\W+', '_', title.lower()).strip('_') or 'song') + '.gp5'
    song = build_song(track_data, title, artist, tempo)
    guitarpro.write(song, base)
    print('wrote %s  (tempo %s, tracks: %s)' %
          (base, tempo, ', '.join(t['name'] for t in track_data)))
    if all_lyrics:
        lrc = lrc_path or os.path.splitext(base)[0] + '.lrc'
        timed = compute_lyric_times(all_measures, all_lyrics, tempo)
        ref = None
        import glob as _g
        here = os.path.dirname(os.path.abspath(__file__))
        cands = (sorted(_g.glob(os.path.join(here, 'input', '*.txt')))
                 + sorted(_g.glob(os.path.join(os.path.dirname(pdf_paths[0]), '*.txt'))))
        for cand in cands:
            try:
                ref = open(cand, encoding='utf-8-sig').read()
            except OSError:
                continue
            if ref.strip():
                print('using reference lyrics: %s' % cand)
                break
            ref = None
        if ref is None:
            ref = fetch_reference_lyrics(title, artist)
            if ref:
                try:
                    autopath = os.path.join(here, 'input', 'lyrics-auto.txt')
                    with open(autopath, 'w', encoding='utf-8') as f:
                        f.write(ref)
                    print('saved fetched lyrics to %s (edit if needed)' % autopath)
                except OSError:
                    pass
        if ref is None:
            print('NOTE: no reference lyrics (no .txt in input, auto-fetch found none).')
            print('      The .lrc will be raw OCR text. Paste real lyrics into')
            print('      input/lyrics.txt and re-run for a clean result.')
        aligned = align_to_reference(timed, ref) if ref else None
        if aligned:
            nl = write_lrc_ref(aligned, lrc, title, artist)
            print('wrote %s  (%d lines, aligned to reference lyrics)' % (lrc, nl))
        else:
            nl = write_lrc(timed, lrc, title, artist)
            print('wrote %s  (%d lines, %d syllables, OCR only)' % (lrc, nl, len(timed)))
    return base


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('pdfs', nargs='*')
    ap.add_argument('-o', '--out')
    ap.add_argument('--lrc')
    a = ap.parse_args()
    import glob as _glob
    here = os.path.dirname(os.path.abspath(__file__))
    indir = os.path.join(here, 'input')
    if not a.pdfs:
        os.makedirs(indir, exist_ok=True)
        found = sorted(_glob.glob(os.path.join(indir, '*.pdf')))
        if not found:
            print('Drop your printed tab PDFs into: %s' % indir)
            print('(one song at a time; name them like song-bass.pdf, song-lead.pdf, '
                  'song-rhythm.pdf)')
            sys.exit(1)
        a.pdfs = found
        if not a.out:
            outdir = os.path.join(here, 'output')
            os.makedirs(outdir, exist_ok=True)
            a.out = os.path.join(outdir, 'song.gp5')   # renamed to title after parse
    paths = []
    for p in a.pdfs:
        hits = sorted(_glob.glob(p))
        paths.extend(hits if hits else [p])
    # stable, sensible track order: bass, lead, rhythm, then others
    order = {'bass': 0, 'lead': 1, 'rhythm': 2}
    paths.sort(key=lambda p: order.get(
        next((k for k in order if k in os.path.basename(p).lower()), ''), 3))
    convert(paths, a.out, a.lrc)
