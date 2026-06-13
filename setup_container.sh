#!/bin/bash
#
# setup_container.sh
#
# First-time setup for the restaurant_language_unit inside the
# LCASTOR docker container (Ubuntu 20.04 / Python 3.8 / ROS Noetic).
#
# Run ONCE inside the container:
#   source ~/ros_ws/src/LCASTOR/restaurant_language_unit/setup_container.sh
#

set -e

echo "========================================"
echo " Restaurant Language Unit – Setup"
echo "========================================"

# ── 1. System packages ────────────────────────────────────────────────
echo ""
echo "[1/4] Installing system packages..."

sudo apt-get update -qq

# PortAudio dev headers (needed to build PyAudio)
sudo apt-get install -y -qq portaudio19-dev

# Already in the container but verify
sudo apt-get install -y -qq python3-pip python3-dev

echo "  ✓ System packages installed."

# ── 2. Python dependencies ────────────────────────────────────────────
echo ""
echo "[2/4] Installing Python dependencies (Python 3.8 compatible)..."

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
pip3 install --no-cache-dir --no-build-isolation av==10.0.0
pip3 install --no-cache-dir -r "$SCRIPT_DIR/requirements_container.txt"

echo "  ✓ Python packages installed."

# ── 3. Verify STT engine ──────────────────────────────────────────────
echo ""
echo "[3/4] Verifying faster-whisper..."

python3 -c "
from faster_whisper import WhisperModel
print('  faster-whisper imported OK')
" 2>&1

echo "  ✓ STT engine ready."

# ── 4. Build ROS package ──────────────────────────────────────────────
echo ""
echo "[4/4] Building restaurant_language_unit ROS package..."

cd ~/ros_ws
catkin build restaurant_language_unit --no-deps

echo "  ✓ ROS package built."

# ── Done ──────────────────────────────────────────────────────────────
echo ""
echo "========================================"
echo " Setup complete!"
echo ""
echo " Start the voice pipeline:"
echo "   source ~/ros_ws/src/LCASTOR/restaurant_language_unit/start_restaurant_voice.sh"
echo ""
echo " Or for foreground debugging:"
echo "   roslaunch restaurant_language_unit restaurant_voice.launch"
echo "========================================"
