cmake_minimum_required(VERSION 3.0.2)
project(restaurant_language_unit)

find_package(catkin REQUIRED COMPONENTS
  rospy
  std_msgs
  std_srvs
)

catkin_package()

# Install Python nodes so rosrun / roslaunch can find them
catkin_install_python(PROGRAMS
  nodes/transcriber_node.py
  nodes/voice_listener_ros_node.py
  nodes/voice_listener_node.py
  nodes/order_processor_node.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)

# Install launch files and config
install(DIRECTORY launch config
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}
)

# Install the python library modules
install(DIRECTORY src/
  DESTINATION ${CATKIN_PACKAGE_PYTHON_DESTINATION}
)

install(PROGRAMS
  voice_listener.py
  order_processor.py
  run_voice.py
  main.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)

install(FILES menu.json
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}
)
