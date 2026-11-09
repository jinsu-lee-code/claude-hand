@echo off
rem Start the claude-hand supervisor with the Unity/Blender chat channel turned on.
rem Run it from anywhere; it switches to this folder, where the channel is registered.
cd /d "%~dp0"
claude --dangerously-load-development-channels server:claude-hand-chat %*
