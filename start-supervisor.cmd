@echo off
rem Claude Hand: start the supervisor session that the Unity / Blender chat panels talk to.
rem Double-click this file, the Desktop "Claude Hand" icon, or the start button in a chat panel.
chcp 65001 >nul
title Claude Hand
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
  echo [Claude Hand] uv가 설치돼 있지 않습니다. claude-hand 폴더에서 Claude Code를 열고 "claude-hand 세팅해줘"라고 해 주세요.
  pause
  exit /b 1
)
where claude >nul 2>nul
if errorlevel 1 (
  echo [Claude Hand] Claude Code 명령 claude 가 없습니다. 설치 명령: winget install Anthropic.ClaudeCode
  pause
  exit /b 1
)

rem Prepare: subagents for the open windows, chat settings, already-running check, first-run guide.
uv run -q --no-project tools\claude_hand.py preflight
if errorlevel 3 (
  pause
  exit /b 0
)

rem Only the session started here serves the chat panels (see tools/chat_channel.py).
set CLAUDE_HAND_SUPERVISOR=1
rem Give the channel server time to start on a slow first run (milliseconds; default is 30 s).
if not defined MCP_TIMEOUT set MCP_TIMEOUT=60000
claude --dangerously-load-development-channels server:claude-hand-chat %*
if errorlevel 1 pause
