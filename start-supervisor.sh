#!/usr/bin/env sh
# Start the claude-hand supervisor with the Unity/Blender chat channel turned on.
cd "$(dirname "$0")" || exit 1
# Only the session started here serves the chat panels (see tools/chat_channel.py).
export CLAUDE_HAND_SUPERVISOR=1
# Give the channel server time to start on a slow first run (milliseconds; default is 30 s).
export MCP_TIMEOUT="${MCP_TIMEOUT:-60000}"
exec claude --dangerously-load-development-channels server:claude-hand-chat "$@"
