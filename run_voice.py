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
        <arg name="silence_dur"   default="1.5"/>
        <arg name="max_listen"    default="15.0"/>

        <!-- Always-on listener: microphone + hotword + transcription -->
        <node name="voice_listener" pkg="restaurant_robot"
              type="voice_listener_node.py" output="screen">
            <param name="wake_words"    value="$(arg wake_words)"/>
            <param name="hotword_model" value="tiny"/>
            <param name="stt_model"     value="$(arg stt_model)"/>
            <param name="silence_dur"   value="$(arg silence_dur)"/>
            <param name="max_listen"    value="$(arg max_listen)"/>
        </node>

        <!-- On-demand processor: dialogue + JSON output -->
        <node name="order_processor" pkg="restaurant_robot"
              type="order_processor_node.py" output="screen">
            <param name="stt_model" value="$(arg stt_model)"/>
            <rosparam file="$(find restaurant_robot)/config/menu.yaml"/>
        </node>
    </launch>

Run with:
    roslaunch restaurant_robot restaurant_voice.launch
    roslaunch restaurant_robot restaurant_voice.launch stt_model:=small
──────────────────────────────────────────────────────────────────────────────

Usage (prototype):
    python run_voice.py
    python run_voice.py --wake-words "hey tiago" "order please"
    python run_voice.py --stt-model small --silence 2.0
    python run_voice.py --list-topics       # print bus topic names and exit
"""

import argparse
import json
import os
import sys
import threading

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


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_menu(path: str) -> list:
    if not os.path.exists(path):
        print(f"[run_voice] ERROR: menu not found at {path}")
        sys.exit(1)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


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
    parser = argparse.ArgumentParser(
        description="Restaurant Ordering Robot – live voice mode (prototype launcher)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--wake-words", nargs="+", default=None, metavar="PHRASE",
        help="Wake words to listen for (default: hey tiago, excuse me, …)",
    )
    parser.add_argument(
        "--stt-model", default="base", metavar="SIZE",
        help="Whisper model: tiny|base|small|medium (default: base).  "
             "Use 'tiny' for faster but less accurate transcription.",
    )
    parser.add_argument(
        "--silence", type=float, default=0.6, metavar="SECS",
        help="Seconds of silence that ends an utterance (default: 0.6).",
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

    # ── Create the shared message bus ─────────────────────────────────────
    # ### ROS INTEGRATION ###
    # Replace QueueBus() with RosBus() in each node's entry point script.
    # This launcher file is not used in ROS.
    bus = QueueBus()

    # ── Instantiate nodes ─────────────────────────────────────────────────
    listener  = VoiceListenerNode(
        bus         = bus,
        wake_words  = args.wake_words,
        stt_model   = args.stt_model,
        silence_dur = args.silence,
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
    print(f"  Wake words : {listener._hotword.wake_words}")
    print(f"  STT model  : {args.stt_model}")
    print(f"  Silence    : {args.silence} s")
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
