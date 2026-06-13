#!/usr/bin/env python3
"""
alsa_audio_bridge.py  –  Minimal ALSA → ROS audio bridge.

Captures from an ALSA device and publishes to a ROS topic with
proper sample-rate conversion using the 'soxr' library.

No GStreamer.  No audio_capture.  No resampling lies.

Usage (on TIAGo):
    python3 alsa_audio_bridge.py _device:=hw:2,0 _native_rate:=44100
"""

import alsaaudio
import numpy as np
import rospy
import soxr
from audio_common_msgs.msg import AudioData


def main():
    rospy.init_node("alsa_audio_bridge", anonymous=False)

    device      = rospy.get_param("~device",       "hw:2,0")
    native_rate = rospy.get_param("~native_rate",   44100)
    target_rate = rospy.get_param("~target_rate",   16000)
    channels    = rospy.get_param("~channels",      1)
    period_size = rospy.get_param("~period_size",   441)   # 10ms @ 44100

    pub = rospy.Publisher("/audio/audio", AudioData, queue_size=20)

    # Open ALSA capture at native rate
    inp = alsaaudio.PCM(
        alsaaudio.PCM_CAPTURE,
        alsaaudio.PCM_NORMAL,
        device=device,
    )
    inp.setchannels(channels)
    inp.setrate(native_rate)
    inp.setformat(alsaaudio.PCM_FORMAT_S16_LE)
    inp.setperiodsize(period_size)

    # Resampler: native → target (e.g. 44100 → 16000)
    resampler = soxr.ResampleStream(
        in_rate=native_rate,
        out_rate=target_rate,
        num_channels=channels,
        dtype='int16',
    )

    rospy.loginfo(
        f"ALSA bridge: {device} @ {native_rate}Hz → /audio/audio @ {target_rate}Hz"
    )

    try:
        while not rospy.is_shutdown():
            length, data = inp.read()
            if length > 0:
                # Convert raw bytes → int16 numpy array
                samples = np.frombuffer(data, dtype=np.int16)

                # Resample: 44100 → 16000
                out = resampler.process(samples)

                if out.size > 0:
                    msg = AudioData()
                    msg.data = out.tobytes()
                    pub.publish(msg)
    except KeyboardInterrupt:
        pass
    finally:
        inp.close()
        rospy.loginfo("ALSA bridge stopped.")


if __name__ == "__main__":
    main()
