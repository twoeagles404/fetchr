#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  Fetchr Agent — Installer / Service Manager
#  Supports: Ubuntu 22.04 / Debian 12 / Proxmox LXC / macOS 13+
#
#  Usage:
#    ./install.sh            — full install + start system service
#    ./install.sh --docker   — print Docker/compose instructions instead
#    ./install.sh --uninstall — stop + remove the service
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
FETCHR_USER="${SUDO_USER:-$USER}"

# ── Colour helpers ────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC}  $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
err()  { echo -e "${RED}✗${NC}  $*" >&2; }
h()    { echo -e "\n${YELLOW}──${NC} $*"; }

echo ""
echo "╔══════════════════════════════════════╗"
echo "║   Fetchr Agent Installer  v2.1      ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ── Docker shortcut ───────────────────────────────────────────────────────────
if [[ "${1:-}" == "--docker" ]]; then
  echo "Docker / Proxmox LXC one-command setup:"
  echo ""
  echo "  cd $(dirname "$SCRIPT_DIR")"
  echo "  docker compose up -d"
  echo ""
  echo "Or build manually:"
  echo "  docker build -t fetchr $(dirname "$SCRIPT_DIR")"
  echo "  docker run -d --name fetchr -p 9876:9876 \\"
  echo "    -v \$(pwd)/downloads:/downloads \\"
  echo "    -v \$(pwd)/data:/data \\"
  echo "    --restart unless-stopped fetchr"
  echo ""
  exit 0
fi

# ── Uninstall ─────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--uninstall" ]]; then
  h "Removing Fetchr system service"
  if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    systemctl stop  fetchr-agent.service 2>/dev/null && ok "Service stopped"   || true
    systemctl disable fetchr-agent.service 2>/dev/null && ok "Service disabled" || true
    rm -f /etc/systemd/system/fetchr-agent.service
    systemctl daemon-reload
    ok "fetchr-agent.service removed"
  fi
  echo ""
  echo "Fetchr service has been removed. The agent files in $SCRIPT_DIR remain."
  exit 0
fi

# ── 1. Python version check ───────────────────────────────────────────────────
h "Checking Python"
if ! command -v python3 &>/dev/null; then
  err "python3 not found. Install Python 3.10+ and retry."
  exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")

if (( PY_MAJOR < 3 || ( PY_MAJOR == 3 && PY_MINOR < 10 ) )); then
  err "Python 3.10+ required (found $PY_VER). Install a newer version."
  exit 1
fi
ok "Python $PY_VER"

# ── 2. aria2c ─────────────────────────────────────────────────────────────────
h "Checking aria2c"
if command -v aria2c &>/dev/null; then
  ok "aria2c already installed ($(aria2c --version | head -1))"
else
  warn "aria2c not found — installing…"
  if [[ "$OSTYPE" == "darwin"* ]]; then
    command -v brew &>/dev/null && brew install aria2 || warn "Install aria2 manually: brew install aria2"
  elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    if command -v apt-get &>/dev/null; then sudo apt-get install -y aria2
    elif command -v dnf &>/dev/null; then  sudo dnf install -y aria2
    elif command -v pacman &>/dev/null; then sudo pacman -S --noconfirm aria2
    else warn "Unknown package manager. Install aria2 manually."; fi
  fi
  command -v aria2c &>/dev/null && ok "aria2c installed" || warn "aria2c not installed — downloads will use fallback mode"
fi

# ── 3. ffmpeg ─────────────────────────────────────────────────────────────────
h "Checking ffmpeg"
if command -v ffmpeg &>/dev/null; then
  ok "ffmpeg already installed"
else
  warn "ffmpeg not found — installing…"
  if [[ "$OSTYPE" == "darwin"* ]]; then
    command -v brew &>/dev/null && brew install ffmpeg || warn "Install ffmpeg manually: brew install ffmpeg"
  elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    if command -v apt-get &>/dev/null; then sudo apt-get install -y ffmpeg
    elif command -v dnf &>/dev/null; then sudo dnf install -y ffmpeg
    elif command -v pacman &>/dev/null; then sudo pacman -S --noconfirm ffmpeg
    else warn "Unknown package manager. Install ffmpeg manually."; fi
  fi
  command -v ffmpeg &>/dev/null && ok "ffmpeg installed" || warn "ffmpeg not installed — video/audio merging unavailable"
fi

# ── 4. Python virtual environment ────────────────────────────────────────────
h "Setting up Python environment"
if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
  ok "Virtual environment created at $VENV_DIR"
else
  ok "Virtual environment already exists"
fi

source "$VENV_DIR/bin/activate"

h "Installing Python packages"
pip install --upgrade pip --quiet
pip install -r "$SCRIPT_DIR/requirements.txt" --quiet
ok "Python packages installed"

# ── 5. Generate extension icons ──────────────────────────────────────────────
h "Generating extension icons"
python "$SCRIPT_DIR/generate_icons.py" 2>/dev/null && ok "Icons generated" || warn "Icon generation skipped (Pillow not installed)"

# ── 6. Systemd service (Linux — system-level, not user-level) ────────────────
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
  h "Installing systemd system service"

  # Resolve the real user for running the service (not root)
  if [[ "$FETCHR_USER" == "root" ]]; then
    warn "Running as root — service will run as root. For production, use a dedicated user."
    SVC_USER="root"
  else
    SVC_USER="$FETCHR_USER"
  fi

  UNIT_PATH="/etc/systemd/system/fetchr-agent.service"

  cat > "$UNIT_PATH" <<EOF
[Unit]
Description=Fetchr Download Agent
Documentation=https://github.com/yourusername/fetchr
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SVC_USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=$VENV_DIR/bin/python $SCRIPT_DIR/main.py
Restart=on-failure
RestartSec=5
# Allow the agent to create and write to the downloads folder
UMask=0022

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable fetchr-agent.service
  systemctl restart fetchr-agent.service

  # Wait a moment for the service to settle
  sleep 2
  if systemctl is-active --quiet fetchr-agent.service; then
    ok "fetchr-agent.service is running (system-level, survives reboots)"
  else
    warn "Service may not have started. Check: journalctl -u fetchr-agent.service -n 30"
  fi

else
  # macOS: use a simple launchd approach or manual start
  h "macOS: starting agent manually"
  echo ""
  echo "  To start the agent:"
  echo "  cd $(dirname "$SCRIPT_DIR") && source agent/.venv/bin/activate && python agent/main.py"
  echo ""
  echo "  For auto-start on login, create a launchd plist:"
  echo "  See: https://www.launchd.info/"
fi

# ── 7. Summary ────────────────────────────────────────────────────────────────
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")

echo ""
echo "════════════════════════════════════════════════"
echo "  ✅  Fetchr agent v2.1 installed!"
echo ""
echo "  Web UI (local):          http://127.0.0.1:9876/"
echo "  Web UI (your network):   http://${LOCAL_IP}:9876/"
echo ""
echo "  Service commands:"
echo "  systemctl status  fetchr-agent"
echo "  systemctl restart fetchr-agent"
echo "  journalctl -u fetchr-agent -f   # live logs"
echo ""
echo "  Browser extension:"
echo "  Load the extension/ folder in Chrome:"
echo "  chrome://extensions → Developer mode → Load unpacked"
echo "════════════════════════════════════════════════"
echo ""
