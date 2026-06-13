# TIAGo Audio Capture Setup

How to enable raw audio streaming from TIAGo's built-in Andrea microphone array
so the `restaurant_language_unit` can capture speech from the robot without any
USB hardware.

---

## 1. Check audio hardware

SSH into the robot and list capture devices.  **Card 1 (`AndreaMA`)** is the
built-in stereo mic array on all TIAGo models (29, 89, 125).

```bash
ssh root@<robot_ip>
arecord -l
```

Expected output:

```
card 0: PAL_INTEL [HDA Intel PCH], device 0: ALC887-VD Analog
card 1: PAL_ANDREA [AndreaMA], device 0: USB Audio       ← this one
card 2: Device [USB Audio Device], device 0: USB Audio    ← external USB (if plugged)
```

The device string is `hw:1,0`.

---

## 2. Install `audio_common` (one-time, per robot)

```bash
# On the robot
apt-get update
apt-get install -y ros-noetic-audio-common
```

Verify:

```bash
dpkg -l | grep ros-noetic-audio-common
# or
rospack find audio_capture
```

---

## 3. Start audio capture

```bash
roslaunch audio_capture capture.launch device:=hw:1,0 format:=wave sample_rate:=16000 channels:=2 depth:=16
```

This publishes raw 16-bit PCM audio on `/audio/raw` (`audio_common_msgs/AudioData`).

Verify from your dev machine:

```bash
rostopic list | grep audio
# Should show:  /audio/raw
rostopic hz /audio/raw
# Should show:  ~100 Hz  (10 ms chunks)
```

---

## 4. Make audio capture persist across reboots

### Option A – Add to TIAGo's tmule launch (recommended)

Edit TIAGo's tmule configuration to include the audio capture node.  This way
audio starts automatically whenever the robot software is launched.

1.  Locate the tmule YAML config (usually in `tiago_bringup`):
    ```bash
    roscd tiago_bringup
    ls tmule/
    ```

2.  Add a new node entry for `audio_capture`:
    ```yaml
    - name: audio_capture
      pkg:  audio_capture
      type: audio_capture
      launch: capture.launch
      args:  device:=hw:1,0
    ```

3.  Relaunch the robot software:
    ```bash
    tmule -c <config>.yaml -W 3 relaunch
    ```

### Option B – Systemd service (runs independently of ROS launch)

Create `/etc/systemd/system/tiaGo-audio-capture.service`:

```ini
[Unit]
Description=TIAGo Audio Capture
After=network.target

[Service]
Type=simple
User=pal
Environment="ROS_MASTER_URI=http://localhost:11311"
Environment="ROS_IP=10.68.0.1"
ExecStart=/opt/ros/noetic/bin/roslaunch audio_capture capture.launch device:=hw:1,0 format:=wave sample_rate:=16000 channels:=2 depth:=16
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
systemctl enable tiaGo-audio-capture.service
systemctl start tiaGo-audio-capture.service
systemctl status tiaGo-audio-capture.service
```

### Option C – Crontab @reboot (simplest, least robust)

```bash
crontab -e
# Add:
@reboot /opt/ros/noetic/bin/roslaunch audio_capture capture.launch device:=hw:1,0 format:=wave sample_rate:=16000 channels:=2 depth:=16 &
```

---

## 5. Deploy to multiple TIAGo robots

The setup is identical for all TIAGo models (29, 89, 125).  Script it:

```bash
#!/bin/bash
# deploy_audio_capture.sh – run once per robot

ROBOT_IP="${1:?Usage: $0 <robot_ip>}"
ROBOT_PW="palroot"

sshpass -p "$ROBOT_PW" ssh root@"$ROBOT_IP" '
    apt-get update && apt-get install -y ros-noetic-audio-common &&
    echo "Installed audio_common on $(hostname)"
'

echo "Done.  Start capture with:"
echo "  sshpass -p '$ROBOT_PW' ssh root@$ROBOT_IP 'roslaunch audio_capture capture.launch device:=hw:1,0 format:=wave sample_rate:=16000 channels:=2 depth:=16'"
```

---

## 6. Launch the restaurant language unit

Once `/audio/raw` is publishing from TIAGo, start the restaurant pipeline on your
dev machine:

```bash
# Quick launch
roslaunch restaurant_language_unit restaurant_voice_tiago.launch

# With custom params
roslaunch restaurant_language_unit restaurant_voice_tiago.launch \
    audio_topic:=/audio/raw \
    stt_model:=small \
    wake_words:="hey waiter, order please"

# Background mode
source ~/ros_ws/src/LCASTOR/restaurant_language_unit/start_restaurant_voice_tiago.sh
```

---

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `/audio/raw` missing | `audio_capture` not running | Start capture on robot (step 3) |
| `/audio/raw` exists but no data | Wrong ALSA device | Try `device:=hw:0,0` or `device:=hw:2,0` |
| `roslaunch` not found on robot | ROS not sourced | `source /opt/ros/noetic/setup.bash` first |
| VAD never triggers | Threshold too high | Lower `vad_threshold` param (try 200) |
| High CPU on dev machine | Whisper model too large | Use `stt_model:=tiny` or `stt_model:=base` |
| No speech detected | Andrea mic needs gain | `alsamixer -c 1` → increase Capture level |

---

## 8. Audio pipeline reference

```
TIAGo 89                              Dev machine (docker)
───────                               ────────────────────
Andrea mic array                      restaurant_language_unit
   │                                       │
   ▼                                       ▼
ALSA (hw:1,0)                   voice_listener_ros_node
   │                                  │
   ▼                                  ▼
audio_capture node        StreamingTranscriberROS
   │                                  │
   ▼                                  ▼
/audio/raw ───────── ROS TCP ─────→ push_chunk()
  (AudioData)                        │
                                     ▼
                              _process_loop()
                                     │
                                     ▼
                              faster-whisper
                                     │
                                     ▼
                              /restaurant/transcript
                                     │
                                     ▼
                              order_processor_node
                                     │
                                     ▼
                              /restaurant/order_result
```
