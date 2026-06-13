"""
streaming_stt_ros.py  –  ROS audio adapter for StreamingTranscriber.

Replaces the PyAudio recorder thread with a rospy.Subscriber on a
ROS audio topic (typically /audio/raw, audio_common_msgs/AudioData).

Usage::

    from src.streaming_stt_ros import StreamingTranscriberROS

    stt = StreamingTranscriberROS(
        audio_topic="/audio/raw",
        model_size="base",
        ...
    )
    stt.start(on_final=lambda text: print(f"You said: {text}"))
    rospy.spin()   # subscriber processes audio chunks
    stt.stop()
"""

import threading
from typing import Callable, Optional

import rospy
from audio_common_msgs.msg import AudioData

from src.streaming_stt import StreamingTranscriber


class StreamingTranscriberROS(StreamingTranscriber):
    """
    StreamingTranscriber variant that subscribes to a ROS audio topic
    instead of opening a local PyAudio microphone.

    Parameters
    ----------
    audio_topic : str
        ROS topic carrying audio_common_msgs/AudioData messages.
        Typical values: /audio/raw, /audio/audio_raw, /audio_capture/audio.
    channels : int
        Number of channels in incoming ROS audio.  TIAGo's Andrea
        mic is stereo (2).  Audio is downmixed to mono for VAD/STT.
    model_size, language, device, compute_type, silence_duration,
    confirmation_delay, max_utterance_seconds, pre_buffer_seconds,
    interrupt_multiplier, sample_rate :
        See StreamingTranscriber base class.
    threshold : float or None
        Energy threshold for VAD.  If None (default), a sensible default
        of 500 is used (no ambient calibration — TIAGo's audio chain
        provides pre-processed audio).
    """

    def __init__(
        self,
        audio_topic:      str   = "/audio/raw",
        channels:         int   = 2,
        threshold:        Optional[float] = None,
        debug_dir:        Optional[str] = None,
        model_dir:        Optional[str] = None,
        **kwargs,
    ) -> None:
        super().__init__(debug_dir=debug_dir, model_dir=model_dir, **kwargs)
        self._audio_topic    = audio_topic
        self._channels       = channels
        self._subscription:  Optional[rospy.Subscriber] = None

        # Use the provided threshold or a sensible default.
        # TIAGo's audio is pre-gained; typical RMS values are higher
        # than raw microphone input.
        if threshold is not None:
            self._threshold = float(threshold)
        elif self.calibration_threshold_default is not None:
            self._threshold = self.calibration_threshold_default
        else:
            self._threshold = 500.0

        rospy.loginfo(
            f"[StreamingSTT-ROS] VAD threshold set to {self._threshold:.0f}"
        )

    # ------------------------------------------------------------------
    # Override start() — skip recorder thread, use ROS subscriber instead
    # ------------------------------------------------------------------

    def start(
        self,
        on_partial: Optional[Callable[[str], None]] = None,
        on_final:   Optional[Callable[[str], None]] = None,
    ) -> bool:
        """
        Subscribe to the ROS audio topic and start the processor thread.

        Does NOT open a local microphone — audio comes from the ROS topic.
        Does NOT block on calibration — uses the pre-configured threshold.
        """
        if not self.available:
            rospy.logerr("[StreamingSTT-ROS] faster-whisper not installed.")
            return False

        self.on_partial = on_partial
        self.on_final   = on_final

        self._stop.clear()
        while not self._chunk_queue.empty():
            try:
                self._chunk_queue.get_nowait()
            except queue.Empty:
                break

        # Subscribe to ROS audio (replaces _record_loop thread)
        self._subscription = rospy.Subscriber(
            self._audio_topic,
            AudioData,
            self._on_audio_chunk,
            queue_size=20,
        )
        rospy.loginfo(
            f"[StreamingSTT-ROS] Subscribed to {self._audio_topic}"
        )

        # Signal "calibrated" immediately (no PyAudio calibration needed)
        self._calibrated.set()

        # Start the processor thread (same as base class)
        self._proc_thread = threading.Thread(
            target=self._process_loop, daemon=True, name="stt-processor"
        )
        self._proc_thread.start()
        return True

    def stop(self) -> None:
        """Unsubscribe from ROS audio and stop the processor thread."""
        self._stop.set()
        if self._subscription:
            self._subscription.unregister()
            self._subscription = None
            rospy.loginfo("[StreamingSTT-ROS] Unsubscribed from audio topic.")
        if self._proc_thread:
            self._proc_thread.join(timeout=3)
        print("[StreamingSTT-ROS] Stopped.")

    # ------------------------------------------------------------------
    # ROS subscriber callback
    # ------------------------------------------------------------------

    def _on_audio_chunk(self, msg: AudioData) -> None:
        """
        Receive one AudioData message from ROS, downmix to mono if
        needed, and push to the processing queue.
        """
        if self._stop.is_set():
            return
        data = bytes(msg.data)
        if self._channels == 2:
            data = self._stereo_to_mono(data)
        self.push_chunk(data)

    @staticmethod
    def _stereo_to_mono(data: bytes) -> bytes:
        """Downmix 16-bit stereo PCM to mono by averaging channels."""
        import struct
        n = len(data) // 4
        if n == 0:
            return data
        mono = bytearray(n * 2)
        for i in range(n):
            left  = struct.unpack_from('<h', data, i * 4)[0]
            right = struct.unpack_from('<h', data, i * 4 + 2)[0]
            struct.pack_into('<h', mono, i * 2, (left + right) // 2)
        return bytes(mono)
