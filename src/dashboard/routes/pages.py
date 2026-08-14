# -*- coding: utf-8 -*-
import shutil
import subprocess
import tempfile
import threading
import time
import os
import wave
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import FileResponse
import src.dashboard.core as _core
from src.dashboard.core import *
globals().update({k: v for k, v in _core.__dict__.items() if not k.startswith('__')})

router = APIRouter(tags=["pages"])

VOICE_MODEL_CACHE = {}
VOICE_SYSTEM_CAPTURE = {
    "running": False,
    "started_at": None,
    "segments": [],
    "error": None,
    "thread": None,
    "chunk_index": 0,
    "language": None,
}
VOICE_SYSTEM_LOCK = threading.Lock()
VOICE_SYSTEM_CHUNK_SECONDS = int(os.environ.get("VOICE_SYSTEM_CHUNK_SECONDS", "15"))

@router.get("/", response_class=FileResponse)
def read_root():
    return FileResponse(WEB_DIR / "templates" / "index.html")




@router.get("/finrl", response_class=FileResponse)
def read_finrl_dashboard():
    return FileResponse(WEB_DIR / "templates" / "finrl.html")




@router.get("/vendors", response_class=FileResponse)
def read_vendor_dashboard():
    return FileResponse(WEB_DIR / "templates" / "vendors.html")




@router.get("/futures-signals", response_class=FileResponse)
def read_futures_signals_dashboard():
    return FileResponse(WEB_DIR / "templates" / "futures_signals.html")




@router.get("/env-settings", response_class=FileResponse)
def read_env_settings():
    return FileResponse(WEB_DIR / "templates" / "env_settings.html")


@router.get("/voice-dashboard", response_class=FileResponse)
def read_voice_dashboard():
    return FileResponse(WEB_DIR / "templates" / "voice_dashboard.html")


def _resolve_ffmpeg() -> str | None:
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        return ffmpeg_path

    try:
        import imageio_ffmpeg
    except ModuleNotFoundError:
        return None

    return imageio_ffmpeg.get_ffmpeg_exe()


def _get_whisper_model():
    try:
        from faster_whisper import WhisperModel
    except ModuleNotFoundError as exc:
        raise HTTPException(
            status_code=501,
            detail=(
                "faster-whisper is not installed. Install it with: "
                "python -m pip install faster-whisper"
            ),
        ) from exc

    model_size = os.environ.get("VOICE_WHISPER_MODEL", "small")
    compute_type = os.environ.get("VOICE_WHISPER_COMPUTE_TYPE", "int8")
    cache_key = (model_size, compute_type)
    if cache_key not in VOICE_MODEL_CACHE:
        VOICE_MODEL_CACHE[cache_key] = WhisperModel(model_size, device="cpu", compute_type=compute_type)
    return VOICE_MODEL_CACHE[cache_key]


def _normalize_voice_language(value: str | None) -> str | None:
    language = (value or "").strip().lower()
    if not language or language == "auto":
        return None
    return language


def _transcribe_media_file(media_path: Path, original_name: str, started_at: float, language: str | None = None) -> dict:
    ffmpeg_path = _resolve_ffmpeg()
    if ffmpeg_path is None:
        raise HTTPException(
            status_code=501,
            detail=(
                "ffmpeg is not installed or not on PATH. Install ffmpeg or run: "
                "python -m pip install imageio-ffmpeg"
            ),
        )

    audio_path = media_path.with_suffix(".wav")
    command = [
        ffmpeg_path,
        "-y",
        "-i",
        str(media_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(audio_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise HTTPException(
            status_code=400,
            detail=f"Could not extract audio: {result.stderr[-500:]}",
        )

    language = _normalize_voice_language(language or os.environ.get("VOICE_LANGUAGE", ""))
    model = _get_whisper_model()
    segments_iter, info = model.transcribe(
        str(audio_path),
        language=language,
        vad_filter=True,
        beam_size=5,
    )
    segments = [
        {
            "start": round(segment.start, 2),
            "end": round(segment.end, 2),
            "text": segment.text.strip(),
        }
        for segment in segments_iter
        if segment.text.strip()
    ]

    return {
        "filename": original_name,
        "language": getattr(info, "language", language),
        "duration": round(float(getattr(info, "duration", 0) or 0), 2),
        "elapsed_seconds": round(time.monotonic() - started_at, 2),
        "segments": segments,
        "text": "\n".join(segment["text"] for segment in segments),
    }


def _write_wav(path: Path, samples, sample_rate: int) -> None:
    import numpy as np

    mono = samples
    if getattr(mono, "ndim", 1) > 1:
        mono = mono[:, 0]
    mono = np.clip(mono, -1.0, 1.0)
    pcm = (mono * 32767).astype(np.int16)

    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm.tobytes())


def _system_audio_capture_worker() -> None:
    sample_rate = int(os.environ.get("VOICE_SYSTEM_SAMPLE_RATE", "16000"))
    runtime_dir = trader.RUNTIME_DIR / "voice-dashboard"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    try:
        import soundcard as sc

        speaker = sc.default_speaker()
        microphone = sc.get_microphone(speaker.name, include_loopback=True)
        with microphone.recorder(samplerate=sample_rate, channels=1) as recorder:
            while True:
                with VOICE_SYSTEM_LOCK:
                    if not VOICE_SYSTEM_CAPTURE["running"]:
                        break
                    chunk_index = int(VOICE_SYSTEM_CAPTURE["chunk_index"])
                    VOICE_SYSTEM_CAPTURE["chunk_index"] = chunk_index + 1
                    language = VOICE_SYSTEM_CAPTURE.get("language")

                samples = recorder.record(numframes=sample_rate * VOICE_SYSTEM_CHUNK_SECONDS)
                started_at = time.monotonic()
                with tempfile.TemporaryDirectory(dir=runtime_dir) as tmp:
                    wav_path = Path(tmp) / "system-audio.wav"
                    _write_wav(wav_path, samples, sample_rate)
                    result = _transcribe_media_file(wav_path, "system-audio.wav", started_at, language=language)

                base_seconds = chunk_index * VOICE_SYSTEM_CHUNK_SECONDS
                shifted_segments = [
                    {
                        "start": round(base_seconds + segment["start"], 2),
                        "end": round(base_seconds + segment["end"], 2),
                        "text": segment["text"],
                    }
                    for segment in result["segments"]
                ]
                with VOICE_SYSTEM_LOCK:
                    VOICE_SYSTEM_CAPTURE["segments"].extend(shifted_segments)
                    VOICE_SYSTEM_CAPTURE["error"] = None
    except Exception as exc:
        with VOICE_SYSTEM_LOCK:
            VOICE_SYSTEM_CAPTURE["running"] = False
            VOICE_SYSTEM_CAPTURE["error"] = str(exc)


def _system_audio_state() -> dict:
    with VOICE_SYSTEM_LOCK:
        return {
            "running": bool(VOICE_SYSTEM_CAPTURE["running"]),
            "started_at": VOICE_SYSTEM_CAPTURE["started_at"],
            "segments": list(VOICE_SYSTEM_CAPTURE["segments"]),
            "error": VOICE_SYSTEM_CAPTURE["error"],
            "language": VOICE_SYSTEM_CAPTURE["language"],
            "chunk_seconds": VOICE_SYSTEM_CHUNK_SECONDS,
        }


@router.post("/api/voice/transcribe")
async def transcribe_video_audio(request: Request):
    try:
        form = await request.form()
    except AssertionError as exc:
        raise HTTPException(
            status_code=501,
            detail="python-multipart is not installed. Install it with: python -m pip install python-multipart",
        ) from exc

    file = form.get("file")
    if file is None or not hasattr(file, "filename") or not hasattr(file, "file"):
        raise HTTPException(status_code=400, detail="Upload a video or audio file in the 'file' field.")

    original_name = Path(file.filename or "video").name
    runtime_dir = trader.RUNTIME_DIR / "voice-dashboard"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    started_at = time.monotonic()
    with tempfile.TemporaryDirectory(dir=runtime_dir) as tmp:
        tmp_dir = Path(tmp)
        video_path = tmp_dir / original_name

        with video_path.open("wb") as out:
            shutil.copyfileobj(file.file, out)

        return _transcribe_media_file(
            video_path,
            original_name,
            started_at,
            language=request.query_params.get("language"),
        )


@router.post("/api/voice/transcribe-stream-chunk")
async def transcribe_stream_chunk(request: Request):
    body = await request.body()
    if len(body) < 512:
        return {"filename": "stream.webm", "language": None, "duration": 0, "elapsed_seconds": 0, "segments": [], "text": ""}

    runtime_dir = trader.RUNTIME_DIR / "voice-dashboard"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    started_at = time.monotonic()
    with tempfile.TemporaryDirectory(dir=runtime_dir) as tmp:
        tmp_dir = Path(tmp)
        chunk_path = tmp_dir / "stream.webm"
        chunk_path.write_bytes(body)
        return _transcribe_media_file(
            chunk_path,
            "stream.webm",
            started_at,
            language=request.query_params.get("language"),
        )


@router.post("/api/voice/system-audio/start")
def start_system_audio_capture(language: str = ""):
    try:
        import soundcard  # noqa: F401
    except ModuleNotFoundError as exc:
        raise HTTPException(
            status_code=501,
            detail="soundcard is not installed. Install it with: python -m pip install soundcard",
        ) from exc

    with VOICE_SYSTEM_LOCK:
        if VOICE_SYSTEM_CAPTURE["running"]:
            already_running = True
        else:
            already_running = False
            VOICE_SYSTEM_CAPTURE.update(
                {
                    "running": True,
                    "started_at": time.time(),
                    "segments": [],
                    "error": None,
                    "chunk_index": 0,
                    "language": _normalize_voice_language(language),
                }
            )
            thread = threading.Thread(target=_system_audio_capture_worker, daemon=True)
            VOICE_SYSTEM_CAPTURE["thread"] = thread
            thread.start()

    if already_running:
        return _system_audio_state()
    return _system_audio_state()


@router.post("/api/voice/system-audio/stop")
def stop_system_audio_capture():
    with VOICE_SYSTEM_LOCK:
        VOICE_SYSTEM_CAPTURE["running"] = False
    return _system_audio_state()


@router.get("/api/voice/system-audio/status")
def system_audio_capture_status():
    return _system_audio_state()
