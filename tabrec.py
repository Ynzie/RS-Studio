"""Tab strip recognition: notes, rhythm, time sigs, lyrics."""
import cv2
import numpy as np
import staffdet

import os
_D = os.path.dirname(os.path.abspath(__file__))
_T = np.load(os.path.join(_D, 'digit_templates.npz'))
TEMPLATES, TLABELS = _T['templates'], _T['labels']
try:
    _S = np.load(os.path.join(_D, 'sig_templates.npz'))
    SIG_T, SIG_L = _S['templates'], _S['labels']
except Exception:
    SIG_T, SIG_L = TEMPLATES, TLABELS


def classify_sig_digit(gray_crop):
    v = cv2.resize(255 - gray_crop, (16, 20), interpolation=cv2.INTER_AREA).astype(np.float32)
    best, bl = -2, '~'
    for t, l in zip(SIG_T, SIG_L):
        r = cv2.matchTemplate(v, t, cv2.TM_CCOEFF_NORMED)[0][0]
        if r > best:
            best, bl = r, l
    return bl, best


def classify_glyph(gray_crop):
    v = cv2.resize(255 - gray_crop, (16, 20), interpolation=cv2.INTER_AREA).astype(np.float32)
    best, bl = -2, '~'
    for t, l in zip(TEMPLATES, TLABELS):
        r = cv2.matchTemplate(v, t, cv2.TM_CCOEFF_NORMED)[0][0]
        if r > best:
            best, bl = r, l
    return (bl if best > 0.55 else '~'), best


def extract_notes(img, bw, lines):
    """Digit tokens on staff lines -> notes: {x, string(1-indexed from top), fret, tie, dead}"""
    sp = (lines[-1] - lines[0]) / (len(lines) - 1)
    nol = staffdet.remove_staff_lines(bw, lines)
    comps, lab = staffdet.glyph_components(nol, lines)
    glyphs = []
    graces = []
    for c in comps:
        if not (7 <= c['h'] <= 16 and 2 <= c['w'] <= 14):
            continue
        d = [abs(c['cy'] - ly) for ly in lines]
        if min(d) > sp * 0.5:
            continue
        g = img[c['y']:c['y'] + c['h'], c['x']:c['x'] + c['w']]
        sym, score = classify_glyph(g)
        if sym == '~':
            continue
        if c['h'] <= 8 and sym not in '<>':
            if sym.isdigit():
                graces.append(c['x'] + c['w'] / 2.0)
            continue                     # grace/slide digits are smaller
        glyphs.append({'x': c['x'], 'x2': c['x'] + c['w'], 'cy': c['cy'],
                       'line': int(np.argmin(d)), 'sym': sym, 'score': score})
    # group into tokens (same line, small gap)
    glyphs.sort(key=lambda g: (g['line'], g['x']))
    tokens = []
    for g in glyphs:
        if tokens and tokens[-1]['line'] == g['line'] and g['x'] - tokens[-1]['x2'] <= 8:
            tokens[-1]['sym'] += g['sym']
            tokens[-1]['x2'] = g['x2']
        else:
            tokens.append(dict(g))
    notes = []
    for t in tokens:
        s = t['sym']
        s = s.replace('LR', 'X').replace('L', 'X').replace('R', 'X')
        tie = s.startswith('(')
        s = s.strip('()')
        harm = '<' in s or '>' in s
        s = s.strip('<>')
        if not s:
            continue
        dead = s == 'X'
        if not dead and not s.isdigit():
            continue
        fret = 0 if dead else int(s)
        if fret > 24:
            continue
        notes.append({'x': t['x'], 'x2': t['x2'], 'xc': (t['x'] + t['x2']) / 2.0,
                      'string': t['line'] + 1, 'fret': fret,
                      'tie': tie, 'dead': dead, 'harm': harm})
    notes.sort(key=lambda n: n['x'])
    return notes, graces


def find_time_sigs(img, bw, lines, nol=None):
    """Time signature = tall fused component (stacked digits). Split & classify.
    Returns [(x, num, den)]."""
    sp = (lines[-1] - lines[0]) / (len(lines) - 1)
    if nol is None:
        nol = staffdet.remove_staff_lines(bw, lines)
    comps, lab = staffdet.glyph_components(nol, lines, pad=2)
    sigs = []
    for c in comps:
        if not (10 <= c['w'] <= 22 and 2.3 * sp <= c['h'] <= 3.0 * sp):
            continue
        g = img[c['y']:c['y'] + c['h'], c['x']:c['x'] + c['w']]
        half = c['h'] // 2
        top, bot = g[:half, :], g[c['h'] - half:, :]
        s1, sc1 = classify_sig_digit(top)
        s2, sc2 = classify_sig_digit(bot)
        if s1.isdigit() and s2.isdigit() and min(sc1, sc2) > 0.6:
            sigs.append((c['x'], int(s1), int(s2)))
    return sorted(sigs)


def find_stems(bw, lines, notes_xs):
    """Stems below bottom staff line.
    Full stem = quarter (or shorter w/ beams); short bottom-half stem = half note.
    Returns list of {x, beams, dot, short} sorted by x."""
    h, w = bw.shape
    y_bot = int(lines[-1])
    sp = (lines[-1] - lines[0]) / (len(lines) - 1)
    y0 = y_bot + 3
    y1 = min(h, int(round(y_bot + 2.7 * sp)))
    band = bw[y0:y1, :]
    bh = band.shape[0]
    colsum = band.sum(axis=0)
    stems = []
    x = 0
    while x < w:
        if colsum[x] >= 0.35 * bh:
            xx0 = x
            while x < w and colsum[x] >= 0.35 * bh:
                x += 1
            if x - xx0 <= 4:  # thin vertical
                # measure ink extent in this column group
                colink = band[:, xx0:x].max(axis=1)
                ys = np.where(colink > 0)[0]
                top, bot = ys.min(), ys.max()
                length = bot - top + 1
                if length < 0.30 * bh:
                    continue
                short = top > 0.30 * bh  # starts well below staff -> half note
                stems.append(((xx0 + x - 1) / 2.0, short, bot))
        else:
            x += 1
    out = []
    for sx, short, bot in stems:
        sxi = int(sx)
        beams = 0
        if not short:
            # count thick horizontal runs (beams) near stem bottom
            run = False
            for yy in range(int(bh * 0.45), bh):
                lft = band[yy, max(0, sxi - 10):sxi - 1].sum()
                rgt = band[yy, sxi + 2:sxi + 11].sum()
                if (lft >= 4) or (rgt >= 4):
                    if not run:
                        beams += 1
                        run = True
                else:
                    run = False
        out.append({'x': sx, 'beams': int(beams), 'dot': False, 'short': bool(short)})
    # augmentation dots: small isolated square blobs; assign to stem on their left
    nb, lab, stats, cent = cv2.connectedComponentsWithStats(band.astype(np.uint8), 8)
    for i in range(1, nb):
        x, y, ww, hh, area = stats[i]
        if 2 <= ww <= 5 and 2 <= hh <= 5 and 4 <= area <= 20 and y >= 4:
            cx = cent[i][0]
            for s in out:
                if 2.0 < cx - s['x'] <= 12:
                    s['dot'] = True
                    break
    return out


def find_tuplets(img, bw, lines):
    """Italic '3' tuplet digits below the stem band -> x centers."""
    h, w = bw.shape
    y_bot = int(lines[-1])
    sp = (lines[-1] - lines[0]) / (len(lines) - 1)
    y0 = int(y_bot + 2.7 * sp)
    y1 = min(h, int(y_bot + 4.3 * sp))
    if y1 <= y0:
        return []
    band = (bw[y0:y1, :] > 0).astype(np.uint8)
    n, lab, stats, cent = cv2.connectedComponentsWithStats(band, connectivity=8)
    boxes = []
    for i in range(1, n):
        x, y, ww, hh, area = stats[i]
        boxes.append((x, y, ww, hh, area, cent[i][0]))
    xs = []
    for x, y, ww, hh, area, cx in boxes:
        if not (4 <= ww <= 10 and 6 <= hh <= 12 and area >= 10):
            continue
        # must be isolated (tuplet digit sits in a bracket gap, lyrics cluster)
        near = [b for b in boxes if b[0] != x and abs((b[0] + b[2] / 2) - cx) < 12
                and abs(b[1] - y) < 6 and b[4] > 6]
        if near:
            continue
        g = img[y0 + y:y0 + y + hh, x:x + ww]
        sym, score = classify_glyph(g)
        if sym != '3' or score <= 0.5:
            continue
        # require tuplet bracket: horizontal segment beside the digit
        ry0 = max(0, y - 4)
        ry1 = min(band.shape[0], y + int(hh * 0.7))
        lseg = band[ry0:ry1, max(0, x - 28):max(0, x - 2)]
        rseg = band[ry0:ry1, x + ww + 2:x + ww + 28]
        lmax = lseg.sum(axis=1).max() if lseg.size else 0
        rmax = rseg.sum(axis=1).max() if rseg.size else 0
        if max(lmax, rmax) >= 12:
            xs.append(cx)
    return xs


def find_rests(img, bw, lines, note_xs, barlines=None):
    """Rest glyphs in staff band away from notes.
    Returns [{'x', 'kind' ('q'|'e'), 'dot'}]."""
    h, w = bw.shape
    sp = (lines[-1] - lines[0]) / (len(lines) - 1)
    nol = staffdet.remove_staff_lines(bw, lines)
    comps, lab = staffdet.glyph_components(nol, lines, pad=2)
    # keep unassigned mid-size comps, merge vertically stacked pieces
    cand = []
    for c in comps:
        if c['x'] < 45 or c['x'] > w - 25:       # clef / edge zones
            continue
        if any(abs(c['cx'] - nx) < 7 for nx in note_xs):
            continue
        if c['w'] > 12 or c['h'] > 2.5 * sp or c['area'] < 8:
            continue
        cand.append(dict(c))
    cand.sort(key=lambda c: c['x'])
    merged = []
    for c in cand:
        if merged and c['x'] - (merged[-1]['x'] + merged[-1]['w']) < 4 and \
           abs(c['cx'] - merged[-1]['cx']) < 8:
            m = merged[-1]
            x2 = max(m['x'] + m['w'], c['x'] + c['w'])
            y2 = max(m['y'] + m['h'], c['y'] + c['h'])
            m['x'] = min(m['x'], c['x']); m['y'] = min(m['y'], c['y'])
            m['w'] = x2 - m['x']; m['h'] = y2 - m['y']
            m['cx'] = m['x'] + m['w'] / 2.0
            m['area'] += c['area']
        else:
            merged.append(c)
    rests = []
    for m in merged:
        kind = None
        if 1.5 * sp <= m['h'] <= 2.4 * sp and 4 <= m['w'] <= 10:
            kind = 'q'
        elif 0.85 * sp <= m['h'] < 1.5 * sp and 5 <= m['w'] <= 11:
            g = img[m['y']:m['y'] + m['h'], m['x']:m['x'] + m['w']]
            sym, score = classify_glyph(g)
            if score < 0.72:
                kind = 'e'
        if kind:
            # augmentation dot: tiny blob to the right at similar height
            dot = any(2 <= c['w'] <= 5 and 2 <= c['h'] <= 5 and
                      0 < c['x'] - (m['x'] + m['w']) < 14 and
                      abs(c['cy'] - m['cy']) < sp for c in comps)
            rests.append({'x': m['cx'], 'kind': kind, 'dot': dot})
    return rests
