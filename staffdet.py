"""Staff / barline / glyph detection for UG print-to-PDF tab strips."""
import cv2
import numpy as np


def load_strip(path):
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    # binarize: ink = 1
    _, bw = cv2.threshold(img, 200, 1, cv2.THRESH_BINARY_INV)
    return img, bw


def find_staff_lines(bw):
    """Staff lines via uniform-grid fit (robust to dashed let-ring lines,
    digit-covered lines, etc.). Returns y centers and x extent."""
    h, w = bw.shape
    rowsum = bw.sum(axis=1)

    def group(th):
        cand = rowsum > th * w
        out = []
        y = 0
        while y < h:
            if cand[y]:
                y0 = y
                while y < h and cand[y]:
                    y += 1
                out.append((y0 + y - 1) / 2.0)
            else:
                y += 1
        return out

    strong = group(0.55)
    if len(strong) < 4:
        # short system (staff doesn't span the page): threshold on longest row
        w_eff = float(rowsum.max())
        if w_eff < 0.15 * w:
            return [], (0, w)
        cand = rowsum > 0.75 * w_eff
        out = []
        y = 0
        while y < h:
            if cand[y]:
                y0 = y
                while y < h and cand[y]:
                    y += 1
                out.append((y0 + y - 1) / 2.0)
            else:
                y += 1
        strong = out
    if not strong:
        return [], (0, w)
    # spacing estimate from consecutive diffs in plausible range
    diffs = [b - a for a, b in zip(strong, strong[1:]) if 11 <= b - a <= 18]
    sp = float(np.median(diffs)) if diffs else 14.25
    # best grid origin: candidate whose lattice matches most candidates
    best_lines = []
    for origin in strong:
        matched = {}
        for c in strong:
            k = round((c - origin) / sp)
            if abs(c - (origin + k * sp)) <= 2.0:
                matched[k] = c
        if not matched:
            continue
        ks = sorted(matched)
        # fill missing lattice points where the row still has decent ink
        filled = {}
        for k in range(ks[0], ks[-1] + 1):
            if k in matched:
                filled[k] = matched[k]
            else:
                yy = origin + k * sp
                yi = int(round(yy))
                if 0 <= yi < h and rowsum[max(0, yi - 1):yi + 2].max() > 0.3 * w:
                    filled[k] = yy
                else:
                    filled = None
                    break
        if filled is None:
            continue
        lines = [filled[k] for k in sorted(filled)]
        if len(lines) > len(best_lines):
            best_lines = lines
    lines = best_lines
    if not (4 <= len(lines) <= 7):
        # fall back to strongest contiguous chain
        lines = lines[:7] if lines else strong[:6]
    midrow = int(lines[len(lines) // 2])
    cols = np.where(bw[midrow] > 0)[0]
    if len(cols) == 0:
        return lines, (0, w)
    return lines, (int(cols.min()), int(cols.max()))


def find_barlines(bw, staff_lines, x_extent):
    """Vertical ink runs spanning top->bottom staff line."""
    y0, y1 = int(staff_lines[0]), int(staff_lines[-1])
    band = bw[y0:y1 + 1, :]
    hgt = y1 - y0 + 1
    colsum = band.sum(axis=0)
    cand = colsum > 0.92 * hgt
    xs = []
    x = 0
    w = bw.shape[1]
    while x < w:
        if cand[x]:
            xx0 = x
            while x < w and cand[x]:
                x += 1
            xs.append(((xx0 + x - 1) / 2.0, x - xx0))
        else:
            x += 1
    # barlines are thin (<6 px); the TAB clef / time sig are wider blobs
    return [(cx, wd) for cx, wd in xs if wd <= 6]


def remove_staff_lines(bw, staff_lines):
    """Erase staff lines but keep glyphs crossing them."""
    out = bw.copy()
    h, w = bw.shape
    for ly in staff_lines:
        yc = int(round(ly))
        for y in range(max(0, yc - 2), min(h, yc + 3)):
            row = out[y]
            # erase pixel if no ink support 3px above AND below the line band
            up = bw[max(0, yc - 4):yc - 1, :].sum(axis=0) if yc >= 4 else np.zeros(w)
            dn = bw[yc + 2:min(h, yc + 5), :].sum(axis=0)
            row[(up == 0) & (dn == 0)] = 0
    return out


def glyph_components(nolines, staff_lines, pad=4):
    """Connected components within the staff band (glyph candidates)."""
    y0 = int(staff_lines[0]) - pad
    y1 = int(staff_lines[-1]) + pad
    band = np.zeros_like(nolines)
    band[max(0, y0):y1 + 1, :] = nolines[max(0, y0):y1 + 1, :]
    n, lab, stats, cent = cv2.connectedComponentsWithStats(band.astype(np.uint8), connectivity=8)
    comps = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < 4:
            continue
        comps.append({"x": x, "y": y, "w": w, "h": h, "area": area,
                      "cx": cent[i][0], "cy": cent[i][1], "id": i})
    return comps, lab
