#!/bin/sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Installing uv and ffmpeg..."
if [ "$(uname -s)" = "Darwin" ]; then
    if ! command -v brew >/dev/null 2>&1; then
        echo "Error: Homebrew is not installed. Install it from https://brew.sh first."
        exit 1
    fi
    brew install uv ffmpeg
elif [ -f /etc/os-release ]; then
    # Source os-release to get ID and ID_LIKE
    . /etc/os-release
    case "$ID $ID_LIKE" in
        *debian*|*ubuntu*)
            sudo apt-get install -y uv ffmpeg ;;
        *fedora*|*rhel*|*centos*)
            sudo dnf install -y uv ffmpeg ;;
        *arch*|*manjaro*)
            sudo pacman -S --noconfirm uv ffmpeg ;;
        *)
            echo "Error: Unsupported Linux distribution '$ID'. Install uv and ffmpeg manually."
            exit 1 ;;
    esac
else
    echo "Error: /etc/os-release not found. Install uv and ffmpeg manually."
    exit 1
fi

echo ""
echo "Populating uv cache (this may take a while on first run)..."
"$SCRIPT_DIR/1-import-pam-recordings.py" --version
"$SCRIPT_DIR/2-analyze-PAM-recordings.py" --version
"$SCRIPT_DIR/3-extract-top-detections.py" --version

echo "Done."
