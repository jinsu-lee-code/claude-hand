"""What claude-hand says, in English: every Korean text that goes through handlib.lang.T, and its English.

The Korean text is the key, exactly as it stands in the code (see handlib/lang.py). A text has the same {names} in
both languages; tools/tests/test_lang.py holds every text of the code to an entry here, and an entry to its names.
The guard hook (tools/guard.py), the Unity package (ChText.cs) and the Blender add-on carry their own tables; a text
that two of them say has the same English in both.

The names of the panels' buttons are the ones README.md gives ("Interface language"). A new text goes under the file
that says it.
"""

EN: dict[str, str] = {
    # tools/handlib/protect.py
    "열림": "Open",
    "보기만": "Look only",
    "잠금": "Locked",
    "씬 저장": "Save scene",
    "에셋 삭제": "Delete asset",
    "플레이 모드": "Play mode",
    ".blend 저장": "Save .blend",
    "보호 규칙이 이 호출을 막았습니다: {rule} ({what}). 더 부르지 말고 이 내용을 그대로 감독에게 보고하세요. 규칙을 푸는 것은 사용자만 할 수 있습니다.":
        "A protection rule refused this call: {rule} ({what}). Make no more calls and report this as it is to the supervisor. Only the "
        "user can release a rule.",
    "보호 규칙이 이 호출을 막았습니다: {rule} ({what}). 이 호출은 무언가를 바꾸면서 보호된 것을 이름으로 가리킵니다. 보호된 것을 바꾸려던 것이면 더 부르지 말고 이 내용을 그대로 감독에게 보고하세요. 읽거나 가져다 "
    "쓰기만 하려던 것이면 읽는 호출과 바꾸는 호출을 나눠 다시 부르세요(바꾸는 호출에는 그 이름을 적지 않습니다). 규칙을 푸는 것은 사용자만 할 수 있습니다.":
        "A protection rule refused this call: {rule} ({what}). This call changes something and names what is protected. If it was going to "
        "change what is protected, make no more calls and report this as it is to the supervisor. If it was only going to read it or put "
        "it to use, split it into a call that reads and a call that changes, and call again (the call that changes does not name it). Only "
        "the user can release a rule.",
    "경로 {count}": "paths: {count}",
    "오브젝트 {count}": "objects: {count}",
    "금지 동작: {actions}": "forbidden: {actions}",
    "이 창은 잠겨 있습니다(잠금)": "this window is locked (Locked)",
    "이 창에서는 아무 일도 하지 않습니다": "nothing is done in this window",
    "보호 규칙 파일": "the protection rules file",
    "규칙은 사용자만 고칩니다": "only the user changes the rules",
    "이 창은 보기만 합니다(보기만)": "this window may only be looked at (Look only)",
    "{tool}: 바꾸는 호출": "{tool}: a call that changes",
    "금지 동작 '{action}'": "forbidden action '{action}'",
    "보호 경로 '{rule}'": "protected path '{rule}'",
    "보호 오브젝트 '{rule}'": "protected object '{rule}'",
    "보호 규칙이 이 호출을 막았습니다: ": "A protection rule refused this call: ",
    "보호 규칙이 막았어요: ": "A protection rule stopped it: ",
    "이 창을 보기만 하기(읽기만 하고 바꾸지 않음)": "Look only in this window (read, change nothing)",
    "이 창을 잠그기(이 창에는 일을 맡기지 않음)": "Lock this window (no work is handed to it)",
    "'{action}' 금지": "Forbid '{action}'",
    "경로 '{value}' 보호(고치기 · 지우기 · 옮기기 금지)": "Protect the path '{value}' (no changing, deleting or moving)",
    "컬렉션 '{name}'의 오브젝트 보호": "Protect the objects of the collection '{name}'",
    "오브젝트 '{value}' 보호(바꾸기 · 지우기 금지)": "Protect the object '{value}' (no changing or deleting)",
    " — 계속": " — lasting",
    " — 이번만(Claude Hand를 끌 때까지)": " — this run only (until Claude Hand is closed)",
    "보호 규칙이 막았어요: {what}": "A protection rule stopped it: {what}",
    "보호된 {what}이(가) 바뀌어 이 요청이 이 창에 한 일을 되돌렸어요": "Protected {what} was changed, so what this request did in this window was undone",
    "보호된 {what}이(가) 바뀌었는데 되돌리지 못했어요({why})": "Protected {what} was changed and it could not be undone ({why})",
    "되돌릴 지점이 없음": "no undo point",
    "{line}. 바꾸는 호출은 더 하지 말고 이 내용을 그대로 감독에게 보고하세요(무엇이 막혔거나 되돌려졌는지, 그래서 이 요청에서 남은 것이 무엇인지). 지금 상태를 읽어 확인하는 것은 괜찮습니다. 같은 일을 다른 방법으로 다시 "
    "하지 마세요. 규칙을 푸는 것은 사용자만 할 수 있습니다.":
        "{line}. Make no more calls that change anything and report this as it is to the supervisor (what was stopped or undone, and so "
        "what of this request is left). Reading the present state to check is fine. Do not do the same thing again another way. Only the "
        "user can release a rule.",
    # tools/handlib/overview.py
    "작업 중": "Working",
    "중지하는 중": "Stopping",
    "답 기다림": "Waiting for the reply",
    "대화상자로 멈춤": "Stopped at a dialog",
    "한가함": "Idle",
    "{seconds}초": "{seconds}s",
    "{minutes}분 {seconds}초": "{minutes}m {seconds}s",
    "{asker}의 요청": "request of {asker}",
    # tools/handlib/eventlog.py
    "요청": "request",
    "맡김": "handed",
    "동작": "action",
    "막음": "refused",
    "ch 호출": "ch call",
    "멈춤": "stopped",
    "답": "reply",
    "다시 알림": "reminder",
    "대화상자": "dialog",
    "중지 누름": "stop pressed",
    "허락 요청": "permission",
    "허락 답": "verdict",
    "되돌림": "undo",
    "시간·토큰": "time/tokens",
    "쥐고 있음": "held",
    "넘김": "released",
    "막음 (보호 규칙)": "refused (protection rule)",
    "보호 규칙": "protection rule",
    "(하는 중이라는 답)": "(a reply that the work goes on)",
    "({seconds:.1f}초 만에)": "(after {seconds:.1f}s)",
    "(데스크톱 앱에서 시킨 일)": "(asked from the desktop app)",
    "(이 요청의 되돌리기 지점)": "(this request's undo point)",
    "성공": "ok",
    "실패 {code}": "failed {code}",
    "{seconds:.1f}초": "{seconds:.1f}s",
    " · 동작 {actions}번": " · {actions} actions",
    "(토큰 기록 없음)": "(no token record)",
    # tools/handlib/usage.py
    "감독": "supervisor",
    "{minutes}분": "{minutes}m",
    "{hours}시간 {minutes}분": "{hours}h {minutes}m",
    "출력 {output} · 새 입력 {input} · 다시 읽기 {cached}": "output {output} · new input {input} · read again {cached}",
    "동작 {actions}번": "{actions} actions",
    "토큰 ": "tokens ",
    "{who} {duration} API {calls}번 {tokens}": "{who} {duration} API {calls} calls {tokens}",
    # tools/handlib/launch.py
    "Bedrock/Vertex/Foundry({names})에서는 Remote Control을 쓸 수 없습니다": "Remote Control cannot be used with Bedrock/Vertex/Foundry ({names})",
    "ANTHROPIC_BASE_URL이 다른 서버를 가리키고 있으면 Remote Control을 쓸 수 없습니다":
        "Remote Control cannot be used while ANTHROPIC_BASE_URL points at another server",
    "ANTHROPIC_API_KEY가 설정돼 있습니다. Remote Control은 API 키로는 안 되고 claude.ai 로그인(Pro·Max·Team 등)이 필요합니다":
        "ANTHROPIC_API_KEY is set. Remote Control does not work with an API key; it needs a claude.ai login (Pro, Max, Team and so on)",
    "────────────────────────────────────────────────────────\n Claude Hand 감독 세션\n   Unity   : Window > Claude Hand > Chat\n   "
    "Blender : 3D 뷰포트에서 N > Claude Hand 탭\n 이 창은 닫지 마세요. 닫으면 채팅이 멈춥니다. 일을 시작하면 저절로 최소화됩니다.\n 켤 때마다 개발용 채널(claude-hand-chat) 경고가 뜹니다: I "
    "am using this for local development 를 고르세요.\n────────────────────────────────────────────────────────":
        "────────────────────────────────────────────────────────\n Claude Hand supervisor session\n   Unity   : Window > Claude Hand > "
        "Chat\n   Blender : in the 3D viewport, N > Claude Hand tab\n Do not close this window: closing it stops the chat. It minimizes by "
        "itself once work starts.\n Every start shows the development channel (claude-hand-chat) warning: choose I am using this for local "
        "development.\n────────────────────────────────────────────────────────",
    "처음에는 Claude Code가 몇 가지를 확인합니다. 화살표 키로 고르고 Enter를 누르세요.\n  - 이 폴더(claude-hand)를 믿는지: 믿는다(Yes)를 고르세요.\n  - 개발용 채널(claude-hand-chat) "
    "경고(켤 때마다 뜸): I am using this for local development 를 고르세요.\n  - (뜨면) MCP 서버 claude-hand-chat 사용: 사용하는 쪽을 고르세요.\n  - 로그인을 묻으면 화면 "
    "안내대로 로그인하세요.\n  - 데스크톱 앱에서 보려면 구독(Pro·Max 등) 계정 로그인이 필요합니다. /status 로 확인하고, 아니면 /login 하세요.":
        "The first time, Claude Code asks a few things. Choose with the arrow keys and press Enter.\n  - Whether you trust this folder "
        "(claude-hand): choose Yes.\n  - The development channel (claude-hand-chat) warning (shown at every start): choose I am using this "
        "for local development.\n  - (If shown) Use of the MCP server claude-hand-chat: choose to use it.\n  - If it asks you to log in, "
        "follow the instructions on screen.\n  - Watching from the desktop app needs a subscription login (Pro, Max and so on). Check with "
        "/status; if it is not one, /login.",
    # tools/handlib/agents.py
    "Unity 프로젝트 {name} 전담 작업자 ({id}). 이 프로젝트의 씬·오브젝트·에셋을 보거나 고칠 때, 또는 다른 Unity 프로젝트와 오브젝트를 주고받을 때 맡긴다. 이 프로젝트 말고는 건드리지 못한다.":
        "Dedicated worker for the Unity project {name} ({id}). Hand it the work of looking at or changing this project's scenes, objects "
        "and assets, or of exchanging objects with another Unity project. It cannot touch anything but this project.",
    " (경로: `{path}`)": " (path: `{path}`)",
    "프로젝트": "project",
    "(저장 안 된 파일)": "(unsaved file)",
    "Blender 창 전담 작업자: {name} ({shown}, MCP for Blender 포트 {port}). 이 Blender의 오브젝트·머티리얼·씬을 보거나 고칠 때, 또는 다른 Blender나 Unity로 모델을 보내고 받을 "
    "때 맡긴다. 다른 Blender 창은 건드리지 못한다.":
        "Dedicated worker for a Blender window: {name} ({shown}, MCP for Blender port {port}). Hand it the work of looking at or changing "
        "this Blender's objects, materials and scene, or of sending models to and receiving them from another Blender or Unity. It cannot "
        "touch any other Blender window.",
    "저장 안 된 파일": "unsaved file",
    "파일": "file",
    # tools/handlib/chatsetup.py
    "!! 이 폴더의 .mcp.json에 claude-hand-chat이 없습니다 → uv run tools/claude_hand.py chat-setup":
        "!! This folder's .mcp.json has no claude-hand-chat → uv run tools/claude_hand.py chat-setup",
    "!! .mcp.json이 이전 버전 형식입니다 → uv run tools/claude_hand.py chat-setup 으로 고치세요":
        "!! .mcp.json is in an earlier version's format → fix it with uv run tools/claude_hand.py chat-setup",
    "!! 이전 버전이 local 범위로 등록한 claude-hand-chat이 남아 있어 .mcp.json보다 먼저 쓰입니다 → uv run tools/claude_hand.py update (또는 claude mcp remove "
    "claude-hand-chat -s local)":
        "!! A claude-hand-chat that an earlier version registered in the local scope is still there and wins over .mcp.json → uv run "
        "tools/claude_hand.py update (or claude mcp remove claude-hand-chat -s local)",
    "!! .mcp.json의 claude-hand-chat이 승인 대기 중입니다 → start-supervisor.cmd로 연 Claude Code에서 승인하세요 (거절했었다면 claude mcp reset-project-choices "
    "후 다시)":
        "!! The claude-hand-chat of .mcp.json is waiting for approval → approve it in the Claude Code opened with start-supervisor.cmd (if "
        "you declined it before: claude mcp reset-project-choices, then again)",
    "!! server.json은 있지만 채널 서버가 응답하지 않습니다({exc}). 감독 세션이 비정상 종료됐을 수 있습니다 → start-supervisor.cmd로 다시 여세요":
        "!! server.json is there but the channel server does not answer ({exc}). The supervisor session may have ended abnormally → open "
        "it again with start-supervisor.cmd",
    "OK 감독 세션이 채팅을 받고 있습니다 (포트 {port}, 채팅 {chats})": "OK The supervisor session is taking chats (port {port}, chats {chats})",
    "아직 없음": "none yet",
    "!! 채널 서버는 떴지만 Claude Code와의 연결이 아직 끝나지 않았습니다. 잠시 뒤 다시 확인하세요":
        "!! The channel server is up but its connection with Claude Code is not finished yet. Check again in a moment",
    "!! 채널 서버가 대기 모드로 떴습니다: {reason}": "!! The channel server came up passive: {reason}",
    "   → 감독 세션은 claude-hand 폴더의 start-supervisor.cmd로 여세요. 데스크톱 앱이나 그냥 연 claude는 채팅을 받지 않습니다":
        "   → Open the supervisor session with start-supervisor.cmd in the claude-hand folder. The desktop app, or a claude opened "
        "plainly, takes no chats",
    "!! 감독 세션이 종료됐습니다 → start-supervisor.cmd로 다시 여세요": "!! The supervisor session has ended → open it again with start-supervisor.cmd",
    "!! 채널 서버가 아직 한 번도 시작되지 않았습니다": "!! The channel server has never started yet",
    "   → start-supervisor.cmd로 연 Claude Code에서 /mcp 를 입력해 claude-hand-chat 상태를 보세요 (⏸ 승인 대기면 승인, failed면 uv가 PATH에 있는지와 chat-setup 점검 "
    "결과를 확인)":
        "   → In the Claude Code opened with start-supervisor.cmd, type /mcp and look at the state of claude-hand-chat (⏸ waiting for "
        "approval: approve it; failed: check that uv is on PATH and what chat-setup's check said)",
    "-- CLAUDE_HAND_HOME={home} 가 설정돼 있습니다. Unity·Blender도 같은 값을 써야 합니다":
        "-- CLAUDE_HAND_HOME={home} is set. Unity and Blender must use the same value",
    # tools/supervisor_hook.py
    "프롬프트(SendMessage면 메시지) 첫 줄에 `[요청 <번호>]`를 적고 다시 부르세요. 번호는 그 채팅창 메시지의 <channel> 태그에 있는 request 값입니다. 채널 태그 없이 터미널이나 Claude 앱에서 온 "
    "요청이면 `[요청 없음]`이라고 적습니다.":
        "Write `[요청 <id>]` (or `[request <id>]`) in the first line of the prompt (of the message, for SendMessage) and call again. The id "
        "is the request value in the <channel> tag of that chat panel message. For a request that came from the terminal or the Claude "
        "app, with no channel tag, write `[요청 없음]` (or `[request none]`).",
    "{window}에게 맡기는 일이 어느 채팅창 요청 때문인지 적혀 있지 않아 막았습니다. ":
        "The work handed to {window} does not say which chat panel request it is for, so it was refused. ",
    "`[요청 {tag}]`은 채널이 모르는 요청 번호라 {window}에게 맡기는 호출을 막았습니다. ":
        "`[요청 {tag}]` is a request id the channel does not know, so the call handing work to {window} was refused. ",
    "사용자가 그 채팅창에서 [중지]를 누른 요청이라 이 호출을 막았습니다. 이 요청의 일은 더 맡기지 말고, 어디까지 했고 무엇이 남았는지 reply로 알리세요.":
        "The user pressed [Stop] on that request in its chat panel, so this call was refused. Hand out no more work for this request, and "
        "tell with reply how far it got and what is left.",
    "{window}은(는) 사용자가 잠가 둔 창이라(보호 규칙: 잠금) 이 호출을 막았습니다. 그 창에는 일을 맡기지 말고, 그 창이 잠겨 있어 하지 못했다고 reply로 알리세요. 잠금은 사용자가 그 창의 패널(보호 → 풀기)에서만 "
    "풉니다.":
        "{window} is a window the user has locked (protection rule: Locked), so this call was refused. Hand that window no work, and tell "
        "with reply that it could not be done because the window is locked. Only the user lifts the lock, in that window's panel (Protect "
        "→ Release).",
    "{window}은(는) 지금 다른 요청{other}으로 일하는 중이라 이 호출을 막았습니다. 한 창에 두 요청의 일이 겹치면 기록, 되돌리기 지점, 중지가 엉뚱한 요청 몫이 됩니다. 그 일이 끝났다는 알림이 온 뒤에 다시 맡기세요. "
    "기다리는 동안 이 요청의 채팅창에는 reply(working: true)로 \"앞의 일이 끝나면 할게요\"라고 알려 둡니다.":
        "{window} is at work for another request{other} right now, so this call was refused. When the work of two requests overlaps in one "
        "window, the record, the undo point and Stop end up with the wrong request. Hand it over again after the notice that that work has "
        "finished. Meanwhile tell this request's chat panel with reply(working: true) that you will do it once the work before it is done.",
    # tools/unity_bridge.py
    "'{title}' 창": "window '{title}'",
    "제목 없음": "no title",
    " [버튼: ": " [buttons: ",
    "Unity {name}가 대화상자 때문에 멈춰 있습니다: {dialogs}. 누를 버튼은 사용자가 정합니다. 그대로 감독에게 보고하세요. 대화상자가 닫히면 이 호출이 그때 실행될 수 있으니 다시 하기 전에 상태부터 확인하세요.":
        "Unity {name} is stopped at a dialog: {dialogs}. Which button to press is the user's decision. Report this as it is to the "
        "supervisor. When the dialog closes this call may run then, so check the state before doing it again.",
    # tools/chat_channel.py
    "Undo a chat panel's request in every window it changed: what the panel's 되돌리기 button does. Use it when the user asks in a chat "
    "panel to cancel or undo what was just done; do not hand the undo to subagents (a subagent undoes its window's newest request, "
    "which need not be the one meant). Each window undoes that request only when nothing newer changed it since. Returns what happened "
    "window by window; tell the user exactly that.":
        "Undo a chat panel's request in every window it changed: what the panel's Undo button does. Use it when the user asks in a chat "
        "panel to cancel or undo what was just done; do not hand the undo to subagents (a subagent undoes its window's newest request, "
        "which need not be the one meant). Each window undoes that request only when nothing newer changed it since. Returns what happened "
        "window by window; tell the user exactly that.",
    "Propose a protection rule for one window: something Claude must not change there from now on. Use it when the user says so in "
    "words ('Hero는 건드리지 마', 'Assets/Art는 손대지 마', '이 창은 보기만 해', '씬은 저장하지 마'). The rule holds only once the user presses [추가] under the "
    "proposal in that window's chat panel: say so in your reply. You cannot add, change or remove a rule yourself, and nothing loosens "
    "one but the user in the panel (보호 → 풀기): when asked to lift a rule, tell the user where that button is. A rule is checked by the "
    "hooks and by the window itself, whatever a subagent does; a note in NOTES.md is only read.":
        "Propose a protection rule for one window: something Claude must not change there from now on. Use it when the user says so in "
        "words ('don't touch Hero', 'leave Assets/Art alone', 'only look at this window', 'don't save the scene'). The rule holds only "
        "once the user presses [Add] under the proposal in that window's chat panel: say so in your reply. You cannot add, change or "
        "remove a rule yourself, and nothing loosens one but the user in the panel (Protect → Release): when asked to lift a rule, tell "
        "the user where that button is. A rule is checked by the hooks and by the window itself, whatever a subagent does; a note in "
        "NOTES.md is only read.",
    "true: kept with the project or the .blend. false: only until Claude Hand is turned off (the user said 'for now', '이번만', '오늘은')":
        "true: kept with the project or the .blend. false: only until Claude Hand is turned off (the user said 'for now', 'just this "
        "once', 'today')",
    "되돌렸어요": "undone",
    "되돌릴 것이 없었어요": "nothing to undo",
    "그 뒤에 다른 요청이 이 창을 바꿔서 건너뛰었어요. 그 창에서 먼저 되돌려 주세요":
        "skipped: another request changed this window after it. Undo that one in its window first",
    "지금 작업 중이라 건너뛰었어요. 끝난 뒤 다시 눌러 주세요": "skipped: it is at work right now. Press again once it has finished",
    "창이 응답하지 않아 건너뛰었어요": "skipped: the window did not answer",
    "되돌리지 못했어요": "could not be undone",
    "Claude Hand Tools가 예전 버전입니다": "Claude Hand Tools is an earlier version",
    "Claude Hand Chat 애드온이 예전 버전입니다": "The Claude Hand Chat add-on is an earlier version",
    "Not sent: say whether the work for this request still goes on. Send the same text again with working: true (a status while the "
    "work goes on, such as 'on it') or working: false (the result, or an answer that needed no work). The panel's work line, its 중지 "
    "button and the alert that an answer came depend on it. Say it on every reply from now on.":
        "Not sent: say whether the work for this request still goes on. Send the same text again with working: true (a status while the "
        "work goes on, such as 'on it') or working: false (the result, or an answer that needed no work). The panel's work line, its Stop "
        "button and the alert that an answer came depend on it. Say it on every reply from now on.",
    "\n…(너무 길어 앞의 {limit}자만 보냅니다)": "\n…(too long: only the first {limit} characters are sent)",
    "(맡기기)": "(hand-out)",
    "사용자가 채팅창에서 중지를 누름": "the user pressed Stop in the chat panel",
    "사용자가 잠근 창 (보호 규칙: 잠금)": "a window the user locked (protection rule: Locked)",
    "그 창이 다른 요청으로 일하는 중": "that window is at work for another request",
    "채팅창 요청이 아님": "not a chat panel request",
    "[{app} 창 정보: {window}]": "[{app} window info: {window}]",
    "감독이 차례 중이라, 차례가 끝나면 넘김": "the supervisor is in a turn: handed over when the turn ends",
    "{seconds:.1f}초 기다림": "waited {seconds:.1f}s",
    " (차례가 끝나지 않아 그냥 넘김)": " (the turn did not end: handed over anyway)",
    "{window} ({app}) 지금 모습": "{window} ({app}) as it looks now",
    "{window}({app})가 대화상자 때문에 멈춰 있어요. {app}에서 직접 골라 주세요: {dialogs}":
        "{window} ({app}) is stopped at a dialog. Please choose in {app} yourself: {dialogs}",
    "[claude-hand] 이 채팅창의 요청이 아직 답을 받지 못했습니다: \"{asked}\". 일이 끝났으면 reply로 결과를, 아직 하는 중이면 reply에 working: true로 지금 상황을 한 줄 보내세요.":
        "[claude-hand] This chat panel's request has had no answer yet: \"{asked}\". If the work is done, send the result with reply; if "
        "it still goes on, send one line on where things stand with reply and working: true.",
    "답이 늦어져 Claude Hand에 다시 알렸어요.": "The answer is late, so Claude Hand was reminded.",
    "하위 세션이 멈춘 뒤 답이 없음": "no answer after the subagents stopped",
    "하위 세션이 일한 흔적이 없음": "no sign of a subagent working",
    "이 요청": "This request",
    "앞 요청 “{asked}”": "Earlier request “{asked}”",
    "다른 하위 세션({agent})이 이 창에서 작업 중": "another subagent ({agent}) is at work in this window",
    "중지를 요청했어요. 지금 하던 동작 하나는 끝까지 갈 수 있어요.": "Stop requested. The one action under way may still run to its end.",
    "[claude-hand] 사용자가 이 채팅창에서 [중지]를 눌렀습니다{asked}. 이 요청으로 일하는 하위 세션의 다음 Unity·Blender 호출은 채널이 막습니다. 이 요청의 일을 더 맡기지 말고, 아직 돌고 있는 하위 "
    "세션이 있으면 멈춘 뒤, 어디까지 했고 무엇이 남았는지 reply로 알리세요.":
        "[claude-hand] The user pressed [Stop] in this chat panel{asked}. The channel refuses the next Unity/Blender call of the subagents "
        "working for this request. Hand out no more work for this request, stop any subagent that is still running, and then tell with "
        "reply how far it got and what is left.",
    "'{label}' 요청은 다른 창에서 되돌릴 것이 없었어요.": "Request '{label}' had nothing to undo in the other windows.",
    "그 뒤의 요청('{top}')이 이 창을 바꿔서 건너뛰었어요. 그 창에서 먼저 되돌려 주세요":
        "skipped: a later request ('{top}') changed this window. Undo that one in its window first",
    "'{label}' 요청 되돌리기: ": "Undoing request '{label}': ",
    "{window} 채팅창의 요청 '{label}'을 여기서도 되돌렸어요.": "The request '{label}' of the {window} chat panel was undone here too.",
    "[사용자가 채팅창에서 되돌렸음: {text}]": "[The user undid this in the chat panel: {text}]",
    "제안: ": "proposed: ",
    "[사용자가 제안된 보호 규칙을 추가했음: {rule}]": "[The user added the proposed protection rule: {rule}]",
    "[사용자가 제안된 보호 규칙을 추가하지 않았음: {rule}]": "[The user did not add the proposed protection rule: {rule}]",
    "추가함: ": "added: ",
    "추가 안 함: ": "not added: ",
    "Proposed in that window's chat panel. It is not a rule yet: it holds once the user presses [추가] there. Tell the user so in your "
    "reply; you will hear what they chose with that panel's next message.":
        "Proposed in that window's chat panel. It is not a rule yet: it holds once the user presses [Add] there. Tell the user so in your "
        "reply; you will hear what they chose with that panel's next message.",
    "그 요청이 바꾼 다른 창이 없습니다": "That request changed no other window",
    "claude mcp get/list가 상태 확인용으로 띄운 실행": "a start by claude mcp get/list to check the status",
    "채널 플래그 없이 연 세션으로 보임 (부모 프로세스 확인 실패: {exc})":
        "looks like a session opened without the channel flag (the parent process could not be checked: {exc})",
    "Claude Code가 이 서버를 채널로 불러옴": "Claude Code loaded this server as a channel",
    "채널 플래그 없이 연 Claude Code 세션 (감독 세션은 start-supervisor.cmd로 여세요)":
        "a Claude Code session opened without the channel flag (open the supervisor session with start-supervisor.cmd)",
    "다른 감독 세션이 이미 채팅창을 받고 있음": "another supervisor session is already taking the chat panels",
    "Claude Hand 창을 찾지 못해 최소화하지 않았습니다 (Windows Terminal 탭이면 그대로 둡니다)":
        "The Claude Hand window was not found, so it was not minimized (a Windows Terminal tab is left as it is)",
    "감독 세션이 종료됨": "the supervisor session ended",
    "[claude-hand] 사용자가 화면 언어를 {language}로 바꿨습니다. 이제부터 채팅창의 버튼 이름과 도구가 돌려주는 글(막힌 호출의 이유 포함)이 그 언어로 나옵니다. 사용자에게 버튼이나 칸을 가리킬 "
    "때 그 언어의 이름으로 말하세요. 답은 지금처럼 사용자가 쓴 언어로 합니다. 이 알림에는 답하지 않습니다.":
        "[claude-hand] The user changed the interface language to {language}. From now on the names of the chat panels' buttons and "
        "the texts the tools return (the reasons of refused calls among them) are in that language. Name a button or a section "
        "to the user as that language has it. Keep answering in the language the user writes in. Do not answer this notice.",
    # tools/claude_hand.py
    "등록(대체 방식): ": "registering (fallback): ",
    "채널 등록": "channel registration",
    "답장·현재 상황 도구 자동 허용, 감독이 Unity·Blender를 직접 조작하는 도구 막기":
        "allow the reply and current-context tools without asking, block the tools with which the supervisor would operate Unity and "
        "Blender directly",
    "고침": "fixed",
    "만듦": "made",
    "채널 등록과 답장 도구 허용은 이미 돼 있습니다.": "The channel registration and the reply tool's allowance are already in place.",
    "건너뜀: 채널 서버 미리 실행(--check)": "skipped: starting the channel server once beforehand (--check)",
    "채널 서버를 한 번 미리 실행합니다(처음이면 uv가 Python을 내려받아 몇 분 걸릴 수 있습니다)...":
        "Starting the channel server once beforehand (the first time uv may download Python, which can take a few minutes)...",
    "점검 실패: ": "check failed: ",
    "점검 통과: Python {python}, 포트 {port} 사용 가능, 하위 세션 {count}개": "check passed: Python {python}, port {port} free, {count} subagents",
    "다음: start-supervisor.cmd 로 감독 세션을 여세요. 켤 때마다 개발용 채널 경고가 뜨고(I am using this for\n      local development), 처음 한 번은 .mcp.json "
    "서버(claude-hand-chat) 사용 승인 창도 뜹니다. 내용을 읽고 승인하세요.":
        "Next: open the supervisor session with start-supervisor.cmd. Every start shows the development channel warning (I am using this "
        "for\n      local development), and the first time also an approval prompt for the .mcp.json server (claude-hand-chat). Read it "
        "and approve.",
    "Blender 사용자 폴더를 찾지 못했습니다. Blender를 한 번 실행한 뒤 다시 하거나 --addons-dir로 알려주세요.":
        "Blender's user folder was not found. Run Blender once and try again, or name the folder with --addons-dir.",
    "{dry}복사: {source} -> {target}": "{dry}copy: {source} -> {target}",
    "Blender 실행 파일을 찾지 못해 켜지는 못했습니다. Preferences > Add-ons에서 'Claude Hand Chat'을 켜세요.":
        "The Blender executable was not found, so the add-on could not be turned on. Turn on 'Claude Hand Chat' in Preferences > Add-ons.",
    "{dry}켜기: {blender} --background (애드온 활성화 후 환경설정 저장)":
        "{dry}enable: {blender} --background (enables the add-on, then saves the preferences)",
    "Blender를 실행하지 못했습니다: {exc}": "Blender could not be run: {exc}",
    "켰습니다. 열려 있던 Blender는 다시 열어야 N 패널에 Claude Hand 탭이 보입니다.":
        "Enabled. A Blender that was open has to be opened again for the Claude Hand tab to show in the N panel.",
    "켜지 못했습니다. Preferences > Add-ons에서 'Claude Hand Chat'을 직접 켜세요.":
        "Could not enable it. Turn on 'Claude Hand Chat' yourself in Preferences > Add-ons.",
    "Claude Hand가 이미 실행 중입니다. 작업 표시줄에서 'Claude Hand' 창을 찾아 그 창을 쓰세요.":
        "Claude Hand is already running. Find the 'Claude Hand' window on the taskbar and use that one.",
    "채팅 설정을 맞췄습니다.": "Chat settings brought up to date.",
    "채팅 설정을 고치지 못했습니다: {exc}": "The chat settings could not be fixed: {exc}",
    "열려 있는 창의 하위 세션을 준비했습니다: ": "Subagents made ready for the open windows: ",
    "하위 세션을 만들지 못했습니다: {exc}": "The subagents could not be made: {exc}",
    "Blender 채팅 애드온이 이 버전과 다릅니다. Blender에서 옮기기·되돌리기가 안 될 수 있으니 `uv run tools/claude_hand.py update`를 한 뒤 Blender를 다시 여세요.":
        "The Blender chat add-on differs from this version. Moving things and undoing may not work in Blender: run `uv run "
        "tools/claude_hand.py update`, then open Blender again.",
    "교환 폴더에서 {days}일 넘은 파일 {count}개({mb:.1f}MB)를 지웠습니다.":
        "Removed {count} files older than {days} days ({mb:.1f}MB) from the exchange folder.",
    " 지난 대화를 이어서 엽니다. 새 대화로 시작하려면: uv run tools/claude_hand.py new-session":
        " Carrying on the earlier conversation. To start a new one: uv run tools/claude_hand.py new-session",
    " 새 대화로 시작합니다. 다음부터는 켤 때마다 이 대화를 이어서 엽니다.": " Starting a new conversation. From now on every start carries this one on.",
    " 다른 Claude Code 세션에서 켜져서, 그 세션의 표시 변수 {count}개를 지우고 따로 시작합니다.":
        " Started from another Claude Code session: {count} of that session's marker variables are cleared and this starts on its own.",
    " 데스크톱 앱에서 보기(Remote Control)는 이번에는 끄고 시작합니다:": " Watching from the desktop app (Remote Control) is left off for this start:",
    " 진행 보기: Claude 데스크톱 앱의 Code 탭(또는 claude.ai/code, 모바일 앱)에서 '{name}' 세션을 여세요.":
        " To watch: open the '{name}' session in the Code tab of the Claude desktop app (or on claude.ai/code, or in the mobile app).",
    "교환 폴더의 파일을 {keep_days}일 동안 둡니다.": "Files in the exchange folder are kept for {keep_days} days.",
    "교환 폴더를 자동으로 정리하지 않습니다.": "The exchange folder is not cleaned by itself.",
    "{folder}: 자동 정리가 꺼져 있습니다. 지금 지우려면 --days N을 붙이세요.": "{folder}: cleaning by itself is off. To delete now, add --days N.",
    "(dry run) {folder}: {days}일 넘은 파일 {count}개({mb:.1f}MB)를 지울 수 있습니다.":
        "(dry run) {folder}: {count} files older than {days} days ({mb:.1f}MB) can be deleted.",
    "{folder}: {days}일 넘은 파일 {count}개({mb:.1f}MB)를 지웠습니다.": "{folder}: deleted {count} files older than {days} days ({mb:.1f}MB).",
    "{log_dir}: 최근 {days}일 동안 채팅창 요청 기록이 없습니다. Claude Hand가 켜져 있을 때 채팅창에서 보낸 요청이 여기에 남습니다.":
        "{log_dir}: no chat panel requests recorded in the last {days} days. Requests sent from a chat panel while Claude Hand is on are "
        "recorded here.",
    "답 {seconds:.0f}초 뒤": "reply after {seconds:.0f}s",
    "답 없음": "no reply",
    " · 막힘 {refused}": " · refused {refused}",
    "{when}  {request:<16} {chat_id:<24} 동작 {actions}{refused} · {answer}  \"{asked}\"":
        "{when}  {request:<16} {chat_id:<24} actions {actions}{refused} · {answer}  \"{asked}\"",
    "\n한 요청을 자세히: uv run tools/claude_hand.py trace <채팅 이름>   또는   trace --request <번호>":
        "\nOne request in detail: uv run tools/claude_hand.py trace <chat name>   or   trace --request <id>",
    "최근 {days}일 기록에 그런 요청이 없습니다. `trace`만 치면 요청 목록이 나옵니다.":
        "No such request in the record of the last {days} days. `trace` alone lists the requests.",
    "요청 {request}  ({chat_id}, {day})": "request {request}  ({chat_id}, {day})",
    "  (아직 답이 없습니다)": "  (no reply yet)",
    "{log_dir}: 최근 {days}일 동안 시간·토큰 기록이 없습니다. Claude Hand가 켜져 있을 때 채팅창 요청에 결과 답이 오면 남습니다.":
        "{log_dir}: no time and token records in the last {days} days. One is written when the result of a chat panel request comes while "
        "Claude Hand is on.",
    "토큰 기록 없음": "no token record",
    "{when}  {chat_id:<24} {duration} · 동작 {actions}번 · {tokens}  \"{asked}\"":
        "{when}  {chat_id:<24} {duration} · {actions} actions · {tokens}  \"{asked}\"",
    "\n합계: 요청 {count}개 · 기다린 시간 {duration} · {tokens}": "\nTotal: {count} requests · waited {duration} · {tokens}",
    "  {who:<24} API {calls}번 · {tokens}": "  {who:<24} API {calls} calls · {tokens}",
    "날짜별:": "By day:",
    "  {day}  요청 {requests}개 · {duration} · {tokens}": "  {day}  {requests} requests · {duration} · {tokens}",
    "\n출력: 써낸 토큰. 새 입력: 새로 읽은 토큰(캐시에 새로 쓴 것 포함). 다시 읽기: 캐시에서 다시 읽은 토큰(대부분이고 쌉니다).":
        "\noutput: tokens written. new input: tokens read anew (including those newly written to the cache). read again: tokens read back "
        "from the cache (most of them, and cheap).",
    "이제 쓰는 방식은 하나입니다. Claude Hand 창(감독 세션)이 일을 하고, Unity·Blender 채팅창과 Claude 데스크톱 앱('{name}' 세션)은 같은 세션을 보는 창입니다.":
        "There is one way of using it now. The Claude Hand window (the supervisor session) does the work, and the Unity and Blender chat "
        "panels and the Claude desktop app (the '{name}' session) are windows onto the same session.",
    "이 세션이 Claude Hand(감독 세션)입니다. 여기서 바로 시키면 됩니다.": "This session is Claude Hand (the supervisor session). Just ask here.",
    "Claude Hand가 이미 켜져 있습니다. Claude 데스크톱 앱의 Code 탭에서 '{name}' 세션을 여세요(작업 표시줄의 'Claude Hand' 창과 같은 대화입니다).":
        "Claude Hand is already on. Open the '{name}' session in the Code tab of the Claude desktop app (the same conversation as the "
        "'Claude Hand' window on the taskbar).",
    "(dry run) 새 창에서 실행: ": "(dry run) would run in a new window: ",
    "Claude Hand 창을 열지 못했습니다: {exc}. 바탕화면의 Claude Hand 아이콘을 누르세요.":
        "The Claude Hand window could not be opened: {exc}. Click the Claude Hand icon on the Desktop.",
    "Claude Hand 창을 열었습니다.": "Opened the Claude Hand window.",
    "  1. 새로 뜬 'Claude Hand' 창에 개발용 채널 경고가 뜹니다(켤 때마다). I am using this for local development 를 고르고 Enter.":
        "  1. The new 'Claude Hand' window shows the development channel warning (at every start). Choose I am using this for local "
        "development and press Enter.",
    "     창은 세션이 일을 시작하면 저절로 최소화됩니다. 닫지는 마세요.": "     The window minimizes by itself once the session starts working. Do not close it.",
    "     창은 닫지 마세요.": "     Do not close the window.",
    " 일을 시작하면 저절로 최소화됩니다.": " It minimizes by itself once work starts.",
    "  2. Claude 데스크톱 앱의 Code 탭 세션 목록에서 '{name}'을 여세요. 거기서 대화하면 됩니다.":
        "  2. Open '{name}' in the session list of the Claude desktop app's Code tab. Talk there.",
    "다음에 Claude Hand를 켜면 새 대화로 시작합니다. 지금까지의 대화는 지워지지 않고 claude --resume 목록에 남습니다.":
        "The next start of Claude Hand begins a new conversation. The conversation so far is not deleted: it stays in the claude --resume "
        "list.",
    "지금 켜져 있는 Claude Hand는 그대로입니다. 닫고 다시 켜면 새 대화가 됩니다.":
        "The Claude Hand that is on now stays as it is. Close it and start it again for the new conversation.",
    "Claude Hand 창 자동 최소화: {state}": "Minimizing the Claude Hand window by itself: {state}",
    "켜짐": "On",
    "꺼짐": "Off",
    "  Windows에서 세션이 처음 일을 시작하면(도구 호출이나 허락 요청) Claude Hand 창을 최소화합니다. 시작할 때의 확인 질문은 창이 보이는 동안 답할 수 있습니다.":
        "  On Windows the Claude Hand window is minimized when the session first starts working (a tool call or a permission request). The "
        "questions at start can be answered while the window shows.",
    " (기본값)": " (default)",
    "  다음에 Claude Hand를 켤 때부터 Claude 데스크톱 앱의 Code 탭(또는 claude.ai/code, 모바일 앱)에 '{name}' 세션이 보입니다. 감독의 대화와 도구 실행, 백그라운드로 도는 하위 세션이 보입니다.":
        "  From the next start of Claude Hand the '{name}' session shows in the Code tab of the Claude desktop app (or on claude.ai/code, "
        "or in the mobile app): the supervisor's conversation and tool runs, and the subagents running in the background.",
    "  필요한 것: Pro·Max·Ultra·Team·Enterprise 구독의 claude.ai 로그인(API 키는 안 됨). Team·Enterprise는 관리자가 켜야 합니다.":
        "  Needed: a claude.ai login with a Pro, Max, Ultra, Team or Enterprise subscription (an API key does not work). On Team and "
        "Enterprise an admin has to turn it on.",
    "  주의: {problem}": "  Note: {problem}",
    "  위 문제가 있는 동안은 Claude Hand를 켤 때 Remote Control을 빼고 시작합니다.": "  While the above holds, Claude Hand starts without Remote Control.",
    "  지금 켜져 있는 Claude Hand에는 적용되지 않습니다. 그 창에서 /remote-control 을 입력하거나, 닫고 다시 켜세요.":
        "  It does not apply to the Claude Hand that is on now. Type /remote-control in that window, or close it and start it again.",
    "(dry run) 바탕화면과 시작 메뉴{startup}에 '{name}' 바로가기 {what} → {script}":
        "(dry run) {what} the '{name}' shortcut on the Desktop and in the Start menu{startup} → {script}",
    "와 시작프로그램": " and in Startup",
    "삭제": "remove",
    "만들기": "create",
    "Claude Hand가 꺼져 있습니다. 켜져 있는 동안 열린 창들이 무엇을 하고 있는지 보여 줍니다.":
        "Claude Hand is off. While it is on, this shows what the open windows are doing.",
    "채팅창이 열려 있거나 일하고 있는 창이 없습니다.": "No window has its chat panel open or is at work.",
    "{prefix}하위 세션: 고침 {written}개, 그대로 {unchanged}개": "{prefix}subagents: {written} fixed, {unchanged} unchanged",
    "{prefix}감독 스킬: {path}": "{prefix}supervisor skill: {path}",
    "{prefix}지움: 예전 이름의 스킬 {parent}": "{prefix}removed: the skill under its old name {parent}",
    "{prefix}지움: 이전 버전의 local 범위 claude-hand-chat 등록": "{prefix}removed: an earlier version's local-scope registration of claude-hand-chat",
    "{prefix}고침: Blender 채팅 애드온 {installed} (Blender를 다시 열어야 적용됩니다)":
        "{prefix}fixed: the Blender chat add-on {installed} (open Blender again for it to apply)",
    "감독 세션을 열어 두었다면 닫고 start-supervisor.cmd로 다시 여세요.":
        "If the supervisor session is open, close it and open it again with start-supervisor.cmd.",
    "채팅은 아직 설정하지 않았습니다(원하면 chat-setup).": "The chat is not set up yet (chat-setup, if you want it).",
    "'{project}' Unity 프로젝트를 찾지 못했습니다. Unity로 열려 있고 Claude Hand Tools가 컴파일됐는지 확인하세요.":
        "The Unity project '{project}' was not found. Check that it is open in Unity and that Claude Hand Tools has compiled.",
    "{tool} 도구가 없습니다. 있는 도구: {tools}": "There is no tool {tool}. The tools there are: {tools}",
    "\n빠진 항목 {missing}개": "\n{missing} items missing",
    "\n모두 준비됐습니다.": "\nEverything is ready.",
    "(진단 {seconds:.1f}초)": "(checked in {seconds:.1f}s)",
    "\n문제 {failed}개": "\n{failed} problems",
    "\n모두 통과했습니다.": "\nEverything passed.",
    "({seconds:.1f}초)": "({seconds:.1f}s)",
    "그대로: {note}": "unchanged: {note}",
    "바꿀 것이 없습니다.": "Nothing to change.",
    "{dry}변경: {change}": "{dry}change: {change}",
    "Packages/manifest.json을 고쳤습니다. 원래 파일: {backup}": "Packages/manifest.json fixed. The original file: {backup}",
    "Unity가 열려 있으면 Unity 창이 앞에 올 때 패키지를 받아 옵니다. 닫혀 있으면 unity-open으로 여세요.":
        "If Unity is open it fetches the packages when its window comes to the front. If it is closed, open it with unity-open.",
    "Unity {version} 에디터를 찾지 못했습니다. --unity-exe로 Unity.exe 경로를 알려주거나 Unity Hub로 여세요.":
        "The Unity {version} editor was not found. Give the path of Unity.exe with --unity-exe, or open the project with Unity Hub.",
    "실행: ": "run: ",
    "포트 {port}은(는) 이미 다른 Blender가 쓰고 있습니다. 다른 포트를 고르세요.": "Port {port} is already in use by another Blender. Choose another port.",
    "Blender 실행 파일을 찾지 못했습니다. --blender-exe로 blender.exe 경로를 알려주세요.":
        "The Blender executable was not found. Give the path of blender.exe with --blender-exe.",
    "{blender} {file} (포트 {port})": "{blender} {file} (port {port})",
    "(새 파일)": "(new file)",
    "Blender가 포트 {port}에서 응답합니다. 이 포트를 파일에 남기려면 .blend를 저장하세요.":
        "Blender answers on port {port}. To keep this port with the file, save the .blend.",
    "{wait}초 안에 포트 {port}에서 응답이 없습니다. 애드온이 켜져 있는지(uvx mcp-for-blender setup) 확인하세요.":
        "No answer on port {port} within {wait} seconds. Check that the add-on is enabled (uvx mcp-for-blender setup).",
    "Unity MCP 설정: {transport} ({origin})": "Unity MCP setup: {transport} ({origin})",
    "실행 중": "running",
    "응답 없음": "no answer",
    "  열려 있는 Unity 프로젝트 없음 (Unity에서 Window > MCP for Unity 상태가 Connected인지 확인)":
        "  No Unity project open (in Unity, check that Window > MCP for Unity says Connected)",
    "Blender (MCP for Blender, 포트 {blender_ports}):": "Blender (MCP for Blender, ports {blender_ports}):",
    "  {name:<28} 포트 {port:<6} {version:<8} {file}": "  {name:<28} port {port:<6} {version:<8} {file}",
    "  응답한 Blender 없음 (3D 뷰포트 N 패널 > MCP for Blender 탭에서 서버가 켜져 있는지 확인)":
        "  No Blender answered (check that the server is on in the 3D viewport's N panel > MCP for Blender tab)",
    "열려 있는 Unity 프로젝트도, 응답하는 Blender도 없습니다. 하위 세션을 만들 창을 먼저 여세요.":
        "No Unity project is open and no Blender answers. Open the windows to make subagents for first.",
    "{prefix}도구 자동 허용: {path} ({rules})": "{prefix}tools allowed without asking: {path} ({rules})",
    "{prefix}Unity MCP 설정: {transport} ({origin})": "{prefix}Unity MCP setup: {transport} ({origin})",
    "{prefix}Blender MCP 설정: {command} {args} ({origin})": "{prefix}Blender MCP setup: {command} {args} ({origin})",
    "{prefix}주의: {warning}": "{prefix}note: {warning}",
    "{prefix}작성: {p}": "{prefix}written: {p}",
    "{prefix}그대로: {p}": "{prefix}unchanged: {p}",
    "{prefix}삭제: {p}": "{prefix}removed: {p}",
    "{prefix}스킬: {path}": "{prefix}skill: {path}",
    "Claude Code에서 /agents 로 unity-* / blender-* 하위 세션이 보이는지 확인하세요. agents 폴더를 처음 만든 경우에만 Claude Code를 다시 시작하면 됩니다.":
        "In Claude Code, check with /agents that the unity-* / blender-* subagents show. Claude Code needs a restart only when the agents "
        "folder was made for the first time.",
    # tools/handlib/checks.py
    "실행하지 못함: {exc}": "could not be run: {exc}",
    "터미널에서 직접 실행해 오류를 보세요: {shown}": "Run it in a terminal yourself and see the error: {shown}",
    "뜸": "starts",
    "오류 내용 없음": "no error text",
    "이 세션은 Claude 데스크톱 앱이 띄운 것이라 uv가 실행 환경을 새로 만들지 못합니다(앱이 AppData를 따로 보여 줍니다). 시작 메뉴에서 연 터미널에서 한 번 실행하세요: {shown}  (한 번 만들어지면 여기서도 "
    "됩니다)":
        "This session was started by the Claude desktop app, so uv cannot build a new environment (the app shows AppData apart). Run it "
        "once in a terminal opened from the Start menu: {shown}  (once it has been built, it works here too)",
    "프로그램 버전 확인 중...": "Checking program versions...",
    "기본 도구": "basic tools",
    "Claude Code CLI (claude 명령)": "Claude Code CLI (the claude command)",
    "없음": "none",
    "winget install --id astral-sh.uv -e  (winget이 없으면 https://docs.astral.sh/uv/getting-started/installation/ 안내대로)":
        "winget install --id astral-sh.uv -e  (without winget: as https://docs.astral.sh/uv/getting-started/installation/ says)",
    "git (Unity가 MCP for Unity를 받을 때 필요)": "git (Unity needs it to fetch MCP for Unity)",
    "winget install --id Git.Git -e  (winget이 없으면 https://git-scm.com/download/win 에서 받아 설치)":
        "winget install --id Git.Git -e  (without winget: download and install from https://git-scm.com/download/win)",
    "https://git-scm.com/downloads 에서 설치": "install from https://git-scm.com/downloads",
    "Node.js 18 이상 (agent-desktop용)": "Node.js 18 or later (for agent-desktop)",
    "winget install --id OpenJS.NodeJS.LTS -e  (winget이 없으면 https://nodejs.org 에서 LTS 설치)":
        "winget install --id OpenJS.NodeJS.LTS -e  (without winget: install the LTS from https://nodejs.org)",
    "Blender 실행 파일": "Blender executable",
    "못 찾음": "not found",
    "Blender를 설치하거나 blender-open --blender-exe 로 경로를 알려주세요": "Install Blender, or give its path with blender-open --blender-exe",
    "MCP for Blender 애드온": "MCP for Blender add-on",
    "설치 안 됨": "not installed",
    "uvx mcp-for-blender setup --dry-run  (확인 후 --yes)": "uvx mcp-for-blender setup --dry-run  (then --yes once checked)",
    "Claude 설정에 MCP for Blender 등록": "MCP for Blender registered in Claude's settings",
    "Blender 창 찾는 중...": "Looking for Blender windows...",
    "응답하는 Blender 창": "Blender windows that answer",
    "Blender를 열고 애드온 서버를 켜세요 (blender-open --port 9876)": "Open Blender and turn the add-on's server on (blender-open --port 9876)",
    "Unity 에디터 찾는 중...": "Looking for Unity editors...",
    "Claude 설정에 MCP for Unity 등록": "MCP for Unity registered in Claude's settings",
    "연결된 Unity 에디터": "Unity editors connected",
    "Unity 프로젝트를 열고 Window > MCP for Unity 가 Connected인지 확인": "Open a Unity project and check that Window > MCP for Unity says Connected",
    "MCP for Unity 서버가 뜨는지 확인 중...": "Checking that the MCP for Unity server starts...",
    "MCP for Unity 서버 실행 (하위 세션이 띄우는 것)": "MCP for Unity server starts (the one a subagent starts)",
    "MCP for Unity 서버가 맡은 창만 봄 (다른 Unity 창으로 새지 않음)": "MCP for Unity server sees only its own window (nothing leaks into another Unity window)",
    "맡은 창만 봄": "sees only its own window",
    "서버가 고정 장치를 읽지 않음": "the server does not read the pin",
    "확인하지 못함: {exc}": "could not check: {exc}",
    "Unity 창을 둘 이상 열어 두면, 맡은 창이 잠깐 응답하지 않을 때(스크립트를 오래 다시 읽음 · 바쁨 · 닫힘) 하위 세션의 작업이 다른 창으로 갈 수 있습니다. MCP for Unity 서버가 uvx로 등록돼 있는지 확인하세요: {command}  그 전까지는 Unity 창을 하나만 열고 쓰세요.":
        "With two or more Unity windows open, a subagent's work can go to another window while its own does not answer for a moment (a long script reload, busy, closed). Check that the MCP for Unity server is registered with uvx: {command}  Until then, work with one Unity window open.",
    "MCP for Unity 연결": "MCP for Unity connection",
    "열려 있지만 MCP for Unity에 안 보임: ": "open, but MCP for Unity does not see it: ",
    "Unity의 Window > MCP for Unity에서 Transport를 Stdio로 하고 Connected인지 확인하세요. Claude Code 등록 방식({transport})이 그 창의 Transport와 달라도 이렇게 "
    "됩니다. 바꿨다면 그 창에서 Claude Code의 Configure를 다시 누르세요. 이 상태로도 ch_* 도구는 쓸 수 있지만 MCP for Unity 기본 도구는 하위 세션에서 안 됩니다":
        "In Unity's Window > MCP for Unity, set Transport to Stdio and check that it says Connected. This also happens when the way Claude "
        "Code registered it ({transport}) differs from that window's Transport. If you changed it, press Configure for Claude Code in that "
        "window again. In this state the ch_* tools work, but MCP for Unity's own tools do not in a subagent",
    "Claude Hand Tools 연결": "Claude Hand Tools connection",
    "MCP for Unity에는 보이지만 Claude Hand Tools가 응답 안 함: ": "MCP for Unity sees it, but Claude Hand Tools does not answer: ",
    "그 프로젝트에 uv run tools/claude_hand.py unity-install 을 했는지, Unity Console에 컴파일 오류가 없는지 확인하세요":
        "Check that uv run tools/claude_hand.py unity-install was run for that project, and that the Unity Console shows no compile errors",
    "프로젝트 {name}": "project {name}",
    "{project}: Unity 프로젝트가 아님": "{project}: not a Unity project",
    "경로를 확인하세요": "Check the path",
    "{label}: 패키지": "{label}: packages",
    "Packages/manifest.json을 확인하세요": "Check Packages/manifest.json",
    "{label}: 에디터 {version}": "{label}: editor {version}",
    "Unity Hub 기본 위치에서 못 찾음": "not found in Unity Hub's default place",
    "Unity Hub에서 해당 버전을 설치하거나 Hub로 직접 여세요": "Install that version in Unity Hub, or open the project with the Hub yourself",
    "채팅": "chat",
    "Claude Code 2.1.234 이상 (채팅창의 허용/거부 버튼)": "Claude Code 2.1.234 or later (the chat panel's Allow/Deny buttons)",
    "Channels를 쓸 수 있는 로그인 (claude.ai 또는 Console API 키)": "A login that can use Channels (claude.ai or a Console API key)",
    "이 환경은 {names} 사용 중 - Channels를 쓸 수 없음": "this environment uses {names} - Channels cannot be used",
    "Bedrock/Vertex/Foundry 아님": "not Bedrock/Vertex/Foundry",
    "claude.ai 로그인이나 Console API 키로 Claude Code를 쓰세요": "Use Claude Code with a claude.ai login or a Console API key",
    "채팅 채널 등록과 답장 허용 (claude-hand 폴더)": "Chat channel registered and reply allowed (claude-hand folder)",
    "됨": "done",
    "빠짐: ": "missing: ",
    "Blender 채팅 애드온": "Blender chat add-on",
    "하위 세션 정의 파일": "subagent definition files",
    "감독 스킬": "supervisor skill",
    "데스크톱 앱에서 보기": "watching from the desktop app",
    "Remote Control을 쓸 수 있는 로그인": "A login that can use Remote Control",
    "문제 없음": "no problem",
    "claude.ai 계정(Pro·Max·Team 등)으로 /login 하세요": "/login with a claude.ai account (Pro, Max, Team and so on)",
    "이전 버전 흔적 확인 중...": "Looking for what an earlier version left...",
    "갱신": "update",
    "이전 버전의 local 범위 채팅 등록 (.mcp.json보다 먼저 쓰임)": "An earlier version's local-scope chat registration (wins over .mcp.json)",
    "설치된 감독 스킬이 저장소 버전과 다름": "The installed supervisor skill differs from the repository's",
    "이전 버전으로 만든 하위 세션 정의": "Subagent definitions made by an earlier version",
    "설치된 Blender 채팅 애드온이 저장소 버전과 다름": "The installed Blender chat add-on differs from the repository's",
    "       고치기: {fix}": "       fix: {fix}",
    "실패: ": "failed: ",
    " 외 {count}개": " and {count} more",
    "끝까지 돌지 못함: ": "did not run to the end: ",
    "출력 없음": "no output",
    "{count}개 통과": "{count} passed",
    " (건너뜀 {count})": " (skipped {count})",
    "uv run tools/claude_hand.py update (그래도 안 되면 Git Bash에서 uv가 실행되는지 보세요)":
        "uv run tools/claude_hand.py update (if that does not help, see whether uv runs in Git Bash)",
    "{stem}: 안전장치 훅": "{stem}: guard hook",
    "안전장치": "guard",
    "Git Bash를 찾지 못해 훅 명령을 돌려 보지 못함": "Git Bash was not found, so the hook command could not be tried",
    "훅 명령을 돌리지 못함: {exc}": "the hook command could not be run: {exc}",
    "다른 대상의 호출을 막음": "refuses a call for another target",
    "막지 못함 (종료 코드 {returncode}): {said}": "did not refuse (exit code {returncode}): {said}",
    "한 바퀴: 창 없는 Unity가 통로를 엶": "round trip: a Unity without a window opens its bridge",
    "통로 등록 파일이 생기지 않음: ": "the bridge's registration file did not appear: ",
    "한 바퀴: Blender가 열린 Unity를 찾아 보냄": "round trip: Blender finds the open Unity and sends to it",
    "Unity의 통로 등록 파일을 Blender가 읽지 못함": "Blender could not read Unity's bridge registration file",
    "한 바퀴: Blender 패널에서 보낸 모델이 Unity에 들어와 씬에 놓이고, 저장을 따라가게 됨":
        "round trip: a model sent from the Blender panel arrives in Unity, is placed in the scene, and follows saves",
    "한 바퀴: Blender에서 고쳐 저장하면 Unity의 모델이 제자리에서 바뀜": "round trip: changing it in Blender and saving replaces the model in Unity in place",
    "한 바퀴: 바뀐 것 없이 저장하면 Unity의 모델은 그대로": "round trip: saving with nothing changed leaves the model in Unity as it is",
    "Unity 안 테스트 ({name})": "tests inside Unity ({name})",
    "Unity로 열려 있어 건너뜀 (창 없이 한 번 더 열 수 없음). 닫고 다시 돌리면 확인":
        "skipped: it is open in Unity (it cannot be opened once more without a window). Close it and run again to check",
    "Unity {version} 에디터를 Unity Hub 기본 위치에서 못 찾아 건너뜀": "skipped: the Unity {version} editor was not found in Unity Hub's default place",
    "Blender에서 시험용 모델 내보내는 중...": "Exporting test models from Blender...",
    "Blender가 보낸 모델(FBX)로 시험": "tested with models Blender sent (FBX)",
    "export_fbx로 같은 오브젝트를 두 번 내보냄": "the same object exported twice with export_fbx",
    "내보내지 못해 OBJ 파일로 대신함: ": "could not export, so OBJ files stand in: ",
    "Unity {version}를 창 없이 열어 시험하는 중 (1~2분)...": "Opening Unity {version} without a window and testing (1-2 minutes)...",
    "Unity 실행": "Unity run",
    "15분 안에 끝나지 않음": "did not finish within 15 minutes",
    "Unity가 대화상자를 띄웠는지 작업 관리자에서 확인하세요": "Check in Task Manager whether Unity has put up a dialog",
    "결과 파일이 없음 (종료 코드 {returncode}): ": "no result file (exit code {returncode}): ",
    "로그 없음": "no log",
    "Unity로 프로젝트를 열어 Console의 컴파일 오류를 보세요": "Open the project in Unity and see the compile errors in the Console",
    "프로젝트에 Claude Hand Tools가 들어 있는지 보세요 (unity-install)": "See whether the project has Claude Hand Tools (unity-install)",
    "Unity가 보낸 모델을 Blender에서 받는 중...": "Receiving in Blender the model Unity sent...",
    "Unity가 보낸 모델을 Blender가 받음 (import_model)": "Blender receives the model Unity sent (import_model)",
    "창 없는 Unity에 통로를 열고, Blender가 모델을 보내고 저장하는 중 (30초쯤)...":
        "Opening a bridge in a Unity without a window, with Blender sending a model and saving (about 30 seconds)...",
    "감독 세션 기록에서 토큰 읽기": "reading tokens from the supervisor session's transcript",
    "감독 세션 기록이 아직 없어 건너뜀 (Claude Hand를 한 번 쓴 뒤 다시 돌리면 확인)":
        "skipped: no supervisor session transcript yet (use Claude Hand once and run again to check)",
    "{name}: 토큰이 적힌 응답을 하나도 찾지 못함": "{name}: not one answer with tokens was found",
    "Claude Code의 기록 형식이 바뀐 것 같습니다. 채팅창의 '이 요청' 줄에는 시간만 나옵니다 (tools/handlib/usage.py)":
        "The format of Claude Code's transcripts seems to have changed. The chat panel's 'This request' line shows the time only "
        "(tools/handlib/usage.py)",
    "응답 {answers}개": "{answers} answers",
    ", 요청 번호로 갈린 요청 {numbered}개": ", {numbered} requests told apart by request id",
    ", 요청 번호가 붙은 것은 아직 없음": ", none with a request id yet",
    "Python 테스트 실행 중...": "Running the Python tests...",
    "테스트": "tests",
    "Python 테스트": "Python tests",
    "Blender 안 애드온 테스트 실행 중...": "Running the add-on tests inside Blender...",
    "Blender 안 애드온 테스트 ({name})": "add-on tests inside Blender ({name})",
    "selftest 출력의 실패한 테스트 이름으로 tools/tests/test_blender_chat_addon.py를 보세요":
        "Look up the failed test's name from selftest's output in tools/tests/test_blender_chat_addon.py",
    "Blender 안 애드온 테스트": "add-on tests inside Blender",
    "Blender 실행 파일을 못 찾아 건너뜀": "skipped: the Blender executable was not found",
    "Unity 확인 중...": "Checking Unity...",
    "저장소에 있는데 Unity에 없는 도구: ": "tools the repository has and Unity does not: ",
    "{version} (저장소 {repo})": "{version} (repository {repo})",
    "ch_* 도구 {count}개 응답": "{count} ch_* tools answer",
    "Unity 창을 앞으로 가져와 새 패키지를 컴파일하게 하고, Console에 컴파일 오류가 없는지 보세요":
        "Bring the Unity window to the front so it compiles the new package, and see that the Console shows no compile errors",
    "씬 뷰를 찍음": "captured the Scene view",
    "그림 파일이 생기지 않음": "no picture file appeared",
    "{name}: 결과 미리보기 찍기": "{name}: capturing a result preview",
    "Unity에 씬 뷰가 열려 있는지 보세요": "See whether a Scene view is open in Unity",
    "열린 Unity": "open Unity",
    "Claude Hand Tools가 응답하는 Unity가 없어 건너뜀": "skipped: no Unity in which Claude Hand Tools answers",
    "Blender 확인 중...": "Checking Blender...",
    "{name}@{port}: 채팅 애드온": "{name}@{port}: chat add-on",
    "상태를 읽지 못함: {exc}": "the state could not be read: {exc}",
    "MCP for Blender 서버가 켜져 있는지 보세요": "See whether the MCP for Blender server is on",
    "켜져 있지 않음": "not enabled",
    " (저장소 {want})": " (repository {want})",
    "uv run tools/claude_hand.py update 뒤 Blender를 다시 여세요": "uv run tools/claude_hand.py update, then open Blender again",
    "열린 Blender": "open Blender",
    "응답하는 Blender가 없어 건너뜀": "skipped: no Blender answers",
    "채팅 확인 중...": "Checking the chat...",
    "감독 세션": "supervisor session",
    "Claude Hand가 꺼져 있어 채널 확인은 건너뜀 (켠 뒤 다시 돌리면 확인)":
        "skipped: Claude Hand is off, so the channel is not checked (turn it on and run again to check)",
    "채널 서버": "channel server",
    "하위 세션의 안전장치 훅 확인 중...": "Checking the subagents' guard hooks...",
    "하위 세션 정의·감독 스킬·Blender 애드온 파일": "subagent definitions, supervisor skill and Blender add-on files",
    "저장소 버전과 같음": "the same as the repository's",
    # tools/claude_hand.py: settings
    "바로 적용": "applies at once",
    "다음에 Claude Hand를 켤 때부터": "from the next start of Claude Hand",
    "{days}일": "{days} days",
    "지우지 않음": "never removed",
    "{since}부터 · {when}": "since {since} · {when}",
    "설정 목록을 읽지 못했습니다: {path}": "The list of settings could not be read: {path}",
    "설정  ({path})": "Settings  ({path})",
    "실험실  (아직 다듬는 중인 기능입니다. 켜야 쓰입니다)": "Lab  (features still being refined: in use only once turned on)",
    "  지금 실험실에 있는 기능이 없습니다.": "  The lab holds no feature right now.",
    "\n바꾸기: uv run tools/claude_hand.py settings <이름> <값>      실험실: settings lab <이름> on|off":
        "\nTo change: uv run tools/claude_hand.py settings <name> <value>      the lab: settings lab <name> on|off",
    "'{name}'(이)라는 설정이 없습니다. `settings`만 치면 목록이 나옵니다.": "There is no setting '{name}'. `settings` alone lists them.",
    "'{name}'은(는) 실험실을 나와 이제 기본 기능입니다. 켜고 끄지 않습니다.":
        "'{name}' has left the lab and is a regular feature now. It is not turned on or off.",
    "'{word}'은(는) 이 설정의 값이 아닙니다. 쓸 수 있는 값: {values}": "'{word}' is not a value of this setting. It takes: {values}",
    "다음에 Claude Hand를 켤 때부터 적용됩니다.": "It applies from the next start of Claude Hand.",
    "지금 켜져 있는 Claude Hand에는 적용되지 않습니다. 닫고 다시 켜면 적용됩니다.":
        "It does not apply to the Claude Hand that is on now. Close it and start it again for it to apply.",
    "하위 세션 정의와 감독 스킬을 이 언어로 다시 썼습니다 ({count}개).":
        "The subagent definitions and the supervisor skill were written again in this language ({count}).",
    "하위 세션 정의를 다시 쓰지 못했습니다: {exc}": "The subagent definitions could not be written again: {exc}",
    "설정을 적지 못했습니다: {exc}": "The settings could not be written: {exc}",
    # skills/orchestrator/SKILL.md: its description, which the list of skills shows (handlib/agents.py, rendered_skill)
    "Unity 프로젝트나 Blender 창 여러 개를 동시에 다룰 때 쓰는 감독 규칙. 사용자가 둘 이상의 Unity 프로젝트나 Blender 파일을 언급하거나, 오브젝트·프리팹·모델을 한 곳에서 다른 "
    "곳(Unity↔Unity, Blender↔Blender, Blender↔Unity)으로 옮기라고 하거나, unity-*/blender-* 하위 세션에게 일을 나눠 맡겨야 할 때, 또는 \"Claude Hand 켜줘\", "
    "\"Claude Hand 시작해줘\"라고 할 때 사용한다.":
        "Supervisor rules for working with several Unity projects or Blender windows at once. Use when the user mentions two or more "
        "Unity projects or Blender files, asks to move objects, prefabs or models from one place to another (Unity↔Unity, "
        "Blender↔Blender, Blender↔Unity), when work has to be handed out to the unity-*/blender-* subagents, or when they say "
        "\"Start Claude Hand\" or \"Turn Claude Hand on\".",
}

# What Korean instructions are given at their end while the interface is English (handlib/agents.py, glossary): the
# names and texts they use in Korean, and what the user's screen and the tools' results show instead. The English of
# each pair is taken from the tables (this one, the guard's, the Unity package's, the Blender add-on's); the test
# holds the pieces of each to them, so a text that is reworded there fails here until this follows.
# What a text that counts something says when the count is one: (the name that holds the count, the text). Korean has
# no plural, so one Korean text stands for both; without these English read "1 actions" and "1 days".
EN_ONE: dict[str, tuple[str, str]] = {
    " · 동작 {actions}번": ("actions", " · {actions} action"),
    "동작 {actions}번": ("actions", "{actions} action"),
    "{who} {duration} API {calls}번 {tokens}": ("calls", "{who} {duration} API {calls} call {tokens}"),
    '{when}  {chat_id:<24} {duration} · 동작 {actions}번 · {tokens}  "{asked}"': ("actions", '{when}  {chat_id:<24} {duration} · {actions} action · {tokens}  "{asked}"'),
    "\n합계: 요청 {count}개 · 기다린 시간 {duration} · {tokens}": ("count", "\nTotal: {count} request · waited {duration} · {tokens}"),
    "  {who:<24} API {calls}번 · {tokens}": ("calls", "  {who:<24} API {calls} call · {tokens}"),
    "  {day}  요청 {requests}개 · {duration} · {tokens}": ("requests", "  {day}  {requests} request · {duration} · {tokens}"),
    "\n빠진 항목 {missing}개": ("missing", "\n{missing} item missing"),
    "\n문제 {failed}개": ("failed", "\n{failed} problem"),
    "응답 {answers}개": ("answers", "{answers} answer"),
    ", 요청 번호로 갈린 요청 {numbered}개": ("numbered", ", {numbered} request told apart by request id"),
    "점검 통과: Python {python}, 포트 {port} 사용 가능, 하위 세션 {count}개": ("count", "check passed: Python {python}, port {port} free, {count} subagent"),
    "교환 폴더의 파일을 {keep_days}일 동안 둡니다.": ("keep_days", "Files in the exchange folder are kept for {keep_days} day."),
    "{days}일": ("days", "{days} day"),
}

GLOSSARY: list[tuple[str, str]] = [
    ("[되돌리기]", "[Undo]"),
    ("[중지]", "[Stop]"),
    ("[추가] · [취소]", "[Add] · [Cancel]"),
    ("[풀기]", "[Release]"),
    ("허용 · 거부", "Allow · Deny"),
    ("\"보호\" 칸", "the \"Protect\" section"),
    ("보기만 · 잠금", "Look only · Locked"),
    ("\"이번만\"", "\"This run only\""),
    ("\"Unity로 보낸 모델\" 칸", "the \"Models sent to Unity\" section"),
    ("[선택한 오브젝트 보내기]", "[Send the selected objects]"),
    ("\"데스크톱 앱에서\"", "\"From the desktop app\""),
    ("\"사용자가 채팅창에서 [중지]를 눌러 이 호출을 막았습니다\"", "\"The user pressed [Stop] in the chat panel, so this call was refused\""),
    ("\"보호 규칙이 이 호출을 막았습니다\"", "\"A protection rule refused this call\""),
    ("\"읽는 호출과 바꾸는 호출을 나눠\"", "\"split it into a call that reads and a call that changes\""),
    ("\"보호 규칙이 막았어요\"", "\"A protection rule stopped it\""),
    ("\"보호된 …이(가) 바뀌어 이 요청이 이 창에 한 일을 되돌렸어요\"", "\"Protected … was changed, so what this request did in this window was undone\""),
    ("\"대화상자 때문에 멈춰 있어 이 호출을 막았습니다\"", "\"is stopped at a dialog, so this call was refused\""),
    ("\"이 창은 지금 다른 하위 세션이 작업 중\"", "\"Another subagent is at work in this window\""),
    ("\"요청이 아직 답을 받지 못했습니다\"", "\"This chat panel's request has had no answer yet\""),
    ("\"사용자가 이 채팅창에서 [중지]를 눌렀습니다\"", "\"The user pressed [Stop] in this chat panel\""),
    ("`[unity 창 정보: …]`, `[blender 창 정보: …]`", "`[unity window info: …]`, `[blender window info: …]`"),
    ("`[사용자가 ... 되돌렸음]`", "`[The user undid …]`"),
    ("`이 요청: 32초 · 동작 2번 · 토큰 …`", "the line that begins `This request` (32s · 2 actions · tokens …)"),
]
