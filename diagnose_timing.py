#!/usr/bin/env python3
"""
diagnose_timing.py - sanity-check a .gp file's tempo map against the actual
audio, to figure out whether "stuff is off in-game" is:

  (a) a constant OFFSET (lyrics/notes shifted by the same amount the whole
      song) - usually a lead-in / padding mismatch, or
  (b) DRIFT that grows over the song - usually means the .gp tempo map
      doesn't match the real recording (a true "bpm issue"), often caused by
      mid-bar tempo automations (Position != 0) that gp2rs's bar_times()
      currently snaps to the start of the bar.

Usage:
  python3 diagnose_timing.py song.gp
  python3 diagnose_timing.py song.gp --audio song.mp3
  python3 diagnose_timing.py song.gp --audio song.mp3 --leadin 5
"""
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gp2rs  # noqa: E402


def audio_duration(path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=60)
        return float(r.stdout.strip())
    except Exception as e:
        print(f"  (couldn't probe audio duration: {e})")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gpfile")
    ap.add_argument("--audio", default=None,
                    help="original (un-padded) audio file, for length comparison")
    ap.add_argument("--leadin", type=float, default=5.0)
    args = ap.parse_args()

    gp = gp2rs.GPSong(args.gpfile)
    print(f"Title:  {gp.title}")
    print(f"Artist: {gp.artist}")
    print(f"Masterbars: {len(gp.masterbars)}")
    print()

    print("Tempo automations found in the .gp file (Bar, Position, BPM):")
    print("  Bar  Position  BPM")
    flagged = []
    for bar, pos, bpm in gp.tempos:
        flag = ""
        if pos != 0:
            flag = "  <-- mid-bar (Position != 0); bar_times() snaps this to the bar start"
            flagged.append((bar, pos, bpm))
        print(f"  {bar:4d}  {pos:8.4f}  {bpm:6.2f}{flag}")
    print()

    if flagged:
        print(f"{len(flagged)} mid-bar tempo automation(s) found.")
        print("Each one introduces a small one-time timing error at that bar, and")
        print("ALL bars after it inherit that error as a constant shift. If you have")
        print("several of these scattered through the song, they add up and look")
        print("like 'drift that gets worse toward the end' - a real BPM/tempo-map")
        print("issue, not something gp2rs's lead-in setting can fix.")
    else:
        print("No mid-bar tempo automations - the tempo map itself should be timed")
        print("correctly bar-by-bar. If notes still drift, double check the .gp")
        print("tempo VALUES against the recording (e.g. tap along with a")
        print("metronome at the reported BPM near the start AND near the end).")
    print()

    # chart length with leadin=0, i.e. just the instrumental body
    _, body_end = gp.bar_times(0.0)
    print(f"Chart body length (no lead-in): {body_end:.3f} s")
    print(f"With --leadin {args.leadin:g}, bar 1 / first note lands at "
          f"t={args.leadin:g}s and the song ends at t={body_end + args.leadin:.3f}s")

    if args.audio:
        dur = audio_duration(args.audio)
        if dur is not None:
            print()
            print(f"Audio file duration: {dur:.3f} s")
            diff = dur - body_end
            pct = (diff / body_end * 100) if body_end else 0
            print(f"Audio - chart body length = {diff:+.3f} s ({pct:+.2f}%)")
            print("(This compares the audio to the chart's *body* length, i.e. with")
            print(" the lead-in removed. Use your ORIGINAL un-padded audio here, not")
            print(" the *_48k.wav that gp2rs_studio already padded with lead-in.)")
            print()
            if abs(pct) < 0.3:
                print("-> Lengths match closely. The tempo map's overall pace looks")
                print("   right; any remaining 'off' feeling is more likely a constant")
                print("   offset (lead-in / lyrics) than a BPM problem.")
            else:
                print("-> Lengths differ by more than ~0.3%. That's consistent with a")
                print("   BPM mismatch between the .gp tempo map and the real")
                print("   recording - notes/lyrics will drift further out of sync the")
                print("   longer the song plays. Re-check the tempo value(s) in the")
                print("   .gp file against the recording.")


if __name__ == "__main__":
    main()
