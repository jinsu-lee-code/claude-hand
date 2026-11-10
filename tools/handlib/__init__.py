"""What tools/claude_hand.py, guard.py, chat_channel.py and unity_bridge.py are made of.

Standard library only, like the scripts themselves, so everything runs with `uv run --no-project` or any Python 3.10+.
Each module uses only the ones above it in this list, and other modules' names through the module
(`discovery.discover_blender(...)`), so a test can replace a function in the one place it lives.

  constants   names and places every part agrees on
  link        the low-level ways to things already running (home folder, chat server file, MCP for Blender's socket)
  settings    the small JSON files claude-hand keeps or edits
  discovery   finding the open Unity editors and Blender windows
  capture     a picture of what a window shows now
  install     setting up Unity and Blender from the command line
  agents      the subagent definition files and the supervisor skill
  chatsetup   the chat channel's registration with Claude Code, and why a panel does not reach the supervisor
  launch      starting the supervisor session
  checks      doctor and selftest
"""
