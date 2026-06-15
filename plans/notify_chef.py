"""
notify_chef.py  –  PNP plan: tell the chef what to prepare.

Reads the last confirmed order from the ROS parameter server
(/restaurant/last_order), which persists across node restarts.
Also listens on /restaurant/order_result for live orders.

Usage:
  python3 notify_chef.py
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
listen_pub = None


def _say(p, text):
    global speech_pub
    if speech_pub is None:
        speech_pub = rospy.Publisher('/robot/speaking', String, queue_size=1)

    line = text.replace(' ', '_')
    speech_pub.publish(String(data=line))
    p.exec_action('speak', line)
    speech_pub.publish(String(data=''))


def _get_order():
    """Try to get the last order from rosparam (persisted) first,
    then fall back to waiting on the live topic."""
    # Check persisted order first
    if rospy.has_param('/restaurant/last_order'):
        order = rospy.get_param('/restaurant/last_order')
        if order:
            rospy.loginfo("Found persisted order on parameter server.")
            return order

    # Fall back to waiting for a live publish
    rospy.loginfo("No persisted order. Waiting for a new one …")
    msg = rospy.wait_for_message('/restaurant/order_result', String, timeout=None)
    if msg is None:
        return None
    try:
        return json.loads(msg.data)
    except json.JSONDecodeError:
        return None


def notify_chef(p):
    order = _get_order()

    if order is None:
        _say(p, "No order received yet.")
        return

    menu = order.get("menu", {})
    condiments = order.get("condiments", [])
    items = order.get("items_to_prepare", [])

    if not items:
        _say(p, "No items in the order.")
        return

    menu_name = menu.get("name", "Unknown menu")
    item_list = ", ".join(items)

    # ── Conversational announcement ──────────────────────────────────
    greeting = (
        f"Hey bossman, a customer has ordered the items on {menu_name}. "
        f"These include: {item_list}."
    )
    _say(p, greeting)

    if condiments:
        c_list = " and ".join(condiments)
        _say(p, f"They also want {c_list} on the side.")

    _say(p, "Could you prepare these for me?")

    rospy.sleep(1.0)
    _say(p, "Thank you.")


if __name__ == "__main__":
    p = PNPCmd()
    p.begin()
    notify_chef(p)
    p.end()
