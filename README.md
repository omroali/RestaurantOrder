# Restaurant Language Unit

Voice-driven restaurant ordering for TIAGo using faster-whisper STT and PNP planning.

## Structure

```
restaurant_language_unit/
├── nodes/
│   └── transcriber_node.py        ← always-on STT (audio → text)
├── plans/
│   ├── take_order_full.py         ← customer: take order via voice
│   ├── take_order_voice.py        ← simple test plan
│   └── inform_bartender.py        ← bartender: announce order
├── src/
│   ├── streaming_stt.py           ← VAD + faster-whisper engine
│   ├── streaming_stt_ros.py       ← ROS audio adapter
│   ├── dialogue_manager.py        ← order dialogue & confirmation
│   ├── order_parser.py            ← natural language order parsing
│   ├── order_state.py             ← order state machine
│   ├── models.py                  ← data models
│   ├── utils.py                   ← text utilities
│   └── ipc.py                     ← ROS topic constants
├── launch/
│   └── restaurant_voice_tiago.launch
├── menu.json                      ← restaurant menu
├── requirements_container.txt     ← Docker container deps
├── requirements_local.txt         ← local venv testing deps
└── docs/
    └── TIAGo_Audio_Setup.md       ← audio capture setup guide
```

## Quick start — Robot mode

```bash
# 1. Start audio on TIAGo (auto via launcher)
source start_restaurant_voice_tiago.sh

# roslaunch audio_capture capture.launch device:=plughw:2,0 format:=wave sample_rate:=16000 channels:=1 depth:=16
roslaunch audio_capture capture.launch device:=plughw:CARD=Device,DEV=0 format:=wave sample_rate:=16000 channels:=1 depth:=16

# Or manually:
rosrun restaurant_language_unit transcriber_node.py _model:=small

# 2. Run the customer plan
python3 plans/take_order_full.py

# 3. After order confirmed, run the bartender plan
python3 plans/inform_bartender.py
```

## Quick start — Simulation mode

```bash
# 1. Mock TTS
python3 /tmp/laptop_tts.py &

# 2. Run the plan
python3 plans/take_order_full.py

# 3. Type what the customer "says" in another terminal:
rostopic pub /transcriber/text std_msgs/String "data: 'Can I have a cheeseburger?'" -1
```

## Local testing (no ROS, venv)

```bash
python3 -m venv .venv && source .venv/bin/activate
# Core dialogue has no external deps — test directly:
python3 -c "
import json
from src.dialogue_manager import DialogueManager
with open('menu.json') as f:
    menu = json.load(f)
m = DialogueManager(menu)
resp, done = m.process_input('Can I have a cheeseburger and a coke?')
print(resp)
"
```

## ROS topics

| Topic | Type | Description |
|---|---|---|
| `/transcriber/text` | String | Live transcriptions |
| `/robot/speaking` | String | Self-echo filter text |
| `/restaurant/order_result` | String (JSON) | Confirmed order output |
