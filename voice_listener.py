#!/usr/bin/env python3
"""
voice_listener.py  –  Always-on voice listener node.

Runs StreamingTranscriber in the background, which emits:
  • on_partial(text)  – live updates while the user is still speaking
  • on_final(text)    – accurate transcript once silence is detected

For each final transcript the node:
  • In IDLE    state: checks for a wake word → calls SVC_START_ORDER
  • In SESSION state: publishes to TOPIC_TRANSCRIPT for OrderProcessorNode
                      and checks for interruption phrases

──────────────────────────────────────────────────────────────────────────────
### ROS INTEGRATION  –  converting to voice_listener_node.py ###

Step 1  Replace the bus:
    from src.ros_bridge import RosBus      # see src/ipc.py for template
    bus = RosBus()
    rospy.init_node("voice_listener_node", anonymous=False)

Step 2  Audio source → ROS audio driver:
    Replace StreamingTranscriber (which reads pyaudio directly) with a
    rospy.Subscriber on /audio/raw (audio_common_msgs/AudioData).
    In streaming_stt.py, add push_chunk(bytes) and call it from the
    subscriber callback instead of the pyaudio loop.

Step 3  on_partial / on_final → ROS TTS / speech display:
    Publish on_final text to /restaurant/transcript (std_msgs/String).
    Publish on_partial text to /restaurant/transcript_partial for debugging
.
    Publish on_final to /robot/speech (std_msgs/String) so a TTS node
    (sound_play, piper-ros) can vocalise robot prompts.

Step 4  Main loop → rospy.spin():
    Replace the threading.Event().wait() loop in run() with rospy.spin().

Step 5  roslaunch entry:
    <node name="voice_listener" pkg="restaurant_robot"
          type="voice_listener_node.py" output="screen">
        <param name="wake_words"    value="hey tiago,excuse me,order please"/>
        <param name="stt_model"     value="base"/>
        <param name="silence_dur"   value="0.6"/>
    </node>
──────────────────────────────────────────────────────────────────────────────
"""

import os
import sys
import threading
from typing import List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from src.hotword import HotwordDetector
from src.ipc import (
    MessageBus,
    SVC_CANCEL_ORDER,
    SVC_START_ORDER,
    TOPIC_INTERRUPTION,
    TOPIC_ORDER_RESULT,
    TOPIC_TRANSCRIPT,
    TOPIC_WAKE_WORD,
)
from src.streaming_stt import StreamingTranscriber

# ─────────────────────────────────────────────────────────────────────────────
# Node states
# ─────────────────────────────────────────────────────────────────────────────
_IDLE    = "IDLE"     # listening for wake word
_SESSION = "SESSION"  # active order – forward transcripts to order_processor


class VoiceListenerNode:
    """
    Always-on voice listener.

    Owns the microphone and runs StreamingTranscriber.  Dispatches
    transcripts to the correct handler based on the current state.

    Parameters
    ----------
    bus            : MessageBus   –  shared communication channel
    wake_words     : list[str]    –  override default hotword list
    stt_model      : str          –  Whisper model for transcription
    silence_dur    : float        –  seconds of silence that ends an utterance
    partial_interval : float      –  seconds between live partial updates
    """

    def __init__(
        self,
        bus:          MessageBus,
        wake_words:   Optional[List[str]] = None,
        stt_model:    str   = "base",
        silence_dur:  float = 0.8,
    ) -> None:
        self._bus   = bus
        self._state = _IDLE
        self._stop  = threading.Event()

        self._hotword = HotwordDetector(wake_words=wake_words)
        self._stt     = StreamingTranscriber(
            model_size       = stt_model,
            silence_duration = silence_dur,
        )

        bus.subscribe(TOPIC_ORDER_RESULT, self._on_order_result)

        # ### ROS INTEGRATION – TTS hook ###
        # Uncomment and implement when adding spoken robot output:
        #   from src.ipc import TOPIC_ROBOT_PROMPT
        #   bus.subscribe(TOPIC_ROBOT_PROMPT,
        #                 lambda text: tts_engine.say(text))
        #   Also publish /robot/mic_mute True during playback (echo cancel).

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        """
        Start StreamingTranscriber and block until stop() is called.

        ### ROS INTEGRATION ###
        Replace the threading.Event().wait() loop with rospy.spin().
        The callbacks (_on_partial, _on_final) are already correct.
        """
        if not self._stt.available:
            print("[VoiceListener] faster-whisper not installed – cannot start.")
            print("  Install: pip install faster-whisper")
            return

        print(f"[VoiceListener] Starting.  Wake words: {self._hotword.wake_words}")

        ok = self._stt.start(
            on_final = self._on_final,
        )
        if not ok:
            print("[VoiceListener] Failed to start StreamingTranscriber.")
            return

        print("[VoiceListener] Listening for wake word…")

        try:
            # Block main thread; background thread does all the work
            while not self._stop.is_set():
                threading.Event().wait(timeout=0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self._stt.stop()
            print("[VoiceListener] Stopped.")

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------
    # StreamingTranscriber callback
    # ------------------------------------------------------------------

    def _on_final(self, text: str) -> None:
        """
        Called once per utterance when silence is detected.
        Routes the transcript based on current state.

        ### ROS INTEGRATION ###
        Publish to /restaurant/transcript (std_msgs/String).
        """
        text = text.strip()
        if not text:
            return

        print(f"\n[You said]: {text}")

        if self._state == _IDLE:
            self._check_wake_word(text)

        elif self._state == _SESSION:
            # Check for cancellation phrases
            if self._hotword.is_interruption(text):
                print("[VoiceListener] Interruption phrase detected.")
                self._bus.publish(TOPIC_INTERRUPTION, text)
                try:
                    self._bus.call_service(SVC_CANCEL_ORDER)
                except Exception:
                    pass
                return

            # Forward to order processor via the bus
            self._bus.publish(TOPIC_TRANSCRIPT, text)

    # ------------------------------------------------------------------
    # Wake word handling
    # ------------------------------------------------------------------

    def _check_wake_word(self, text: str) -> None:
        """Check transcribed text for a wake word and start a session."""
        matched = self._hotword.detect_in_text(text)
        if not matched:
            return

        print(f"\n[VoiceListener] Wake word detected: '{matched}'")
        self._bus.publish(TOPIC_WAKE_WORD, matched)
        self._state = _SESSION

        try:
            result = self._bus.call_service(SVC_START_ORDER)
            if not result.get("success"):
                print(f"[VoiceListener] start_order rejected: "
                      f"{result.get('message')}")
                self._state = _IDLE
        except Exception as exc:
            print(f"[VoiceListener] start_order failed: {exc}")
            self._state = _IDLE

    # ------------------------------------------------------------------
    # Bus callbacks
    # ------------------------------------------------------------------

    def _on_order_result(self, payload) -> None:
        """Session ended (confirmed or cancelled). Return to IDLE."""
        self._state = _IDLE
        print("\n[VoiceListener] Session ended.  Listening for wake word…")
