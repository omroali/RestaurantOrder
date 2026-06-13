#!/bin/bash
#
# start_restaurant_voice.sh
#
# Launch the restaurant ordering voice pipeline in the background.
#
# Usage:
#   source start_restaurant_voice.sh              # use defaults
#   source start_restaurant_voice.sh small        # use small STT model
#   source start_restaurant_voice.sh base table_1 # custom model + table_id
#
# What it does:
#   1. Sources ROS and workspace setup.bash
#   2. Checks that faster-whisper is installed (warns if not)
#   3. Builds the restaurant_language_unit package (first time only)
#   4. Launches voice_listener + order_processor via roslaunch
#   5. Output goes to ~/.lcastor/restaurant_voice.log
#
# First-time setup (run once inside the container):
#   source ~/ros_ws/src/LCASTOR/restaurant_language_unit/setup_container.sh
#
# To stop:
#   pkill -f "restaurant_voice.launch"
#   or:  rosnode kill /voice_listener /order_processor

STT_MODEL="${1:-base}"
TABLE_ID="${2:-default}"

# ── Paths ─────────────────────────────────────────────────────────────
LCASTOR_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
RESTAURANT_DIR="$LCASTOR_DIR/restaurant_language_unit"
LOG_DIR="$HOME/.lcastor"
LOG_FILE="$LOG_DIR/restaurant_voice.log"
mkdir -p "$LOG_DIR"
MODEL_DIR="$HOME/.lcastor/whisper_models"
mkdir -p "$MODEL_DIR"

# ── ROS environment ───────────────────────────────────────────────────
if [ -f /opt/ros/noetic/setup.bash ]; then
    source /opt/ros/noetic/setup.bash
fi
if [ -f "$HOME/base_ws/devel/setup.bash" ]; then
    source "$HOME/base_ws/devel/setup.bash"
fi
if [ -f "$HOME/ros_ws/devel/setup.bash" ]; then
    source "$HOME/ros_ws/devel/setup.bash"
fi

# ── Dependency check ──────────────────────────────────────────────────
python3 -c "from faster_whisper import WhisperModel" 2>/dev/null || {
    echo ""
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║  faster-whisper is not installed.                          ║"
    echo "║                                                            ║"
    echo "║  Run the first-time setup (inside the container):          ║"
    echo "║    source ~/ros_ws/src/LCASTOR/restaurant_language_unit/   ║"
    echo "║           setup_container.sh                               ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo ""
    return 1 2>/dev/null || exit 1
}

# ── Build restaurant_language_unit if needed ──────────────────────────
PACKAGE_XML="$RESTAURANT_DIR/package.xml"
if [ -f "$PACKAGE_XML" ]; then
    if ! rospack list 2>/dev/null | grep -q restaurant_language_unit; then
        echo "[restaurant_voice] Building restaurant_language_unit package..."
        cd "$(dirname "$RESTAURANT_DIR")" && catkin build restaurant_language_unit --no-deps 2>&1 | tail -3
        if [ -f "$HOME/ros_ws/devel/setup.bash" ]; then
            source "$HOME/ros_ws/devel/setup.bash"
        fi
    fi
fi

# ── Warn if no USB microphone detected ────────────────────────────────
python3 -c "import pyaudio; p = pyaudio.PyAudio(); print(f'[restaurant_voice] Found {p.get_device_count()} audio devices'); p.terminate()" 2>/dev/null || {
    echo "[restaurant_voice] ⚠ pyaudio not working – check portaudio19-dev is installed"
}

# ── Launch ────────────────────────────────────────────────────────────
echo "[restaurant_voice] Starting pipeline (STT model: $STT_MODEL, table: $TABLE_ID)"
echo "[restaurant_voice] Logs → $LOG_FILE"

roslaunch restaurant_language_unit restaurant_voice.launch \
    model_dir:="$HOME/.lcastor/whisper_models" \
    stt_model:="$STT_MODEL" \
    table_id:="$TABLE_ID" \
    > "$LOG_FILE" 2>&1 &

PID=$!
echo "[restaurant_voice] Launched (PID: $PID)"
echo "[restaurant_voice] Monitor:  tail -f $LOG_FILE"
echo "[restaurant_voice] Stop:     pkill -f 'restaurant_voice.launch'"

# Save PID for later reference
echo "$PID" > "$LOG_DIR/restaurant_voice.pid"
