**On the tiago**
make sure the ros-${ROS_DISTRO}-audio-common-msgs package is installed
roslaunch audio_capture capture.launch device:=plughw:2,0 format:=wave sample_rate:=16000 channels:=1 depth:=16


**On docker**
<!--launch the transriber:-->
rosrun restaurant_language_unit transcriber_node.py _model:=small _vad_threshold:=500 _confirm_delay:=1.5 _silence_dur:=0.8
<!--launch the plan:-->
python3 ~/ros_ws/src/LCASTOR/restaurant_language_unit/plans/take_order_full.py
