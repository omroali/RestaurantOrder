"""
ros_bridge.py  –  ROS 1 MessageBus implementation.

Drop-in replacement for QueueBus.  Maps every bus method to its ROS
equivalent so that VoiceListenerNode and OrderProcessorNode can run as
separate ROS nodes communicating via topics and services.

Usage (inside a node entry point):
    import rospy
    from src.ros_bridge import RosBus

    rospy.init_node("my_node")
    bus = RosBus()
    node = VoiceListenerNode(bus=bus, **params)
    bus.spin()   # blocks until shutdown
"""

import json
import threading
from typing import Any, Callable, Dict, List

import rospy
from std_msgs.msg import String
from std_srvs.srv import Trigger, TriggerRequest, TriggerResponse

from src.ipc import MessageBus, ServiceNotFoundError

# ─────────────────────────────────────────────────────────────────────────────
# RosBus
# ─────────────────────────────────────────────────────────────────────────────


class RosBus(MessageBus):
    """
    ROS 1 message bus.

    Mirrors the QueueBus API exactly, but transports every message through
    ROS topics and services so that voice_listener and order_processor can
    run in separate processes (or machines) without any code changes to the
    node classes themselves.

    Topic payloads are JSON-encoded into std_msgs/String.
    Service requests/responses use std_srvs/Trigger.
    """

    def __init__(self) -> None:
        self._pubs: Dict[str, rospy.Publisher] = {}
        self._subs: List[rospy.Subscriber] = []
        self._srvs: List[rospy.Service] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # MessageBus interface
    # ------------------------------------------------------------------

    def publish(self, topic: str, payload: Any) -> None:
        """
        Publish *payload* as a JSON-encoded std_msgs/String on *topic*.

        Publishers are created lazily on first use.
        """
        if topic not in self._pubs:
            with self._lock:
                if topic not in self._pubs:  # double-check
                    self._pubs[topic] = rospy.Publisher(
                        topic, String, queue_size=10
                    )
        # JSON-encode the payload; None becomes "null" string
        self._pubs[topic].publish(String(data=json.dumps(payload)))

    def subscribe(self, topic: str, callback: Callable[[Any], None]) -> None:
        """
        Register *callback* to receive JSON-decoded messages on *topic*.

        The callback receives the deserialised Python object, matching the
        prototype QueueBus signature exactly.
        """

        def _cb(msg: String) -> None:
            try:
                payload = json.loads(msg.data)
            except (json.JSONDecodeError, TypeError):
                rospy.logwarn_throttle(
                    10,
                    f"[RosBus] Could not decode JSON on '{topic}': "
                    f"{msg.data[:120]}",
                )
                return
            callback(payload)

        sub = rospy.Subscriber(topic, String, _cb)
        self._subs.append(sub)  # prevent garbage collection

    def call_service(self, name: str, request: Any = None) -> Any:
        """
        Call ROS service *name* (std_srvs/Trigger) and return a dict
        with 'success' and 'message' keys.
        """
        try:
            rospy.wait_for_service(name, timeout=3.0)
        except rospy.ROSException:
            raise ServiceNotFoundError(
                f"Service '{name}' is not available (timeout)"
            )
        try:
            proxy = rospy.ServiceProxy(name, Trigger)
            resp: TriggerResponse = proxy()
            return {"success": resp.success, "message": resp.message}
        except rospy.ServiceException as exc:
            raise ServiceNotFoundError(
                f"Service '{name}' call failed: {exc}"
            )

    def advertise_service(
        self, name: str, handler: Callable[[Any], Any]
    ) -> None:
        """
        Advertise a ROS service *name* (std_srvs/Trigger).

        The *handler* receives the request and must return a dict
        {'success': bool, 'message': str}.
        """

        def _cb(req: TriggerRequest) -> TriggerResponse:
            result = handler(req)
            return TriggerResponse(
                success=result.get("success", True),
                message=result.get("message", ""),
            )

        srv = rospy.Service(name, Trigger, _cb)
        self._srvs.append(srv)

    def spin(self) -> None:
        """
        Block and process ROS callbacks until shutdown.

        Call this INSTEAD of the manual threading.Event / time.sleep loops
        used in the prototype run() methods.
        """
        rospy.spin()

    def shutdown(self) -> None:
        """Request ROS node shutdown."""
        rospy.signal_shutdown("Requested via bus")
