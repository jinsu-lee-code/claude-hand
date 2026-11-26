---
name: claude-hand
description: Supervisor rules for working with several Unity projects or Blender windows at once. Use when the user mentions two or more Unity projects or Blender files, asks to move objects, prefabs or models from one place to another (Unity↔Unity, Blender↔Blender, Blender↔Unity), when work has to be handed out to the unity-*/blender-* subagents, or when they say "Start Claude Hand" or "Turn Claude Hand on".
---

# Supervising several Unity and Blender windows (claude-hand)

Every Unity project has a dedicated subagent `unity-<project>`, every Blender window a `blender-<file>`. Each subagent is connected to its own target only, and the guard refuses any other target. You are the supervisor. Do not do Unity and Blender work yourself: hand it to the subagent in charge.

**Do not use tools that operate Unity or Blender directly.** That holds even when MCP for Unity (`unityMCP` and the like), Unity AI Assistant (`unity-mcp`) or MCP for Blender (`blender` and the like) are attached to this session. Those tools do not go through the pinning and the guard of the subagents. In the claude-hand folder they are blocked by the settings. Whether it is looking something up or changing something, always hand it to the subagent in charge.

## When you start
1. Check which `unity-*`, `blender-*` subagents there are.
2. If there is no one in charge of the target the user named, **make one yourself.** Check that that Unity project or Blender file is open (for Blender, the MCP for Blender server has to be on), and run this command.
   ```
   uv run "{{CLAUDE_HAND}}/tools/claude_hand.py" sync
   ```
   A few seconds later the new `unity-*`/`blender-*` subagent can be used. There is no need to restart. If it still does not show, ask the user to open Claude Code (or the desktop app session) again. If it cannot be made because the window is not open, tell the user what has to be opened.
3. With several Blender windows, each window has to have a different port. Have the user go to `N` → the MCP for Blender tab in the 3D viewport and set them differently, as in 9876, 9877….
4. Hand work to each subagent with the goal, the target (a name or a ref) and the values to hand back when it is done written out clearly. In Claude Hand (the supervisor session), also write in the first line of the text that hands the work over which request it is for: `[request <id>]` for a chat panel request, otherwise `[request none]` ("Messages from the Unity and Blender chat panels" below).

## "Start Claude Hand"

When the user asks to turn Claude Hand on (whether in any Code tab session of the desktop app or in a terminal), run this command and pass the output on as it is.
```
uv run "{{CLAUDE_HAND}}/tools/claude_hand.py" start
```
- If it is already on, or this session is Claude Hand, tell them so and open nothing.
- If it was newly opened, explain two things. ① If the new "Claude Hand" window shows confirmation questions, choose with the arrow keys and press Enter (for the development channel warning, the side that continues). ② Open "Claude Hand" in the session list of the desktop app's Code tab and carry on talking there. That window minimizes by itself once work starts.
- If this session is not Claude Hand, tell the user once that the work is better done in the "Claude Hand" session. The connection to the chat panels, leaving records in the chat panels and `current_context` exist only in that session. If the user wants to do it here, follow the rules below, but no record is left in the chat panels.

{{CHAT_RULES}}
## Project and file notes (rules for work that spans several days)
- Each window has a notes file. For Unity it is `<project path>/ClaudeHand/NOTES.md`, for Blender `<file name>.claude-hand.md` in the same folder as the `.blend`. The subagent in charge reads it when it starts (the path is in the subagent's definition too).
- When the user states a rule or a preference to keep **from now on too** for that project or file (for example "materials in this project are URP Lit only", "object names in English PascalCase"), add it to those notes as one line with the date. If the file does not exist, make it with the title `# <name> notes`. If it overlaps a line that is already there, change that line. Do not write down what is to be done only once.
- If a subagent's report has "To note:", ask the user and add it.
- When you have changed the notes, tell that window "Noted: …". The subagent in charge reads it from the next request on.

## Rules for handing out work at the same time
- Work for **different targets** (another project, another Blender window) may be handed out at the same time (in the background).
- Do not put two subagents on **the same target** at the same time. Commands are handled one at a time, and changes made at the same time overwrite each other.
- Work that needs the result of an earlier step (for example a manifest path) is handed out after that result has come.

## Unity → Unity
1. To `unity-a`: "Find <target> in the scene, export it with `ch_export_objects` and hand back `manifest_path`, `roots`, `scripts`, `package_dependencies`, `warnings`."
2. Check the result. If there are `scripts`, remember that B will need to compile; if there are `package_dependencies`, that B has to have the same packages too.
3. To `unity-b`: "Check with `ch_import_package(manifest=<manifest_path>, dry_run=true)` and report the result as it is."
4. If there is even one of `would_overwrite`, `same_guid_elsewhere`, `missing_packages`, **ask the user first.** Overwriting changes B's assets.
5. If there is no problem, or the user allows it, hand the real import to `unity-b`. Have it use `overwrite=true` only when overwriting was allowed. If it is to go under a particular parent, have it find the parent's ref with a snapshot first.
6. Report to the user what was placed where, and tell them the scene has not been saved yet. Hand out saving only when the user wants it.

## Blender → Blender
1. To `blender-a`: "Export <object> to a .blend the 'Sending to another Blender' way and hand back the manifest path."
2. To `blender-b`: "Import <manifest path> the 'Receiving from another Blender' way and hand back the names of the objects that were really added."
3. If `.001` was added because a name was already taken, tell the user. Have the .blend file saved only when the user wants it.

## Blender → Unity
1. To `blender-a`: "Export <object> to FBX the 'Sending to Unity' way and hand back the manifest path."
2. To `unity-b`: "Import it with `ch_import_model(manifest=<manifest path>)`, place it in the scene and hand back `placed` and `warnings`." If it is to go under a particular parent, have it find the parent's ref with a snapshot first.
3. When the same model goes into several Unity projects, do step 1 only once and hand out only step 2 for each project. These may run at the same time.
4. **When a changed model is sent again, do steps 1 and 2 just the same.** When the same object of the same saved .blend comes again, Unity does not add a new one: it replaces the model file the project had in place (the `updated` of the result is true). What is placed in scenes and prefabs takes the new shape where it stands. Tell the user "it was not added anew: the model that was there was replaced, and N in the scene took the new shape". This file replacement too is taken back by the [Undo] of the chat panel (the file from before the replacement is put back). For a model that came in new, [Undo] takes that asset out of the project (it is not deleted but kept in that project's `Library/ClaudeHand/removed`). Have it received with `new_copy=true` only when the user wants a separate second copy.
5. If the materials look different, tell the user it is a limit of FBX materials. Tell them that if the project has materials of the same name, the model can be received again with `ch_import_model(..., materials="project")` and linked to those materials, and ask whether to do so. If the user says "always like that in this project", write it in the project notes.

## Updating Unity on every save (models that follow saves)
A model that went from Blender to Unity can be turned on to "follow saves". While it is on, every time that .blend is saved Blender's Claude Hand panel sends the model again by itself and Unity replaces it in place. It goes through neither you nor a subagent, so it costs no tokens and takes about a second. It suits a model that will be looked at while it keeps being changed.
1. When the user says something like "update it every time I save", "I'll keep changing it, so make it follow", and that model is not in Unity yet, first send it with steps 1 and 2 of Blender → Unity above.
2. To `unity-b`: "Find the `asset_path` of <model> with `ch_model_follow(action="list")`, turn it on with `ch_model_follow(action="set", asset_path=..., follow=true)` and hand back the result." To turn it off, `follow=false`.
3. Tell the user "from now on, saving in Blender changes that model in Unity. It starts from the next save". The saving is done by the user. Do not have the Blender worker save (only when the user wanted it).
- The user can also turn it on and off directly: the tick in the "Models sent to Unity" section at the bottom of Blender's Claude Hand panel. [Send the selected objects] in the same section is the button that sends for the first time without Claude. Tell them when they ask.
- For a model with following turned on, "send it again" works as usual too (it stays on).
- What changed following a save is not the work of a request, so the [Undo] of the chat panel does not take it back. The model in Unity follows the last saved .blend. When [Undo] is pressed in Blender, the undone shape goes to Unity again.

## Unity → Blender
1. To `unity-a`: "Export <target> with `ch_export_model` and hand back `manifest_path`, `via`, `warnings`." For a scene object, have it find the ref with a snapshot first; for a model asset as it is, have it use `asset_path`.
2. If the `via` of the result is `source_file`, the original model file of that object went just as it was when imported. If `warnings` has "what was changed in the scene does not go", tell the user.
3. An error that "FBX Exporter is needed" means that object is a mesh made in Unity. Ask the user whether to install FBX Exporter (`com.unity.formats.fbx`) in the Package Manager. Do not install it on your own.
4. To `blender-b`: "Import <manifest path> the 'Receiving from Unity' way and hand back the names of the objects that were added."
5. Report what came in under which name. Have the .blend file saved only when the user wants it.

## When a subagent is stuck
- `STALE_REF`: have it take the snapshot again.
- `EDITOR_BUSY`: hand the work over again a little later.
- `CONFLICT`: ask the user, as in step 4 of Unity → Unity above.
- A Unity connection error or "No Unity instances": ask the user to check that that Unity window is open and that Window > MCP for Unity says Connected.
- A Blender connection error: ask the user to check that that Blender window is open, that the MCP for Blender server is on, and that the port is the same as in the definition file.
- If a subagent tried to touch another target and was refused by the guard, hand that work over again to the right one in charge.
- If a subagent's call was refused with "Another subagent is at work in this window", two subagents were put on the same target. Hand the work over again after the one that was handed work first has finished.
- If a subagent's call was refused with "The user pressed [Stop] in the chat panel", do not hand that work over again. Tell the asking chat panel with `reply` how far it got and what is left. Stop is lifted once that request is answered.

## When Unity or Blender is stopped at a dialog

Unity stops where it is when a dialog comes up such as "Save the scene?", "API update" or an import confirmation. On Windows claude-hand notices this by itself.
- The subagent's Unity call comes back with "is stopped at a dialog, so this call was refused" or a `DIALOG` error. The dialog's title, text and buttons are in it.
- In the asking chat panel the channel shows "… is stopped at a dialog. Please choose in Unity yourself" (nothing for you to do separately).

When a subagent reports this, **show the user the title, text and buttons as they are and ask which to press.** Never decide a choice about saving, deleting or overwriting on your own. When the user has pressed it in Unity themselves, see with `ch_editor_status` whether it is released and then hand the work on to the subagent. The call made just before it got stuck may have run when the dialog closed, so have the state checked first before having it done again.

When the user asks you to press it for them, or when commands keep timing out without the notice above (Blender, a window that cannot be read), do the following in order if agent-desktop is installed (check with `agent-desktop version`).
1. Look at the window titles of that app with `agent-desktop list-windows`. A dialog usually shows as a separate window. If the window is minimized, `restore` it first.
2. Read the text and the buttons of the dialog with `agent-desktop snapshot --app <exe> -i`. A dialog is a native OS window, so it can usually be read.
3. **Show the user the content and the buttons as they are and ask which to press.** Never decide a choice about saving, deleting or overwriting on your own.
4. Press only the button the user chose, with `click <ref>`, and check again with snapshot or `ch_editor_status` whether it is released.

If agent-desktop is not there or the window cannot be read, ask the user to check that window themselves. For how to set up for the first time, follow the `claude-hand-setup` skill of the claude-hand repository.
