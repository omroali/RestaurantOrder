"""
audio_input.py

Handles microphone recording and audio file I/O.

This module is entirely optional: if pyaudio is not installed, only WAV
file loading is available and microphone recording gracefully fails with an
informative message.

Supported operations
--------------------
    audio_input = AudioInput()
    raw_bytes   = audio_input.record_microphone(duration=8.0)
    raw_bytes   = audio_input.load_wav_file("sample_order.wav")

The returned bytes are raw 16-bit little-endian PCM frames at the configured
sample_rate so they can be written directly to a WAV file or passed to the
STT engine.

Future improvements
-------------------
- Integrate a proper VAD (e.g. webrtcvad or silero-vad) to stop recording
  automatically when the user stops speaking, rather than using a fixed timeout.
- Add noise reduction pre-processing (noisereduce library).
- Support stereo input and channel mixing.
"""

import wave
from typing import Optional


# ---------------------------------------------------------------------------
# Try to import pyaudio; degrade gracefully if unavailable.
# ---------------------------------------------------------------------------
try:
    import pyaudio
    _PYAUDIO_AVAILABLE = True
except ImportError:
    _PYAUDIO_AVAILABLE = False


class AudioInput:
    """
    Records audio from the microphone or loads a WAV file.

    Parameters
    ----------
    sample_rate : int
        Sample rate in Hz.  Whisper expects 16000 Hz.
    channels : int
        Number of channels.  1 = mono (required by Whisper).
    chunk_size : int
        Frames per read buffer.  512 – 1024 works well in practice.
    """

    def __init__(
        self,
        sample_rate: int = 16_000,
        channels: int = 1,
        chunk_size: int = 512,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels    = channels
        self.chunk_size  = chunk_size

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def record_microphone(self, duration: float = 10.0) -> Optional[bytes]:
        """
        Record audio from the default microphone for up to *duration* seconds.

        Parameters
        ----------
        duration : float
            Maximum recording time in seconds.

        Returns
        -------
        bytes or None
            Raw 16-bit PCM audio data, or None if pyaudio is unavailable
            or recording fails.

        Notes
        -----
        Recording stops after *duration* seconds.  A proper VAD implementation
        would stop earlier when the user falls silent – see Future improvements.
        """
        if not _PYAUDIO_AVAILABLE:
            print(
                "[AudioInput] pyaudio is not installed.\n"
                "             Install it with:  pip install pyaudio\n"
                "             Microphone input is unavailable."
            )
            return None

        pa = pyaudio.PyAudio()
        frames = []

        try:
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                frames_per_buffer=self.chunk_size,
            )

            total_chunks = int(self.sample_rate / self.chunk_size * duration)
            print(f"[AudioInput] Recording for up to {duration:.0f} seconds … (speak now)")

            for _ in range(total_chunks):
                data = stream.read(self.chunk_size, exception_on_overflow=False)
                frames.append(data)

            print("[AudioInput] Recording complete.")
            stream.stop_stream()
            stream.close()

        except Exception as exc:                          # noqa: BLE001
            print(f"[AudioInput] Recording failed: {exc}")
            return None
        finally:
            pa.terminate()

        return b"".join(frames)
