"""
take_order_full.py  –  PNP plan: take a menu order by voice.

Prerequisites:
  rosrun restaurant_language_unit transcriber_node.py _model:=small

Usage:
  python3 take_order_full.py
"""

import os, sys, json
import rospy
from std_msgs.msg import String

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG  = os.path.dirname(_HERE)
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from src.menu_dialogue import MenuDialogue

try:
    sys.path.insert(0, os.environ["PNP_HOME"] + '/scripts')
except:
    print("Please set PNP_HOME environment variable to PetriNetPlans folder.")
    sys.exit(1)

from pnp_cmd_ros import PNPCmd

speech_pub = None
listen_pub = None


def _say(p, text):
    global speech_pub, listen_pub
    if speech_pub is None:
        speech_pub = rospy.Publisher('/robot/speaking', String, queue_size=1)
        listen_pub = rospy.Publisher('/transcriber/listen', String, queue_size=1)

    lines = [l.strip() for l in text.split('\n') if l.strip()]
    for line in lines:
        line = line.replace(' ', '_')
        listen_pub.publish(String(data='0'))  # gate transcriber OFF
        speech_pub.publish(String(data=line))
        p.exec_action('speak', line)
    # Keep is_robot_speaking=True while TTS plays, then clear it
    speech_pub.publish(String(data=''))
    rospy.loginfo("Listening \u2026")


def _listen(p, timeout=None):
    global listen_pub, speech_pub
    if listen_pub:
        listen_pub.publish(String(data="1"))  # gate transcriber ON
    msg = rospy.wait_for_message('/transcriber/text', String, timeout=timeout)
    if msg is None:
        rospy.loginfo("(no message)")
        return None
    rospy.loginfo(f"Heard: {msg.data}")
    return msg.data


def _stop_listening():
    global listen_pub
    if listen_pub:
        listen_pub.publish(String(data="0"))


def take_order(p):
    dialogue = MenuDialogue()

    # 1. Greeting → get menu list
    response, done = dialogue.process("")
    _say(p, response)

    # 2. Conversation loop
    while not done:
        text = _listen(p)
        if text is None:
            _say(p, "I_didn't_catch_that._Could_you_repeat?")
            continue

        response, done = dialogue.process(text)
        _say(p, response)

    # 3. Output order
    order = dialogue.get_order()
    rospy.loginfo(f"Order confirmed: {json.dumps(order, indent=2)}")

    order_pub = rospy.Publisher('/restaurant/order_result', String, queue_size=1, latch=True)
    order_pub.publish(String(data=json.dumps(order)))
    rospy.set_param('/restaurant/last_order', order)

    return order


if __name__ == "__main__":
    p = PNPCmd()
    p.begin()
    take_order(p)
    p.end()
