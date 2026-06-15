#!/bin/bash
#
# start_restaurant_voice_tiago.sh
#
# Launch the restaurant language unit in TIAGo-audio mode (background).
# Audio comes from TIAGo's mic via /audio/audio — no USB mic needed.
#
# Automatically starts audio_capture on TIAGo via SSH if not already running.
#
# Usage:
#   source start_restaurant_voice_tiago.sh
#   source start_restaurant_voice_tiago.sh small       # faster STT model
#   source start_restaurant_voice_tiago.sh base table_1 # custom model + table
#
# To stop:
#   pkill -f "restaurant_voice_tiago.launch"

STT_MODEL="${1:-base}"
TABLE_ID="${2:-default}"

LCASTOR_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
RESTAURANT_DIR="$LCASTOR_DIR/restaurant_language_unit"
LOG_DIR="$HOME/.lcastor"
LOG_FILE="$LOG_DIR/restaurant_voice_tiago.log"
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

# ── Derive TIAGo IP from ROS_MASTER_URI ───────────────────────────────
# e.g. http://10.68.0.1:11311 → 10.68.0.1
TIAGO_IP=$(echo "$ROS_MASTER_URI" | sed -n 's|http://\([^/:]*\).*|\1|p')
TIAGO_IP="${TIAGO_IP:-10.68.0.1}"
TIAGO_PW="${TIAGO_PW:-palroot}"  # only used as fallback

# ── Start audio_capture on TIAGo if not already publishing ────────────
rostopic list 2>/dev/null | grep -q "/audio/audio" || {
    echo "[restaurant_voice] /audio/audio not found — starting audio_capture on TIAGo ($TIAGO_IP) …"

    # Kill any existing audio_capture first
    ssh -o StrictHostKeyChecking=no root@"$TIAGO_IP" \
        'pkill -f audio_capture' 2>/dev/null || true
    sleep 1

    # Start audio_capture in background on TIAGo
    ssh -o StrictHostKeyChecking=no root@"$TIAGO_IP" \
        'source /opt/ros/noetic/setup.bash && \
         nohup roslaunch audio_capture capture.launch \
             device:=plughw:2,0 format:=wave sample_rate:=16000 \
             channels:=1 depth:=16 \
             > /tmp/audio_capture.log 2>&1 &'
    sleep 2

    # Wait for topic to appear
    for i in $(seq 1 10); do
        rostopic list 2>/dev/null | grep -q "/audio/audio" && break
        echo "  waiting for /audio/audio … ($i/10)"
        sleep 1
    done
}

# ── Dependency check ──────────────────────────────────────────────────
python3 -c "from faster_whisper import WhisperModel" 2>/dev/null || {
    echo ""
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║  faster-whisper is not installed.                          ║"
    echo "║  Run: source ~/ros_ws/src/LCASTOR/restaurant_language_unit/║"
    echo "║       setup_container.sh                                   ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo ""
    return 1 2>/dev/null || exit 1
}

# ── Final check ───────────────────────────────────────────────────────
rostopic list 2>/dev/null | grep -q "/audio/audio" || {
    echo "[restaurant_voice] ERROR: /audio/audio still not available. Aborting."
    return 1 2>/dev/null || exit 1
}

# ── Build if needed ───────────────────────────────────────────────────
if ! rospack list 2>/dev/null | grep -q restaurant_language_unit; then
    echo "[restaurant_voice] Building restaurant_language_unit package..."
    cd "$(dirname "$RESTAURANT_DIR")" && catkin build restaurant_language_unit --no-deps 2>&1 | tail -3
    if [ -f "$HOME/ros_ws/devel/setup.bash" ]; then
        source "$HOME/ros_ws/devel/setup.bash"
    fi
fi

# ── Launch ────────────────────────────────────────────────────────────
echo "[restaurant_voice] Starting pipeline (TIAGo audio, STT: $STT_MODEL, table: $TABLE_ID)"
echo "[restaurant_voice] Logs → $LOG_FILE"

roslaunch restaurant_language_unit restaurant_voice_tiago.launch \
    model_dir:="$HOME/.lcastor/whisper_models" \
    stt_model:="$STT_MODEL" \
    table_id:="$TABLE_ID" \
    audio_topic:=/audio/audio \
    channels:=1 \
    > "$LOG_FILE" 2>&1 &

PID=$!
echo "[restaurant_voice] Launched (PID: $PID)"
echo "[restaurant_voice] Monitor:  tail -f $LOG_FILE"
echo "[restaurant_voice] Stop:     pkill -f 'restaurant_voice_tiago.launch'"
echo "$PID" > "$LOG_DIR/restaurant_voice_tiago.pid"
