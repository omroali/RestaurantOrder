"""
ipc.py  –  Inter-process communication abstraction.

Defines a MessageBus interface whose methods map 1-to-1 onto ROS primitives.
The prototype uses QueueBus (in-process, thread-safe).  Switching to ROS
only requires replacing QueueBus with RosBus at the two node entry points.

──────────────────────────────────────────────────────────────────────────────
### ROS INTEGRATION ###

Implement this class in a new file  src/ros_bridge.py  inside your ROS package:

    import json
    import rospy
    from std_msgs.msg import String
    from std_srvs.srv import Trigger, TriggerResponse

    class RosBus(MessageBus):
        def __init__(self):
            self._pubs: dict  = {}   # topic → rospy.Publisher
            self._subs: list  = []   # keep references alive
            self._srvs: list  = []

        def publish(self, topic: str, payload) -> None:
            if topic not in self._pubs:
                self._pubs[topic] = rospy.Publisher(topic, String, queue_size=10)
            self._pubs[topic].publish(String(data=json.dumps(payload)))

        def subscribe(self, topic: str, callback) -> None:
            sub = rospy.Subscriber(
                topic, String,
                lambda msg: callback(json.loads(msg.data))
            )
            self._subs.append(sub)

        def call_service(self, name: str, request=None):
            rospy.wait_for_service(name)
            proxy = rospy.ServiceProxy(name, Trigger)
            resp  = proxy()
            return {"success": resp.success, "message": resp.message}

        def advertise_service(self, name: str, handler) -> None:
            def _cb(req):
                result = handler(req)
                return TriggerResponse(
                    success=result.get("success", True),
                    message=result.get("message", ""),
                )
            srv = rospy.Service(name, Trigger, _cb)
            self._srvs.append(srv)

        def spin(self)    -> None: rospy.spin()
        def shutdown(self)-> None: rospy.signal_shutdown("Requested")

Then in each node entry point, replace:
    bus = QueueBus()
with:
    rospy.init_node("voice_listener_node")   # or order_processor_node
    bus = RosBus()
──────────────────────────────────────────────────────────────────────────────
"""

import threading
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Topic and service name constants
#
# These strings become the ROS topic/service names without any renaming.
# ─────────────────────────────────────────────────────────────────────────────

# Topics  (fire-and-forget, many subscribers)
TOPIC_WAKE_WORD    = "/restaurant/wake_word"     # listener  → processor  : wake phrase detected
TOPIC_TRANSCRIPT   = "/restaurant/transcript"    # listener  → processor  : user utterance text
TOPIC_ROBOT_PROMPT = "/restaurant/robot_prompt"  # processor → listener   : robot reply text
TOPIC_ORDER_RESULT = "/restaurant/order_result"  # processor → *          : final confirmed JSON
TOPIC_INTERRUPTION = "/restaurant/interruption"  # listener  → processor  : mid-session cancel phrase

# Services  (request-response, one provider)
SVC_START_ORDER  = "/restaurant/start_order"     # caller → processor : begin a new session
SVC_CANCEL_ORDER = "/restaurant/cancel_order"    # caller → processor : abort active session


# ─────────────────────────────────────────────────────────────────────────────
# Abstract interface
# ─────────────────────────────────────────────────────────────────────────────

class MessageBus(ABC):
    """
    Abstract message bus.  Maps directly to ROS primitives – see module
    docstring for the RosBus drop-in implementation.
    """

    @abstractmethod
    def publish(self, topic: str, payload: Any) -> None:
        """Publish *payload* to *topic* (fire-and-forget)."""

    @abstractmethod
    def subscribe(self, topic: str, callback: Callable[[Any], None]) -> None:
        """Register *callback* to be called whenever *topic* receives a message."""

    @abstractmethod
    def call_service(self, name: str, request: Any = None) -> Any:
        """Call a service synchronously and return the response."""

    @abstractmethod
    def advertise_service(self, name: str, handler: Callable[[Any], Any]) -> None:
        """Register *handler* as the provider for service *name*."""

    @abstractmethod
    def spin(self) -> None:
        """Block and process messages (no-op for QueueBus; rospy.spin() for ROS)."""

    @abstractmethod
    def shutdown(self) -> None:
        """Tear down the bus."""


# ─────────────────────────────────────────────────────────────────────────────
# Prototype implementation  (thread-safe in-process queues)
# ─────────────────────────────────────────────────────────────────────────────

class ServiceNotFoundError(RuntimeError):
    """Raised when call_service targets an unregistered service name."""


class QueueBus(MessageBus):
    """
    In-process message bus backed by Python threading primitives.

    Both nodes share a single QueueBus instance.  Callbacks are called
    synchronously in the publisher's thread, which mirrors ROS1 single-
    threaded spinner semantics closely enough for the prototype.

    ### ROS INTEGRATION ###
    Replace with RosBus at the two entry points (voice_listener.py and
    order_processor.py).  The node classes themselves need no changes.
    """

    def __init__(self) -> None:
        self._subscriptions: Dict[str, List[Callable]] = {}
        self._services:      Dict[str, Callable]       = {}
        self._lock = threading.Lock()
        self._running = False

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def publish(self, topic: str, payload: Any) -> None:
        """Deliver *payload* to all subscribers of *topic*."""
        # Copy the list under the lock, then call callbacks outside it so
        # that a callback can itself call publish() without deadlocking.
        with self._lock:
            callbacks = list(self._subscriptions.get(topic, []))
        for cb in callbacks:
            try:
                cb(payload)
            except Exception as exc:            # noqa: BLE001
                print(f"[Bus] callback error on '{topic}': {exc}")

    def subscribe(self, topic: str, callback: Callable[[Any], None]) -> None:
        with self._lock:
            self._subscriptions.setdefault(topic, []).append(callback)

    def call_service(self, name: str, request: Any = None) -> Any:
        with self._lock:
            handler = self._services.get(name)
        if handler is None:
            raise ServiceNotFoundError(f"Service '{name}' is not advertised")
        return handler(request)

    def advertise_service(self, name: str, handler: Callable[[Any], Any]) -> None:
        with self._lock:
            self._services[name] = handler

    def spin(self) -> None:
        """Block until shutdown() is called."""
        self._running = True
        try:
            while self._running:
                threading.Event().wait(timeout=0.1)
        except KeyboardInterrupt:
            pass

    def shutdown(self) -> None:
        self._running = False
