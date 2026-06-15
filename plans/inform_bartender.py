"""
inform_bartender.py  –  PNP plan: tell the bartender what the customer ordered.

Reads the last confirmed order from /restaurant/order_result and speaks
each item so the bartender can prepare them.

Usage:
  python3 inform_bartender.py
"""

import os, sys, json
import rospy
from std_msgs.msg import String

try:
    sys.path.insert(0, os.environ["PNP_HOME"] + '/scripts')
except:
    print("Please set PNP_HOME environment variable to PetriNetPlans folder.")
    sys.exit(1)

from pnp_cmd_ros import PNPCmd

speech_pub = None


def _say(p, text):
    global speech_pub
    if speech_pub is None:
        speech_pub = rospy.Publisher('/robot/speaking', String, queue_size=1)
    speech_pub.publish(String(data=text))
    p.exec_action('speak', text)
    rospy.sleep(1.5)
    speech_pub.publish(String(data=''))


def inform_bartender(p):
    # Wait for an order to be available
    rospy.loginfo("Waiting for an order …")
    msg = rospy.wait_for_message('/restaurant/order_result', String, timeout=60.0)

    if msg is None:
        _say(p, "No_order_received_yet.")
        return

    try:
        order = json.loads(msg.data)
    except json.JSONDecodeError:
        _say(p, "Invalid_order_received.")
        return

    if not order:
        _say(p, "The_order_is_empty.")
        return

    # Announce the order
    items = order.get("items", [])
    if not items:
        _say(p, "No_items_in_the_order.")
        return

    _say(p, "Hi,_a_customer_ordered_the_following:")

    for item in items:
        name = item.get("name", "unknown_item")
        qty = item.get("quantity", 1)
        line = f"{qty}_x_{name}".replace(' ', '_')
        _say(p, line)

    _say(p, "Please_prepare_the_order._Thank_you.")


if __name__ == "__main__":
    p = PNPCmd()
    p.begin()
    inform_bartender(p)
    p.end()
