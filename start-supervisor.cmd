@echo off
rem Start the claude-hand supervisor with the Unity/Blender chat channel turned on.
rem Run it from anywhere; it switches to this folder, where chat-setup registered the channel.
cd /d "%~dp0"
rem Only the session started here serves the chat panels (see tools/chat_channel.py).
set CLAUDE_HAND_SUPERVISOR=1
rem Give the channel server time to start on a slow first run (milliseconds; default is 30 s).
if not defined MCP_TIMEOUT set MCP_TIMEOUT=60000
claude --dangerously-load-development-channels server:claude-hand-chat %*
