#!/bin/sh
# Bhap installer for Linux and macOS — no sudo, nothing installed without asking.
#
#   curl -fsSL https://raw.githubusercontent.com/dracofe/Bhap/main/scripts/install.sh | sh
#
# What it does (each download step asks first; pass -y to accept all):
#   1. installs `uv` (Python tool manager) into ~/.local/bin if it is missing
#   2. installs Bhap as an isolated tool:  uv tool install "bhap-ai[all] @ git+<repo>"
#   3. installs Ollama into ~/.local/opt/ollama if it is missing (official release, no sudo)
#   4. runs `bhap setup`, which suggests models for your hardware and asks before downloading
# After that Bhap works fully offline. Environment overrides: BHAP_REPO, BHAP_REF.
set -eu

REPO="${BHAP_REPO:-https://github.com/dracofe/Bhap}"
REF="${BHAP_REF:-main}"
YES=0
[ "${1:-}" = "-y" ] && YES=1
BIN="$HOME/.local/bin"

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
ask() {
  [ "$YES" = 1 ] && return 0
  printf '%s [Y/n] ' "$1"
  read -r answer </dev/tty || answer=""
  case "$answer" in [nN]*) return 1 ;; *) return 0 ;; esac
}

case "$(uname -s)" in
  Linux|Darwin) ;;
  *) warn "Use Windows instructions in docs/installation.md (pipx/uv on PowerShell)."; exit 1 ;;
esac
mkdir -p "$BIN"
case ":$PATH:" in *":$BIN:"*) ;; *) export PATH="$BIN:$PATH"; PATH_HINT=1 ;; esac

# 1. uv
if ! command -v uv >/dev/null 2>&1; then
  if ask "uv (Python tool installer, ~30 MB) is needed. Install it into $BIN?"; then
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$BIN" INSTALLER_NO_MODIFY_PATH=1 sh
  else
    warn "Without uv you can still install Bhap with: pipx install \"bhap-ai[all] @ git+$REPO@$REF\""; exit 1
  fi
fi

# 2. Bhap
say "Installing Bhap from $REPO ($REF)"
uv tool install --force "bhap-ai[all] @ git+$REPO@$REF"

# 3. Ollama (local model runtime)
if ! command -v ollama >/dev/null 2>&1 && [ ! -x "$HOME/.local/opt/ollama/bin/ollama" ]; then
  if [ "$(uname -s)" = "Darwin" ]; then
    warn "Install Ollama for macOS from https://ollama.com/download, then run: bhap setup"
  elif ask "Ollama (local AI runtime, ~1.5 GB download) is not installed. Install it into ~/.local/opt/ollama (no sudo)?"; then
    arch="$(uname -m)"; case "$arch" in x86_64) arch=amd64 ;; aarch64|arm64) arch=arm64 ;; esac
    mkdir -p "$HOME/.local/opt/ollama"
    curl -fL "https://github.com/ollama/ollama/releases/latest/download/ollama-linux-$arch.tar.zst" \
      | tar --zstd -x -C "$HOME/.local/opt/ollama"
    ln -sf "$HOME/.local/opt/ollama/bin/ollama" "$BIN/ollama"
  fi
fi

# 4. desktop app shortcut (Linux application menu)
if [ "$(uname -s)" = "Linux" ] && ask "Add Bhap to your application menu (desktop app)?"; then
  "$BIN/bhap" app --install-shortcut || true
fi

# 5. models (Bhap asks before every download; starts Ollama by itself when needed)
say "Choosing local models"
if [ "$YES" = 1 ]; then bhap setup --download --yes || true; else bhap setup </dev/tty || true; fi

say "Done. Terminal: run  bhap    ·  Desktop app: run  bhap app  (or open Bhap from your app menu)"
say "Then: /upload <file>  — and just ask. Works offline once models are downloaded."
[ "${PATH_HINT:-0}" = 1 ] && warn "Add $BIN to your PATH (fish: fish_add_path ~/.local/bin; bash/zsh: export PATH=\"\$HOME/.local/bin:\$PATH\")"
exit 0
