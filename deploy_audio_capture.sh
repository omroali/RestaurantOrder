#!/bin/bash
#
# deploy_audio_capture.sh
#
# Install and start audio_capture on one or more TIAGo robots.
#
# Usage:
#   ./deploy_audio_capture.sh 10.68.0.1             # single robot (Ethernet)
#   ./deploy_audio_capture.sh 192.168.1.29          # single robot (WiFi)
#   ./deploy_audio_capture.sh 29 89 125             # multiple robots by ID
#   ./deploy_audio_capture.sh --all                 # all three robots via WiFi
#
# What it does:
#   1. SSHes into each robot
#   2. Installs ros-noetic-audio-common (if not present)
#   3. Starts audio_capture on hw:1,0 (Andrea mic array)
#   4. Verifies /audio/raw is publishing
#

set -e

ROBOT_PW="${ROBOT_PW:-palroot}"  # only used as fallback if keys not set up

# ── Resolve robot IDs to IPs ───────────────────────────────────────────

resolve_ip() {
    case "$1" in
        29)  echo "192.168.1.29"  ;;
        89)  echo "192.168.1.89"  ;;
        125) echo "192.168.1.125" ;;
        *)   echo "$1" ;;  # assume it's already an IP
    esac
}

# ── Deploy to one robot ────────────────────────────────────────────────

deploy_one() {
    local ip="$1"
    echo ""
    echo "========================================"
    echo " Deploying audio_capture to $ip …"
    echo "========================================"

    ssh -o StrictHostKeyChecking=no root@"$ip" '
        set -e

        # 1. Install audio_common
        if dpkg -l | grep -q ros-noetic-audio-common; then
            echo "[OK] ros-noetic-audio-common already installed."
        else
            echo "[*] Installing ros-noetic-audio-common …"
            apt-get update -qq
            apt-get install -y -qq ros-noetic-audio-common
            echo "[OK] Installed."
        fi

        # 2. Kill any existing audio_capture
        pkill -f "audio_capture" 2>/dev/null || true
        sleep 1

        # 3. Start audio capture
        echo "[*] Starting audio_capture (hw:1,0) …"
        source /opt/ros/noetic/setup.bash
        nohup roslaunch audio_capture capture_to_topic.launch device:=hw:1,0 \
            > /tmp/audio_capture.log 2>&1 &
        sleep 2

        # 4. Verify
        if rostopic list 2>/dev/null | grep -q "/audio/raw"; then
            echo "[OK] /audio/raw is publishing on $(hostname)"
        else
            echo "[WARN] /audio/raw not detected — check /tmp/audio_capture.log"
        fi
    '
    echo "Done with $ip."
}

# ── Main ───────────────────────────────────────────────────────────────

if [ $# -eq 0 ]; then
    echo "Usage: $0 <robot_id_or_ip> [<robot_id_or_ip> ...]"
    echo "       $0 --all"
    echo ""
    echo "Examples:"
    echo "  $0 89                    # deploy to TIAGo 89 via WiFi"
    echo "  $0 10.68.0.1             # deploy via Ethernet IP"
    echo "  $0 29 89 125             # deploy to all three"
    echo "  $0 --all                 # same as above"
    exit 1
fi

if [ "$1" = "--all" ]; then
    set -- 29 89 125
fi

for target in "$@"; do
    ip="$(resolve_ip "$target")"
    deploy_one "$ip"
done

echo ""
echo "========================================"
echo " All deployments complete."
echo ""
echo " Verify with:  rostopic list | grep audio"
echo " (ensure ROS_MASTER_URI is set to the robot)"
echo "========================================"
