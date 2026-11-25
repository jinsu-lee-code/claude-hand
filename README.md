# claude-hand

**English** · [한국어](README.ko.md)

A toolkit for working in several Unity editors and Blender windows at once, with **a dedicated Claude subagent attached to each window**.
It can also move objects from one window to another: Unity → Unity, Blender → Blender, Blender → Unity, Unity → Blender.
It does not replace the MCP servers you already use. It builds on top of them.
- Unity: [MCP for Unity (CoplayDev)](https://github.com/CoplayDev/unity-mcp)
- Blender: [MCP for Blender (ahujasid/blender-mcp)](https://github.com/ahujasid/blender-mcp)
- First-time setup and dialog handling: [agent-desktop (vercel-labs)](https://github.com/vercel-labs/agent-desktop)

```
Chat panels inside Unity and Blender ──(claude-hand-chat channel)──┐
                                                                   ▼
Supervisor session (Claude Code, the one you talk to)
 ├─ subagent unity-mygame      ── its own MCP server (pinned to MyGame)    ── Unity window: MyGame
 ├─ subagent unity-props       ── its own MCP server (pinned to Props)     ── Unity window: Props
 ├─ subagent blender-character ── its own MCP server (pinned to port 9876) ── Blender window: character.blend
 └─ exchange folder %USERPROFILE%\.claude-hand\exchange  (.unitypackage / .blend / .fbx + manifest, cleaned at startup after 30 days)
```

> **1.0.0** (2026-11-24) · Checked on Windows with real Unity 6.3 and Blender 5.2, with the windows open and a real supervisor session. On macOS and Linux only the automated tests have been run.
> What was checked and how is in the [test plan](docs/TEST-PLAN.md) and the [review notes](docs/REVIEW-2026-11-10.md) (both in Korean). What changed in each version is under [Changelog](#changelog).
>
> **The interface is in Korean.** The chat panels, the command output and the tool's own messages are Korean; this README gives each label with its meaning. You can write your requests in English. → [Interface language](#interface-language)

## What it does

- **A dedicated Claude per window.** Every open Unity project and Blender file gets one subagent. It cannot touch any window but its own.
- **Chat inside the window.** Ask, and get the answer, in a chat panel inside Unity and Blender. Your current selection travels with the message, so "duplicate this" works. → [Chatting in each window](#chatting-in-each-window)
- **Move things between windows.** Unity ↔ Unity, Blender ↔ Blender, Blender ↔ Unity. A model from Blender arrives with its materials, rotation and animation. → [Moving things between windows](#moving-things-between-windows)
- **Follow on save.** Every time you save in Blender, the model in Unity is replaced in place. Claude is not involved, so it costs no tokens. → [Letting the model in Unity follow your saves](#letting-the-model-in-unity-follow-your-saves)
- **Undo per request.** One button undoes what that request changed: files too, and other windows the request changed too. → [Undo and Stop](#undo-and-stop)
- **Stop, overview, history.** Stop work in progress, see what the other windows are doing, and see what happened in a request and how much time and how many tokens it took (`status`, `trace`, `usage`).
- **Protection rules.** Say "don't touch this" per window. Nobody relies on Claude remembering it: the call is blocked, or the change is undone, by the machinery. → [Protection rules](#protection-rules)
- **Desktop app and mobile.** Watch and talk to the same session from the Claude desktop app and the mobile app. → [Watching and talking from the Claude desktop app](#watching-and-talking-from-the-claude-desktop-app)

## Contents

- [Interface language](#interface-language)
- [Requirements](#requirements) · [Team and Enterprise accounts](#team-and-enterprise-accounts)
- [Installation](#installation)
- [Daily use](#daily-use)
- [Chatting in each window](#chatting-in-each-window)
- [Moving things between windows](#moving-things-between-windows)
- [Watching and talking from the Claude desktop app](#watching-and-talking-from-the-claude-desktop-app)
- [Updating](#updating)
- [What is in the repository](#what-is-in-the-repository)
- [Safeguards](#safeguards)
- [Known limits](#known-limits)
- [Changelog](#changelog)
- [Development](#development)
- [License](#license)

## Interface language

claude-hand was written in Korean and has so far been used only in Korean.

- **In Korean:** the labels and buttons of the chat panels, the lines the tool itself writes into the chat (progress, time and tokens, refusals), the output of the `claude_hand.py` commands, the instructions given to Claude (`prompts/` and the skills), and the documents under `docs/`.
- **Your requests can be in English.** Nothing in the code looks at the language you type in; the request goes to Claude as it is. English requests have not been tried with open windows yet, so this part is unverified.
- The phrases this README quotes in English ("Set up claude-hand", "Start Claude Hand") are translations of the Korean phrases the skills were written for. The Korean original is given next to each.

The labels you will meet most often:

| Label | Meaning |
|---|---|
| **보내기** | Send |
| **선택 항목 함께** (Unity: **선택 항목 함께 보내기**) | Send the current selection along with the message |
| **되돌리기** | Undo (what the last request changed) |
| **중지** | Stop |
| **허용** / **거부** | Allow / Deny (a permission request) |
| **Claude Hand 시작** | Start Claude Hand |
| **전체 복사** | Copy the whole conversation |
| **다른 창** | Other windows |
| `작업 중` · `답 기다림` · `대화상자로 멈춤` · `한가함` | Working · Waiting for the reply · Stopped at a dialog · Idle |
| **보호** | Protect (the protection-rules section of the panel) |
| **선택한 것 보호** (Blender: **선택한 오브젝트 보호**) | Protect the selection |
| **이 창 보기만** | Look only: Claude may read this window but not change it |
| **이 창 잠금** | Lock this window: no work is handed to it at all |
| **동작 금지** | Forbidden actions |
| **이번만** | This run only (until Claude Hand is closed) |
| **추가** / **취소** | Add / Cancel (a rule Claude proposed) |
| **풀기** | Release (remove a rule) |
| **Unity로 보낸 모델** | Models sent to Unity |
| **선택한 오브젝트 보내기** | Send the selected objects |
| **이 파일로 잇기** / **선택한 오브젝트로 잇기** | Link to this file / Link to the selected object |
| **한글** | Opens a Windows text box for typing Korean (not needed for English) |

## Requirements

- **Claude Code** and a **claude.ai login** (Pro, Max, Team or Enterprise).
  - The chat panels use the Channels feature of Claude Code. It works with a claude.ai login or a Console API key, and is not available on Bedrock, Vertex or Foundry.
  - The Allow/Deny buttons in the chat panel need Claude Code 2.1.234 or later.
  - Watching from the desktop app (Remote Control) needs a claude.ai login. An API key does not work for it.
- **Windows.** On macOS and Linux only the automated tests have been run.
- **Unity** with [MCP for Unity](https://github.com/CoplayDev/unity-mcp) (tested version v10.3.0), and **Blender** with [MCP for Blender](https://github.com/ahujasid/blender-mcp). Checked with Unity 6.3 and Blender 5.2. Using only one of the two is fine.
- uv, git, Node.js 18 or later, agent-desktop. The setup skill installs whatever is missing.

`uv run tools/claude_hand.py doctor` tells you which of these are in place.

### Team and Enterprise accounts

Unlike a personal account (Pro, Max), **the organization's settings have to be switched on first.** What follows comes from the Claude Code documentation ([Channels](https://code.claude.com/docs/en/channels), [Remote Control](https://code.claude.com/docs/en/remote-control), [Settings](https://code.claude.com/docs/en/settings)); this repository has only been run on a personal account. Before setting up, check these three with your organization's Owner.

- **Channels must be on for the chat panels to work.** On Team and Enterprise they are blocked by default. An Owner turns them on at claude.ai under Organization settings > Claude Code > Channels, or puts `channelsEnabled: true` in the managed settings. While they are off, the channel server connects and the reply tools show up, but **messages sent from a chat panel never reach the supervisor.** A warning at startup asks you to have an admin enable them. The development-channel flag claude-hand uses also works only with this switch on.
- **Remote Control is a separate switch.** It is off by default, and an Owner turns it on from the same page. With it off the chat panels still work; only watching from the desktop app and mobile does not. Organizations with Zero Data Retention or HIPAA cannot turn it on.
- **Managed settings can block what claude-hand uses.** Managed settings deployed by the organization take precedence over user and project settings.

  | What the organization has set | What gets blocked |
  |---|---|
  | MCP server restrictions (`managed-mcp.json`, `allowManagedMcpServersOnly`, `mcp` in `strictPluginOnlyCustomization`) | This folder's `.mcp.json` (the chat channel) and each subagent's own MCP server |
  | Hook restrictions (`allowManagedHooksOnly`, `hooks` in `strictPluginOnlyCustomization`) | The subagents' guard and the supervisor's hook. Without the guard, none of these work: blocking other windows, undo points, protection rules |
  | Subagent and skill restrictions (`agents`, `skills` in `strictPluginOnlyCustomization`) | The window subagents in `~/.claude/agents` and the supervisor skill |
  | Permission-rule restrictions (`allowManagedPermissionRulesOnly`) | The automatic allow for the reply tools that `chat-setup` adds |

  The documentation does not say what happens to hooks written in a subagent definition under a hook restriction. In an organization with such restrictions, try one window first: send a request from the chat panel, then run `uv run tools/claude_hand.py trace`. If it shows a `동작` (action) line and `(이 요청의 되돌리기 지점)` (this request's undo point), the hooks are running.

Also:

- **Login.** Log in with the claude.ai account your admin invited. If the organization pins the login (`forceLoginOrgUUID`), API keys are refused at startup.
- **Usage.** It comes out of each seat's 5-hour and weekly allowance, shared with Claude chat. The supervisor and every window's subagent draw on the same allowance, so more windows use it up faster (see [Time and tokens per request](#time-and-tokens-per-request)). Admins can see usage per user.

## Installation

### Let Claude do it

All you need is **Claude Code** and this repository.

1. Get the `main` branch of the repository. Without `git`, choose the `main` branch on the GitHub page and download it as a ZIP.
   ```powershell
   git clone -b main https://github.com/jinsu-lee-code/claude-hand.git
   cd claude-hand
   ```
2. Open Claude Code in that folder (`claude`). In the desktop app, open this folder.
3. Say **"Set up claude-hand"** (Korean: "claude-hand 세팅해줘"), or run the skill by name with `/claude-hand-setup`.

The `claude-hand-setup` skill then works through these steps.

1. **Diagnose:** it shows a table of what is installed and what is missing (`doctor`).
2. **Plan and permission:** it shows what it would install or change and lets you choose the steps. Only the steps you choose are carried out.
3. **Install and set up:**
   - install missing basics (uv, git, Node.js) with winget
   - install agent-desktop
   - install MCP for Blender (the add-on, enabling it, and the Claude configuration)
   - add the packages to your Unity project and set Unity not to throttle in the background
   - create the subagents and, if you want, connect the Unity and Blender chat panels
   - agent-desktop handles only the on-screen steps that no command can do.
4. **Check:** it diagnoses again at the end and tells you what is left.

Some things Claude does not do for you and **asks you to do yourself**:

- administrator (UAC) prompts, Windows Firewall and security prompts
- logging in, accepting licences, typing passwords and API keys
- closing Blender or Unity windows that have unsaved work
- restarting Claude Code (needed once after a new MCP server is registered)

### Install by hand

1. Basics: uv, git, Node.js 18 or later (`winget install --id astral-sh.uv -e` / `Git.Git` / `OpenJS.NodeJS.LTS`)
2. agent-desktop: `npm install -g agent-desktop`
3. MCP for Blender: check with `uvx mcp-for-blender setup --dry-run`, then `uvx mcp-for-blender setup --yes`
4. For each Unity project, run the following. MCP for Unity and Claude Hand Tools are added to `Packages/manifest.json`, and the original file is kept as `.bak`.
   ```powershell
   uv run tools/claude_hand.py unity-install "C:\path\to\project"
   ```
   MCP for Unity is added at the tested version (v10.3.0). A project that already has it at `#main` is left alone and reported; add `--pin-mcp-for-unity` to move it.
5. Use MCP for Unity in **Stdio** mode. The MCP for Unity setup window that appears after the package is first added registers HTTP, so in Unity open **Window > MCP for Unity**, change Transport to **Stdio** and press **Configure** for Claude Code. You can also register it with a command (only if it is not registered yet). Then restart Claude Code.
   ```powershell
   claude mcp add --scope user unityMCP -- uvx --from mcpforunityserver mcp-for-unity --transport stdio
   ```
6. In Unity, choose **Tools > Claude Hand > Apply Recommended Settings** (Interaction Mode = No Throttling).
7. To use several Blender windows, open each one on a different port.
   ```powershell
   uv run tools/claude_hand.py blender-open "C:\Art\props.blend" --port 9877
   ```
   Or press `N` in the 3D viewport, change the port in the MCP for Blender tab and start the server. The port is saved in the .blend.
8. With every window you want to work in open, create the subagents.
   ```powershell
   uv run tools/claude_hand.py sync
   ```
   Type `/agents` in Claude Code. When `unity-*` and `blender-*` are listed, you are done.
9. For the chat panels, register the channel and install the Blender chat add-on.
   ```powershell
   uv run tools/claude_hand.py chat-setup            # register the channel + allow the reply tools + start and check the channel server once
   uv run tools/claude_hand.py blender-install-chat  # install and enable the Blender chat add-on
   uv run tools/claude_hand.py sync --allow-tools    # (optional) let the subagents run their Unity/Blender tools without asking
   uv run tools/claude_hand.py shortcut              # Claude Hand icon on the desktop and Start menu (--startup: start it when the PC starts)
   ```

About `sync`:

- It finds the MCP configuration you already use (`.mcp.json`, `~/.claude.json`, the Claude Desktop configuration) and starts the servers the same way. For Unity both stdio and HTTP work.
- For Blender it checks ports 9876–9885, reads the .blend path of each window that answers (read-only) and names the subagent after it. For another range add `--blender-ports 9900-9910`.
- Run `sync` again after opening or closing windows. Add `--prune` to delete the definition files of windows you closed. While Claude Hand is running, a new window gets its subagent by itself the first time you send a message from it.
- Run `sync` again if you move the repository folder: the path of the guard is written into the definition files.

### Check the installation

```powershell
uv run tools/claude_hand.py doctor      # what is missing, and the command that fixes it
uv run tools/claude_hand.py selftest    # whether it really works (about a minute, changes nothing)
```

- `selftest` runs the tests (Python, and the add-on inside Blender) and checks that the open Unity and Blender windows and the chat channel answer with this version. Use `--no-tests` to skip the tests.
- To really run the Unity tools that move models, give it a project that is **closed** (3 to 4 minutes).
  ```powershell
  uv run tools/claude_hand.py selftest --unity-project "C:\path\to\project"
  ```
  It opens Unity without a window, receives a model Blender really exported, replaces a model that arrives again in place, and checks undo, follow-on-save and the protection rules (72 checks). It works in a new scene that is never saved and deletes the assets it made when it finishes. Copies of the files from before a replacement, and the test assets removed while checking undo, stay in `Library/ClaudeHand/replaced/` and `removed/` (the latest 10 of each).

## Daily use

1. Just type in the chat panel in Unity (**Window > Claude Hand > Chat**) or Blender (3D viewport `N` > **Claude Hand**).
   - If Claude Hand is not running, the chat panel starts it and sends your message once it is connected.
   - You can also start it first with the **Claude Hand** icon on the desktop or the **[Claude Hand 시작]** (Start Claude Hand) button in the chat panel.
2. A black Claude Hand window appears. **Every time it starts** it shows the development-channel warning: choose "I am using this for local development" and press Enter. This is the security confirmation Claude Code always asks for a channel you built yourself, so claude-hand does not answer it for you, and on a personal account it cannot be turned off.
3. Once the session starts working, that window minimizes itself (Windows). **Do not close it.** Closing it stops the chat.

That is all.

- Opening a new project or file needs nothing extra. The first message you send from it creates that window's subagent.
- Double-clicking the icon again only tells you it is already running (`이미 실행 중`); a second one does not open.
- Each start continues the same conversation. To start a fresh one: `uv run tools/claude_hand.py new-session`.

## Chatting in each window

You can chat right inside Unity and Blender. **A message typed in a window goes to that window's subagent, and the answer comes back to that window.**

- Unity: **Window > Claude Hand > Chat**
- Blender: 3D viewport `N` panel > **Claude Hand** tab

```
Unity chat panel ─┐                        ┌─ unity-mygame (keeps this window's conversation going)
Blender panel ────┼─ channel ─ supervisor ─┼─ blender-props
                  │  (token)               └─ if another window is needed, the supervisor coordinates
                  └── replies and permission requests (Allow/Deny) come back to the same window
```

### Basics

- **Your selection goes along.** The message carries the objects you have selected and the scene they are in, so you can say "duplicate this". A checkbox turns this off.
- **Work that needs another window is asked from your window too.** Say "bring Rock from Blender's props.blend" in a Unity window; the supervisor uses both subagents and reports the result in that Unity window.
- **A picture of the other window is attached.** If you said "send it to Unity" in a Blender window, the current view of that Unity window appears under the answer. The channel takes it directly, so it costs no tokens, and no picture is attached for a window that was only looked at.
- **Buttons appear when permission is needed.** When Claude asks for permission to run a tool, **허용/거부** (Allow/Deny) appear in the chat panel.
- **You are told when the answer arrives.** If an answer or a permission request arrives while you are in another program, the taskbar button flashes and the Unity chat tab shows the unread count in its title.
- **A missing answer is chased.** If the work finished but no answer came, the channel reminds the supervisor once after a minute and shows in the chat panel that it did.
- **A dialog that stops Unity is reported at once (Windows).** If Unity stops at a dialog such as "Save the scene?", its text and buttons are reported to the supervisor and the chat panel without waiting for a timeout. Which button to press is your decision.

### Undo and Stop

When the work is done, a **되돌리기** (Undo) button appears above the input box. It goes back to just before that request started.

- It uses Unity's Undo and Blender's undo history.
- In Unity it also puts back the **files** that changed while the request was working: edited scripts and assets return to what they were, created ones leave the project, deleted ones come back.
- If the request **changed other windows too** ("send it to Unity", asked from Blender), the one button in the chat panel that made the request undoes those windows as well. The button shows the text of the request it will undo and the names of the other windows.
- A request that only asked a question has nothing to undo, so it is skipped and the button points at the last request that changed something.
- You can also just say "undo what you just did".

While work is in progress there is a **중지** (Stop) button next to the "작업 중" (working) line. It blocks the subagents working on that request from their next action on (the one action already running finishes), and the supervisor tells you how far it got. What was done is not undone; press **되돌리기** afterwards for that.

### Seeing what the other windows are doing

The **다른 창** (other windows) line above the input box lists the other windows that have a chat panel open, one line each: `작업 중` (working; "○○의 요청" if it is working on a request from another window), `답 기다림` (waiting for the reply), `대화상자로 멈춤` (stopped at a dialog). Idle windows share one line. It shows what the channel already knows, so it costs no tokens.

There is a command too. It adds the elapsed time and the action in progress.

```powershell
uv run tools/claude_hand.py status
```
```
Level (Unity) · 작업 중 · 1분 15초 · props의 요청 · ch_import_model · “의자를 Level로 보내줘”
props (Blender) · 답 기다림 · 1분 20초 · “의자를 Level로 보내줘”
한가함: MyGame (Unity)
```

Read as: Level has been working for 1 min 15 s on a request from props and is running `ch_import_model`; props has been waiting 1 min 20 s for the reply to "send the chair to Level"; MyGame is idle (`한가함`).

### Seeing what happened in a request

```powershell
uv run tools/claude_hand.py trace                    # requests from the chat panels in the last two days
uv run tools/claude_hand.py trace unity-mygame       # that window's last request, line by line
uv run tools/claude_hand.py trace --request <number> # the request with a number from the list
```

Every request sent from a chat panel gets a number, and what follows is written line by line to `%USERPROFILE%\.claude-hand\logs\<date>.jsonl`. Use it afterwards to answer "why was the reply late" or "where did it get stuck".

- What is recorded: which window the supervisor handed work to (`맡김`), the subagents' tool calls, the calls the guard and the protection rules blocked and why, whether each `ch_*` call succeeded and how long it took, when Stop was pressed, when a subagent stopped, the reply, and on the last line the time and tokens.
- What another window did is recorded under the number of the chat-panel request that caused it.
- Only the first 200 characters of messages and replies are kept, and files older than two weeks are deleted at startup.
- Work asked from the desktop app is recorded without a request number and does not appear in the list.

### Time and tokens per request

A line is added under the result: `이 요청: 32초 · 동작 2번 · 토큰 출력 2.9천 · 새 입력 4.5천 · 다시 읽기 66만` (this request: 32 s · 2 actions · tokens: output 2.9K · new input 4.5K · re-read 660K). It is the sum for the supervisor and the windows that worked on the request. It appears a few seconds after the reply, when the supervisor finishes that turn. The numbers are read from the transcript Claude Code keeps, so showing them costs no extra tokens. The Korean units are `천` = thousand and `만` = ten thousand.

```powershell
uv run tools/claude_hand.py usage             # time, actions and tokens for each request in the last two days, with totals
uv run tools/claude_hand.py usage --days 7    # a week (with a total per day)
```
```
11-23 14:32:41  unity-hand-test          32초 · 동작 2번 · 출력 2.9천 · 새 입력 4.5천 · 다시 읽기 66만  "빨간 큐브 하나 만들어줘"
11-23 14:40:02  blender-test-blender     1분 9초 · 동작 8번 · 출력 6.4천 · 새 입력 9.7만 · 다시 읽기 157만  "의자를 고쳐서 Unity로 보내줘"

합계: 요청 2개 · 기다린 시간 1분 41초 · 출력 9.3천 · 새 입력 10만 · 다시 읽기 223만
  감독                     API 9번 · 출력 2.6천 · 새 입력 7.1만 · 다시 읽기 101만
  blender-test-blender     API 7번 · 출력 5.2천 · 새 입력 2.6만 · 다시 읽기 84만
  unity-hand-test          API 4번 · 출력 1.5천 · 새 입력 4.3천 · 다시 읽기 38만
```

- **Time** (`초`, `분`) runs from sending in the chat panel to the result arriving. **Actions** (`동작`) are the number of Unity/Blender tool calls the subagents made. `합계` is the total and `감독` is the supervisor.
- **Output** (`출력`) is tokens Claude wrote, **new input** (`새 입력`) is tokens read for the first time (including what was newly written to the cache), and **re-read** (`다시 읽기`) is tokens read back from the cache. Re-reads are most of the total and much cheaper, so they are shown separately rather than added in.
- A request sent after a long pause finds the cache cold and reads the conversation afresh, so `새 입력` jumps by tens of times. That is the place to look when cutting cost.
- Nothing is converted to dollars (it means something different on each plan).
- `usage` works with Claude Hand closed. To turn off the line in the chat panel, put `"show_cost": false` in `%USERPROFILE%\.claude-hand\config.json` and restart Claude Hand. The log and `usage` still keep the numbers.

### Letting the model in Unity follow your saves

When you refine a model in Blender while looking at it in Unity, you do not have to say "send it again" after every change.

1. Send it once the usual way ("send the chair to Unity"). Or, in the **Unity로 보낸 모델** (models sent to Unity) section at the bottom of the Claude Hand panel in Blender, select the objects and press **[선택한 오브젝트 보내기 → project name]** (send the selected objects). This button sends directly, without Claude, and turns following on as well.
2. Turn following on. Tick that model in the same section, or say "update the chair in Unity every time I save".
3. From then on, **every time you save** in Blender (Ctrl+S) the model in Unity is replaced in place about a second later. Whatever is placed in scenes and prefabs takes the new shape where it stands.

- **Claude is not involved.** Blender sends to Unity directly, so it costs no tokens and works with Claude Hand closed. Unity must be open; it may be in the background.
- **Nothing is sent when nothing changed.** If you edit only other objects and save, Unity stays as it is.
- **One line is left only when something changed** (`저장 → hand_test: Chair 반영`, "saved → hand_test: Chair updated"). On further saves the line does not multiply; it changes to `3번째` (3rd).
- **Whether a model follows is recorded on the Unity side** (in the model's `.meta`). It disappears with the model, and giving the `.blend` to someone else does not send anything to their Unity.
- **Renaming is fine.** The name the object was sent under stays on it as a custom property (`claude_hand_sent_as`). If you duplicate the object and rename both, there is no telling which one it is, and the panel asks (**[선택한 오브젝트로 잇기]**, link to the selected object).
- **Save As is fine** (Save Copy is not). If you moved or renamed the file in Explorer, the panel shows "다른 파일에서 온 것으로 적혀 있음" (recorded as coming from another file); press **[이 파일로 잇기]** (link to this file).
- **Saves made while Unity was closed** are caught up once, when Unity opens or when that `.blend` is opened again.
- **It is not something Undo covers**, because it is not a request. The model in Unity follows the last saved `.blend`. Press **되돌리기** in Blender and the undone shape goes to Unity again. A model first sent with the panel's send button can be removed with **되돌리기** in the Unity chat panel.
- To turn it off, untick it or say "stop updating the chair in Unity".

### Protection rules

You can decide, per window, what must not be changed. Notes (`NOTES.md`) are only something Claude reads and tries to respect; protection rules are **enforced by the machinery, which blocks the call or undoes the change.**

**Setting them.** Use the **보호** (Protect) section of the Unity chat panel and the Blender panel.

| Button | Meaning |
|---|---|
| **[선택한 것 보호]** (protect the selection) | The selected objects and everything under them. In Unity, also assets and folders selected in the Project window |
| **[이 창 보기만]** (look only) | Claude may only read this window |
| **[이 창 잠금]** (lock this window) | No work is handed to this window at all |
| **동작 금지** (forbidden actions) | Unity: saving the scene, deleting assets, play mode. Blender: saving the .blend |

- Add a rule with **이번만** (this run only) ticked and it lasts until Claude Hand is closed. Unticked, it is written to the project (`ClaudeHand/protect.json`) or next to the .blend (`<name>.claude-hand.protect.json`) and stays.
- You can also say it: "don't touch Hero", "leave Assets/Art alone", "only look at this window". Claude **proposes** the rule, and it becomes a rule only when you press **[추가]** (Add) in the chat panel.
- **Only you can release a rule.** Press **[풀기]** (Release) on its line in the Protect section. Claude has no tool that edits or removes rules.
- The state is shown in words, not colour (`[잠김]` locked / `[열림]` open in the section title; a padlock icon in Blender).

**When a rule is hit.**

1. A subagent reads the rules when it starts work. It leaves the protected things out, or does nothing and answers that you should press [풀기] in the Protect section and ask again.
2. A call that would touch them anyway is **blocked before it runs**: a tool that edits a protected asset, a call that names a protected object, a forbidden action, a changing call in a look-only window.
3. In Unity, saving, deleting and moving a protected asset is **refused by Unity itself**, whatever code attempts it.
4. If something protected changed all the same, it is noticed right after the call and **everything that request did in that window is undone.**

The chat panel shows one line: `보호 규칙이 막았어요: …` (a protection rule blocked this) or `보호된 Hero (자리)이(가) 바뀌어 이 요청이 이 창에 한 일을 되돌렸어요` (protected Hero (position) changed, so what this request did in this window was undone).

**Using** a protected asset is allowed (placing a protected prefab in the scene, putting a protected material on another object). What is blocked is changing the asset itself.

### When the chat does not connect

```powershell
uv run tools/claude_hand.py chat-status
```

It tells you the current state and how to fix it. The common causes:

- The supervisor session was opened some other way than `start-supervisor.cmd` (the desktop app, plain `claude`). The channel server then starts in standby, and the chat panel shows why.
- A registration from an earlier version is still there → see [Updating](#updating).
- The `.mcp.json` server is waiting for approval or was rejected → approve it in `/mcp`. If you rejected it earlier, run `claude mcp reset-project-choices`.

Good to know:

- **Channels in Claude Code are still a research preview.** Because this is a channel you built yourself, it has to be enabled with `--dangerously-load-development-channels`, and the first time you see the development-channel warning and the `.mcp.json` server approval prompt.
- **Only the session opened with `start-supervisor` receives chat.** A Claude Code opened plainly in this folder also starts the channel server, but it does not take chat. That keeps a session without the channel from intercepting chat. Open only one supervisor session.
- The channel server takes chat on one of two conditions: the mark passed by `start-supervisor` (`CLAUDE_HAND_SUPERVISOR=1`) is present, or the Claude Code that started it loaded this server as a channel.
- The chat panels and the channel talk only inside your PC (127.0.0.1), and only between programs that know the token. The token is in `%USERPROFILE%\.claude-hand\chat\server.json`. No firewall rule is needed; if a firewall prompt appears you do not have to allow it.
- `chat-setup` creates `.mcp.json` (the channel registration) and `.claude/settings.local.json` (automatic allow for the reply and current-context tools; a block on the tools that would let the supervisor operate Unity or Blender directly) in this folder. Existing content is kept and neither file goes into git. It also starts the channel server once to check it (the first time, uv may be slow because it downloads Python).

## Moving things between windows

Just say it, in a chat panel or to the supervisor session.

| Say this | What happens |
|---|---|
| "Look at the scene structure of MyGame and Props at the same time" | The two subagents each take a snapshot of their own project. |
| "Put the Rock_A prefab from Props under Environment in the MyGame scene" | unity-props exports it; unity-mygame checks with a dry run (if something would be overwritten the supervisor asks you first), imports it and places it. It reports back with the scene left unsaved. |
| "Put Hero from character.blend into both the MyGame and Props scenes" | blender-character exports an FBX once, and the two Unity subagents import it at the same time. |
| "Bring the Rock set from props.blend into character.blend" | blender-props exports a .blend and blender-character appends it. |
| (after editing Hero in Blender) "Send Hero again" | It is the same object from the same .blend, so the existing model is replaced instead of a new one being added. Every Hero placed in the scene takes the new shape where it stands. |
| "Update Hero in MyGame every time I save" | Following is turned on for that model. From then on a save in Blender replaces it directly, without Claude. |
| "Send Rock_A from the MyGame scene to character.blend" | unity-mygame exports a model file and blender-character imports it. |

A model going from Blender to Unity gets its materials (Principled BSDF) built as URP Lit, keeps the rotation of its parts exactly as in Blender, and brings its clips and shape keys. What goes across and what does not is under [Known limits](#known-limits).

## Watching and talking from the Claude desktop app

By default Claude Hand opens with **Remote Control** of Claude Code. There is one way of working: **the black Claude Hand window does the work, and the Unity and Blender chat panels and the desktop app are entrances to that same session.**

- **"Claude Hand"** appears in the session list of the **Code tab of the Claude desktop app** (and claude.ai/code, and the mobile app). You can watch and talk there.
- Subagents running in the background are visible too, and you can stop them from there.
- You can say things like "send the selection to Blender". Unity and Blender keep Claude Hand informed of what is selected, so the supervisor finds the target.
- Work asked from the desktop app is also shown in that window's chat panel, marked **"데스크톱 앱에서"** (from the desktop app).
- When Claude Hand is not running, say **"Start Claude Hand"** (Korean: "Claude Hand 켜줘") in any Code-tab session of the desktop app. When the black window appears, answer its confirmation and move to "Claude Hand" in the session list.

Good to know:

- A Code-tab session you open yourself in the desktop app is not Claude Hand. It is not connected to the chat panels and leaves no log. Work in the "Claude Hand" session.
- It does not work in the ordinary **Chat** tab, only in the **Code** tab.
- On Team and Enterprise accounts the organization has to turn Remote Control on. → [Team and Enterprise accounts](#team-and-enterprise-accounts)
- When the conditions are not met (API key, Bedrock and so on), Claude Hand starts without Remote Control and shows the reason in its window. The chat panels work as usual. `/status` in the Claude Hand window shows how you are logged in.

Settings:

```powershell
uv run tools/claude_hand.py remote-control off   # turn off watching from the desktop app (on to turn it back on)
uv run tools/claude_hand.py minimize off         # stop the Claude Hand window minimizing itself
uv run tools/claude_hand.py clean-exchange --keep-days 30   # how many days to keep transferred files in the exchange folder (default 30, 0 = never delete)
```

## Updating

```powershell
git pull
uv run tools/claude_hand.py update     # bring the subagents, supervisor skill, chat settings and Blender add-on to the new version (--dry-run to preview)
```

- Then restart Claude Hand. Blender has to be reopened to use the new add-on. A Unity project that has Claude Hand Tools by folder path compiles the new package when you reopen it or return to its window.
- With the setup skill: **after `git pull`, restart Claude Code, then** say "continue the setup" (Korean: "세팅 이어서 해줘"). A skill is read when the session starts, so a session opened before the pull follows the old steps.
- `update` also cleans up registrations left by earlier versions. A channel that an old `chat-setup` registered at local scope is used ahead of the new `.mcp.json` and blocks the chat.
- What changed in the version you received is under [Changelog](#changelog).

## What is in the repository

| Location | Contents |
|---|---|
| `.claude/skills/claude-hand-setup/` | The first-time setup skill. Available as soon as you open Claude Code in this folder. |
| `skills/orchestrator/` | The rules (a skill) the supervisor session follows day to day. `sync` installs it to `~/.claude/skills/claude-hand/`. |
| `prompts/` | The source text of the instructions given to Claude: for the subagents (`unity-subagent.md`, `blender-subagent.md`) and the supervisor's rules for the chat panels (`chat-rules.md`). After editing them, reinstall with `update` and restart Claude Hand. |
| `tools/claude_hand.py` | The commands. Creating subagents (`list`, `sync`), setup (`doctor`, `selftest`, `update`, `unity-install`, `unity-open`, `blender-open`, `chat-setup`, `blender-install-chat`, `shortcut`), settings (`mode`, `remote-control`, `minimize`, `new-session`), viewing (`status`, `trace`, `usage`, `chat-status`), cleaning (`clean-exchange`). |
| `tools/guard.py` | The subagents' guard (a hook). Before a call it blocks calls aimed at another target and calls that break a protection rule, shows the calls it lets through on the working line of the chat panel, places the undo point before the first change of a request, and copies the files a Unity call names beforehand. After a call it tells the subagent what the window blocked or undid. It is kept as a small separate file that uses only the standard library, so a mistake in another file cannot stop the guard. |
| `tools/supervisor_hook.py` | The supervisor session's hook. Each time the supervisor hands work to a subagent, it tells the channel which chat-panel request that work belongs to (`[요청 <number>]`). It refuses a hand-off without a number, and a hand-off to a window still working on another request or to a locked window. |
| `tools/chat_channel.py` | The chat channel server. An MCP server run by Claude Code; it talks to the Unity and Blender chat panels over local HTTP. |
| `tools/unity_bridge.py` | The MCP server the subagents use to call the `ch_*` tools. `--list` shows the Unity editors that have opened their bridge. |
| `tools/handlib/` | The modules behind the scripts above: finding open windows (`discovery`), subagent definitions (`agents`), diagnosis (`checks`), the request log (`eventlog`), the overview (`overview`), telling whether code only reads (`looks`), keeping files that are about to change (`keeps`), reading tokens (`usage`), protection rules (`protect`) and more. The list is in `tools/handlib/__init__.py`. |
| `unity/com.claudehand.tools/` | The Unity package. Adds 11 `ch_*` tools and the chat panel. The `ch_*` tools are called through a bridge this package opens itself, so it compiles without MCP for Unity and works whatever its version. |
| `blender/claude_hand_chat.py` | The Blender chat panel add-on. |
| `start-supervisor.cmd` / `.sh` | Opens the supervisor session. The desktop icon and the chat-panel button run this file. Each time, `preflight` brings the subagents and settings in line, and it tells you if one is already running. |
| `docs/` | In Korean: [design and its reasons](docs/DESIGN.md), [detailed changelog](docs/CHANGELOG.md), [test plan](docs/TEST-PLAN.md), test results ([step 12](docs/TEST-RESULTS-12-WINDOWS.md), [step 13 on](docs/TEST-RESULTS-13-WINDOWS.md)), [structure review and later notes](docs/REVIEW-2026-11-10.md) |

### Unity tools it adds

| Tool | What it does |
|---|---|
| `ch_editor_status` | Reports the connected project, whether it has focus and whether it is compiling. Changes nothing. |
| `ch_scene_snapshot` | Returns the scene structure as JSON and gives every object a ref of the form `@s1a2b3c4:e5`. Using an old ref after the scene changed gives `STALE_REF`. |
| `ch_export_objects` | Packs scene objects (refs) or assets, with the assets they depend on, into a `.unitypackage` in the exchange folder. The source scene is not changed. |
| `ch_import_package` | Imports such a package and places it in the scene. `dry_run=true` shows first which assets would be overwritten, and it does not go ahead with an overwrite without permission. |
| `ch_import_model` | Imports an FBX (or OBJ, GLB) exported by Blender under `Assets/ClaudeHand/Imports/<name>/` and places it in the scene. **When the same object from the same .blend arrives again, the existing model file is replaced in place.** The file from before is kept in `Library/ClaudeHand/replaced/`. `dry_run=true` shows which of the two will happen, `new_copy=true` adds a separate copy, and `materials="project"` connects to the project's materials of the same name. |
| `ch_model_follow` | Makes a model that came from Blender follow the saves of its `.blend`. `list` shows the received models and whether they follow; `set` turns it on and off. While on, the Blender panel re-sends by itself on save. |
| `ch_export_model` | Puts scene objects (refs) or a model asset in the exchange folder as a model file for Blender (or another Unity project). An object that came from a model file is sent as that file; a mesh made in Unity is written as FBX when the FBX Exporter package (`com.unity.formats.fbx`) is present. |
| `ch_checkpoint` | Keeps an undo point per request. It is placed by itself just before the first change of a request, and **되돌리기** in the chat panel or `action=undo` undoes the changes after it. What happened to files while the request was working is put back too. |
| `ch_protect` | Shows this project's protection rules (`state`). It cannot change them. |
| `ch_apply_settings` | Sets Interaction Mode to **No Throttling**, which lets Unity process commands while it is in the background. Also in the menu **Tools > Claude Hand**. |
| `ch_capture` | Saves the Scene view as it is to a PNG (without gizmos). Used for the result preview in the chat panel. |

- They can be called from the command line too: `uv run tools/claude_hand.py unity-call MyGame ch_editor_status`
- Every error carries a `retry` value. `safe`: call again as is. `after_fix`: fix the arguments first. `check_first`: it may already have been applied, so check the state first.

### On the Blender side

Work in Blender goes through the Python execution tool of MCP for Blender. Moving things, undo and the protection rules are functions of the Claude Hand Chat add-on, which a subagent calls in one line.

| Function | What it does |
|---|---|
| `export_blend`, `import_blend` | Writes a `.blend` for another Blender, and appends one that was received. |
| `export_fbx` | Writes an FBX for Unity. Sending the same object from a saved .blend again makes Unity replace the existing model in place (even after a rename). It leaves the selection and edit mode alone. |
| `import_model` | Imports a model (FBX, OBJ, GLB) that Unity sent with `ch_export_model`. |
| `undo_last_request`, `undo_request` | Undo per request. The second undoes by request number (the channel calls it for each window when several are undone at once). |
| `protection_rules` | Shows this window's protection rules. |

The add-on also does two things no subagent calls. It re-sends models set to follow straight to Unity when the file is saved, and it checks before and after each call that the protected objects are unchanged.

The undo point is placed before the first action of a request that could change something. The Python execution tool runs any code, so the guard reads the code and lets it through without an undo point only when it clearly just reads (reading properties, `print`, filling a list it made itself, exporting).

### What agent-desktop is used for

- **First-time setup:** it performs the clicks no command can do, for example the dialog Unity shows while fetching a package, or changing the port of a Blender that is already open.
- **Day to day:** when Unity or Blender stops at a dialog, it reads the dialog, shows it to you and presses the button you choose.
- **Limits:** it cannot operate a minimized window (restore it first). The internal panels of Unity and Blender expose almost no accessibility information and may not be readable; in that case, after getting your permission, it captures the screen and clicks by coordinates (your mouse and keyboard really move while it does). That is why the real work (editing scenes, moving models) is done by MCP, not agent-desktop.

## Safeguards

- **A subagent has only its own window.** The `tools` allow-list in its definition file contains only its own MCP server. The Unity server is pinned with `--default-instance <Name@hash>`, the Blender server with `--port <port>`.
- **Every call is checked.** The guard hook (`tools/guard.py`) inspects each Unity call and blocks a `unity_instance` that points at another project and any change of target (`set_active_instance`). It looks inside `batch_execute` too.
- **Protection rules.** The same hook compares each call with that window's [protection rules](#protection-rules). It runs once more after the call and tells the subagent what the window blocked or undid.
- **When in doubt, block.** Whatever goes wrong inside the hook, the call is blocked (bad arguments, unreadable input, an error while deciding, protection rules that cannot be checked).
- **When the hook cannot even start.** In that case Claude Code does not block the call. So `sync` writes the full path of uv into the hook command, and `selftest` really runs the installed hook the same way Claude Code does.
- **One subagent per window.** Two working in the same window would overwrite each other, so while Claude Hand is running the calls of the one that came later are blocked.
- **Setup does only the steps you allowed.** When the mouse and keyboard really have to be moved for on-screen steps, it asks again separately.

## Known limits

**Environment**

- Taskbar notifications, the Korean text box, dialog detection and minimizing the Claude Hand window work only on Windows. Nothing has been checked with real windows on macOS or Linux.
- Running `unity-install` from a development checkout adds Claude Hand Tools by this PC's folder path (`file:`), so that project will not open on another PC. From a checkout of a release tag (for example `v1.0.0`) it is added by its GitHub address. `unity-install --dry-run` tells you which it will be.
- When a Blender subagent first checks its connection, and when `sync` and `doctor` read the file name, read-only Python (`print(bpy.data.filepath)`) runs inside Blender.

**Unity ↔ Unity**

- A script that belongs to an asmdef is exported without the asmdef. On the receiving side it compiles into the default assembly.
- Assets inside `Packages/` (URP shaders and the like) are not put in the `.unitypackage`. The receiving project needs the same package; the dry run lists them under `missing_packages`.

**Blender → Unity**

- **What goes across:** meshes, two UV sets, vertex colours, rigs and weights, clips, shape keys, lights, cameras, Geometry Nodes instances (as meshes), Principled BSDF materials (inputs that read a value or an image directly go as they are; inputs that pass through other nodes are baked to textures).
- **A bake is a fixed image.** Node effects that depend on the viewing angle or on time are not captured. When several objects whose UVs overlap share one material, the baked image is drawn over itself.
- **What does not go across yet:** shaders other than Principled BSDF, transparency that passes through nodes (you are told what was left out on import), simulations that move vertices such as cloth and soft body, particles, hair, Grease Pencil, the world.
- Materials are built only in URP projects and projects without a render pipeline (in HDRP you get the default materials Unity makes). Another way is to create materials of the same name in the project and receive with `materials="project"`.
- **When a model is replaced in place:** when the same object is sent from a saved .blend. It also works after renaming the object or after Save As in Blender. If you moved the .blend in Explorer you have to press **[이 파일로 잇기]** (link to this file) in the Blender panel; otherwise it arrives as a new model.

**Unity → Blender**

- An object that came from a model file is sent as that file. What you changed in the scene (swapped materials, added objects, position) does not go.
- A mesh made in Unity needs the FBX Exporter package, and this path was written from that package's source and has not actually been run.

**Undo**

- Files are put back only if they changed **while the request was working**. Files you created or edited yourself after the subagent finished are not touched. The scene is different: what you edited by hand afterwards is undone along with it.
- Files that are not put back: the file of an open scene (it changes only when saved again), files outside `Assets` (`Packages`, `ProjectSettings`), files a subagent overwrote directly by running code, files over 64 MB. For the last two the earlier content was not kept, and the undo tells you their names. When a re-sent model is undone, the files in `Materials/` and `Textures/` are not put back either.
- The model files from before a replacement are kept in `Library/ClaudeHand/replaced/` for the latest 10 requests; anything older does not come back with undo.
- An asset deleted in Unity while a request is working goes to that project's `Library/ClaudeHand/removed/req_<request>/` instead of the Recycle Bin (undo brings it back). The same holds for what you delete yourself in the Project window during that time, because it would otherwise be gone for good when the Recycle Bin is set to delete immediately. Checked only on Unity 6.3.
- An asset removed by an undo does not come back with Unity's Redo (Ctrl+Y). If you need it, move it from `Library/ClaudeHand/removed/undo_<time>/` back to its place together with its `.meta` (the latest 10 undos are kept).
- Undoing several windows at once works only for **requests made from a chat panel** (work asked from the desktop app or a terminal is undone window by window). A window is undone only if that request is its newest change; if a later request changed it, or it is working, it is skipped and you are told. Press the same button again once that window is free and only the remaining windows are undone. If one window fails, the others are still undone.
- Skipping a request that only asked a question works differently in the two apps. Unity looks at whether anything changed after the undo point was placed (a change that bypassed Undo counts as "nothing changed", and such a change cannot be undone either). Blender reads the code before running it. When it fails to recognise code that only reads, an undo point with nothing to undo is left, and the first press of Undo does nothing. Press once more and the request before it is undone.

**Stop**

- It blocks the subagents from their next Unity/Blender call on. One action already started (a long import, say) runs to the end.
- The block is lifted when the reply to that request arrives, and after 3 minutes even without one. Work you ask of another window from the desktop app in the meantime may be blocked as well.

**Time and tokens**

- They are read from the transcript files Claude Code keeps. That is an undocumented internal format (checked on 2.1.322), so when Claude Code changes, the line may lose its tokens and show only time and actions. The `selftest` item "감독 세션 기록에서 토큰 읽기" (reading tokens from the supervisor's transcript) tells you when that happens.
- Tokens spent on the request after the line appeared are not included. When the supervisor works on two requests in one turn, its tokens go to the request that started that turn.
- Work asked from the desktop app or a terminal is not shown per request. The line in the chat panel disappears when Claude Hand restarts; the log (`trace`, `usage`) keeps it for two weeks.

**Models that follow saves**

- They are sent only while Unity is open. They are not recorded in the request log (`trace`).
- A mesh with a Bevel modifier comes out with slightly different UVs each time Blender evaluates it, so the file alone cannot tell whether it changed. Such a model is sent only when Blender reports it as touched, and once on the first save after the `.blend` is opened.
- Following models are exported on every save (about 0.1 s for 80,000 faces), so a very heavy model makes saving that much longer. A model whose export takes over a second is exported only when it was touched.

**Protection rules**

- They are not a lock against deliberately hidden code. They stop a subagent from touching something by mistake or because it missed a note. Other tools that do not go through claude-hand are not covered.
- While a request is working, your own saves, deletions and play mode are refused too if a rule covers them, and if you edit a protected object yourself while a call is running, it is taken for Claude's change and undone.
- In Unity a change that bypassed Undo is noticed but cannot be undone, and you are told so.
- Blender's "saving the .blend" ban blocks only when a save call is visible in the code.
- If the name of a protected object appears in quotes in the code, the call is blocked once even when it only meant to read, with advice to split the reading call from the changing call.

## Changelog

The number follows `VERSION` at the repository root. Tags exist for `v0.9.0` and `v1.0.0`. The detailed record of what changed in each version and why is in [docs/CHANGELOG.md](docs/CHANGELOG.md) (in Korean).

### 1.0.0 (2026-11-24, tag `v1.0.0`): first full release

Everything planned for 1.0 is in: undoing files whichever tool changed them (0.13.0), undoing several windows at once (0.14.0), time and tokens per request (0.15.0), re-sending automatically on save (0.16.0), models from Blender arriving clean (0.17.0), protection rules per window (0.18.0). The features are those of 0.18.0; what changed in this version is the number and the documents.

- The README was rewritten: what it does and a table of contents at the top, requirements (including what to watch for on Team and Enterprise accounts) and installation moved forward, one section per feature, and the detailed per-version record moved to its own file. An English README was added; the Korean one is [README.ko.md](README.ko.md).
- Checked: 0.13.0 through 0.18.0 with open windows and a real supervisor session ([test plan](docs/TEST-PLAN.md), steps 27 to 32). Upgrading from an older version with `update` on another PC worked too.
- Not yet checked: step 28 H, step 29 E and G. These are things to watch while using it.

| Version | Date | What changed |
|---|---|---|
| 0.18.0 | 2026-11-24 | Protection rules per window |
| 0.17.1 | 2026-11-24 | Three more things found with the windows open |
| 0.17.0 | 2026-11-24 | Models from Blender arrive clean |
| 0.16.0 | 2026-11-24 | Re-send automatically on save |
| 0.15.0 | 2026-11-23 | Time and tokens per request |
| 0.14.0 | 2026-11-23 | Undo several windows at once |
| 0.13.0 | 2026-11-16 | Undo files whichever tool changed them |
| 0.12.1 | 2026-11-12 | Fixes for what a real supervisor session turned up |
| 0.12.0 | 2026-11-11 | All windows at a glance |
| 0.11.0 | 2026-11-11 | Undo that includes files |
| 0.10.0 | 2026-11-11 | Request numbers carried through |
| 0.9.1 | 2026-11-11 | Preview picture attached to the wrong chat panel |
| 0.9.0 | 2026-11-10 | Re-sending models, request log, Stop (tag `v0.9.0`) |
| 0.8.0 | 2026-11-10 | Restructuring |

## Development

```bash
python -m unittest discover -s tools/tests -v     # Python 3.10 or later, nothing else to install
```

- On GitHub the same tests run on Windows and Linux with Python 3.10 and 3.13 (`.github/workflows/tests.yml`).
- The version is set in one place, `VERSION` at the repository root, and a test checks that the numbers in the Unity package and the Blender add-on match it.
- `tools/tests/test_blender_chat_addon.py` runs the Blender add-on inside a real Blender. The command above skips it (Python has no `bpy`); `uv run tools/claude_hand.py selftest` starts the installed Blender without a window and runs it.
- On the Unity side, `ChSelfTest.cs` in the package runs the tools in a real Unity. `selftest --unity-project <closed project>` opens Unity without a window to run it.
- The tests run with `CLAUDE_HAND_LOG=off` so that they do not touch the real event log.
- Code comments and commit messages are in English; the user-facing strings and the documents under `docs/` are in Korean.

## License

[MIT](LICENSE). MCP for Unity, MCP for Blender and agent-desktop, which this works with, are not included in this repository and have their own licences.
