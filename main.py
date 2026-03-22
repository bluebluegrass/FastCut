import os
import json
import tempfile
import subprocess
import re
import logging
import wave
import audioop
import array
from functools import lru_cache
from pathlib import Path
from typing import List

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import openai
import aiofiles
from dotenv import load_dotenv

load_dotenv(override=True)

app = FastAPI(title="Video Transcript Editor")
logger = logging.getLogger("fastcut.export")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:4173",
        "http://localhost:4174",
        "http://localhost:4186",
        "http://localhost:4190",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:4173",
        "http://127.0.0.1:4174",
        "http://127.0.0.1:4186",
        "http://127.0.0.1:4190",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path("uploads")
OUTPUT_DIR = Path("outputs")
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# Filler words to auto-detect (Chinese + common English)
FILLER_WORDS = {
    "嗯", "额", "啊", "呃", "哦", "噢", "唔", "哎", "哼",
    "那个", "就是", "然后", "这个", "就", "嘛", "吧", "呢",
    "uh", "um", "erm", "hmm", "ah", "oh", "like", "you know", "so",
}

client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

TRANSCRIPTION_PROVIDER = os.environ.get("TRANSCRIPTION_PROVIDER", "local_whisper")
LOCAL_WHISPER_MODEL = os.environ.get("LOCAL_WHISPER_MODEL", "base")
LOCAL_WHISPER_LANGUAGE = os.environ.get("LOCAL_WHISPER_LANGUAGE", "zh")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
QWEN_ASR_MODEL = os.environ.get("QWEN_ASR_MODEL", "Qwen/Qwen3-ASR-0.6B")
QWEN_ASR_ALIGNER_MODEL = os.environ.get("QWEN_ASR_ALIGNER_MODEL", "Qwen/Qwen3-ForcedAligner-0.6B")
QWEN_ASR_LANGUAGE = os.environ.get("QWEN_ASR_LANGUAGE", "Chinese")
QWEN_ASR_DEVICE = os.environ.get("QWEN_ASR_DEVICE", "auto")
QWEN_ASR_DTYPE = os.environ.get("QWEN_ASR_DTYPE", "auto")
QWEN_ASR_MAX_BATCH_SIZE = int(os.environ.get("QWEN_ASR_MAX_BATCH_SIZE", "8"))
QWEN_ASR_MAX_NEW_TOKENS = int(os.environ.get("QWEN_ASR_MAX_NEW_TOKENS", "256"))
QWEN_ASR_ENABLE_ALIGNER = os.environ.get("QWEN_ASR_ENABLE_ALIGNER", "true").lower() in {"1", "true", "yes", "on"}
FUNASR_MODEL = os.environ.get("FUNASR_MODEL", "paraformer-zh")
FUNASR_DEVICE = os.environ.get("FUNASR_DEVICE", "cpu")
MAX_TRANSCRIPTION_FILE_BYTES = 24 * 1024 * 1024
TRANSCRIPTION_CHUNK_SECONDS = 8 * 60
OPENAI_CHUNK_TARGET_MB = 24
OPENAI_CHUNK_MIN_SECONDS = 60
OPENAI_CHUNK_AUDIO_BITRATE = "128k"
EXPORT_AUDIO_BITRATE = "128k"
PAUSE_THRESHOLD_MS = 2000
MIN_AUDIO_FILLER_MS = 250
MAX_AUDIO_FILLER_MS = 1800
LONG_WORD_THRESHOLD_MS = 1200
SUSPICIOUS_WORD_THRESHOLD_MS = 1800
SUSPICIOUS_REGION_PADDING_MS = 800
SUSPICIOUS_REGION_MAX_MS = 6000
GAP_SPEECH_MIN_MS = 500
GAP_REFINEMENT_MIN_WORDS = 2
GAP_REFINEMENT_PADDING_MS = 120
TAIL_HALLUCINATION_GAP_MS = 1800
TAIL_HALLUCINATION_MAX_CLUSTER_MS = 900
TAIL_HALLUCINATION_MAX_WORDS = 4
TAIL_HALLUCINATION_END_WINDOW_MS = 2500
MIN_WORD_DURATION_MS = 90
EXPORT_WORD_START_PAD_MS = 90
EXPORT_WORD_END_PAD_MS = 45
EXPORT_SHORT_WORD_START_PAD_MS = 170
EXPORT_SHORT_WORD_END_PAD_MS = 75
KEEP_TAIL_PROTECT_MS = 60
KEEP_HEAD_PROTECT_MS = 60
EXPORT_LOW_ENERGY_SEARCH_MS = 260
EXPORT_LOW_ENERGY_FRAME_MS = 20
EXPORT_LOW_ENERGY_RMS_THRESHOLD = 280
FRAME_MS = 30
VOICE_RMS_THRESHOLD = 350
VOICE_FRAME_RATIO_THRESHOLD = 0.35
LOW_ZCR_THRESHOLD = 25
MAX_FRAME_GAP_MS = 90
VAD_ALIGNMENT_WINDOW_MS = 220
VERBATIM_PROMPT = (
    "Transcribe verbatim. Preserve repetitions, false starts, restarts, "
    "self-corrections, duplicate phrases, filler words, stutters, unfinished "
    "phrases, and hesitations exactly as spoken. Do not summarize, rewrite, "
    "normalize, clean up, or remove repeated content. Output what was actually "
    "said, even if it sounds redundant or broken."
)


class Word(BaseModel):
    word: str
    start_ms: int
    end_ms: int
    is_filler: bool = False
    deleted: bool = False
    kind: str = "word"


class TranscriptResponse(BaseModel):
    video_id: str
    words: List[Word]
    duration_ms: int


class ExportRequest(BaseModel):
    video_id: str
    deleted_words: List[Word]


def is_filler(word: str) -> bool:
    clean = word.strip().lower().strip(".,!?，。！？")
    normalized = re.sub(r"[~～\-—…\.·]+", "", clean)
    collapsed = re.sub(r"(.)\1{1,}", r"\1", normalized)

    if clean in FILLER_WORDS or normalized in FILLER_WORDS or collapsed in FILLER_WORDS:
        return True

    filler_patterns = [
        r"^(嗯|呃|额|啊|哦|噢|唔|哎|哼)+$",
        r"^(um+|uh+|erm+|emm+|mm+|hmm+|ah+|oh+)+$",
    ]
    return any(re.fullmatch(pattern, normalized) for pattern in filler_patterns)


def insert_pause_markers(words: list["Word"], duration_ms: int) -> list["Word"]:
    if not words:
        return words

    with_pauses: list[Word] = []
    previous_end = 0

    for word in words:
        gap_ms = word.start_ms - previous_end
        if gap_ms >= PAUSE_THRESHOLD_MS:
            with_pauses.append(Word(
                word=f"[pause {(gap_ms / 1000):.1f}s]",
                start_ms=previous_end,
                end_ms=word.start_ms,
                is_filler=True,
                deleted=False,
                kind="pause",
            ))
        with_pauses.append(word)
        previous_end = max(previous_end, word.end_ms)

    return with_pauses


def detect_voiced_ratio(audio_path: Path, start_ms: int, end_ms: int) -> float:
    if end_ms <= start_ms:
        return 0.0

    with wave.open(str(audio_path), "rb") as wav_file:
        frame_rate = wav_file.getframerate()
        sample_width = wav_file.getsampwidth()
        channels = wav_file.getnchannels()
        frame_size = max(1, int(frame_rate * FRAME_MS / 1000))

        start_frame = int(start_ms * frame_rate / 1000)
        end_frame = int(end_ms * frame_rate / 1000)
        wav_file.setpos(min(start_frame, wav_file.getnframes()))
        remaining_frames = max(0, end_frame - start_frame)

        voiced_frames = 0
        total_frames = 0
        while remaining_frames > 0:
            current_frame_count = min(frame_size, remaining_frames)
            raw = wav_file.readframes(current_frame_count)
            if not raw:
                break
            if channels > 1:
                raw = audioop.tomono(raw, sample_width, 0.5, 0.5)
            rms = audioop.rms(raw, sample_width)
            total_frames += 1
            if rms >= VOICE_RMS_THRESHOLD:
                voiced_frames += 1
            remaining_frames -= current_frame_count

    if total_frames == 0:
        return 0.0
    return voiced_frames / total_frames


def detect_low_information_runs(audio_path: Path, start_ms: int, end_ms: int) -> list[tuple[int, int]]:
    if end_ms <= start_ms:
        return []

    runs: list[tuple[int, int]] = []

    with wave.open(str(audio_path), "rb") as wav_file:
        frame_rate = wav_file.getframerate()
        sample_width = wav_file.getsampwidth()
        channels = wav_file.getnchannels()
        frame_size = max(1, int(frame_rate * FRAME_MS / 1000))

        start_frame = int(start_ms * frame_rate / 1000)
        end_frame = int(end_ms * frame_rate / 1000)
        wav_file.setpos(min(start_frame, wav_file.getnframes()))
        remaining_frames = max(0, end_frame - start_frame)

        current_start_ms = None
        last_match_end_ms = None
        frame_index = 0

        while remaining_frames > 0:
            current_frame_count = min(frame_size, remaining_frames)
            raw = wav_file.readframes(current_frame_count)
            if not raw:
                break
            if channels > 1:
                raw = audioop.tomono(raw, sample_width, 0.5, 0.5)

            frame_start_ms = start_ms + frame_index * FRAME_MS
            frame_end_ms = min(end_ms, frame_start_ms + FRAME_MS)
            rms = audioop.rms(raw, sample_width)
            zcr = audioop.cross(raw, sample_width)
            is_low_information_voice = rms >= VOICE_RMS_THRESHOLD and zcr <= LOW_ZCR_THRESHOLD

            if is_low_information_voice:
                if current_start_ms is None:
                    current_start_ms = frame_start_ms
                last_match_end_ms = frame_end_ms
            elif current_start_ms is not None and last_match_end_ms is not None:
                if frame_start_ms - last_match_end_ms <= MAX_FRAME_GAP_MS:
                    last_match_end_ms = frame_end_ms
                else:
                    runs.append((current_start_ms, last_match_end_ms))
                    current_start_ms = None
                    last_match_end_ms = None

            remaining_frames -= current_frame_count
            frame_index += 1

        if current_start_ms is not None and last_match_end_ms is not None:
            runs.append((current_start_ms, last_match_end_ms))

    return [
        (run_start, run_end)
        for run_start, run_end in runs
        if MIN_AUDIO_FILLER_MS <= (run_end - run_start) <= MAX_AUDIO_FILLER_MS
    ]


def insert_audio_filler_markers(words: list["Word"], audio_path: Path, duration_ms: int) -> list["Word"]:
    if not words:
        return words
    if audio_path.suffix.lower() != ".wav":
        return words

    marked_words: list[Word] = []
    candidate_markers: list[Word] = []
    previous_end = 0

    for word in words:
        gap_start = previous_end
        gap_end = word.start_ms
        gap_ms = gap_end - gap_start

        if MIN_AUDIO_FILLER_MS <= gap_ms <= MAX_AUDIO_FILLER_MS:
            voiced_ratio = detect_voiced_ratio(audio_path, gap_start, gap_end)
            if voiced_ratio >= VOICE_FRAME_RATIO_THRESHOLD:
                candidate_markers.append(Word(
                    word=f"[audio filler {(gap_ms / 1000):.1f}s]",
                    start_ms=gap_start,
                    end_ms=gap_end,
                    is_filler=True,
                    deleted=False,
                    kind="audio_filler",
                ))

        word_duration_ms = word.end_ms - word.start_ms
        if word.kind == "word" and not word.is_filler and word_duration_ms >= LONG_WORD_THRESHOLD_MS:
            for run_start, run_end in detect_low_information_runs(audio_path, word.start_ms, word.end_ms):
                candidate_markers.append(Word(
                    word=f"[audio filler {(run_end - run_start) / 1000:.1f}s]",
                    start_ms=run_start,
                    end_ms=run_end,
                    is_filler=True,
                    deleted=False,
                    kind="audio_filler",
                ))

        marked_words.append(word)
        previous_end = max(previous_end, word.end_ms)

    tail_gap_ms = duration_ms - previous_end
    if MIN_AUDIO_FILLER_MS <= tail_gap_ms <= MAX_AUDIO_FILLER_MS:
        voiced_ratio = detect_voiced_ratio(audio_path, previous_end, duration_ms)
        if voiced_ratio >= VOICE_FRAME_RATIO_THRESHOLD:
            candidate_markers.append(Word(
                word=f"[audio filler {(tail_gap_ms / 1000):.1f}s]",
                start_ms=previous_end,
                end_ms=duration_ms,
                is_filler=True,
                deleted=False,
                kind="audio_filler",
            ))

    combined = marked_words + candidate_markers
    combined.sort(key=lambda w: (w.start_ms, 0 if w.kind == "audio_filler" else 1, w.end_ms))

    deduped: list[Word] = []
    for item in combined:
        if deduped and item.kind == "audio_filler" and deduped[-1].kind == "audio_filler":
            overlaps = item.start_ms <= deduped[-1].end_ms + MAX_FRAME_GAP_MS
            if overlaps:
                deduped[-1].end_ms = max(deduped[-1].end_ms, item.end_ms)
                deduped[-1].word = f"[audio filler {(deduped[-1].end_ms - deduped[-1].start_ms) / 1000:.1f}s]"
                continue
        deduped.append(item)

    return deduped


def get_media_duration_ms(path: Path) -> int:
    result = subprocess.run([
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", str(path)
    ], capture_output=True, text=True)
    try:
        info = json.loads(result.stdout)
        return int(float(info["format"]["duration"]) * 1000)
    except Exception:
        return 0


def has_video_stream(path: Path) -> bool:
    result = subprocess.run([
        "ffprobe",
        "-v", "quiet",
        "-select_streams", "v:0",
        "-show_entries", "stream=index",
        "-of", "json",
        str(path),
    ], capture_output=True, text=True)
    try:
        data = json.loads(result.stdout or "{}")
    except Exception:
        return False
    return bool(data.get("streams"))


def run_ffmpeg_export(command_variants: list[list[str]]) -> subprocess.CompletedProcess:
    last_result = None
    for command in command_variants:
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode == 0:
            return result
        last_result = result
    return last_result


@lru_cache(maxsize=1)
def get_silero_vad_model():
    from silero_vad import load_silero_vad

    return load_silero_vad()


def load_audio_for_vad(audio_path: Path):
    import torch

    result = subprocess.run([
        "ffmpeg",
        "-i", str(audio_path),
        "-f", "s16le",
        "-acodec", "pcm_s16le",
        "-ac", "1",
        "-ar", "16000",
        "-",
    ], capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio decode error: {result.stderr.decode('utf-8', errors='ignore')}")

    samples = array.array("h")
    samples.frombytes(result.stdout)
    audio = torch.tensor(samples, dtype=torch.float32) / 32768.0
    return audio


def get_silence_intervals_ms(audio_path: Path, duration_ms: int) -> list[tuple[int, int]]:
    from silero_vad import get_speech_timestamps

    audio = load_audio_for_vad(audio_path)
    speech_timestamps = get_speech_timestamps(
        audio,
        get_silero_vad_model(),
        sampling_rate=16000,
        return_seconds=False,
    )

    silences: list[tuple[int, int]] = []
    cursor_ms = 0
    for segment in speech_timestamps:
        speech_start_ms = int(segment["start"] / 16)
        speech_end_ms = int(segment["end"] / 16)
        if cursor_ms < speech_start_ms:
            silences.append((cursor_ms, speech_start_ms))
        cursor_ms = max(cursor_ms, speech_end_ms)

    if cursor_ms < duration_ms:
        silences.append((cursor_ms, duration_ms))

    return silences


def get_speech_intervals_ms(audio_path: Path) -> list[tuple[int, int]]:
    from silero_vad import get_speech_timestamps

    audio = load_audio_for_vad(audio_path)
    speech_timestamps = get_speech_timestamps(
        audio,
        get_silero_vad_model(),
        sampling_rate=16000,
        return_seconds=False,
    )

    return [
        (int(segment["start"] / 16), int(segment["end"] / 16))
        for segment in speech_timestamps
    ]


def get_gap_speech_regions(
    speech_intervals: list[tuple[int, int]],
    gap_start_ms: int,
    gap_end_ms: int,
) -> list[tuple[int, int]]:
    regions: list[tuple[int, int]] = []
    for speech_start_ms, speech_end_ms in speech_intervals:
        overlap_start = max(gap_start_ms, speech_start_ms)
        overlap_end = min(gap_end_ms, speech_end_ms)
        if overlap_end - overlap_start >= GAP_SPEECH_MIN_MS:
            regions.append((overlap_start, overlap_end))
    return regions


def snap_boundary_to_silence(boundary_ms: int, silences: list[tuple[int, int]], duration_ms: int) -> int:
    search_start = max(0, boundary_ms - VAD_ALIGNMENT_WINDOW_MS)
    search_end = min(duration_ms, boundary_ms + VAD_ALIGNMENT_WINDOW_MS)

    candidates: list[int] = []
    for silence_start, silence_end in silences:
        overlap_start = max(search_start, silence_start)
        overlap_end = min(search_end, silence_end)
        if overlap_start >= overlap_end:
            continue
        candidates.append((overlap_start + overlap_end) // 2)

    if not candidates:
        return boundary_ms

    return min(candidates, key=lambda candidate: abs(candidate - boundary_ms))


def align_deleted_segments_to_silence(
    deleted_words: list[Word], session_words: list[Word], audio_path: Path, duration_ms: int
) -> list[tuple[int, int]]:
    try:
        silences = get_silence_intervals_ms(audio_path, duration_ms)
    except Exception:
        silences = []

    deleted_keys = {
        (item.word, item.start_ms, item.end_ms, item.kind)
        for item in deleted_words
    }
    kept_words = [
        item for item in session_words
        if item.kind == "word" and (item.word, item.start_ms, item.end_ms, item.kind) not in deleted_keys
    ]

    aligned_segments: list[tuple[int, int]] = []
    for item in deleted_words:
        start_ms = item.start_ms
        end_ms = item.end_ms
        raw_start_ms = start_ms
        raw_end_ms = end_ms
        padded_start_ms = start_ms
        padded_end_ms = end_ms
        prev_kept = None
        next_kept = None

        if item.kind == "word":
            word_duration_ms = max(1, item.end_ms - item.start_ms)
            start_pad_ms = max(EXPORT_WORD_START_PAD_MS, int(word_duration_ms * 0.45))
            end_pad_ms = max(EXPORT_WORD_END_PAD_MS, int(word_duration_ms * 0.2))
            if word_duration_ms <= 140 or len(item.word.strip()) <= 1 or item.is_filler:
                start_pad_ms = max(start_pad_ms, EXPORT_SHORT_WORD_START_PAD_MS)
                end_pad_ms = max(end_pad_ms, EXPORT_SHORT_WORD_END_PAD_MS)

            padded_start_ms = max(0, start_ms - start_pad_ms)
            padded_end_ms = min(duration_ms, end_ms + end_pad_ms)
            start_ms = snap_start_boundary_to_low_energy(audio_path, padded_start_ms)
            end_ms = padded_end_ms

            for candidate in kept_words:
                if candidate.end_ms <= item.start_ms:
                    prev_kept = candidate
                    continue
                if candidate.start_ms >= item.end_ms:
                    next_kept = candidate
                    break

            # Keep a small protected margin on adjacent kept words so
            # aggressive padding for deleted short words does not clip
            # the perceptual tail/head of the words we intend to keep.
            if prev_kept is not None:
                start_ms = max(start_ms, prev_kept.end_ms - KEEP_TAIL_PROTECT_MS)
            if next_kept is not None:
                end_ms = min(end_ms, next_kept.start_ms + KEEP_HEAD_PROTECT_MS)

            logger.info(
                "export-boundary token=%s raw=%s->%s padded=%s->%s clamped=%s->%s prev_kept=%s next_kept=%s",
                item.word,
                raw_start_ms,
                raw_end_ms,
                padded_start_ms,
                padded_end_ms,
                start_ms,
                end_ms,
                f"{prev_kept.word}@{prev_kept.start_ms}->{prev_kept.end_ms}" if prev_kept else "-",
                f"{next_kept.word}@{next_kept.start_ms}->{next_kept.end_ms}" if next_kept else "-",
            )

        if silences:
            start_ms = snap_boundary_to_silence(start_ms, silences, duration_ms)
            end_ms = snap_boundary_to_silence(end_ms, silences, duration_ms)

        if item.kind == "word":
            if prev_kept is not None:
                start_ms = max(start_ms, prev_kept.end_ms - KEEP_TAIL_PROTECT_MS)
            if next_kept is not None:
                end_ms = min(end_ms, next_kept.start_ms + KEEP_HEAD_PROTECT_MS)

        start_ms = max(0, min(start_ms, duration_ms))
        end_ms = max(0, min(end_ms, duration_ms))
        if end_ms <= start_ms:
            start_ms = item.start_ms
            end_ms = item.end_ms
        if end_ms > start_ms:
            aligned_segments.append((start_ms, end_ms))

    aligned_segments.sort()

    merged: list[tuple[int, int]] = []
    for start_ms, end_ms in aligned_segments:
        if merged and start_ms <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end_ms))
        else:
            merged.append((start_ms, end_ms))
    return merged


def snap_start_boundary_to_low_energy(audio_path: Path, boundary_ms: int) -> int:
    if audio_path.suffix.lower() != ".wav":
        return boundary_ms

    search_start_ms = max(0, boundary_ms - EXPORT_LOW_ENERGY_SEARCH_MS)
    if search_start_ms >= boundary_ms:
        return boundary_ms

    with wave.open(str(audio_path), "rb") as wav_file:
        frame_rate = wav_file.getframerate()
        sample_width = wav_file.getsampwidth()
        channels = wav_file.getnchannels()
        frame_size = max(1, int(frame_rate * EXPORT_LOW_ENERGY_FRAME_MS / 1000))

        start_frame = int(search_start_ms * frame_rate / 1000)
        end_frame = int(boundary_ms * frame_rate / 1000)
        wav_file.setpos(min(start_frame, wav_file.getnframes()))
        remaining_frames = max(0, end_frame - start_frame)

        best_rms = None
        best_boundary_ms = boundary_ms
        frame_index = 0
        while remaining_frames > 0:
            current_frame_count = min(frame_size, remaining_frames)
            raw = wav_file.readframes(current_frame_count)
            if not raw:
                break
            if channels > 1:
                raw = audioop.tomono(raw, sample_width, 0.5, 0.5)

            rms = audioop.rms(raw, sample_width)
            frame_start_ms = search_start_ms + frame_index * EXPORT_LOW_ENERGY_FRAME_MS
            frame_mid_ms = frame_start_ms + (EXPORT_LOW_ENERGY_FRAME_MS // 2)

            if best_rms is None or rms < best_rms:
                best_rms = rms
                best_boundary_ms = frame_mid_ms

            remaining_frames -= current_frame_count
            frame_index += 1

    if best_rms is None or best_rms > EXPORT_LOW_ENERGY_RMS_THRESHOLD:
        return boundary_ms
    return min(boundary_ms, max(0, best_boundary_ms))


def get_torch_device() -> str:
    if WHISPER_DEVICE:
        return WHISPER_DEVICE

    try:
        import torch
    except ImportError:
        return "cpu"

    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def get_runtime_torch_device(preferred: str) -> str:
    if preferred and preferred != "auto":
        return preferred

    try:
        import torch
    except ImportError:
        return "cpu"

    if torch.cuda.is_available():
        return "cuda:0"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def get_qwen_language() -> str | None:
    language = (QWEN_ASR_LANGUAGE or "").strip()
    if not language or language.lower() in {"auto", "none"}:
        return None

    aliases = {
        "zh": "Chinese",
        "chinese": "Chinese",
        "mandarin": "Chinese",
        "en": "English",
        "english": "English",
        "ja": "Japanese",
        "japanese": "Japanese",
        "ko": "Korean",
        "korean": "Korean",
        "yue": "Cantonese",
        "cantonese": "Cantonese",
    }
    return aliases.get(language.lower(), language)


def get_qwen_dtype():
    try:
        import torch
    except ImportError:
        return None

    if QWEN_ASR_DTYPE and QWEN_ASR_DTYPE != "auto":
        explicit = getattr(torch, QWEN_ASR_DTYPE, None)
        if explicit is None:
            raise HTTPException(status_code=500, detail=f"Unsupported QWEN_ASR_DTYPE: {QWEN_ASR_DTYPE}")
        return explicit

    device = get_runtime_torch_device(QWEN_ASR_DEVICE)
    if device.startswith("cuda"):
        return torch.bfloat16
    return torch.float32


@lru_cache(maxsize=1)
def get_local_whisper_model():
    import whisper

    device = get_torch_device()
    return whisper.load_model(LOCAL_WHISPER_MODEL, device=device)


@lru_cache(maxsize=1)
def get_qwen_asr_model():
    try:
        from qwen_asr import Qwen3ASRModel
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="qwen-asr is not installed. Install qwen-asr in the backend environment to use qwen3_asr_local.",
        ) from exc

    init_kwargs = {
        "dtype": get_qwen_dtype(),
        "device_map": get_runtime_torch_device(QWEN_ASR_DEVICE),
        "max_inference_batch_size": QWEN_ASR_MAX_BATCH_SIZE,
        "max_new_tokens": QWEN_ASR_MAX_NEW_TOKENS,
    }

    if QWEN_ASR_ENABLE_ALIGNER and QWEN_ASR_ALIGNER_MODEL:
        init_kwargs["forced_aligner"] = QWEN_ASR_ALIGNER_MODEL
        init_kwargs["forced_aligner_kwargs"] = {
            "dtype": get_qwen_dtype(),
            "device_map": get_runtime_torch_device(QWEN_ASR_DEVICE),
        }

    try:
        return Qwen3ASRModel.from_pretrained(QWEN_ASR_MODEL, **init_kwargs)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load Qwen3 ASR model '{QWEN_ASR_MODEL}': {exc}",
        ) from exc


def transcribe_audio_file_openai(audio_path: Path):
    with open(audio_path, "rb") as audio_file:
        return client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )


def create_openai_chunk_source(audio_path: Path, output_path: Path) -> Path:
    result = subprocess.run([
        "ffmpeg",
        "-i", str(audio_path),
        "-ac", "1",
        "-ar", "16000",
        "-b:a", OPENAI_CHUNK_AUDIO_BITRATE,
        "-c:a", "aac",
        "-f", "ipod",
        str(output_path),
        "-y",
    ], capture_output=True, text=True)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"ffmpeg compression error: {result.stderr}")
    return output_path


def create_audio_subclip(audio_path: Path, output_path: Path, start_ms: int, end_ms: int) -> Path:
    result = subprocess.run([
        "ffmpeg",
        "-ss", f"{start_ms / 1000:.3f}",
        "-to", f"{end_ms / 1000:.3f}",
        "-i", str(audio_path),
        "-ac", "1",
        "-ar", "16000",
        "-b:a", OPENAI_CHUNK_AUDIO_BITRATE,
        "-c:a", "aac",
        "-f", "ipod",
        str(output_path),
        "-y",
    ], capture_output=True, text=True)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"ffmpeg subclip error: {result.stderr}")
    return output_path


def split_audio_for_openai_chunks(audio_path: Path, chunk_dir: Path) -> list[Path]:
    duration_ms = get_media_duration_ms(audio_path)
    if duration_ms <= 0:
        raise HTTPException(status_code=500, detail="Could not determine audio duration for chunking")

    file_size_bytes = audio_path.stat().st_size
    duration_seconds = duration_ms / 1000
    bitrate_bps = (file_size_bytes * 8) / max(duration_seconds, 1)
    segment_seconds = int((OPENAI_CHUNK_TARGET_MB * 1024 * 1024 * 8) / bitrate_bps) - 1
    segment_seconds = max(segment_seconds, OPENAI_CHUNK_MIN_SECONDS)

    chunk_dir.mkdir(parents=True, exist_ok=True)
    chunk_pattern = chunk_dir / "seg%03d.m4a"
    result = subprocess.run([
        "ffmpeg",
        "-i", str(audio_path),
        "-f", "segment",
        "-segment_time", str(segment_seconds),
        "-c", "copy",
        str(chunk_pattern),
        "-y",
    ], capture_output=True, text=True)

    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"ffmpeg chunking error: {result.stderr}")

    chunks = sorted(chunk_dir.glob("seg*.m4a"))
    if not chunks:
        raise HTTPException(status_code=500, detail="OpenAI chunking produced no files")
    return chunks


def identify_suspicious_regions(words: list["Word"], duration_ms: int) -> list[dict[str, int]]:
    regions: list[dict[str, int]] = []
    for word in words:
        word_duration_ms = word.end_ms - word.start_ms
        if word.kind != "word":
            continue
        if word_duration_ms < SUSPICIOUS_WORD_THRESHOLD_MS:
            continue
        clip_start_ms = max(0, word.start_ms - SUSPICIOUS_REGION_PADDING_MS)
        clip_end_ms = min(duration_ms, word.end_ms + SUSPICIOUS_REGION_PADDING_MS)
        if clip_end_ms - clip_start_ms > SUSPICIOUS_REGION_MAX_MS:
            clip_end_ms = min(duration_ms, clip_start_ms + SUSPICIOUS_REGION_MAX_MS)
        regions.append({
            "replace_start_ms": word.start_ms,
            "replace_end_ms": word.end_ms,
            "clip_start_ms": clip_start_ms,
            "clip_end_ms": clip_end_ms,
        })

    merged: list[dict[str, int]] = []
    for region in sorted(regions, key=lambda item: (item["clip_start_ms"], item["clip_end_ms"])):
        if merged and region["clip_start_ms"] <= merged[-1]["clip_end_ms"]:
            merged[-1]["clip_end_ms"] = max(merged[-1]["clip_end_ms"], region["clip_end_ms"])
            merged[-1]["replace_start_ms"] = min(merged[-1]["replace_start_ms"], region["replace_start_ms"])
            merged[-1]["replace_end_ms"] = max(merged[-1]["replace_end_ms"], region["replace_end_ms"])
        else:
            merged.append(region)
    return merged


def refine_suspicious_regions_with_openai(audio_path: Path, words: list["Word"]) -> list["Word"]:
    duration_ms = get_media_duration_ms(audio_path)
    suspicious_regions = identify_suspicious_regions(words, duration_ms)
    if not suspicious_regions:
        return words

    refined_words = words[:]
    for region in suspicious_regions:
        clip_start_ms = region["clip_start_ms"]
        clip_end_ms = region["clip_end_ms"]
        replace_start_ms = region["replace_start_ms"]
        replace_end_ms = region["replace_end_ms"]
        with tempfile.TemporaryDirectory(prefix="openai_refine_") as tmp_dir:
            clip_path = create_audio_subclip(audio_path, Path(tmp_dir) / "region.m4a", clip_start_ms, clip_end_ms)
            response = transcribe_audio_file_openai(clip_path)
            data = response.model_dump()
            region_words = []
            for item in data.get("words") or []:
                word_text = (item.get("word") or "").strip()
                if not word_text:
                    continue
                region_words.append(Word(
                    word=word_text,
                    start_ms=clip_start_ms + int(float(item["start"]) * 1000),
                    end_ms=clip_start_ms + int(float(item["end"]) * 1000),
                    is_filler=is_filler(word_text),
                    deleted=False,
                ))

        region_words = [
            word for word in region_words
            if word.end_ms > replace_start_ms and word.start_ms < replace_end_ms
        ]
        if len(region_words) <= 1:
            continue

        refined_words = [
            w for w in refined_words
            if w.end_ms <= replace_start_ms or w.start_ms >= replace_end_ms
        ] + region_words
        refined_words.sort(key=lambda w: (w.start_ms, w.end_ms))

    return refined_words


def recover_words_from_speech_gaps(audio_path: Path, words: list["Word"], duration_ms: int) -> list["Word"]:
    try:
        speech_intervals = get_speech_intervals_ms(audio_path)
    except Exception:
        return words

    recovered_words = words[:]
    base_words = sorted(
        [word for word in words if word.kind == "word"],
        key=lambda word: (word.start_ms, word.end_ms),
    )
    if not base_words:
        return words

    gap_ranges: list[tuple[int, int]] = []
    previous_end = 0
    for word in base_words:
        if word.start_ms - previous_end >= PAUSE_THRESHOLD_MS:
            gap_ranges.append((previous_end, word.start_ms))
        previous_end = max(previous_end, word.end_ms)

    if duration_ms - previous_end >= PAUSE_THRESHOLD_MS:
        gap_ranges.append((previous_end, duration_ms))

    for gap_start_ms, gap_end_ms in gap_ranges:
        speech_regions = get_gap_speech_regions(speech_intervals, gap_start_ms, gap_end_ms)
        if not speech_regions:
            continue

        region_start_ms = max(0, speech_regions[0][0] - GAP_REFINEMENT_PADDING_MS)
        region_end_ms = min(duration_ms, speech_regions[-1][1] + GAP_REFINEMENT_PADDING_MS)

        with tempfile.TemporaryDirectory(prefix="gap_refine_") as tmp_dir:
            clip_path = create_audio_subclip(audio_path, Path(tmp_dir) / "gap.m4a", region_start_ms, region_end_ms)
            response = transcribe_audio_file_openai(clip_path)
            data = response.model_dump()

        region_words: list[Word] = []
        for item in data.get("words") or []:
            word_text = (item.get("word") or "").strip()
            if not word_text:
                continue
            region_words.append(Word(
                word=word_text,
                start_ms=region_start_ms + int(float(item["start"]) * 1000),
                end_ms=region_start_ms + int(float(item["end"]) * 1000),
                is_filler=is_filler(word_text),
                deleted=False,
            ))

        region_words = [
            word for word in region_words
            if word.end_ms > gap_start_ms and word.start_ms < gap_end_ms
        ]
        if len(region_words) < GAP_REFINEMENT_MIN_WORDS:
            continue

        recovered_words.extend(region_words)
        recovered_words.sort(key=lambda word: (word.start_ms, word.end_ms))

    deduped: list[Word] = []
    for word in recovered_words:
        if deduped:
            prev = deduped[-1]
            same_text = prev.word == word.word
            close_start = abs(prev.start_ms - word.start_ms) <= 120
            close_end = abs(prev.end_ms - word.end_ms) <= 120
            if prev.kind == word.kind == "word" and same_text and close_start and close_end:
                continue
        deduped.append(word)

    return deduped


def remove_suspicious_tail_words(words: list["Word"], duration_ms: int) -> list["Word"]:
    base_words = [word for word in words if word.kind == "word"]
    if not base_words:
        return words

    tail_words: list[Word] = []
    for word in reversed(base_words):
        if not tail_words:
            tail_words.append(word)
            continue
        next_word = tail_words[-1]
        gap_ms = next_word.start_ms - word.end_ms
        if gap_ms > 180:
            break
        tail_words.append(word)

    tail_words.reverse()
    if not tail_words:
        return words

    cluster_start_ms = tail_words[0].start_ms
    cluster_end_ms = tail_words[-1].end_ms
    previous_end_ms = 0
    for word in base_words:
        if word.start_ms >= cluster_start_ms:
            break
        previous_end_ms = max(previous_end_ms, word.end_ms)

    leading_gap_ms = cluster_start_ms - previous_end_ms
    cluster_duration_ms = cluster_end_ms - cluster_start_ms
    near_end = duration_ms - cluster_start_ms <= TAIL_HALLUCINATION_END_WINDOW_MS

    if (
        leading_gap_ms >= TAIL_HALLUCINATION_GAP_MS
        and cluster_duration_ms <= TAIL_HALLUCINATION_MAX_CLUSTER_MS
        and len(tail_words) <= TAIL_HALLUCINATION_MAX_WORDS
        and near_end
    ):
        tail_ids = {id(word) for word in tail_words}
        return [word for word in words if id(word) not in tail_ids]

    return words


def normalize_zero_length_words(words: list["Word"], duration_ms: int) -> list["Word"]:
    normalized = [word.model_copy(deep=True) for word in words]
    for index, word in enumerate(normalized):
        if word.kind != "word" or word.end_ms > word.start_ms:
            continue

        prev_end_ms = 0
        for prev in reversed(normalized[:index]):
            if prev.kind == "word":
                prev_end_ms = prev.end_ms
                break

        next_start_ms = duration_ms
        next_end_ms = duration_ms
        for nxt in normalized[index + 1:]:
            if nxt.kind == "word":
                next_start_ms = nxt.start_ms
                next_end_ms = nxt.end_ms
                break

        if prev_end_ms < word.start_ms:
            window = min(MIN_WORD_DURATION_MS, word.start_ms - prev_end_ms)
            word.start_ms = max(prev_end_ms, word.start_ms - window)
            word.end_ms = max(word.end_ms, word.start_ms + window)
            word.end_ms = min(word.end_ms, next_end_ms)
        elif next_end_ms > word.start_ms:
            word.end_ms = min(next_end_ms, word.start_ms + MIN_WORD_DURATION_MS)
        else:
            word.end_ms = min(duration_ms, word.start_ms + MIN_WORD_DURATION_MS)

        if word.end_ms <= word.start_ms:
            word.end_ms = min(duration_ms, word.start_ms + MIN_WORD_DURATION_MS)
        if word.end_ms <= word.start_ms and word.start_ms >= MIN_WORD_DURATION_MS:
            word.start_ms -= MIN_WORD_DURATION_MS
            word.end_ms = min(duration_ms, word.start_ms + MIN_WORD_DURATION_MS)

    normalized.sort(key=lambda word: (word.start_ms, word.end_ms))
    return normalized


def transcribe_audio_file_openai_chunked(audio_path: Path, debug_path: Path | None = None) -> list["Word"]:
    with tempfile.TemporaryDirectory(prefix="openai_chunked_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        compressed_path = create_openai_chunk_source(audio_path, tmp_path / "compressed.m4a")
        chunk_paths = split_audio_for_openai_chunks(compressed_path, tmp_path / "segments")

        all_words: list[dict] = []
        all_segments: list[dict] = []
        acc_offset_ms = 0

        for chunk_path in chunk_paths:
            response = transcribe_audio_file_openai(chunk_path)
            data = response.model_dump()

            chunk_words = data.get("words") or []
            chunk_segments = data.get("segments") or []

            for w in chunk_words:
                w["start"] = (w.get("start") or 0.0) + (acc_offset_ms / 1000)
                w["end"] = (w.get("end") or 0.0) + (acc_offset_ms / 1000)
            for s in chunk_segments:
                s["start"] = (s.get("start") or 0.0) + (acc_offset_ms / 1000)
                s["end"] = (s.get("end") or 0.0) + (acc_offset_ms / 1000)

            all_words.extend(chunk_words)
            all_segments.extend(chunk_segments)
            if all_words:
                acc_offset_ms = int(float(all_words[-1].get("end", acc_offset_ms / 1000)) * 1000)

        if debug_path is not None:
            with open(debug_path, "w", encoding="utf-8") as f:
                json.dump({"segments": all_segments, "words": all_words}, f, ensure_ascii=False, indent=2)

        words: list[Word] = []
        for w in all_words:
            word_text = (w.get("word") or "").strip()
            if not word_text:
                continue
            words.append(Word(
                word=word_text,
                start_ms=int(float(w["start"]) * 1000),
                end_ms=int(float(w["end"]) * 1000),
                is_filler=is_filler(word_text),
                deleted=False,
            ))
        words = normalize_zero_length_words(words, get_media_duration_ms(audio_path))
        words = refine_suspicious_regions_with_openai(audio_path, words)
        words = recover_words_from_speech_gaps(audio_path, words, get_media_duration_ms(audio_path))
        return remove_suspicious_tail_words(words, get_media_duration_ms(audio_path))


def transcribe_audio_file_local(audio_path: Path) -> list["Word"]:
    model = get_local_whisper_model()
    result = model.transcribe(
        str(audio_path),
        word_timestamps=True,
        condition_on_previous_text=False,
        temperature=0,
        no_speech_threshold=0.3,
        compression_ratio_threshold=2.4,
        verbose=False,
        fp16=False,
        language=LOCAL_WHISPER_LANGUAGE,
    )

    words: list[Word] = []
    for segment in result.get("segments", []):
        for w in segment.get("words", []) or []:
            word_text = (w.get("word") or "").strip()
            if not word_text:
                continue
            words.append(Word(
                word=word_text,
                start_ms=int(float(w["start"]) * 1000),
                end_ms=int(float(w["end"]) * 1000),
                is_filler=is_filler(word_text),
                deleted=False,
            ))
    return normalize_zero_length_words(words, get_media_duration_ms(audio_path))


def transcribe_audio_file_qwen_local(audio_path: Path, debug_path: Path | None = None) -> list["Word"]:
    model = get_qwen_asr_model()
    language = get_qwen_language()

    try:
        results = model.transcribe(
            audio=str(audio_path),
            language=language,
            return_time_stamps=QWEN_ASR_ENABLE_ALIGNER,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Qwen3 ASR transcription failed: {exc}") from exc

    serializable_results = []
    words: list[Word] = []

    for result in results or []:
        result_text = getattr(result, "text", None)
        result_language = getattr(result, "language", None)
        time_stamps = getattr(result, "time_stamps", None) or []
        serializable_result = {
            "language": result_language,
            "text": result_text,
            "time_stamps": [],
        }

        for item in time_stamps:
            text = getattr(item, "text", None)
            start_time = getattr(item, "start_time", None)
            end_time = getattr(item, "end_time", None)

            if isinstance(item, dict):
                text = item.get("text", text)
                start_time = item.get("start_time", start_time)
                end_time = item.get("end_time", end_time)

            serializable_result["time_stamps"].append({
                "text": text,
                "start_time": start_time,
                "end_time": end_time,
            })

            if text is None or start_time is None or end_time is None:
                continue

            word_text = str(text).strip()
            if not word_text:
                continue

            words.append(Word(
                word=word_text,
                start_ms=int(float(start_time) * 1000),
                end_ms=int(float(end_time) * 1000),
                is_filler=is_filler(word_text),
                deleted=False,
            ))

        serializable_results.append(serializable_result)

    if debug_path is not None:
        with open(debug_path, "w", encoding="utf-8") as f:
            json.dump({"results": serializable_results}, f, ensure_ascii=False, indent=2)

    if not words:
        if not QWEN_ASR_ENABLE_ALIGNER:
            raise HTTPException(
                status_code=500,
                detail="Qwen3 ASR local returned no timestamps. Enable QWEN_ASR_ENABLE_ALIGNER and QWEN_ASR_ALIGNER_MODEL for editing.",
            )
        raise HTTPException(
            status_code=500,
            detail="Qwen3 ASR local returned no word timestamps. Check aligner setup or model compatibility.",
        )

    return normalize_zero_length_words(words, get_media_duration_ms(audio_path))


def split_audio_for_transcription(audio_path: Path, chunk_dir: Path) -> list[Path]:
    chunk_pattern = chunk_dir / "chunk_%03d.mp3"
    result = subprocess.run([
        "ffmpeg",
        "-i", str(audio_path),
        "-f", "segment",
        "-segment_time", str(TRANSCRIPTION_CHUNK_SECONDS),
        "-c:a", "libmp3lame",
        "-b:a", "64k",
        str(chunk_pattern),
        "-y",
    ], capture_output=True, text=True)

    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"ffmpeg chunking error: {result.stderr}")

    chunks = sorted(chunk_dir.glob("chunk_*.mp3"))
    if not chunks:
        raise HTTPException(status_code=500, detail="Audio chunking produced no files")
    return chunks


@lru_cache(maxsize=1)
def get_funasr_model():
    from funasr import AutoModel
    return AutoModel(
        model=FUNASR_MODEL,
        model_revision="v2.0.4",
        vad_model="fsmn-vad",
        vad_model_revision="v2.0.4",
        punc_model="ct-punc",
        punc_model_revision="v2.0.4",
        device=FUNASR_DEVICE,
        disable_update=True,
    )


def transcribe_audio_file_funasr(audio_path: Path, debug_path: Path | None = None) -> list["Word"]:
    model = get_funasr_model()
    result = model.generate(
        input=str(audio_path),
        batch_size_s=300,
        hotword="嗯 额 啊 呃 哦 那个 就是 然后",
    )

    if debug_path is not None:
        with open(debug_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    words: list[Word] = []
    if not result or not result[0].get("timestamp"):
        # Fallback: no timestamps returned — surface an empty list so the
        # caller can raise a meaningful error rather than silently succeeding.
        return normalize_zero_length_words(words, audio_duration_ms)

    timestamps = result[0].get("timestamp", [])
    audio_duration_ms = get_media_duration_ms(audio_path)

    def append_word(token_text: str, start_ms: int, end_ms: int):
        token_text = token_text.strip()
        if not token_text:
            return
        words.append(Word(
            word=token_text,
            start_ms=int(start_ms),
            end_ms=int(end_ms),
            is_filler=is_filler(token_text),
            deleted=False,
        ))

    # Prefer timestamp entries that already contain their own token text.
    # FunASR timestamp formats vary across model versions, so handle the common
    # tuple/list and dict shapes rather than assuming a 1:1 char zip with text.
    consumed_from_timestamps = False
    for item in timestamps:
        if isinstance(item, dict):
            token_text = (
                item.get("text")
                or item.get("word")
                or item.get("token")
                or item.get("char")
                or ""
            )
            start_ms = item.get("start") or item.get("begin") or item.get("start_ms")
            end_ms = item.get("end") or item.get("stop") or item.get("end_ms")
            if token_text and start_ms is not None and end_ms is not None:
                append_word(token_text, start_ms, end_ms)
                consumed_from_timestamps = True
        elif isinstance(item, (list, tuple)) and len(item) >= 3 and isinstance(item[0], str):
            token_text = item[0]
            start_ms = item[1]
            end_ms = item[2]
            append_word(token_text, start_ms, end_ms)
            consumed_from_timestamps = True

    if consumed_from_timestamps:
        return words

    # Fallback: if timestamps are bare [start, end] pairs, align only the
    # number of visible non-space tokens that actually have timestamps.
    text = result[0].get("text", "")
    bare_pairs = [
        item for item in timestamps
        if isinstance(item, (list, tuple)) and len(item) >= 2
    ]
    visible_tokens = [ch for ch in text if ch not in " \t"]
    used_count = min(len(visible_tokens), len(bare_pairs))
    for ch, pair in zip(visible_tokens[:used_count], bare_pairs[:used_count]):
        start_ms, end_ms = pair[0], pair[1]
        if ch in "，。！？、；：\"'「」【】…—":
            continue
        append_word(ch, start_ms, end_ms)

    remaining_tokens = [
        ch for ch in visible_tokens[used_count:]
        if ch not in "，。！？、；：\"'「」【】…—"
    ]
    if remaining_tokens and words and audio_duration_ms > words[-1].end_ms:
        tail_start_ms = words[-1].end_ms
        tail_duration_ms = audio_duration_ms - tail_start_ms
        step_ms = max(1, tail_duration_ms // len(remaining_tokens))
        cursor_ms = tail_start_ms
        for index, ch in enumerate(remaining_tokens):
            next_ms = audio_duration_ms if index == len(remaining_tokens) - 1 else min(audio_duration_ms, cursor_ms + step_ms)
            append_word(ch, cursor_ms, next_ms)
            cursor_ms = next_ms

    return normalize_zero_length_words(words, audio_duration_ms)


def transcribe_audio_with_timestamps(audio_path: Path, debug_path: Path | None = None) -> list["Word"]:
    if TRANSCRIPTION_PROVIDER == "funasr":
        return transcribe_audio_file_funasr(audio_path, debug_path=debug_path)
    if TRANSCRIPTION_PROVIDER == "qwen3_asr_local":
        return transcribe_audio_file_qwen_local(audio_path, debug_path=debug_path)
    if TRANSCRIPTION_PROVIDER == "openai_whisper_chunked":
        return transcribe_audio_file_openai_chunked(audio_path, debug_path=debug_path)
    if TRANSCRIPTION_PROVIDER == "local_whisper":
        return transcribe_audio_file_local(audio_path)

    audio_size = audio_path.stat().st_size
    if audio_size <= MAX_TRANSCRIPTION_FILE_BYTES:
        responses = [(0, transcribe_audio_file_openai(audio_path))]
    else:
        responses = []
        with tempfile.TemporaryDirectory(prefix="audio_chunks_") as tmp_dir:
            offset_ms = 0
            for chunk_path in split_audio_for_transcription(audio_path, Path(tmp_dir)):
                response = transcribe_audio_file_openai(chunk_path)
                responses.append((offset_ms, response))
                offset_ms += get_media_duration_ms(chunk_path)

    words: list[Word] = []
    for offset_ms, response in responses:
        if not hasattr(response, "words") or not response.words:
            continue
        for w in response.words:
            word_text = w.word.strip()
            words.append(Word(
                word=word_text,
                start_ms=offset_ms + int(w.start * 1000),
                end_ms=offset_ms + int(w.end * 1000),
                is_filler=is_filler(word_text),
                deleted=False,
            ))
    return normalize_zero_length_words(words, get_media_duration_ms(audio_path))


@app.post("/transcribe", response_model=TranscriptResponse)
async def transcribe_video(file: UploadFile = File(...)):
    """Upload a video, extract audio, transcribe with word-level timestamps."""

    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    # Save uploaded video
    video_id = tempfile.mktemp(dir="", prefix="vid_").lstrip(os.sep)
    video_ext = Path(file.filename).suffix or ".mp4"
    video_path = UPLOAD_DIR / f"{video_id}{video_ext}"

    async with aiofiles.open(video_path, "wb") as f:
        content = await file.read()
        await f.write(content)

    is_audio_upload = (file.content_type or "").startswith("audio/")
    if is_audio_upload:
        audio_path = video_path
    else:
        audio_path = UPLOAD_DIR / f"{video_id}_audio.wav"
        result = subprocess.run([
            "ffmpeg", "-i", str(video_path),
            "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            str(audio_path), "-y"
        ], capture_output=True, text=True)

        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"ffmpeg error: {result.stderr}")

    duration_ms = get_media_duration_ms(video_path)
    debug_path = UPLOAD_DIR / f"{video_id}_{TRANSCRIPTION_PROVIDER}_raw.json"

    # Transcribe with OpenAI audio API using verbatim prompting and chunking for large files.
    try:
        words = transcribe_audio_with_timestamps(audio_path, debug_path=debug_path)
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="Local Whisper dependencies are not installed correctly.",
        ) from exc
    except openai.AuthenticationError as exc:
        raise HTTPException(
            status_code=401,
            detail="OpenAI API key is invalid. Update OPENAI_API_KEY and try again.",
        ) from exc
    except openai.OpenAIError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"OpenAI transcription failed: {exc}",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Transcription failed: {exc}",
        ) from exc

    if not words:
        raise HTTPException(
            status_code=502,
            detail="Transcription completed but returned no word timestamps.",
        )

    words = insert_audio_filler_markers(words, audio_path, duration_ms)
    words = insert_pause_markers(words, duration_ms)

    # Store transcript alongside video for later export
    transcript_path = UPLOAD_DIR / f"{video_id}.json"
    with open(transcript_path, "w", encoding="utf-8") as f:
        json.dump({
            "video_id": video_id,
            "video_path": str(video_path),
            "audio_path": str(audio_path),
            "duration_ms": duration_ms,
            "words": [w.model_dump() for w in words],
        }, f, ensure_ascii=False, indent=2)

    return TranscriptResponse(
        video_id=video_id,
        words=words,
        duration_ms=duration_ms,
    )


@app.post("/export")
async def export_video(req: ExportRequest):
    """Cut deleted words from video and return the processed file."""

    transcript_path = UPLOAD_DIR / f"{req.video_id}.json"
    if not transcript_path.exists():
        raise HTTPException(status_code=404, detail="Video session not found")

    with open(transcript_path) as f:
        session = json.load(f)

    video_path = session["video_path"]
    audio_path = Path(session.get("audio_path", ""))
    duration_ms = session["duration_ms"]
    session_words = [Word(**item) for item in session.get("words", [])]

    if not Path(video_path).exists():
        raise HTTPException(status_code=404, detail="Video file not found")
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")

    # Build list of segments to DELETE, sorted by start time
    deleted = align_deleted_segments_to_silence(req.deleted_words, session_words, audio_path, duration_ms)

    # Build list of segments to KEEP (inverse of deleted)
    keep_segments = []
    cursor = 0
    for start_ms, end_ms in deleted:
        if cursor < start_ms:
            keep_segments.append((cursor, start_ms))
        cursor = end_ms
    if cursor < duration_ms:
        keep_segments.append((cursor, duration_ms))

    if not keep_segments:
        raise HTTPException(status_code=400, detail="Nothing to keep after all deletions")

    input_path = Path(video_path)
    input_has_video = has_video_stream(input_path)
    if input_has_video:
        output_path = OUTPUT_DIR / f"{req.video_id}_edited.mp4"
        video_trims = []
        audio_trims = []
        for i, (start, end) in enumerate(keep_segments):
            s = start / 1000.0
            e = end / 1000.0
            video_trims.append(f"[0:v]trim=start={s}:end={e},setpts=PTS-STARTPTS[v{i}];")
            audio_trims.append(f"[0:a]atrim=start={s}:end={e},asetpts=PTS-STARTPTS[a{i}];")

        n = len(keep_segments)
        v_labels = "".join(f"[v{i}]" for i in range(n))
        a_labels = "".join(f"[a{i}]" for i in range(n))
        concat = f"{v_labels}concat=n={n}:v=1:a=0[outv];{a_labels}concat=n={n}:v=0:a=1[outa]"
        filter_complex = "".join(video_trims) + "".join(audio_trims) + concat

        result = run_ffmpeg_export([
            [
                "ffmpeg", "-i", video_path,
                "-filter_complex", filter_complex,
                "-map", "[outv]", "-map", "[outa]",
                "-c:v", "h264_videotoolbox",
                "-b:v", "6M",
                "-c:a", "aac",
                "-b:a", EXPORT_AUDIO_BITRATE,
                str(output_path), "-y",
            ],
            [
                "ffmpeg", "-i", video_path,
                "-filter_complex", filter_complex,
                "-map", "[outv]", "-map", "[outa]",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "23",
                "-c:a", "aac",
                "-b:a", EXPORT_AUDIO_BITRATE,
                str(output_path), "-y",
            ],
        ])
        media_type = "video/mp4"
        filename = f"edited_{req.video_id}.mp4"
    else:
        output_path = OUTPUT_DIR / f"{req.video_id}_edited.m4a"
        audio_trims = []
        for i, (start, end) in enumerate(keep_segments):
            s = start / 1000.0
            e = end / 1000.0
            audio_trims.append(f"[0:a]atrim=start={s}:end={e},asetpts=PTS-STARTPTS[a{i}];")

        n = len(keep_segments)
        a_labels = "".join(f"[a{i}]" for i in range(n))
        filter_complex = "".join(audio_trims) + f"{a_labels}concat=n={n}:v=0:a=1[outa]"

        result = run_ffmpeg_export([
            [
                "ffmpeg", "-i", video_path,
                "-filter_complex", filter_complex,
                "-map", "[outa]",
                "-c:a", "aac",
                "-b:a", EXPORT_AUDIO_BITRATE,
                str(output_path), "-y",
            ],
        ])
        media_type = "audio/mp4"
        filename = f"edited_{req.video_id}.m4a"

    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"ffmpeg error: {result.stderr}")

    return FileResponse(
        path=str(output_path),
        media_type=media_type,
        filename=filename,
    )


@app.get("/health")
def health():
    return {"status": "ok"}
