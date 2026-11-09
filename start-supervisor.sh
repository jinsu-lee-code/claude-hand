#!/usr/bin/env sh
# Start the claude-hand supervisor with the Unity/Blender chat channel turned on.
cd "$(dirname "$0")" || exit 1
exec claude --dangerously-load-development-channels server:claude-hand-chat "$@"
