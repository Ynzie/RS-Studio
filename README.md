# RS Studio — Guitar Pro → Rocksmith 2014 CDLC Builder

> **Turn any Guitar Pro 7/8 file into a playable Rocksmith 2014 CDLC in minutes with no manual syncing**

---

## Features

**Auto-fetch everything**
Paste a YouTube URL and RS Studio downloads the audio, reads the video title to auto-correct song metadata, fetches album art from iTunes, and pulls synced lyrics from lrclib.net — all in one click.

**Guitar Pro conversion**
Reads `.gp` (Guitar Pro 7/8) files directly. Extracts tempo maps, time signatures, notes, chords, bends, slides, hammer-ons/pull-offs, palm mutes, harmonics, vibrato, tremolo, sections, and more. Exports full Rocksmith 2014 arrangement XML (Lead, Rhythm, and Bass).

**PDF tab import**
No Guitar Pro file? Click **📄 Import PDF Tab** and hand RS Studio 1-3 Ultimate Guitar print-to-PDF tabs (bass/lead/rhythm) for a song — it reads the notes, rhythm, tempo, tuning, sections, and lyrics straight off the PDF and builds a `.gp5` file for you automatically, no lyrics file or extra prompts needed. If the PDF has a lyric line, RS Studio uses the timed `.lrc` pdf2gp builds from it directly (Auto-Fetch won't re-search or overwrite it). If pdf2gp can't read a tempo mark off the PDF it defaults to 120 BPM and logs a warning on the Log page — always double-check the BPM field after a PDF import.

**AI lyric timestamping**
Uses [Whisper](https://github.com/openai/whisper) (via `faster-whisper` / `stable-ts`) to generate word-level timestamps and sync lyrics to audio automatically. RS Studio installs the AI model on first use — no manual setup needed.

**Live Sync & Verify**
Zoomable waveform viewer with a scrolling tab note overlay so you can see exactly where your notes land on the audio. Nudge the lead-in offset per-track, fine-tune BPM scaling, and adjust lyrics timing independently — all in real time before building.

**Dynamic Difficulty**
The bundled DLC Builder packer (`RSDDC_Build2`) generates beginner/intermediate/expert difficulty tiers for your arrangements automatically as part of the PSARC build — exactly like official DLC. No separate DDC tool or step needed.

**One-click PSARC build**
Packages everything (arrangement XMLs, vocals XML, audio, album art, dynamic difficulty) into a `.psarc` ready to drop into your Rocksmith DLC folder, using the bundled `RSDDC_Build2` packer. If your audio isn't already a `.wem`, RS Studio converts it for you — using a modern Wwise install (2019/2021/2022/2023) if you have one, via the bundled Wwise project templates.

**Slopsmith integration**
Optionally embeds Slopsmith Desktop (Electron) for additional sync verification and testing tools.

**Themeable UI**
Dark and light modes. Eight accent colour presets (green, pink, orange, gold, cyan, blue, violet, red) plus a full colour picker. Your accent colour is saved between sessions.

**Zero-click dependency management**
RS Studio automatically downloads `yt-dlp`, `ffmpeg`, `ffplay`, and the Whisper AI model on first launch — you don't need to install anything manually beyond Python itself.

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

The **PDF tab import** feature needs a few extra packages (`opencv-python`, `pikepdf`, `pdfplumber`, `pdfminer.six`, `pyguitarpro`) — RS Studio installs these automatically the first time you click **📄 Import PDF Tab**, so you don't need to install them up front. For the best lyric OCR (spelling/accents), also install [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) — this part is optional.

### Bundled tools (auto-downloaded on first launch)

RS Studio will automatically download these into a `tools/` folder next to the app on first run:

- **yt-dlp** — YouTube audio downloader
- **ffmpeg / ffplay** — audio conversion and playback
- **Whisper AI model** (`faster-whisper` / `stable-ts`) — for AI lyric timestamping

You don't need to install these manually. `RSDDC_Build2` (the packer that builds the final `.psarc` and generates Dynamic Difficulty) ships as part of the repo itself, not as a first-run download.

### Optional: Wwise (for building your own `.wem` audio)

RS Studio's packer (`RSDDC_Build2`) needs your song audio as a `.wem` file. If you don't already have one, RS Studio can convert your `.wav` for you, but only if you have a modern Wwise install:

- [Wwise 2019, 2021, 2022, or 2023](https://www.audiokinetic.com/downloads/) installed — provides `WwiseConsole.exe`, found under `Audiokinetic\Wwise 20XX\Authoring\x64\Release\bin`

RS Studio finds it automatically (or via a `WWISEROOT` environment variable) and uses its own bundled Wwise project templates (`wwise_templates/`) to do the conversion — no Custom Song Toolkit or Wwise 2013 template needed.

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

   On first launch, RS Studio will automatically download `yt-dlp` and `ffmpeg`/`ffplay` (and the Whisper AI model the first time you use lyric timestamping). This takes about 30–60 seconds depending on your connection.

### Running as a standalone EXE (Windows)

If you'd prefer a single `.exe` you can double-click without needing Python installed, a `build_exe.bat` script is included. It needs `_build_helper.py` (the actual PyInstaller build logic) in the same folder — both are already part of the repo. Run it once from the project folder:

```
build_exe.bat
```

This installs PyInstaller and the other packaging dependencies, then bundles everything into `dist/RS STUDIO.exe`. Full build output is also saved to `build_log.txt` next to the script for troubleshooting.

---

## Quick Start

1. **Load a Guitar Pro file** — click **+ Start New Project** on the splash screen, or drag a `.gp` file onto the window. RS Studio reads the title, artist, BPM, and track layout automatically.

   Don't have a `.gp` file? Click **📄 Import PDF Tab** instead, pick 1-3 Ultimate Guitar print-to-PDF tabs for the song (bass/lead/rhythm), and RS Studio builds and loads a `.gp5` for you.

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
| `wwise_convert.py` | WAV → WEM audio conversion via a modern Wwise install + bundled `wwise_templates/` |
| `pdf2gp.py`, `staffdet.py`, `tabrec.py` | PDF tab import: turns Ultimate Guitar print-to-PDF tabs into a `.gp5` file (+ `digit_templates.npz` / `sig_templates.npz` template data) |
| `lyrics_align.py`, `lyric_sync.py`, `lyric_font.py` | Lyrics fetching, AI timestamp alignment, and in-game lyric font/timing handling |
| `tone_designer.py` | In-app tone/effects chain editor |
| `RSDDC_Build2/` | Bundled DLC Builder-based packer — builds the final `.psarc` and generates Dynamic Difficulty; committed to the repo, not downloaded |
| `_build_helper.py` | PyInstaller build logic used by `build_exe.bat` to produce the standalone `.exe` |

> **Note:** `gp2rs_studio.py` is the actively maintained entry point. The other files are support modules called by the studio and should be kept in the same folder. `cst_template.py` is a leftover from an earlier Custom Song Toolkit pipeline and is no longer used, but is still bundled for now.

---

## Troubleshooting

**No audio after Auto-Fetch**
Make sure `yt-dlp.exe` and `ffmpeg.exe` are present in the `tools/` folder next to the app. RS Studio downloads them automatically, but if there was a network error on first launch you can grab them manually from [yt-dlp releases](https://github.com/yt-dlp/yt-dlp/releases) and [ffmpeg.org](https://ffmpeg.org/download.html).

**Wrong song fetched from YouTube**
Paste a direct YouTube URL (e.g. `https://www.youtube.com/watch?v=...`) into the Audio URL field before clicking Auto-Fetch.

**Build failed**
Check the **Log** page for the exact error. Common causes: `RSDDC_Build2.exe` missing (should already be bundled — re-download the repo/exe if it's gone), bad audio format, or no arrangements selected.

**Lyrics not showing in-game**
Make sure the `.lrc` file path is set in the Lyrics field on the Main page before building.

**Volume too loud in-game**
Lower the Volume field (e.g. `-12` instead of `-8`) and rebuild.

**AI timestamps need to be re-run**
Click **🎙 Get Lyrics & Timestamps** on the Main page. RS Studio will re-download lyrics and re-run Whisper. `faster-whisper` is installed automatically on first use.

**CDLC doesn't appear in Rocksmith**
Make sure you have the CDLC enabler (D3DX9 patch) installed. Without it Rocksmith won't load custom songs.

**PDF import produced a bad or empty chart**
Make sure the PDF is Ultimate Guitar's own print-to-PDF export (not a scan or another site's tab) and that it's named clearly (e.g. `song-bass.pdf`). Check the Log page for per-file stats and any `warn measure N` lines — those measures were approximated because of ornaments the tool can't fully read (grace notes, slides, hammer-ons/pull-offs, strum arrows).

**PDF import has the wrong tempo**
If pdf2gp couldn't OCR a tempo mark off the PDF, it defaults to 120 BPM and logs a `WARNING` on the Log page — that default is almost never the song's real tempo. Fix it in the BPM field on the Main page, or load audio and click **Auto ↺** on the Sync page to rescale it to match.

---

## Credits

Built on top of the [Custom Song Toolkit](https://github.com/rscustom/rocksmith-custom-song-toolkit) ecosystem and the incredible work of the Rocksmith modding community.

AI transcription powered by [OpenAI Whisper](https://github.com/openai/whisper) via [faster-whisper](https://github.com/guillaumekln/faster-whisper) / [stable-ts](https://github.com/jianfch/stable-ts).

Lyrics sourced from [lrclib.net](https://lrclib.net) and [Genius](https://genius.com).

📺 **[YouTube → @Ynziepoo](https://www.youtube.com/@Ynziepoo)**
