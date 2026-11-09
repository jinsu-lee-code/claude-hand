#!/usr/bin/env sh
# Claude Hand: start the supervisor session that the Unity / Blender chat panels talk to.
cd "$(dirname "$0")" || exit 1
pause() { printf '%s' "Enter를 누르면 닫힙니다..."; read -r _; }

if ! command -v uv >/dev/null 2>&1; then
  echo '[Claude Hand] uv가 설치돼 있지 않습니다. claude-hand 폴더에서 Claude Code를 열고 "claude-hand 세팅해줘"라고 해 주세요.'
  pause; exit 1
fi
if ! command -v claude >/dev/null 2>&1; then
  echo '[Claude Hand] Claude Code(claude 명령)가 없습니다. 설치: curl -fsSL https://claude.ai/install.sh | bash'
  pause; exit 1
fi

# Prepare: subagents for the open windows, chat settings, already-running check, first-run guide.
uv run -q --no-project tools/claude_hand.py preflight
status=$?
if [ "$status" -eq 3 ]; then pause; exit 0; fi

# Started from another Claude Code session ("Claude Hand 켜줘", or a Unity/Blender it launched): drop that
# session's variables (listed by preflight) so this one is not taken for its child.
if [ -f .launch-unset ]; then
  while IFS= read -r name; do [ -n "$name" ] && unset "$name"; done < .launch-unset
fi

# Only the session started here serves the chat panels (see tools/chat_channel.py).
export CLAUDE_HAND_SUPERVISOR=1
# Give the channel server time to start on a slow first run (milliseconds; default is 30 s).
export MCP_TIMEOUT="${MCP_TIMEOUT:-60000}"
# Extra arguments from preflight, e.g. --remote-control to show this session in the Claude desktop app.
CH_ARGS=""
[ -f .launch-args ] && CH_ARGS=$(cat .launch-args)
eval "set -- $CH_ARGS \"\$@\""
claude --dangerously-load-development-channels server:claude-hand-chat "$@" || pause
