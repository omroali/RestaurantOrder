"""
take_order_full.py  –  PNP plan: full restaurant ordering with dialogue.

Architecture:
  transcriber_node.py        → /transcriber/text   (always-on STT)
  DialogueManager            → order parsing & confirmation
  This plan                  → orchestrates speak/listen/confirm loop

The plan:
  1. Greets and asks for order
  2. Listens → forwards to DialogueManager
  3. Speaks the robot's response (clarification, confirmation, etc.)
  4. Repeats until order is confirmed or cancelled
  5. Outputs the final order JSON

Prerequisites:
  rosrun restaurant_language_unit transcriber_node.py _model:=small
"""

import os, sys, json, time
import rospy
from std_msgs.msg import String

# Add RestaurantOrder to path
_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG  = os.path.dirname(_HERE)
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from src.dialogue_manager import DialogueManager

try:
    sys.path.insert(0, os.environ["PNP_HOME"] + '/scripts')
except:
    print("Please set PNP_HOME environment variable to PetriNetPlans folder.")
    sys.exit(1)

from pnp_cmd_ros import PNPCmd

# ── Helpers ───────────────────────────────────────────────────────────

speech_pub = None

def _say(p, text):
    """Speak via TIAGo — splits multi-line text into individual utterances."""
    global speech_pub
    if speech_pub is None:
        speech_pub = rospy.Publisher('/robot/speaking', String, queue_size=1)

    # Split on newlines and speak each non-empty line separately
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    for line in lines:
        line = line.replace(' ', '_')
        speech_pub.publish(String(data=line))
        p.exec_action('speak', line)
        rospy.sleep(0.3)  # brief pause between lines
    rospy.sleep(0.5)  # let any residual echo dissipate
    speech_pub.publish(String(data=''))


def _listen(p, timeout=15.0):
    """Wait for the next utterance from the transcriber. Returns text or None."""
    rospy.loginfo("Listening …")
    msg = rospy.wait_for_message('/transcriber/text', String, timeout=timeout)
    if msg is None:
        rospy.loginfo("(timeout)")
        return None
    rospy.loginfo(f"Heard: {msg.data}")
    return msg.data


def _load_menu():
    """Load menu from the package's menu.json."""
    menu_path = os.path.join(_PKG, "menu.json")
    if os.path.exists(menu_path):
        with open(menu_path) as f:
            return json.load(f)
    rospy.logwarn("menu.json not found — using empty menu")
    return []


# ── Main plan ─────────────────────────────────────────────────────────

def take_order(p, table_id="default"):
    menu = _load_menu()
    manager = DialogueManager(menu)

    # 1. Greet and prompt for order
    greeting = ("Hello!_I'll_be_serving_you_today._"
                "You_can_ask_what's_on_the_menu,_"
                "or_just_tell_me_what_you'd_like.")
    _say(p, greeting)

    done = False
    # 2. Conversation loop
    while not done:
        text = _listen(p, timeout=20.0)
        if text is None:
            _say(p, "I_didn't_catch_that._Could_you_repeat_your_order?")
            continue

        # Check for cancellations
        if any(w in text.lower() for w in ["cancel", "never mind", "stop", "abort"]):
            _say(p, "Understood._Cancelling_your_order.")
            manager.reset(table_id=table_id)
            return None

        # Process through dialogue manager
        response, done = manager.process_input(text, table_id=table_id)
        _say(p, response.replace(' ', '_'))

    # 3. Order confirmed — output JSON
    final_order = manager.get_final_json(table_id=table_id)
    rospy.loginfo(f"Order confirmed: {json.dumps(final_order, indent=2)}")

    # Publish to the standard order result topic
    order_pub = rospy.Publisher('/restaurant/order_result', String, queue_size=1)
    order_pub.publish(String(data=json.dumps(final_order)))

    _say(p, "Thank_you._Your_order_will_be_ready_shortly.")
    return final_order


if __name__ == "__main__":
    p = PNPCmd()
    p.begin()
    order = take_order(p)
    if order:
        rospy.loginfo(f"Final order: {json.dumps(order, indent=2)}")
    p.end()
