"""
main.py

Command-line interface for the Restaurant Ordering Robot.

Two modes
---------
  Typed-text mode  (default)
      python main.py
      Prompts the user to type order text directly; ideal for testing the
      parser and dialogue logic without any audio hardware.

  Audio-file mode
      python main.py --audio path/to/sample_order.wav
      Transcribes the WAV file with faster-whisper, then enters typed-text
      mode for the confirmation/correction turns so you can interact with the
      robot after the initial speech input.

  Microphone mode  (experimental)
      python main.py --mic
      Records from the default microphone then processes as above.
      Requires pyaudio and faster-whisper to be installed.

Usage examples
--------------
  python main.py
  python main.py --audio sample_order.wav
  python main.py --audio sample_order.wav --model small
  python main.py --mic --duration 8
"""

import argparse
import json
import os
import sys

# ---------------------------------------------------------------------------
# Ensure the project root is on the Python path so "src" is importable
# regardless of where the script is invoked from.
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from src.dialogue_manager import DialogueManager
from src.speech_to_text import SpeechToText


# ---------------------------------------------------------------------------
# ANSI colour helpers (degrade to plain text if not supported)
# ---------------------------------------------------------------------------
try:
    from colorama import init as _colorama_init, Fore, Style
    _colorama_init(autoreset=True)
    def _robot(msg: str) -> str:
        return Fore.CYAN + f"[Robot] {msg}" + Style.RESET_ALL
    def _user_prompt() -> str:
        return Fore.GREEN + "You: " + Style.RESET_ALL
    def _info(msg: str) -> str:
        return Fore.YELLOW + msg + Style.RESET_ALL
except ImportError:
    def _robot(msg: str) -> str:
        return f"[Robot] {msg}"
    def _user_prompt() -> str:
        return "You: "
    def _info(msg: str) -> str:
        return msg


# ---------------------------------------------------------------------------
# Menu loading
# ---------------------------------------------------------------------------

def load_menu(menu_path: str) -> list:
    """Load and return the menu from *menu_path* (JSON file)."""
    if not os.path.exists(menu_path):
        print(f"[Error] Menu file not found: {menu_path}")
        sys.exit(1)
    with open(menu_path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Conversation loop
# ---------------------------------------------------------------------------

def run_typed_mode(manager: DialogueManager, table_id: str, initial_text: str = "") -> None:
    \"\"\"
    Drive the dialogue from the terminal for a specific table.

    Parameters
    ----------
    manager : DialogueManager
        An already-initialised manager (may have state from a prior audio turn).
    table_id : str
        The ID of the table for which the order is being processed.
    initial_text : str
        Pre-populated first turn (used when the initial order came from audio).
    \"\"\"
    print(_info(\"=\" * 60))
    print(_info(f\"  Restaurant Ordering Robot  –  Table: {table_id} (typed-text mode)\"))
    print(_info(\"  Type your order, then answer the robot\'s questions.\"))
    print(_info(\"  Press Ctrl-C or type \'quit\' to exit.\"))
    print(_info(\"=\" * 60))
    print()

    # If the audio layer already produced a transcript, process it first.
    if initial_text:
        print(f\"[Transcript] {initial_text}\")
        response, done = manager.process_input(initial_text, table_id=table_id)
        print(_robot(response))
        if done:
            return
        print()

    # Interactive loop
    while True:
        try:
            raw = input(_user_prompt()).strip()
        except (EOFError, KeyboardInterrupt):
            print(\"\\n[Info] Session ended by user.\")
            break

        if raw.lower() in (\"quit\", \"exit\", \"q\"):\
            print(\"[Info] Goodbye!\")
            break

        if not raw:\
            continue

        response, done = manager.process_input(raw, table_id=table_id)
        print(_robot(response))

        if done:
            print()\
            print(_info(\"=\" * 60))\
            print(_info(\"  Order confirmed!  Final JSON:\"))\
            print(_info(\"=\" * 60))\
            # response IS the JSON when done=True
            print(response)
            break

        print()


def run_audio_mode(
    manager: DialogueManager,
    stt: SpeechToText,
    audio_path: str,
    table_id: str,
) -> None:
    \"\"\"
    Transcribe *audio_path* and feed the result into the dialogue manager,
    then hand off to typed-text mode for confirmation turns.
    \"\"\"\
    print(_info(f\"[Audio] Transcribing: {audio_path}\"))
    try:
        transcript = stt.transcribe_file(audio_path)
    except (FileNotFoundError, RuntimeError) as exc:\
        print(f\"[Error] {exc}\")
        sys.exit(1)

    if not transcript:
        print(\"[Warning] Transcription produced no output.  Switching to typed mode.\")
        run_typed_mode(manager, table_id)
        return

    run_typed_mode(manager, table_id, initial_text=transcript)


def run_microphone_mode(
    manager: DialogueManager,
    stt: SpeechToText,
    duration: float,
    table_id: str,
) -> None:
    \"\"\"Record from the microphone then hand off to typed-text mode.\"\"\"\
    print(_info(f\"[Mic] Recording for up to {duration:.0f} s …  Speak now!\"))
    try:
        transcript = stt.transcribe_microphone(duration=duration)
    except RuntimeError as exc:\
        print(f\"[Error] {exc}\")
        sys.exit(1)

    if not transcript:
        print(\"[Warning] No speech detected.  Switching to typed mode.\")
        run_typed_mode(manager, table_id)
        return

    run_typed_mode(manager, table_id, initial_text=transcript)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Restaurant Ordering Robot – speech / typed order processor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--audio",
        metavar="WAV_FILE",
        help="Path to a WAV file for initial order input (requires faster-whisper).",
    )
    group.add_argument(
        "--mic",
        action="store_true",
        help="Record from the microphone for the initial order (requires pyaudio + faster-whisper).",
    )
    p.add_argument(
        "--menu",
        default=os.path.join(_HERE, "menu.json"),
        metavar="MENU_JSON",
        help=\"Path to the menu JSON file (default: menu.json next to main.py).\",
    )\
    p.add_argument(
        \"--model\",
        default=\"base\",
        metavar=\"SIZE\",
        help=\"Whisper model size: tiny | base | small | medium | large-v2 (default: base).\",
    )\
    p.add_argument(
        \"--duration\",
        type=float,
        default=10.0,
        metavar=\"SECONDS\",
        help=\"Maximum microphone recording time in seconds (default: 10).\",
    )\
    p.add_argument(
        \"--table-id\",
        type=str,
        default=\"default\",
        help=\"Identifier for the table (e.g., \'table_1\'). Default is \'default\'.\",
    )\
    return p


# ---------------------------------------------------------------------------\
# Entry point
# ---------------------------------------------------------------------------\


def main() -> None:
    args = build_parser().parse_args()

    menu    = load_menu(args.menu)\
    manager = DialogueManager(menu)

    table_id = args.table_id
    if not args.audio and not args.mic and table_id == \"default\":
        # If in typed mode and no table_id provided, prompt for it
        table_id = input(_user_prompt() + \"Enter table ID (e.g., \'table_1\'): \").strip()
        if not table_id:
            table_id = \"default\" # Fallback if user enters nothing
        print(_info(f\"Using table ID: {table_id}\"))
        print()


    if args.audio:\
        stt = SpeechToText(model_size=args.model)
        run_audio_mode(manager, stt, args.audio, table_id)
    elif args.mic:\
        stt = SpeechToText(model_size=args.model)
        run_microphone_mode(manager, stt, args.duration, table_id)
    else:\
        run_typed_mode(manager, table_id)


if __name__ == \"__main__\":
    main()
