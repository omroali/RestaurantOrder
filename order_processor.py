#!/usr/bin/env python3
"""
order_processor.py  –  On-demand order processor node.

Subscribes to TOPIC_TRANSCRIPT and processes each utterance through
DialogueManager when a session is active.  Never touches the microphone.

Session lifecycle
-----------------
  SVC_START_ORDER   →  create DialogueManager, emit opening prompt
  TOPIC_TRANSCRIPT  →  process utterance, emit robot response
  order confirmed   →  publish TOPIC_ORDER_RESULT, go idle
  SVC_CANCEL_ORDER  →  publish TOPIC_ORDER_RESULT (None), go idle

──────────────────────────────────────────────────────────────────────────────
### ROS INTEGRATION  –  converting to order_processor_node.py ###

Step 1  Replace the bus:
    from src.ros_bridge import RosBus      # see src/ipc.py for template
    bus = RosBus()
    rospy.init_node("order_processor_node", anonymous=False)

Step 2  Services → rospy.Service:
    bus.advertise_service(SVC_START_ORDER, ...)
      becomes:  rospy.Service("/restaurant/start_order", Trigger, ...)
    bus.advertise_service(SVC_CANCEL_ORDER, ...)
      becomes:  rospy.Service("/restaurant/cancel_order", Trigger, ...)

Step 3  Subscriber → rospy.Subscriber:
    bus.subscribe(TOPIC_TRANSCRIPT, self._on_transcript)
      becomes:  rospy.Subscriber("/restaurant/transcript", String,
                                 lambda m: self._on_transcript(m.data))

Step 4  Publishers → rospy.Publisher:
    bus.publish(TOPIC_ROBOT_PROMPT, text)
      becomes:  self._prompt_pub.publish(String(data=text))
    bus.publish(TOPIC_ORDER_RESULT, json)
      becomes:  self._result_pub.publish(String(data=json or ""))

Step 5  run() → rospy.spin():
    Replace the time.sleep loop with rospy.spin().

Step 6  roslaunch entry:
    <node name="order_processor" pkg="restaurant_robot"
          type="order_processor_node.py" output="screen">
        <rosparam file="$(find restaurant_robot)/config/menu.yaml"/>
    </node>
──────────────────────────────────────────────────────────────────────────────
"""

import os
import sys
import threading
import time
from typing import List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from src.dialogue_manager import DialogueManager
from src.ipc import (
    SVC_CANCEL_ORDER,
    SVC_START_ORDER,
    TOPIC_ORDER_RESULT,
    TOPIC_ROBOT_PROMPT,
    TOPIC_TRANSCRIPT,
    MessageBus,
)


class OrderProcessorNode:
    """
    Event-driven ordering pipeline node.

    Parameters
    ----------
    bus  : MessageBus  – shared communication channel
    menu : list[dict]  – loaded menu data
    """

    def __init__(
        self, bus: MessageBus, menu: List[dict], table_id: str = "default"
    ) -> None:
        self._bus = bus
        self._menu = menu
        self._table_id = table_id  # Store the table_id this node is responsible for
        self._manager = DialogueManager(
            self._menu
        )  # DialogueManager handles per-table state internally
        self._active_sessions: Dict[
            str, bool
        ] = {}  # Track active sessions per table_id
        self._lock = threading.Lock()  # guards _active_sessions
        self._stop = threading.Event()

        # Advertise services
        bus.advertise_service(SVC_START_ORDER, self._handle_start)
        bus.advertise_service(SVC_CANCEL_ORDER, self._handle_cancel)

        # Subscribe to transcripts – this is the main event driver
        # ### ROS INTEGRATION ###
        # In ROS replace with:
        #   rospy.Subscriber(TOPIC_TRANSCRIPT, String,
        #                    lambda m: self._on_transcript(m.data))
        bus.subscribe(TOPIC_TRANSCRIPT, self._on_transcript)

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        """
        Keep the node alive until stop() is called.

        ### ROS INTEGRATION ###
        Replace with: rospy.spin()
        """
        print("[OrderProcessor] Ready.  Waiting for wake word…")
        try:
            while not self._stop.is_set():
                time.sleep(0.05)
        except KeyboardInterrupt:
            pass
        print("[OrderProcessor] Stopped.")

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------
    # Service handlers
    # ------------------------------------------------------------------

    def _handle_start(
        self, _=None
    ) -> dict:  # In prototype, table_id is from self._table_id
        """Start a new ordering session for this node's table_id."""
        with self._lock:
            if self._active_sessions.get(self._table_id, False):
                return {
                    "success": False,
                    "message": f"Session for table {self._table_id} already active",
                }
            self._active_sessions[self._table_id] = True
            # DialogueManager is initialized once in __init__ and manages internal state per table_id

        self._emit_prompt(
            "Hello! I'll be serving you today. If you would like to learn what we have, please ask about what's on the menu \
            otherwise, you can let me know what you'd like",
            self._table_id,
        )
        return {
            "success": True,
            "message": f"Session for table {self._table_id} started",
        }

    def _handle_cancel(
        self, _=None
    ) -> dict:  # In prototype, table_id is from self._table_id
        """Abort the active session for this node's table_id."""
        with self._lock:
            was_active = self._active_sessions.pop(self._table_id, False)
            if was_active:
                self._manager.reset(
                    table_id=self._table_id
                )  # Reset the dialogue manager's state for this table

        if was_active:
            self._emit_prompt("Understood – cancelling your order.", self._table_id)
            self._bus.publish(TOPIC_ORDER_RESULT, None)  # Publish None for cancellation
            return {
                "success": True,
                "message": f"Cancelled order for table {self._table_id}",
            }
        return {
            "success": False,
            "message": f"No active session for table {self._table_id}",
        }

    # ------------------------------------------------------------------
    # Transcript handler  (the main event)
    # ------------------------------------------------------------------

    def _on_transcript(self, text: str) -> None:
        """
        Process one user utterance.

        Called by the bus whenever VoiceListenerNode publishes a final
        transcript.  Ignored when no session is active.

        ### ROS INTEGRATION ###
        This method is the subscriber callback.  Thread-safety is provided
        by self._lock.  In ROS1 with the default single-threaded spinner
        callbacks are already serialised, but the lock is harmless.
        """
        with self._lock:
            if not self._active_sessions.get(self._table_id, False):
                return
            manager = (
                self._manager
            )  # local ref (manager is guaranteed to exist from __init__)

        response, done = manager.process_input(text, table_id=self._table_id)
        self._emit_prompt(response, self._table_id)

        if done:
            with self._lock:
                self._active_sessions[self._table_id] = (
                    False  # Deactivate session for this table
                )
                self._manager.reset(
                    table_id=self._table_id
                )  # Reset the DialogueManager's state for this table
            self._bus.publish(TOPIC_ORDER_RESULT, response)
            print(
                f"\n[OrderProcessor] Order confirmed and published for table {self._table_id}."
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _emit_prompt(self, text: str, table_id: str = "default") -> None:
        """Print and publish the robot's response."""
        print(f"\n[Robot] [Table {table_id}] {text}")
        # Note: The underlying QueueBus.publish for TOPIC_ROBOT_PROMPT expects a string.
        # If true multi-table output routing is needed via the bus, TOPIC_ROBOT_PROMPT
        # would need to carry the table_id in its message type (e.g., a tuple or dict).
        # For this prototype, as OrderProcessorNode is instantiated per table_id,
        # printing with table_id is sufficient for local clarity.
        self._bus.publish(TOPIC_ROBOT_PROMPT, text)
