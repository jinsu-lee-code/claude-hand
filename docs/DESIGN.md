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
사용자 ⇄ 감독 세션 (Claude Code 메인 대화, 스킬: claude-hand)
            │ Agent 도구로 위임 (병렬 가능)
            ├── unity-a (서브에이전트) ─ 인라인 stdio MCP 서버 (--default-instance A@hash) ─┐
            │                            └ unity_a_ch: unity_bridge.py --hash <A> ─────┼──┐
            └── unity-b (서브에이전트) ─ 인라인 stdio MCP 서버 (--default-instance B@hash) ─┤  │
                                         └ unity_b_ch: unity_bridge.py --hash <B> ─────┼──┤
                                                                                   TCP │  │ TCP (claude-hand 통로, ChBridge.cs)
                                               Unity A ◀──────────────────────────────┤◀─┤
                                               Unity B ◀──────────────────────────────┘◀─┘
              교환 폴더 ~/.claude-hand/exchange ◀── ch_export_objects / ch_import_package
```

MCP for Unity 기본 도구(씬·오브젝트·스크립트)는 MCP for Unity를 거치고, claude-hand의 `ch_*` 도구는 claude-hand 통로를 거친다(결정 6).

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
2. **도구 허용 목록:** `tools: mcp__<자기서버>__*, mcp__<자기서버>_ch__*, Read, Glob, Grep`. 부모의 `unityMCP` 도구는 아예 보이지 않는다. `_ch` 서버는 시작 인자(`--hash`)로 자기 프로젝트에만 붙으니 다른 프로젝트를 고를 방법이 없다.
3. **PreToolUse 훅 (`tools/guard.py`):** 서버 고정이 막지 못하는 경우를 막는다.
   - 호출마다 넣을 수 있는 `unity_instance` 인자
   - `set_active_instance`
   - 다른 서버의 도구
   - `batch_execute` 안에 든 명령도 재귀로 검사한다.
   - 훅에 오류가 나면 막는 쪽(fail closed)으로 동작한다. Claude Code는 훅이 종료 코드 0으로 거부를 출력하거나 종료 코드 2로 끝날 때만 호출을 막고, 그 밖의 종료 코드는 "훅 오류"로 보고 호출을 그대로 진행한다. 그래서 가드는 0이나 2로만 끝난다.
     - 가드를 표준 라이브러리만 쓰는 작은 파일로 따로 둔다. 처음에는 `claude_hand.py`의 한 명령이어서, 그 큰 파일 어디에서든 문법 오류가 나면 종료 코드 1로 끝나 호출이 통과됐다(REVIEW-2026-11-10의 3번).
     - 판단 뒤에 하는 일(작업 중 표시, 되돌리기 지점)에 필요한 것은 그때 불러온다. 거기서 나는 오류는 판단을 바꾸지 않는다.
     - 훅이 시작도 못 하는 경우(훅 셸이 uv를 찾지 못함: 종료 코드 127)는 가드가 막을 수 없다. `sync`가 훅 명령에 uv의 전체 경로를 적고(빈칸이 든 경로는 Git Bash와 PowerShell 양쪽에 맞는 따옴표가 없어 이름만 적는다), `selftest`가 설치된 훅 명령을 Git Bash로 그대로 돌려 "남의 호출을 막는지" 본다.
     - 훅 입력은 UTF-8 바이트로 직접 읽는다. Python이 파이프를 어떤 인코딩으로 읽는지는 PC 설정에 따라 달라서(시험한 PC는 UTF-8이었다) 기본값에 기대지 않는다.

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
  - 상태는 `Library/ClaudeHand/jobs/`에 저장한다(`ChJobState`). 그래서 도메인 리로드가 일어나도 이어진다.

### 결정 5: agent-desktop의 스냅샷과 ref 방식을 빌려 온다

- `ch_scene_snapshot`의 ref는 그 스냅샷에서만 유효하다(`@<snapshot>:e<n>`). 예전 ref나 맨 `@e5`는 거부한다.
- 오브젝트를 찾을 때는 instance ID를 먼저 쓰고, 없으면 `GlobalObjectId`로 찾는다. 둘 다 실패하면 `STALE_REF`를 돌려준다.
- instance ID는 `ChCompat`을 거친다. Unity 6.3은 `GetInstanceID`·`EditorUtility.InstanceIDToObject`에 사용 중단 경고(CS0618)를 내고, Unity 7 참조 소스는 이를 오류로 바꾸고 `GetEntityId`·`EntityIdToObject`를 쓰게 한다. 그래서 `UNITY_7000_0_OR_NEWER`에서는 EntityId를, 그 아래에서는 instance ID를(경고만 끄고) 쓴다. 스냅샷 파일에는 문자열 `object_id`로 저장해서 둘 다 담는다(Windows 결과 과제 10).
- 스냅샷은 `Library/ClaudeHand/snapshots/`에 최근 20개를 보관한다. 도메인 리로드가 일어나도 남는다.
- 노드에는 `path`와 `instance_id`도 같이 넣는다. MCP for Unity 기본 도구에 그대로 넘길 수 있다.


### 결정 6: ch_* 도구는 claude-hand 통로로 부른다

Windows 시험에서 MCP for Unity 10.3.0 stdio 서버는 C# 커스텀 도구를 목록에 내보내지 않았다(`manage_tools sync`, `execute_custom_tool`도 안 됨). MCP for Unity 버전마다 달라질 수 있는 부분이라 기대지 않기로 했다.

- Unity 쪽: `ChBridge.cs`가 `[InitializeOnLoad]`로 `127.0.0.1`의 빈 포트에 소켓을 연다. 한 줄 JSON 요청(`{token, tool, params}`)을 받아 메인 스레드(`EditorApplication.update`)에서 `[ChTool]` 클래스의 `HandleCommand`를 부른다.
- **패키지는 MCP for Unity 없이 컴파일된다.** 처음에는 도구 속성(`McpForUnityTool`, `ToolParameter`), 응답 형식(`SuccessResponse`, `ErrorResponse`, `PendingResponse`), 작업 상태 저장(`McpJobStateStore`) 여섯 가지를 MCP for Unity에서 빌려 썼다. 호출 경로만 독립이고 컴파일은 묶여 있어서, 그쪽에서 이름 하나가 바뀌면 통로·채팅창·되돌리기·캡처가 한꺼번에 사라질 수 있었다(REVIEW-2026-11-10의 1번). 지금은 같은 일을 하는 자기 형식(`ChTool.cs`: `ChTool`, `ChParameter`, `ChSuccess`, `ChError`, `ChPending`, `ChJobState`)을 쓰고, asmdef는 `MCPForUnity.Editor`를 참조하지 않는다. 도구가 돌려주는 JSON 모양은 전과 같다. 패키지가 필요로 하는 것은 `com.unity.nuget.newtonsoft-json`뿐이고 `package.json`에 적혀 있다.
  - 그래서 `ch_*` 도구는 MCP for Unity에는 등록되지 않는다. 10.3.0 stdio는 어차피 커스텀 도구를 내보내지 않아서(가정 3) 잃는 것이 없다.
  - `unity-install`은 MCP for Unity를 시험한 버전의 태그(`v10.3.0`)로 넣는다. 전에는 `#main`이라 새로 설치하는 프로젝트마다 그날의 최신본을 받았다. 이미 `#main`으로 넣은 프로젝트는 그대로 두고 알려 주기만 하며, `unity-install --pin-mcp-for-unity`로 옮길 수 있다.
- 등록 파일: `~/.claude-hand/unity/<hash>.json`. hash는 MCP for Unity와 같은 SHA1(Application.dataPath) 앞 8자리라 `Name@hash`가 그대로 맞는다. 포트, 토큰, 도구 목록(이름·설명·인자·폴링 여부)을 담고 30초마다 다시 쓴다. Unity를 닫으면 지운다.
- 토큰: 처음 열 때 만들어 SessionState에 둔다. 같은 PC의 다른 프로그램이 포트만 알아서는 부를 수 없다. 파일은 사용자 폴더 안에 있다.
- 스크립트 리로드: 리로드 직전에 소켓을 닫고 기다리던 요청에는 `EDITOR_BUSY`(retry: safe)로 답한다. 다시 열 때 같은 포트를 먼저 시도한다. `unity_bridge.py`는 연결이 안 되면 등록 파일을 다시 읽어 재시도한다.
- 폴링 도구(`ch_import_package`)는 `unity_bridge.py`가 `action=status`로 끝날 때까지 물어본다. 하위 세션 쪽에서는 한 번의 호출로 보인다.
- 하위 세션: `unity-<이름>`마다 `<서버키>_ch` 인라인 서버로 `unity_bridge.py --hash <hash>`를 띄운다. 안전장치 훅은 자기 `_ch` 서버 도구를 통과시킨다.
- 명령줄: `claude_hand.py unity-call <프로젝트> <도구> key=value ...`. 세팅 스킬이 감독 없이 `ch_apply_settings`를 부를 때 쓴다.
- 등록 파일은 Unity 찾기에도 쓴다. MCP for Unity가 HTTP로 등록돼 `~/.unity-mcp` 상태 파일이 없을 때도 `list`·`sync`가 열린 프로젝트를 찾는다. `doctor`는 두 경로에서 보이는 프로젝트가 다르면 알려 준다.

### 결정 7: 감독은 Unity·Blender를 직접 만지지 않는다

감독 세션에 MCP for Unity나 MCP for Blender가 붙어 있으면, 감독이 하위 세션을 건너뛰고 직접 조작하는 일이 생겼다(고정도 안전장치도 거치지 않는다). `chat-setup`이 사용자 설정에서 그런 서버(MCP for Unity, Unity AI Assistant 릴레이, MCP for Blender)를 찾아 claude-hand 폴더의 `settings.local.json`에 `permissions.deny: mcp__<키>__*`로 넣는다. 하위 세션은 자기 이름(`unity_<이름>`, `blender_<이름>`)의 서버를 쓰니 막히지 않는다. 감독 스킬에도 같은 규칙을 적었다.
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
- **옮기기:** Claude Hand Chat 애드온의 함수다(`export_blend`, `import_blend`, `export_fbx`). 하위 세션은 `execute_code`로 `import claude_hand_chat; print(claude_hand_chat.export_fbx([...], "이름"))` 한 줄을 실행한다.
  - 처음에는 하위 세션 지침 속의 코드 블록 세 개였다. 하위 세션이 옮길 때마다 스무 줄 남짓을 옮겨 적어야 했고, 테스트가 지키는 것은 지침에 적힌 원문이지 실제로 실행되는 코드가 아니었다(REVIEW-2026-11-10의 6번). 지금은 함수를 Blender 안에서 직접 테스트하고, 지침은 92줄에서 29줄이 됐다.
  - 그래서 Blender에서 옮기려면 채팅 애드온이 있어야 한다. 없으면 `ModuleNotFoundError`가 나고, 하위 세션은 설치가 필요하다고 보고한다.
  - 애드온 이름(`claude_hand_chat`)은 채팅보다 넓은 일을 하게 됐지만 바꾸지 않았다. 바꾸면 설치된 애드온을 지우고 다시 켜는 일이 모든 PC에 생긴다.
  - 결과 미리보기용 캡처 코드는 애드온으로 옮기지 않았다(`handlib/capture.py`). Unity 채팅창에 Blender의 모습을 붙이는 일은 그 Blender에 채팅 애드온이 없어도 돼야 한다.
  - Blender → Blender: `bpy.data.libraries.write(...)`로 대상 오브젝트와 그 의존 데이터(메시, 머티리얼)를 `.blend`로 쓴다. `path_remap="ABSOLUTE"`로 외부 텍스처 경로가 유지되게 한다. 받는 쪽은 `bpy.data.libraries.load(link=False)`로 Append한다.
  - Blender → Unity: FBX로 내보낸다(`-Z` 앞, `Y` 위, `FBX_SCALE_ALL`, 텍스처 포함). Unity가 별도 패키지 없이 가져오는 형식이라서다. Unity 쪽 `ch_import_model`이 `Assets/ClaudeHand/Imports/<이름>/`으로 복사해 동기로 가져오고 배치한다. 같은 원본이 다시 오면 그 파일을 제자리에서 바꾼다(아래 "0.9에서 더한 것").
  - Unity → Blender: `ch_export_model`이 같은 manifest 형식으로 쓰고 애드온의 `import_model`이 받는다(같은 절).
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
- **창 → 하위 세션 연결은 채널 서버가 한다.** `sync`가 만든 정의 파일(`~/.claude/agents/*.md`)에서 이름과 고정 대상을 읽어 메시지 메타에 `agent`로 붙인다.
  - 정의 파일에는 프로그램이 읽는 줄이 하나 있다: `<!-- claude-hand: {"app": …, "pin": …, "path": …, "server": …} -->`. 채널 서버, `update`, `doctor`는 이 줄만 읽는다(`handlib.agents.read_agent`). 처음에는 훅 명령 속의 `--pin`과 프롬프트 문장 속의 경로를 정규식으로 꺼냈는데, 그러면 훅 명령이나 문장을 고칠 때 채팅 연결과 `update`가 오류 없이 어긋난다(REVIEW-2026-11-10의 5번).
  - 따로 목록 파일(`agents.json`)을 두지 않고 정의 파일 안에 넣었다. 목록과 파일이 둘이면 서로 어긋날 수 있고(파일을 손으로 지운 경우), 파일 하나면 그럴 일이 없다.
  - 그 줄이 없는 예전 파일은 예전 방식으로 읽는다. `update`가 다시 쓰면 그 길은 더 쓰이지 않는다.
  - 채널 서버는 메시지, 현재 상황 보고, 하위 세션의 도구 호출마다 이 목록이 필요하다. 그래서 파일이 바뀌었을 때만(수정 시각과 크기) 다시 읽는다.
  - Blender 창용 등록 파일은 만들지 않았다. 채팅 애드온이 없는 Blender 때문에 포트 훑기는 어차피 남고, 등록 파일이 생기면 닫힌 창이 남긴 파일을 가려내는 일이 하나 더 생긴다.
  - Unity는 프로젝트 이름으로 찾는다. 같은 이름이 둘이면 추측하지 않고 `none`으로 둔다.
  - Blender는 `<파일>@<포트>`가 정확히 같아야 한다.
  - `chat_id`는 담당 하위 세션 이름이라 창마다 하나다.
- **대화 맥락:** 감독은 같은 `chat_id`에 같은 하위 세션을 `SendMessage`로 이어서 맡긴다. 문서상 이어 쓴 하위 세션은 전체 대화 기록을 유지한다. 채널 서버는 그 창에서 마지막으로 일한 하위 세션의 ID를 기억해 태그에 `agent_id`로 붙인다. 감독이 긴 대화를 압축하다 잊어도 이어 맡길 수 있다(가정 15의 대안).
- **감독이 지킬 규칙의 원문은 한 곳에 둔다**(`prompts/chat-rules.md`). 채널 서버는 이 글을 `instructions`로 주고, 감독 스킬은 설치될 때 `{{CHAT_RULES}}` 자리에 같은 글을 넣는다. 전에는 같은 규칙을 채널(영문)과 스킬(한국어)에 따로 적어서, 한쪽에만 있는 규칙이 지켜지지 않은 적이 있다(15단계).
- **지침에만 있던 규칙 가운데 채널 서버가 알 수 있는 것은 코드가 받친다**(REVIEW-2026-11-10의 4번). 근거가 되는 사실 두 가지를 실제 Claude Code 2.1.321에서 확인했다: 하위 세션의 PreToolUse 훅 입력에는 `agent_id`가 들어 있고, 하위 세션 정의의 `Stop` 훅은 그 하위 세션이 차례를 마칠 때 `SubagentStop`으로 불린다.
  - 가드는 동작을 보고할 때 `agent_id`를 같이 보낸다. 정의 파일의 `Stop` 훅은 같은 `guard.py`를 `--stop`으로 불러 "차례를 마쳤다"를 알린다(`/v1/stopped`). 이 훅은 무슨 일이 있어도 종료 코드 0으로 끝난다. 2로 끝나면 하위 세션이 멈추지 못한다.
  - **같은 창에 하위 세션 둘:** 그 창에서 다른 하위 세션이 90초 안에 동작을 보고했고 아직 마치지 않았으면, 채널 서버가 `busy`로 답하고 가드가 그 호출을 막는다. 하위 세션은 막힌 이유를 감독에게 보고한다. 먼저 일하던 쪽이 마쳤다고 알리면 바로 풀린다. 마쳤다는 알림 없이 조용해진 경우(강제 중지)에는 90초 뒤 풀린다. Claude Hand가 꺼져 있으면 채널 서버가 없으니 이 확인도 없다.
  - **답 누락:** 요청이 열려 있는데 하위 세션들이 모두 마친 지 1분이 지났거나, 하위 세션의 일이 한 번도 보이지 않은 채 5분이 지나면, 채널 서버가 감독에게 `[claude-hand]`로 시작하는 알림을 한 번 보낸다. 채팅창에는 "답이 늦어져 Claude Hand에 다시 알렸어요"가 뜬다. 감독이 답을 `reply` 대신 터미널에 쓰면 그 창의 사용자는 끝없이 기다리게 되는데, 이 경우를 잡는다. 다시 켠 뒤 기록에서 다시 보이는 예전 요청은 건드리지 않는다.
  - `working: true`를 빼먹는 것과 데스크톱 앱 요청을 채팅창에 남기는 것은 여전히 감독에게 달려 있다. 글 내용으로는 가려낼 수 없다.
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
  - 기존 하위 세션 정의가 어느 창의 것인지 읽어 새 템플릿으로 다시 쓴다
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
- **검증:** 채널 서버는 실제 프로세스로 띄워 MCP 핸드셰이크, 메시지 전달, `reply`, 권한 중계를 끝까지 테스트했다. Blender 애드온은 실제 Blender 5.0.1에서 채널 서버와 주고받기, 권한 버튼, 화면 없는 활성화까지 테스트했다. 그 뒤 Windows의 실제 Unity 6.3과 Blender 5.2에서 사용자가 화면으로 확인했다(TEST-RESULTS-12·13-WINDOWS). `selftest`가 Blender 안 테스트와 열린 창의 응답을 한 번에 돌린다.

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

- 기본으로(`remote-control off`가 아니면) `preflight`가 저장소의 `.launch-args`(git 제외)에 `--remote-control --name "Claude Hand"`를 쓴다. 시작 스크립트가 그 내용을 `claude` 명령 뒤에 붙인다. cmd는 `set /p`, sh는 `eval "set -- …"`로 읽어서, 따옴표 안의 이름이 하나의 인자로 유지된다.
- 결과로 세션이 데스크톱 앱 Code 탭, claude.ai/code, 모바일의 세션 목록에 이름으로 보인다. 문서에 따르면 연결된 기기는 대화를 실시간으로 보여주고, 백그라운드로 도는 하위 세션과 워크플로를 보여주며 멈출 수 있게 한다.
- 그래서 감독 지침에 "하위 세션은 백그라운드로 맡긴다"를 넣었다. 이렇게 하면 앱에서 보이기도 하고, 감독이 그동안 다른 창의 메시지를 받을 수도 있다.
- 사용자 전체 설정(`remoteControlAtStartup`, `~/.claude/settings.json`)은 쓰지 않았다. 모든 세션이 원격 연결되기 때문이다. 감독 세션에만 플래그를 붙인다.
- 조건: Pro·Max·Ultra·Team·Enterprise의 claude.ai 로그인이 필요하다. API 키, Bedrock·Vertex·Foundry, 커스텀 `ANTHROPIC_BASE_URL`에서는 안 된다. `doctor`와 `preflight`가 환경변수로 이걸 찾아 알린다.
- Channels 개발 플래그와 함께 써도 된다는 것을 Windows에서 확인했다(가정 20, TEST-RESULTS-12-WINDOWS).

배치 파일 주의점:
- 한국어 메시지가 깨지지 않게 `chcp 65001`을 맨 앞에 둔다. BOM은 넣지 않는다(첫 줄이 깨진다).
- `if (...)` 블록 안의 echo에는 괄호를 쓰지 않는다. `)`가 블록을 일찍 닫는다.
- 테스트가 이 두 가지를 검사한다.

## 하나로 정리한 방식: Claude Hand 창이 엔진, 채팅창과 데스크톱 앱은 입구

```
Unity·Blender 채팅창 ─▶ 채널 ─▶ [Claude Hand 창: 감독 세션(엔진)] ◀═ Remote Control ═▶ Claude 데스크톱 앱(보기·입력)
        │                                    ▲
        └── 선택·활성 씬/파일 (/v1/context) ──┘ current_context 도구
```

TEST-PLAN 12(Windows)에서 Remote Control과 Channels를 한 세션에 같이 쓸 수 있음을 확인했다(가정 20). 그래서 예전의 "Code 탭만" 방식(`mode desktop`)을 없애고 이렇게 정리했다.

- **모드 없음.** `config.json`의 예전 `"mode"` 값은 무시하고 지운다(`write_user_config`). `mode` 명령은 그 사실만 알린다. `doctor`의 채팅 항목은 늘 필요한 항목이다.
- **Remote Control 기본 켜짐.** `config.json`에 `remote_control: false`가 없으면 켠다. 환경변수로 안 되는 조건(API 키, 다른 서버를 가리키는 `ANTHROPIC_BASE_URL`, Bedrock·Vertex·Foundry)을 찾으면 `preflight`가 `.launch-args`에서 플래그를 빼고 이유를 창에 쓴다. 데스크톱 앱이 자기 세션에 넣는 `ANTHROPIC_BASE_URL=https://api.anthropic.com`은 문제로 보지 않는다(Windows 결과 과제 5). 구독이 아닌 claude.ai 로그인처럼 환경변수로 알 수 없는 경우는 Claude Code가 알린다.
- **창은 보이게 열고, 쓰기 시작하면 최소화.** 개발용 채널 경고는 Claude Code의 보안 확인이라 대신 답하지 않는다. 그래서 바로가기와 채팅창 버튼은 창을 보이게 연다. 채널 서버가 Claude의 첫 도구 호출이나 첫 허락 요청을 받으면(그때는 시작 질문을 지나온 것이 확실하다) Windows에서 그 창을 최소화한다. 방법: 프로세스 표에서 Claude Code와 그 위 두 단계(콘솔을 가진 cmd)를 찾고, `EnumWindows`로 그 프로세스들의 **보이는** 콘솔 창(`ConsoleWindowClass`)을 골라 `ShowWindowAsync(SW_MINIMIZE)`한다. 처음 방식(창 없는 Python에서 `AttachConsole` → `GetConsoleWindow`)은 Claude Code 아래에서 15초 시간 초과로 멈췄다. 보이지 않는 콘솔까지 잡으면 숨어 있던 Unity·릴레이 콘솔이 튀어나와서 보이는 것만 고른다. Windows Terminal 탭이면 찾지 못하고 그대로 둔다. `minimize off`(config `minimize_after_start`)로 끈다.
- **처음 메시지로 켜기.** 채팅창은 Claude Hand가 꺼져 있거나(`server.json` 없음) 응답이 없으면 메시지를 맡아 두고 시작 스크립트를 연다. 그동안 "Claude Hand 창에서 확인을 눌러 주세요"를 보여 준다. 서버가 응답하면 맡아 둔 메시지를 보낸다. Unity는 `[SerializeField]`로 스크립트 리로드에도 남긴다. 채널 서버는 Claude Code가 `notifications/initialized`를 보내기 전에 온 메시지를 모아 두었다가 그때 보낸다(`backlog`).
- **현재 상황 공유(`current_context`).** Unity는 `ChContextReporter`(InitializeOnLoad)가 `Selection.selectionChanged`, `EditorApplication.focusChanged`, `activeSceneChangedInEditMode`에 반응해, Blender는 애드온 타이머가 바뀐 것을 비교해, 활성 씬·파일, 선택한 오브젝트·에셋(각 20개까지), 포커스를 `/v1/context`로 보낸다. 0.5초에 한 번까지, 바뀌었을 때만, 1분마다 한 번 새로 보낸다. Claude Hand가 꺼져 있으면 아무것도 보내지 않는다. 채널 서버는 창마다 마지막 상태와 마지막으로 쓴 시각을 들고, 감독의 `current_context` 도구가 최근 순(포커스 먼저)으로 돌려준다.
- **데스크톱 앱에서 시킨 일도 채팅창에.** `reply`에 `origin: "desktop"`, `request`(무엇을 시켰는지 한 줄)를 더했다. 채널 서버는 패널이 한 번도 말하지 않은 창이라도 그 창의 하위 세션 이름이면 받아 기록한다. 채팅창은 "데스크톱 앱에서" 아래에 요청과 결과를 보여 준다. 감독 지침(채널 instructions, 감독 스킬)에 "채널 태그 없는 요청이 창을 바꿨으면 그 창에 이렇게 남긴다"를 넣었다.
- **데스크톱 앱에서 켜기.** 데스크톱 앱에는 cmd를 여는 방법이 문서에 없다. 그래서 아무 Code 탭 세션에서 "Claude Hand 켜줘"라고 하면 감독 스킬이 `claude_hand.py start`를 실행한다. 이 명령은 이미 켜져 있거나 자기가 Claude Hand(`CLAUDE_HAND_SUPERVISOR=1`)면 아무것도 열지 않고, 아니면 새 창을 연 뒤 확인 질문과 세션 목록의 "Claude Hand"로 옮겨 가라고 안내한다.
- Code 탭에서 새로 연 다른 세션은 Claude Hand가 아니다. 채널 서버는 대기 모드로 뜨고(채널 옵션이 없음), 감독 스킬은 작업을 "Claude Hand" 세션에서 하라고 한 번 권한다. 그래도 여기서 하길 원하면 하위 세션으로 일은 되지만 채팅창 기록과 `current_context`는 없다.

## 매일 쓰기: 이어 쓰기, 되돌리기, 알림 (TEST-PLAN 14~21)

Windows에서 며칠 써 보며 더한 것들이다. 공통 원칙은 **토큰이 드는 길을 피하는 것**이다. 감독이 도구를 한 번 더 부르게 하지 않고, 패널·채널 서버·guard 훅·Unity 패키지가 직접 한다. 그래서 감독이 단계를 세어 채우던 진행 막대(`progress` 도구)는 만들었다가 뺐다(실제로도 감독이 잘 부르지 않았다).

- **대화 이어 쓰기.** 작업은 며칠씩 이어지는데, 켤 때마다 새 세션이면 감독이 앞일을 모르고 데스크톱 앱에도 세션이 쌓인다. `preflight`가 `config.json`의 `supervisor_session`을 `claude --resume`으로 연다. 처음 한 번은 이 폴더에서 가장 최근의 "Claude Hand" 대화(대화 파일의 `customTitle`)를 이어받고, 없으면 `--session-id`로 새 ID를 정한다. 긴 대화는 Claude Code가 알아서 압축한다. `new-session`은 다음 시작을 새 대화로 한다. 다시 켜면 예전 하위 세션은 없을 수 있어서, 채널 지침에 "`SendMessage`가 실패하면 새로 맡긴다"를 넣었다.
- **채팅 기록.** 채널 서버가 창마다 최근 100개(나·Claude·그림)를 `~/.claude-hand/chat/history/<chat_id>.json`에 두고 시작할 때 다시 보여 준다. 허락 요청과 안내는 남기지 않는다. 대화의 본체는 Claude Code에 있으니 패널은 맥락을 짐작할 만큼만 든다. Unity는 100개(한 번에 40개, 메시지 1500자), Blender는 60개(40줄, 메시지 14줄)를 보여 주고 나머지는 "전체 복사"로 꺼낸다. 오래 쓴 패널이 느려지거나 입력칸이 밀려나지 않게 하려는 것이다. Blender는 사이드바 너비를 `blf`로 재서 줄을 바꾼다.
- **세션 변수 정리.** 데스크톱 앱의 Code 탭에서 "Claude Hand 켜줘"로 켜면 그 세션의 환경변수(`CLAUDECODE`, `CLAUDE_CODE_*` 등)가 새 감독에게 넘어가, 감독이 남의 세션 소켓을 물려받았다. `preflight`가 넘겨받은 세션 변수 이름을 `.launch-unset`(git 제외)에 쓰고 시작 스크립트가 지운다. 사용자가 정한 설정(`CLAUDE_CODE_USE_*`, `CLAUDE_CODE_GIT_BASH_PATH`, `CLAUDE_CODE_OAUTH_TOKEN`, 영구 환경변수)은 남긴다.
- **작업 중 표시.** 하위 세션의 guard 훅이 허락한 Unity·Blender 호출마다 채널 서버 `/v1/progress`로 도구와 요약을 보낸다(0.5초 안에 못 보내면 건너뜀). 패널은 입력칸 위에 "작업 중 · N초 · <동작> · N번째 동작" 한 줄을 보여 주고, 답이 오면 지운다. Claude는 이 보고를 보지 않는다. 막대 그림은 사용자 요청으로 뺐다.
- **되돌리기.** 같은 보고에 채널 서버가 "그 요청의 첫 동작"인지 답하면, guard가 그 창에 되돌리기 지점을 찍는다.
  - Unity: `ch_checkpoint`가 이름 붙인 Undo 그룹을 SessionState에 20개까지 둔다(Undo 기록처럼 에디터를 끄면 사라진다). 채팅창 버튼과 `action=undo`가 `Undo.RevertAllDownToGroup`으로 돌아간다. Undo에 남는 변경만 돌아가서, 하위 세션 지침에 `execute_code`의 변경을 Undo에 남기라고 적었다. 모델·패키지 가져오기가 파일에 한 일은 지점에 같이 적어 두었다가 함께 돌린다(아래 "파일까지 되돌리기").
  - Blender: Python에서 실행 취소 기록 목록을 볼 수 없어서 지점을 데이터에 남긴다. 모든 씬의 숨은 속성에 "이번 실행:번호"를 넣고 `ed.undo_push`를 한다. 되돌릴 때는 "되돌리기 전" 단계를 하나 넣고(Ctrl+Shift+Z로 살릴 수 있게), 번호가 지점보다 작아질 때까지 `ed.undo`, 그다음 `ed.redo` 한 번이면 지점을 찍은 바로 그 상태다. 한도(실행 취소 단계 수)를 넘으면 제자리로 돌려놓고 실패를 알린다. 연산자 안에서 undo하면 연산자가 쥔 데이터가 풀리므로 버튼은 타이머에서 돌린다. guard는 MCP for Blender의 `execute_code`로 채팅 애드온을 불러 지점을 찍는다.
  - "방금 거 취소해줘"도 새 요청이라, 다른 도구를 먼저 쓰면 그 자리에 빈 지점이 찍혀 아무것도 돌아가지 않았다. guard는 되돌리기 호출 앞에서는 찍지 않고, 지침은 "다른 도구보다 먼저 되돌린다"로 했다.
- **프로젝트·파일 메모.** 며칠 가는 규칙(이름, 폴더, 재질)은 대화 속에만 두면 압축될 때 흐려진다. `<프로젝트>/ClaudeHand/NOTES.md`(Assets 밖이라 Unity가 가져오지 않음)와 `<파일>.claude-hand.md`에 두고, 하위 세션이 시작할 때 읽고 감독이 고친다. 이 폴더 밖이라 Read·Edit가 매번 허락을 물어서, `sync`·`update`·시작 때 창마다 그 파일 하나만 미리 허용한다. Windows 경로는 `Read(//c/…)`처럼 써야 맞는다는 것을 `claude -p`로 확인했다.
- **Blender 한글 입력창.** Blender 입력칸은 Windows 한글 입력기와 맞지 않아 조합을 끝내는 띄어쓰기·기호가 빠지고 글자가 한 박자 늦게 보인다. Python이 키를 받기 전에 생기는 일이라 애드온으로는 고칠 수 없다. 그래서 [한글] 버튼이 PowerShell WinForms 텍스트 상자를 띄운다(`-EncodedCommand`, 창 이름은 base64로 넣어 스크립트로 읽히지 않음). 메시지는 한 줄씩 base64(UTF-8)로 받아 그 창의 채팅으로 보낸다. Blender가 끝나면 1초 안에 닫힌다.
- **결과 미리보기.** 한 창에서 시킨 일이 다른 창을 바꾸면(Blender에서 "Unity로 보내줘"), 답과 함께 그 창의 지금 모습을 붙인다. 찍는 것은 채널 서버가 직접 한다. **어느 창을 붙일지는 감독이 말한다**: `reply`의 `changed_windows`에 그 요청으로 바꾼 다른 창을 적는다(도구 정의에서 꼭 적어야 하는 값이고, 없으면 `[]`). 그 요청에 어느 하위 세션을 썼는지 아는 것은 감독뿐이다. 채널은 어느 창이 언제 일했는지만 보고, 그것만으로 추정하다가 두 프로젝트를 나란히 시키면 서로의 그림이 붙는 일이 세 번 났다(아래 "다른 창의 일이 누구 몫인가"). 감독이 적지 않은 답(예전 정의를 아는 세션)에만 채널의 추정을 쓰고, 그 추정은 확실할 때만 그림을 붙인다. 감독이 먼저 보내는 "작업 중이에요"는 `reply`의 `working: true`로 표시해, 끝난 답으로 치지 않는다. 그래서 요청이 열린 채로 남고, 진행 줄과 되돌리기 지점이 이어지며, 그림·깜빡임은 결과 답에만 따른다. 글 내용으로는 둘을 가려낼 수 없어 감독이 표시한다. Unity는 `ch_capture`(씬 뷰 카메라를 복사한 카메라로 RenderTexture에 그림, URP와 백그라운드에서 동작, 기즈모 없음), Blender는 `execute_code`로 `GPUOffScreen.draw_view3d`를 쓴다. `render.opengl`은 사용자의 Render Result를 덮어써서 버렸다. 그림은 최근 60장, 채팅 기록에도 남는다.
- **끝났다는 알림.** 패널이 30초 안에 생긴 답·허락 요청·대화상자 안내를 받았는데 앱이 앞에 없으면 `FlashWindowEx`로 작업 표시줄을 그 앱이 앞에 올 때까지 깜빡인다. Unity 채팅 탭에는 안 읽은 수를 글자로 붙인다. 색이 아니라 깜빡임과 글자로 알린다. 다시 켠 뒤 다시 보이는 기록과 데스크톱 앱에서 시킨 일의 답은 알리지 않는다.
- **대화상자 감지(Unity, Windows).** 모달 대화상자는 메인 스레드를 막아 모든 호출이 시간 초과까지 기다렸다. Unity의 `DisplayDialog`는 Windows 기본 대화상자(`#32770`, 주인은 Unity 주 창)라 밖에서 읽힌다. 제목은 창 글자, 본문은 `Edit` 컨트롤(다른 프로세스라 `WM_GETTEXT`로), 선택지는 `Button`이다. 진행 막대 창(`msctls_progress32`)은 고를 것이 없어 뺀다. pid는 Claude Hand Tools 등록 파일에서 얻는다.
  - guard: Unity 호출 직전에 대화상자가 있으면 내용과 함께 막는다. 하위 세션은 버튼을 누르지 않고 감독에게 보고한다.
  - Claude Hand 통로: 기다리는 동안 1.5초마다 보고, 대화상자가 뜨면 `DIALOG`(retry `check_first`: 닫히면 그때 실행될 수 있음)로 바로 끝낸다.
  - 채널 서버: 하위 세션이 일하는 동안 2초마다 보고, 답을 기다리는 **다른** 창의 채팅에 알린다(깜빡임 포함). 무엇을 누를지는 항상 사용자가 정한다.
- **selftest.** `doctor`가 "설치됐나"를 본다면 `selftest`는 "도나"를 본다. 테스트 두 묶음(Python, Blender 안), 열린 Unity의 `ch_*` 도구가 저장소와 같은지(옛 컴파일 잡기)와 씬 뷰 찍기, 열린 Blender의 채팅 애드온 버전, 채널, 설치 파일을 본다. 아무것도 바꾸지 않는다. (`--unity-project`를 붙이면 닫힌 프로젝트에서 모델 도구를 실제로 돌린다. 아래 "0.9에서 더한 것".)

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
| Claude Code에 MCP for Unity 등록 | Unity의 Window > MCP for Unity에서 Transport를 Stdio로 하고 Configure, 또는 `claude mcp add --scope user unityMCP -- uvx --from mcpforunityserver mcp-for-unity --transport stdio`. 처음 설정 창의 기본값은 HTTP지만, HTTP로는 `~/.unity-mcp` 상태 파일이 안 생기고 시험도 Stdio로 했다 | 없음 (재시작은 사용자가) |
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
| 3 | ~~MCP for Unity의 stdio 서버가 C# 커스텀 도구를 개별 도구로 노출한다~~ **틀림** (Windows, MCP for Unity 10.3.0 stdio: 목록에 없고 sync·execute_custom_tool도 안 됨). 대신 Claude Hand Tools가 자기 통로를 연다: `ChBridge.cs`가 루프백 소켓을 열고 `~/.claude-hand/unity/<hash>.json`에 포트·토큰·도구 목록을 쓰며, 하위 세션마다 `tools/unity_bridge.py`가 `<서버키>_ch` MCP 서버로 붙는다. 남은 가정: 스크립트 리로드 뒤 같은 포트를 다시 잡는다(SessionState), Unity 6.3에서 백그라운드 스레드 소켓과 `EditorApplication.update` 처리가 비활성·최소화 상태에서도 돈다(No Throttling) | 포트가 바뀌면 `unity_bridge.py`가 등록 파일을 다시 읽어 재시도한다(최대 약 20초). 최소화 상태에서 안 돌면 가정 6과 같이 깨우기 장치를 넣는다 |
| 4 | HTTP 방식에서 인라인 http 서버 연결이 부모와 별개의 MCP 세션이다 | stdio로 바꾼다 |
| 5 | **확인됨(Windows)** Windows Git Bash에서 `uv`가 PATH에 있다. 이제는 여기에 기대지 않는다: 훅 명령에 uv의 전체 경로를 적고 `selftest`가 설치된 훅을 Git Bash로 돌려 본다(결정 3) | `sync --runner "py -3"` 같이 다른 실행기를 지정한다 |
| 6 | 비활성·최소화 상태의 Unity가 No Throttling에서 명령을 제때 처리한다 | 2단계: Unity 쪽 깨우기 장치를 넣는다 |
| 7 | `ImportPackage(interactive:false)`가 포커스 없이 끝난다 | 실측 후 대응한다 |
| 8 | 최소화된 Blender에서도 애드온 타이머(`bpy.app.timers`)가 명령을 처리한다 | 실측 후 대응한다 |
| 9 | `execute_code` 안에서 `bpy.ops.export_scene.fbx`가 컨텍스트 오류 없이 돈다. 애드온의 `export_scene`도 같은 방식이다 | 컨텍스트를 덮어쓰는 방식으로 바꾼다 |
| 10 | 사용 중인 `blender-mcp`/`mcp-for-blender` 버전이 `--port` 또는 `BLENDER_PORT`를 받는다. 둘 다 넣는다 | 패키지를 업데이트한다 |
| 11 | `blender-open`의 `--python-expr`가 애드온 자동 시작보다 먼저 돈다(타이머는 이벤트 루프에서만 돈다) | N 패널에서 포트를 바꾸고 서버를 켠다 |
| 12 | `UpdateInteractionModeSettings` 리플렉션 호출이 Unity 6.3에서 바로 적용된다 | Unity를 다시 시작하면 적용된다(도구가 `applied_live`로 알려준다) |
| 13 | Unity가 열려 있을 때 `manifest.json`을 고치면 창이 앞에 올 때 패키지를 다시 받는다 | Unity를 다시 연다 |
| 14 | `.mcp.json`으로 등록한 서버를 `--dangerously-load-development-channels server:claude-hand-chat`이 채널로 불러온다(문서 예시와 같음). `.mcp.json`의 절대 경로 인자가 그대로 쓰인다 | `chat-setup --register-local`로 등록한다 |
| 15 | 감독이 같은 `chat_id`에 같은 하위 세션을 계속 이어 쓴다(지침을 따르는지). 이제 채널 서버가 그 창에서 마지막으로 일한 하위 세션의 ID를 태그의 `agent_id`로 넘긴다. 남은 가정: 훅이 받는 `agent_id`가 `SendMessage`가 받는 ID와 같다 | 지침을 고친다 |
| 16 | Unity 에디터가 비활성일 때도 `async` 이어 받기가 늦지 않게 돈다 | 폴링을 `EditorApplication.update`로 옮긴다 |
| 17 | Windows에서 PowerShell `Get-CimInstance Win32_Process`가 채널 서버 시작 중에 30초 안에 끝난다(보통 1~2초) | 환경변수 경로만 쓴다 |
| 18 | `settings.local.json`의 `enabledMcpjsonServers`가 `.mcp.json` 승인 질문을 없앤다(문서는 local 파일의 승인이 믿는 폴더에서 적용된다고만 함) | 처음 한 번 질문에 답한다(안내 문구가 있음) |
| 19 | Unity·Blender 버튼의 `cmd /c start "Claude Hand" <script>`가 새 콘솔 창을 연다 | 바탕화면 아이콘을 쓴다 |
| 20 | **확인됨(Windows)** `--remote-control`과 `--dangerously-load-development-channels`를 한 세션에 같이 쓸 수 있다 | `remote-control off`로 끄고, 필요할 때 그 창에서 `/remote-control`을 입력한다 |
| 21 | 데스크톱 앱 Code 탭이 `~/.claude/agents`의 하위 세션(인라인 MCP 서버·훅 포함)과 저장소의 `.claude/skills`를 읽는다 | "Claude Hand 켜줘"가 안 되면 바탕화면 아이콘으로 켠다 |
| 22 | **확인됨(Windows)** 개발용 채널 경고는 Claude Code가 시작할 때 묻고, Claude의 첫 도구 호출은 그 뒤에만 온다. 그래서 그때 최소화하면 질문을 가리지 않는다. **경고는 켤 때마다 뜬다**(Windows에서 확인). 문서상으로도 그렇다: 직접 만든 채널은 Anthropic 허용 목록에 없어서 `--dangerously-load-development-channels`가 필요하고, 경고를 끄는 설정은 없다. 피하는 길은 Team·Enterprise 관리자의 `allowedChannelPlugins`(플러그인으로 묶어 조직 허용 목록에 넣기)나 공식 마켓플레이스 등록뿐이고, Pro·Max 개인 계정에는 없다. 문서의 예시는 "경고를 수락한 뒤 채널 서버를 띄운다"고 적고 있다 | `minimize off` |
| 23 | ~~창 없는 Python에서 `AttachConsole(<claude pid>)`로 Claude Hand 콘솔 창을 찾아 최소화할 수 있다~~ **틀림**: Claude Code 아래에서 멈췄다. 지금은 Claude Code 위 두 단계 프로세스의 보이는 콘솔 창을 `EnumWindows`로 찾아 `ShowWindowAsync`한다(Windows 10 conhost에서 확인). 남은 가정: Windows Terminal이 기본 터미널이면 콘솔 창을 찾지 못한다 | 최소화가 안 될 뿐 동작에는 지장이 없다. 사용자가 직접 최소화한다 |
| 24 | Remote Control로 들어온 데스크톱 앱 입력이 채널 태그 없는 일반 사용자 메시지로 보인다(그래서 감독이 "데스크톱에서 온 요청"으로 구분한다) | 지침을 "채널 태그가 없으면"에서 다른 표시로 바꾼다 |
| 25 | **확인됨(Claude Code 2.1.321)** 하위 세션의 PreToolUse 훅 입력에 `agent_id`·`agent_type`이 있다. 하위 세션 정의의 `Stop` 훅은 `SubagentStop`으로 불리고 같은 `agent_id`를 받는다. 채널 서버가 `busy`로 답하면 가드의 거부가 하위 세션의 MCP 호출을 막는다 | `agent_id`가 없으면 같은 창 잠금과 태그의 `agent_id`는 동작하지 않고, 나머지는 전과 같다 |
| 26 | 사용자가 데스크톱 앱에서 하위 세션을 강제로 멈춰도 `Stop` 훅이 불린다 | 안 불리면 그 창은 90초 동안 다른 하위 세션을 막는다 |
| 27 | **확인됨(Unity 6000.3.25f1, 배치 모드)** 모델의 `.meta`에 적은 `userData`와 라벨로 "전에 들어온 모델"을 다시 찾고, 파일을 덮어쓴 뒤 다시 가져오면 GUID와 씬 오브젝트가 유지된다. `AddRemap`·`RemoveRemap`으로 머티리얼 연결이 바뀐다 | 기록을 `Library/ClaudeHand/` 아래의 목록 파일로 옮긴다 |
| 28 | FBX Exporter의 `ModelExporter.ExportObjects(string, Object[])`를 리플렉션으로 부르면 씬 오브젝트가 FBX로 써진다. 패키지 5.1.6의 소스에서 이름과 인자를 확인했을 뿐 실행해 보지 않았다(시험 프로젝트에 그 패키지가 없다) | 오류를 그대로 보고하고, 사용자가 Unity 메뉴(GameObject > Export To FBX)로 내보낸 파일을 `import_model`에 직접 준다 |
| 29 | 감독이 `[중지]` 알림을 받으면 그 요청의 일을 더 맡기지 않고 `reply`로 답한다(지침). 실제 감독 세션으로 확인하지 않았다 | 하위 세션의 다음 호출은 채널이 막으므로 일은 멈춘다. 답이 없으면 1분 뒤 "답이 없다" 알림이 가고, 중지는 3분 뒤 풀린다 |
| 30 | 한 창의 일이 어느 요청 몫인지에 대한 채널의 추정(자기 채팅이 기다리는 요청, 없으면 다른 창의 가장 최근 요청)이 대체로 맞는다. 중지가 막을 창과 `trace`의 요청 번호가 여기에 달려 있다. 2026-11-11에 "일이 보이기 전에 온 답은 끝난 답이 아니다"를 더했고, 같은 날 감독의 훅이 맡길 때마다 요청 번호를 알려 주게 해서 이 추정은 훅이 없는 세션에서만 쓰인다(위 "다른 창의 일이 누구 몫인가", 가정 32) | 틀리면 중지가 데스크톱 앱에서 시킨 다른 일까지 3분 동안 막을 수 있다 |
| 32 | **확인됨(Claude Code 2.1.321, 헤드리스)** 감독 세션의 `Agent`·`SendMessage` 호출 직전에 PreToolUse 훅이 불리고, 입력에 `subagent_type`·`prompt`와 `to`·`message`가 있다. `--settings`로 넘긴 파일의 훅이 그 세션에 적용된다. 훅이 거부하면 감독은 이유를 읽고 `[요청 <번호>]`를 적어 다시 맡긴다. 그 뒤 하위 세션의 가드가 보고한 동작이 그 요청 번호로 기록된다. 실제 감독 세션(채널 태그로 번호를 받는 경우)과 `SendMessage`로 이어 맡기는 경우는 확인하지 않았다 | 훅이 불리지 않거나 감독이 번호를 못 적으면 채널이 전의 추정으로 돌아간다. 감독이 거부에서 헤어나지 못하면 `~/.claude-hand/config.json`에 `"supervisor_hook": false`를 넣고 다시 켠다(훅 없이 시작) |
| 33 | **확인됨(Unity 6000.3.25f1, 배치 모드)** `Undo.RevertAllDownToGroup`과 `MoveAssetToTrash`가 창 없이도 동작한다. 보관한 파일과 그 `.meta`를 다시 놓고 `ImportAsset`하면 이전 가져오기 설정(머티리얼 연결)으로 돌아간다. `ImportPackage`가 덮어쓴 에셋도 같은 방법으로 돌아온다. 열린 에디터에서 채팅창의 되돌리기 버튼으로는 확인하지 않았다 | 휴지통으로 못 옮긴 에셋은 그대로 두고 `problems`로 알린다. 지우는 쪽으로 넘어가지 않는다 |
| 31 | 감독이 결과 답마다 `changed_windows`에 그 요청으로 바꾼 다른 창을 맞게 적는다. 헤드리스 Claude Code(Haiku)로 두 경우를 확인했고, 실제 감독 세션에서 여러 창을 나란히 쓰며 확인하지는 않았다 | 틀린 창을 적으면 그 창의 그림이 붙는다. 빠뜨리면 그림이 없다. 어느 쪽도 일을 망치지는 않는다. 빠뜨린 답에는 감독의 훅이 알려 준 "그 요청으로 일한 창"을 쓴다 |

## 정한 방향: "데스크톱 앱 + 채팅창" 하나로

TEST-PLAN 12에서 확인된 뒤 위의 "하나로 정리한 방식"으로 만들었다. 사용자와 정한 것: 모드는 하나, Remote Control 기본 켜짐, 현재 상황 공유, 데스크톱 앱에서 시킨 일을 채팅창에도 기록, 처음 대화할 때 자동 시작(컴퓨터를 켤 때 자동 실행은 하지 않음), 데스크톱 앱의 아무 Code 탭 세션에서 "Claude Hand 켜줘". 바뀐 점 하나: 창을 처음부터 최소화로 열기로 했었지만, 개발용 채널 경고가 켤 때마다 떠서 사용자가 그 창에서 답해야 한다. 그래서 보이게 열고 쓰기 시작하면 최소화한다(가정 22).

## 배포와 설치 (2026-11-10 개편)

- **버전은 한 곳.** 저장소 루트의 `VERSION`이 원본이다. 채널 서버와 통로 서버는 그것을 읽는다. Unity 패키지(`package.json`)와 Blender 애드온(`bl_info`)은 체크아웃 밖으로 복사돼 쓰이므로 같은 번호를 적어 두고, 테스트가 같은지 확인한다. 전에는 네 곳이 따로였다(0.1.0 셋, 애드온 0.7.1). Unity 통로의 등록 파일에도 패키지 버전이 들어가서 `selftest`가 "예전 컴파일"을 버전으로도 잡는다.
- **Claude Hand Tools를 어디서 받는가.** 처음에는 늘 이 체크아웃의 폴더 경로(`file:C:/…`)를 프로젝트의 `Packages/manifest.json`에 적었다. 그러면 그 프로젝트는 다른 PC에서 열리지 않고, 이 PC에서는 작업 중인 코드가 열린 프로젝트 전부에 바로 컴파일된다(REVIEW-2026-11-10의 2번). 지금은 `unity-install`이 고른다.
  - 이 체크아웃이 정확히 `v<VERSION>` 태그면 GitHub 주소(`…claude-hand.git?path=/unity/com.claudehand.tools#v<VERSION>`)로 넣는다.
  - 그 밖의 체크아웃(브랜치 끝, 고치는 중)이면 폴더 경로로 넣고, 그 프로젝트가 이 PC에서만 열린다고 알린다. GitHub 주소로 넣으면 지금 코드와 다른 코드를 받게 되기 때문이다.
  - `--release`와 `--folder`로 정해 줄 수도 있다. 태그가 GitHub에 올라가기 전에는 `--release`로 넣은 프로젝트가 패키지를 받지 못한다.
- **교환 폴더는 하나의 규칙으로 찾고, 오래된 것은 지운다.** `CLAUDE_HAND_EXCHANGE_DIR`, 없으면 claude-hand 홈(`CLAUDE_HAND_HOME` 또는 `~/.claude-hand`)의 `exchange`다. Python, Unity 패키지, Blender 애드온이 같은 규칙을 쓴다(전에는 Unity와 Blender가 `CLAUDE_HAND_HOME`을 보지 않았다). 옮긴 파일은 사본이라 시작할 때(`preflight`) 30일 넘은 것을 지운다. claude-hand가 쓰는 종류(`.unitypackage`, `.blend`, `.fbx`, manifest, 캡처)이고 폴더 바로 아래에 있는 파일만 지운다. `config.json`의 `exchange_keep_days`로 바꾸고 0이면 지우지 않는다.
- **자동 테스트.** `.github/workflows/tests.yml`이 Windows·Linux, Python 3.10·3.13에서 돌린다. 3.10에서 돌려 보니 스크립트가 약속한 `requires-python >=3.10`이 지켜지지 않고 있었다(f-string 안의 역슬래시는 3.12부터). 3.12에서는 "무엇이 먼저 일어났나"를 시각으로 비교하던 곳이 틀렸다: Windows의 Python 3.13 전 시계는 15ms쯤씩 움직여서 메시지와 그 직후의 동작이 같은 시각이 된다. 그런 비교는 시각이 아니라 항목 번호(`seq`)로 한다.

## 0.9에서 더한 것 (2026-11-10)

[REVIEW-2026-11-10](REVIEW-2026-11-10.md)의 "넣으면 좋은 기능" 가운데 남은 다섯 가지다.

### 다시 보낸 모델은 제자리에서 바꾼다

전에는 `ch_import_model`이 올 때마다 새 폴더를 만들었다. 모델을 고쳐 다시 보내면 사본이 하나 더 생기고, 씬 배치·프리팹·머티리얼 연결은 옛 사본에 남았다.

- **무엇을 "같은 것"으로 보는가.** manifest의 `source`(.blend 경로)와 `roots`(보낸 오브젝트 이름들)가 같으면 같은 모델의 새 버전이다. 보낼 때 붙인 이름(`name`)은 보지 않는다. 저장된 적 없는 .blend는 `source`가 비어 있어 늘 새 모델이 되고, `export_fbx`가 그 사실을 결과의 `note`로 알린다. 모델 파일을 직접 준 경우에는 그 파일 경로가 `source`다.
- **기록을 어디에 두는가.** 모델의 `.meta` 안(`AssetImporter.userData`)에 `source`·`roots`·머티리얼 선택을 적고, 찾기 쉽게 에셋 라벨 `ClaudeHandImport`를 붙인다. 따로 목록 파일을 두지 않은 이유는 5번 점검 때와 같다: 목록과 에셋이 어긋날 수 있다. `.meta`에 있으면 Project 창에서 옮기거나 이름을 바꿔도 따라가고, 에셋을 지우면 기록도 함께 사라진다. 다른 도구가 이미 `userData`를 쓰고 있으면 덮어쓰지 않고 경고한다. 대가는 .blend의 절대 경로가 `.meta`에 들어간다는 것이다. 다른 PC에서는 경로가 달라 맞지 않을 뿐이고(새 모델로 들어온다), 풀리는 데는 쓰이지 않는다.
- **바꾸는 방법.** 같은 경로의 파일을 덮어쓰고 다시 가져온다. GUID와 가져오기 설정이 그대로라 씬 오브젝트와 프리팹이 그 자리에서 새 모양이 된다. 씬에 이미 놓인 것이 있으면 새로 놓지 않고 그 ref들을 돌려준다.
- **허락을 묻는가.** `ch_import_package`는 덮어쓸 것이 있으면 `overwrite=true` 없이는 거부한다. 여기서는 묻지 않기로 했다. 바뀌는 것이 이 도구가 같은 원본에서 넣어 둔 파일 하나뿐이고(프로젝트의 다른 에셋은 닿지 않는다), 모델 파일은 Unity 안에서 고칠 수 없어 사라질 손작업이 없고, "다시 보내줘"가 곧 바꾸라는 뜻이기 때문이다. 대신 세 가지를 뒀다: 바꾸기 전 파일을 `Library/ClaudeHand/replaced/`에 최근 10개까지 남기고(파일 교체는 Undo에 남지 않아서, 되돌리기가 이 보관본을 다시 놓는다. 이름은 시각이고, 같은 초에 또 바꾸면 `_2`, `_3`이 붙는다), `dry_run=true`가 어느 모델을 바꿀지 미리 알려 주고, `new_copy=true`가 따로 한 벌을 넣는다. 따로 넣은 사본에는 기록을 남기지 않아서, 다음에 다시 보내면 원래 것이 바뀐다.
- **이름.** 새 모델은 `Assets/ClaudeHand/Imports/<이름>/<이름>.fbx`로 들어간다. 전에는 파일 이름에 보낸 시각이 붙었는데, 계속 바뀔 에셋의 이름으로는 맞지 않았다. manifest에 `name`이 따로 들어간다.
- **머티리얼.** `materials="project"`면 모델이 쓰는 머티리얼마다 프로젝트(`Assets/`)의 같은 이름 `.mat`을 찾아 연결한다(`AddRemap`). 정확히 하나일 때만 한다. 같은 이름이 둘 이상이면 그대로 두고 후보를 보고한다. Unity의 `SearchAndRemapMaterials`를 쓰지 않은 이유가 이것이다: 그 함수는 무엇을 골랐는지, 왜 못 골랐는지 말해 주지 않는다. 선택은 기록에 남아 다시 보낼 때 그대로 적용되고, `materials="embedded"`를 주면 연결을 풀고 무엇을 풀었는지 알린다(`unlinked`).

### Unity → Blender

네 방향 가운데 빠져 있던 것이다. `ch_export_model`이 `claude-hand/model@1` manifest를 쓰고(Blender가 Unity로 보낼 때와 같은 형식), Blender 애드온의 `import_model`이 받는다. 같은 manifest를 다른 Unity 프로젝트의 `ch_import_model`도 받는다.

- 모델 파일(FBX, OBJ, GLB)에서 온 오브젝트는 **그 파일을 그대로** 보낸다. 다시 내보내면서 잃는 것이 없다. 대신 씬에서 바꾼 것은 가지 않고, 그럴 때는 결과의 `warnings`가 알린다(프리팹 override가 있을 때, 모델의 일부만 골랐을 때, 메시만 빌려 쓴 오브젝트일 때).
- 그 밖의 것(기본 도형, Unity에서 만든 메시, 여러 파일의 메시를 섞은 것)은 FBX로 새로 써야 한다. 이 패키지가 FBX 쓰기를 직접 하지는 않고, 프로젝트에 Unity의 FBX Exporter가 있으면 그것을 이름으로 찾아 부른다(`ModelExporter.ExportObjects`). 참조하지 않으니 없어도 컴파일된다. 없으면 설치하라는 오류를 돌려주고, 설치할지는 사용자가 정한다.

### 요청 단위 기록 (`trace`)

채널 서버와 통로는 stderr에만 쓰고 가드는 아무것도 남기지 않아서, 지난 요청에 무슨 일이 있었는지 볼 방법이 없었다.

- 채널 서버가 채팅창 메시지마다 요청 번호를 붙이고, 그 뒤의 일을 `~/.claude-hand/logs/<날짜>.jsonl`에 한 줄씩 남긴다. 쓰는 곳은 셋이다: 채널 서버(메시지, 가드가 보고한 호출, 자기가 막은 호출, 하위 세션이 멈춘 때, 답, 다시 알림, 대화상자, 허락 요청), 가드(자기가 막은 호출과 이유), 통로(`ch_*` 호출이 어떻게 끝났고 얼마나 걸렸는지). 서로 다른 프로세스라 한 줄을 통째로 덧붙이고 파일을 열어 두지 않는다.
- 감독이 하위 세션에게 일을 맡길 때마다 `맡김` 줄이 남는다(어느 창에, 어느 요청으로). 감독의 훅이 채널에 알린 것이다.
- 가드와 통로는 자기가 어느 요청을 위해 일하는지 모른다. 그래서 번호 없이 창 이름(`pin`)만 적고, `trace`가 "그 요청의 메시지와 답 사이에, 그 요청으로 일한 창에서 나온 줄"로 끼워 넣는다. 사이는 시각이 아니라 줄 순서로 본다(Windows의 굵은 시계).
- 어느 창의 일이 어느 요청 몫인지는 감독의 훅이 알려 준 대로 적는다. 훅이 없는 세션에서는 채널이 추정한다: 그 창 자기 채팅이 기다리는 요청이 있으면 그것, 없으면 다른 창에서 가장 최근에 기다리기 시작한 요청. "기다리는 요청"이 무엇인지는 아래 "다른 창의 일이 누구 몫인가"에 있다. 데스크톱 앱에서 시킨 일은 번호 없이 남는다.
- 메시지와 답은 앞 200자만 남긴다(대화 기록을 하나 더 만들지 않는다). 2주가 지난 파일은 시작할 때 지운다. `CLAUDE_HAND_LOG=off`면 쓰지 않는다. 테스트와 `selftest`의 훅 확인이 이것을 켠다.

### 중지 버튼

- 채팅창의 "작업 중" 줄에 **중지**가 있다. 누르면 채널이 두 가지를 한다.
  1. **코드로 하는 것:** 그 요청으로 일하는 하위 세션의 가드가 다음 호출을 보고할 때 `stop`으로 답한다. 가드는 그 호출을 막고, 하위 세션에게 "더 부르지 말고 보고하고 끝내라"고 한다. 감독이 무엇을 하든 일은 여기서 멈춘다. 4번 점검에서 정한 방향(규칙을 지침에만 두지 않는다) 그대로다.
  2. **감독에게 알리는 것:** `[claude-hand] 사용자가 … [중지]를 눌렀습니다` 알림을 보낸다. 감독은 그 요청의 일을 더 맡기지 않고, 어디까지 했는지 답한다.
- 중지는 눌린 요청에 묶인다. 그 요청에 답이 오거나 그 채팅창에서 새 메시지를 보내면 풀리고, 감독이 끝내 답하지 않아도 3분 뒤에는 풀린다(창이 막힌 채로 남지 않게). 채팅창에 열린 요청이 없을 때(데스크톱 앱에서 시킨 일) 누르면 그 창의 하위 세션이 멈출 때까지다.
- 무엇을 막는가: 그 채팅창의 창, 그리고 그 요청으로 일하는 다른 창. 어느 창이 그 요청으로 일하는지는 감독이 맡길 때 알려 준 것을 따른다(아래 "다른 창의 일이 누구 몫인가"). 감독의 훅이 없는 세션에서는 자기 채팅이 기다리는 요청이 없는 창을, 기다리는 채팅창이 하나뿐일 때 그 요청 몫으로 본다. 중지를 누른 요청으로는 감독이 일을 새로 맡기는 것도 거부된다.
- 되돌리지는 않는다. 멈추는 것과 되돌리는 것은 다른 결정이고, 되돌리기 버튼이 따로 있다.

### 다른 창의 일이 누구 몫인가 (2026-11-11)

미리보기 그림, 중지가 막을 창, `trace`의 요청 번호가 모두 같은 물음에 달려 있다: 지금 이 창에서 하위 세션이 하는 일은 어느 채팅창의 요청 때문인가. 가드는 창 이름과 하위 세션 ID만 알고, 채널은 누가 언제 일했는지만 본다. 처음 규칙은 "그 창 자기 채팅에 답을 기다리는 요청이 없으면 다른 창에서 시킨 일"이었다. 이 규칙은 감독이 `working: true`를 빼먹으면 무너진다.

- **무슨 일이 났나.** Unity 프로젝트 둘에 각자 일을 시킨다. 감독이 한쪽에 "하고 있어요"를 `working` 없이 보낸다. 채널은 그 요청이 끝난 줄 안다. 그 뒤 그 창이 하는 자기 일은 "다른 창에서 시킨 일"이 되어, 상대 채팅창의 결과에 그 창의 그림이 붙는다. 양쪽에서 일어나면 그림이 서로 바뀐다. 같은 이유로 한쪽에서 누른 중지가 다른 쪽 일을 막고, `trace`는 그 일을 상대 요청 아래에 적는다. 2026-11-10에 `working`을 넣어 고쳤다고 봤지만(TEST-RESULTS-13-WINDOWS), 감독이 그 값을 매번 넣지는 않아 다시 났다.
- **정한 것 1: 그림은 감독이 말한다.** `reply`의 `changed_windows`. 빼먹을 수 없게 도구 정의에서 꼭 적는 값으로 했다. `working`이 선택 값이라 자주 빠진 것에서 배운 것이다. 감독이 적은 창만 붙이고, 채널이 본 것은 쓰지 않는다. 실제 Claude Code(헤드리스, Haiku)로 확인했다: 자기 창만 바꾼 요청에는 `[]`, Blender → Unity에는 그 Unity 창을 적는다.
- **정한 것 2: 일이 보이기 전에 온 답은 끝난 답으로 치지 않는다.** `working` 없는 답이 왔는데 그 요청으로 일한 창이 아직 하나도 없으면, 그 답은 "하는 중"일 수 있다. 그 요청을 그 채팅 몫으로 남겨 둔다(`early`). 일이 시작되기를 2분 기다리고, 일이 보이면 다음 답(결과)이 올 때까지 이어진다. 결과가 끝내 오지 않아도 마지막 동작 뒤 5분이면 끝난다. 2분 안에 아무 일도 없으면 일이 필요 없던 답이었던 것이다. 그래서 `working`이 빠져도 그 창의 일은 자기 요청 몫으로 남고, 중지와 `trace`도 같이 맞는다.
- **정한 것 3: 채널의 추정은 확실할 때만.** 감독이 `changed_windows`를 주지 않은 답에만 쓴다. 다른 창이 일하는 순간에 요청을 기다리는 채팅창이 **하나뿐일 때만** 그 채팅 몫으로 적는다. 둘 이상이 기다리면 누구 몫인지 알 수 없어 아무에게도 붙이지 않는다.
- **정한 것 4: 맡기는 순간에 감독이 말한다.** 위 셋은 추정을 다듬은 것이다. 추정을 없애려면 그 일이 누구 요청인지 아는 쪽이 말해야 하고, 그것은 일을 맡기는 감독이다.
  - 채널 태그에 요청 번호(`request`)를 넣는다. 감독은 하위 세션에게 맡기는 글(`Agent`의 프롬프트, `SendMessage`의 메시지) 첫 줄에 `[요청 <번호>]`를 적는다. 터미널이나 데스크톱 앱에서 온 일은 `[요청 없음]`이다.
  - 감독 세션에만 다는 훅(`tools/supervisor_hook.py`, `Agent`와 `SendMessage` 직전)이 그 번호를 채널에 알린다(`/v1/assign`). 채널은 그 창의 하위 세션이 멈출 때까지 그 창의 일을 그 요청 몫으로 적는다. 중지가 막을 창, `trace`의 요청 번호, 되돌리기 지점의 이름, 감독이 `changed_windows`를 주지 않았을 때의 그림이 모두 이것을 따른다.
  - 번호가 없거나 채널이 모르는 번호면 훅이 그 호출을 거부하고 적는 법을 알려 준다. 지침에만 둔 값은 빠진다는 것을 `working`에서 배웠다. 중지를 누른 요청이면 새로 맡기는 것도 거부한다.
  - 번호를 하위 세션이나 가드에게 넘기지 않은 이유: 가드가 받는 훅 입력에는 감독이 준 글이 없다. 하위 세션이 번호를 되받아 적게 하면 지켜야 할 쪽이 둘이 된다. 맡기는 호출 하나만 붙잡으면 하위 세션은 몰라도 된다.
  - 이 훅은 가드와 반대로 **막지 않는 쪽으로** 실패한다. Claude Hand가 꺼져 있거나 채널이 늦거나 훅에 오류가 나면 맡기기는 그대로 진행되고, 채널은 위의 추정으로 돌아간다. 안전장치가 아니라 기록을 맞추는 장치라서다.
  - 훅은 `preflight`가 쓰는 `.launch-settings.json`(git 제외)을 시작 스크립트가 `--settings`로 넘겨 감독 세션에만 단다. 이 폴더에서 연 다른 Claude Code 세션은 영향을 받지 않는다. `~/.claude-hand/config.json`에 `"supervisor_hook": false`를 넣으면 훅 없이 시작한다.
  - 미리보기는 여전히 `changed_windows`가 먼저다. 일한 창과 바뀐 창은 다르다(Blender는 내보내기만 한다).
- **남는 한계.** 아래는 감독 훅이 없는 세션(시작 스크립트를 거치지 않고 연 감독)에만 해당한다. 구조만으로는 가를 수 없는 경우가 있다: A 채팅창이 일 없는 답을 받고, 2분 안에 B 채팅창이 A의 창을 바꾸는 일을 시킨 경우와, A가 "하는 중"을 `working` 없이 받고 B가 자기 일을 시킨 경우는 채널에게 똑같이 보인다. 추정은 앞의 경우를 A 몫으로 본다(그림 없음). 감독이 `changed_windows`를 적으면 맞게 붙는다. 진행 줄과 되돌리기 지점은 여전히 `working`에 달려 있다: 빠지면 진행 줄이 잠깐 사라졌다 다시 나오고, 일하던 중에 온 답이면 되돌리기 지점이 하나 더 생긴다.

### 파일까지 되돌리기 (2026-11-11)

채팅창의 되돌리기는 Unity의 Undo에 남은 것만 돌렸다. 가져오기 도구가 파일에 한 일(다시 보낸 모델로 바뀐 파일, 새로 들어온 에셋)은 남아서, 되돌렸는데 프로젝트에는 그대로였다.

- **적어 두고 돌린다.** 되돌리기 지점(`ch_checkpoint`의 mark)에 그 요청이 파일에 한 일을 적는다: 덮어쓴 파일과 그 이전 것이 보관된 곳, 새로 들어온 에셋. 되돌릴 때 Undo로 씬을 돌린 다음 이 기록을 거꾸로 따라간다.
  - 덮어쓴 파일은 보관본과 그 `.meta`를 다시 놓고 가져온다. GUID가 같고, 가져오기 설정(머티리얼 연결 포함)도 이전 그대로다.
  - 새로 들어온 에셋은 휴지통으로 옮긴다(`MoveAssetToTrash`). 지우지 않는 이유는, 되돌리기가 사용자의 뜻과 달랐을 때 꺼낼 수 있어야 해서다.
- **누가 적는가.** `ch_import_model`(새 모델의 폴더, 제자리에서 바꾼 파일)과 `ch_import_package`(가져오기 직전에 덮어쓸 파일을 보관하고, 새로 생길 에셋과 폴더를 적는다). 지점이 없을 때(요청 밖에서 부른 경우)는 적을 곳이 없어 전처럼 보관만 하고, 응답이 그렇게 말한다.
- **보관.** `Library/ClaudeHand/replaced/`에 최근 10개. 패키지 하나가 덮어쓴 파일들은 그 전송 이름의 폴더 하나로 센다. 파일이 많은 패키지가 다른 보관본을 밀어내지 않게 하려는 것이다. 오래된 순서는 보관한 때로 본다. 복사본은 원본의 수정 시각을 물려받아서, 수정 시각으로 보면 오래전에 고친 파일의 보관본이 곧바로 지워진다.
- **넣지 않은 것.**
  - MCP for Unity의 도구가 만들거나 고친 파일(스크립트, 머티리얼 에셋, 프리팹 파일). 그 도구들은 파일을 직접 쓰고, Unity는 쓴 뒤에야 안다. 돌리려면 호출 직전에 가드가 대상 파일을 보관하게 해야 한다. 다음 단계다.
  - 한 요청이 여러 창을 바꿨을 때 한 번에 되돌리기. 지금은 창마다 그 창의 되돌리기를 누른다. 요청 번호가 이어졌으니(위 "정한 것 4") 채널이 그 요청으로 일한 창들을 차례로 되돌리게 할 수 있다.
- **알아 둘 것.** 휴지통으로 간 에셋은 Unity의 다시 실행(Ctrl+Y)으로 돌아오지 않는다. 다시 실행하면 씬 오브젝트만 돌아와 없는 프리팹을 가리킨다. 보관본이 10개 밖으로 밀려난 뒤의 되돌리기는 그 파일을 돌리지 못하고, 그렇게 알린다.

### 전체 현황 한눈에 (2026-11-11)

창이 셋을 넘으면 누가 일하는 중이고 누가 막혀 있는지 알 수 없었다. 각 채팅창은 자기 창의 진행만 보여 줬다.

- **새로 모으지 않는다.** 채널은 이미 안다: 어느 창의 하위 세션이 동작을 보고하는지(작업 줄), 멈췄는지(Stop 훅), 어느 채팅창의 요청이 답을 기다리는지, 어느 창에 대화상자가 떠 있는지, 그 일이 누구의 요청인지(요청 번호). 창마다 이것을 상태 하나로 줄인다: `작업 중`, `답 기다림`(요청은 있는데 일하는 하위 세션이 없다: 감독이 답할 차례), `대화상자로 멈춤`, `한가함`. 누구에게도 묻지 않으니 Claude도 토큰도 들지 않는다.
- **어느 창을 보여 주나.** 채팅창이 소식을 묻고 있는 창(최근 90초), 지금 일하는 창, 답을 기다리는 창. 하위 세션 정의만 남아 있는 닫힌 프로젝트는 넣지 않는다.
- **채팅창에는 다른 창만, 바뀔 때만.** 소식을 묻는 응답(`/v1/updates`)에 다른 창들의 줄이 같이 가고, 패널은 자기가 가진 판 번호(`orev`)를 알려 준다. 어느 창의 **상태**가 바뀌면 판이 바뀌어 기다리던 패널이 깨어난다. 도구 호출 하나하나와 흐르는 초는 판에 넣지 않았다. 넣으면 한 창이 일할 때마다 모든 패널이 깨어난다. 그래서 다른 창의 줄에는 경과 시간과 지금 동작이 없고, 그것은 그 창의 채팅창과 `status` 명령에 있다.
- **글자로만.** 상태는 낱말로 가른다. 색으로 가르지 않는다(색을 구분하기 어려운 사용자). 줄을 만드는 곳은 한 군데(`handlib/overview.py`)이고 두 패널은 받은 줄을 그대로 그린다.
- **`claude_hand.py status`**는 같은 것을 경과 시간, 지금 동작과 함께 보여 준다(`/v1/overview`). 감독의 `current_context`에도 창마다 상태가 들어가서 "지금 다들 뭐 하고 있어?"에 답할 수 있다.
- **한계.** Unity는 채팅창이 열려 있어야 "열린 창"으로 잡힌다(일하는 동안은 채팅창이 없어도 잡힌다). 채팅창을 닫은 창은 길게는 90초 동안 더 보인다. 두 패널에 실제로 어떻게 보이는지는 창을 열어 확인하지 않았다(TEST-PLAN 26).

### Unity 안에서 도는 자체 테스트

C# 쪽에는 테스트가 없었다(점검 8번). `ChSelfTest.cs`가 파일을 옮기는 도구를 실제 Unity에서 돌린다: 새로 들어오기, 같은 원본이 다시 왔을 때 제자리에서 바뀌기, 씬 오브젝트가 새 메시를 보이기, 바꾸기 전 파일 남기기, 머티리얼 연결과 풀기, 따로 한 벌, 다시 내보내기, 요청을 되돌리면 바뀐 파일이 돌아오고 들어온 에셋이 휴지통으로 가는 것, 씬 오브젝트를 패키지로 내보냈다가 다시 받는 것과 그 되돌리기(26개 항목). 패키지 가져오기는 에디터가 몇 번 도는 동안 끝나는 일이라, 그 부분은 `EditorApplication.update`에서 이어서 확인한다. `selftest --unity-project <경로>`가 닫혀 있는 프로젝트를 창 없이(`-batchmode -executeMethod`) 열어 실행한다. Blender가 설치돼 있으면 시험 모델은 애드온의 `export_fbx`가 실제로 내보낸 것 두 벌이고, Unity가 내보낸 것을 Blender가 `import_model`로 다시 읽는다. 그래서 Blender → Unity → Blender가 창 없이 한 바퀴 돈다. Unity Test Framework를 쓰지 않은 이유는 프로젝트의 manifest에 `testables`를 넣어야 패키지 테스트가 보이기 때문이다. 이 방식은 프로젝트에 아무것도 더하지 않는다.

통로(`ChBridge`)는 배치 모드에서 열리지 않으므로, 이 테스트가 보는 것은 도구 자체다. 통로를 거친 호출, 채팅창, 중지 버튼의 화면은 열린 에디터에서 봐야 한다(TEST-PLAN 23).

## 다음 단계 (2단계 후보)

- **세션 간 직접 대화:** Claude Code 에이전트 팀(실험 기능)을 쓰거나 Channels로 감독을 거치지 않고 메시지를 주고받는다. 지금은 감독이 중계한다.
- **대화상자 감지의 남은 곳:** Unity(Windows)는 위 "매일 쓰기"에서 만들었다. 남은 것은 Blender(자기 창 안에 그리는 팝업이라 밖에서 안 읽힘), Unity 자체 모달 창(`EditorWindow.ShowModal`, `#32770`이 아님), macOS.
- **macOS·Linux:** 작업 표시줄 알림, 한글 입력창, 대화상자 감지, 창 최소화는 Windows API로만 만들었다.
- **백그라운드 보완:** 실측 결과 Windows에서 막히면 Unity 쪽에서 메인 루프를 깨우는 장치를 넣는다.
