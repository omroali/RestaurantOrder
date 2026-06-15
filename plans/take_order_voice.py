"""
take_order_voice.py  –  PNP plan: take a food order by voice.

Architecture:
  transcriber_node.py runs in background → publishes /transcriber/text
  Self-echo is filtered by matching against what the robot said.
  Anything heard WHILE the robot speaks → barge-in (/transcriber/interruption).

Prerequisites:
  rosrun restaurant_language_unit transcriber_node.py _model:=tiny

Usage:
  python3 take_order_voice.py
"""

import os
import sys
import rospy
from std_msgs.msg import String

try:
    sys.path.insert(0, os.environ["PNP_HOME"] + '/scripts')
except:
    print("Please set PNP_HOME environment variable to PetriNetPlans folder.")
    sys.exit(1)

from pnp_cmd_ros import PNPCmd

speech_pub = None
listen_pub = None


def _say(p, text):
    """Speak, keep flag up during TTS, then signal ready to listen."""
    global speech_pub
    if speech_pub is None:
        speech_pub = rospy.Publisher('/robot/speaking', String, queue_size=1)
        listen_pub = rospy.Publisher('/transcriber/listen', String, queue_size=1)

    speech_pub.publish(String(data=text))
    p.exec_action('speak', text)
    # Keep is_robot_speaking=True while TTS plays, then clear it
    speech_pub.publish(String(data=''))
    rospy.loginfo("Listening \u2026")


def take_order(p):
    # 1. Greet
    _say(p, 'Hi,_welcome._What_would_you_like_to_order?')

    # 2. Wait for customer response (self-echo auto-filtered, no timeout)
    msg = rospy.wait_for_message('/transcriber/text', String, timeout=None)

    if msg is None:
        heard = "nothing"
    else:
        heard = msg.data.replace(' ', '_')
        rospy.loginfo(f"Heard: {msg.data}")

    # 3. Confirm
    _say(p, f'I_heard_{heard}._Preparing_your_order_now.')


if __name__ == "__main__":
    p = PNPCmd()
    p.begin()
    take_order(p)
    p.end()
