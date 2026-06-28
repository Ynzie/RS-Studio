#!/usr/bin/env python3
"""
gp2rs.py — Guitar Pro 7/8 (.gp) -> Rocksmith 2014 arrangement XML

Converts one track of a .gp file straight into a Rocksmith 2014 lead/rhythm
arrangement XML that DLC Builder (or the old Custom Song Toolkit) accepts.
Skips Editor on Fire entirely for charts whose GP tempo map matches the audio.

Supported: notes, chords (+templates/handshapes), sustains, ties, hammer-ons /
pull-offs, bends (multi-point), shift & legato slides, slide-outs (unpitched),
palm mutes, dead notes, accents, vibrato, tremolo, harmonics, sections ->
RS sections/phrases, tempo & time-signature map -> ebeats, anchors.

Bonus: --lrc file.lrc generates a Rocksmith vocals XML from a synced lyric file.

Usage:
  python3 gp2rs.py song.gp --list                       # show tracks
  python3 gp2rs.py song.gp --track 1 -o lead.xml        # convert track 1
  python3 gp2rs.py song.gp --track 1 --arr Rhythm --leadin 10 \
      --title ".CoDa." --artist "Dead Poet Society" --album "" --year 2021
  python3 gp2rs.py --lrc lyrics.lrc -o vocals.xml       # vocals only
"""
import argparse, math, re, sys, zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from fractions import Fraction

STD_TUNING = [40, 45, 50, 55, 59, 64]  # EADGBE low->high (6 string)
STD_BASS = [28, 33, 38, 43]            # EADG bass
NOTE_VALUES = {"Whole": 4, "Half": 2, "Quarter": 1, "Eighth": Fraction(1, 2),
               "16th": Fraction(1, 4), "32nd": Fraction(1, 8), "64th": Fraction(1, 16),
               "128th": Fraction(1, 32)}

RS_SECTION_MAP = [  # (regex, rs name)
    (r"pre.?chorus", "prechorus"), (r"post.?chorus", "postchorus"),
    (r"chorus", "chorus"), (r"pre.?verse", "preverse"), (r"verse", "verse"),
    (r"intro", "intro"), (r"outro", "outro"), (r"bridge", "bridge"),
    (r"break", "breakdown"), (r"solo", "solo"), (r"interlude", "interlude"),
    (r"riff", "riff"), (r"hook", "hook"), (r"build", "buildup"),
    (r"tag", "tag"), (r"vamp", "vamp"), (r"melody", "melody"),
]


def f3(x):
    return f"{x:.3f}"


# ---------------------------------------------------------------- gp parsing
class Prop:
    """Flatten a gpif <Properties> block into a dict."""
    def __init__(self, el):
        self.d = {}
        if el is None:
            return
        for p in el.findall("Property"):
            name = p.get("name")
            child = list(p)
            if not child:
                self.d[name] = True
            else:
                c = child[0]
                self.d[name] = True if c.tag == "Enable" else (c.text or "").strip()

    def get(self, k, default=None):
        return self.d.get(k, default)

    def __contains__(self, k):
        return k in self.d


class GPNote:
    __slots__ = ("string", "fret", "props", "tie_dest", "tie_orig", "accent")

    def __init__(self, el):
        self.props = Prop(el.find("Properties"))
        self.string = int(self.props.get("String", 0))
        self.fret = int(self.props.get("Fret", 0))
        tie = el.find("Tie")
        self.tie_dest = tie is not None and tie.get("destination") == "true"
        self.tie_orig = tie is not None and tie.get("origin") == "true"
        acc = el.findtext("Accent")
        self.accent = bool(acc and int(acc) & 0x08 or el.find("Accent") is not None)


class GPBeat:
    __slots__ = ("start", "dur", "notes", "props", "tremolo")

    def __init__(self, start, dur, notes, beat_el):
        self.start, self.dur, self.notes = start, dur, notes
        self.props = Prop(beat_el.find("Properties"))
        self.tremolo = beat_el.find("Tremolo") is not None


class GPSong:
    def __init__(self, path):
        with zipfile.ZipFile(path) as z:
            self.root = ET.fromstring(z.read("Content/score.gpif"))
        sc = self.root.find("Score")
        self.title = (sc.findtext("Title") or "").strip()
        self.artist = (sc.findtext("Artist") or "").strip()
        self.album = (sc.findtext("Album") or "").strip()
        self.tracks = list(self.root.find("Tracks"))
        self.masterbars = list(self.root.find("MasterBars"))
        self.bars = {b.get("id"): b for b in self.root.find("Bars")}
        self.voices = {v.get("id"): v for v in self.root.find("Voices")}
        self.beats = {b.get("id"): b for b in self.root.find("Beats")}
        self.notes = {n.get("id"): n for n in self.root.find("Notes")}
        self.rhythms = {r.get("id"): r for r in self.root.find("Rhythms")}
        # tempo automations: bar index -> (bpm, position-in-bar 0..1)
        self.tempos = []
        mt = self.root.find("MasterTrack")
        if mt is not None:
            for a in mt.iter("Automation"):
                if (a.findtext("Type") or "").strip() == "Tempo":
                    val = (a.findtext("Value") or "120 2").split()
                    bpm = float(val[0])
                    unit = int(val[1]) if len(val) > 1 else 2
                    # unit: 1=eighth 2=quarter 3=quarter-dot 4=half 5=half-dot
                    mult = {1: 0.5, 2: 1, 3: 1.5, 4: 2, 5: 3}.get(unit, 1)
                    self.tempos.append((int(a.findtext("Bar") or 0),
                                        float(a.findtext("Position") or 0),
                                        bpm * mult))
        if not self.tempos:
            self.tempos = [(0, 0.0, 120.0)]
        self.tempos.sort()

    def track_names(self):
        return [" ".join((t.findtext("Name") or "?").split()) for t in self.tracks]

    def tuning(self, ti):
        tr = self.tracks[ti]
        for p in tr.iter("Property"):
            if p.get("name") == "Tuning":
                return [int(x) for x in p.findtext("Pitches").split()]
        return STD_TUNING

    def capo(self, ti):
        for p in self.tracks[ti].iter("Property"):
            if p.get("name") == "CapoFret":
                return int(p.findtext("Fret") or 0)
        return 0

    def track_kind(self, ti):
        """'bass', 'guitar', or None (drums/other)."""
        tr = self.tracks[ti]
        name = " ".join((tr.findtext("Name") or "").lower().split())
        short = " ".join((tr.findtext("ShortName") or "").lower().split())
        combined = name + " " + short

        # MIDI channel 9 (0-indexed) = percussion channel — always drums
        for mc in tr.iter("MidiConnection"):
            if (mc.findtext("Channel") or mc.get("channel") or "") in ("9", "10"):
                return None

        # Check GP instrument/sound name if present
        for sound_el in tr.iter("Sound"):
            sound_name = (sound_el.get("name") or sound_el.text or "").lower()
            if any(k in sound_name for k in ("drum", "perc", "sax", "trumpet",
                                              "trombone", "violin", "flute", "piano")):
                return None

        tun = self.tuning(ti)
        if all(t == 0 for t in tun):
            return None

        # Non-guitar/bass instruments — exclude before falling through to "guitar"
        _non_guitar = ("drum", "perc", "kick", "snare", "hat", "cymbal",
                       "vocal", "voc", "voice", "choir", "lead vox",
                       "sax", "saxophone", "tenor", "alto", "soprano", "baritone",
                       "trumpet", "trombone", "horn", "brass", "wind",
                       "flute", "clarinet", "oboe", "violin", "viola",
                       "cello", "fiddle", "piano", "keys", "keyboard",
                       "organ", "synth", "strings", "orchestra",
                       "banjo", "mandolin", "ukulele", "uke")
        if any(k in combined for k in _non_guitar):
            return None
        if "bass" in name or len(tun) <= 5 and max(tun) < 50:
            return "bass"
        return "guitar"

    def effective_tuning(self, ti):
        """Reduce 7/8-string guitars to their top 6 strings; pad bass to 4."""
        tun = self.tuning(ti)
        kind = self.track_kind(ti)
        if kind == "guitar" and len(tun) > 6:
            return tun[len(tun) - 6:], len(tun) - 6  # (tuning, dropped low strings)
        return tun, 0

    def time_sig(self, mb):
        n, d = (mb.findtext("Time") or "4/4").split("/")
        return int(n), int(d)

    def rhythm_quarters(self, rid):
        r = self.rhythms[rid]
        nv = r.find("NoteValue").text.strip()
        dur = Fraction(NOTE_VALUES[nv])
        dot = r.find("AugmentationDot")
        if dot is not None:
            c = int(dot.get("count", 1))
            dur = dur * (2 - Fraction(1, 2 ** c))
        tup = r.find("PrimaryTuplet")
        if tup is not None:
            dur = dur * Fraction(int(tup.get("den")), int(tup.get("num")))
        return dur

    def bar_times(self, leadin, bpm_scale=1.0):
        """Return (start_seconds, quarters_per_bar, bpm) for each masterbar.

        bpm_scale > 1.0 speeds up the note chart so it fits a shorter audio file.
        e.g. bpm_scale = gp_duration / audio_duration compresses notes to match audio.
        """
        out, t = [], leadin
        bpm_idx = 0
        cur_bpm = self.tempos[0][2] * bpm_scale
        for i, mb in enumerate(self.masterbars):
            # tempo changes that land at the start of this bar
            while bpm_idx < len(self.tempos) and self.tempos[bpm_idx][0] <= i:
                cur_bpm = self.tempos[bpm_idx][2] * bpm_scale
                bpm_idx += 1
            n, d = self.time_sig(mb)
            quarters = n * 4 / d
            out.append((t, quarters, cur_bpm))
            t += quarters * 60.0 / cur_bpm
        return out, t  # t = song body end

    def track_beats(self, ti, leadin, bpm_scale=1.0):
        """Yield GPBeat list (merged voices, time-ordered) for a track."""
        bar_times, _ = self.bar_times(leadin, bpm_scale=bpm_scale)
        all_beats = []
        for i, mb in enumerate(self.masterbars):
            bar_id = mb.findtext("Bars").split()[ti]
            bar = self.bars[bar_id]
            t0, quarters, bpm = bar_times[i]
            spq = 60.0 / bpm
            for vid in (bar.findtext("Voices") or "").split():
                if vid == "-1":
                    continue
                pos = Fraction(0)
                for bid in (self.voices[vid].findtext("Beats") or "").split():
                    bel = self.beats[bid]
                    dq = self.rhythm_quarters(bel.find("Rhythm").get("ref"))
                    start = t0 + float(pos) * spq
                    dur = float(dq) * spq
                    nids = (bel.findtext("Notes") or "").split()
                    notes = [GPNote(self.notes[n]) for n in nids]
                    if notes:
                        all_beats.append(GPBeat(start, dur, notes, bel))
                    pos += dq
        all_beats.sort(key=lambda b: b.start)
        return all_beats, bar_times


# ------------------------------------------------------------ rs conversion
class RSNote:
    def __init__(self, t, dur, gpn, beat):
        self.time, self.dur = t, dur
        self.string, self.fret = gpn.string, gpn.fret
        p = gpn.props
        self.palm = "PalmMuted" in p
        self.mute = "Muted" in p
        self.accent = gpn.accent
        self.vibrato = "Vibrato" in p
        self.tremolo = beat.tremolo
        self.harmonic = p.get("HarmonicType") == "Natural"
        self.pinch = p.get("HarmonicType") == "Pinch"
        self.hopo_dest = "HopoDestination" in p
        self.tie_dest, self.tie_orig = gpn.tie_dest, gpn.tie_orig
        self.hammer = self.pull = False
        self.slide_to = self.uslide_to = -1
        self.link_next = False
        slide = int(p.get("Slide", 0)) if "Slide" in p else 0
        self.slide_flags = slide
        self.bend_pts = []  # (frac_of_dur 0..1, semitones)
        if "Bended" in p:
            def g(k):
                return float(p.get(k, 0) or 0)
            pts = [(g("BendOriginOffset") / 100, g("BendOriginValue") / 50),
                   (g("BendMiddleOffset1") / 100, g("BendMiddleValue") / 50),
                   (g("BendMiddleOffset2") / 100, g("BendMiddleValue") / 50),
                   (g("BendDestinationOffset") / 100, g("BendDestinationValue") / 50)]
            seen, out = None, []
            for off, val in pts:
                val = round(val * 2) / 2  # snap to half-semitone
                if (off, val) != seen:
                    out.append((off, val))
                    seen = (off, val)
            self.bend_pts = [(o, v) for o, v in out if v > 0 or o > 0]
            if not any(v > 0 for _, v in self.bend_pts):
                self.bend_pts = []
        self.max_bend = max((v for _, v in self.bend_pts), default=0)


def convert_track(gp, ti, leadin, sustain_min=0.40, bpm_scale=1.0):
    beats, bar_times = gp.track_beats(ti, leadin, bpm_scale=bpm_scale)
    tun = gp.tuning(ti)
    eff, dropped = gp.effective_tuning(ti)
    notes = []
    for b in beats:
        for gpn in b.notes:
            n = RSNote(b.start, b.dur, gpn, b)
            if dropped:
                if n.string < dropped:
                    # note lives on a dropped low string: refret onto new lowest
                    midi = tun[n.string] + n.fret
                    f = midi - eff[0]
                    while f < 0:
                        f += 12
                    if f > 22:
                        continue  # unreachable; drop
                    n.string, n.fret = 0, f
                else:
                    n.string -= dropped
            notes.append(n)
    # merge tied notes into their origin
    by_string = {}
    keep = []
    for n in sorted(notes, key=lambda n: n.time):
        if n.tie_dest:
            prev = by_string.get(n.string)
            if prev is not None:
                prev.dur = (n.time + n.dur) - prev.time
            continue
        by_string[n.string] = n
        keep.append(n)
    notes = keep
    # hammer-on / pull-off resolution + pitched slide destinations
    prev_on_string = {}
    for n in notes:
        p = prev_on_string.get(n.string)
        if n.hopo_dest and p is not None:
            if n.fret > p.fret:
                n.hammer = True
            elif n.fret < p.fret:
                n.pull = True
        prev_on_string[n.string] = n
    nxt_on_string = {}
    for n in reversed(notes):
        if n.slide_flags & 0b11:  # shift or legato slide -> next note same string
            nx = nxt_on_string.get(n.string)
            if nx is not None and nx.fret != n.fret and nx.fret > 0:
                n.slide_to = nx.fret
                n.link_next = True
            else:
                n.uslide_to = max(1, n.fret + (4 if n.slide_flags & 0b1000 else -4))
        if n.slide_flags & 0b0100:  # slide out down
            n.uslide_to = max(1, n.fret - 4)
        elif n.slide_flags & 0b1000:  # slide out up
            n.uslide_to = min(24, n.fret + 4)
        nxt_on_string[n.string] = n
    # sustains
    for n in notes:
        need = n.bend_pts or n.slide_to > 0 or n.uslide_to > 0 or n.tremolo or n.vibrato
        n.sustain = n.dur if (n.dur >= sustain_min or need) else 0.0
    return notes, beats, bar_times


def group_chords(notes):
    """Group simultaneous notes into chords; returns (singles, chords, templates)."""
    from collections import defaultdict
    by_time = defaultdict(list)
    for n in notes:
        by_time[round(n.time, 4)].append(n)
    singles, chords, templates, tindex = [], [], [], {}
    for t in sorted(by_time):
        grp = sorted(by_time[t], key=lambda n: n.string)
        if len(grp) == 1:
            singles.append(grp[0])
            continue
        frets = [-1] * 6
        for n in grp:
            frets[n.string] = n.fret
        key = tuple(frets)
        if key not in tindex:
            tindex[key] = len(templates)
            templates.append(key)
        chords.append((grp[0].time, tindex[key], grp))
    return singles, chords, templates


def chord_fingers(frets):
    """Crude but valid fingering heuristic for chord templates."""
    fingers = [-1] * 6
    fretted = sorted({f for f in frets if f > 0})
    if not fretted:
        return fingers
    base = fretted[0]
    barre = sum(1 for f in frets if f == base) > 1 and base == min(fretted)
    for s, f in enumerate(frets):
        if f <= 0:
            continue
        idx = f - base + 1
        if barre and f == base:
            fingers[s] = 1
        else:
            fingers[s] = max(1, min(4, idx))
    return fingers


def build_anchors(items, last_time):
    """items: list of (time, min_fret, max_fret). Greedy 4-fret window anchors."""
    anchors, cur = [], None
    for t, lo, hi in items:
        if lo <= 0:
            lo = hi if hi > 0 else 1
        lo = max(1, lo)
        width = max(4, (hi - lo + 1) if hi > 0 else 4)
        if cur is None or not (cur[1] <= lo and (hi <= cur[1] + cur[2] - 1 or hi <= 0)):
            cur = [t, lo, width]
            anchors.append(cur)
        elif width > cur[2]:
            cur[2] = width
    return anchors


def map_section(text):
    t = (text or "").strip().lower()
    for rx, name in RS_SECTION_MAP:
        if re.search(rx, t):
            return name
    return "riff"


def xml_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def note_attrs(n, in_chord=False):
    a = {
        "time": f3(n.time), "linkNext": int(n.link_next), "accent": int(n.accent),
        "bend": (f"{n.max_bend:g}" if n.max_bend else "0"), "fret": n.fret,
        "hammerOn": int(n.hammer), "harmonic": int(n.harmonic), "hopo": int(n.hammer or n.pull),
        "ignore": 0, "leftHand": -1, "mute": int(n.mute), "palmMute": int(n.palm),
        "pluck": -1, "pullOff": int(n.pull), "slap": -1,
        "slideTo": n.slide_to, "string": n.string, "sustain": f3(n.sustain),
        "tremolo": int(n.tremolo), "harmonicPinch": int(n.pinch), "pickDirection": 0,
        "rightHand": -1, "slideUnpitchTo": n.uslide_to, "tap": 0, "vibrato": 80 if n.vibrato else 0,
    }
    return " ".join(f'{k}="{v}"' for k, v in a.items())


def bend_values_xml(n, indent):
    if not n.bend_pts:
        return ""
    sus = n.sustain or n.dur
    pts = []
    for off, val in n.bend_pts:
        if val <= 0:
            continue
        pts.append((n.time + off * sus, val))
    if not pts:
        return ""
    s = f'{indent}<bendValues count="{len(pts)}">\n'
    for t, v in pts:
        s += f'{indent}  <bendValue time="{f3(t)}" step="{v:g}"/>\n'
    s += f"{indent}</bendValues>\n"
    return s


def render_notes(singles, indent="        "):
    out = []
    for n in singles:
        bv = bend_values_xml(n, indent + "  ")
        if bv:
            out.append(f"{indent}<note {note_attrs(n)}>\n{bv}{indent}</note>")
        else:
            out.append(f"{indent}<note {note_attrs(n)}/>")
    return "\n".join(out)


def render_chords(chords, indent="        "):
    out = []
    for t, cid, grp in chords:
        palm = int(all(n.palm for n in grp))
        accent = int(any(n.accent for n in grp))
        mute = int(all(n.mute for n in grp))
        link = int(any(n.link_next for n in grp))
        head = (f'{indent}<chord time="{f3(t)}" linkNext="{link}" accent="{accent}" chordId="{cid}" '
                f'fretHandMute="{mute}" highDensity="0" ignore="0" palmMute="{palm}" hopo="0" strum="down">')
        body = ""
        for n in grp:
            bv = bend_values_xml(n, indent + "    ")
            if bv:
                body += f"{indent}  <chordNote {note_attrs(n, True)}>\n{bv}{indent}  </chordNote>\n"
            else:
                body += f"{indent}  <chordNote {note_attrs(n, True)}/>\n"
        out.append(head + "\n" + body + f"{indent}</chord>")
    return "\n".join(out)


def make_arrangement(gp, ti, args):
    leadin = args.leadin
    bpm_scale = getattr(args, "bpm_scale", 1.0)
    notes, beats, bar_times = convert_track(gp, ti, leadin, bpm_scale=bpm_scale)
    singles, chords, templates = group_chords(notes)
    body_end = bar_times[-1][0] + bar_times[-1][1] * 60.0 / bar_times[-1][2]
    # Use audio duration if available so SongLength always spans the full audio file.
    # Without this, GP files with fewer bars than the audio length cause notes to
    # cut off early in Rocksmith while the audio keeps playing.
    audio_dur = getattr(args, "audio_duration", None)
    if audio_dur and audio_dur > body_end + 3.0:
        song_len = audio_dur + 1.0   # tiny buffer so Rocksmith doesn't clip the tail
    else:
        song_len = body_end + 3.0

    # ebeats
    ebeats = []
    for i, (t0, quarters, bpm) in enumerate(bar_times):
        nbeats = max(1, round(quarters))
        spb = (quarters * 60.0 / bpm) / nbeats
        for b in range(nbeats):
            ebeats.append((t0 + b * spb, i + 1 if b == 0 else -1))
    # CST's SNG serializer builds a beat-time lookup array and crashes with
    # IndexOutOfRangeException if any phraseIteration/section time exceeds the
    # last ebeat.  body_end is one beat past the last ebeat (end of last bar),
    # so clamp everything to last_ebeat_t — exactly what CST does in its own
    # LoadFromFolder path.
    last_ebeat_t = ebeats[-1][0] if ebeats else body_end

    # sections & phrases
    sec_starts = []
    for i, mb in enumerate(gp.masterbars):
        sec = mb.find("Section")
        if sec is not None:
            sec_starts.append((bar_times[i][0], map_section(sec.findtext("Text"))))
    if not sec_starts or sec_starts[0][0] > leadin:
        sec_starts.insert(0, (leadin, "intro"))

    # If the GP file has no section markers (only the auto-inserted intro),
    # generate sections every ~16 bars so Riff Repeater works properly.
    if len(sec_starts) == 1 and sec_starts[0][1] == "intro":
        seg_names = ["intro", "verse", "chorus", "verse", "chorus",
                     "bridge", "chorus", "outro"]
        seg_idx = 0
        interval = max(8, len(bar_times) // max(1, len(seg_names)))
        sec_starts = []
        for bar_i, (bt, _q, _bpm) in enumerate(bar_times):
            if bar_i == 0:
                sec_starts.append((bt, "intro"))
                seg_idx = 1
            elif bar_i > 0 and bar_i % interval == 0 and seg_idx < len(seg_names):
                sec_starts.append((bt, seg_names[seg_idx]))
                seg_idx += 1
    counts = {}
    sections = []
    for t, name in sec_starts:
        counts[name] = counts.get(name, 0) + 1
        sections.append((name, counts[name], t))
    sections.append(("noguitar", 1, min(body_end, last_ebeat_t)))

    phrase_names, phrase_idx = [], {}
    def pid(name):
        if name not in phrase_idx:
            phrase_idx[name] = len(phrase_names)
            phrase_names.append(name)
        return phrase_idx[name]
    iters = [(leadin if not sections else min(leadin, sections[0][2]), pid("COUNT"))]
    for name, num, t in sections[:-1]:
        iters.append((t, pid(name)))
    iters.append((min(body_end, last_ebeat_t), pid("END")))

    # anchors from combined note/chord stream
    stream = []
    for n in singles:
        stream.append((n.time, n.fret if n.fret > 0 else 0, n.fret))
    for t, cid, grp in chords:
        fr = [n.fret for n in grp if n.fret > 0]
        stream.append((t, min(fr) if fr else 0, max(fr) if fr else 0))
    stream.sort()
    anchors = build_anchors(stream, body_end)

    # handshapes: one per chord, end at min(next event, chord end)
    handshapes = []
    for idx, (t, cid, grp) in enumerate(chords):
        end = t + max(n.sustain or n.dur * 0.8 for n in grp)
        handshapes.append((cid, t, min(end, song_len)))

    tun, _ = gp.effective_tuning(ti)
    is_bass = args.arr == "Bass"
    ref = STD_BASS if is_bass else STD_TUNING
    offs = [tun[i] - ref[i] for i in range(min(len(ref), len(tun)))]
    while len(offs) < 6:
        offs.append(0)
    avg_bpm = sum(b for _, _, b in bar_times) / len(bar_times)

    used = lambda f: int(any(f(n) for n in notes))
    title = args.title or gp.title or "Unknown"
    artist = args.artist or gp.artist or "Unknown"

    L = []
    A = L.append
    A('<?xml version="1.0" encoding="UTF-8"?>')
    A('<song version="8">')
    A(f"  <title>{xml_escape(title)}</title>")
    A(f"  <arrangement>{args.arr}</arrangement>")
    A("  <part>1</part>")
    A(f"  <offset>{f3(-leadin)}</offset>")
    A("  <centOffset>0</centOffset>")
    A(f"  <songLength>{f3(song_len)}</songLength>")
    A(f"  <startBeat>{f3(leadin)}</startBeat>")
    A(f"  <averageTempo>{avg_bpm:.3f}</averageTempo>")
    A("  <tuning " + " ".join(f'string{i}="{offs[i]}"' for i in range(6)) + "/>")
    A(f"  <capo>{gp.capo(ti)}</capo>")
    A(f"  <artistName>{xml_escape(artist)}</artistName>")
    A(f"  <albumName>{xml_escape(args.album or gp.album)}</albumName>")
    A(f"  <albumYear>{args.year}</albumYear>")
    A("  <crowdSpeed>1</crowdSpeed>")
    A('  <arrangementProperties represent="1" bonusArr="0" standardTuning="%d" '
      'nonStandardChords="0" barreChords="0" powerChords="0" dropDPower="0" '
      'openChords="1" fingerPicking="0" pickDirection="0" doubleStops="0" '
      'palmMutes="%d" harmonics="%d" pinchHarmonics="%d" hopo="%d" tremolo="%d" '
      'slides="%d" unpitchedSlides="%d" bends="%d" tapping="0" vibrato="%d" '
      'fretHandMutes="0" slapPop="0" twoFingerPicking="0" fifthsAndOctaves="0" '
      'syncopation="0" bassPick="0" sustain="1" pathLead="%d" pathRhythm="%d" '
      'pathBass="%d" routeMask="%d"/>' % (
          int(all(o == 0 for o in offs)),
          used(lambda n: n.palm), used(lambda n: n.harmonic), used(lambda n: n.pinch),
          used(lambda n: n.hammer or n.pull), used(lambda n: n.tremolo),
          used(lambda n: n.slide_to > 0), used(lambda n: n.uslide_to > 0),
          used(lambda n: n.max_bend > 0), used(lambda n: n.vibrato),
          int(args.arr == "Lead"), int(args.arr == "Rhythm"),
          int(args.arr == "Bass"),
          {"Lead": 1, "Rhythm": 2, "Bass": 4}.get(args.arr, 1)))
    A(f"  <lastConversionDateTime>{datetime.now().strftime('%m-%d-%y %H:%M')}</lastConversionDateTime>")
    A(f'  <phrases count="{len(phrase_names)}">')
    for nm in phrase_names:
        # The arrangement below contains a SINGLE level (<levels count="1">, the
        # difficulty="0" full transcription). maxDifficulty is the highest level
        # index a phrase uses, so with one level it MUST be 0 for every phrase.
        # Stamping "5" while only level 0 exists makes the toolkit's SNG builder
        # index a non-existent difficulty and crash with
        # "Index was outside the bounds of the array."
        # (When DDC successfully expands the chart to multiple levels it rewrites
        # these maxDifficulty values itself, so 0 is the correct safe baseline.)
        _md = "0"
        A(f'    <phrase disparity="0" ignore="0" maxDifficulty="{_md}" name="{nm}" solo="0"/>')
    A("  </phrases>")
    A(f'  <phraseIterations count="{len(iters)}">')
    for t, p in iters:
        A(f'    <phraseIteration time="{f3(t)}" phraseId="{p}" variation=""/>')
    A("  </phraseIterations>")
    A('  <newLinkedDiffs count="0"/>')
    A('  <linkedDiffs count="0"/>')
    A('  <phraseProperties count="0"/>')
    A(f'  <chordTemplates count="{len(templates)}">')
    for frets in templates:
        fingers = chord_fingers(list(frets))
        attrs = " ".join(f'fret{i}="{frets[i]}" finger{i}="{fingers[i]}"' for i in range(6))
        A(f'    <chordTemplate chordName="" displayName="" {attrs}/>')
    A("  </chordTemplates>")
    A('  <fretHandMuteTemplates count="0"/>')
    A(f'  <ebeats count="{len(ebeats)}">')
    for t, m in ebeats:
        A(f'    <ebeat time="{f3(t)}" measure="{m}"/>')
    A("  </ebeats>")
    A(f'  <sections count="{len(sections)}">')
    for name, num, t in sections:
        A(f'    <section name="{name}" number="{num}" startTime="{f3(t)}"/>')
    A("  </sections>")
    A('  <events count="0"/>')

    def level_block(tag, diff):
        B = []
        B.append(f'  <{tag} difficulty="{diff}">')
        B.append(f'    <notes count="{len(singles)}">')
        if singles:
            B.append(render_notes(singles, "      "))
        B.append("    </notes>")
        B.append(f'    <chords count="{len(chords)}">')
        if chords:
            B.append(render_chords(chords, "      "))
        B.append("    </chords>")
        B.append(f'    <anchors count="{len(anchors)}">')
        for t, fret, width in anchors:
            B.append(f'      <anchor time="{f3(t)}" fret="{fret}" width="{f3(width)}"/>')
        B.append("    </anchors>")
        B.append(f'    <handShapes count="{len(handshapes)}">')
        for cid, t0, t1 in handshapes:
            B.append(f'      <handShape chordId="{cid}" endTime="{f3(t1)}" startTime="{f3(t0)}"/>')
        B.append("    </handShapes>")
        B.append(f"  </{tag}>")
        return "\n".join(B)

    A(level_block("transcriptionTrack", -1))
    A('  <levels count="1">')
    A(level_block("level", 0).replace("  <level", "    <level").replace("\n  ", "\n    ").replace("  </level", "    </level"))
    A("  </levels>")
    A("</song>")
    return "\n".join(L), len(notes), len(singles), len(chords)


# ----------------------------------------------------------------- vocals
def norm_header(s):
    s = re.sub(r"[^a-z]", "", s.lower())
    return s.rstrip("0123456789")


HEADER_EQUIV = {"prechorus": {"prechorus"}, "chorus": {"chorus", "postchorus"},
                "verse": {"verse", "preverse"}, "bridge": {"bridge", "breakdown", "interlude"},
                "intro": {"intro"}, "outro": {"outro", "noguitar"},
                "break": {"breakdown", "bridge", "interlude"},
                "breakdown": {"breakdown", "bridge"}, "hook": {"hook", "chorus"}}


def parse_lyric_blocks(path):
    """Plain text with [Verse 1]/[Chorus]... headers -> [(norm_header, [lines])]."""
    blocks, cur = [], None
    for raw in open(path, encoding="utf-8", errors="replace"):
        line = re.sub(r"\(https?://\S+\)", "", raw)        # markdown links
        line = re.sub(r"https?://\S+", "", line).strip()
        m = re.match(r"^\[([^\]]+)\]\s*(.*)$", line)
        if m and not re.search(r"\d:\d\d", m.group(1)):    # a [Header], not an LRC stamp
            cur = (norm_header(m.group(1)), [])
            blocks.append(cur)
            line = m.group(2).strip()
        line = line.strip("[]").strip()
        if not line or "you might also like" in line.lower():
            continue
        if cur is None:
            cur = ("verse", [])
            blocks.append(cur)
        cur[1].append(line)
    return [b for b in blocks if b[1]]


def lyrics_txt_to_vocals(path, gp, leadin):
    """Match [Section] blocks to GP section markers; spread lines across each."""
    bar_times, body_end = gp.bar_times(leadin)
    secs = []
    for i, mb in enumerate(gp.masterbars):
        s = mb.find("Section")
        if s is not None:
            secs.append((bar_times[i][0], map_section(s.findtext("Text"))))
    ends = [secs[j + 1][0] if j + 1 < len(secs) else body_end for j in range(len(secs))]
    blocks = parse_lyric_blocks(path)

    def match(b, s):
        return 1 if s in HEADER_EQUIV.get(b, {b}) else 0

    m, n = len(blocks), len(secs)
    # f[i][j]: best #matches placing first i blocks within first j sections,
    # assignments non-decreasing (blocks may share a section)
    f = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        for j in range(n + 1):
            if j:
                f[i][j] = f[i][j - 1]
            if i and j:
                f[i][j] = max(f[i][j], f[i - 1][j] + match(blocks[i - 1][0], secs[j - 1][1]))
    assign, i, j = [0] * m, m, n
    while i > 0:
        sc = match(blocks[i - 1][0], secs[j - 1][1]) if j else 0
        if j and f[i][j] == f[i - 1][j] + sc and (sc or j == 1 or f[i][j] != f[i][j - 1]):
            assign[i - 1] = j - 1
            i -= 1
        else:
            j -= 1

    vocals = []
    from collections import defaultdict
    per_sec = defaultdict(list)
    for bi, si in enumerate(assign):
        per_sec[si].append(bi)
    for si, bis in per_sec.items():
        t0, t1 = secs[si][0], ends[si]
        total = sum(len(blocks[b][1]) for b in bis)
        per_line = (t1 - t0) * 0.95 / max(1, total)
        line_no = 0
        for b in bis:
            for line in blocks[b][1]:
                lt = t0 + line_no * per_line
                line_no += 1
                words = line.split()
                step = (per_line * 0.9) / max(1, len(words))
                for w, word in enumerate(words):
                    word = re.sub(r'[<>&"]', "", word)[:32]
                    mark = word + ("+" if w == len(words) - 1 else "")
                    vocals.append((lt + w * step, min(max(step * 0.9, 0.2), 2.0), mark))
    vocals.sort()
    out = ['<?xml version="1.0" encoding="UTF-8"?>', f'<vocals count="{len(vocals)}">']
    for t, ln, w in vocals:
        out.append(f'  <vocal time="{f3(t)}" note="254" length="{f3(ln)}" lyric="{xml_escape(w)}"/>')
    out.append("</vocals>")
    return "\n".join(out)


def warp_time(t, anchors):
    """Charter-style piecewise-linear time warp. `anchors` is a list of
    (orig_t, new_t) pairs; times between anchors are linearly interpolated, and
    times outside the range are shifted by the nearest anchor's delta. Used to
    re-time lyrics after the user drags individual cues without disturbing the
    rest."""
    if not anchors:
        return t
    pts = sorted((float(o), float(n)) for o, n in anchors)
    if t <= pts[0][0]:
        return t + (pts[0][1] - pts[0][0])
    if t >= pts[-1][0]:
        return t + (pts[-1][1] - pts[-1][0])
    for i in range(1, len(pts)):
        o0, n0 = pts[i - 1]
        o1, n1 = pts[i]
        if o0 <= t <= o1:
            if o1 == o0:
                return n1
            f = (t - o0) / (o1 - o0)
            return n0 + (n1 - n0) * f
    return t


def lrc_to_vocals(path, leadin=0.0, lrc_offset=0.0, warp_anchors=None):
    """Synced .lrc -> Rocksmith vocals XML (words spread evenly across each line).

    .lrc timestamps are relative to the ORIGINAL (un-padded) audio, i.e. t=0 is
    the first sample of the song. Every other chart element (notes, sections,
    offset/startBeat) is relative to the PADDED audio, where bar 1 / the song
    body starts at t=leadin. So `leadin` must be added here too, or every lyric
    cue fires `leadin` seconds early relative to the music.
    lrc_offset shifts all lyrics earlier (negative) or later (positive)."""
    lines = []
    rx = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)")
    for raw in open(path, encoding="utf-8", errors="replace"):
        m = rx.match(raw.strip())
        if not m:
            continue
        t0 = int(m.group(1)) * 60 + float(m.group(2))
        if warp_anchors:
            t0 = warp_time(t0, warp_anchors)
        t = max(0.0, t0 + leadin + lrc_offset)
        txt = m.group(3).strip()
        lines.append((t, txt))
    lines.sort()
    vocals = []
    for i, (t, txt) in enumerate(lines):
        if not txt:
            continue
        end = lines[i + 1][0] if i + 1 < len(lines) else t + 4.0
        words = txt.split()
        span = max(0.5, (end - t) - 0.2)
        # Cap word spacing: if there's a long gap to the next line, don't smear the
        # words across the whole gap (that makes each word hang on screen forever).
        step = min(span / max(1, len(words)), 0.5)
        for w, word in enumerate(words):
            mark = word + ("+" if w == len(words) - 1 else "")
            vocals.append((t + w * step, min(step * 0.9, 1.4), mark))
    out = ['<?xml version="1.0" encoding="UTF-8"?>', f'<vocals count="{len(vocals)}">']
    for t, ln, w in vocals:
        out.append(f'  <vocal time="{f3(t)}" note="254" length="{f3(ln)}" lyric="{xml_escape(w)}"/>')
    out.append("</vocals>")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gpfile", nargs="?")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--track", type=int, default=None)
    ap.add_argument("--all", action="store_true",
                    help="auto-export every guitar/bass track as Lead/Rhythm/Bass")
    ap.add_argument("--arr", default="Lead", choices=["Lead", "Rhythm", "Bass", "Combo"])
    ap.add_argument("--leadin", type=float, default=5.0,
                    help="seconds of silence before bar 1 (pad your audio to match)")
    ap.add_argument("--title", default="")
    ap.add_argument("--artist", default="")
    ap.add_argument("--album", default="")
    ap.add_argument("--year", default="")
    ap.add_argument("--lrc", default=None, help="synced .lrc lyrics -> vocals XML")
    ap.add_argument("--lyrics-txt", default=None,
                    help="plain lyrics with [Section] headers -> vocals XML timed via GP sections")
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args()

    if args.lrc and not args.gpfile:
        out = args.out or "vocals.xml"
        open(out, "w", encoding="utf-8").write(lrc_to_vocals(args.lrc, args.leadin))
        print(f"wrote {out}")
        return

    gp = GPSong(args.gpfile)
    if args.list or (args.track is None and not args.all):
        for i, nm in enumerate(gp.track_names()):
            kind = gp.track_kind(i) or "skip"
            print(f"  track {i}: {nm}  [{kind}]  (tuning {gp.tuning(i)})")
        if args.track is None and not args.all:
            print("\nre-run with --track N or --all")
            return

    base = (args.out or "arrangement.xml").rsplit(".", 1)[0]
    jobs = []  # (track_index, arr_name, outfile)
    if args.all:
        guitars = [i for i in range(len(gp.tracks)) if gp.track_kind(i) == "guitar"]
        basses = [i for i in range(len(gp.tracks)) if gp.track_kind(i) == "bass"]
        # more chords per note-event -> Rhythm; the other guitar -> Lead
        if len(guitars) >= 2:
            def chordiness(i):
                notes, _, _ = convert_track(gp, i, args.leadin)
                _, ch, _ = group_chords(notes)
                return len(ch) / max(1, len(notes))
            ranked = sorted(guitars, key=chordiness)
            jobs.append((ranked[0], "Lead", f"{base}_lead.xml"))
            jobs.append((ranked[-1], "Rhythm", f"{base}_rhythm.xml"))
            for extra in ranked[1:-1]:
                jobs.append((extra, "Lead", f"{base}_lead{extra}.xml"))
        elif guitars:
            jobs.append((guitars[0], "Lead", f"{base}_lead.xml"))
        if basses:
            jobs.append((basses[0], "Bass", f"{base}_bass.xml"))
    else:
        jobs.append((args.track, args.arr, args.out or "arrangement.xml"))

    for ti, arr, out in jobs:
        args.arr = arr
        xml, n_notes, n_single, n_chord = make_arrangement(gp, ti, args)
        open(out, "w", encoding="utf-8").write(xml)
        name = " ".join((gp.tracks[ti].findtext("Name") or "").split())
        print(f"wrote {out} [{arr} <- track {ti} '{name}']: "
              f"{n_notes} notes -> {n_single} singles + {n_chord} chords")

    if args.lyrics_txt:
        vout = f"{base}_vocals.xml"
        open(vout, "w", encoding="utf-8").write(
            lyrics_txt_to_vocals(args.lyrics_txt, gp, args.leadin))
        print(f"wrote {vout} (auto-timed from GP section markers)")
    elif args.lrc:
        vout = f"{base}_vocals.xml"
        open(vout, "w", encoding="utf-8").write(lrc_to_vocals(args.lrc, args.leadin))
        print(f"wrote {vout}")


def _auto_invocation():
    """Drag-and-drop / double-click support:
    - gp2rs.exe song.gp  (file dragged onto the exe) -> full --all conversion
    - gp2rs.exe          (double-clicked)            -> ask for the file path
    Outputs land next to the .gp file. A lyrics file named like the song is auto-used for LRC timestamps.
    """
    import sys as _sys
    argv = _sys.argv[1:]
    if not argv:
        try:
            path = input("Drag a .gp file here (or type path): ").strip().strip('"')
        except (EOFError, KeyboardInterrupt):
            return
    else:
        path = argv[0]
    if not path or not os.path.isfile(path):
        print(f"File not found: {path!r}")
        return
    import argparse as _ap
    p = _ap.ArgumentParser()
    p.add_argument("gp", nargs="?", default=path)
    p.add_argument("--all", action="store_true", default=True)
    args = p.parse_args([path, "--all"])
    main(args)


if __name__ == "__main__":
    _auto_invocation()
