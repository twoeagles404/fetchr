#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  Fetchr Agent — installer
#  Tested on: Ubuntu 22.04, Debian 12, macOS 13+
# ─────────────────────────────────────────────────────────────
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"

echo ""
echo "╔══════════════════════════════════════╗"
echo "║        Fetchr Agent Installer        ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ── 1. Check Python 3.10+ ──────────────────────────────────
if ! command -v python3 &>/dev/null; then
  echo "❌  python3 not found. Install Python 3.10+ and retry."
  exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "✓  Python $PY_VER detected"

# ── 2. Install aria2c (primary download engine) ────────────
echo "→  Checking for aria2c…"
if command -v aria2c &>/dev/null; then
  echo "✓  aria2c already installed ($(aria2c --version | head -1))"
else
  echo "   aria2c not found — installing…"
  if [[ "$OSTYPE" == "darwin"* ]]; then
    if command -v brew &>/dev/null; then
      brew install aria2
    else
      echo "❌  Homebrew not found. Install it from https://brew.sh then re-run this script."
      echo "    Or install aria2 manually: brew install aria2"
      echo "    Fetchr will fall back to yt-dlp for downloads without aria2c."
    fi
  elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    if command -v apt-get &>/dev/null; then
      sudo apt-get install -y aria2
    elif command -v dnf &>/dev/null; then
      sudo dnf install -y aria2
    elif command -v pacman &>/dev/null; then
      sudo pacman -S --noconfirm aria2
    else
      echo "⚠  Could not auto-install aria2. Install it manually for your distro."
      echo "   Fetchr will fall back to yt-dlp for downloads without aria2c."
    fi
  fi

  if command -v aria2c &>/dev/null; then
    echo "✓  aria2c installed successfully"
  else
    echo "⚠  aria2c not installed — Fetchr will still work but downloads will be slower"
  fi
fi

# ── 3. Check for ffmpeg (needed for merging video+audio) ───
echo "→  Checking for ffmpeg…"
if command -v ffmpeg &>/dev/null; then
  echo "✓  ffmpeg already installed"
else
  echo "   ffmpeg not found — installing…"
  if [[ "$OSTYPE" == "darwin"* ]]; then
    command -v brew &>/dev/null && brew install ffmpeg || echo "⚠  Install ffmpeg manually: brew install ffmpeg"
  elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    if command -v apt-get &>/dev/null; then
      sudo apt-get install -y ffmpeg
    elif command -v dnf &>/dev/null; then
      sudo dnf install -y ffmpeg
    elif command -v pacman &>/dev/null; then
      sudo pacman -S --noconfirm ffmpeg
    else
      echo "⚠  Could not auto-install ffmpeg. Install it manually."
    fi
  fi
  command -v ffmpeg &>/dev/null && echo "✓  ffmpeg installed" || echo "⚠  ffmpeg not installed — video+audio merging may fail"
fi

# ── 4. Create venv ─────────────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
  echo "→  Creating virtual environment…"
  python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

# ── 5. Install dependencies ────────────────────────────────
echo "→  Installing Python packages…"
pip install --upgrade pip --quiet
pip install -r "$SCRIPT_DIR/requirements.txt" --quiet

echo "✓  Dependencies installed"

# ── 6. Generate icons ──────────────────────────────────────
echo "→  Generating extension icons…"
python "$SCRIPT_DIR/generate_icons.py"

# ── 7. Create systemd unit (Linux only) ───────────────────
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
  UNIT_PATH="$HOME/.config/systemd/user/fetchr-agent.service"
  mkdir -p "$(dirname "$UNIT_PATH")"
  cat > "$UNIT_PATH" <<EOF
[Unit]
Description=Fetchr Download Agent
After=network.target

[Service]
ExecStart=$VENV_DIR/bin/python $SCRIPT_DIR/main.py
WorkingDirectory=$SCRIPT_DIR
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

  systemctl --user daemon-reload
  systemctl --user enable fetchr-agent.service
  systemctl --user start  fetchr-agent.service
  echo "✓  systemd user service installed and started"
  echo "   → fetchr-agent.service  (auto-starts on login)"
else
  echo ""
  echo "ℹ  macOS: start the agent manually with:"
  echo "   cd $(dirname "$SCRIPT_DIR") && source agent/.venv/bin/activate && python agent/main.py"
fi

echo ""
echo "════════════════════════════════════════"
echo "  Fetchr agent running at:"
echo "  http://127.0.0.1:9876"
echo ""
echo "  Next: load the extension/  folder in"
echo "  Chrome → chrome://extensions (dev mode)"
echo "════════════════════════════════════════"
echo ""
