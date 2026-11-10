# claude-hand

Unity 에디터와 Blender 창을 여러 개 동시에 띄워 두고, **창마다 전담 Claude 하위 세션**을 붙여서 일을 나눠 맡기는 도구 모음입니다.
한 곳의 오브젝트를 다른 곳으로 옮길 수도 있습니다: Unity → Unity, Blender → Blender, Blender → Unity, Unity → Blender.
기존 MCP를 대체하지 않고, 그 위에 덧붙입니다.
- Unity: [MCP for Unity (CoplayDev)](https://github.com/CoplayDev/unity-mcp)
- Blender: [MCP for Blender (ahujasid/blender-mcp)](https://github.com/ahujasid/blender-mcp)
- 처음 세팅과 대화상자 처리: [agent-desktop (vercel-labs)](https://github.com/vercel-labs/agent-desktop)

```
Unity·Blender 안의 채팅창 ──(claude-hand-chat 채널)──┐
                                                    ▼
감독 세션 (Claude Code, 사용자와 대화)
 ├─ 하위 세션 unity-mygame      ── 전용 MCP 서버 (MyGame에 고정)  ── Unity 창: MyGame
 ├─ 하위 세션 unity-props       ── 전용 MCP 서버 (Props에 고정)   ── Unity 창: Props
 ├─ 하위 세션 blender-character ── 전용 MCP 서버 (포트 9876 고정) ── Blender 창: character.blend
 └─ 교환 폴더 %USERPROFILE%\.claude-hand\exchange  (.unitypackage / .blend / .fbx + manifest, 30일 지나면 켤 때 정리)
```

> 상태: **0.9.0.** Windows의 실제 Unity 6.3과 Blender 5.2에서 써 가며 확인했습니다([12단계](docs/TEST-RESULTS-12-WINDOWS.md), [13~21단계](docs/TEST-RESULTS-13-WINDOWS.md)). macOS·Linux에서는 자동 테스트만 돌렸습니다.
> 2026-11-10의 구조 개편([점검](docs/REVIEW-2026-11-10.md))과 그 뒤에 더한 기능(다시 보낸 모델을 제자리에서 바꾸기, Unity → Blender, 요청 기록, 중지 버튼)은 자동 테스트와, 창 없이 연 Unity·Blender에서 실제로 돌려 확인했습니다. 열린 창과 감독 세션이 있어야 볼 수 있는 것은 [TEST-PLAN](docs/TEST-PLAN.md) 22·23단계에 있습니다.

## 매일 쓰는 법

1. Unity(**Window > Claude Hand > Chat**)나 Blender(3D 뷰포트 `N` > **Claude Hand**)의 채팅창에 바로 말합니다.
   - Claude Hand가 꺼져 있으면 채팅창이 켜고, 연결되면 그 메시지를 보냅니다.
   - 바탕화면의 **Claude Hand** 아이콘이나 채팅창의 **[Claude Hand 시작]** 버튼으로 먼저 켜도 됩니다.
2. 검은 Claude Hand 창이 뜹니다. **켤 때마다** 개발용 채널 경고가 나오니 "I am using this for local development"를 고르고 Enter를 누릅니다. 직접 만든 채널에 Claude Code가 늘 묻는 보안 확인이라 claude-hand가 대신 답하지 않고, 개인 계정에서는 끌 수도 없습니다.
3. 세션이 일을 시작하면 그 창은 저절로 최소화됩니다(Windows). **닫지는 마세요.** 닫으면 채팅이 멈춥니다.

이게 전부입니다.
- 새 프로젝트나 파일을 열어도 따로 할 일이 없습니다. 처음 메시지를 보내면 그 창 담당이 자동으로 만들어집니다.
- 아이콘을 두 번 눌러도 "이미 실행 중"이라고 알려 줄 뿐 둘이 열리지 않습니다.

## Claude 데스크톱 앱에서 보고 말하기

Claude Hand는 기본으로 Claude Code의 **Remote Control**로 열립니다. 쓰는 방식은 하나입니다. **검은 Claude Hand 창이 일을 하고, Unity·Blender 채팅창과 데스크톱 앱은 같은 세션에 말하는 입구**입니다.

- **Claude 데스크톱 앱의 Code 탭**(그리고 claude.ai/code, 모바일 앱) 세션 목록에 **"Claude Hand"**가 보입니다. 거기서 보고 말할 수 있습니다.
- 백그라운드로 도는 하위 세션도 보이고, 거기서 멈출 수 있습니다.
- "선택한 거 Blender로 보내줘"처럼 말해도 됩니다. Unity·Blender가 지금 무엇이 선택돼 있는지 Claude Hand에 알려 주고 있어서, 감독이 대상을 찾습니다(`current_context`).
- 데스크톱 앱에서 시킨 일도 그 창의 채팅창에 **"데스크톱 앱에서"** 표시와 함께 남습니다. 창마다 작업 기록이 채팅창에 다 모입니다.
- Claude Hand가 꺼져 있을 때는 데스크톱 앱의 아무 Code 탭 세션에서 **"Claude Hand 켜줘"**라고 하면 됩니다. 검은 창이 뜨면 확인 질문에 답하고, 세션 목록에서 "Claude Hand"로 옮겨 가세요.

필요한 것:
- Pro·Max·Ultra·Team·Enterprise 구독의 **claude.ai 로그인**. API 키로는 안 됩니다. 로그인 방식은 Claude Hand 창에서 `/status`로 볼 수 있습니다.
- Team·Enterprise는 관리자가 Remote Control을 켜야 합니다.
- 조건이 안 맞으면(API 키, Bedrock 등) Claude Hand는 Remote Control을 빼고 켜고 그 이유를 창에 보여 줍니다. 채팅창은 그대로 됩니다.

설정:
```powershell
uv run tools/claude_hand.py remote-control off   # 데스크톱 앱에서 보기 끄기 (on으로 다시 켜기)
uv run tools/claude_hand.py minimize off         # Claude Hand 창 자동 최소화 끄기
uv run tools/claude_hand.py clean-exchange --keep-days 30   # 교환 폴더의 옮긴 파일을 며칠 둘지 (기본 30일, 0이면 지우지 않음)
```

알아둘 점:
- 데스크톱 앱에서 새로 연 Code 탭 세션은 Claude Hand가 아닙니다. Unity·Blender 채팅창과 연결되지 않고, 채팅창에 기록도 남지 않습니다. 작업은 "Claude Hand" 세션에서 하세요.
- 일반 **채팅** 탭에서는 이 스킬이 동작하지 않습니다. **Code** 탭에서만 동작합니다.

## 처음 설치: Claude에게 맡기기

필요한 건 **Claude Code**와 이 저장소뿐입니다.

1. 저장소의 `main` 브랜치를 받습니다. `git`이 없으면 GitHub 페이지에서 `main` 브랜치를 고른 뒤 ZIP으로 받아 풀어도 됩니다.
   ```powershell
   git clone -b main https://github.com/jinsu-lee-code/claude-hand.git
   cd claude-hand
   ```
2. 그 폴더에서 Claude Code를 엽니다(`claude`). 데스크톱 앱이면 이 폴더를 엽니다.
3. **"claude-hand 세팅해줘"**라고 말합니다.

그러면 `claude-hand-setup` 스킬이 다음 순서로 진행합니다.
1. **진단:** 무엇이 설치돼 있고 무엇이 빠졌는지 표로 보여줍니다(`doctor`).
2. **계획과 허락:** 설치하거나 바꿀 것을 보여주고, 할 단계를 고르게 합니다. 고른 단계만 진행합니다.
3. **설치와 세팅:** 아래 순서로 진행합니다.
   - 빠진 기본 도구(uv, git, Node.js)를 winget으로 설치
   - **agent-desktop** 설치
   - **MCP for Blender** 설치: 애드온 설치와 활성화, Claude 설정까지
   - Unity 프로젝트에 패키지 추가
   - Unity 절전 설정
   - 하위 세션 생성
   - 원하면 Unity·Blender 채팅창 연결
   - 명령어로 안 되는 화면 조작만 agent-desktop이 직접 합니다.
4. **확인:** 마지막에 다시 진단해서 남은 항목을 알려줍니다.

Claude가 대신 하지 않고 **사용자에게 직접 부탁하는 것**도 있습니다.
- 관리자 권한(UAC) 창, Windows 방화벽·보안 창
- 로그인, 라이선스 동의
- 비밀번호, API 키 입력
- 저장 안 된 Blender·Unity 창 닫기
- Claude Code 재시작 (새 MCP 서버를 등록한 뒤 한 번 필요합니다)

## 창마다 채팅하기

Unity와 Blender 안에서 바로 채팅할 수 있습니다. **그 창에서 친 메시지는 그 창 담당 하위 세션에게 가고, 답도 그 창으로 옵니다.**
- Unity: **Window > Claude Hand > Chat**
- Blender: 3D 뷰포트 `N` 패널 > **Claude Hand** 탭

```
Unity 채팅창 ─┐            ┌─ unity-mygame (이 창의 대화를 계속 이어감)
Blender 패널 ─┼─ 채널 ─ 감독 ┼─ blender-props
             │  (토큰)     └─ 다른 창이 필요하면 감독이 함께 조율
             └── 답장·권한 요청(허용/거부)이 같은 창으로 돌아옴
```

- 메시지에는 지금 선택한 오브젝트와 씬 정보가 함께 붙습니다. 그래서 "이거 복제해줘"처럼 말해도 됩니다. 끄는 체크박스가 있습니다.
- 다른 창이 필요한 일도 그 창에서 말하면 됩니다. 예를 들어 Unity 창에서 "Blender props.blend의 Rock 가져와"라고 하면, 감독이 Blender 담당과 Unity 담당을 함께 써서 처리하고 결과를 그 Unity 창에 알려줍니다.
- Claude가 도구를 실행하려고 허락을 구하면 채팅창에 **허용/거부** 버튼이 뜹니다.
- 일이 끝났는데 답이 오지 않으면 채널이 1분 뒤 Claude Hand에 한 번 다시 알리고, 채팅창에 그렇게 했다고 보여 줍니다.
- 한 창에서 시킨 일이 다른 창을 바꾸면(Blender 창에서 "Unity로 보내줘"), 답과 함께 바뀐 창의 **지금 모습**이 그림으로 붙습니다. Claude를 거치지 않고 채널이 직접 찍어서 토큰이 들지 않습니다.
- 다른 프로그램을 보는 동안 답이나 허락 요청이 오면 Unity·Blender의 작업 표시줄 단추가 깜빡이고, Unity 채팅 탭 이름에 안 읽은 수가 붙습니다.
- Unity가 "씬을 저장할까요?" 같은 대화상자에서 멈추면(Windows) 시간 초과를 기다리지 않고 바로 알아채, 대화상자의 글과 버튼을 감독과 채팅창에 알립니다. 무엇을 누를지는 사용자가 정합니다.
- 일이 끝나면 입력칸 위에 **되돌리기** 버튼이 뜹니다. 그 요청이 시작되기 직전으로 Unity Undo나 Blender 실행 취소 기록을 되돌립니다. "방금 거 취소해줘"라고 해도 됩니다.
- 일하는 동안에는 "작업 중" 줄 옆에 **중지** 버튼이 있습니다. 누르면 그 요청으로 일하던 하위 세션의 다음 동작부터 막히고(하던 동작 하나는 끝까지 갑니다), 감독이 어디까지 했는지 알려 줍니다. 한 것은 되돌리지 않습니다. 되돌리려면 그다음에 **되돌리기**를 누릅니다.

준비(세팅 스킬이 대신 해 줍니다):
```powershell
uv run tools/claude_hand.py chat-setup            # 채널 등록 + 답장 도구 허용 + 채널 서버 미리 실행·점검
uv run tools/claude_hand.py blender-install-chat  # Blender 채팅 애드온 설치·활성화
uv run tools/claude_hand.py sync --allow-tools    # (선택) 하위 세션의 Unity·Blender 도구를 묻지 않고 실행
```
그다음 감독 세션을 엽니다. 바탕화면 아이콘, 채팅창의 **[Claude Hand 시작]** 버튼, `start-supervisor.cmd` 중 어느 것이든 됩니다. 아이콘은 `uv run tools/claude_hand.py shortcut`으로 만들고, 컴퓨터를 켤 때 자동으로 켜려면 `--startup`을 붙입니다.

`chat-setup`이 하는 일:
- 이 폴더에 `.mcp.json`(채널 등록)과 `.claude/settings.local.json`(답장·현재 상황 도구 `mcp__claude-hand-chat__reply`, `mcp__claude-hand-chat__current_context` 자동 허용, 감독이 Unity·Blender를 직접 조작하는 도구 막기)을 만듭니다. 기존 내용은 유지하고, 둘 다 git에 올라가지 않습니다.
- 채널 서버를 한 번 미리 실행해서 점검합니다(`--check`). Claude Code는 MCP 서버가 30초 안에 떠야 하는데, 처음에는 uv가 Python을 내려받느라 늦을 수 있어서 미리 받아 둡니다. `start-supervisor`는 여유를 두려고 `MCP_TIMEOUT=60000`도 설정합니다.

알아둘 점:
- Claude Code의 **Channels는 아직 리서치 프리뷰**입니다. 직접 만든 채널이라 `--dangerously-load-development-channels`로 켜야 하고, 처음에 개발용 채널 경고와 `.mcp.json` 서버 승인 창이 뜹니다.
- 채팅창의 **허용/거부 버튼은 Claude Code 2.1.234 이상**에서 동작합니다. Channels는 claude.ai 로그인이나 Console API 키가 필요하고, Bedrock·Vertex·Foundry에서는 쓸 수 없습니다. 회사 계정(Team/Enterprise)은 관리자가 Channels를 켜야 합니다. `doctor`가 버전과 환경을 확인합니다.
- **채팅을 받는 건 `start-supervisor`로 연 세션뿐입니다.** 이 폴더에서 그냥 연 Claude Code도 채널 서버를 띄우지만 채팅은 받지 않습니다. 채널 없는 세션이 채팅을 가로채지 않게 하기 위해서입니다. 감독 세션은 하나만 여세요.
- 채팅창과 채널은 내 PC 안(127.0.0.1)에서만, 토큰을 아는 프로그램끼리만 통신합니다. 토큰은 `%USERPROFILE%\.claude-hand\chat\server.json`에 있습니다. 방화벽 규칙은 필요 없고, 방화벽 창이 떠도 허용하지 않아도 됩니다.

### 채팅창에 "감독 세션" 관련 메시지가 뜰 때

```powershell
uv run tools/claude_hand.py chat-status
```
지금 상태와 고치는 방법을 알려줍니다. 흔한 원인은 이렇습니다.
- 감독 세션을 `start-supervisor.cmd`가 아닌 방법(데스크톱 앱, 그냥 `claude`)으로 열었다. 채널 서버가 대기 모드로 뜨고, 채팅창에 그 이유가 보입니다.
- 이전 버전의 등록이 남아 있다 → 아래 **새 버전 받기**를 하세요.
- `.mcp.json` 서버 승인 대기나 거절 → `/mcp`에서 승인하세요. 거절했었다면 `claude mcp reset-project-choices`를 하세요.

채널 서버는 다음 경우에만 채팅을 받습니다.
- `start-supervisor`가 넘긴 표시(`CLAUDE_HAND_SUPERVISOR=1`, `.mcp.json`의 `env`로 전달)가 있을 때
- 자기를 띄운 Claude Code가 이 서버를 채널로 불러왔을 때(`--dangerously-load-development-channels server:claude-hand-chat`)

### 한 요청에 무슨 일이 있었는지 보기

```powershell
uv run tools/claude_hand.py trace                   # 최근 이틀의 채팅창 요청 목록
uv run tools/claude_hand.py trace unity-mygame      # 그 창의 마지막 요청을 한 줄씩
uv run tools/claude_hand.py trace --request <번호>  # 목록에 나온 번호의 요청
```
채팅창에서 보낸 요청마다 번호가 붙고, 그 뒤의 일이 `%USERPROFILE%\.claude-hand\logs\<날짜>.jsonl`에 한 줄씩 남습니다: 하위 세션의 도구 호출, 안전장치가 막은 호출과 이유, `ch_*` 호출의 성공 여부와 걸린 시간, 하위 세션이 멈춘 때, 답. "왜 답이 늦었지", "어디서 막혔지"를 뒤에서 볼 때 씁니다. 메시지와 답은 앞 200자만 남고, 2주가 지난 파일은 켤 때 지워집니다. 데스크톱 앱에서 시킨 일은 요청 번호 없이 남아 `trace` 목록에는 나오지 않습니다.

## 새 버전 받기 (이미 설치한 경우)

```powershell
git pull
uv run tools/claude_hand.py update     # 하위 세션·감독 스킬·채팅 설정·Blender 애드온을 새 버전으로 (--dry-run으로 미리 보기)
```
- 그다음 감독 세션을 `start-supervisor.cmd`로 다시 엽니다.
- 세팅 스킬로 할 때는 **`git pull` 뒤에 Claude Code를 다시 시작한 다음** "세팅 이어서 해줘"라고 하세요. 스킬 내용은 세션을 시작할 때 읽혀서, 받기 전에 연 세션은 옛 순서를 따릅니다.
- `update`는 이전 버전이 남긴 등록도 정리합니다. 이전 `chat-setup`이 local 범위로 등록한 채널은 새 `.mcp.json`보다 먼저 쓰여서 채팅을 막습니다.

## 들어 있는 것

| 위치 | 내용 |
|---|---|
| `.claude/skills/claude-hand-setup/` | 처음 세팅 스킬입니다. 이 폴더에서 Claude Code를 열면 바로 쓸 수 있습니다. |
| `skills/orchestrator/` | 평소에 감독 세션이 따르는 규칙(스킬)입니다. `sync`가 `~/.claude/skills/claude-hand/`에 설치합니다. |
| `tools/claude_hand.py` | 하위 세션 생성(`list`, `sync`), 세팅 도우미(`doctor`, `selftest`, `update`, `mode`, `unity-install`, `unity-open`, `blender-open`, `chat-setup`, `blender-install-chat`, `chat-status`, `shortcut`, `remote-control`), 요청 기록 보기(`trace`), 교환 폴더 정리(`clean-exchange`)가 들어 있습니다. |
| `tools/guard.py` | 하위 세션의 안전장치(PreToolUse 훅)입니다. 다른 대상으로 가는 호출을 막습니다. 표준 라이브러리만 쓰는 작은 파일로 따로 두어, 다른 파일의 실수가 안전장치를 멈추지 못하게 했습니다. |
| `tools/handlib/` | 위 스크립트들을 이루는 모듈입니다: 열린 창 찾기(`discovery`), 하위 세션 정의 만들기(`agents`), 채팅 설정(`chatsetup`), 시작 준비(`launch`), 진단(`checks`), 요청 기록(`eventlog`) 등. 목록은 `tools/handlib/__init__.py`에 있습니다. |
| `prompts/` | Claude에게 주는 지침의 원문입니다. 하위 세션용(`unity-subagent.md`, `blender-subagent.md`)에는 `{{이름}}` 자리에 창마다의 값이 들어갑니다. `chat-rules.md`는 감독이 채팅창을 다루는 규칙으로, 채널 서버와 감독 스킬이 같은 글을 씁니다. 고친 뒤에는 `update`로 다시 설치하고 Claude Hand를 다시 켭니다. |
| `tools/chat_channel.py` | 채팅 채널 서버입니다. Claude Code가 실행하는 MCP 서버이고, Unity·Blender 채팅창과는 로컬 HTTP로 통신합니다. |
| `unity/com.claudehand.tools/` | Unity 패키지입니다. `ch_*` 도구 9개와 채팅창(Window > Claude Hand > Chat)을 추가합니다. `ch_*` 도구는 이 패키지가 여는 자기 통로로 불립니다. MCP for Unity 없이도 컴파일되고, 그 버전에 상관없이 동작합니다. |
| `tools/unity_bridge.py` | 하위 세션이 그 통로로 `ch_*` 도구를 부를 때 쓰는 MCP 서버입니다. `--list`로 통로를 연 Unity를 볼 수 있습니다. |
| `blender/claude_hand_chat.py` | Blender 채팅 패널 애드온입니다. |
| `start-supervisor.cmd` / `.sh` | 감독 세션을 엽니다. 바탕화면 아이콘과 채팅창 버튼이 이 파일을 실행합니다. 열 때마다 `preflight`가 하위 세션과 설정을 맞추고, 이미 켜져 있으면 알려 줍니다. |
| `docs/` | [설계](docs/DESIGN.md), [PC 검증 순서](docs/TEST-PLAN.md) |

### 추가되는 Unity 도구

| 도구 | 하는 일 |
|---|---|
| `ch_editor_status` | 연결된 프로젝트, 포커스 여부, 컴파일 중인지를 알려줍니다. 아무것도 바꾸지 않습니다. |
| `ch_scene_snapshot` | 씬 구조를 JSON으로 돌려주고, 오브젝트마다 `@s1a2b3c4:e5` 형태의 ref를 붙입니다. 씬이 바뀐 뒤 옛 ref를 쓰면 `STALE_REF`가 납니다. |
| `ch_export_objects` | 씬 오브젝트(ref)나 에셋을 의존 에셋과 함께 `.unitypackage`로 묶어 교환 폴더에 둡니다. 원본 씬은 바뀌지 않습니다. |
| `ch_import_package` | 위 패키지를 가져와 씬에 배치합니다. `dry_run=true`로 덮어쓸 에셋을 먼저 보여주고, 덮어쓸 게 있으면 허락 없이는 진행하지 않습니다. |
| `ch_import_model` | Blender가 내보낸 FBX(또는 OBJ, GLB)를 `Assets/ClaudeHand/Imports/<이름>/` 아래로 가져와 씬에 배치합니다. **같은 .blend의 같은 오브젝트가 다시 오면 새로 넣지 않고 있던 모델 파일을 제자리에서 바꿉니다.** 씬과 프리팹에 놓인 것이 그 자리에서 새 모양이 됩니다. 바꾸기 전 파일은 `Library/ClaudeHand/replaced/`에 남습니다. `dry_run=true`로 어느 쪽이 될지 먼저 볼 수 있고, `new_copy=true`면 따로 한 벌을 넣습니다. `materials="project"`면 프로젝트에 같은 이름의 머티리얼이 하나 있을 때 그것으로 연결합니다. |
| `ch_export_model` | 씬 오브젝트(ref)나 모델 에셋을 Blender(또는 다른 Unity 프로젝트)가 받을 모델 파일로 교환 폴더에 둡니다. 모델 파일(FBX, OBJ, GLB)에서 온 오브젝트는 그 파일을 가져올 때 모습 그대로 보냅니다. Unity에서 만든 메시는 프로젝트에 FBX Exporter 패키지(`com.unity.formats.fbx`)가 있을 때 FBX로 씁니다. 프로젝트는 바뀌지 않습니다. |
| `ch_apply_settings` | Interaction Mode를 **No Throttling**으로 바꿉니다. Unity가 뒤에 있어도 명령을 처리하게 하는 설정입니다. 보기(`show`), 적용(`apply`), 되돌리기(`revert`)를 할 수 있습니다. 메뉴 **Tools > Claude Hand**에도 있습니다. |
| `ch_checkpoint` | 요청마다 되돌리기 지점을 둡니다. 하위 세션이 그 요청의 첫 동작을 하기 직전에 저절로 찍히고, 채팅창의 **되돌리기** 버튼이나 `action=undo`로 그 지점 이후의 변경을 Unity Undo로 되돌립니다. Undo에 남는 변경만 되돌아갑니다. |
| `ch_capture` | 씬 뷰를 지금 모습 그대로 PNG로 저장합니다(기즈모 없이). 씬은 바꾸지 않습니다. 채팅창의 결과 미리보기에 씁니다. |

명령줄에서도 부를 수 있습니다. 예: `uv run tools/claude_hand.py unity-call MyGame ch_editor_status`

모든 오류에는 `retry` 값이 붙습니다.
- `safe`: 그대로 다시 호출해도 됩니다.
- `after_fix`: 인자를 고친 뒤 다시 호출합니다.
- `check_first`: 이미 반영됐을 수 있으니 상태부터 확인합니다.

### Blender 쪽

Blender 쪽 일은 MCP for Blender의 Python 실행 도구(`execute_code`)로 합니다. 옮기기와 되돌리기는 Claude Hand Chat 애드온(`blender-install-chat`으로 설치)의 함수이고, 하위 세션은 그것을 한 줄로 부릅니다.
- `export_blend`: 다른 Blender로 보낼 `.blend`를 씁니다
- `import_blend`: 다른 Blender가 보낸 것을 Append로 가져옵니다
- `export_fbx`: Unity로 보낼 FBX를 씁니다. 저장된 .blend의 같은 오브젝트를 다시 보내면 Unity가 있던 모델을 제자리에서 바꿉니다
- `import_model`: Unity가 `ch_export_model`로 보낸 모델(FBX, OBJ, GLB)을 가져옵니다
- `mark_undo_point`, `undo_last_request`: 요청 단위 되돌리기

### agent-desktop은 어디에 쓰나

- **처음 세팅:** 명령어로 할 수 없는 클릭을 대신합니다. 예를 들어 Unity가 패키지를 받는 동안 뜨는 대화상자, 이미 열린 Blender의 포트 변경입니다.
- **평소:** Unity나 Blender가 대화상자 때문에 멈추면 그 내용을 읽어 사용자에게 보여주고, 고른 버튼을 눌러 줍니다.
- **한계:**
  - **최소화된 창은 조작하지 못합니다.** 먼저 복원해야 합니다. 다른 창 뒤에 가려진 창은 백그라운드로 조작할 수 있습니다.
  - Unity·Blender의 내부 패널은 접근성 정보가 거의 없어서 읽히지 않을 수 있습니다. 그때는 허락을 받은 뒤 화면을 캡처하고 좌표로 클릭합니다. 이 동안에는 마우스와 키보드가 실제로 움직입니다.
  - 그래서 창이 최소화된 상태에서의 실제 작업(씬 수정, 모델 옮기기)은 agent-desktop이 아니라 MCP가 맡습니다.

## 직접 설치하기 (Claude 없이)

1. 기본 도구: uv, git, Node.js 18 이상(`winget install --id astral-sh.uv -e` / `Git.Git` / `OpenJS.NodeJS.LTS`)
2. agent-desktop: `npm install -g agent-desktop`
3. MCP for Blender: `uvx mcp-for-blender setup --dry-run`으로 확인한 뒤 `uvx mcp-for-blender setup --yes`
4. Unity 프로젝트마다 다음을 실행합니다. MCP for Unity와 Claude Hand Tools가 `Packages/manifest.json`에 들어가고, 원래 파일은 `.bak`으로 남습니다.
   ```powershell
   uv run tools/claude_hand.py unity-install "C:\경로\프로젝트"
   ```
   MCP for Unity는 시험한 버전(v10.3.0)으로 들어갑니다. 이미 `#main`으로 넣어 둔 프로젝트는 그대로 두고 알려 주며, 옮기려면 `--pin-mcp-for-unity`를 붙입니다.
5. MCP for Unity를 **Stdio** 방식으로 씁니다. 패키지를 넣고 처음 뜨는 MCP for Unity 설정 창은 HTTP로 등록하니, Unity의 **Window > MCP for Unity**에서 Transport를 **Stdio**로 바꾸고 Claude Code의 **Configure**를 누릅니다. 명령으로 등록해도 됩니다(아직 없을 때만). 그다음 Claude Code를 다시 시작합니다.
   ```powershell
   claude mcp add --scope user unityMCP -- uvx --from mcpforunityserver mcp-for-unity --transport stdio
   ```
6. Unity에서 메뉴 **Tools > Claude Hand > Apply Recommended Settings**를 누릅니다(Interaction Mode = No Throttling).
7. Blender 창을 여러 개 쓸 거면 창마다 포트를 다르게 해서 엽니다.
   ```powershell
   uv run tools/claude_hand.py blender-open "C:\Art\props.blend" --port 9877
   ```
   또는 3D 뷰포트에서 `N` → MCP for Blender 탭에서 포트를 바꾸고 서버를 켭니다. 포트는 .blend에 저장됩니다.
8. 작업할 창을 모두 연 상태에서 하위 세션을 만듭니다.
   ```powershell
   uv run tools/claude_hand.py sync
   ```
   Claude Code에서 `/agents`를 입력해 `unity-*`, `blender-*`가 보이면 끝입니다.

`uv run tools/claude_hand.py doctor`를 실행하면 언제든 빠진 항목과 고치는 명령을 볼 수 있습니다.
설치가 아니라 **실제로 동작하는지**는 `uv run tools/claude_hand.py selftest`로 봅니다. 테스트(Python, Blender 안 애드온)를 돌리고, 열린 Unity·Blender와 채팅 채널이 이 버전으로 응답하는지 확인합니다(1분쯤, 아무것도 바꾸지 않음). 테스트를 빼려면 `--no-tests`.
모델을 옮기는 Unity 도구까지 실제로 돌려 보려면 **닫혀 있는** 프로젝트를 알려 줍니다.
```powershell
uv run tools/claude_hand.py selftest --unity-project "C:\경로\프로젝트"
```
Unity를 창 없이 열어, Blender가 실제로 내보낸 모델을 받고, 같은 모델이 다시 왔을 때 제자리에서 바꾸고, 프로젝트 머티리얼에 연결하고, 다시 Blender로 보내는 것까지 확인합니다(2~3분). 저장하지 않는 새 씬에서 하고, 만든 에셋은 끝나면 지웁니다. 바꾸기 전 파일 사본이 `Library/ClaudeHand/replaced/`에 하나 남습니다.

`sync`에 대해 알아둘 것:
- 지금 쓰는 MCP 설정(`.mcp.json`, `~/.claude.json`, Claude Desktop 설정)을 찾아서 같은 방식으로 서버를 띄웁니다. Unity는 stdio와 HTTP 둘 다 됩니다.
- Blender는 포트 9876~9885를 확인하고, 응답한 창의 .blend 경로를 읽어(읽기만 합니다) 이름을 붙입니다. 다른 범위를 쓰려면 `--blender-ports 9900-9910`을 붙입니다.
- 창을 새로 열거나 닫았으면 다시 `sync`하세요. 닫은 창의 정의 파일까지 지우려면 `--prune`을 붙입니다.
- 저장소 폴더를 옮겼으면 다시 `sync`해야 합니다. 안전장치 경로가 정의 파일에 들어가 있어서요.

## 이렇게 씁니다

감독 세션(평소 대화하는 Claude Code)에 그냥 말하면 됩니다.

- "MyGame이랑 Props 씬 구조 동시에 봐줘"
  - 두 하위 세션이 각자 자기 프로젝트를 스냅샷합니다.
- "Props에 있는 Rock_A 프리팹을 MyGame 씬의 Environment 아래에 넣어줘"
  1. unity-props가 내보냅니다.
  2. unity-mygame이 dry run으로 확인합니다. 덮어쓸 게 있으면 감독이 먼저 물어봅니다.
  3. unity-mygame이 가져와서 배치합니다.
  4. 씬은 저장하지 않은 상태로 보고합니다.
- "character.blend의 Hero를 MyGame이랑 Props 씬에 둘 다 넣어줘"
  1. blender-character가 FBX로 한 번 내보냅니다.
  2. unity-mygame과 unity-props가 동시에 `ch_import_model`로 가져옵니다.
- "props.blend의 Rock 세트를 character.blend로 가져와"
  - blender-props가 .blend로 내보내고, blender-character가 Append합니다.
- (Blender에서 Hero를 고친 뒤) "Hero 다시 보내줘"
  - blender-character가 다시 내보내고, unity-mygame이 `ch_import_model`로 받습니다. 같은 .blend의 같은 오브젝트라 새로 넣지 않고 있던 모델을 바꿉니다. 씬에 놓아 둔 Hero들이 그 자리에서 새 모양이 됩니다.
- "MyGame 씬의 Rock_A를 character.blend로 보내줘"
  - unity-mygame이 `ch_export_model`로 내보내고, blender-character가 `import_model`로 가져옵니다.

## 안전장치

- 하위 세션은 자기 전용 MCP 서버만 씁니다. 정의 파일의 `tools` 허용 목록에 그 서버만 들어 있습니다.
  - Unity 서버는 `--default-instance <Name@hash>`로 고정됩니다.
  - Blender 서버는 `--port <포트>`로 고정됩니다. Blender는 서버 하나가 창 하나에만 붙기 때문에, 이것만으로도 다른 창에 닿지 않습니다.
- 안전장치 훅(`tools/guard.py`)은 Unity 호출을 하나하나 검사합니다. 다른 프로젝트를 가리키는 `unity_instance`와 대상 변경(`set_active_instance`)을 막습니다. `batch_execute` 안에 들어 있는 명령도 검사합니다.
- 훅 안에서 무엇이 잘못되든 호출을 막습니다(잘못된 인자, 읽을 수 없는 입력, 판단 중 오류).
- 훅이 시작조차 못 하면 Claude Code는 호출을 막지 않습니다. 그래서 `sync`가 훅 명령에 uv의 전체 경로를 적고, `selftest`가 설치된 훅을 Claude Code와 같은 방법으로 실제로 돌려 봅니다.
- 세팅 스킬은 허락받은 단계만 진행합니다. 화면 조작 중 마우스와 키보드를 실제로 움직여야 할 때는 따로 다시 묻습니다.

## 알려진 한계

- 작업 표시줄 알림, 한글 입력창, 대화상자 감지, Claude Hand 창 자동 최소화는 Windows에서만 됩니다. macOS·Linux에서는 실제 창으로 확인하지 않았습니다.
- 개발 중인 체크아웃에서 `unity-install`을 하면 Claude Hand Tools가 이 PC의 폴더 경로(`file:`)로 들어가서, 그 프로젝트는 다른 PC에서 열리지 않습니다. 릴리스 태그(`v0.9.0`)를 받은 체크아웃에서는 GitHub 주소로 들어갑니다. `unity-install --dry-run`이 어느 쪽인지 알려 줍니다.
- 내보내는 스크립트가 asmdef에 속해 있어도 asmdef는 같이 가지 않습니다. 받는 쪽에서는 기본 어셈블리로 컴파일됩니다.
- `Packages/` 안의 에셋(URP 셰이더 등)은 `.unitypackage`에 들어가지 않습니다. 받는 프로젝트에 같은 패키지가 있어야 하고, dry run의 `missing_packages`에 표시됩니다.
- 같은 프로젝트나 같은 Blender 창에 하위 세션 둘을 동시에 붙이면 안 됩니다. 명령이 하나씩 처리되기 때문입니다. 감독 스킬에 이 규칙이 들어 있고, Claude Hand가 켜져 있는 동안에는 안전장치가 나중에 온 하위 세션의 호출을 막습니다.
- Blender → Unity는 FBX라서 Blender 셰이더 노드가 그대로 옮겨지지 않습니다. 기본 색과 텍스처 정도만 옮겨지고, 복잡한 재질은 Unity에서 다시 손봐야 합니다. 프로젝트에 같은 이름의 머티리얼을 만들어 두고 `materials="project"`로 받으면, 다시 보낼 때마다 손볼 필요가 없습니다.
- 다시 보낸 모델을 제자리에서 바꾸는 것은 **저장된 .blend**에서 **같은 오브젝트 이름**으로 보냈을 때만 됩니다. .blend를 다른 이름으로 저장했거나 옮겼거나 오브젝트 이름을 바꿨으면 새 모델로 들어옵니다. 모델 파일을 바꾼 것은 Unity Undo에 남지 않아 **되돌리기** 버튼으로 돌아가지 않습니다. 바꾸기 전 파일이 `Library/ClaudeHand/replaced/`에 최근 10개까지 있습니다.
- Unity → Blender에서 모델 파일에서 온 오브젝트는 그 파일이 그대로 갑니다. 씬에서 바꾼 것(머티리얼 교체, 덧붙인 오브젝트, 위치)은 가지 않습니다. Unity에서 만든 메시는 FBX Exporter 패키지가 있어야 하고, 이 경로는 그 패키지의 소스를 보고 만들었을 뿐 실제로 돌려 보지는 않았습니다.
- **중지** 버튼은 하위 세션의 다음 Unity·Blender 호출부터 막습니다. 이미 시작한 동작 하나(긴 가져오기 등)는 끝까지 갑니다. 중지는 그 요청에 답이 오면 풀리고, 답이 없어도 3분 뒤에는 풀립니다. 그 사이에 데스크톱 앱에서 다른 창에 시킨 일이 있으면 그 일도 함께 막힐 수 있습니다.
- Blender 하위 세션이 처음 연결을 확인할 때와 `sync`·`doctor`가 파일 이름을 읽을 때, Blender 안에서 읽기 전용 Python(`print(bpy.data.filepath)`)이 실행됩니다.

## 개발

```bash
python -m unittest discover -s tools/tests -v     # Python 3.10 이상, 따로 설치할 것 없음
```
GitHub에 올리면 같은 테스트가 Windows와 Linux, Python 3.10과 3.13에서 돕니다(`.github/workflows/tests.yml`). 버전은 저장소 루트의 `VERSION` 한 곳에서 정하고, Unity 패키지와 Blender 애드온에 적힌 번호가 같은지 테스트가 확인합니다.
`tools/tests/test_blender_chat_addon.py`는 Blender 애드온(채팅, 되돌리기, 중지, .blend 보내기·받기, FBX 내보내기, 모델 받기)을 실제 Blender 안에서 실행합니다. 위 명령으로는 건너뛰고(Python에 `bpy`가 없으므로), `uv run tools/claude_hand.py selftest`가 설치된 Blender를 화면 없이 띄워 돌립니다.
Unity 쪽은 패키지 안의 `ChSelfTest.cs`가 모델 도구를 실제 Unity에서 돌립니다. `selftest --unity-project <닫힌 프로젝트>`가 Unity를 창 없이 열어 실행합니다. 테스트는 진짜 이벤트 기록을 건드리지 않게 `CLAUDE_HAND_LOG=off`로 돕니다.

## 라이선스

[MIT](LICENSE)입니다. 함께 쓰는 MCP for Unity, MCP for Blender, agent-desktop은 이 저장소에 들어 있지 않고 각자의 라이선스를 따릅니다.
