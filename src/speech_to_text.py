"""
speech_to_text.py

Thin wrapper around faster-whisper for Automatic Speech Recognition (ASR).

Design goals
------------
- If faster-whisper is installed and a model is available, use it.
- If it is not installed, fall back gracefully so the rest of the system
  (typed-text mode) still works without any ASR dependency.
- The public interface is intentionally minimal:
      stt = SpeechToText()
      text = stt.transcribe_file("audio.wav")
      text = stt.transcribe_microphone()   # records then transcribes

Future improvements
-------------------
- Add word-level timestamps for disfluency detection.
- Support whisper.cpp via ctypes for fully offline, lower-memory inference.
- Add language detection / multilingual support.
- Add Voice Activity Detection (VAD) pre-filtering to improve accuracy on
  noisy recordings (e.g. use silero-vad before passing to Whisper).
"""

import os
from typing import Optional

# ---------------------------------------------------------------------------
# Try to import faster-whisper; silently degrade if unavailable.
# ---------------------------------------------------------------------------
try:
    from faster_whisper import WhisperModel
    _FASTER_WHISPER_AVAILABLE = True
except ImportError:
    _FASTER_WHISPER_AVAILABLE = False


class SpeechToText:
    """
    Speech-to-text engine backed by faster-whisper.

    Parameters
    ----------
    model_size : str
        Whisper model size: "tiny", "base", "small", "medium", "large-v2", etc.
        Smaller models are faster but less accurate.
        Defaults to "base" which balances speed and accuracy for English.
    device : str
        "cpu" or "cuda".  Defaults to "cpu" for maximum portability.
    compute_type : str
        Quantisation type: "int8", "float16", "float32", etc.
        "int8" is recommended for CPU inference.
    language : str or None
        Force a transcription language (e.g. "en").  None = auto-detect.
    """

    def __init__(
        self,
        model_size: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
        language: Optional[str] = "en",
    ) -> None:
        self.model_size   = model_size
        self.device       = device
        self.compute_type = compute_type
        self.language     = language
        self._model       = None   # loaded lazily on first use

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def transcribe_file(self, path: str) -> str:
        """
        Transcribe a WAV (or any ffmpeg-compatible) audio file.

        Parameters
        ----------
        path : str
            Path to the audio file.

        Returns
        -------
        str
            Transcribed text, or an empty string on failure.

        Raises
        ------
        FileNotFoundError
            If *path* does not exist.
        RuntimeError
            If faster-whisper is not installed.
        """
        if not os.path.exists(path):
            raise FileNotFoundError(f"Audio file not found: {path}")

        if not _FASTER_WHISPER_AVAILABLE:
            raise RuntimeError(
                "faster-whisper is not installed. "
                "Install it with:  pip install faster-whisper\n"
                "Or use typed-text mode (run without --audio flag)."
            )

        model = self._get_model()
        segments, _info = model.transcribe(
            path,
            language=self.language,
            beam_size=5,
            vad_filter=True,        # built-in VAD to skip silence
            vad_parameters={"min_silence_duration_ms": 500},
        )
        text = " ".join(seg.text.strip() for seg in segments)
        return text.strip()

    def transcribe_microphone(self, duration: float = 10.0) -> str:
        """
        Record *duration* seconds from the default microphone, then transcribe.

        Requires pyaudio to be installed:
            pip install pyaudio

        Parameters
        ----------
        duration : float
            Maximum recording time in seconds.

        Returns
        -------
        str
            Transcribed text, or an empty string on failure.
        """
        if not _FASTER_WHISPER_AVAILABLE:
            raise RuntimeError(
                "faster-whisper is not installed. "
                "Cannot transcribe microphone input."
            )

        try:
            from .audio_input import AudioInput
        except ImportError:
            raise RuntimeError("audio_input module is unavailable.")

        audio_input = AudioInput()
        audio_data  = audio_input.record_microphone(duration=duration)

        if audio_data is None:
            return ""

        # Write to a temp file then transcribe
        import tempfile, wave
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            with wave.open(tmp_path, "wb") as wf:
                wf.setnchannels(audio_input.channels)
                wf.setsampwidth(2)   # 16-bit PCM
                wf.setframerate(audio_input.sample_rate)
                wf.writeframes(audio_data)
            return self.transcribe_file(tmp_path)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _get_model(self) -> "WhisperModel":
        """Load the model on first use (lazy initialisation)."""
        if self._model is None:
            print(f"[STT] Loading Whisper model '{self.model_size}' on {self.device} …")
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
            )
            print("[STT] Model loaded.")
        return self._model
