# ✂️ CutCut — Video Transcript Editor

Upload a video → get a word-level transcript with millisecond timestamps → click words to mark them for deletion → export a cleanly cut video.

**Auto-detects Chinese filler words**: 嗯 额 啊 呃 哦 那个 就是 然后 etc.

---

## Prerequisites

- Python 3.10+
- Node.js 18+
- ffmpeg (must be in PATH)
- An OpenAI API key

---

## Setup

### 1. Backend

```bash
cd backend
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...       # Mac/Linux
# set OPENAI_API_KEY=sk-...        # Windows
uvicorn main:app --reload --port 8000
```

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open **http://localhost:3000**

---

## How to Use

1. **Upload** your video (MP4, MOV, AVI, MKV)
2. Click **Transcribe with Whisper** — Whisper returns every word with ms-level timestamps
3. In the editor:
   - 🟡 Orange chips = auto-detected filler words (嗯 额 etc.)
   - Click **Auto-mark all fillers** to mark them all at once
   - Click any word chip to toggle it for deletion (turns red with strikethrough)
   - Use the **Search & delete** box to find and cut a specific word everywhere
   - Click any word to also jump the video to that moment
4. Click **Export** → ffmpeg cuts the marked segments → download the clean video

---

## Filler Words Auto-Detected

Chinese: 嗯 额 啊 呃 哦 噢 唔 哎 哼 那个 就是 然后 这个 嘛 吧 呢  
English: uh um erm hmm ah oh

Add more in `backend/main.py` → `FILLER_WORDS` set, and `frontend/src/TranscriptEditor.jsx` → `FILLER_WORDS` set.

---

## Project Structure

```
video-transcript-editor/
├── backend/
│   ├── main.py           # FastAPI server
│   ├── requirements.txt
│   ├── uploads/          # auto-created: incoming videos + transcripts
│   └── outputs/          # auto-created: exported videos
└── frontend/
    ├── src/
    │   ├── App.jsx           # Upload, loading, done screens
    │   ├── TranscriptEditor.jsx  # Core word editing UI
    │   └── App.css           # Dark editorial design
    ├── index.html
    ├── package.json
    └── vite.config.js
```
