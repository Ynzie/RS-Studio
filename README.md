# RS Studio — Guitar Pro → Rocksmith 2014 CDLC Builder

> **Turn any Guitar Pro 7/8 file into a playable Rocksmith 2014 CDLC in minutes with no manual syncing**

---

## Features

**Auto-fetch everything**
Paste a YouTube URL and RS Studio downloads the audio, reads the video title to auto-correct song metadata, fetches album art from iTunes, and pulls synced lyrics from lrclib.net — all in one click.

**Guitar Pro conversion**
Reads `.gp` (Guitar Pro 7/8) files directly. Extracts tempo maps, time signatures, notes, chords, bends, slides, hammer-ons/pull-offs, palm mutes, harmonics, vibrato, tremolo, sections, and more. Exports full Rocksmith 2014 arrangement XML (Lead, Rhythm, and Bass).

**AI lyric timestamping**
Uses [Whisper](https://github.com/openai/whisper) (via `faster-whisper` / `stable-ts`) to generate word-level timestamps and sync lyrics to audio automatically. RS Studio installs the AI model (~145 MB) on first use — no manual setup needed. *****INWK********

**Live Sync & Verify**
Zoomable waveform viewer with a scrolling tab note overlay so you can see exactly where your notes land on the audio. Nudge the lead-in offset per-track, fine-tune BPM scaling, and adjust lyrics timing independently — all in real time before building.

**Dynamic Difficulty (DDC)**
Automatically runs DDC on your arrangement XMLs to generate beginner/intermediate/expert difficulty tiers, exactly like official DLC.

**One-click PSARC build**
Packages everything (arrangement XMLs, vocals XML, audio, album art) into a `.psarc` ready to drop into your Rocksmith DLC folder. Supports both DLC Builder output and Custom Song Toolkit (CST) + Wwise 2013 pipelines.

**Slopsmith integration**
Optionally embeds Slopsmith Desktop (Electron) for additional sync verification and testing tools.

**Themeable UI**
Dark and light modes. Eight accent colour presets (green, pink, orange, gold, cyan, blue, violet, red) plus a full colour picker. Your accent colour is saved between sessions.

**Zero-click dependency management**
RS Studio automatically downloads `yt-dlp`, `ffmpeg`, `ffplay`, and `ddc` on first launch — you don't need to install anything manually beyond Python itself.

---

## Requirements

### Python

Python **3.9 or newer** is required (3.11+ recommended).

Download from [python.org](https://www.python.org/downloads/) — make sure to tick **"Add Python to PATH"** during install.

### Python packages

Install with pip:

```
pip install pillow stable-ts faster-whisper
```

`tkinter` is included with standard Python on Windows. If it's missing (some Linux distros strip it), install `python3-tk` via your package manager.

### Bundled tools (auto-downloaded on first launch)

RS Studio will automatically download these into a `tools/` folder next to the app on first run:

- **yt-dlp** — YouTube audio downloader
- **ffmpeg / ffplay** — audio conversion and playback
- **ddc** — Dynamic Difficulty Creator (from the CST project)

You don't need to install these manually.

### Optional: Custom Song Toolkit + Wwise 2013 (for full PSARC auto-build)

If you want RS Studio to build the final `.psarc` file directly (rather than just exporting project files for DLC Builder), you'll also need:

- [Custom Song Toolkit](https://github.com/rscustom/rocksmith-custom-song-toolkit) installed — provides `packer.exe` and the `Wwise2013.tar.bz2` template
- [Wwise 2013.2.x](https://www.audiokinetic.com/downloads/) installed — provides `WwiseCLI.exe` for audio conversion

Point RS Studio at your `packer.exe` path in the app settings to enable this pipeline.

---

## Installation

1. **Download or clone this repository**

   ```
   git clone https://github.com/YOUR_USERNAME/rs-studio.git
   cd rs-studio
   ```

   Or download the ZIP from the green **Code** button and extract it anywhere.

2. **Install Python dependencies**

   ```
   pip install pillow stable-ts faster-whisper
   ```

3. **Run the app**

   ```
   python gp2rs_studio.py
   ```

   On first launch, RS Studio will automatically download `yt-dlp`, `ffmpeg`, and `ddc`. This takes about 30–60 seconds depending on your connection.

### Running as a standalone EXE (Windows)

If you'd prefer a single `.exe` you can double-click without needing Python installed, a `build_exe.bat` script is included. Run it once from the project folder:

```
build_exe.bat
```

This uses PyInstaller to bundle everything into `dist/RS Studio.exe`.

---

## Quick Start

1. **Load a Guitar Pro file** — click **+ Start New Project** on the splash screen, or drag a `.gp` file onto the window. RS Studio reads the title, artist, BPM, and track layout automatically.

2. **Fetch audio** — paste a YouTube URL into the **Audio URL** field and click **Auto-Fetch**. Audio, album art, and synced lyrics are all downloaded automatically.

3. **Check your settings** — review Title, Artist, Album, Year, Song Delay, Volume, and which tracks are set to Lead / Rhythm / Bass.

4. **Tune timing** — open the **Sync & Verify** page. Press **▶ Play** to hear the audio with notes scrolling over the waveform. Use the **Track leadin** nudge buttons to shift notes earlier or later. Adjust the **Lyrics offset** slider if lyrics feel off. Use **BPM scale** if the GP tempo doesn't match the recording.

5. **Build** — click **✓ Build & Export PSARC** (or **⚡ Build** on the main page). RS Studio converts the audio, generates the chart XMLs, applies dynamic difficulty, and packages the `.psarc`.

6. **Install** — copy the `.psarc` into your Rocksmith DLC folder:

   ```
   Steam\steamapps\common\Rocksmith2014\dlc\
   ```

   Launch Rocksmith 2014 Remastered — the song appears in Learn a Song.

---

## File Overview

| File | Purpose |
|------|---------|
| `gp2rs_studio.py` | Main GUI app — this is what you run |
| `gp2rs.py` | Core converter: Guitar Pro → Rocksmith 2014 arrangement XML |
| `wwise_convert.py` | WAV → WEM audio conversion via Wwise 2013 + CST template |
| `cst_template.py` | Generates the CST `.dlc.xml` project file for `packer.exe` |

> **Note:** `gp2rs_studio.py` is the actively maintained entry point. The other files are support modules called by the studio and should be kept in the same folder.

---

## Troubleshooting

**No audio after Auto-Fetch**
Make sure `yt-dlp.exe` and `ffmpeg.exe` are present in the `tools/` folder next to the app. RS Studio downloads them automatically, but if there was a network error on first launch you can grab them manually from [yt-dlp releases](https://github.com/yt-dlp/yt-dlp/releases) and [ffmpeg.org](https://ffmpeg.org/download.html).

**Wrong song fetched from YouTube**
Paste a direct YouTube URL (e.g. `https://www.youtube.com/watch?v=...`) into the Audio URL field before clicking Auto-Fetch.

**Build failed**
Check the **Log** page for the exact error. Common causes: missing packer path, bad audio format, or no arrangements selected.

**Lyrics not showing in-game**
Make sure the `.lrc` file path is set in the Lyrics field on the Main page before building.

**Volume too loud in-game**
Lower the Volume field (e.g. `-12` instead of `-8`) and rebuild.

**AI timestamps need to be re-run**
Click **🎙 Get Lyrics & Timestamps** on the Main page. RS Studio will re-download lyrics and re-run Whisper. `faster-whisper` is installed automatically on first use.

**CDLC doesn't appear in Rocksmith**
Make sure you have the CDLC enabler (D3DX9 patch) installed. Without it Rocksmith won't load custom songs.

---

## Credits

Built on top of the [Custom Song Toolkit](https://github.com/rscustom/rocksmith-custom-song-toolkit) ecosystem and the incredible work of the Rocksmith modding community.

AI transcription powered by [OpenAI Whisper](https://github.com/openai/whisper) via [faster-whisper](https://github.com/guillaumekln/faster-whisper) / [stable-ts](https://github.com/jianfch/stable-ts).

Lyrics sourced from [lrclib.net](https://lrclib.net) and [Genius](https://genius.com).

📺 **[YouTube → @Ynziepoo](https://www.youtube.com/@Ynziepoo)**
