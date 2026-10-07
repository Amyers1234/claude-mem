#!/usr/bin/env bash
# Set up claude-mem + an Obsidian second-brain vault on this machine.
#
#   ./setup.sh [--vault PATH] [--no-hook] [--skip-claude-mem]
#
#   --vault PATH        Where to create the vault (default: ~/Documents/SecondBrain).
#                       An existing vault is fine: template files never overwrite yours.
#   --no-hook           Don't add the auto-sync hooks to ~/.claude/settings.json.
#   --skip-claude-mem   Don't run the claude-mem installer (already installed).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAULT="${HOME}/Documents/SecondBrain"
ADD_HOOK=1
INSTALL_CLAUDE_MEM=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vault) VAULT="$2"; shift 2 ;;
    --no-hook) ADD_HOOK=0; shift ;;
    --skip-claude-mem) INSTALL_CLAUDE_MEM=0; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
VAULT="${VAULT/#\~/$HOME}"

step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing '$1'. $2" >&2
    exit 1
  fi
}

need python3 "Install Python 3.9+ (https://www.python.org/downloads/)."

# 1. claude-mem ---------------------------------------------------------------
if [[ $INSTALL_CLAUDE_MEM -eq 1 ]]; then
  step "Installing claude-mem (interactive installer from thedotmack/claude-mem)"
  need npx "Install Node.js 18+ (https://nodejs.org) and re-run."
  npx --yes claude-mem install
else
  step "Skipping claude-mem install"
fi

# 2. Vault --------------------------------------------------------------------
step "Creating Obsidian vault at ${VAULT}"
mkdir -p "$VAULT"
# Copy the template without overwriting anything that already exists.
(cd "$REPO_DIR/vault" && find . -type d) | while read -r d; do mkdir -p "$VAULT/$d"; done
(cd "$REPO_DIR/vault" && find . -type f) | while read -r f; do
  if [[ ! -e "$VAULT/$f" ]]; then
    cp "$REPO_DIR/vault/$f" "$VAULT/$f"
  fi
done

# 3. Sync script --------------------------------------------------------------
INSTALL_DIR="${HOME}/.claude-mem-obsidian"
step "Installing sync script to ${INSTALL_DIR}"
mkdir -p "$INSTALL_DIR"
cp "$REPO_DIR/scripts/claude_mem_to_obsidian.py" "$INSTALL_DIR/"
SYNC_CMD="python3 \"${INSTALL_DIR}/claude_mem_to_obsidian.py\" --vault \"${VAULT}\" --quiet"

# 4. Claude Code hooks --------------------------------------------------------
if [[ $ADD_HOOK -eq 1 ]]; then
  step "Adding auto-sync hooks (SessionStart + Stop) to ~/.claude/settings.json"
  SYNC_CMD="$SYNC_CMD" python3 - <<'PY'
import json, os, shutil
from pathlib import Path

path = Path.home() / ".claude" / "settings.json"
path.parent.mkdir(parents=True, exist_ok=True)
settings = {}
if path.exists():
    text = path.read_text(encoding="utf-8").strip()
    settings = json.loads(text) if text else {}
    shutil.copy2(path, path.with_suffix(".json.bak"))

cmd = os.environ["SYNC_CMD"]
hooks = settings.setdefault("hooks", {})
for event in ("SessionStart", "Stop"):
    groups = hooks.setdefault(event, [])
    # Drop any earlier version of our hook (e.g. a different vault path), then add the current one.
    for g in groups:
        g["hooks"] = [h for h in g.get("hooks", []) if "claude_mem_to_obsidian.py" not in h.get("command", "")]
    groups[:] = [g for g in groups if g.get("hooks")]
    groups.append({"hooks": [{"type": "command", "command": cmd, "timeout": 60}]})

path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
print(f"Updated {path} (backup: {path.with_suffix('.json.bak').name})" if path.with_suffix(".json.bak").exists()
      else f"Created {path}")
PY
fi

# 5. First sync ---------------------------------------------------------------
step "Running first sync"
if ! eval "$SYNC_CMD"; then
  echo "No claude-mem data yet — that's expected on a fresh install. Notes appear after your first Claude Code session."
fi

cat <<EOF

Done.
  1. Open Obsidian -> "Open folder as vault" -> ${VAULT}
  2. Start a Claude Code session in any project. claude-mem records it;
     the hooks copy it into "Claude Memory/" in your vault.
  3. Sync by hand any time:
       python3 "${INSTALL_DIR}/claude_mem_to_obsidian.py" --vault "${VAULT}"
  claude-mem's own viewer: http://localhost:37777
EOF
