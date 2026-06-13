#!/usr/bin/env python3
"""
run_voice.py  –  Prototype launcher: runs both voice nodes in one process.

Creates a shared QueueBus and starts VoiceListenerNode and OrderProcessorNode
as threads.  This file is NOT needed in ROS – use a .launch file instead.

──────────────────────────────────────────────────────────────────────────────
### ROS INTEGRATION ###

Replace this file with a roslaunch .launch file, e.g.
restaurant_voice.launch:

    <launch>
        <arg name="wake_words"    default="hey tiago,excuse me,order please"/>
        <arg name="stt_model"     default="base"/>
        <arg name="silence_dur"   default="0.6"/>
        <arg name="confirm_delay" default="2.0"/>
        <arg name="max_listen"    default="15.0"/>

        <!-- Always-on listener: microphone + hotword + transcription -->
        <node name="voice_listener" pkg="restaurant_language_unit"
              type="voice_listener_node.py" output="screen">
            <param name="wake_words"    value="$(arg wake_words)"/>
            <param name="hotword_model" value="tiny"/>
            <param name="stt_model"     value="$(arg stt_model)"/>
            <param name="silence_dur"   value="$(arg silence_dur)"/>
            <param name="confirm_delay" value="$(arg confirm_delay)"/>
            <param name="max_listen"    value="$(arg max_listen)"/>
        </node>

        <!-- On-demand processor: dialogue + JSON output -->
        <node name="order_processor" pkg="restaurant_language_unit"
              type="order_processor_node.py" output="screen">
            <param name="stt_model" value="$(arg stt_model)"/>
            <rosparam file="$(find restaurant_language_unit)/config/menu.yaml"/>
        </node>
    </launch>

Run with:
    roslaunch restaurant_language_unit restaurant_voice.launch
    roslaunch restaurant_language_unit restaurant_voice.launch stt_model:=small
──────────────────────────────────────────────────────────────────────────────

Usage (prototype):
    python run_voice.py
    python run_voice.py --wake-words "hey tiago" "order please"
    python run_voice.py --stt-model small --silence 2.0
    python run_voice.py --silence 0.6 --confirm-delay 2.0
    python run_voice.py --list-topics       # print bus topic names and exit
"""

import argparse
import json
import os
import sys
import threading

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is expected to be available
    yaml = None

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from src.ipc import (
    QueueBus,
    TOPIC_ORDER_RESULT,
    TOPIC_ROBOT_PROMPT,
    TOPIC_TRANSCRIPT,
    TOPIC_WAKE_WORD,
    SVC_START_ORDER,
    SVC_CANCEL_ORDER,
)
from order_processor import OrderProcessorNode
from voice_listener import VoiceListenerNode

DEFAULT_SPEECH_CONFIG = {
    "wake_words": ["hey tiago", "excuse me", "order please"],
    "stt_model": "base",
    "language": "en",
    "device": "cpu",
    "compute_type": "int8",
    "silence_dur": 0.6,
    "confirm_delay": 2.0,
    "sample_rate": 16000,
    "pre_buffer_seconds": 0.4,
    "max_utterance_seconds": 10.0,
    "interrupt_multiplier": 6.0,
    "enable_ambient_calibration": True,
    "ambient_calibration_interval": 60.0,
    "calibration_noise_floor_default": None,
    "calibration_threshold_default": None,
    "tts_rate": 175,
    "tts_volume": 1.0,
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_menu(path: str) -> list:
    if not os.path.exists(path):
        print(f"[run_voice] ERROR: menu not found at {path}")
        sys.exit(1)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _merge_config(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_config(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_speech_config(path: str) -> dict:
    if not path:
        return dict(DEFAULT_SPEECH_CONFIG)
    if yaml is None:
        print("[run_voice] ERROR: PyYAML is required to read speech config.")
        sys.exit(1)
    if not os.path.exists(path):
        return dict(DEFAULT_SPEECH_CONFIG)
    with open(path, encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}
    if not isinstance(loaded, dict):
        print(f"[run_voice] ERROR: speech config must be a mapping: {path}")
        sys.exit(1)
    return _merge_config(DEFAULT_SPEECH_CONFIG, loaded)


def _check_deps() -> bool:
    """Warn about missing optional dependencies. Returns True if all present."""
    ok = True
    try:
        import pyaudio        # noqa: F401
    except ImportError:
        print("[run_voice] WARNING: pyaudio not installed – microphone unavailable.")
        print("           Install: sudo pacman -S python-pyaudio  OR  "
              "pip install pyaudio --break-system-packages")
        ok = False
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        print("[run_voice] WARNING: faster-whisper not installed – STT unavailable.")
        print("           Install: pip install faster-whisper --break-system-packages")
        ok = False
    return ok


def _print_topics() -> None:
    print("Bus topics (map to ROS topic/service names):")
    print(f"  Topics (pub/sub):")
    print(f"    {TOPIC_WAKE_WORD:<40} wake word detected")
    print(f"    {TOPIC_TRANSCRIPT:<40} user utterance text")
    print(f"    {TOPIC_ROBOT_PROMPT:<40} robot response text")
    print(f"    {TOPIC_ORDER_RESULT:<40} final confirmed JSON")
    print(f"  Services (req/resp):")
    print(f"    {SVC_START_ORDER:<40} start a new session")
    print(f"    {SVC_CANCEL_ORDER:<40} abort active session")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument(
        "--speech-config",
        default=os.path.join(_HERE, "config", "speech.yaml"),
        metavar="PATH",
        help="Path to the speech YAML config (default: config/speech.yaml).",
    )
    pre_args, _ = pre.parse_known_args()
    speech_config = _load_speech_config(pre_args.speech_config)

    parser = argparse.ArgumentParser(
        description="Restaurant Ordering Robot – live voice mode (prototype launcher)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
        parents=[pre],
    )
    parser.add_argument(
        "--wake-words", nargs="+", default=None, metavar="PHRASE",
        help="Wake words to listen for (default: speech config).",
    )
    parser.add_argument(
        "--stt-model", default=speech_config["stt_model"], metavar="SIZE",
        help="Whisper model: tiny|base|small|medium (default: speech config).  "
             "Use 'tiny' for faster but less accurate transcription.",
    )
    parser.add_argument(
        "--language", default=None, metavar="CODE",
        help="Force a language code (default: speech config).",
    )
    parser.add_argument(
        "--device", default=None, metavar="DEVICE",
        help="Whisper device such as cpu or cuda (default: speech config).",
    )
    parser.add_argument(
        "--compute-type", default=None, metavar="TYPE",
        help="Whisper compute type such as int8 or float16 (default: speech config).",
    )
    parser.add_argument(
        "--silence", type=float, default=None, metavar="SECS",
        help="Seconds of silence before confirmation starts (default: speech config).",
    )
    parser.add_argument(
        "--confirm-delay", type=float, default=None, metavar="SECS",
        help="Extra silence to wait before finalizing an utterance (default: speech config).",
    )
    parser.add_argument(
        "--sample-rate", type=int, default=None, metavar="HZ",
        help="Microphone sample rate in Hz (default: speech config).",
    )
    parser.add_argument(
        "--pre-buffer", type=float, default=None, metavar="SECS",
        help="Seconds of audio to keep before speech starts (default: speech config).",
    )
    parser.add_argument(
        "--max-utterance", type=float, default=None, metavar="SECS",
        help="Hard cap for one spoken utterance (default: speech config).",
    )
    parser.add_argument(
        "--interrupt-multiplier", type=float, default=None, metavar="FACTOR",
        help="Speech threshold multiplier while robot is talking (default: speech config).",
    )
    parser.add_argument(
        "--tts-rate", type=int, default=None, metavar="WPM",
        help="TTS speaking rate in words per minute (default: speech config).",
    )
    parser.add_argument(
        "--tts-volume", type=float, default=None, metavar="LEVEL",
        help="TTS volume from 0.0 to 1.0 (default: speech config).",
    )
    parser.add_argument(
        "--enable-ambient-calibration", action="store_true", default=None,
        help="Enable continuous ambient noise calibration (default: speech config).",
    )
    parser.add_argument(
        "--disable-ambient-calibration", action="store_true",
        help="Disable continuous ambient noise calibration.",
    )
    parser.add_argument(
        "--calibration-interval", type=float, default=None, metavar="SECS",
        help="Seconds between ambient recalibrations during idle (default: speech config).",
    )
    parser.add_argument(
        "--calibration-noise-floor", type=float, default=None, metavar="LEVEL",
        help="Default ambient noise floor to use (default: speech config or auto-calibrate).",
    )
    parser.add_argument(
        "--calibration-threshold", type=float, default=None, metavar="LEVEL",
        help="Default speech detection threshold to use (default: speech config or auto-calibrate).",
    )
    parser.add_argument(
        "--menu", default=os.path.join(_HERE, "menu.json"), metavar="PATH",
        help="Path to menu.json (default: menu.json next to this script)",
    )
    parser.add_argument(
        "--table-id", type=str, default="default",
        help="Identifier for the table (e.g., 'table_1'). Default is 'default'.",
    )
    parser.add_argument(
        "--list-topics", action="store_true",
        help="Print bus topic/service names (useful for ROS mapping) and exit",
    )
    args = parser.parse_args()

    if args.list_topics:
        _print_topics()
        return

    _check_deps()

    menu = _load_menu(args.menu)
    wake_words = args.wake_words or speech_config["wake_words"]
    stt_model = args.stt_model or speech_config["stt_model"]
    language = args.language or speech_config["language"]
    device = args.device or speech_config["device"]
    compute_type = args.compute_type or speech_config["compute_type"]
    silence_dur = speech_config["silence_dur"] if args.silence is None else args.silence
    confirm_delay = speech_config["confirm_delay"] if args.confirm_delay is None else args.confirm_delay
    sample_rate = speech_config["sample_rate"] if args.sample_rate is None else args.sample_rate
    pre_buffer = speech_config["pre_buffer_seconds"] if args.pre_buffer is None else args.pre_buffer
    max_utterance = speech_config["max_utterance_seconds"] if args.max_utterance is None else args.max_utterance
    interrupt_multiplier = (
        speech_config["interrupt_multiplier"]
        if args.interrupt_multiplier is None
        else args.interrupt_multiplier
    )
    tts_rate = speech_config["tts_rate"] if args.tts_rate is None else args.tts_rate
    tts_volume = speech_config["tts_volume"] if args.tts_volume is None else args.tts_volume
    
    # Handle calibration settings with explicit flags
    enable_calibration = speech_config["enable_ambient_calibration"]
    if args.disable_ambient_calibration:
        enable_calibration = False
    elif args.enable_ambient_calibration:
        enable_calibration = True
    
    calibration_interval = (
        speech_config["ambient_calibration_interval"]
        if args.calibration_interval is None
        else args.calibration_interval
    )
    
    calibration_noise_floor = (
        speech_config["calibration_noise_floor_default"]
        if args.calibration_noise_floor is None
        else args.calibration_noise_floor
    )
    
    calibration_threshold = (
        speech_config["calibration_threshold_default"]
        if args.calibration_threshold is None
        else args.calibration_threshold
    )

    # ── Create the shared message bus ─────────────────────────────────────
    # ### ROS INTEGRATION ###
    # Replace QueueBus() with RosBus() in each node's entry point script.
    # This launcher file is not used in ROS.
    bus = QueueBus()

    # ── Instantiate nodes ─────────────────────────────────────────────────
    listener  = VoiceListenerNode(
        bus         = bus,
        wake_words  = wake_words,
        stt_model   = stt_model,
        silence_dur = silence_dur,
        confirmation_delay = confirm_delay,
        language = language,
        device = device,
        compute_type = compute_type,
        sample_rate = sample_rate,
        pre_buffer_seconds = pre_buffer,
        max_utterance_seconds = max_utterance,
        interrupt_multiplier = interrupt_multiplier,
        tts_rate = tts_rate,
        tts_volume = tts_volume,
        enable_ambient_calibration = enable_calibration,
        ambient_calibration_interval = calibration_interval,
        calibration_noise_floor_default = calibration_noise_floor,
        calibration_threshold_default = calibration_threshold,
    )
    processor = OrderProcessorNode(
        bus      = bus,
        menu     = menu,
        table_id = args.table_id,
    )

    # ── Print startup banner ──────────────────────────────────────────────
    print("=" * 64)
    print("  Restaurant Ordering Robot  –  LIVE VOICE MODE")
    print("  Speak a wake word to start an order.")
    print(f"  Config     : {pre_args.speech_config}")
    print(f"  Wake words : {listener._hotword.wake_words}")
    print(f"  STT model  : {stt_model}")
    print(f"  Silence    : {silence_dur} s + {confirm_delay} s confirm delay")
    print("  Press Ctrl-C to exit.")
    print("=" * 64)
    print()

    # ── Run both nodes as threads ─────────────────────────────────────────
    # ### ROS INTEGRATION ###
    # Each thread here becomes a separate ROS node process launched via
    # roslaunch.  The threading.Thread calls below are not needed in ROS.
    t_listener  = threading.Thread(target=listener.run,  daemon=True,
                                   name="voice-listener")
    t_processor = threading.Thread(target=processor.run, daemon=True,
                                   name="order-processor")

    t_listener.start()
    t_processor.start()

    try:
        # Keep the main thread alive so Ctrl-C is caught here
        t_listener.join()
        t_processor.join()
    except KeyboardInterrupt:
        print("\n[run_voice] Shutting down…")
        listener.stop()
        processor.stop()

    t_listener.join(timeout=2)
    t_processor.join(timeout=2)
    print("[run_voice] Done.")


if __name__ == "__main__":
    main()
