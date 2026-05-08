"""
vad.py  –  Voice Activity Detection.

Provides energy-based VAD with noise-floor calibration.
The microphone is opened and closed explicitly so that two nodes can
hand off ownership cleanly (voice_listener owns the mic; order_processor
never opens it directly).

──────────────────────────────────────────────────────────────────────────────
### ROS INTEGRATION ###

In ROS the microphone is owned by a dedicated audio_driver node that
publishes raw audio to a topic (e.g. /audio/raw using audio_common_msgs).
VoiceActivityDetector would then consume chunks from a rospy.Subscriber
callback instead of reading pyaudio directly.

Concretely, replace the pyaudio stream calls with:
    rospy.Subscriber("/audio/raw", AudioData, self._on_audio_chunk)
and buffer incoming chunks in a thread-safe deque.

The open() / close() / calibrate() / record_utterance() interface stays
identical – only the chunk-reading backend changes.

### UPGRADE PATH (VAD engine) ###
To use webrtcvad or silero-vad instead of energy thresholding:
    • pip install webrtcvad   (or torch + silero-vad)
    • Override _is_speech(chunk: bytes) -> bool
    • Energy approach is already extracted there for easy replacement.
──────────────────────────────────────────────────────────────────────────────
"""

import struct
import threading
import time
from collections import deque
from typing import Optional

try:
    import pyaudio
    _PYAUDIO_AVAILABLE = True
except ImportError:
    _PYAUDIO_AVAILABLE = False


class VoiceActivityDetector:
    """
    Reads audio from the microphone, buffers it, and detects speech segments
    using a simple RMS energy threshold.

    Typical usage::

        vad = VoiceActivityDetector()
        vad.open()
        vad.calibrate()            # ~1 s of ambient silence required
        audio = vad.record_utterance()
        vad.close()
    """

    #: Duration of each audio chunk in milliseconds.
    #: 20 ms is the standard for most VAD libraries (webrtcvad, etc.)
    CHUNK_MS: int = 20

    def __init__(self, sample_rate: int = 16_000, channels: int = 1) -> None:
        self.sample_rate  = sample_rate
        self.channels     = channels
        self.chunk_size   = int(sample_rate * self.CHUNK_MS / 1000)

        self._threshold: Optional[float] = None   # set by calibrate()
        self._pa:         Optional[object] = None  # pyaudio.PyAudio
        self._stream:     Optional[object] = None  # pyaudio.Stream

    # ------------------------------------------------------------------
    # Microphone lifecycle
    # ------------------------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self._stream is not None

    def open(self) -> bool:
        """
        Open the default microphone.

        Returns False (instead of raising) when pyaudio is not installed,
        so callers can degrade gracefully.
        """
        if not _PYAUDIO_AVAILABLE:
            print("[VAD] pyaudio is not installed – microphone unavailable.")
            return False
        if self.is_open:
            return True
        self._pa     = pyaudio.PyAudio()
        self._stream = self._pa.open(
            format               = pyaudio.paInt16,
            channels             = self.channels,
            rate                 = self.sample_rate,
            input                = True,
            frames_per_buffer    = self.chunk_size,
        )
        return True

    def close(self) -> None:
        """Release the microphone so another component can open it."""
        if self._stream:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None
        if self._pa:
            self._pa.terminate()
            self._pa = None

    # ------------------------------------------------------------------
    # Calibration
    # ------------------------------------------------------------------

    def calibrate(self, duration: float = 1.0) -> float:
        """
        Sample *duration* seconds of ambient audio and set the energy
        threshold to  max(3 × ambient_rms, 50).

        Returns the threshold that was set.

        ### ROS INTEGRATION ###
        In ROS, request a calibration period via a service call or
        parameter instead of blocking for *duration* seconds here.
        """
        if not self.is_open:
            raise RuntimeError("Call open() before calibrate().")

        n_chunks = max(1, int(duration * 1000 / self.CHUNK_MS))
        energies = [self.rms(self._read_chunk()) for _ in range(n_chunks)]
        ambient  = sum(energies) / len(energies)

        self._threshold = max(ambient * 3.0, 50.0)
        print(f"[VAD] Calibrated – ambient RMS: {ambient:.0f}, "
              f"threshold: {self._threshold:.0f}")
        return self._threshold

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_utterance(
        self,
        silence_duration:   float = 1.5,
        max_duration:       float = 15.0,
        pre_buffer_duration: float = 0.4,
    ) -> Optional[bytes]:
        """
        Block until speech starts, record until silence, return raw PCM.

        A rolling pre-buffer captures audio just before speech is detected
        so the beginning of an utterance is not clipped.

        Returns None if:
          - pyaudio is not available
          - the microphone is not open
          - no speech was detected within *max_duration* seconds

        ### ROS INTEGRATION ###
        Replace direct _read_chunk() calls with reads from a thread-safe
        deque that is populated by the /audio/raw subscriber callback.
        """
        if not self.is_open:
            return None
        if self._threshold is None:
            self.calibrate()

        pre_buf_n  = max(1, int(pre_buffer_duration * 1000 / self.CHUNK_MS))
        silence_n  = max(1, int(silence_duration    * 1000 / self.CHUNK_MS))
        max_n      = max(1, int(max_duration        * 1000 / self.CHUNK_MS))

        pre_buffer:   deque  = deque(maxlen=pre_buf_n)
        recording:    list   = []
        speech_started       = False
        silence_count: int   = 0

        for _ in range(max_n):
            chunk  = self._read_chunk()
            energy = self.rms(chunk)

            if not speech_started:
                pre_buffer.append(chunk)
                if energy > self._threshold:
                    speech_started = True
                    recording.extend(pre_buffer)
                    pre_buffer.clear()
                    print("[VAD] Speech detected")
            else:
                recording.append(chunk)
                if energy <= self._threshold:
                    silence_count += 1
                    if silence_count >= silence_n:
                        break
                else:
                    silence_count = 0

        if not speech_started or not recording:
            return None

        return b"".join(recording)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _read_chunk(self) -> bytes:
        """Read one raw audio chunk from the open stream."""
        return self._stream.read(self.chunk_size, exception_on_overflow=False)

    @staticmethod
    def rms(data: bytes) -> float:
        """Compute the RMS energy of a block of 16-bit little-endian PCM."""
        n = len(data) // 2
        if n == 0:
            return 0.0
        samples = struct.unpack(f"<{n}h", data[: n * 2])
        return (sum(s * s for s in samples) / n) ** 0.5

    def _is_speech(self, chunk: bytes) -> bool:
        """
        Return True if *chunk* is classified as speech.

        ### UPGRADE PATH ###
        Replace this with webrtcvad or silero-vad for more robust detection:

            import webrtcvad
            _vad = webrtcvad.Vad(2)          # aggressiveness 0-3
            return _vad.is_speech(chunk, self.sample_rate)
        """
        return self.rms(chunk) > (self._threshold or 50.0)

    @property
    def threshold(self) -> Optional[float]:
        return self._threshold

    @threshold.setter
    def threshold(self, value: float) -> None:
        self._threshold = float(value)
