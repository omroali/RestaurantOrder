#!/usr/bin/env python3
"""
order_processor_node.py  –  ROS node: dialogue + order parsing & confirmation.

Drop this into a ROS package and launch with::

    rosrun restaurant_language_unit order_processor_node.py

or via the provided launch file::

    roslaunch restaurant_language_unit restaurant_voice.launch
"""

import json
import os
import sys
from typing import Any, Dict, List

import rospy

# Ensure the restaurant_language_unit package root is on the Python path.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG  = os.path.dirname(_HERE)  # restaurant_language_unit/
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from src.ros_bridge import RosBus
from order_processor import OrderProcessorNode


def _load_menu(menu_param: str = "~menu") -> List[Dict[str, Any]]:
    """
    Load menu data from a ROS parameter.

    The parameter may be:
      - a YAML list loaded via <rosparam>
      - a path to a JSON file
    Returns a list of menu item dicts.
    """
    if rospy.has_param(menu_param):
        raw = rospy.get_param(menu_param)
        if isinstance(raw, list):
            return raw

    # Fallback: try loading menu.json next to this script or from the
    # config directory.
    candidates = [
        os.path.join(_PKG, "menu.json"),
        os.path.join(_PKG, "config", "menu.json"),
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)

    rospy.logwarn(
        "No menu data found via ~menu param or menu.json.  "
        "OrderProcessor will start with an empty menu."
    )
    return []


def main() -> None:
    rospy.init_node("order_processor", anonymous=False)

    # ── load menu ───────────────────────────────────────────────────────
    menu = _load_menu()

    # ── table_id (supports multi-table deployments in the future) ───────
    table_id = rospy.get_param("~table_id", "default")

    # ── create the ROS bus ──────────────────────────────────────────────
    bus = RosBus()

    # ── instantiate the existing (transport-agnostic) node ──────────────
    node = OrderProcessorNode(bus=bus, menu=menu, table_id=table_id)

    rospy.loginfo(
        f"OrderProcessor ready (table={table_id}, "
        f"{len(menu)} menu items loaded).  "
        "Waiting for wake word …"
    )

    # ── block on ROS spin (replaces the time.sleep loop) ────────────────
    try:
        bus.spin()  # rospy.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.stop()
        rospy.loginfo("OrderProcessor stopped.")


if __name__ == "__main__":
    main()
