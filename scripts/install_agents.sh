#!/bin/bash
# Install the launchd agents on macOS.
#
# Fills in the two placeholders the plists carry and loads them. Both exist
# because launchd is not a shell: it starts with a minimal PATH and no working
# directory, so `uv` and the repo path have to be absolute. Hardcoding them in
# the committed plist would break the moment the repo moved, so they are
# substituted at install time from wherever this script is run.
#
#   ./scripts/install_agents.sh                        # the Discord bot only
#   ./scripts/install_agents.sh --with-app             # and the Streamlit app
#   ./scripts/install_agents.sh --with-app --bind 0.0.0.0   # reachable on the LAN
#   ./scripts/install_agents.sh --uninstall
#
# The app has NO login of its own and every page load acts as your ESPN session,
# so binding 0.0.0.0 means anyone who can reach this machine can read the rosters
# and drive requests as you. On a home network that is usually an acceptable
# trade; on anything shared it is not. 127.0.0.1 plus the Cloudflare tunnel with
# an Access policy is the version that authenticates.
#
# Run it again after pulling; it reloads rather than duplicating.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENTS="$HOME/Library/LaunchAgents"
WANT=("com.thecombine.bot")
BIND="127.0.0.1"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-app) WANT+=("com.thecombine.app") ;;
    --bind) BIND="${2:?--bind needs an address}"; shift ;;
    --bind=*) BIND="${1#*=}" ;;
    --uninstall) UNINSTALL=1 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
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
if [[ " ${WANT[*]} " == *com.thecombine.app* ]]; then
  echo "app:  http://$BIND:8501"
  if [[ "$BIND" != "127.0.0.1" && "$BIND" != "localhost" ]]; then
    echo "      NOTE: the app has no login. anything that can reach $BIND:8501"
    echo "      can read your rosters and make ESPN requests as you."
  fi
fi

UID_NUM="$(id -u)"

for label in "${WANT[@]}"; do
  src="$REPO/scripts/$label.plist"
  dest="$AGENTS/$label.plist"
  [[ -f "$src" ]] || { echo "missing $src" >&2; exit 1; }

  # Remove any existing copy first. Loading over a loaded label is an error, and
  # a stale plist keeps running the old command.
  launchctl bootout "gui/$UID_NUM/$label" 2>/dev/null || true
  launchctl unload -w "$dest" 2>/dev/null || true

  sed -e "s|&lt;REPO&gt;|$REPO|g" -e "s|<REPO>|$REPO|g" \
      -e "s|&lt;UV&gt;|$UV|g"     -e "s|<UV>|$UV|g" \
      -e "s|&lt;BIND&gt;|$BIND|g" -e "s|<BIND>|$BIND|g" "$src" > "$dest"

  # Validate before loading. A malformed plist fails at load with a message
  # that does not say which key is wrong, and a bad substitution is silent.
  if ! plutil -lint "$dest" >/dev/null; then
    echo "generated plist is not valid: $dest" >&2
    exit 1
  fi
  if grep -q "<REPO>\|<UV>\|<BIND>\|&lt;REPO&gt;\|&lt;UV&gt;\|&lt;BIND&gt;" "$dest"; then
    echo "substitution missed a placeholder in $dest" >&2
    exit 1
  fi

  # Everything launchd will exec must exist. When an exec fails, launchd logs to
  # the system log and writes NOTHING to the job's own log files, which reads as
  # a program that started and died silently. Catch it here instead.
  while IFS= read -r prog; do
    case "$prog" in
      /*) [[ -x "$prog" ]] || { echo "not executable: $prog" >&2; exit 1; } ;;
    esac
  done < <(plutil -extract ProgramArguments json -o - "$dest" \
           | tr ',' '\n' | tr -d '[]" ' | grep '^/' || true)

  echo "loading $label"
  echo "  command: $(plutil -extract ProgramArguments.$(( $(plutil -extract ProgramArguments json -o - "$dest" | tr ',' '\n' | wc -l) - 1 )) raw -o - "$dest" 2>/dev/null || echo '?')"
  if launchctl bootstrap "gui/$UID_NUM" "$dest" 2>/tmp/combine_load_err; then
    :
  elif launchctl load -w "$dest" 2>>/tmp/combine_load_err; then
    :
  else
    echo "launchctl refused to load $label:" >&2
    cat /tmp/combine_load_err >&2
    exit 1
  fi
done

sleep 2
echo
echo "status (second column is the last exit code; 0 or - is healthy):"
launchctl list | grep thecombine || echo "  NOT RUNNING"
echo
for label in "${WANT[@]}"; do
  short="${label##*.}"
  errlog="$REPO/logs/$short.err"
  echo "--- $short: last lines of $errlog ---"
  if [[ -s "$errlog" ]]; then
    tail -5 "$errlog"
  else
    echo "  (empty). If the job is also not listed above, launchd could not exec"
    echo "  it at all; ask the system log:"
    echo "    log show --predicate 'eventMessage CONTAINS \"$label\"' --last 10m"
  fi
done
