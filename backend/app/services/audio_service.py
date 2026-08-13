"""
services/audio_service.py
--------------------------
Audio transcription using the Groq Whisper API.
Supports: WAV, MP3, M4A, MP4 (audio track).
"""

from app.services.llm_service import transcribe_audio as _groq_transcribe


async def transcribe(file_path: str, filename: str) -> str:
    """
    Transcribe an audio/video file using Groq Whisper.

    Args:
        file_path: Absolute or relative path to the saved audio file.
        filename: Original filename (used for context in the output).

    Returns:
        Transcribed text, prefixed with the filename.
    """
    try:
        text = await _groq_transcribe(file_path)
        return f"[Audio Transcript — {filename}]\n\n{text}"
    except Exception as e:
        return f"[Audio Transcript — {filename}]\n[Transcription failed: {e}]"
