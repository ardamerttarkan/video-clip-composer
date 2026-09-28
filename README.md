# Nostalgic Reels Studio

A local Streamlit app for turning music videos into 9:16 Reels. Select a clip, add a headline and day counter, preview the result, and export a **1080 × 1920 MP4**.

## Features

- Import a video from YouTube, an upload, or a local file path
- Download only the selected section of a YouTube video
- Fit or crop the clip, with an optional artist and song label
- Preview before exporting an H.264 MP4 (AAC audio when available)
- Optionally generate a Turkish post caption with the Gemini API

## Setup

Requires **Python 3.10+**, **FFmpeg** (including `ffprobe` and `libx264`), and the **DejaVu Sans** font. On Debian or Ubuntu:

```bash
sudo apt install python3 python3-venv python3-pip ffmpeg fonts-dejavu-core
```

```bash
git clone <repository-url> reels_musics
cd reels_musics
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
./run.sh
```

Open **http://127.0.0.1:8501**. For future sessions, run `./run.sh`.

YouTube imports also require **Deno 2.3+** or **Node.js 22+**. Neither is needed for local videos.

## Usage

1. Choose a video source and select the clip you want to use.
2. Edit the headline, day counter, and layout options.
3. Click **Kısa önizleme oluştur** (Create short preview), then **Nihai videoyu üret** (Generate final video).

Finished videos are saved in `outputs/`. Temporary files and previews are stored in `work/`.

To generate a Turkish post caption, copy `.env.example` to `.env` and set `GEMINI_API_KEY`. This feature is optional; video creation works without an API key. The `.env` file and generated videos are ignored by Git.

## Local check

```bash
.venv/bin/python selftest.py
```

This creates a sample video and checks its dimensions, duration, and audio stream.
