"""
streaming_stt.py  –  Continuous speech transcription.

Two-thread design
-----------------
The recorder and transcriber are deliberately separated so that audio is
never dropped while Whisper is running (the previous single-thread design
lost chunks whenever the base model took >100 ms to transcribe).

    Thread 1 – recorder  (lightweight, never blocks):
        pyaudio → 20 ms chunks → queue  (unbounded, drops oldest if full)

Thread 2 – processor (can be slow during Whisper):
        queue → energy VAD → speech buffer
                                  │
                        silence ≥ silence_duration
                                  │
                    wait confirmation_delay more
                                  │
                             Whisper (beam=5)
                                  │
                            on_final(text)

Visual feedback while the user speaks is provided by printing
[Speaking…] / [Transcribing…] inline rather than running Whisper on
partial audio (which causes hallucinations, especially on the tiny model).

──────────────────────────────────────────────────────────────────────────
### ROS INTEGRATION ###

In ROS the audio comes from an audio_driver node publishing
/audio/raw (audio_common_msgs/AudioData).  Adapt as follows:

  1. Remove _record_loop() and the recorder thread entirely.
  2. Add push_chunk(chunk: bytes) which puts the chunk onto self._chunk_queue.
  3. Subscribe the ROS audio callback to push_chunk():
         rospy.Subscriber("/audio/raw", AudioData,
                          lambda m: self.push_chunk(bytes(m.data)))
  4. Call start() without the recorder thread (just the processor thread).

The on_final callback and the rest of the interface stay identical.

### UPGRADE PATH ###
For true sub-word streaming, replace _process_loop with the sliding-window
approach from github.com/ufal/whisper_streaming.  The two-thread scaffold
here is already the right foundation for that.
──────────────────────────────────────────────────────────────────────────
"""

import os
import queue
import tempfile
import threading
import time
import wave
from collections import deque
from typing import Callable, Optional

try:
    from faster_whisper import WhisperModel
    _FW_AVAILABLE = True
except ImportError:
    _FW_AVAILABLE = False

from .vad import VoiceActivityDetector


class StreamingTranscriber:
    """
    Continuous microphone transcription.

    Emits on_final(text) once per spoken utterance, after silence is detected.

    Parameters
    ----------
    model_size       : str    – faster-whisper model ("tiny","base","small",…)
    language         : str    – force language code ("en")
    silence_duration : float  – seconds of silence before confirmation starts
    confirmation_delay : float – extra silence to wait before finalizing
    sample_rate      : int    – audio sample rate (must match pyaudio stream)
    """

    # Maximum number of 20 ms chunks to hold in the audio queue.
    # 3000 × 20 ms = 60 s – enough to absorb the slowest Whisper run.
    _QUEUE_MAXSIZE = 3_000

    def __init__(
        self,
        model_size:       str   = "base",
        language:         str   = "en",
        device:           str   = "cpu",
        compute_type:     str   = "int8",
        silence_duration: float = 0.8,
        confirmation_delay: float = 2.0,
        max_utterance_seconds: float = 10.0,
        pre_buffer_seconds: float = 0.4,
        interrupt_multiplier: float = 6.0,
        enable_ambient_calibration: bool = True,
        ambient_calibration_interval: float = 60.0,
        calibration_noise_floor_default: Optional[float] = None,
        calibration_threshold_default: Optional[float] = None,
        queue_maxsize: int = _QUEUE_MAXSIZE,
        # partial_interval is accepted for API compatibility but no longer used
        partial_interval: float = 0.3,
        sample_rate:      int   = 16_000,
        debug_dir:        Optional[str] = None,
        model_dir:        Optional[str] = None,
    ) -> None:
        self.model_size       = model_size
        self.language         = language
        self.device           = device
        self.compute_type     = compute_type
        self.silence_duration = silence_duration
        self.confirmation_delay = max(0.0, confirmation_delay)
        self.max_utterance_seconds = max(1.0, max_utterance_seconds)
        self.pre_buffer_seconds = max(0.0, pre_buffer_seconds)
        self.sample_rate      = sample_rate
        self._debug_dir       = debug_dir
        self._model_dir       = model_dir
        self.enable_ambient_calibration = enable_ambient_calibration
        self.ambient_calibration_interval = max(1.0, ambient_calibration_interval)
        self.calibration_noise_floor_default = calibration_noise_floor_default
        self.calibration_threshold_default = calibration_threshold_default

        self._model:      Optional["WhisperModel"] = None
        self._stop        = threading.Event()
        self._calibrated  = threading.Event()
        self._threshold:  Optional[float] = None
        self._chunk_queue: queue.Queue = queue.Queue(maxsize=max(1, queue_maxsize))

        self._rec_thread:  Optional[threading.Thread] = None
        self._proc_thread: Optional[threading.Thread] = None
        self._last_calibration_time: float = 0.0

        self.on_partial: Optional[Callable[[str], None]] = None   # kept for API compat
        self.on_final:   Optional[Callable[[str], None]] = None

        # Barge-in / Interruption handling
        self.is_robot_speaking = False
        self.interrupt_multiplier = interrupt_multiplier   # Scale threshold when robot speaks

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def start(
        self,
        on_partial: Optional[Callable[[str], None]] = None,
        on_final:   Optional[Callable[[str], None]] = None,
    ) -> bool:
        """
        Start the recorder and processor threads.

        Returns False if pyaudio or faster-whisper is unavailable.
        Blocks briefly (~1 s) while the noise floor is calibrated.
        """
        if not _FW_AVAILABLE:
            print("[StreamingSTT] faster-whisper not installed.")
            return False

        self.on_partial = on_partial
        self.on_final   = on_final

        self._stop.clear()
        self._calibrated.clear()
        while not self._chunk_queue.empty():
            try:
                self._chunk_queue.get_nowait()
            except queue.Empty:
                break

        # Thread 1 – recorder
        self._rec_thread = threading.Thread(
            target=self._record_loop, daemon=True, name="stt-recorder"
        )
        self._rec_thread.start()

        # Wait for calibration before starting the processor
        if not self._calibrated.wait(timeout=6.0):
            print("[StreamingSTT] Calibration timed out.")
            self._stop.set()
            return False

        # Thread 2 – processor
        self._proc_thread = threading.Thread(
            target=self._process_loop, daemon=True, name="stt-processor"
        )
        self._proc_thread.start()
        return True

    def stop(self) -> None:
        """Stop both threads and release the microphone."""
        self._stop.set()
        if self._rec_thread:
            self._rec_thread.join(timeout=2)
        if self._proc_thread:
            self._proc_thread.join(timeout=3)
        print("[StreamingSTT] Stopped.")

    @property
    def available(self) -> bool:
        return _FW_AVAILABLE

    # ------------------------------------------------------------------
    ### ROS INTEGRATION – push_chunk() entry point (replaces recorder thread)
    # ------------------------------------------------------------------

    def push_chunk(self, chunk: bytes) -> None:
        """
        Feed one raw PCM chunk directly into the processing queue.

        Use this in ROS instead of the internal recorder thread:
            rospy.Subscriber("/audio/raw", AudioData,
                             lambda m: self.push_chunk(bytes(m.data)))

        If the queue is full the oldest chunk is dropped to make room.
        """
        try:
            self._chunk_queue.put_nowait(chunk)
        except queue.Full:
            try:
                self._chunk_queue.get_nowait()   # drop oldest
            except queue.Empty:
                pass
            try:
                self._chunk_queue.put_nowait(chunk)
            except queue.Full:
                pass

    # ------------------------------------------------------------------
    # Thread 1 – recorder  (owns the mic, never blocks on Whisper)
    # ------------------------------------------------------------------

    def _record_loop(self) -> None:
        """
        Open the microphone, calibrate, then push chunks to the queue forever.

        This thread does NO transcription – it just reads audio as fast as
        possible so pyaudio's internal buffer never overflows.
        """
        vad = VoiceActivityDetector(sample_rate=self.sample_rate)
        if not vad.open():
            print("[StreamingSTT] Cannot open microphone – recorder thread exiting.")
            self._calibrated.set()   # unblock start() to avoid deadlock
            return

        # Use default calibration if provided, otherwise calibrate fresh
        if self.calibration_threshold_default is not None:
            self._threshold = self.calibration_threshold_default
            print(f"[Calibration] Using provided threshold default: {self._threshold:.1f}")
        else:
            vad.calibrate()
            self._threshold = vad.threshold
            print(f"[Calibration] Initial ambient noise floor calibrated at threshold: {self._threshold:.1f}")

        self._calibrated.set()       # signal processor thread to start

        try:
            while not self._stop.is_set():
                chunk = vad._read_chunk()
                self.push_chunk(chunk)
        finally:
            vad.close()

    # ------------------------------------------------------------------
    # Thread 2 – processor  (VAD + Whisper; can be slow)
    # ------------------------------------------------------------------

    def _process_loop(self) -> None:
        """
        Drain the audio queue, run VAD, and call Whisper when speech ends.

        Runs independently of the recorder thread so transcription latency
        never causes audio loss.
        """
        CHUNK_MS  = VoiceActivityDetector.CHUNK_MS
        SILENCE_N = max(1, int(self.silence_duration * 1000 / CHUNK_MS))
        CONFIRM_N = max(1, int(self.confirmation_delay * 1000 / CHUNK_MS))
        MAX_N     = max(1, int(self.max_utterance_seconds * 1000 / CHUNK_MS))
        PRE_N     = max(1, int(self.pre_buffer_seconds * 1000 / CHUNK_MS))

        threshold = self._threshold or 50.0
        # Use default noise floor if provided, otherwise derive from threshold
        if self.calibration_noise_floor_default is not None:
            noise_floor = self.calibration_noise_floor_default
        else:
            noise_floor = (threshold / 3.0) if self._threshold else None
        NOISE_ALPHA = 0.05

        def _update_noise_floor(energy: float) -> None:
            nonlocal noise_floor, threshold
            if noise_floor is None:
                noise_floor = energy
            else:
                noise_floor = (1.0 - NOISE_ALPHA) * noise_floor + NOISE_ALPHA * energy
            threshold = max(noise_floor * 3.0, 50.0)

        pre_buf:       deque = deque(maxlen=PRE_N)
        speech_buf:    list  = []
        speech_active: bool  = False
        silence_count: int   = 0
        confirm_count: int   = 0

        while not self._stop.is_set():
            try:
                chunk = self._chunk_queue.get(timeout=0.05)
            except queue.Empty:
                continue

            energy = VoiceActivityDetector.rms(chunk)

            # If robot is speaking, discard audio to prevent self-echo
            # Don't just raise threshold - completely skip speech detection
            if self.is_robot_speaking:
                pre_buf.append(chunk)  # Keep pre-buffer for when robot stops
                continue

            # Determine effective threshold for normal operation
            effective_threshold = threshold

            if not speech_active:
                pre_buf.append(chunk)
                _update_noise_floor(energy)

                # Periodic ambient calibration during idle
                if self.enable_ambient_calibration:
                    current_time = time.time()
                    if current_time - self._last_calibration_time >= self.ambient_calibration_interval:
                        self._last_calibration_time = current_time
                        # Recalibrate by resetting noise floor to current energy
                        # This allows the system to adapt to changing ambient conditions
                        old_threshold = threshold
                        noise_floor = energy
                        threshold = max(noise_floor * 3.0, 50.0)
                        print(f"[Calibration] Ambient recalibration: noise_floor={noise_floor:.1f}, threshold={threshold:.1f} (was {old_threshold:.1f})")

                if energy > effective_threshold:
                    speech_active = True
                    speech_buf    = list(pre_buf)
                    silence_count = 0
                    confirm_count = 0
                    print("\n[Speaking…] ", end="", flush=True)
            else:
                speech_buf.append(chunk)

                # Trim so we never accumulate more than MAX_N chunks
                if len(speech_buf) > MAX_N:
                    speech_buf = speech_buf[-MAX_N:]

                if energy <= effective_threshold:
                    _update_noise_floor(energy)
                    silence_count += 1
                    if silence_count >= SILENCE_N:
                        confirm_count += 1
                        if confirm_count == 1:
                            print("[Pausing…] ", end="", flush=True)
                    else:
                        confirm_count = 0

                    if confirm_count >= CONFIRM_N:
                        # ── Speech ended: transcribe ──────────────────────
                        print("[Transcribing…]", flush=True)
                        audio = b"".join(speech_buf)
                        if self._debug_dir:
                            import os as _os
                            _os.makedirs(self._debug_dir, exist_ok=True)
                            import wave as _wave
                            with _wave.open(_os.path.join(self._debug_dir, "last_utterance.wav"), "wb") as _wf:
                                _wf.setnchannels(1)
                                _wf.setsampwidth(2)
                                _wf.setframerate(self.sample_rate)
                                _wf.writeframes(audio)
                        text  = self._transcribe(audio)
                        if text:
                            if self.on_final:
                                self.on_final(text)
                        speech_buf    = []
                        speech_active = False
                        silence_count = 0
                        confirm_count = 0
                else:
                    silence_count = 0
                    confirm_count = 0

    # ------------------------------------------------------------------
    # Transcription
    # ------------------------------------------------------------------

    def _transcribe(self, raw_pcm: bytes) -> str:
        """
        Write *raw_pcm* to a temp WAV file and transcribe with Whisper.

        Always uses full-quality settings (beam_size=5, vad_filter=True).
        Partial/streaming transcription has been intentionally removed:
        Whisper hallucinates heavily on incomplete audio buffers.
        """
        if not raw_pcm:
            return ""

        model = self._get_model()

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            path = tmp.name

        try:
            with wave.open(path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.sample_rate)
                wf.writeframes(raw_pcm)

            segments, _ = model.transcribe(
                path,
                language           = self.language,
                beam_size          = 5,
                best_of            = 5,
                vad_filter         = True,
                without_timestamps = True,
            )
            return " ".join(s.text.strip() for s in segments).strip()

        except Exception:           # noqa: BLE001
            return ""
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def _get_model(self) -> "WhisperModel":
        """Lazy-load the Whisper model (shared by all transcription calls)."""
        if self._model is None:
            print(f"\n[StreamingSTT] Loading Whisper '{self.model_size}' …")
            self._model = WhisperModel(
                self.model_size,
                device       = self.device,
                compute_type = self.compute_type,
                download_root = self._model_dir,
            )
            print("[StreamingSTT] Model ready.")
        return self._model
