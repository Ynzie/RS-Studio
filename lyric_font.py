#!/usr/bin/env python3
"""
lyric_font.py — generate a custom glyph atlas (.dds) + GlyphDefinitions (.glyphs.xml)
for NON-LATIN lyrics, replicating what DLC Builder's external "Font Generator"
tool produces.

Why: Rocksmith's built-in lyrics font (assets/ui/lyrics/lyrics.dds) only covers
Latin characters. Japanese/Cyrillic/Greek/etc. render as blanks unless the CDLC
ships its own glyph atlas. DLC Builder packs a custom font when a Vocals
arrangement has CustomFont set: it loads "<name>.glyphs.xml" (via
Path.ChangeExtension on the .dds) and the matching ".dds", then writes the SNG
SymbolsTextures / SymbolDefinitions from them (see PackageBuilder.getFontOption +
ConvertVocals.xmlToSng).

So we emit exactly:
  <base>.dds          — 32-bit BGRA uncompressed atlas, white glyphs on alpha
  <base>.glyphs.xml   — <GlyphDefinitions TextureWidth TextureHeight> with one
                        <GlyphDefinition Symbol Inner*/Outer*> per character,
                        coordinates normalised 0..1 over the texture.
Then set the Vocals arrangement's CustomFont to "<base>.dds" (Japanese=true).

NOTE: the DDS pixel format / V-orientation should be confirmed in-game once; if
glyphs appear vertically flipped, set FLIP_V = True below.
"""
import os
import struct

FLIP_V = False  # set True if lyrics render upside-down in game

# Default lyrics.dds covers ASCII + Latin-1 + Latin Extended-A/B. Anything past
# Latin Extended-B (U+024F) needs a custom font.
_LATIN_MAX = 0x024F


def needs_custom_font(text):
    """True if the text contains characters the default lyric font can't render."""
    for ch in text:
        if ch in "\r\n\t ":
            continue
        if ord(ch) > _LATIN_MAX:
            return True
    return False


# Broad-coverage Windows fonts, CJK-capable first, then general Unicode.
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\YuGothM.ttc", r"C:\Windows\Fonts\YuGothR.ttc",
    r"C:\Windows\Fonts\meiryo.ttc",  r"C:\Windows\Fonts\msgothic.ttc",
    r"C:\Windows\Fonts\malgun.ttf",  r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simsun.ttc",  r"C:\Windows\Fonts\seguisym.ttf",
    r"C:\Windows\Fonts\arialuni.ttf", r"C:\Windows\Fonts\arial.ttf",
]


def _find_font(font_hint=None):
    for f in ([font_hint] if font_hint else []) + _FONT_CANDIDATES:
        if f and os.path.isfile(f):
            return f
    return None


def _po2(n):
    p = 1
    while p < n:
        p <<= 1
    return p


def _write_dds_bgra8(path, width, height, pixels):
    """Write an uncompressed 32-bit BGRA DDS (A8R8G8B8)."""
    DDSD_CAPS, DDSD_HEIGHT, DDSD_WIDTH, DDSD_PIXELFORMAT, DDSD_PITCH = 0x1, 0x2, 0x4, 0x1000, 0x8
    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT | DDSD_PITCH
    DDPF_ALPHAPIXELS, DDPF_RGB = 0x1, 0x40
    hdr = b"DDS " + struct.pack("<I", 124)
    hdr += struct.pack("<IIIII", flags, height, width, width * 4, 0)  # pitch, depth
    hdr += b"\x00" * 44                                               # 11 reserved dwords
    hdr += struct.pack("<IIIIIIII", 32, DDPF_ALPHAPIXELS | DDPF_RGB, 0, 32,
                       0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)  # pixelformat
    hdr += struct.pack("<IIIII", 0x1000, 0, 0, 0, 0)                  # caps (TEXTURE)
    with open(path, "wb") as f:
        f.write(hdr)
        f.write(pixels)


def generate(lyric_lines, out_dir, base_name, log=print, font_hint=None,
             cell=64, tex_w=1024):
    """Render a glyph atlas + glyph definitions for the characters in lyric_lines.
    Returns {"dds":path, "glyphs":path, "chars":n} or None on failure."""
    try:
        from PIL import Image, ImageFont, ImageDraw
    except Exception as e:
        log(f"  [font] Pillow not available: {e}")
        return None

    # unique characters (keep one space glyph)
    chars, seen = [], set()
    for line in lyric_lines:
        for ch in line:
            if ch in "\r\n\t":
                continue
            if ch not in seen:
                seen.add(ch)
                chars.append(ch)
    if " " not in seen:
        chars.append(" ")
    if not chars:
        return None

    font_path = _find_font(font_hint)
    if not font_path:
        log("  [font] no Unicode-capable font found on this system — cannot build "
            "custom lyric font. Install/point to a font (e.g. Noto Sans).")
        return None
    fnt = ImageFont.truetype(font_path, int(cell * 0.8))

    cols = max(1, tex_w // cell)
    rows = (len(chars) + cols - 1) // cols
    tex_h = _po2(rows * cell)

    img = Image.new("RGBA", (tex_w, tex_h), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    glyphs = []
    for i, ch in enumerate(chars):
        cx, cy = (i % cols) * cell, (i // cols) * cell
        try:
            bb = draw.textbbox((0, 0), ch, font=fnt)
        except Exception:
            bb = (0, 0, cell, cell)
        gw, gh = bb[2] - bb[0], bb[3] - bb[1]
        ix0 = cx + max(0, (cell - gw) // 2)
        iy0 = cy + max(0, (cell - gh) // 2)
        draw.text((ix0 - bb[0], iy0 - bb[1]), ch, font=fnt, fill=(255, 255, 255, 255))
        ix1, iy1 = ix0 + gw, iy0 + gh
        glyphs.append((ch,
                       cx / tex_w, cy / tex_h, (cx + cell) / tex_w, (cy + cell) / tex_h,
                       ix0 / tex_w, iy0 / tex_h, ix1 / tex_w, iy1 / tex_h))

    if FLIP_V:
        img = img.transpose(Image.FLIP_TOP_BOTTOM)
        glyphs = [(ch, oxn, 1 - oyx, oxx, 1 - oyn, ixn, 1 - iyx, ixx, 1 - iyn)
                  for (ch, oxn, oyn, oxx, oyx, ixn, iyn, ixx, iyx) in glyphs]

    dds_path = os.path.join(out_dir, base_name + ".dds")
    _write_dds_bgra8(dds_path, tex_w, tex_h, img.tobytes("raw", "BGRA"))

    def _esc(c):
        return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}.get(c, c)

    xml = ['<?xml version="1.0" encoding="utf-8"?>',
           f'<GlyphDefinitions TextureWidth="{tex_w}" TextureHeight="{tex_h}">']
    for (ch, oxn, oyn, oxx, oyx, ixn, iyn, ixx, iyx) in glyphs:
        xml.append(
            f'  <GlyphDefinition Symbol="{_esc(ch)}" '
            f'InnerYMin="{iyn:.6f}" InnerYMax="{iyx:.6f}" '
            f'InnerXMin="{ixn:.6f}" InnerXMax="{ixx:.6f}" '
            f'OuterYMin="{oyn:.6f}" OuterYMax="{oyx:.6f}" '
            f'OuterXMin="{oxn:.6f}" OuterXMax="{oxx:.6f}"/>')
    xml.append("</GlyphDefinitions>")
    glyphs_path = os.path.join(out_dir, base_name + ".glyphs.xml")
    with open(glyphs_path, "w", encoding="utf-8") as f:
        f.write("\n".join(xml))

    log(f"  [font] custom lyric font: {len(chars)} glyphs, {tex_w}x{tex_h} "
        f"({os.path.basename(font_path)})")
    return {"dds": dds_path, "glyphs": glyphs_path, "chars": len(chars)}
