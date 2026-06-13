#!/usr/bin/env python3
"""
voice_listener_ros_node.py  –  ROS node: TIAGo mic → hotword → STT.

Like voice_listener_node.py but listens to TIAGo's published audio topic
instead of a local PyAudio microphone.  No USB mic required.

Usage::

    rosrun restaurant_language_unit voice_listener_ros_node.py \
        _audio_topic:=/audio/raw
"""

import os
import sys

import rospy

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG  = os.path.dirname(_HERE)
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from src.ros_bridge import RosBus
from src.streaming_stt_ros import StreamingTranscriberROS
from voice_listener import VoiceListenerNode


class VoiceListenerROSNode(VoiceListenerNode):
    """
    VoiceListenerNode that captures audio from a ROS topic instead of
    a local PyAudio microphone.

    Overrides only the StreamingTranscriber creation; everything else
    (hotword, bus, state machine, TTS) is inherited unchanged.
    """

    def __init__(
        self,
        bus,
        audio_topic: str = "/audio/raw",
        threshold: float = None,
        **kwargs,
    ) -> None:
        # Bypass VoiceListenerNode.__init__'s StreamingTranscriber
        # creation by calling the parent of VoiceListenerNode (object)
        # and then manually initialising attributes.
        #
        # We replicate the init logic but swap StreamingTranscriber
        # for StreamingTranscriberROS.

        # ── same as VoiceListenerNode.__init__ ─────────────────────
        self._bus   = bus
        self._state = "IDLE"
        self._stop  = False  # unused in ROS mode; stop via rospy signal

        from src.hotword import HotwordDetector
        self._hotword = HotwordDetector(wake_words=kwargs.pop("wake_words", None))

        tts_rate   = kwargs.pop("tts_rate", 175)
        tts_volume = kwargs.pop("tts_volume", 1.0)

        # ── the ROS-specific STT engine ────────────────────────────
        self._stt = StreamingTranscriberROS(
            audio_topic=audio_topic,
            threshold=threshold,
            **kwargs,
        )

        # ── TTS (same as base) ─────────────────────────────────────
        try:
            import pyttsx3
            self._tts_engine = pyttsx3.init()
            self._tts_engine.setProperty('rate', tts_rate)
            self._tts_engine.setProperty('volume', tts_volume)
        except Exception as e:
            rospy.logwarn(f"[VoiceListener] TTS init failed: {e}")
            self._tts_engine = None

        # ── bus subscriptions (same as base) ───────────────────────
        from src.ipc import TOPIC_ORDER_RESULT, TOPIC_ROBOT_PROMPT
        bus.subscribe(TOPIC_ORDER_RESULT, self._on_order_result)
        bus.subscribe(TOPIC_ROBOT_PROMPT, self._on_robot_prompt)


def _rosparam_list(name: str, default):
    """Fetch a ROS param that may be a YAML list or comma-separated string."""
    val = rospy.get_param(name, default)
    if isinstance(val, str):
        return [w.strip() for w in val.split(",") if w.strip()]
    if isinstance(val, list):
        return val
    return default


def main() -> None:
    rospy.init_node("voice_listener_ros", anonymous=False)

    # ── params ─────────────────────────────────────────────────────
    audio_topic    = rospy.get_param("~audio_topic",    "/audio/raw")
    wake_words     = _rosparam_list("~wake_words",
                     ["hey tiago", "excuse me", "order please"])
    stt_model      = rospy.get_param("~stt_model",      "base")
    language       = rospy.get_param("~language",       "en")
    device         = rospy.get_param("~device",         "cpu")
    compute_type   = rospy.get_param("~compute_type",   "int8")
    silence_dur    = rospy.get_param("~silence_dur",    0.8)
    confirm_delay  = rospy.get_param("~confirm_delay",  2.0)
    sample_rate    = rospy.get_param("~sample_rate",    16000)
    pre_buffer     = rospy.get_param("~pre_buffer_seconds", 0.4)
    max_utterance  = rospy.get_param("~max_utterance_seconds", 10.0)
    interrupt_mult = rospy.get_param("~interrupt_multiplier", 6.0)
    tts_rate       = rospy.get_param("~tts_rate",       175)
    tts_volume     = rospy.get_param("~tts_volume",     1.0)
    threshold      = rospy.get_param("~vad_threshold",   None)
    channels       = rospy.get_param("~channels",       2)
    debug_dir      = rospy.get_param("~debug_dir",      "/tmp/utterances")
    model_dir      = rospy.get_param("~model_dir",      None)

    # ── ROS bus ─────────────────────────────────────────────────────
    bus = RosBus()

    # ── create the ROS-audio-aware node ─────────────────────────────
    node = VoiceListenerROSNode(
        bus=bus,
        audio_topic=audio_topic,
        channels=channels,
        threshold=threshold,
        wake_words=wake_words,
        model_size=stt_model,
        language=language,
        device=device,
        compute_type=compute_type,
        silence_duration=silence_dur,
        confirmation_delay=confirm_delay,
        sample_rate=sample_rate,
        pre_buffer_seconds=pre_buffer,
        max_utterance_seconds=max_utterance,
        interrupt_multiplier=interrupt_mult,
        debug_dir=debug_dir,
        model_dir=model_dir,
        tts_rate=tts_rate,
        tts_volume=tts_volume,
    )

    if not node._stt.available:
        rospy.logerr(
            "faster-whisper not installed. "
            "Install with: pip install faster-whisper"
        )
        return

    rospy.loginfo(
        f"VoiceListener starting.  Audio source: {audio_topic}, "
        f"Wake words: {node._hotword.wake_words}"
    )

    ok = node._stt.start(on_final=node._on_final)
    if not ok:
        rospy.logerr("Failed to start StreamingTranscriberROS.")
        return

    rospy.loginfo("VoiceListener listening for wake word …")

    try:
        bus.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node._stt.stop()
        rospy.loginfo("VoiceListener stopped.")


if __name__ == "__main__":
    main()
