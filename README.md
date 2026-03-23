# ✂️ FastCut

FastCut is a transcript-first video and audio editor.

Upload a file, generate a transcript, select words in the transcript, cut them out, preview the edited result, and export a clean final file.

FastCut is designed for spoken content workflows such as:
- removing filler words
- trimming repeated takes
- cleaning up pauses and thinking sounds
- cutting directly from transcript text instead of a traditional timeline

---

## Default Setup

FastCut now defaults to a **local-only transcription setup**.

For the standard setup, you do **not** need:
- an OpenAI API key
- any other external AI API key

By default FastCut uses:
- `qwen3_asr_local`
- `Qwen/Qwen3-ASR-0.6B`
- `Qwen/Qwen3-ForcedAligner-0.6B`

You only need an API key if you intentionally switch the provider to `openai_whisper_chunked`.

---

## What FastCut Does

- Generates editable transcript tokens with timestamps
- Lets you select transcript text and cut or restore it with keyboard shortcuts
- Supports drag selection across multiple words
- Detects filler words, long pauses, short pauses, and thinking sounds
- Generates an **accurate edited preview** using the same backend render path as final export
- Exports edited video or audio with ffmpeg

---

## Current Editing Model

FastCut no longer uses per-token `CUT / KEEP` buttons as the main workflow.

The current editing model is:
- Click a word to move the playhead
- Drag across words to select a range
- Press `D` to cut the selected range
- Press `F` to restore the selected range
- Press `Space` to play or pause

This makes the editor behave more like editing text than clicking a control panel.

---

## Supported Media

Input:
- Video: `MP4`, `MOV`, `AVI`, `MKV`
- Audio: `M4A`, `MP3`, `WAV`

Output:
- Edited video export for video inputs
- Edited audio export for audio-only inputs

---

## Transcription Providers

FastCut supports multiple transcription backends through `TRANSCRIPTION_PROVIDER`.

Implemented providers:
- `qwen3_asr_local`
- `local_whisper`
- `openai_whisper_chunked`
- `funasr`

The current codebase also supports local Qwen ASR + forced alignment settings.

Example provider-related settings in [`.env.example`](/Users/simona/Downloads/video cutter/.env.example):

```env
TRANSCRIPTION_PROVIDER=qwen3_asr_local

LOCAL_WHISPER_MODEL=base
LOCAL_WHISPER_LANGUAGE=zh
WHISPER_DEVICE=cpu

QWEN_ASR_MODEL=Qwen/Qwen3-ASR-0.6B
QWEN_ASR_ALIGNER_MODEL=Qwen/Qwen3-ForcedAligner-0.6B
QWEN_ASR_LANGUAGE=Chinese
QWEN_ASR_DEVICE=auto
QWEN_ASR_DTYPE=auto
QWEN_ASR_MAX_BATCH_SIZE=8
QWEN_ASR_MAX_NEW_TOKENS=256
QWEN_ASR_ENABLE_ALIGNER=true

# Optional: only needed if you switch to the OpenAI provider
# OPENAI_API_KEY=sk-your-real-openai-api-key
```

Notes:
- `qwen3_asr_local` is the default local-only setup and does not require an API key
- `openai_whisper_chunked` remains available if you want to use the OpenAI API
- local Qwen / local Whisper may download models on first run
- ffmpeg must be installed and available in `PATH`

---

## Prerequisites

- Python 3.10+
- Node.js 18+
- `ffmpeg` installed and available in `PATH`
- enough local disk / memory to run local ASR models
- an OpenAI API key only if you explicitly switch to `openai_whisper_chunked`

---

## Setup

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Install frontend dependencies

```bash
npm install
```

### 3. Create your local env file

```bash
cp .env.example .env
```

Then update the values in [`.env`](/Users/simona/Downloads/video cutter/.env) only if you want to change the default provider.

If you do not want to use any external AI API, keep the default local setup:

```env
TRANSCRIPTION_PROVIDER=qwen3_asr_local
QWEN_ASR_MODEL=Qwen/Qwen3-ASR-0.6B
QWEN_ASR_ALIGNER_MODEL=Qwen/Qwen3-ForcedAligner-0.6B
```

---

## Run the App

### Backend

```bash
uvicorn main:app --reload --port 8000
```

### Frontend

```bash
npm run dev -- --port 4190
```

Then open:
- Frontend: [http://127.0.0.1:4190](http://127.0.0.1:4190)
- Backend health check: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

On first run with the default local setup, FastCut may spend extra time downloading the Qwen ASR and aligner weights before the first transcription completes.

---

## How to Use

1. Upload a video or audio file
2. Generate the transcript
3. Edit the transcript:
   - click a word to move the playhead
   - drag across words to select a range
   - press `D` to cut
   - press `F` to restore
   - press `Space` to play or pause
4. Use **Play edited cut** to generate and preview the edited version
5. Export the final cut

---

## Editing Features

### Transcript-based cutting
- Edit by selecting transcript text instead of scrubbing a timeline
- Deleted words stay visible with strike-through, so you can review your edits

### Batch selection
- Drag across multiple words to select them in one gesture
- Works left-to-right and right-to-left

### Keyboard shortcuts
- `D` → cut selection
- `F` → restore selection
- `Space` → play / pause

### Auto-mark tools
- Mark filler words
- Mark thinking sounds
- Mark pauses

### Search-based cutting
- Find matching words
- Cut all matches in one action

### Manual time cuts
- Add cuts directly by time range
- Useful when transcript text is not enough for a specific boundary

---

## Preview vs Export

FastCut now uses two distinct ideas:

### Transcript editing preview
- You edit through the transcript UI
- Selection and cut state are shown in the editor

### Accurate edited preview
- `Play edited cut` calls the backend `/preview`
- The backend renders a temporary edited media file using the same ffmpeg concat path as final export
- This makes preview playback much closer to the final export than simple browser skip-seeking

### Final export
- `/export` renders the final edited file
- Video and audio are cut using ffmpeg based on transcript deletions and manual cuts

---

## Pause / Filler / Annotation Behavior

FastCut can display:
- filler words
- thinking sounds
- long pauses
- short pauses

These appear inline in the transcript so they can be selected and cut like normal text.

Examples:
- `〈停顿 1.7s〉`
- `〈短停顿 0.8s〉`
- `〈思考音 1.5s〉`

---

## Project Structure

```text
video cutter/
├── App.jsx
├── App.css
├── TranscriptEditor.jsx
├── main.py
├── requirements.txt
├── package.json
├── index.html
├── vite.config.js
├── .env.example
├── uploads/          # transcripts, uploaded media, raw debug outputs
└── outputs/          # preview and exported media
```

Key files:
- [App.jsx](/Users/simona/Downloads/video cutter/App.jsx) — upload, loading, editing, export, done states
- [TranscriptEditor.jsx](/Users/simona/Downloads/video cutter/TranscriptEditor.jsx) — transcript editing UI
- [App.css](/Users/simona/Downloads/video cutter/App.css) — styling
- [main.py](/Users/simona/Downloads/video cutter/main.py) — FastAPI backend, transcription, preview, export

---

## API Endpoints

Implemented backend endpoints:
- `POST /transcribe`
- `POST /preview`
- `POST /export`
- `GET /health`

---

## Notes

- Local model providers may be slow on first run because they download weights
- Export speed depends on media length, edit density, and available hardware acceleration
- Accurate preview and final export are intentionally aligned so playback is closer to the exported result

---

## GitHub

Repository:
- [https://github.com/bluebluegrass/FastCut](https://github.com/bluebluegrass/FastCut)
