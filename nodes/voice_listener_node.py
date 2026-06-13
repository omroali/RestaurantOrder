#!/usr/bin/env python3
"""
voice_listener_node.py  –  ROS node: always-on microphone + hotword + STT.

Drop this into a ROS package and launch with::

    rosrun restaurant_language_unit voice_listener_node.py

or via the provided launch file::

    roslaunch restaurant_language_unit restaurant_voice.launch
"""

import os
import sys

import rospy

# Ensure the restaurant_language_unit package root is on the Python path so that
# the prototype modules (src/*, voice_listener, order_processor) can be
# imported without modification.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG  = os.path.dirname(_HERE)  # restaurant_language_unit/
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from src.ros_bridge import RosBus
from voice_listener import VoiceListenerNode


def _rosparam_list(name: str, default):
    """Fetch a ROS param that may be a YAML list or comma-separated string."""
    val = rospy.get_param(name, default)
    if isinstance(val, str):
        return [w.strip() for w in val.split(",") if w.strip()]
    if isinstance(val, list):
        return val
    return default


def main() -> None:
    rospy.init_node("voice_listener", anonymous=False)

    # ── private namespace params (~) ────────────────────────────────────
    wake_words = _rosparam_list(
        "~wake_words",
        ["hey tiago", "excuse me", "order please"],
    )
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
    enable_calib   = rospy.get_param("~enable_ambient_calibration", True)
    calib_interval = rospy.get_param("~ambient_calibration_interval", 60.0)
    calib_floor    = rospy.get_param("~calibration_noise_floor_default", None)
    calib_thresh   = rospy.get_param("~calibration_threshold_default", None)

    # ── create the ROS bus ──────────────────────────────────────────────
    bus = RosBus()

    # ── instantiate the existing (transport-agnostic) node ──────────────
    node = VoiceListenerNode(
        bus=bus,
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
        tts_rate=tts_rate,
        tts_volume=tts_volume,
        enable_ambient_calibration=enable_calib,
        ambient_calibration_interval=calib_interval,
        calibration_noise_floor_default=calib_floor,
        calibration_threshold_default=calib_thresh,
    )

    # ── start the streaming transcriber ─────────────────────────────────
    if not node._stt.available:
        rospy.logerr(
            "faster-whisper not installed.  "
            "Install with:  pip install faster-whisper"
        )
        return

    rospy.loginfo(
        f"VoiceListener starting.  Wake words: {node._hotword.wake_words}"
    )

    ok = node._stt.start(on_final=node._on_final)
    if not ok:
        rospy.logerr("Failed to start StreamingTranscriber.")
        return

    rospy.loginfo("VoiceListener listening for wake word …")

    # ── block on ROS spin (replaces the threading.Event loop) ───────────
    try:
        bus.spin()  # rospy.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node._stt.stop()
        rospy.loginfo("VoiceListener stopped.")


if __name__ == "__main__":
    main()
