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

# Note names for the root pitch of the low string (used by tuning_name()).
_PITCH_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


# Named 6-string alternate-tuning *shapes*, as semitone offsets from standard
# EADGBE (low to high). These are well-established, conventionally-fixed
# voicings (unlike e.g. "Open A" where different charts disagree on the exact
# strings), so they're safe to label confidently. Each shape can still sit at
# any overall transposition (e.g. "Open D" up a step is still recognizably
# the Open D *shape*, just like "Drop D" up a step becomes "Drop E") — we
# detect the shape, then name the root off the lowest string actually heard.
_NAMED_SHAPES = [
    # (offsets relative to EADGBE, semitones from the LOW STRING up to the
    # tuning's actual named root, name template; {root} = that root's name).
    # The named root isn't always the low string — e.g. Open G's low string
    # rings D (a 4th below the G major chord's root) by slide-guitar
    # convention, so it needs its own offset rather than reusing the low
    # string's pitch directly like Standard/Drop tunings do.
    ([-2, 0, 0, 0, 0, -2], 0, "Double Drop {root}"),
    # Open D shape (D A D F# A D), root = low string. Transposed +2 this is
    # also Open E (E B E G# B E) — one entry covers both via the
    # transposition search below; no separate "Open E" pattern needed.
    ([-2, 0, 0, -1, -2, -2], 0, "Open {root}"),
    # Open G shape (D G D G B D): low string is D, root is G, a 4th above.
    ([-2, -2, 0, 0, 0, -2], 5, "Open {root}"),
    ([-2, 0, 0, 0, -2, -2], 0, "DADGAD"),
]


def tuning_name(tuning, is_bass=False):
    """Map an absolute MIDI tuning list to a human-readable tuning name for
    upload purposes (e.g. 'E Standard', 'Eb Standard', 'Drop D', 'Drop C#',
    'Double Drop D', 'Open D', 'Open G', 'Open E', 'DADGAD').
    Falls back to 'Custom' when it doesn't match a known shape.

    `tuning` is the per-string MIDI list, low string first (as GP/RS store it).
    Note: only exact 6-string sets are checked against the alternate-tuning
    shapes below (7/8-string extended-range guitars fall back to Custom for
    those, same as before — they're still correctly detected as Standard/Drop
    if every string except the low one(s) matches).
    """
    if not tuning:
        return "Custom"
    ref = STD_BASS if is_bass else STD_TUNING
    n = min(len(ref), len(tuning))
    if n < (4 if is_bass else 6):
        return "Custom"
    offs = [tuning[i] - ref[i] for i in range(n)]

    # All strings shifted by the same amount -> "<root> Standard".
    if len(set(offs)) == 1:
        semis = offs[0]
        if semis == 0:
            return "E Standard"
        root_midi = (STD_BASS[0] if is_bass else STD_TUNING[0]) + semis
        root = _PITCH_NAMES[root_midi % 12]
        return f"{root} Standard"

    # Drop tunings: low string is 2 semitones below the rest, which are uniform.
    upper = offs[1:]
    if len(set(upper)) == 1 and offs[0] == upper[0] - 2:
        low_midi = (STD_BASS[0] if is_bass else STD_TUNING[0]) + offs[0]
        root = _PITCH_NAMES[low_midi % 12]
        return f"Drop {root}"

    # Named alternate-tuning shapes (guitar only — these are 6-string voicings).
    if not is_bass and n == 6 and len(tuning) == 6:
        for i in range(-3, 4):  # allow the whole shape to sit anywhere nearby
            shifted = [o - i for o in offs]
            for shape, root_offset, template in _NAMED_SHAPES:
                if shifted == shape:
                    root_midi = STD_TUNING[0] + offs[0] + root_offset
                    root = _PITCH_NAMES[root_midi % 12]
                    return template.format(root=root)

    return "Custom"

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


# Guitar Pro's gpif format stores explicit left-hand fingering (when the tab
# author set it) as a "LeftFingering" property with text matching PyGuitarPro's
# Fingering enum names. Map to Rocksmith chordTemplate finger numbers: 1-4 are
# unambiguous (index/middle/ring/pinky); thumb's exact numeric encoding isn't
# independently confirmed from a real Rocksmith chordTemplate sample, so 0 is
# a best-effort choice — chord_fingers() logs the first time it's used so it's
# easy to spot and verify in-game if it looks wrong.
_GP_FINGER_MAP = {"Open": -1, "NoFinger": -1, "Thumb": 0,
                   "Index": 1, "Middle": 2, "Annular": 3, "Little": 4}


class GPNote:
    __slots__ = ("string", "fret", "props", "tie_dest", "tie_orig", "accent", "finger")

    def __init__(self, el):
        self.props = Prop(el.find("Properties"))
        self.string = int(self.props.get("String", 0))
        self.fret = int(self.props.get("Fret", 0))
        tie = el.find("Tie")
        self.tie_dest = tie is not None and tie.get("destination") == "true"
        self.tie_orig = tie is not None and tie.get("origin") == "true"
        acc = el.findtext("Accent")
        self.accent = bool(acc and int(acc) & 0x08 or el.find("Accent") is not None)
        self.finger = _GP_FINGER_MAP.get(self.props.get("LeftFingering"))


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
        self.finger = gpn.finger  # explicit GP fingering, if the tab author set it
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


def convert_track(gp, ti, leadin, sustain_min=None, bpm_scale=1.0):
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
    # CF charting standard: each sustain must leave at least a 1/32-note gap before
    # the next note on the SAME string, so sustains never visually run into the
    # following note. Trim (never extend) to honour that gap.
    def _bpm_at(t):
        bpm = 120.0
        for bt in bar_times:
            if bt[0] <= t:
                bpm = bt[2] or bpm
            else:
                break
        return bpm or 120.0

    def _bar_dur_at(t):
        """Length of a full measure (in seconds) at time t, e.g. 4 beats at the
        prevailing BPM/time-signature for a 4/4 bar — used as the sustain-trim
        threshold so it's correct at any tempo, not a single flat cutoff."""
        quarters, bpm = 4.0, 120.0
        for bt in bar_times:
            if bt[0] <= t:
                quarters, bpm = bt[1] or quarters, bt[2] or bpm
            else:
                break
        return (quarters * 60.0 / bpm) if bpm else 2.0

    # sustains: a note's sustain is only worth SHOWING if it lasts at least one
    # full bar (the CDLC community standard — shorter sustains just clutter
    # the highway) — UNLESS the note has a technique that needs a visible
    # sustain regardless of length (bend, slide, tremolo, vibrato).
    # `sustain_min`, if explicitly passed, overrides the bar-relative
    # threshold with a flat one (kept for callers/tests that want that).
    for n in notes:
        need = n.bend_pts or n.slide_to > 0 or n.uslide_to > 0 or n.tremolo or n.vibrato
        thresh = sustain_min if sustain_min is not None else _bar_dur_at(n.time)
        n.sustain = n.dur if (n.dur >= thresh or need) else 0.0

    by_string = {}
    for n in notes:
        by_string.setdefault(n.string, []).append(n)
    for ns in by_string.values():
        ns.sort(key=lambda x: x.time)
        for i in range(len(ns)):
            cur = ns[i]
            nxt = ns[i + 1] if i + 1 < len(ns) else None
            if cur.link_next:
                # linkNext needs a real, adjacent next note on this string to
                # link into; otherwise DLC Builder flags it. Connect the sustain
                # to it (no 1/32 gap) and align the slide target to its fret.
                if nxt is None or (nxt.time - (cur.time + cur.dur)) > 2.0:
                    cur.link_next = False
                    if cur.slide_to > 0:
                        cur.uslide_to = cur.slide_to
                        cur.slide_to = -1
                    continue
                cur.sustain = max(cur.sustain, round(nxt.time - cur.time, 3))
                if cur.slide_to > 0 and nxt.fret > 0:
                    cur.slide_to = nxt.fret
                continue
            # normal notes: keep at least a 1/32-note gap before the next note
            if cur.sustain > 0 and nxt is not None:
                gap = (60.0 / _bpm_at(cur.time)) / 8.0
                max_end = nxt.time - gap
                if cur.time + cur.sustain > max_end:
                    cur.sustain = max(0.0, max_end - cur.time)
    return notes, beats, bar_times


def group_chords(notes):
    """Group simultaneous notes into chords; returns (singles, chords, templates,
    finger_hints). finger_hints is a list parallel to templates: a 6-tuple of
    explicit GP-authored fingers (-1/None where the tab didn't specify one),
    used to override the chord_fingers() heuristic when available."""
    from collections import defaultdict
    by_time = defaultdict(list)
    for n in notes:
        by_time[round(n.time, 4)].append(n)
    singles, chords, templates, finger_hints, tindex = [], [], [], [], {}
    for t in sorted(by_time):
        grp = sorted(by_time[t], key=lambda n: n.string)
        if len(grp) == 1:
            singles.append(grp[0])
            continue
        frets = [-1] * 6
        hints = [None] * 6
        for n in grp:
            frets[n.string] = n.fret
            hints[n.string] = n.finger
        key = tuple(frets)
        if key not in tindex:
            tindex[key] = len(templates)
            templates.append(key)
            finger_hints.append(hints)
        else:
            # Same shape seen again — fill in any hint the first occurrence
            # was missing (a tab author may only finger some instances).
            existing = finger_hints[tindex[key]]
            for i in range(6):
                if existing[i] is None and hints[i] is not None:
                    existing[i] = hints[i]
        chords.append((grp[0].time, tindex[key], grp))
    return singles, chords, templates, finger_hints


def chord_fingers(frets, hints=None, log=None):
    """Fingering for chord templates.

    `hints`, if given, is a 6-tuple of explicit fingers the GP tab author set
    per string (-1/None where unset) — those are used directly. Any string
    without a hint falls back to the heuristic:

    A real barre (finger 1 on several strings at the base fret) is only used
    when there are NO open strings — otherwise DLC Builder flags "barre over
    open strings". When open strings are present, every fretted note gets a
    DISTINCT finger (so finger 1 never spans an open string)."""
    fingers = [-1] * 6
    pos = [(s, f) for s, f in enumerate(frets) if f > 0]
    if not pos:
        return fingers
    has_open = any(f == 0 for f in frets)
    base = min(f for _s, f in pos)
    same_base = sum(1 for _s, f in pos if f == base)

    if same_base > 1 and not has_open:
        # genuine barre across the base fret
        for s, f in pos:
            fingers[s] = 1 if f == base else max(1, min(4, f - base + 1))
    else:
        # distinct fingers, low fret -> high; bump on collision so no finger repeats
        used = set()
        for s, f in sorted(pos, key=lambda sf: (sf[1], sf[0])):
            fng = max(1, min(4, f - base + 1))
            while fng in used and fng < 4:
                fng += 1
            used.add(fng)
            fingers[s] = fng

    # Overlay any explicit fingering the GP tab author actually set — only on
    # strings that are really fretted here, never guessed at.
    if hints:
        for s, f in pos:
            h = hints[s] if s < len(hints) else None
            if h is not None and h >= 0:
                fingers[s] = h
                if h == 0 and log:
                    log("  [chord] Using GP-authored thumb fingering on a chord "
                        "— RS Studio's best guess is finger value 0 for thumb; "
                        "double-check it shows correctly in-game/DLC Builder.")
    return fingers


def build_anchors(items, last_time):
    """items: list of (time, min_fret, max_fret).

    Returns [[time, fret, width], ...] fret-hand positions. Instead of starting
    a fresh anchor at every note that leaves the window (which produces jittery,
    constantly-moving hands), this segments the note stream: it extends a single
    anchor forward over as many consecutive notes as fit inside a 4-fret span,
    then anchors that whole run on its lowest fret (where the index finger sits).
    Open-string-only events never force a hand move. The result is far closer to
    how a player actually anchors, which is what CF 'proper FHP' expects."""
    # Normalise; mark open-only events (no fretted note) with lo=None.
    evs = []
    for t, lo, hi in items:
        if hi is None or hi <= 0:
            evs.append((t, None, None))
        else:
            lo = max(1, lo if (lo and lo > 0) else hi)
            evs.append((t, lo, max(hi, lo)))

    anchors = []
    i, n = 0, len(evs)
    while i < n:
        # leading open-only events ride the previous anchor (or a default first)
        if evs[i][1] is None:
            if not anchors:
                anchors.append([evs[i][0], 1, 4])
            i += 1
            continue
        start_t, seg_lo, seg_hi = evs[i][0], evs[i][1], evs[i][2]
        j = i + 1
        while j < n:
            lo2, hi2 = evs[j][1], evs[j][2]
            if lo2 is None:          # open notes don't break a hand position
                j += 1
                continue
            nlo, nhi = min(seg_lo, lo2), max(seg_hi, hi2)
            if nhi - nlo + 1 <= 4:   # still fits one 4-fret hand span
                seg_lo, seg_hi = nlo, nhi
                j += 1
            else:
                break
        width = max(4, seg_hi - seg_lo + 1)
        anchors.append([start_t, seg_lo, width])
        i = j
    if not anchors:
        anchors.append([0.0, 1, 4])
    return anchors


def detect_arpeggio_handshapes(singles, templates, finger_hints, anchor_ts, song_len,
                                max_gap=0.5, min_notes=3, max_span=3):
    """Detect a chord shape played one string at a time (a broken/rolled
    chord - e.g. a triad picked note-by-note instead of strummed together)
    and add a handShape spanning the run, WITHOUT changing how the notes
    themselves are written. This matches how official Rocksmith DLC
    represents arpeggios: the individual notes stay plain <note> elements
    (correct, since they really are played one at a time) - only the
    on-screen chord-box overlay needs a handShape pointing at a chordTemplate
    that matches the shape.

    A run of consecutive singles (sorted by time) qualifies when: each note
    lands on a string not already used earlier in the run (a real arpeggio
    moves across strings — it doesn't repeat one, which is what separates
    this from an ordinary scale/melodic run), consecutive notes are close
    together in time (<= max_gap seconds), there are at least `min_notes` of
    them, and the resulting fret shape is tight enough to be a real chord
    voicing (span <= max_span frets, excluding open strings).

    Reuses the same chordTemplate slot if this exact shape already exists
    elsewhere in the song (e.g. it's also played as a real simultaneous
    chord somewhere), otherwise registers a new one — mutates `templates`
    and `finger_hints` in place, same convention as group_chords().
    Returns a list of (chordId, startTime, endTime) handShape tuples.
    """
    import bisect as _bis
    tindex = {tuple(f): i for i, f in enumerate(templates)}
    new_handshapes = []
    ordered = sorted(singles, key=lambda nn: nn.time)
    i, n = 0, len(ordered)
    while i < n:
        run = [ordered[i]]
        used_strings = {ordered[i].string}
        j = i + 1
        while j < n:
            gap = ordered[j].time - run[-1].time
            if gap <= 0 or gap > max_gap:
                break
            if ordered[j].string in used_strings:
                break
            run.append(ordered[j])
            used_strings.add(ordered[j].string)
            j += 1
        if len(run) >= min_notes:
            fretted = [r.fret for r in run if r.fret > 0]
            if len(fretted) >= 2 and (max(fretted) - min(fretted)) <= max_span:
                frets = [-1] * 6
                for r in run:
                    frets[r.string] = r.fret
                key = tuple(frets)
                if key not in tindex:
                    tindex[key] = len(templates)
                    templates.append(key)
                    hints = [None] * 6
                    for r in run:
                        hints[r.string] = getattr(r, "finger", None)
                    finger_hints.append(hints)
                cid = tindex[key]
                t0 = run[0].time
                last = run[-1]
                t1 = min(t0 + max(last.sustain or last.dur * 0.8, 0.05), song_len)
                _i2 = _bis.bisect_right(anchor_ts, t0 + 1e-4)
                if _i2 < len(anchor_ts):
                    t1 = min(t1, anchor_ts[_i2])
                if t1 > t0:
                    new_handshapes.append((cid, t0, t1))
        i = j
    return new_handshapes


def map_section(text):
    t = (text or "").strip().lower()
    for rx, name in RS_SECTION_MAP:
        if re.search(rx, t):
            return name
    return "riff"


def xml_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def note_attrs(n, in_chord=False, mute_override=None):
    _mute = int(n.mute) if mute_override is None else int(mute_override)
    a = {
        "time": f3(n.time), "linkNext": int(n.link_next), "accent": int(n.accent),
        "bend": (f"{n.max_bend:g}" if n.max_bend else "0"), "fret": n.fret,
        "hammerOn": int(n.hammer), "harmonic": int(n.harmonic), "hopo": int(n.hammer or n.pull),
        "ignore": 0, "leftHand": -1, "mute": _mute, "palmMute": int(n.palm),
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
        chord_muted = all(n.mute for n in grp)
        mute = int(chord_muted)
        link = int(any(n.link_next for n in grp))
        head = (f'{indent}<chord time="{f3(t)}" linkNext="{link}" accent="{accent}" chordId="{cid}" '
                f'fretHandMute="{mute}" highDensity="0" ignore="0" palmMute="{palm}" hopo="0" strum="down">')
        body = ""
        # A single string must not be muted inside a non-muted chord
        # (DLC Builder: "muted string in non-muted chord").
        _mo = None if chord_muted else 0
        for n in grp:
            bv = bend_values_xml(n, indent + "    ")
            if bv:
                body += f"{indent}  <chordNote {note_attrs(n, True, _mo)}>\n{bv}{indent}  </chordNote>\n"
            else:
                body += f"{indent}  <chordNote {note_attrs(n, True, _mo)}/>\n"
        out.append(head + "\n" + body + f"{indent}</chord>")
    return "\n".join(out)


def apply_low_bass_fix(offs, is_bass, pitch):
    """Rocksmith low-bass-tuning workaround (matches DLC Builder's ApplyLowTuningFix):
    a bass tuned very low (TuningPitch > 230 and any string offset < -4) is raised
    an octave (+12 per string) with TuningPitch dropped to 220 Hz. Same sounding
    pitch, but the game detects it. Returns (offs, pitch)."""
    if is_bass and pitch > 230.0 and any(o < -4 for o in offs):
        offs = [o + 12 for o in offs]
        pitch = 220.0
    return offs, pitch


def make_arrangement(gp, ti, args):
    leadin = args.leadin
    bpm_scale = getattr(args, "bpm_scale", 1.0)
    notes, beats, bar_times = convert_track(gp, ti, leadin, bpm_scale=bpm_scale)
    singles, chords, templates, finger_hints = group_chords(notes)
    body_end = bar_times[-1][0] + bar_times[-1][1] * 60.0 / bar_times[-1][2]
    # Use audio duration if available so SongLength always spans the full audio file.
    # Without this, GP files with fewer bars than the audio length cause notes to
    # cut off early in Rocksmith while the audio keeps playing.
    audio_dur = getattr(args, "audio_duration", None)
    if audio_dur and audio_dur > body_end + 3.0:
        song_len = audio_dur + 1.0   # tiny buffer so Rocksmith doesn't clip the tail
    else:
        song_len = body_end + 3.0
    # Make sure SongLength always covers the last note (+ its sustain) so DLC
    # Builder never flags "note after song end".
    _alln = list(singles) + [n for (_t, _c, grp) in chords for n in grp]
    last_note_end = max((n.time + (getattr(n, "sustain", 0.0) or 0.0) for n in _alln),
                        default=body_end)
    song_len = max(song_len, last_note_end + 0.5)

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
    # Terminal ebeat at the end of the last bar so the trailing no-guitar section
    # / END phrase sit AFTER the final note (otherwise notes in the last beat fall
    # "inside" the no-guitar section).
    if not ebeats or ebeats[-1][0] < body_end - 1e-6:
        ebeats.append((body_end, -1))
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

    # handshapes: one per chord. End at the chord's sustain, but never let a
    # handshape span an anchor change (DLC Builder flags "anchor inside
    # handshape"), so cap it at the next anchor after the chord.
    import bisect as _bis
    _anchor_ts = sorted(a[0] for a in anchors)
    handshapes = []
    for idx, (t, cid, grp) in enumerate(chords):
        end = min(t + max(n.sustain or n.dur * 0.8 for n in grp), song_len)
        _i = _bis.bisect_right(_anchor_ts, t + 1e-4)
        if _i < len(_anchor_ts):
            end = min(end, _anchor_ts[_i])
        if end <= t:
            end = t + 0.05
        handshapes.append((cid, t, end))

    # Arpeggiated chord shapes: notes picked one string at a time instead of
    # strummed together never get grouped by group_chords() (it only groups
    # exact-same-time notes), so a chord shape played that way was showing no
    # chord box at all in-game. Detect those runs and add matching handShapes
    # (templates/finger_hints are extended in place) without touching how the
    # underlying notes are written — they correctly stay individual notes.
    handshapes.extend(detect_arpeggio_handshapes(
        singles, templates, finger_hints, _anchor_ts, song_len))

    tun, _ = gp.effective_tuning(ti)
    is_bass = args.arr == "Bass"
    ref = STD_BASS if is_bass else STD_TUNING
    offs = [tun[i] - ref[i] for i in range(min(len(ref), len(tun)))]
    while len(offs) < 6:
        offs.append(0)
    offs, _lbp = apply_low_bass_fix(offs, is_bass, 440.0)
    avg_bpm = sum(b for _, _, b in bar_times) / len(bar_times)

    used = lambda f: int(any(f(n) for n in notes))

    # ── accurate chord/technique flags for <arrangementProperties> ──────────
    # CF "official looking" checks these match the actual chart content.
    def _pitch(nn):
        return tun[nn.string] + nn.fret if 0 <= nn.string < len(tun) else nn.fret
    has_barre = 0
    for _frets in templates:
        _ft = sorted({f for f in _frets if f > 0})
        if _ft and sum(1 for f in _frets if f == _ft[0]) >= 2:
            has_barre = 1
            break
    has_power = has_double = has_openchord = 0
    for _t, _cid, _grp in chords:
        _frets_only = [n.fret for n in _grp]
        if any(f == 0 for f in _frets_only) and any(f > 0 for f in _frets_only):
            has_openchord = 1
        _ps = sorted(_pitch(n) for n in _grp)
        if len(_grp) == 2:
            _iv = _ps[1] - _ps[0]
            if _iv in (7, 12):           # root + 5th / octave -> power chord
                has_power = 1
            else:
                has_double = 1
        elif len(_grp) == 3:
            _ivset = {_ps[1] - _ps[0], _ps[2] - _ps[1]}
            if _ivset <= {5, 7} or _ivset == {7, 5}:   # root-5th-octave shape
                has_power = 1
    has_fhmute = used(lambda n: getattr(n, "mute", False))

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
      'nonStandardChords="0" barreChords="%d" powerChords="%d" dropDPower="0" '
      'openChords="%d" fingerPicking="0" pickDirection="0" doubleStops="%d" '
      'palmMutes="%d" harmonics="%d" pinchHarmonics="%d" hopo="%d" tremolo="%d" '
      'slides="%d" unpitchedSlides="%d" bends="%d" tapping="0" vibrato="%d" '
      'fretHandMutes="%d" slapPop="0" twoFingerPicking="0" fifthsAndOctaves="0" '
      'syncopation="0" bassPick="0" sustain="1" pathLead="%d" pathRhythm="%d" '
      'pathBass="%d" routeMask="%d"/>' % (
          int(all(o == 0 for o in offs)),
          has_barre, has_power, has_openchord, has_double,
          used(lambda n: n.palm), used(lambda n: n.harmonic), used(lambda n: n.pinch),
          used(lambda n: n.hammer or n.pull), used(lambda n: n.tremolo),
          used(lambda n: n.slide_to > 0), used(lambda n: n.uslide_to > 0),
          used(lambda n: n.max_bend > 0), used(lambda n: n.vibrato),
          has_fhmute,
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
    for _ci, frets in enumerate(templates):
        fingers = chord_fingers(list(frets), hints=finger_hints[_ci])
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
    # Tone-change events: each is (time_seconds, slot 0-3) where slot maps to
    # the arrangement's Tones[A..D] list in the .rs2dlc project (built by
    # build_project()). The tone active at song start is the project's
    # BaseTone and needs no event here — Rocksmith just starts on it.
    tone_events = sorted(getattr(args, "tone_events", None) or [])
    _TONE_CODES = ("tone_a", "tone_b", "tone_c", "tone_d")
    if tone_events:
        A(f'  <events count="{len(tone_events)}">')
        for t, slot in tone_events:
            code = _TONE_CODES[max(0, min(3, int(slot)))]
            A(f'    <event time="{f3(t)}" code="{code}"/>')
        A("  </events>")
    else:
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


def gp_embedded_lyrics(gp):
    """Find lyrics embedded in the .gp itself (Official UG / many GP files carry
    them). Returns (track_index, [(offset_bar, text), ...]) for the first track
    that has a <Lyrics> block, else (None, []).

    Defensive about tag names across GP versions: looks for <Lyrics>/<Line> with
    <Text> and a bar offset in <Offset>/<Bar> or an attribute."""
    def _line_offset(line):
        for tag in ("Offset", "Bar", "StartBar", "bar", "offset"):
            v = line.findtext(tag) if tag[0].isupper() else line.get(tag)
            if v not in (None, ""):
                try:
                    return int(float(v))
                except Exception:
                    pass
        for a in ("offset", "bar", "startBar"):
            if line.get(a):
                try:
                    return int(float(line.get(a)))
                except Exception:
                    pass
        return 0

    for ti, tr in enumerate(gp.tracks):
        lyr = tr.find("Lyrics")
        if lyr is None:
            # some files nest it deeper
            lyr = next((e for e in tr.iter() if e.tag == "Lyrics"), None)
        if lyr is None:
            continue
        lines = []
        for line in lyr.iter("Line"):
            text = (line.findtext("Text") or "").strip()
            if text:
                lines.append((_line_offset(line), text))
        if lines:
            return ti, lines
    return None, []


def _syllabify(text):
    """Split a GP lyric line into (syllable, is_word_final) tokens.
    GP marks syllable breaks within a word with '-', words with spaces, and the
    occasional '+' elision (treated here as a word break)."""
    out = []
    for word in text.replace("+", " ").split():
        sylls = [s for s in word.split("-") if s != ""]
        for k, s in enumerate(sylls):
            out.append((s, k == len(sylls) - 1))
    return out


def gp_lyric_lines(gp, leadin, bpm_scale=1.0):
    """[(start_time, line_text), ...] for the embedded lyrics — used by the Sync
    preview's karaoke strip. Each line is timed to the first note at/after its
    offset bar."""
    import bisect
    ti, lines = gp_embedded_lyrics(gp)
    if ti is None:
        return []
    beats, bar_times = gp.track_beats(ti, leadin, bpm_scale=bpm_scale)
    starts = [b.start for b in beats]
    out = []
    for off, text in lines:
        bt = bar_times[off][0] if 0 <= off < len(bar_times) else (
            bar_times[0][0] if bar_times else leadin)
        i = bisect.bisect_left(starts, bt - 1e-6)
        t = starts[i] if i < len(starts) else bt
        plain = re.sub(r"\s+", " ", text.replace("-", "").replace("+", " ")).strip()
        out.append((t, plain))
    out.sort()
    return out


def gp_lyrics_to_vocals(gp, leadin, bpm_scale=1.0):
    """Build a Rocksmith vocals XML straight from the lyrics embedded in the .gp,
    timing each syllable to the lyric track's note onsets. Returns the XML, or
    None if the file has no embedded lyrics."""
    import bisect
    ti, lines = gp_embedded_lyrics(gp)
    if ti is None:
        return None
    beats, bar_times = gp.track_beats(ti, leadin, bpm_scale=bpm_scale)
    if not beats:
        return None
    starts = [b.start for b in beats]
    n = len(beats)
    vocals = []
    for off, text in lines:
        toks = _syllabify(text)
        if not toks:
            continue
        bt = bar_times[off][0] if 0 <= off < len(bar_times) else bar_times[0][0]
        start_idx = bisect.bisect_left(starts, bt - 1e-6)
        for k, (syl, final) in enumerate(toks):
            bi = start_idx + k
            if bi >= n:
                break
            t = beats[bi].start
            nxt = beats[bi + 1].start if bi + 1 < n else t + (beats[bi].dur or 0.4)
            length = max(0.15, min(nxt - t, 2.0))
            syl = re.sub(r'[<>&"]', "", syl)[:32]
            mark = syl + ("" if final else "-")   # '-' = hyphenated continuation
            vocals.append((t, length, mark))
    if not vocals:
        return None
    vocals.sort()
    out = ['<?xml version="1.0" encoding="UTF-8"?>', f'<vocals count="{len(vocals)}">']
    for t, ln, w in vocals:
        out.append(f'  <vocal time="{f3(t)}" note="254" length="{f3(ln)}" lyric="{xml_escape(w)}"/>')
    out.append("</vocals>")
    return "\n".join(out)


def main(args=None):
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
    if args is None:
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
        if len(guitars) >= 2:
            def chordiness(i):
                notes, _, _ = convert_track(gp, i, args.leadin)
                _, ch, _, _ = group_chords(notes)
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
    args.gpfile = path
    main(args)


if __name__ == "__main__":
    _auto_invocation()
