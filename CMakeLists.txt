cmake_minimum_required(VERSION 3.0.2)
project(restaurant_language_unit)

find_package(catkin REQUIRED COMPONENTS
  rospy
  std_msgs
  std_srvs
)

catkin_package()

catkin_install_python(PROGRAMS
  nodes/transcriber_node.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)

install(DIRECTORY launch docs config
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}
)

install(DIRECTORY src/
  DESTINATION ${CATKIN_PACKAGE_PYTHON_DESTINATION}
)

install(FILES menu.json
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}
)

install(DIRECTORY plans/
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}/plans
  FILES_MATCHING PATTERN "*.py"
)
