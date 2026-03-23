import { useState, useRef, useCallback } from "react";
import TranscriptEditor from "./TranscriptEditor";
import "./App.css";

const API = "http://localhost:8000";
const DRAFT_STORAGE_PREFIX = "fastcut:draft:";

export default function App() {
  const [stage, setStage] = useState("upload"); // upload | transcribing | editing | exporting | done
  const [videoFile, setVideoFile] = useState(null);
  const [videoUrl, setVideoUrl] = useState(null);
  const [mediaType, setMediaType] = useState("video");
  const [session, setSession] = useState(null); // { video_id, words, duration_ms }
  const [editorDraft, setEditorDraft] = useState(null);
  const [error, setError] = useState(null);
  const [exportUrl, setExportUrl] = useState(null);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [previewSignature, setPreviewSignature] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const fileInputRef = useRef();

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    const file = e.dataTransfer.files[0];
    if (file && (file.type.startsWith("video/") || file.type.startsWith("audio/"))) selectFile(file);
  }, []);

  const selectFile = (file) => {
    setVideoFile(file);
    setVideoUrl(URL.createObjectURL(file));
    setMediaType(file.type.startsWith("audio/") ? "audio" : "video");
    setEditorDraft(null);
    setError(null);
    setExportUrl(null);
    setPreviewUrl(null);
    setPreviewSignature(null);
    setPreviewLoading(false);
  };

  const handleTranscribe = async () => {
    setStage("transcribing");
    setError(null);
    const form = new FormData();
    form.append("file", videoFile);
    try {
      const res = await fetch(`${API}/transcribe`, { method: "POST", body: form });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Transcription failed");
      }
      const data = await res.json();
      const savedDraft = sessionStorage.getItem(`${DRAFT_STORAGE_PREFIX}${data.video_id}`);
      setSession(data);
      setEditorDraft(savedDraft ? JSON.parse(savedDraft) : null);
      setStage("editing");
    } catch (e) {
      setError(e.message);
      setStage("upload");
    }
  };

  const handleDraftChange = useCallback((nextDraft) => {
    setEditorDraft(nextDraft);
    if (!session?.video_id) return;
    sessionStorage.setItem(`${DRAFT_STORAGE_PREFIX}${session.video_id}`, JSON.stringify(nextDraft));
  }, [session?.video_id]);

  const handleExport = async (deletedWords) => {
    setStage("exporting");
    setError(null);
    try {
      const res = await fetch(`${API}/export`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ video_id: session.video_id, deleted_words: deletedWords }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Export failed");
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      setExportUrl(url);
      setStage("done");
    } catch (e) {
      setError(e.message);
      setStage("editing");
    }
  };

  const handlePreview = useCallback(async (deletedWords) => {
    if (!session?.video_id) return null;
    const signature = JSON.stringify(
      deletedWords.map((word) => ({
        word: word.word,
        start_ms: word.start_ms,
        end_ms: word.end_ms,
        kind: word.kind,
      }))
    );

    if (previewUrl && previewSignature === signature) {
      return previewUrl;
    }

    setPreviewLoading(true);
    try {
      const res = await fetch(`${API}/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ video_id: session.video_id, deleted_words: deletedWords }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Preview failed");
      }
      const blob = await res.blob();
      if (previewUrl) {
        URL.revokeObjectURL(previewUrl);
      }
      const url = URL.createObjectURL(blob);
      setPreviewUrl(url);
      setPreviewSignature(signature);
      return url;
    } finally {
      setPreviewLoading(false);
    }
  }, [previewSignature, previewUrl, session?.video_id]);

  const backToEditing = () => {
    setStage("editing");
    setError(null);
  };

  const reset = () => {
    if (session?.video_id) {
      sessionStorage.removeItem(`${DRAFT_STORAGE_PREFIX}${session.video_id}`);
    }
    setStage("upload");
    setVideoFile(null);
    setVideoUrl(null);
    setMediaType("video");
    setSession(null);
    setEditorDraft(null);
    setExportUrl(null);
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
    }
    setPreviewUrl(null);
    setPreviewSignature(null);
    setPreviewLoading(false);
    setError(null);
  };

  return (
    <div className="app">
      <header className="header">
        <div className="logo">✂️ FastCut</div>
        <div className="tagline">Video transcript editor — cut filler words instantly</div>
      </header>

      {error && (
        <div className="error-banner">
          ⚠️ {error}
          <button onClick={() => setError(null)}>✕</button>
        </div>
      )}

      {stage === "upload" && (
        <div className="upload-section">
          <div
            className="drop-zone"
            onDrop={handleDrop}
            onDragOver={(e) => e.preventDefault()}
            onClick={() => fileInputRef.current.click()}
          >
            {videoFile ? (
              <>
                <div className="file-selected">🎬 {videoFile.name}</div>
                <div className="file-size">{(videoFile.size / 1024 / 1024).toFixed(1)} MB</div>
              </>
            ) : (
              <>
                <div className="drop-icon">🎬</div>
                <div className="drop-text">Drop your video or audio here</div>
                <div className="drop-sub">or click to browse — MP4, MOV, AVI, MKV, M4A, MP3, WAV</div>
              </>
            )}
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept="video/*,audio/*,.m4a,.mp3,.wav"
            style={{ display: "none" }}
            onChange={(e) => e.target.files[0] && selectFile(e.target.files[0])}
          />
          {videoUrl && (
            <div className="preview-wrap">
              {mediaType === "audio" ? (
                <audio src={videoUrl} controls className="preview-audio" />
              ) : (
                <video src={videoUrl} controls className="preview-video" />
              )}
            </div>
          )}
          {videoFile && (
            <button className="btn-primary" onClick={handleTranscribe}>
              🎙️ Transcribe with Whisper
            </button>
          )}
        </div>
      )}

      {stage === "transcribing" && (
        <div className="loading-section">
          <div className="spinner" />
          <div className="loading-text">Transcribing with OpenAI Whisper…</div>
          <div className="loading-sub">This may take a minute for longer videos</div>
        </div>
      )}

      {stage === "editing" && session && (
        <TranscriptEditor
          session={session}
          draft={editorDraft}
          videoUrl={videoUrl}
          previewUrl={previewUrl}
          previewLoading={previewLoading}
          mediaType={mediaType}
          onDraftChange={handleDraftChange}
          onPreview={handlePreview}
          onExport={handleExport}
        />
      )}

      {stage === "exporting" && (
        <div className="loading-section">
          <div className="spinner" />
          <div className="loading-text">Cutting video with ffmpeg…</div>
          <div className="loading-sub">Removing deleted segments and re-encoding</div>
        </div>
      )}

      {stage === "done" && exportUrl && (
        <div className="done-section">
          <div className="done-icon">✅</div>
          <div className="done-title">Your edited video is ready!</div>
          {mediaType === "audio" ? (
            <audio src={exportUrl} controls className="done-preview-audio" />
          ) : (
            <video src={exportUrl} controls className="done-preview-video" />
          )}
          <div className="done-actions">
            <a href={exportUrl} download={mediaType === "audio" ? "edited_audio.m4a" : "edited_video.mp4"} className="btn-primary">
              ⬇️ Download Edited {mediaType === "audio" ? "Audio" : "Video"}
            </a>
            <button className="btn-secondary" onClick={backToEditing}>
              Back to editing
            </button>
            <button className="btn-secondary" onClick={reset}>
              Start Over
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
