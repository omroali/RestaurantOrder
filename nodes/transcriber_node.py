#!/usr/bin/env python3
"""
transcriber_node.py  –  Minimal continuous transcription from ROS audio.

Subscribes to a ROS audio topic, performs VAD + faster-whisper STT, and
prints every utterance.  No wake words, no dialogue — just raw transcription.

Usage:
    rosrun restaurant_language_unit transcriber_node.py
    rosrun restaurant_language_unit transcriber_node.py _audio_topic:=/audio/audio _model:=small

Output:
    [You said]: hello I am checking how this is working
    [You said]: can you hear me now
"""

import os, sys, wave, tempfile, threading, time, queue
from collections import deque
from typing import Optional, Callable

import rospy
from audio_common_msgs.msg import AudioData
from std_msgs.msg import String

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG  = os.path.dirname(_HERE)
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from src.streaming_stt import StreamingTranscriber


class MinimalTranscriber(StreamingTranscriber):
    """
    StreamingTranscriber that gets audio from a ROS topic instead of PyAudio.
    Much simpler than the full VoiceListenerROSNode — no bus, no hotword.
    """

    def __init__(
        self,
        audio_topic: str = "/audio/audio",
        channels:    int = 1,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._audio_topic   = audio_topic
        self._channels      = channels
        self._subscription  = None
        self._threshold     = kwargs.get("calibration_threshold_default") or 150.0
        rospy.loginfo(f"[Transcriber] VAD threshold: {self._threshold:.0f}")

    def start(
        self,
        on_final: Optional[Callable[[str], None]] = None,
    ) -> bool:
        if not self.available:
            rospy.logerr("[Transcriber] faster-whisper not installed.")
            return False

        self.on_final = on_final
        self._stop.clear()

        # Clear queue
        while not self._chunk_queue.empty():
            try:
                self._chunk_queue.get_nowait()
            except queue.Empty:
                break

        # Subscribe to ROS audio
        self._subscription = rospy.Subscriber(
            self._audio_topic, AudioData,
            lambda m: self._push_audio(bytes(m.data)),
            queue_size=20,
        )
        rospy.loginfo(f"[Transcriber] Subscribed to {self._audio_topic}")

        # Signal calibrated, start processor thread
        self._calibrated.set()
        self._proc_thread = threading.Thread(
            target=self._process_loop, daemon=True, name="stt-proc"
        )
        self._proc_thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._subscription:
            self._subscription.unregister()
        if self._proc_thread:
            self._proc_thread.join(timeout=3)
        rospy.loginfo("[Transcriber] Stopped.")

    def _push_audio(self, data: bytes) -> None:
        """Receive audio from ROS, downmix if stereo, push to queue."""
        if self._stop.is_set():
            return
        if self._channels == 2:
            import struct
            n = len(data) // 4
            if n > 0:
                mono = bytearray(n * 2)
                for i in range(n):
                    left  = struct.unpack_from('<h', data, i * 4)[0]
                    right = struct.unpack_from('<h', data, i * 4 + 2)[0]
                    struct.pack_into('<h', mono, i * 2, (left + right) // 2)
                data = bytes(mono)
        self.push_chunk(data)


def main():
    rospy.init_node("transcriber", anonymous=False)

    audio_topic  = rospy.get_param("~audio_topic",  "/audio/audio")
    model        = rospy.get_param("~model",         "small")
    language     = rospy.get_param("~language",      "en")
    device       = rospy.get_param("~device",        "cpu")
    compute_type = rospy.get_param("~compute_type",  "int8")
    sample_rate  = rospy.get_param("~sample_rate",   16000)
    channels     = rospy.get_param("~channels",      1)
    silence_dur  = rospy.get_param("~silence_dur",   1.2)
    confirm_del  = rospy.get_param("~confirm_delay", 3.0)
    threshold    = rospy.get_param("~vad_threshold", 200)
    pre_buffer    = rospy.get_param("~pre_buffer_seconds", 1.0)

    # Self-echo prevention: mute VAD while robot speaks.
    # Text-based filtering is also applied as a safety net.
    said_by_robot = ["", 0.0]  # [text, timestamp]

    def _on_robot_speech(msg):
        if msg.data:
            said_by_robot[0] = msg.data.lower()
            said_by_robot[1] = rospy.get_time()
            transcriber.is_robot_speaking = True
        else:
            transcriber.is_robot_speaking = False

    rospy.Subscriber("/robot/speaking", String, _on_robot_speech)

    interruption_pub = rospy.Publisher("/transcriber/interruption", String, queue_size=5)

    pub = rospy.Publisher("/transcriber/text", String, queue_size=10)

    def on_final(text: str):
        text = text.strip()
        if not text:
            return

        # Filter out self-echo: if the transcription matches what
        # the robot just said, ignore it.
        # Filter self-echo only if robot spoke recently (within 5s)
        age = rospy.get_time() - said_by_robot[1]
        if said_by_robot[0] and age < 5.0 and said_by_robot[0] in text.lower():
            rospy.loginfo(f"[Self-echo filtered]: {text}")
            return

        rospy.loginfo(f"[You said]: {text}")

        # If the robot was speaking, this is a barge-in
        if transcriber.is_robot_speaking:
            interruption_pub.publish(String(data=text))
            rospy.loginfo("[Barge-in detected!]")
        else:
            pub.publish(String(data=text))

    transcriber = MinimalTranscriber(
        audio_topic=audio_topic,
        channels=channels,
        model_size=model,
        language=language,
        device=device,
        compute_type=compute_type,
        silence_duration=silence_dur,
        confirmation_delay=confirm_del,
        sample_rate=sample_rate,
        calibration_threshold_default=threshold,
        pre_buffer_seconds=pre_buffer,
    )

    # Eagerly load model to avoid first-utterance delay
    rospy.loginfo(f"Loading Whisper model '{model}' …")
    transcriber._get_model()  # triggers download/load now
    rospy.loginfo("Model loaded.")

    transcriber.start(on_final=on_final)
    rospy.loginfo(f"Transcriber ready. Model: {model}, topic: {audio_topic}")
    rospy.spin()
    transcriber.stop()


if __name__ == "__main__":
    main()
