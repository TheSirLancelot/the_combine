#!/bin/bash
# Install the launchd agents on macOS.
#
# Fills in the two placeholders the plists carry and loads them. Both exist
# because launchd is not a shell: it starts with a minimal PATH and no working
# directory, so `uv` and the repo path have to be absolute. Hardcoding them in
# the committed plist would break the moment the repo moved, so they are
# substituted at install time from wherever this script is run.
#
#   ./scripts/install_agents.sh            # the Discord bot only
#   ./scripts/install_agents.sh --with-app # and the Streamlit app
#   ./scripts/install_agents.sh --uninstall
#
# Run it again after pulling; it reloads rather than duplicating.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
WANT=("com.thecombine.bot")

for arg in "$@"; do
  case "$arg" in
    --with-app) WANT+=("com.thecombine.app") ;;
    --uninstall) UNINSTALL=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [[ -n "${UNINSTALL:-}" ]]; then
  for label in com.thecombine.bot com.thecombine.app; do
    plist="$AGENTS/$label.plist"
    [[ -f "$plist" ]] || continue
    launchctl unload -w "$plist" 2>/dev/null || true
    rm -f "$plist"
    echo "removed $label"
  done
  exit 0
fi

UV="$(command -v uv || true)"
if [[ -z "$UV" ]]; then
  for candidate in /opt/homebrew/bin/uv /usr/local/bin/uv "$HOME/.local/bin/uv"; do
    [[ -x "$candidate" ]] && UV="$candidate" && break
  done
fi
if [[ -z "$UV" ]]; then
  echo "cannot find uv. install it, or set UV=/path/to/uv and re-run." >&2
  exit 1
fi

# launchd writes stdout and stderr to these paths and fails to start the job if
# the directory is absent. logs/ is gitignored, so a fresh clone has none.
mkdir -p "$REPO/logs"
mkdir -p "$AGENTS"

echo "repo: $REPO"
echo "uv:   $UV"

for label in "${WANT[@]}"; do
  src="$REPO/scripts/$label.plist"
  dest="$AGENTS/$label.plist"
  [[ -f "$src" ]] || { echo "missing $src" >&2; exit 1; }
  # Unload an existing copy first: loading over a loaded label is an error, and
  # a stale plist keeps running the old command.
  [[ -f "$dest" ]] && launchctl unload -w "$dest" 2>/dev/null || true
  sed -e "s|&lt;REPO&gt;|$REPO|g" -e "s|<REPO>|$REPO|g" \
      -e "s|&lt;UV&gt;|$UV|g"     -e "s|<UV>|$UV|g" "$src" > "$dest"
  launchctl load -w "$dest"
  echo "loaded $label"
done

echo
echo "status:"
launchctl list | grep thecombine || echo "  nothing running yet, check the logs"
echo
echo "The second column is the last exit code. 0 or '-' is fine; anything else"
echo "means it started and died, and logs/ will say why:"
for label in "${WANT[@]}"; do
  short="${label##*.}"
  echo "  tail -f $REPO/logs/$short.err"
done
