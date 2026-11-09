# 설계: 여러 Unity 프로젝트를 Claude 하위 세션으로 다루기

## 목표

1. Unity 창 여러 개를 띄워 둔 채 **포커스나 최소화와 상관없이** 각 프로젝트를 조작한다.
2. 프로젝트마다 **전담 세션**을 두고 감독 세션이 일을 나눠 준다.
3. 한 프로젝트의 오브젝트를 **다른 프로젝트로 가져와** 쓴다.

## 왜 지금은 포커스된 창만 되는가

MCP for Unity의 명령은 Unity 메인 스레드가 돌 때 처리된다. 그런데 Unity 창이 뒤로 가면 세 가지 이유로 명령 처리가 늦어지거나 멈춘다.
- Unity 자체 절전 (Interaction Mode)
- OS의 백그라운드 절전 (macOS App Nap, Windows의 비활성 에디터)
- 모달 대화상자: 메인 스레드를 막는다

MCP for Unity는 테스트를 실행할 때 Unity 창을 잠깐 앞으로 끌어오는 "focus nudge"로 이 문제를 피해 간다. 그래서 포커스된 창만 되는 것처럼 보인다.

관련 자료:
- [CoplayDev/unity-mcp#1491](https://github.com/CoplayDev/unity-mcp/issues/1491): 비활성 Windows 에디터에서 명령이 30초 넘게 멈춘다. 아직 열려 있다.
- [#1454](https://github.com/CoplayDev/unity-mcp/issues/1454)
- MCP for Unity troubleshooting 문서

이 시제품의 대응은 다음과 같다.
- Interaction Mode를 **No Throttling**으로 둔다. 사용자가 설정한다.
- 하위 세션의 서버에 `UNITY_MCP_DISABLE_FOCUS_NUDGE=1`을 넣는다. 창이 앞으로 튀어나오지 않게 한다.
- `ch_editor_status`로 비활성·최소화 상태의 응답을 **실측**한다. 결과에 따라 2단계에서 Unity 쪽 보완을 넣는다.

## 구조

```
사용자 ⇄ 감독 세션 (Claude Code 메인 대화, 스킬: claude-hand-unity)
            │ Agent 도구로 위임 (병렬 가능)
            ├── unity-a (서브에이전트) ─ 인라인 stdio MCP 서버 (--default-instance A@hash) ─┐
            └── unity-b (서브에이전트) ─ 인라인 stdio MCP 서버 (--default-instance B@hash) ─┤
                                                                                   TCP │ (MCP for Unity 브리지)
                                               Unity A ◀──────────────────────────────┤
                                               Unity B ◀──────────────────────────────┘
              교환 폴더 ~/.claude-hand/exchange ◀── ch_export_objects / ch_import_package
```

### 결정 1: 허브 서버를 새로 만들지 않고 MCP for Unity와 Claude Code 기능을 조합한다

- 여러 Unity 창을 구분하는 일(`Name@hash` ID, `unity_instance`, `set_active_instance`)은 MCP for Unity가 이미 한다.
- 세션 만들기, 병렬 실행, 이어서 시키기(SendMessage)는 Claude Code 서브에이전트가 이미 한다.
- 그래서 우리가 만드는 건 그 사이를 잇는 부분과, MCP for Unity에 없는 도구뿐이다.

### 결정 2: 하위 세션마다 전용 MCP 서버를 둔다

서브에이전트는 기본적으로 부모의 MCP 연결을 같이 쓴다. 그런데 MCP for Unity는 "현재 대상 인스턴스"를 **연결(세션) 단위로 하나만** 기억한다. 그래서 하위 세션 둘이 한 연결을 쓰면 대상이 서로 덮어써진다.

대응은 이렇다.
- 서브에이전트 정의의 `mcpServers`에 인라인 서버를 둔다. 서브에이전트가 시작할 때 뜨고 끝나면 내려간다.
- stdio 방식에서는 `--default-instance`로 대상 프로젝트를 고정한다.
- HTTP 방식에서는 연결이 따로이니 첫 호출로 `set_active_instance(pin)`를 부르게 한다. 안전장치는 고정된 값만 허용한다.

### 결정 3: 고정은 세 겹으로 한다

1. **서버 고정:** `--default-instance`. 인자 없이 부른 호출은 고정된 프로젝트로 간다.
2. **도구 허용 목록:** `tools: mcp__<자기서버>__*, Read, Glob, Grep`. 부모의 `unityMCP` 도구는 아예 보이지 않는다.
3. **PreToolUse 훅 (`claude_hand.py guard`):** 서버 고정이 막지 못하는 경우를 막는다.
   - 호출마다 넣을 수 있는 `unity_instance` 인자
   - `set_active_instance`
   - 다른 서버의 도구
   - `batch_execute` 안에 든 명령도 재귀로 검사한다.
   - 훅에 오류가 나면 막는 쪽(fail closed)으로 동작한다.

### 결정 4: 프로젝트 간 이동은 세션 대화가 아니라 파일로 한다

데이터는 `.unitypackage`와 manifest(`<id>.claudehand.json`)로 교환 폴더를 거쳐 간다. 세션끼리는 `manifest_path`라는 짧은 문자열만 주고받는다. 이 방식이 빠르고, 토큰이 들지 않고, 정확하다.

- **내보내기**
  - 씬 오브젝트를 `Assets/ClaudeHand/Transfers/<id>/`에 임시 프리팹으로 저장한다.
  - `AssetDatabase.GetDependencies(recursive)`로 의존 에셋을 모은다.
  - `Packages/` 안의 의존 에셋은 패키지 이름만 기록하고 내용은 넣지 않는다.
  - `ExportPackage`로 묶은 뒤 임시 프리팹을 지운다. 원본 프로젝트는 그대로 남는다.
- **가져오기**
  - manifest로 계획을 세운다.
    - `new_assets`: 새로 추가되는 것
    - `would_overwrite`: 같은 경로에 이미 있어서 덮어써지는 것
    - `same_guid_elsewhere`: 같은 GUID가 다른 경로에 있는 것
    - `missing_packages`: 받는 쪽에 없는 패키지
  - 덮어쓸 게 있으면 `overwrite=true` 없이는 거부한다.
  - `ImportPackage(interactive:false)`를 실행하고, 폴링하면서 manifest의 GUID가 모두 나타나고 컴파일이 끝나기를 기다린다.
  - 그다음 루트 프리팹을 활성 씬에 배치하고 새 ref를 돌려준다.
  - 상태는 `McpJobStateStore`(Library/)에 저장한다. 그래서 도메인 리로드가 일어나도 이어진다.

### 결정 5: agent-desktop의 스냅샷과 ref 방식을 빌려 온다

- `ch_scene_snapshot`의 ref는 그 스냅샷에서만 유효하다(`@<snapshot>:e<n>`). 예전 ref나 맨 `@e5`는 거부한다.
- 오브젝트를 찾을 때는 instance ID를 먼저 쓰고, 없으면 `GlobalObjectId`로 찾는다. 둘 다 실패하면 `STALE_REF`를 돌려준다.
- 스냅샷은 `Library/ClaudeHand/snapshots/`에 최근 20개를 보관한다. 도메인 리로드가 일어나도 남는다.
- 노드에는 `path`와 `instance_id`도 같이 넣는다. MCP for Unity 기본 도구에 그대로 넘길 수 있다.

## Blender

### 어떤 Blender MCP를 쓰는가

**MCP for Blender (ahujasid/blender-mcp, GitHub판)만 쓴다.**
- 포트가 **씬 속성**(`blendermcp_port`)이라 .blend마다 저장된다.
- 서버는 `--port` 또는 `BLENDER_PORT`로 붙을 Blender를 정한다. 공식 README에 창 여러 개를 동시에 쓰는 예시가 있다.
- MCP for Unity의 `blender_bridge`도 이 애드온 소켓에 붙는다.
- Blender 3.0 이상에서 돈다.

`sync`가 설정에서 다른 Blender MCP를 발견하면 경고만 하고 기본값 `uvx mcp-for-blender`를 쓴다. 포트를 확인할 때 `ping`을 모르는 상대는 건너뛴다.

### 구조

```
blender-<파일> (서브에이전트) ─ 인라인 stdio MCP 서버 (mcp-for-blender --port N, BLENDER_PORT=N)
                                   └─ TCP 127.0.0.1:N ─ Blender 창 (MCP for Blender 애드온)
```

- **고정:** Unity와 달리 서버 하나가 Blender 창 하나에만 붙는다. 그래서 `--port`만으로 대상이 고정된다. 호출마다 대상을 바꾸는 인자가 없어서, 안전장치는 "자기 서버 도구만" 검사한다.
- **발견:** `sync`가 포트 범위를 하나씩 확인한다.
  - `{"type":"ping"}`에 `status: success`로 답한 곳만 MCP for Blender로 본다.
  - 이어서 `get_addon_info`로 Blender 버전을, `execute_code`로 `bpy.data.filepath`를 읽는다(읽기만 한다).
  - 이름은 .blend 파일 이름이고, 저장 안 된 파일은 `untitled-<포트>`가 된다.
- **옮기기:** Blender 쪽에는 새 애드온을 만들지 않았다. `execute_code`로 실행할 Python 방법을 하위 세션 지침에 넣었다. 파이썬 문법은 테스트에서 검사한다.
  - Blender → Blender: `bpy.data.libraries.write(...)`로 대상 오브젝트와 그 의존 데이터(메시, 머티리얼)를 `.blend`로 쓴다. `path_remap="ABSOLUTE"`로 외부 텍스처 경로가 유지되게 한다. 받는 쪽은 `bpy.data.libraries.load(link=False)`로 Append한다.
  - Blender → Unity: FBX로 내보낸다(`-Z` 앞, `Y` 위, `FBX_SCALE_ALL`, 텍스처 포함). Unity가 별도 패키지 없이 가져오는 형식이라서다. Unity 쪽 `ch_import_model`이 `Assets/ClaudeHand/Imports/<id>/`로 복사해 동기로 가져오고 배치한다.
  - manifest 형식은 `claude-hand/blend@1`과 `claude-hand/model@1`이다.
- **MCP for Unity의 `blender_bridge`와의 관계:** Blender가 하나뿐이면 그 도구로 Unity에서 바로 가져와도 된다. 단 기본으로 꺼진 `asset_gen` 그룹이고, Blender 주소를 Unity 설정창에 하나만 둘 수 있다. 그래서 여러 창 구조에서는 교환 폴더 방식을 쓴다.

## 창마다 채팅 (claude-hand-chat 채널)

목표: Unity나 Blender 창에서 친 메시지가 그 창 담당 하위 세션에게 가고, 답이 그 창으로 오게 한다. 창끼리 조율(다른 창에서 가져오기)도 그대로 되게 한다.

### 구조

```
Unity EditorWindow (HttpClient) ─┐   127.0.0.1:8765~, X-Claude-Hand-Token
Blender N 패널 애드온 (urllib)  ─┼──▶ chat_channel.py ──stdio MCP──▶ 감독 세션 (Claude Code, 채널 플래그)
                                 │    · notifications/claude/channel  (meta: chat_id, app, window, agent)
                                 │    · reply 도구 → 그 chat_id 창으로
                                 └──◀ · permission_request → 모든 열린 창에 허용/거부 → permission verdict
```

### 결정

- **채널은 감독 세션 하나에만 붙인다.** 하위 세션마다 따로 Claude를 띄우는 방식(`claude --agent`)은 창끼리 서로를 몰라서 프로젝트 간 작업이 안 된다. 감독이 받아서 그 창 담당에게 넘기면 1:1 대화와 창 사이 조율이 둘 다 된다.
- **창 → 하위 세션 연결은 채널 서버가 한다.** `sync`가 만든 정의 파일(`~/.claude/agents/*.md`)에서 이름과 고정 대상(`--pin`, `--app`)을 읽어 메시지 메타에 `agent`로 붙인다.
  - Unity는 프로젝트 이름으로 찾는다. 같은 이름이 둘이면 추측하지 않고 `none`으로 둔다.
  - Blender는 `<파일>@<포트>`가 정확히 같아야 한다.
  - `chat_id`는 담당 하위 세션 이름이라 창마다 하나다.
- **대화 맥락:** 감독은 같은 `chat_id`에 같은 하위 세션을 `SendMessage`로 이어서 맡긴다. 문서상 이어 쓴 하위 세션은 전체 대화 기록을 유지한다. 규칙은 채널의 `instructions`와 감독 스킬 두 곳에 적었다.
- **권한 요청 중계:** `claude/channel/permission`을 선언한다. Claude Code가 보낸 `permission_request`를 열린 모든 채팅창에 보여주고, 처음 들어온 답 하나만 `notifications/claude/channel/permission`으로 돌려준다. 그래서 Unity 창에 앉은 채로 허락할 수 있다.
- **보안:** 채널 문서는 "인증 없는 채널은 프롬프트 주입 통로"라고 경고한다.
  - HTTP는 127.0.0.1에만 연다.
  - 시작할 때 만든 무작위 토큰을 `~/.claude-hand/chat/server.json`에 쓰고(POSIX는 0600), 모든 요청에 그 토큰을 요구한다. 내 계정으로 도는 프로그램만 메시지를 넣을 수 있다.
  - 메시지는 8000자, 창 정보는 3000자, 요청 본문은 64KB로 제한한다.
- **등록:** 채널 문서의 예시처럼 저장소 루트의 `.mcp.json`에 등록한다. 다만 저장소에 커밋하지 않고, 각 PC에서 `chat-setup`이 절대 경로로 만든다. 만드는 김에 `.claude/settings.local.json`에 `reply` 도구 허용 규칙도 넣는다. 그러지 않으면 답장마다 허락을 묻는다. 둘 다 `.gitignore`에 있다.
  - 이 시제품을 만든 개발 세션은 같은 폴더에서 돌고 있어서, 설정 파일을 직접 커밋하려다 "자기 설정 변경"으로 막혔다. 이 판단도 각 PC에서 만드는 쪽이 맞다는 근거다.
  - `.mcp.json`이 문서대로 안 불러와지는 버전이면 `chat-setup --register-local`(`claude mcp add --scope local`)로 등록한다.
- **감독만 채팅을 받는다.** `.mcp.json`은 그 폴더에서 연 모든 세션이 채널 서버를 띄우게 한다. 그래서 서버는 감독일 때만 HTTP를 열고 `server.json`을 쓴다. 그 밖의 세션에서는 MCP에는 정상 응답하지만 채팅창은 받지 않는다(passive). 채널 플래그 없이 연 세션이 채팅을 가로채 메시지가 사라지는 일을 막는다. 감독인지는 두 가지로 판단한다.
  1. `CLAUDE_HAND_SUPERVISOR=1`: `start-supervisor`가 설정하고, `.mcp.json`의 `env`에 `${CLAUDE_HAND_SUPERVISOR:-0}`로 명시해 넘긴다. Claude Code가 MCP 서버에 자기 환경변수를 다 넘기는지는 문서에 없고, `${VAR}` 확장은 문서에 있다. 첫 시제품은 환경변수가 그대로 넘어간다고 가정했다가, 실제 PC에서 "감독 세션 없음"이 나왔다.
  2. 부모 프로세스 확인: 위로 올라가며 **가장 가까운 Claude Code 프로세스**를 찾는다(실행 파일 이름에 claude가 있거나, claude-code를 실행하는 node·bun). 그 명령줄에 `--dangerously-load-development-channels`(또는 `--channels`)와 `server:claude-hand-chat`이 있으면 감독이다. Windows는 PowerShell CIM, 그 밖은 `ps`로 읽는다. 사용자가 `start-supervisor` 없이 직접 채널 플래그로 열어도 동작한다. 가까운 Claude Code만 보는 건, 중간에 낀 셸의 명령줄 글자에 속지 않기 위해서다(개발 중 실제로 이렇게 오탐이 났다).
- **상태 기록:** 시작할 때마다 `~/.claude-hand/chat/status.json`에 상태(serving, passive, stopped)와 이유를 남긴다. `server.json`이 없을 때 채팅창은 이 파일을 읽고 "대기 중: 채널 플래그 없이 연 세션" 같은 정확한 원인을 보여준다. `claude_hand.py chat-status`는 여기에 설정 형식, 예전 local 등록, 승인 대기, 응답 없는 `server.json`까지 함께 진단한다.
- **갱신:** `update`가 다음을 한 번에 한다.
  - 기존 하위 세션 정의를 그 안의 `--pin`에서 다시 읽어 새 템플릿으로 다시 쓴다
  - 감독 스킬을 다시 설치하고, 첫 버전의 `claude-hand-unity` 폴더를 지운다
  - 예전 local 범위 채널 등록을 지운다. local이 project보다 우선하기 때문이다
  - `chat-setup`을 다시 실행해 점검까지 한다
  - 옛 Blender 채팅 애드온을 바꾼다
  `doctor`의 [갱신] 항목이 이런 흔적을 찾아낸다.
- **첫 실행 시간:** MCP 서버 시작 제한은 기본 30초다(`MCP_TIMEOUT`). `chat-setup`이 `uv run … chat_channel.py --check`를 한 번 돌려 uv가 Python을 미리 받게 하고, 포트와 저장 폴더를 점검한다. `start-supervisor`는 `MCP_TIMEOUT=60000`으로 여유를 둔다.
- **요구 사항 확인(`doctor`):**
  - Claude Code 2.1.234 이상: 이 버전부터 채널에 권한 요청이 전달된다.
  - Bedrock·Vertex·Foundry가 아닐 것: Channels는 claude.ai 로그인이나 Console API 키가 필요하다.
  - 회사 계정의 `channelsEnabled`는 확인할 수 없어서 세팅 스킬이 사용자에게 묻는다.
- **(선택) 하위 세션 도구 자동 허용:** `sync --allow-tools`가 이 폴더의 `settings.local.json`에 생성된 서버별 규칙(`mcp__<서버>__*`)만 넣는다. 서버 이름 앞부분만 맞추는 와일드카드(`mcp__unity_*`)는 권한 규칙에서 지원되지 않아서 서버마다 적는다. `--prune`이면 사라진 창의 규칙을 지운다.
- **창 쪽 구현**
  - Unity: IMGUI EditorWindow다. 긴 폴링(최대 20초)으로 답을 받는다. 도메인 리로드 뒤에는 서버 기록으로 대화를 다시 채우고, 감독 세션이 바뀌면(pid) 기록을 비운다.
  - Blender: 통신은 백그라운드 스레드에서 하고, bpy는 메인 스레드 타이머에서만 만진다. 입력칸에서 Enter를 치면 보낸다.
- **검증:** 채널 서버는 실제 프로세스로 띄워 MCP 핸드셰이크, 메시지 전달, `reply`, 권한 중계를 끝까지 테스트했다. Blender 애드온은 실제 Blender 5.0.1에서 채널 서버와 주고받기, 권한 버튼, 화면 없는 활성화까지 테스트했다. Unity 창은 스텁 컴파일만 했다.

## 쉬운 시작: 터미널을 몰라도 쓰기

목표: 세팅이 끝난 뒤에는 **아이콘 하나나 버튼 하나**로 시작한다. 그 밖에 기억할 것(폴더, 명령, `sync`)이 없게 한다.

| 막히던 지점 | 바꾼 것 |
|---|---|
| 터미널을 열고 폴더로 가서 명령 입력 | 바탕화면·시작 메뉴 "Claude Hand" 바로가기(`shortcut`, Windows는 PowerShell WScript.Shell로 .lnk), 원하면 시작프로그램(`--startup`) |
| 감독 세션이 꺼져 있는지 모름 | Unity·Blender 채팅창이 꺼진 상태를 알아채고 **[Claude Hand 시작]** 버튼을 보여준다. 버튼은 `~/.claude-hand/config.json`의 `start_script`를 연다. 이 파일은 `chat-setup`·`preflight`·`update`가 기록하고, Unity는 패키지 위치로도 찾는다. Windows는 `cmd /c start "Claude Hand" …`로 새 창을 연다 |
| 새 프로젝트·파일마다 `sync` | ① 시작할 때 `preflight`가 열린 창의 하위 세션을 만들거나 고친다. ② 채널이 하위 세션 없는 창의 메시지를 받으면 그 자리에서 같은 일을 한다(창마다 20초에 한 번까지). Claude Code는 `~/.claude/agents`의 새 파일을 다시 시작하지 않아도 읽는다. 다만 세션이 시작될 때 없던 폴더의 첫 파일은 예외라서, `preflight`가 폴더를 먼저 만든다 |
| 두 번 실행해 감독이 둘 | `preflight`가 살아 있는 `server.json`을 보면 "이미 실행 중"을 출력하고 종료 코드 3으로 끝낸다. 시작 스크립트는 Claude Code를 열지 않는다 |
| 첫 실행의 낯선 확인 창들 | `.mcp.json` 서버를 `settings.local.json`의 `enabledMcpjsonServers`로 미리 승인한다(믿는 폴더에서만 적용된다고 문서에 나옴). 줄일 수 없는 폴더 신뢰·개발용 채널 경고는 `preflight`가 처음 한 번 무엇을 고르면 되는지 한국어로 미리 보여준다 |
| 창이 그냥 사라짐 | 시작 스크립트가 uv·claude가 없으면 쉬운 말로 알리고 멈춘다(`pause`). Claude Code가 오류로 끝나도 멈춘다. 창 제목을 "Claude Hand"로 해서 작업 표시줄에서 찾기 쉽게 했다 |
| Claude Code 명령이 없음(데스크톱 앱만 씀) | `doctor`가 찾아내고 `winget install Anthropic.ClaudeCode`를 안내한다. 세팅 스킬은 기본 도구 단계에서 설치한다 |

### Claude 데스크톱 앱에서 진행 보기 (Remote Control)

- `remote-control on`이면 `preflight`가 저장소의 `.launch-args`(git 제외)에 `--remote-control --name "Claude Hand"`를 쓴다. 시작 스크립트가 그 내용을 `claude` 명령 뒤에 붙인다. cmd는 `set /p`, sh는 `eval "set -- …"`로 읽어서, 따옴표 안의 이름이 하나의 인자로 유지된다.
- 결과로 세션이 데스크톱 앱 Code 탭, claude.ai/code, 모바일의 세션 목록에 이름으로 보인다. 문서에 따르면 연결된 기기는 대화를 실시간으로 보여주고, 백그라운드로 도는 하위 세션과 워크플로를 보여주며 멈출 수 있게 한다.
- 그래서 감독 지침에 "하위 세션은 백그라운드로 맡긴다"를 넣었다. 이렇게 하면 앱에서 보이기도 하고, 감독이 그동안 다른 창의 메시지를 받을 수도 있다.
- 사용자 전체 설정(`remoteControlAtStartup`, `~/.claude/settings.json`)은 쓰지 않았다. 모든 세션이 원격 연결되기 때문이다. 감독 세션에만 플래그를 붙인다.
- 조건: Pro·Max·Ultra·Team·Enterprise의 claude.ai 로그인이 필요하다. API 키, Bedrock·Vertex·Foundry, 커스텀 `ANTHROPIC_BASE_URL`에서는 안 된다. `doctor`와 `preflight`가 환경변수로 이걸 찾아 알린다.
- 확인 안 된 것: Channels 개발 플래그와 함께 쓸 때의 동작(가정 20).

배치 파일 주의점:
- 한국어 메시지가 깨지지 않게 `chcp 65001`을 맨 앞에 둔다. BOM은 넣지 않는다(첫 줄이 깨진다).
- `if (...)` 블록 안의 echo에는 괄호를 쓰지 않는다. `)`가 블록을 일찍 닫는다.
- 테스트가 이 두 가지를 검사한다.

## Claude 데스크톱 앱 Code 탭에서 쓰기 (`mode desktop`)

- Code 탭의 로컬 세션은 Claude Code다. 문서에 "로컬 세션은 `~/.claude/skills/`의 개인 스킬을 읽는다", 권한 요청 버튼, 하위 세션을 보여주는 Background tasks 창이 나온다. 그래서 감독 스킬과 `unity-*`/`blender-*` 하위 세션을 그대로 쓴다.
- Channels는 데스크톱 앱 문서에 없다. 그래서 Unity·Blender 채팅창은 이 모드에서 연결되지 않는다. 데스크톱 세션이 띄운 채널 서버는 가장 가까운 Claude Code 프로세스에 채널 옵션이 없어서 대기 모드로 남는다. 따라서 같은 폴더에서 CLI 감독과 같이 써도 채팅을 가로채지 않는다.
- 시작 스크립트(`preflight`)가 돌지 않으니, 감독이 하위 세션 없는 프로젝트를 만나면 `sync`를 직접 실행한다. 설치되는 감독 스킬은 `{{CLAUDE_HAND}}` 자리에 이 저장소의 절대 경로를 넣어서, 어느 폴더에서 열어도 명령이 맞는다(`rendered_skill`).
- `mode desktop`은 `config.json`에 기록된다. `doctor`는 채팅 항목을 "필요 없음"으로 바꾸고, Unity·Blender 채팅창은 "데스크톱 앱 Code 탭에서 대화하세요"를 먼저 보여준다(버튼으로 채팅창 모드도 켤 수 있다).
- 확인 안 된 것: Code 탭이 `~/.claude/agents`의 사용자 하위 세션(인라인 `mcpServers`와 `hooks` 포함)과 저장소 `.claude/skills`를 CLI와 똑같이 읽는지(가정 21). 문서 페이지의 확인한 부분에는 개인 스킬만 명시돼 있다.

## 처음 세팅 (claude-hand-setup 스킬)

처음 쓰는 사람이 Claude Code에 "세팅해줘"라고만 하면 되게 하는 것이 목표다. 스킬은 저장소의 `.claude/skills/`에 있어서, 저장소 폴더에서 Claude Code를 열면 바로 쓸 수 있다.

### 순서와 허락

1. `claude_hand.py doctor`로 진단한다. 기본 도구, agent-desktop, Blender, Unity, claude-hand 항목마다 상태와 고치는 명령이 나온다.
2. 빠진 항목만 계획으로 보여주고 `AskUserQuestion`으로 여러 개를 고르게 한다. 한 번의 허락으로 진행하고, Claude Code 자체의 도구 권한 요청이 그 위에 한 겹 더 있다.
   다음 경우에만 따로 다시 묻는다.
   - 마우스와 키보드를 실제로 움직이는 화면 조작
   - 대화상자의 선택
   - 덮어쓰기
3. 고른 단계만 진행하고, 단계마다 결과를 확인한다.

### 무엇으로 하나: 명령어 → 파일 → MCP → 화면 조작

| 할 일 | 방법 | 화면 조작이 필요한 경우 |
|---|---|---|
| uv, git, Node.js | `winget install` | 없음 (UAC는 사용자가) |
| agent-desktop | `npm install -g agent-desktop` | 없음. Windows는 따로 권한을 줄 필요가 없다 |
| MCP for Blender 애드온 설치·활성화, Claude 설정 | `uvx mcp-for-blender setup --dry-run` → `--yes`. 애드온을 복사하고 Blender를 headless로 실행해 켠다 | 없음 |
| Blender 창마다 포트 | `blender-open <파일> --port N`: `--python-expr`로 씬 포트를 바꾼 뒤 이벤트 루프가 시작돼서, 애드온 자동 시작이 새 포트를 쓴다 | 이미 열린 창을 닫을 수 없을 때만 N 패널 조작 |
| Unity 패키지 (MCP for Unity, Claude Hand Tools) | `unity-install`: `Packages/manifest.json`을 고친다(백업 남김) | Unity가 열려 있으면 창이 앞에 와야 패키지를 받는다(사용자 클릭 또는 `focus-window`), 대화상자 |
| Claude Code에 MCP for Unity 등록 | `claude mcp add --scope user --transport http unityMCP http://localhost:8080/mcp` (MCP for Unity 기본값 HTTP에 맞춤) | 없음 (재시작은 사용자가) |
| Unity Interaction Mode | MCP로 `ch_apply_settings(apply)`. EditorPrefs `InteractionMode=1`, `ApplicationIdleTime=0`(UnityCsReference의 Preferences 코드와 같은 키)로 바꾸고 internal `EditorApplication.UpdateInteractionModeSettings`를 리플렉션으로 부른다. 메뉴 Tools > Claude Hand에도 있다 | MCP가 안 될 때 메뉴 클릭 |
| 하위 세션 | `sync` | 없음 |

### agent-desktop의 역할과 한계

- **할 수 있는 것:** OS 접근성 트리(Windows UI Automation)로 창과 버튼을 읽고 누른다. 기본은 백그라운드(headless)라 포커스를 뺏지 않는다.
  - 처음 세팅의 대화상자, 이미 열린 창의 설정 변경
  - 평소에는 Unity·Blender를 멈추게 한 대화상자 처리. 감독 스킬에 순서가 있고, 무엇을 누를지는 항상 사용자에게 묻는다.
- **할 수 없는 것**
  - **최소화된 창.** agent-desktop의 Windows 문서에 따르면 최소화된 창의 요소는 모두 offscreen이라 조작이 실패한다. `restore`로 먼저 복원해야 한다.
  - 접근성 정보가 없는 화면. Unity·Blender 내부 패널은 직접 그려서 거의 읽히지 않는다. 이때는 허락을 받고 `--headed`로 화면을 캡처한 뒤 좌표로 클릭한다. 실제 마우스와 키보드를 쓴다.
- 그래서 처음 목표인 "최소화 상태에서도 조작"은 agent-desktop이 아니라 MCP 경로(Unity: MCP for Unity + No Throttling, Blender: 애드온 타이머)가 맡는다. 실측으로 확인한다(가정 6, 8).

## 아직 검증하지 않은 가정 (PC에서 확인 필요)

| # | 가정 | 틀렸을 때 |
|---|---|---|
| 1 | 인라인 MCP 서버의 도구 이름이 `mcp__<서버키>__<도구>` 형식이다 | `sync --no-allowlist`로 허용 목록을 끄고 훅에만 의존한다 |
| 2 | `tools`에 `mcp__x__*` 와일드카드가 동작한다 | 위와 같다 |
| 3 | MCP for Unity의 stdio 서버가 시작할 때 Unity에서 C# 커스텀 도구 목록을 받아 와 개별 도구로 노출한다. 소스에서 확인했지만, 시작 시점의 동기화라 늦으면 목록에 빠질 수 있다 | 하위 세션 지침대로 `manage_tools(action="sync")`를 부르고, 그래도 없으면 `execute_custom_tool`로 부른다 |
| 4 | HTTP 방식에서 인라인 http 서버 연결이 부모와 별개의 MCP 세션이다 | stdio로 바꾼다 |
| 5 | Windows Git Bash에서 `uv`가 PATH에 있다 | `sync --runner "py -3"` 같이 다른 실행기를 지정한다 |
| 6 | 비활성·최소화 상태의 Unity가 No Throttling에서 명령을 제때 처리한다 | 2단계: Unity 쪽 깨우기 장치를 넣는다 |
| 7 | `ImportPackage(interactive:false)`가 포커스 없이 끝난다 | 실측 후 대응한다 |
| 8 | 최소화된 Blender에서도 애드온 타이머(`bpy.app.timers`)가 명령을 처리한다 | 실측 후 대응한다 |
| 9 | `execute_code` 안에서 `bpy.ops.export_scene.fbx`가 컨텍스트 오류 없이 돈다. 애드온의 `export_scene`도 같은 방식이다 | 컨텍스트를 덮어쓰는 방식으로 바꾼다 |
| 10 | 사용 중인 `blender-mcp`/`mcp-for-blender` 버전이 `--port` 또는 `BLENDER_PORT`를 받는다. 둘 다 넣는다 | 패키지를 업데이트한다 |
| 11 | `blender-open`의 `--python-expr`가 애드온 자동 시작보다 먼저 돈다(타이머는 이벤트 루프에서만 돈다) | N 패널에서 포트를 바꾸고 서버를 켠다 |
| 12 | `UpdateInteractionModeSettings` 리플렉션 호출이 Unity 6.3에서 바로 적용된다 | Unity를 다시 시작하면 적용된다(도구가 `applied_live`로 알려준다) |
| 13 | Unity가 열려 있을 때 `manifest.json`을 고치면 창이 앞에 올 때 패키지를 다시 받는다 | Unity를 다시 연다 |
| 14 | `.mcp.json`으로 등록한 서버를 `--dangerously-load-development-channels server:claude-hand-chat`이 채널로 불러온다(문서 예시와 같음). `.mcp.json`의 절대 경로 인자가 그대로 쓰인다 | `chat-setup --register-local`로 등록한다 |
| 15 | 감독이 같은 `chat_id`에 같은 하위 세션을 계속 이어 쓴다(지침을 따르는지) | 지침을 고치거나, 채널 서버가 agent id를 기억해 메타로 넘긴다 |
| 16 | Unity 에디터가 비활성일 때도 `async` 이어 받기가 늦지 않게 돈다 | 폴링을 `EditorApplication.update`로 옮긴다 |
| 17 | Windows에서 PowerShell `Get-CimInstance Win32_Process`가 채널 서버 시작 중에 30초 안에 끝난다(보통 1~2초) | 환경변수 경로만 쓴다 |
| 18 | `settings.local.json`의 `enabledMcpjsonServers`가 `.mcp.json` 승인 질문을 없앤다(문서는 local 파일의 승인이 믿는 폴더에서 적용된다고만 함) | 처음 한 번 질문에 답한다(안내 문구가 있음) |
| 19 | Unity·Blender 버튼의 `cmd /c start "Claude Hand" <script>`가 새 콘솔 창을 연다 | 바탕화면 아이콘을 쓴다 |
| 20 | `--remote-control`과 `--dangerously-load-development-channels`를 한 세션에 같이 쓸 수 있다 | `remote-control off`로 끄고, 필요할 때 그 창에서 `/remote-control`을 입력한다 |
| 21 | 데스크톱 앱 Code 탭이 `~/.claude/agents`의 하위 세션(인라인 MCP 서버·훅 포함)과 저장소의 `.claude/skills`를 읽는다 | 하위 세션이 안 보이면 채팅창 모드(Claude Hand 창)를 쓴다 |

## 다음 단계 (2단계 후보)

- **세션 간 직접 대화:** Claude Code 에이전트 팀(실험 기능)을 쓰거나 Channels로 감독을 거치지 않고 메시지를 주고받는다. 지금은 감독이 중계한다.
- **모달 대화상자 자동 감지:** 지금은 감독이 시간 초과를 보고 agent-desktop으로 확인한다. 하위 세션의 시간 초과를 감독에게 바로 알리는 장치를 넣는다.
- **백그라운드 보완:** 실측 결과 Windows에서 막히면 Unity 쪽에서 메인 루프를 깨우는 장치를 넣는다.
