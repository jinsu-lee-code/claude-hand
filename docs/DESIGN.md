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
   - `--default-instance`는 기본값일 뿐이고 포트는 신분증이 아니다(1.0.1에서 알게 됨, MCP for Unity 10.3.0).
     - 고정한 창이 서버의 목록에서 잠깐 빠지면(연결이 꺼짐, 스크립트를 60초 넘게 다시 읽음, 0.3초 안에 답하지 못함) 서버가 다른 창을 고른다: 미들웨어가 "하나뿐인 창"을 골라 세션에 남기고, 보내다 실패한 명령은 응답하는 아무 포트로 다시 보낸다.
     - Unity 쪽은 다시 읽은 뒤 포트를 3초 안에 못 잡으면 6400부터 비어 있는 가장 낮은 포트로 옮긴다. 방금까지 다른 창의 포트였을 수 있고, 연결의 첫인사에는 프로젝트가 없다.
     - Unity 쪽은 새 연결이 오면 기존 연결을 모두 닫는다. 서버는 창을 찾을 때 모든 창의 포트에 붙어 보므로, 창이 둘이면 서버들이 서로의 연결을 끊는다.
   - 그래서 서버에 장치를 건다(`tools/unity_pin/sitecustomize.py`). `sync`가 정의 파일의 서버 환경에 `PYTHONPATH`(그 폴더)와 `CLAUDE_HAND_UNITY_PIN`을 적고, Python이 시작하며 그 파일을 읽는다.
     - 창 찾기(`PortDiscovery.discover_all_unity_instances`, `discover_unity_port`)가 그 해시의 창만 돌려준다. 고를 다른 창이 없다.
     - 응답 확인(`_try_probe_unity_mcp`)은 자기 창의 포트에만, 그것도 연결을 쥐고 있지 않을 때만 붙는다. 남의 연결도 자기 명령도 끊지 않는다.
     - 새 연결(`UnityConnection.connect`)은 그 너머의 창에게 `get_project_info`로 Assets 경로를 물어, 그 해시가 고정한 것일 때만 쓴다. 연결은 붙은 창에 머무니 연결마다 한 번이면 된다. 답이 없거나 알 수 없으면 쓰지 않는다(막는 쪽).
   - 호출마다 `unity_instance`를 넣는 방법은 시험해 보고 버렸다: 미들웨어 쪽은 막지만, 명령이 가는 도중에 창이 다시 읽기를 시작하면 여전히 다른 창으로 다시 보낸다.
   - 환경 변수로 거는 까닭: 등록된 서버 명령(uvx, 버전)을 그대로 쓸 수 있다. 서버의 것이 아닌 Python(uv의 보조 프로세스)에서는 아무것도 하지 않고, 구조가 다른 서버에서는 전부 걸지 못했다고 stderr에 남긴 채 서버를 그대로 띄운다(일부만 거는 일은 없다). 걸렸는지는 `doctor`가 묻는다(`CLAUDE_HAND_UNITY_PIN_CHECK`: 답하고 끝내며 서버는 뜨지 않는다).
   - claude-hand 자신의 창 찾기(`discovery.discover_stdio`)도 포트에 붙지 않는다. 상태 파일이 15초 안에 쓰였는지(연결이 살아 있으면 0.5초마다 쓰인다)로 본다.
   - 연결이 꺼진 채 남는 것은 Claude Hand Tools가 다시 켠다(`ChMcpLink`). MCP for Unity는 다시 읽은 뒤 여섯 번 켜 보고 포기하는데, Unity가 옛 포트를 놓지 않아 그 여섯 번이 다 실패할 수 있다. 2초마다 `StdioBridgeHost.IsRunning`을 이름으로 찾아 보고, 컴파일 · 가져오기 중이 아닌데 20초 넘게 꺼져 있으면 `TransportManager.StartAsync(Stdio)`(없으면 `StartAutoConnect`)를 부른다. 20초는 MCP for Unity의 빠른 시도(0 · 1 · 3 · 5 · 10초)가 먼저 지나가게 둔 것이다. 이름으로 찾으므로 MCP for Unity 없이도 컴파일되고, 구조가 다르면 아무것도 하지 않는다.
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
  - Blender → Unity: FBX로 내보낸다. Unity가 별도 패키지 없이 가져오는 형식이라서다. 0.16까지는 `-Z` 앞, `Y` 위에 텍스처를 파일 안에 넣었는데, 그 텍스처를 Unity는 꺼내 쓰지 않았고 부품마다 회전이 붙었다. 0.17부터는 축을 Blender 그대로 적고 Unity가 변환을 굽고, 재질과 이미지는 파일 옆에 따로 보낸다(아래 "Blender에서 보낸 모델이 깔끔하게 들어오게"). Unity 쪽 `ch_import_model`이 `Assets/ClaudeHand/Imports/<이름>/`으로 복사해 동기로 가져오고 배치한다. 같은 원본이 다시 오면 그 파일을 제자리에서 바꾼다(아래 "0.9에서 더한 것").
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
- **앱의 컨테이너 벗어나기 (Windows, 0.17.0).** 넘어가는 것이 환경변수만이 아니었다. Windows의 Claude 데스크톱 앱은 패키지 앱이라, 그 앱이 띄운 프로세스와 그 자식들은 AppData를 앱 전용 사본을 거쳐 본다: AppData에 새로 만드는 폴더는 `AppData\Local\Packages\<앱>\LocalCache\…`에 생기고, 원래 있던 폴더는 겹쳐 보인다. 이 안에서 uv는 자기 폴더(`AppData\Local\uv`, `AppData\Roaming\uv`)에 실행 환경을 새로 만들지 못한다(방금 만든 폴더를 지우다 os error 32; 만들어 둔 환경을 다시 쓰는 것은 된다). `cmd /c start`로 연 창은 부른 프로세스의 자식이라 감독, 하위 세션, 하위 세션이 `uvx`로 띄우는 MCP for Unity 서버까지 모두 그 안에 들어갔고, 서버의 환경을 새로 만들어야 하는 날에는 Unity 하위 세션에 MCP for Unity 도구가 말없이 빠졌다. 그래서 `start`는 자기가 그런 자리에 있는지 본 뒤(`launch.inside_app_container`: AppData에 폴더를 하나 만들어 실제 위치를 물어본다), 그렇다면 창을 `explorer.exe <시작 스크립트>`로 연다. 셸이 여는 것이라 더블 클릭과 같고, 컨테이너 밖이다. 아니면 전처럼 `cmd /c start`다(내려받은 zip의 파일에는 탐색기가 보안 경고를 띄우므로 늘 탐색기로 열지는 않는다). `doctor`는 등록된 명령에 `--help`를 붙여 MCP for Unity 서버를 실제로 띄워 본다(`checks.unity_server_starts`). 찾는 데 오래 걸린 이유: 같은 명령이 다른 폴더에서는 되고, 단순한 파일 만들기 · 지우기로는 재현되지 않고, 폴더를 쥔 프로세스가 없었다. 갈린 것은 같은 명령을 앱이 띄우지 않은 프로세스(열려 있던 Blender)에서 돌렸을 때였다. `blender-open` · `unity-open`도 같다(0.17.1): 그렇게 연 Blender · Unity는 부른 프로세스의 자식이라 컨테이너 안에 있었다. `install.launch_detached`가 같은 판정을 한 뒤 탐색기로 띄운다. 탐색기는 프로그램에 인자를 넘겨 주지 않으므로, 임시 폴더에 명령을 JSON으로 적고(`argv.json`) 그것을 읽어 띄우는 `launch.cmd`를 만들어 그 파일을 열게 한다(`outside_script`). 명령을 cmd의 글로 옮기지 않는 것은 `--python-expr`에 따옴표 · `&` · `%`가 들어가기 때문이다. 데스크톱 앱 세션에서 이렇게 띄운 프로그램이 AppData에 만든 폴더가 진짜 자리에 생기고 앱 전용 사본에는 없는 것, 인자가 그대로 도착하는 것을 확인했다. 임시 폴더(`claude-hand-launch-*`)는 지우지 않는다(파일 둘, Windows가 치운다).
- **작업 중 표시.** 하위 세션의 guard 훅이 허락한 Unity·Blender 호출마다 채널 서버 `/v1/progress`로 도구와 요약을 보낸다(0.5초 안에 못 보내면 건너뜀). 패널은 입력칸 위에 "작업 중 · N초 · <동작> · N번째 동작" 한 줄을 보여 주고, 답이 오면 지운다. Claude는 이 보고를 보지 않는다. 막대 그림은 사용자 요청으로 뺐다.
- **되돌리기.** 같은 보고에 채널 서버가 "그 요청에서 무언가를 바꿀 수 있는 첫 동작"인지 답하면, guard가 그 창에 되돌리기 지점을 찍는다(요청마다, 창마다 하나. 아래 "실제 감독 세션에서 드러난 것").
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
| 26 | **틀림(2026-11-11, 실제 감독 세션)** 강제로 멈춘 하위 세션도 `Stop` 훅을 부른다고 봤다. 감독이 `TaskStop`으로 끊은 하위 세션은 부르지 않았다 | 그 창은 90초 동안 다른 하위 세션을 막고, 5분 동안 "작업 중"으로 보인다. 중지 뒤에 끊은 경우는 감독의 답이 올 때 채널이 그 요청으로 일하던 창을 끝난 것으로 친다(아래 "실제 감독 세션에서 드러난 것") |
| 27 | **확인됨(Unity 6000.3.25f1, 배치 모드)** 모델의 `.meta`에 적은 `userData`와 라벨로 "전에 들어온 모델"을 다시 찾고, 파일을 덮어쓴 뒤 다시 가져오면 GUID와 씬 오브젝트가 유지된다. `AddRemap`·`RemoveRemap`으로 머티리얼 연결이 바뀐다 | 기록을 `Library/ClaudeHand/` 아래의 목록 파일로 옮긴다 |
| 28 | FBX Exporter의 `ModelExporter.ExportObjects(string, Object[])`를 리플렉션으로 부르면 씬 오브젝트가 FBX로 써진다. 패키지 5.1.6의 소스에서 이름과 인자를 확인했을 뿐 실행해 보지 않았다(시험 프로젝트에 그 패키지가 없다) | 오류를 그대로 보고하고, 사용자가 Unity 메뉴(GameObject > Export To FBX)로 내보낸 파일을 `import_model`에 직접 준다 |
| 29 | **확인됨(2026-11-11, 실제 감독 세션)** 감독이 `[중지]` 알림을 받으면 그 요청의 일을 더 맡기지 않고 `reply`로 답한다(지침). 두 번 모두 감독이 하위 세션을 `TaskStop`으로 끊고 10~25초 안에 어디까지 했는지 답했다. 하위 세션의 다음 호출보다 감독이 빨라서, 채널이 호출을 막는 쪽은 쓰일 틈이 없었다 | 하위 세션의 다음 호출은 채널이 막으므로 일은 멈춘다. 답이 없으면 1분 뒤 "답이 없다" 알림이 가고, 중지는 3분 뒤 풀린다 |
| 30 | 한 창의 일이 어느 요청 몫인지에 대한 채널의 추정(자기 채팅이 기다리는 요청, 없으면 다른 창의 가장 최근 요청)이 대체로 맞는다. 중지가 막을 창과 `trace`의 요청 번호가 여기에 달려 있다. 2026-11-11에 "일이 보이기 전에 온 답은 끝난 답이 아니다"를 더했고, 같은 날 감독의 훅이 맡길 때마다 요청 번호를 알려 주게 해서 이 추정은 훅이 없는 세션에서만 쓰인다(위 "다른 창의 일이 누구 몫인가", 가정 32) | 틀리면 중지가 데스크톱 앱에서 시킨 다른 일까지 3분 동안 막을 수 있다 |
| 32 | **확인됨(Claude Code 2.1.321, 헤드리스)** 감독 세션의 `Agent`·`SendMessage` 호출 직전에 PreToolUse 훅이 불리고, 입력에 `subagent_type`·`prompt`와 `to`·`message`가 있다. `--settings`로 넘긴 파일의 훅이 그 세션에 적용된다. 훅이 거부하면 감독은 이유를 읽고 `[요청 <번호>]`를 적어 다시 맡긴다. 그 뒤 하위 세션의 가드가 보고한 동작이 그 요청 번호로 기록된다. 2026-11-11에 실제 감독 세션에서도 확인했다: `SendMessage`로 이어 맡기는 호출이 한 번 거부된 뒤로 감독은 스스로 번호를 적었다. 그때 드러난 것: 이어 쓰는 감독은 예전 하위 세션을 **ID로** 부르고, 훅 입력에는 그 ID가 어느 하위 세션인지가 없다. Claude Code가 세션 기록 옆에 두는 `subagents/agent-<ID>.meta.json`의 `agentType`으로 찾는다(문서에 없는 자리다) | 그 파일을 못 읽으면 채널이 기억하는 ID로 찾고(그 창에서 한 번이라도 일한 ID), 그것도 없으면 그 맡기기는 번호 없이 지나가 채널이 전의 추정으로 돌아간다. 훅이 불리지 않거나 감독이 번호를 못 적으면 채널이 전의 추정으로 돌아간다. 감독이 거부에서 헤어나지 못하면 `~/.claude-hand/config.json`에 `"supervisor_hook": false`를 넣고 다시 켠다(훅 없이 시작) |
| 33 | **확인됨(Unity 6000.3.25f1)** `Undo.RevertAllDownToGroup`이 창 없이도 동작한다. 보관한 파일과 그 `.meta`를 다시 놓고 `ImportAsset`하면 이전 가져오기 설정(머티리얼 연결)으로 돌아간다. `ImportPackage`가 덮어쓴 에셋도 같은 방법으로 돌아온다. 2026-11-11에 열린 에디터에서도 확인했다(버튼 대신 같은 도구로): 다시 보낸 모델을 되돌리니 파일이 처음 것과 바이트까지 같았다. 들어온 에셋을 `Assets` 밖으로 옮기고 `AssetDatabase.Refresh`를 부르면 에셋 목록에서 빠진다 | 못 옮긴 에셋은 그대로 두고 `problems`로 알린다. 지우는 쪽으로 넘어가지 않는다 |
| 31 | **틀림(2026-11-11, 실제 감독 세션)** 감독이 결과 답마다 `changed_windows`를 적는다고 봤다. 헤드리스 Claude Code(Haiku)는 적었지만, 며칠째 이어 쓰는 실제 감독 세션은 한 번도 적지 않았다. 도구 정의의 "꼭 적는 값"은 강제가 아니고, 그 세션은 두 값만 넣어 부르던 버릇대로 부른다 | 빠뜨린 답에는 감독의 훅이 알려 준 "그 요청으로 일한 창" 가운데 실제로 무언가를 바꾼 창을 쓴다. 그날 요청 열넷에서 그림은 모두 맞게 붙었다 |

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
- **남는 한계.** 아래는 감독 훅이 없는 세션(시작 스크립트를 거치지 않고 연 감독)에만 해당한다. 구조만으로는 가를 수 없는 경우가 있다: A 채팅창이 일 없는 답을 받고, 2분 안에 B 채팅창이 A의 창을 바꾸는 일을 시킨 경우와, A가 "하는 중"을 `working` 없이 받고 B가 자기 일을 시킨 경우는 채널에게 똑같이 보인다. 추정은 앞의 경우를 A 몫으로 본다(그림 없음). 감독이 `changed_windows`를 적으면 맞게 붙는다. 진행 줄은 여전히 `working`에 달려 있다: 빠지면 진행 줄이 잠깐 사라졌다 다시 나온다. 되돌리기 지점은 그 창 자기 채팅의 요청이면 `working`과 상관없이 하나다(아래 "실제 감독 세션에서 드러난 것").

### 실제 감독 세션에서 드러난 것 (2026-11-11)

위의 것들은 자동 테스트와 헤드리스 Claude Code로 확인했다. 실제 감독 세션(며칠째 `--resume`으로 이어 쓰는 세션)에 채팅창처럼 요청 열넷을 넣어 보니, 그림은 한 번도 섞이지 않았지만 가정 몇 개가 틀려 있었다. 공통된 뿌리는 둘이다: 감독은 **이어 쓰는 세션**이고(예전 하위 세션, 예전 버릇), **"하는 중" 답에 `working`을 한 번도 붙이지 않는다**(열세 번 가운데 열세 번).

- **이어 쓰는 하위 세션의 ID.** 감독은 같은 창의 일을 `SendMessage`로 예전 하위 세션에게 이어 맡기고, 그때 이름이 아니라 ID를 쓴다. 채널은 그 ID를 "그 창에서 일하는 것을 본 하위 세션"으로만 알았으니, 방금 켠 채널에게는 모르는 ID였다. 훅은 "창의 하위 세션이 아님"으로 알고 통과시켰고, Claude Hand를 켠 뒤 창마다 첫 맡기기가 번호 없이 지나갔다. 이제 두 군데서 찾는다. 훅이 Claude Code가 세션 기록 옆에 두는 파일에서 그 ID의 하위 세션 이름을 읽어 채널에 함께 알리고(가정 32), 채널은 창마다 마지막으로 일한 하위 세션의 ID를 그 채팅의 기록 파일에 함께 남겨 다시 켠 뒤에도 안다. 기억한 ID를 채널 태그의 `agent_id`로 내보내지는 않는다. 새로 연 감독 세션에게 다른 세션의 하위 세션을 가리키게 되기 때문이다.
- **"하는 중" 답 뒤의 중지.** "일이 보이기 전에 온 답은 끝난 답이 아니다"(정한 것 2)를 누구 몫인지 정하는 곳에만 썼고, 중지는 "아직 답이 없는 요청"만 봤다. 그래서 `working` 없는 "하는 중" 답 뒤에 누른 중지는 어느 요청의 것도 아니었다: 기록에 요청 번호 없이 남고, 감독에게 가는 알림에 요청 글이 없고, 그 요청으로 일하는 **다른 창**은 막지 않고, 같은 요청을 다시 맡기는 것도 거부되지 않았다. 중지, 중지가 덮는 창, 맡기기 거부, 대화상자 안내가 모두 같은 물음(`_asking`: 이 채팅이 아직 결과를 기다리는 요청)을 쓰게 했다.
- **끊긴 하위 세션.** 중지를 받으면 감독은 하위 세션을 끊고 답한다. 끊긴 하위 세션은 Stop 훅을 부르지 않아서(가정 26), 채널은 그 창이 아직 일하는 줄 알았다: 5분 동안 "작업 중"으로 보이고 90초 동안 다른 하위 세션을 막았다. 중지를 누른 요청에 답이 오면, 그 요청으로 일하던 창을 끝난 것으로 친다(작업 줄, 창 잠금, 맡김 기록). 중지 뒤의 답은 일이 없었어도 "하는 중"으로 보지 않는다.
- **답 기다림.** 다른 창의 상태 줄이 "하는 중" 답과 첫 동작 사이, 한 하위 세션이 끝나고 다음이 시작하기 전에 "한가함"으로 보였다. "하는 중"으로 드러난 요청(그 뒤에 일이 맡겨졌거나 보인 요청)은 결과가 올 때까지 "답 기다림"이다. 일이 필요 없던 답은 전처럼 바로 "한가함"이다. 맡김은 기다리는 패널을 바로 깨운다(처음에는 깨우지 않아, 패널이 다음에 물을 때까지 길게는 20초 늦게 바뀌었다).
- **읽기만 한 창.** 그림은 "그 요청으로 일한 창"에 붙었는데, 중지로 멈춘 요청에서 Unity는 씬을 읽기만 했다. 창을 그대로 두는 도구(`ch_scene_snapshot`, `ch_editor_status`, `ch_capture`, 내보내기, `get_`·`read_`·`find_`·`list_`로 시작하는 것)만 쓴 창은 바뀐 창으로 치지 않는다.
- **`working`은 답마다 말하게 한다.** 감독이 번호 없이 맡겼다가 한 번 거부된 뒤로는 그 세션 내내 스스로 번호를 적었다. `working`에는 먼저 약한 방법을 써 봤다: "하는 중"을 결과처럼 보낸 것이 드러나면 그 결과의 `reply` 응답에 "다음에는 `working: true`로"를 붙였다. 다시 켠 감독 세션에서 세 번 붙였고 세 번 다 다음 "하는 중"에 `working`이 없었다. 도구 정의의 "꼭 적는 값"도 강제가 아니었다(가정 31). 그래서 번호와 같은 방법으로 바꿨다: `working`이 없는 답은 채팅창에 보내지 않고 "하는 중이면 `true`, 결과면 `false`로 같은 글을 다시 보내라"고 돌려보낸다. 채널이 "하는 중"인지 추측하는 대신 감독이 고르게 하는 것이다. 다섯 번까지만 돌려보낸다. 끝내 빼먹는 감독에게 모든 답을 두 번 보내게 하지 않고, 그 뒤로는 전처럼 채널이 감당한다(정한 것 2). 데스크톱 앱에서 시킨 일의 기록(`origin: desktop`)에는 묻지 않는다. 한 번 더 켠 감독 세션에서는 돌려보낼 일이 없었다: 도구 정의와 규칙이 바뀐 것만으로 "하는 중" 답 여섯 번에 모두 `working: true`가 붙었다. 일부러 빼고 보내게 한 답은 돌려보내자 `working: false`로 다시 왔다.
- **지점은 요청마다, 창마다 하나, 바꾸는 첫 동작 앞에.** 되돌리기 지점을 "작업 줄이 새로 생길 때"에 찍었더니 둘이 어긋났다. Unity 창이 Blender 채팅창의 요청으로 일하는 중에 Unity 채팅창이 자기 일을 시켰고, 감독이 "지금 일이 끝나면 할게요"라고 답하자 그 답이 Unity 창의 작업 줄을 지웠다. 이어지는 동작은 새 요청의 첫 동작처럼 보여 같은 요청에 지점이 하나 더 찍혔다. 또 씬을 읽기만 하고 중지된 요청이 아무것도 되돌리지 않는 지점을 남겼다. 이제 지점은 "이 창이 지금 어느 요청으로 일하는가"에 묶는다: 감독이 알려 줬거나 그 창 자기 채팅의 요청이면 그 요청에 하나이고, "하는 중" 답이나 하위 세션이 여러 번 이어 일하는 것과 상관없다. 누구 요청인지 모를 때(데스크톱 앱에서 시킨 일, 훅 없는 세션의 추정)만 전처럼 작업 줄에 묶는다. 그리고 창을 그대로 두는 도구(읽기, 내보내기)는 지점을 찍지 않고, 무언가를 바꿀 수 있는 첫 동작 앞에 찍는다. 덕분에 "취소해줘"를 받은 하위 세션이 씬이나 지점 목록을 먼저 읽어도 되돌릴 것이 남는다.
- **코드를 돌리는 도구로 읽기만 한 요청.** `execute_code`와 `execute_blender_code`는 아무 코드나 돌리므로 이름만으로는 무엇을 하는지 알 수 없어, 바꿀 수 있는 동작으로 쳤다. 그래서 "오브젝트 위치만 알려줘"에 하위 세션이 코드로 읽으면 되돌릴 것이 없는 지점이 남았다. 채팅창의 되돌리기에는 그 요청의 글이 떴고, 처음 누르면 아무 일도 없었다(한 번 더 눌러야 앞 요청이 돌아갔다). 두 앱에서 다르게 고쳤다. 앱마다 믿을 수 있는 신호가 다르기 때문이다.
  - **Unity는 찍은 뒤에 본다.** `ChCheckpoint`의 지점은 "그 뒤로 바뀐 것이 없음"을 들고 있다가 에디터가 변경을 알리면 내려놓는다. 알림은 `ObjectChangeEvents`(Undo나 SerializedObject를 거친 오브젝트 생성·삭제·구조·속성 변경, 에디터의 실행 취소), 에셋이 들어오거나 나가거나 옮겨진 것, 가져오기 도구의 파일 기록이다. 바뀐 것이 없는 지점은 버튼과 `list`에 나오지 않고, `undo`가 건너뛰고, 다음 요청의 지점이 그 자리를 대신한다. 무엇으로 읽었든 맞는다. Undo를 거치지 않은 변경은 알림이 없어 "바뀐 것 없음"으로 남는데, 그런 변경은 되돌리기로도 돌아가지 않는다. 씬을 읽는 것, 선택을 바꾸는 것, `ch_capture`는 알림이 없다. `hierarchyChanged`는 쓰지 않는다. Unity 창이 뒤에 있으면 오지 않다가 사용자가 Unity를 누를 때 한꺼번에 와서, 그때의 최신 지점(읽기만 한 요청의 것)을 바뀐 것으로 만든다. 알림은 변경이 일어난 에디터 틱의 끝에 온다. 한 틱 안에서 찍고, 바꾸고, 또 찍으면 앞 지점이 빈 것으로 보이지만, 지점은 서로 다른 요청의 것이라 그 사이에 하위 세션의 호출이 여러 번 오간다. 자체 테스트만 틱을 나눠서 본다.
  - **Blender는 돌리기 전에 코드를 읽는다.** Blender에서는 "무엇이 바뀌었나"를 Python으로 빠짐없이 알 수 없다. depsgraph 알림은 이름 바꾸기, 사용자 속성, 렌더 설정 같은 것을 놓치는데 Blender의 실행 취소는 그런 것도 돌린다. 놓친 변경의 지점을 버리면 그 요청은 따로 되돌릴 수 없게 된다. 그래서 가드가 `execute_blender_code`의 코드를 `ast`로 읽고(`handlib/looks.py`), 읽기만 하는 것이 확실할 때 채널에 `looks`로 알린다. 채널은 그 호출을 읽는 도구처럼 다룬다(지점 없음, 바뀐 창으로 치지 않음). 확실하다는 것은 허용한 것만으로 이루어졌다는 뜻이다: 속성 읽기, 무엇에 불러도 아무것도 바꾸지 않는 함수·메서드, 코드가 직접 만든 리스트·딕셔너리 채우기. 속성에 넣기, `bpy.ops`, 모르는 메서드, `del`, 클래스 정의는 모두 "바꿀 수 있음"이다. 제자리 연산(`+=`)은 코드가 만든 값에만 허용한다. 오브젝트에서 꺼낸 Vector에 `+=`를 하면 오브젝트가 바뀌기 때문이다. 그래서 틀려도 지점이 하나 더 찍히는 쪽으로만 틀린다. 실제 감독 세션에서 Blender 하위 세션이 돌린 코드 32가지 가운데 9가지가 읽기만 하는 것으로 읽혔다(파일 경로와 저장 여부, 오브젝트·머티리얼·모디파이어 조회). 나머지에는 실제 변경과, `bpy.ops`를 언급한 조회 하나가 있었다. 알아보지 못한 읽기 코드는 전처럼 빈 지점을 남긴다. 일부러 숨긴 코드를 막는 장치는 아니다. 하위 세션은 어차피 씬을 바꿀 수 있고, 여기서는 지점을 찍을지만 정한다. 채팅 애드온의 내보내기(`claude_hand_chat.export_fbx`)는 허용한 것에 들어간다(0.17.1): 파일은 교환 폴더에 쓰고, 선택 · 모드 · 굽느라 건드린 것은 제자리로 돌려놓고, 창에 남기는 것은 보낸 이름을 적는 사용자 속성(`claude_hand_sent_as`)뿐이다. 그 속성은 다시 보낼 때 같은 모델로 알아보려는 것이라 되돌릴 대상이 아니다. 넣지 않았을 때는 고치지 않고 보내기만 한 요청에도 Blender에 지점이 찍혀, 버튼이 "이 창 +"라고 했다. 애드온의 다른 함수(따라가기 켜기, 가져오기, 되돌리기)는 전처럼 "바꿀 수 있음"이다.
  - Unity의 `execute_code`(C#)는 읽지 않는다. 지점은 위의 방법으로 건너뛰지만, 코드로 읽기만 한 Unity 창이 다른 채팅창의 답에 그림으로 붙을 수는 있다.
- **작업 줄은 그 창의 일에 묶는다.** 답이 오면 그 채팅창의 작업 줄이 사라지는데, 그 창이 다른 채팅의 요청으로 일하는 중이면 지우지 않는다. 반대로 다른 채팅의 요청으로 일하던 하위 세션이 끝나면 그 창의 작업 줄을 바로 지운다. 답은 요청한 채팅창으로 가므로 이 창의 줄을 지워 줄 답이 오지 않고, 줄이 남아 있는 동안에는 되돌리기 버튼도 가려진다. 감독이 바뀐 창에도 한마디 남겨 줄 때만 지워지던 것이다.

### 파일까지 되돌리기 (2026-11-11)

채팅창의 되돌리기는 Unity의 Undo에 남은 것만 돌렸다. 가져오기 도구가 파일에 한 일(다시 보낸 모델로 바뀐 파일, 새로 들어온 에셋)은 남아서, 되돌렸는데 프로젝트에는 그대로였다.

- **적어 두고 돌린다.** 되돌리기 지점(`ch_checkpoint`의 mark)에 그 요청이 파일에 한 일을 적는다: 덮어쓴 파일과 그 이전 것이 보관된 곳, 새로 들어온 에셋. 되돌릴 때 Undo로 씬을 돌린 다음 이 기록을 거꾸로 따라간다.
  - 덮어쓴 파일은 보관본과 그 `.meta`를 다시 놓고 가져온다. GUID가 같고, 가져오기 설정(머티리얼 연결 포함)도 이전 그대로다.
  - 새로 들어온 에셋은 프로젝트에서 뺀다: `.meta`와 함께 `Library/ClaudeHand/removed/<뺀 때>_<이름>/`으로 옮기고 최근 10개를 남긴다. 지우지 않는 이유는, 되돌리기가 사용자의 뜻과 달랐을 때 꺼낼 수 있어야 해서다. 처음에는 휴지통으로 보냈다(`MoveAssetToTrash`). 그런데 Windows 휴지통은 드라이브마다 "바로 삭제"로 설정할 수 있고, 개발하던 PC가 그랬다. 채팅창은 "휴지통으로 옮겼어요"라고 했지만 휴지통은 비어 있었다(2026-11-11). 꺼낼 수 있다는 약속을 운영체제 설정에 맡길 수 없어 보관을 직접 한다.
- **누가 적는가.** `ch_import_model`(새 모델의 폴더, 제자리에서 바꾼 파일)과 `ch_import_package`(가져오기 직전에 덮어쓸 파일을 보관하고, 새로 생길 에셋과 폴더를 적는다). 지점이 없을 때(요청 밖에서 부른 경우)는 적을 곳이 없어 전처럼 보관만 하고, 응답이 그렇게 말한다.
- **보관.** `Library/ClaudeHand/replaced/`에 최근 10개. 패키지 하나가 덮어쓴 파일들은 그 전송 이름의 폴더 하나로 센다. 파일이 많은 패키지가 다른 보관본을 밀어내지 않게 하려는 것이다. 오래된 순서는 보관한 때로 본다. 복사본은 원본의 수정 시각을 물려받아서, 수정 시각으로 보면 오래전에 고친 파일의 보관본이 곧바로 지워진다.
- **넣지 않은 것.**
  - MCP for Unity의 도구가 만들거나 고친 파일(스크립트, 머티리얼 에셋, 프리팹 파일). 그 도구들은 파일을 직접 쓰고, Unity는 쓴 뒤에야 안다. 돌리려면 호출 직전에 가드가 대상 파일을 보관하게 해야 한다. 0.13.0에서 넣었다(아래 "어떤 도구가 바꿨든 파일까지").
  - 한 요청이 여러 창을 바꿨을 때 한 번에 되돌리기. 지금은 창마다 그 창의 되돌리기를 누른다. 요청 번호가 이어졌으니(위 "정한 것 4") 채널이 그 요청으로 일한 창들을 차례로 되돌리게 할 수 있다. 0.14.0에서 넣었다(아래 "여러 창을 한 번에 되돌리기").
- **지점은 요청마다 하나.** 가드가 그 요청의 첫 동작 직전에 찍고, 이름은 사용자가 채팅창에 친 글이다. 하위 세션이 `ch_checkpoint`의 `mark`를 직접 부르면 지점이 하나 더 생긴다. 되돌리기 버튼은 가장 새 지점을 보여 주므로 "Replace CH_Cylinder (4 tall)" 같은 하위 세션이 지은 이름이 뜨고, 파일 기록도 그 지점에 붙고, 한 번 누른 뒤에는 아무것도 되돌리지 않는 버튼이 남는다. 지침은 "저절로 찍힌다"고만 했고, 며칠째 이어 쓰는 하위 세션은 버릇대로 불렀다(2026-11-11, 요청 여섯에서). 그래서 Claude Hand가 켜져 있는 동안에는 가드가 그 호출을 막고 "이미 찍혀 있으니 이어서 하라"고 답한다. 그 호출이 요청의 첫 동작이면 가드가 요청 이름으로 지점을 찍은 다음 막는다. Claude Hand가 꺼져 있으면 저절로 찍히는 지점이 없으니 막지 않는다.
- **알아 둘 것.** 빠진 에셋은 Unity의 다시 실행(Ctrl+Y)으로 돌아오지 않는다. 다시 실행하면 씬 오브젝트만 돌아와 없는 프리팹을 가리킨다. 보관본이 10개 밖으로 밀려난 뒤의 되돌리기는 그 파일을 돌리지 못하고, 그렇게 알린다.

### 어떤 도구가 바꿨든 파일까지 (2026-11-16, 0.13.0)

0.11.0의 되돌리기는 claude-hand의 가져오기 도구가 한 일만 돌렸다. 하위 세션이 MCP for Unity의 도구로 스크립트를 고치면, 되돌려도 스크립트는 그대로였다.

- **먼저 확인한 것.** MCP for Unity 10.3.0에서 파일을 쓰는 도구 소스가 31개다. 도구마다 규칙을 두면 그쪽이 바뀔 때마다 깨진다. 그래서 도구가 아니라 Unity가 무엇을 알려 주는지를 봤다(Unity 6000.3, 창 없이 파일 동작 24가지).
  - 미리 알려 주는 것: 새 에셋(`OnWillCreateAsset`. 직접 쓴 새 파일도 `.meta`를 만들 때 알린다), Unity를 거친 저장(`OnWillSaveAssets`. 머티리얼, 프리팹, 가져오기 설정. 그 순간 디스크에는 이전 내용이 그대로다), 옮기기와 이름 바꾸기(`OnWillMoveAsset`), 삭제(`OnWillDeleteAsset`).
  - 알려 주지 않는 것: 있는 파일을 직접 덮어쓰기(`File.Replace`), 직접 지우기. 가져온 뒤에야 안다. MCP for Unity는 스크립트와 셰이더를 이렇게 쓴다.
  - `OnWillCreateAsset`은 있는 씬을 다시 저장할 때도 온다. 그래서 "알려 준 그 경로가 아직 디스크에 없을 때"만 새 에셋으로 본다.
  - 삭제는 가로챌 수 있다. `OnWillDeleteAsset`에서 에셋과 `.meta`를 다른 곳으로 옮기고 `DidDelete`를 돌려주면 Unity는 지운 것으로 처리한다. 되놓으면 GUID가 그대로다.
- **원칙: 그 요청이 일하는 동안 생긴 변화만 그 요청 몫이다.** 씬 쪽(Unity Undo)은 지점 뒤의 모든 것을 돌린다. 파일까지 그렇게 하면, 요청이 끝난 뒤 사용자가 IDE에서 고친 코드가 되돌리기 한 번에 사라진다. "일하는 동안"은 가드가 프로젝트의 `Library/ClaudeHand/at-work.json`에 적는다: 호출마다 요청 이름과 시각을 새로 적고, 하위 세션이 끝나면(Stop 훅) 끝난 시각을 적는다. Unity는 끝난 뒤 15초까지를 그 요청으로 친다(방금 쓴 스크립트를 가져오는 시간). Stop이 오지 않은 하위 세션은 마지막 호출 뒤 5분이면 끝난 것으로 본다.
- **요청 이름.** 채널이 가드에게 답할 때 그 창이 지금 어느 요청으로 일하는지 이름을 준다(`point`): 요청 번호, 누구 요청인지 모를 때는 작업 줄이 시작된 때. 가드는 그 이름으로 지점을 찍고(`ch_checkpoint mark`의 `request`), 같은 이름으로 `at-work.json`을 적는다. Unity는 가장 새 지점의 이름과 일하는 중인 요청의 이름이 같을 때만 파일 변화를 적는다. Claude Hand가 꺼져 있으면 가드가 이름을 지어 쓴다(`local-…`): 일이 5분 넘게 쉬지 않는 동안 한 요청이고, 하위 세션이 지점을 직접 찍으면 새 요청이다.
- **누가 적는가.**
  - Unity 패키지(`ChCheckpoint`의 `WillCreate`·`WillSave`·`WillDelete`·`WillMove`): 새 에셋은 `added`. Unity를 거친 저장은 쓰기 직전에 파일과 `.meta`를 복사하고 `replaced`. 삭제는 에셋을 `Library/ClaudeHand/removed/req_<요청>/`으로 옮기고 `deleted`. 옮기기는 `moved`.
  - 가드(`tools/handlib/keeps.py`): 호출 직전에, 도구 입력에 적힌 짧은 글 가운데 `Assets` 아래에 실제로 있는 파일을 가리키는 것을 찾아 복사한다. 경로, `unity://path/…` 주소, 절대 경로, `Assets/`를 뺀 경로, 나란히 적힌 폴더와 이름(확장자 없는 이름이면 그 이름의 파일), 이름만 있으면 MCP for Unity의 기본 폴더(`Scripts`, `Shaders`, `Scenes`). 도구 이름은 보지 않는다. 복사본은 `Library/ClaudeHand/replaced/req_<요청>/`에, 무엇을 어디에 두었는지는 그 폴더의 `kept.jsonl`에 한 줄씩 남긴다. 요청마다 파일당 한 번이라, 복사본은 그 요청 전의 파일이다. 파일 복사뿐이라 Unity가 컴파일 중이어도 된다. 읽기만 하는 도구와 `ch_*`(자기가 보관한다)는 건너뛴다.
  - 읽기만 한 호출이 이름 붙인 파일도 복사된다(도구 이름을 보지 않으니 알 수 없다). 되돌릴 때 내용이 같으면 아무 일도 하지 않으므로, 드는 것은 복사 한 번이다.
- **못 잡는 것은 알린다.** 일하는 동안 가져오기가 끝난 파일 가운데 그 요청 뒤에 쓰였고(수정 시각), 새 에셋도 아니고 복사본도 없는 것은 직접 덮어쓴 것이다. `changed`로 적어 둔다. 가드가 복사해 뒀으면 되돌릴 때 그것으로 돌리고, 없으면(코드 실행으로 덮어쓴 경우) "이전 내용이 보관되지 않아 그대로"라고 이름과 함께 알린다(`unkept`). 조용히 넘어가지 않는다.
- **되돌리는 순서.** Unity Undo로 씬을 돌린 다음, 패키지의 기록과 가드의 복사본을 시각 순으로 한 줄로 세워 새것부터 거꾸로 간다: 옮긴 것은 제자리로(Unity의 `MoveAsset`으로. 에셋 이름이 파일 안에도 적혀 있어서다), 지운 것은 되놓고, 바꾼 것은 복사본으로(내용이 다를 때만), 만든 것은 `removed/undo_<때>/`로 뺀다. 한 파일의 복사본이 여럿이면 거꾸로 가다가 가장 오래된 것에서 끝난다. 그 요청이 스스로 만든 파일의 복사본은 건너뛴다(돌아갈 "이전"이 없다). 복사본은 원본의 수정 시각을 물려받으므로, 되놓은 파일의 수정 시각을 지금으로 바꾼다(에디터와 IDE가 새 파일로 알아보게).
- **보관.** `replaced/`와 `removed/` 모두 요청 하나(또는 되돌리기 한 번)가 폴더 하나이고, 최근 10개를 남긴다. 일하는 동안에는 가져오기 도구가 바꾼 파일도 그 요청의 폴더로 간다. 요청 하나가 파일을 많이 바꿔도 자기 복사본을 밀어내지 않게 하려는 것이다.
- **하지 않는 것.** 열려 있는 씬의 파일(에디터 밑에서 바꾸면 다시 읽을지 묻는 대화상자가 뜬다. 전처럼 다시 저장해야 바뀐다), `Assets` 밖(`Packages`, `ProjectSettings`), 64 MB가 넘는 파일의 미리 복사(`changed`로 적고 알린다), Blender 쪽 파일.
- **삭제를 가로채는 범위.** 요청이 일하는 동안에만이다. 그동안에는 사용자가 Project 창에서 직접 지운 것도 `removed`로 가고 그 요청의 기록에 붙는다(되돌리면 돌아온다). 잃는 쪽이 아니라 남는 쪽으로 틀린다. 옮기다 실패하면(파일이 잠겨 있음) Unity가 평소대로 지운다. 하위 세션 지침에는 "도구가 휴지통이라고 답해도 실제로는 `removed`에 있다"를 넣었다. Unity 6000.3에서만 확인했다.
- **컴파일 중에도 지점을 찍는다.** 스크립트를 고친 요청 바로 다음 요청은 Unity가 컴파일하거나 스크립트를 다시 불러오는 중에 시작된다. 전에는 그때 `mark`가 `EDITOR_BUSY`로 거절되거나 통로가 잠깐 닫혀 있어 지점 없이 지나갔고, 그 요청의 변경은 앞 요청과 함께만 되돌릴 수 있었다. `mark`는 컴파일 중에도 받고, 가드는 통로가 없을 때 2초 간격으로 다섯 번까지 다시 부른다(한 번에 3초 넘게 걸린 실패는 에디터가 멈춘 것이라 다시 부르지 않는다).
- **이 패키지가 잠깐 두는 파일은 적지 않는다.** `ch_export_objects`는 프리팹을 프로젝트 안에 잠깐 만들었다가 패키지에 넣고 지운다. 요청이 일하는 중이면 이것도 "만든 에셋", "지운 에셋"으로 적혀, 되돌릴 때 그 폴더를 되살렸다가 다시 빼게 된다. 내보내는 동안은 적지 않고 삭제도 넘겨받지 않는다(`ChCheckpoint.Unnoted`).
- **확인.** Python 테스트(경로 찾기, 보관, 일하는 중 표시, 가드). 창 없는 Unity 자체 테스트에 여덟 항목(만든 에셋 빼기, Unity 저장 복구, 직접 덮어쓴 파일 복구, 지운 에셋 되살리기, 이름 바꾼 에셋 제자리, 내보내기가 잠깐 둔 파일은 적지 않기, 일이 끝난 뒤 만든 에셋은 남기기): 직접 덮어쓴 파일의 복사본은 `selftest`가 Unity를 열기 전에 가드의 코드로 만든다. 스크립트는 컴파일과 스크립트 다시 불러오기가 끼어서 자체 테스트에 넣지 않았다(시험 스크립트가 프로젝트에 남으면 컴파일을 깨뜨린다). 대신 한 번 따로 돌려 봤다: 창 없는 Unity에서, 호출마다 실제 가드를 프로세스로 실행하고(Claude Code가 하듯이) MCP for Unity 10.3.0의 도구 코드를 그대로 불러 스크립트를 만들고(`manage_script create`), 고치고(`update`), 지우고(`delete`), 머티리얼과 셰이더를 만들고(`manage_asset`, `manage_shader`), 머티리얼 이름을 바꿨다. 컴파일과 스크립트 다시 불러오기를 여러 번 거친 뒤 되돌리자 모두 돌아왔고(고친 스크립트는 이전 내용과 이전 컴파일 결과로, 지운 스크립트는 같은 GUID로), 가드가 끝났다고 알린 17초 뒤에 직접 만든 스크립트는 남았다. 가드는 호출마다 0.16~0.19초 걸렸다. Claude Hand가 꺼진 경우(가드가 요청 이름을 짓는 경우)로 돌렸다. 채널이 이름을 주는 경로는 양쪽의 자동 테스트로만 봤고, 열린 에디터와 실제 감독 세션으로는 아직 보지 않았다(TEST-PLAN 27).
- **보다가 알게 된 것.** MCP for Unity 10.3.0의 `manage_asset`은 옮기기·이름 바꾸기에 성공하고도 "MoveAsset call failed unexpectedly"라고 답한다(`AssetDatabase.MoveAsset`은 성공하면 빈 문자열을 주는데, 그 코드는 빈 문자열을 실패로 본다). 실제로는 옮겨져 있고, 되돌리기도 그것을 제자리로 돌린다.

### 여러 창을 한 번에 되돌리기 (2026-11-23, 0.14.0)

Blender 채팅창에서 "의자를 고쳐서 Unity로 보내줘"라고 하면 Blender와 Unity가 함께 바뀐다. 되돌리기 버튼은 창마다 그 창의 가장 새 지점만 알아서, Blender에서 누르면 Blender만 돌아가고 Unity에는 의자가 남았다. Blender가 내보내기만 한 요청이면 더 나빴다: Blender 채팅창의 버튼은 그 요청과 상관없는 앞 요청을 가리켰다.

- **지점에 요청 이름이 있다.** 0.13.0에서 Unity의 지점에 요청 번호를 실었고(파일 때문에), 이번에 Blender의 지점에도 싣는다. 가드가 지점을 찍을 때 채널이 준 번호를 넘긴다. 번호를 모르는 예전 애드온에게는 전처럼 글만 넘긴다.
- **채널이 요청마다 바뀐 창을 적는다.** 가드에게 "이 요청의 첫 변경이니 지점을 찍어라"라고 답할 때, 그 창을 요청의 채팅 메시지에 적는다(`points`). 채팅 기록과 함께 저장하므로 Claude Hand를 다시 켜도 안다. 누구 요청인지 모르는 일(데스크톱 앱에서 시킨 것, 훅 없는 세션의 추정)은 적지 않는다. 그런 일은 전처럼 창마다 되돌린다.
- **창은 "그 요청이 맨 위일 때만" 되돌린다.** 새 동작 둘이다: Unity의 `ch_checkpoint` `undo_request`, Blender 애드온의 `undo_request(요청 번호)`. 되돌릴 것이 있는 가장 새 지점이 그 요청의 것이면 되돌리고(`undone`), 그 요청의 지점이 없으면 `nothing`, 더 새 요청의 지점이 위에 있으면 아무것도 하지 않고 무엇이 위에 있는지 답한다(`blocked`). 되돌리기는 순서대로만 갈 수 있어서, 위의 요청을 건너 아래 것만 돌릴 수는 없다. 함께 돌리면 사용자가 취소하지 않은 요청이 사라진다. 그래서 건너뛰고 알린다.
  - 동작 이름을 따로 둔 이유: 예전 패키지는 모르는 인자를 무시한다. `undo`에 `request`를 붙여 보내면 예전 패키지는 맨 위 지점을 그냥 되돌린다. 새 이름이면 예전 패키지는 "그런 동작 없음"으로 답하고, 채널은 그것을 "되돌리지 못함"으로 전한다.
- **요청한 채팅창의 버튼이 요청을 따라간다.** 채널은 소식을 묻는 응답(`/v1/updates`)에 "이 채팅에서 시킨 요청 가운데 다른 창에 지점이 있는 가장 새 것"을 싣는다(`undo`: 요청 번호, 글, 시각, 다른 창 이름들). 어느 버튼을 그릴지는 패널이 정한다. 자기 창의 지점은 패널이 가장 정확히 알기 때문이다(Unity는 바뀐 것 없는 지점을 건너뛰고, 사용자는 Ctrl+Z도 누른다).
  - 자기 창의 맨 위 지점이 그 요청의 것이면: "되돌리기: 글 (이 창 + hand_test (Unity))". 누르면 이 창은 전처럼 그 자리에서 되돌리고, 나머지는 채널에 맡긴다(`/v1/undo`).
  - 자기 창에 지점이 없거나 맨 위 지점이 그 요청보다 오래됐으면: "되돌리기: 글 (hand_test (Unity))". 이 창은 그 요청으로 바뀐 것이 없으니 건드리지 않는다.
  - 맨 위 지점이 그 요청보다 새 요청의 것이면: 전처럼 그 지점의 버튼. 그것을 되돌리고 나면 위의 버튼이 나온다.
- **채널이 창마다 부른다.** Unity는 통로로, Blender는 MCP for Blender의 코드 실행으로. 락 밖에서 부르고(창 하나가 오래 걸릴 수 있다), 작업 중인 창은 부르지 않고 건너뛴다. 결과는 요청한 채팅창에 한 줄로 온다("hand_test (Unity): 되돌렸어요 (파일 1개를…) / props (Blender): 그 뒤의 요청('…')이 이 창을 바꿔서 건너뛰었어요"). 되돌린 창의 채팅창에도 "○○ 채팅창의 요청 '…'을 여기서도 되돌렸어요"가 남는다. 감독에게는 그 채팅창의 다음 메시지에 붙여 알린다(창 안 버튼이 하던 것과 같다).
  - 건너뛴 창은 요청에 남아 있어서, 그 창이 풀린 뒤 같은 버튼을 다시 누르면 남은 창만 되돌린다. 모두 끝나면 버튼이 창 안 것으로 돌아간다.
  - 바뀐 창의 채팅창에서 그 창 버튼으로 먼저 되돌렸으면 패널이 채널에 알린다(`/v1/undone`). 알리지 못했어도 문제는 없다: 다시 물으면 그 창은 "되돌릴 것 없음"이라고 답한다. 이름으로 묻는 동작은 몇 번 불러도 같다.
- **말로 한 취소는 감독이 채널 도구로 한다(`undo_request`).** 전에는 하위 세션에게 맡겼다. 하위 세션은 자기 창의 맨 위 지점을 되돌리므로, 그것이 취소하려던 요청이 아닐 수 있고 다른 창은 남았다. 도구는 그 채팅이 가장 최근에 시킨 "무언가를 바꾼" 요청을 그 요청이 바꾼 모든 창에서 되돌리고 창마다 결과를 돌려준다. 미리 허용하지 않았다. Claude Code가 허락을 물으면 채팅창에 허용·거부 버튼이 뜨고, 그것이 버튼의 확인 창과 같은 구실을 한다. 감독이 말을 잘못 알아듣고 여러 창의 일을 묻지 않고 되돌리는 것을 막는다. 요청 번호가 없는 일(데스크톱 앱에서 시킨 것)에는 "되돌릴 요청이 없다"고 답하고, 그때만 전처럼 하위 세션에게 맡긴다.
- **한 창에는 한 번에 한 요청의 일만.** 창마다 "지금 누구 요청으로 일하나"를 적는 자리가 하나다(`assigned`). 그 창이 일하는 중에 다른 요청의 일을 맡기면 그 자리를 덮어써서, 앞 일의 나머지와 그 지점과 중지가 뒤 요청 몫이 됐다. 지점의 요청 이름이 틀리면 이름으로 되돌리는 것이 엉뚱한 요청을 되돌린다. 그래서 감독의 훅이 맡기기 전에 물을 때, 그 창이 다른 요청으로 일하는 중이면 채널이 거부하고 훅이 "끝났다는 알림 뒤에 다시 맡기라"고 돌려보낸다. 같은 요청의 이어 맡기기는 된다. 풀리는 때: 그 창의 하위 세션이 멈췄을 때(창을 한 번도 건드리지 않고 끝난 하위 세션 포함), 끊겨서 멈춤 알림이 없는 하위 세션은 마지막 동작 뒤 90초(두 하위 세션을 떼어 놓는 시간과 같다).
- **하지 않은 것.** 다른 채팅에서 시킨 요청을 이 채팅창에서 고르기(버튼은 그 채팅에서 시킨 가장 새 요청 하나만 내놓는다). 건너뛴 창을 자동으로 다시 해 보기. 여러 창을 한꺼번에 "모두 아니면 전무"로 되돌리기: 한 창이 실패해도 다른 창은 되돌아가고, 무엇이 남았는지 알린다.
- **확인.** Python 테스트(채널: 바뀐 창 기록과 다시 켠 뒤, 창마다 결과와 안내, 작업 중·응답 없음·실패, 두 번 누르기, 겹친 맡기기 거부, HTTP와 감독 도구. 창에 묻는 법과 예전 패키지·애드온의 답). Blender 안 테스트 둘(이름으로 되돌리기의 세 가지 답, 버튼이 다른 창까지 되돌리는 것과 다른 창만 바꾼 요청). Unity 자체 테스트 넷(`undo_request`의 세 가지 답). 열린 창과 실제 감독 세션으로는 아직 보지 않았다(TEST-PLAN 28).

### 요청마다 시간 · 토큰 (2026-11-23, 0.15.0)

요청 하나가 얼마나 걸렸고 토큰을 얼마나 썼는지 볼 방법이 없었다. 감독 세션의 `/cost`는 세션 전체의 누적이고, 창마다 하위 세션이 따로 있어서 어느 요청이 비쌌는지는 알 수 없었다. `trace`에는 시간과 동작만 있었다.

- **토큰은 Claude Code가 남기는 대화 기록에서 읽는다.** 감독은 `~/.claude/projects/<폴더>/<세션>.jsonl`, 하위 세션은 그 옆 `<세션>/subagents/agent-<id>.jsonl`(어느 하위 세션인지는 `agent-<id>.meta.json`). 응답마다 쓴 토큰이 적혀 있다. 응답 하나가 블록마다 한 줄씩 적히고 줄마다 같은 토큰 수가 있어서, 응답 id로 한 번만 센다. 채널이 토큰을 따로 세지 않으니 Claude도 토큰도 더 들지 않는다.
- **누구 요청인지 추정하지 않는다.** 기록에 이미 있다. 감독의 차례는 그 차례를 시작시킨 줄이 정한다: 채팅창 메시지면 `<channel>` 태그의 요청 번호, 하위 세션의 보고면 그 하위 세션에게 마지막으로 맡긴 글의 `[요청 번호]`, 터미널이나 Claude 앱에서 친 말이면 번호 없음. 하위 세션의 차례는 맡은 글 첫 줄의 `[요청 번호]`가 정한다. 그 줄은 0.9부터 감독의 훅이 강제해 왔다. 장부(`handlib/usage.py`의 `Ledger`)는 파일들을 자란 데까지 이어 읽으며 응답을 "요청 → 누가" 아래에 더한다.
- **세 갈래로 보여 준다.** `출력`(써낸 것), `새 입력`(새로 읽은 것: 그냥 입력 + 캐시에 새로 쓴 것), `다시 읽기`(캐시에서 다시 읽은 것). 합치지 않는다. 다시 읽기가 거의 전부라서(실제 기록에서 99%쯤) 합치면 아무것도 안 보인다. 따로 두면 쉬었다가 보낸 요청이 눈에 띈다: 캐시가 식어 대화 전체를 새로 읽으므로 `새 입력`이 수십 배가 된다(실제 기록: 평소 4천쯤, 쉰 뒤 20만). 달러로 바꾸지 않는다. 가격표를 따로 들고 다녀야 하고, 요금제에 따라 뜻이 다르다.
- **시간은 채널이 잰다.** 채팅창에서 보낸 때부터 결과 답이 온 때까지, 사용자가 기다린 시간이다. 동작 수는 가드가 보고한 호출 수. 기록에서 읽는 시간은 따로 있다: 감독과 하위 세션이 각자 일한 시간(차례가 시작된 때부터 응답들까지). `trace`에만 나온다.
- **언제 말하나.** 결과 답(`working`이 아닌 reply)이 오면 그 요청을 셈할 것에 넣는다. 감독 차례의 마지막 응답은 차례가 끝나야 기록에 적히므로, 2초마다 도는 감시가 장부를 이어 읽다가 "그 요청의 감독 응답 가운데 답보다 뒤에 온 것이 차례를 끝냈다"가 보이면 말한다. 실제 감독 세션의 요청 37개로 재 보니 답 뒤 6초쯤이 보통이고, 감독이 그 차례에 다른 요청의 일을 이어 한 두 번은 34초와 60초였다. 그래서 90초까지 기다리고, 그때까지 안 보이면 있는 것으로 말한다. 기록을 못 찾거나 못 읽으면 기다리지 않고 시간만 말한다.
  - "하는 중" 답은 세지 않는다. `working` 없이 온 "지금 할게요"는 채널이 아직 결과를 기다리는 동안(일을 맡겼거나 일이 보였을 때) 미루고, 결과 답이 오면 그 답으로 센다. 요청마다 줄은 하나다.
  - 줄이 나오기 전에 그 채팅창에 다음 메시지가 왔으면 "이 요청" 대신 "앞 요청 “큐브를 만들어…”"라고 적는다. 줄은 늘 맨 아래에 붙기 때문이다.
- **어디에.**
  - 채팅창: 결과 아래 한 줄. `이 요청: 32초 · 동작 2번 · 토큰 출력 2.9천 · 새 입력 4.5천 · 다시 읽기 66만`. 기존 안내 줄(`notice`)에 `cost` 표시를 붙여 보내고, Unity는 상자 없이 작은 글씨로, Blender는 시계 아이콘으로 그린다. 예전 패널은 보통 안내 줄로 그린다. `~/.claude-hand/config.json`의 `"show_cost": false`로 끈다(기록에는 남는다).
  - 요청 기록: `cost` 줄(기다린 시간, 동작 수, 누가 얼마나). `trace`가 요청의 마지막 줄로 보여 준다: `감독 6초 API 4번 출력 … / unity-hand-test 16초 API 2번 출력 …`.
  - `claude_hand.py usage [--days N]`: 요청별 목록과 합계(누가, 날짜별). 기록의 `cost` 줄만 읽는다. Claude Hand가 꺼져 있어도 된다.
- **감독의 기록 파일 찾기.** 감독의 훅이 일을 맡길 때마다 채널에 알리는 말(`/v1/assign`)에 자기 기록 파일 경로를 같이 보낸다(훅 입력의 `transcript_path`). 훅이 아직 한 번도 안 불렸으면 `start-supervisor`가 적어 둔 세션 번호로 찾는다.
- **읽기.** 감시 스레드에서, 락 밖에서, 셈할 요청이 있을 때만. 처음에는 채널이 켜진 때보다 1분 앞부터 읽는다. 이어 쓰는 감독 세션의 기록은 몇 MB라서, 긴 파일은 반씩 잘라 그 시각을 찾는다(줄이 적힌 순서대로 있다). 실제 4.9 MB 기록을 처음부터 다 읽어도 0.1초였다.
- **`selftest`에 항목 하나.** 이 형식은 Claude Code의 내부 형식이고 문서에 없다(2.1.322에서 확인). 바뀌면 줄에 토큰이 빠지고 시간만 나온다. 조용히 그렇게 되지 않도록 `selftest`가 감독 세션 기록에서 토큰이 읽히는지 본다("응답 509개, 요청 번호로 갈린 요청 37개").
- **한계.**
  - 줄이 나온 뒤에 그 요청으로 더 쓴 토큰은 들어가지 않는다(90초를 넘긴 차례의 나머지, 결과 답 뒤에 이어진 일).
  - 감독의 토큰은 차례 단위로 간다. 한 차례에 두 요청의 일을 이어 하면 그 차례를 시작시킨 요청 몫이 된다. 하위 세션의 토큰은 맡은 글 단위라 섞이지 않는다.
  - 번호 없는 일(데스크톱 앱, 터미널)은 요청별로 나오지 않는다. 대화 제목 짓기 같은 Claude Code 안의 작은 호출은 기록에 응답으로 남지 않아 빠진다.
  - 채팅창의 줄은 안내 줄이라 Claude Hand를 다시 켜면 채팅창에서는 사라진다. 기록에는 남아 `trace`와 `usage`로 본다(2주).
- **확인.** Python 테스트(장부: 응답이 여러 줄로 적힌 기록, 겹친 두 요청, 번호 없는 일, 이어 읽기와 반쯤 적힌 줄, 모르는 줄, 이름 없는 하위 세션, 긴 기록의 중간부터 읽기. 채널: 차례가 끝난 뒤 한 번만, 기록 없음, 90초, 끄기, "할게요" 뒤의 결과, 다음 메시지 뒤에 나온 줄, 감시 스레드. `trace`와 `usage` 출력, `selftest` 항목). 실제 감독 세션 기록의 요청 37개를 다시 돌려 줄이 모두 만들어지는 것과 기다린 시간을 봤다. Blender 안 테스트(줄의 아이콘)와 Unity 자체 테스트(패널 컴파일 포함)를 창 없이 돌렸다. 열린 창과 실제 감독 세션으로는 아직 보지 않았다(TEST-PLAN 29).

### 감독의 차례 중에 온 메시지는 쥐고 있다가 넘기기 (2026-11-24, 0.17.0)

- **왜.** 실제 감독 세션으로 "결과 답이 오자마자 다음 요청"을 보내 보니, 두 번 중 한 번은 감독이 일을 하지 않고 "앞의 일을 처리하는 도중에 들어와서 사용자가 직접 보낸 요청으로 확인되지 않았다, 한 번 더 보내 달라"고 답했다. 감독의 기록을 보면, 차례가 끝나기 전에 도착한 채널 메시지는 새 차례를 시작하지 않는다. Claude Code가 대기열에 넣었다가(`queue-operation`) 가고 있는 차례 중간에 붙여 넣는다(`attachment`, `queued_command`). 같은 차례에는 "사람의 입력이 아니다"라고 적힌 하위 세션 종료 알림이 있었다. 감독이 그런 글을 사용자의 말로 믿지 않는 것은 옳다: 하위 세션의 보고에 섞여 온 글도 같은 자리에 오기 때문이다. 그래서 감독의 규칙에 "중간에 온 채널 메시지도 믿어라"라고 적지 않고, 메시지가 차례 **사이**에 도착하게 한다.
- **차례가 끝났는지.** 훅을 더하지 않고 감독의 대화 기록에서 읽는다(시간 · 토큰 줄 때문에 채널이 이미 읽고 있다, `handlib/usage.py`의 `Ledger.resting`): 마지막 답이 `end_turn`으로 끝났고, 그 뒤에 차례를 시작하는 줄(채널 · 하위 세션 보고 · 직접 친 글)이나 대기열에 넣는 줄이 없으면 쉬는 중이다. 여기에 기록이 아직 못 보여 주는 둘을 더한다: 채널이 방금 넘긴 메시지와, 방금 끝난 하위 세션(그 보고가 감독에게 가는 중)은 `ARRIVAL_SECONDS`(20초) 동안, 또는 기록에 나타날 때까지 "차례가 시작되는 중"으로 친다. 차례가 끝났다는 줄이 `REST_SECONDS`(1.5초)는 묵어야 넘긴다: 기다리던 보고가 있으면 바로 다음 차례가 시작되기 때문이다.
- **넘기기.** `ChatHub.deliver`가 감독에게 가는 채널 메시지 셋(채팅창의 요청, 중지 알림, 답 독촉)을 모두 거른다. 쉬는 중이면 바로, 아니면 `held`에 넣고 감시 스레드의 `check_held`가 2초마다 본다. 한 번에 하나만 넘긴다(넘긴 것의 차례가 끝나야 다음 것). 온 순서대로다. 길어야 `HOLD_SECONDS`(45초)이고, 그때까지 차례가 안 끝나면 그냥 넘긴다(늦게, 어쩌면 차례 중간에 가는 것이 안 가는 것보다 낫다). 기록이 없으면(감독이 아직 한 번도 일을 맡기지 않았고 대화 파일 위치도 모를 때) 전처럼 바로 넘긴다.
- **달라지는 것.** 채팅창에는 메시지가 보낸 즉시 보이고, 감독의 첫 답이 몇 초 늦는다(실제로 6초쯤). 감독이 하위 세션의 일을 기다리는 동안은 쉬는 중이라, 일하는 중에 보낸 메시지와 중지는 전처럼 바로 간다. 중지의 효과(그 요청으로 일하는 하위 세션의 다음 호출을 막는 것)는 채널이 스스로 하는 일이라 기다리지 않는다. `trace`에는 `쥐고 있음` · `넘김 6.2초 기다림`으로 남는다.
- **한계.** 넘기는 바로 그 순간에 하위 세션의 보고가 감독에게 도착하면 여전히 차례 중간에 낄 수 있다(기록에 나타나기 전의 틈). 기록의 줄 모양은 Claude Code의 것이라, 바뀌면 "쉬는 중"을 못 읽는다: 그때는 45초 뒤에 넘어간다.
- **압축 요약 줄 (0.17.1).** 바로 그 일이 한 번 있었다. Claude Code가 긴 대화를 압축하면 요약을 `user` 줄로 적는다(`isCompactSummary`, 메타 표시 없음). 이것을 차례를 시작하는 줄로 읽어, 쉬는 중에 압축된 감독을 다음 답이 나올 때까지 "차례 중"으로 봤고, 두 시간 뒤의 요청이 46.6초를 기다렸다(`trace`: `넘김 46.6초 기다림 (차례가 끝나지 않아 그냥 넘김)`). `usage.starts_a_turn`이 그 줄을 건너뛴다. 압축 앞의 상태가 그대로 남으므로, 쉬는 중이었으면 쉬는 중이고 차례 중간에 압축됐으면 차례 중이다.
- **확인.** 채널 테스트 다섯(차례 중에는 기다림, 온 순서대로 한 차례에 하나, 방금 끝난 하위 세션, 가장 긴 기다림과 중지, 기록 없이는 바로). 실제 감독 세션에서 고치기 전 두 번 중 한 번 거절, 고친 뒤 세 번 모두 처리(6.8 · 6.3 · 6.2초 기다림).

### 저장하면 자동으로 다시 보내기 (2026-11-24, 0.16.0)

모델을 Blender에서 고쳐 가며 Unity에서 볼 때, 고칠 때마다 "다시 보내줘"라고 해야 했다. 그때마다 감독과 하위 세션 둘이 돌아 1~2분과 토큰이 들었다. 다시 보내는 일 자체는 정해진 두 동작(내보내기, 제자리에서 바꾸기)이라 Claude가 판단할 것이 없다.

- **Blender가 Unity에 직접 보낸다.** Unity 쪽 통로(`ChBridge`)는 등록 파일(`~/.claude-hand/unity/<hash>.json`)의 포트와 토큰만 있으면 되는 한 줄 JSON 소켓이다. Blender 애드온이 그 소켓을 직접 부른다. Claude도 채널도 거치지 않으므로 토큰이 들지 않고, Claude Hand가 꺼져 있어도 된다. 대신 요청 기록(`trace`)에는 남지 않는다. 요청이 아니기 때문이다.
- **따라가는지는 Unity 쪽 모델에 적는다.** `ch_import_model`이 모델의 `.meta`에 남기는 기록에 `follow`를 더했다. 기본은 꺼짐이다. 바뀌는 쪽이 허락을 쥐는 셈이고, 모델을 지우면 표시도 함께 사라진다. `.blend`에는 어디로 가는지를 적지 않으므로 오브젝트를 복제하거나 파일을 넘겨도 엉뚱한 곳으로 가지 않는다. 대가는 저장할 때마다 열린 Unity에 "이 파일에서 온 모델이 있나"를 한 번 묻는 것이다(소켓 한 번).
- **새 도구 `ch_model_follow`.** `list`(이 프로젝트가 Blender에서 받은 모델들과 따라가는지), `set`(켜고 끄기, 다른 파일에 잇기), 그리고 Blender 패널만 부르는 `update`(저장이 보내는 것)와 `receive`(패널의 보내기 버튼). 켜고 끄기는 Blender 패널의 체크로도, 말로도(Unity 하위 세션이 `set`) 한다.
- **한 번의 "살펴보기"가 전부다.** 열린 Unity마다 이 파일에서 온 모델을 묻고(`list`), 따라가는 것을 내보내고, Unity가 가진 것과 다르면 보낸다(`update`). 살펴보는 때: 저장한 뒤, [되돌리기] 뒤, 파일을 연 뒤, Unity가 새로 보일 때, 패널에서 켜거나 새로 고칠 때. 저장하지 않은 변경이 있는 파일은 "열 때"와 "Unity가 보일 때"에는 목록만 본다(Unity는 저장된 것을 따른다). bpy를 건드리는 일은 메인 스레드에서, Unity와의 통신은 뒤 스레드에서 한다.
- **같은 것은 같은 파일로 나오게 했다.** Unity가 이미 가진 것인지는 파일로 견준다(`list`가 주는 SHA-256과 내보낸 파일의 것, Unity도 받을 때 바이트로 한 번 더 본다). 그러려면 바뀌지 않은 모델이 늘 같은 바이트여야 하는데, 세 가지가 막았다.
  - 쓴 시각: FBX 머리의 `CreationTimeStamp`. 내보낸 뒤 그 일곱 숫자를 고정한다.
  - 번호: Blender의 내보내기는 오브젝트마다 번호를 "종류 + 이름" 문자열의 `hash()`로 만드는데, Python은 실행마다 그 값을 달리한다. 그래서 Blender를 다시 열면 같은 모델도 다른 파일이 됐다. 내보내는 동안만 내보내기 모듈의 `hash`를 이름에서 나오는 고정 해시로 바꾸고, 끝나면 되돌린다(그 모듈이 쥐고 있던 번호 표도). 창 없는 Blender를 세 번 따로 띄워 같은 바이트가 나오는 것을 확인했다(모디파이어 일곱 가지, 애니메이션 여섯, 뼈 여섯과 스킨). 내보내기 모듈이 달라져 이 방법이 안 통하는 Blender에서는 그냥 내보낸다. 그때는 Blender를 연 뒤 첫 저장에 한 번 더 갈 뿐이다.
  - 파일 이름: 텍스처가 있으면 보낸 파일의 이름이 FBX 안에 들어간다. 따라가는 모델은 늘 같은 이름으로 내보낸다(보낸 뒤 지운다).
  - 내보내기는 선택을 바꾸지 않는다. 전에는 보낼 것을 선택했다가 되돌렸는데, 이제 내보내기에 무엇을 가져갈지 넘겨준다(`temp_override(selected_objects=…)`). 선택, 활성 오브젝트, 편집 모드가 그대로다. Claude가 쓰는 `export_fbx`도 같은 함수를 쓴다.
- **베벨 모디파이어는 예외다.** 내보내기는 메시를 만들 때마다 모디파이어를 다시 계산하는데, 베벨이 새로 만든 면의 UV가 그때마다 다르게 나온다(재 보니 2756개 중 133개, 잡음이 아니라 다른 값). 그런 메시는 건드리지 않아도 두 번 내보내면 다르다. 그래서 파일이 Unity의 것과 다른데 Blender가 "그 모델에 한 일이 없다"고 하면 한 번 더 내보내 본다. 두 번이 같으면 진짜 차이라 보내고, 다르면 원래 매번 다른 모델이라 보내지 않는다. "한 일이 있는지"는 Blender가 알려 주는 것으로 안다(`depsgraph_update_post`: 이동, 메시 편집, 모디파이어와 재질의 값). 오브젝트는 이름으로, 메시와 재질은 그것을 쓰는 모델의 것으로만 친다(`_made_of`; 재질 노드의 값을 바꾸면 Blender는 그 재질, 그것을 쓰는 오브젝트와 메시를 알려 준다. 재질에 딸린 노드 트리가 따로 알려질 때도 있는데, 재질과 함께 왔으면 그 재질 몫이다). 처음에는 메시 · 재질의 변경을 모든 모델의 것으로 쳤는데, 그러면 베벨 모델이 상관없는 재질을 고친 저장마다 다시 갔다(0.17.0에서 열린 창으로 보고 고쳤다). 노드 그룹, 셰이프 키, 액션처럼 누구 것인지 여기서 알 수 없는 것은 여전히 모든 모델의 것으로 친다. 그것이 못 알려 주는 둘은 따로 본다: 오브젝트 이름 목록과 텍스처 파일의 시각. 살펴보기 자신의 내보내기가 일으키는 재계산은 세지 않는다. 남는 것: 그런 모델은 파일을 연 뒤 첫 번에는 한 번 간다(Unity의 것이 맞는지 알 길이 없다).
- **무거운 모델.** 내보내기가 1초를 넘긴 모델은 한 일이 있다고 할 때만 내보낸다. 보통 모델(면 8만 개에 0.1초)은 저장할 때마다 내보내 파일로 견준다. 그쪽이 확실하다.
- **오브젝트는 보낸 이름을 지닌다.** Unity는 모델을 "어느 파일의 어느 오브젝트들"로 안다. 이름을 바꾸면 끊겼다. 이제 보낼 때 그 오브젝트에 사용자 속성 `claude_hand_sent_as`로 보낸 이름을 남기고, Unity에는 늘 그 이름을 말한다. Unity 쪽은 바뀐 것이 없다. 복제하면 표시도 복제되므로 규칙을 뒀다: 표시가 같은 것이 둘이면 그 이름을 지금 가진 쪽, 둘 다 아니면 고르지 않고 패널에 묻는다([선택한 오브젝트로 잇기]). 새 오브젝트가 옛 이름을 가져가면 번호를 붙인 다른 이름으로 보낸다. Claude의 "다시 보내줘"도 같은 표시를 쓴다.
- **다른 이름으로 저장하면 모델이 따라간다.** 애드온은 저장 전후의 경로를 안다. 달라졌으면(사본 저장은 경로가 안 바뀌므로 아니다) 다음 살펴보기에서 Unity에 "옛 경로의 모델은 이제 새 경로의 것"이라고 알린다. 옛 파일은 그 모델에 닿지 않게 된다. 탐색기에서 옮긴 파일은 Blender가 알 수 없어서, 패널이 "다른 파일에서 온 것으로 적힌 모델"을 보여 주고 [이 파일로 잇기]를 둔다. 아무 파일에나 뜨지 않게, 이 파일의 오브젝트가 그 이름으로 보낸 표시를 지닌 경우만 보여 준다.
- **Unity에서는 요청의 일과 섞이지 않게 했다.** 저장을 따라 바뀐 것은 사용자가 한 일이다.
  - 되돌리기 지점에 적지 않는다. `ch_import_model`을 요청 밖에서 부르면 파일 기록이 가장 새 지점(예전 요청의 것)에 붙었다. 그 요청을 되돌리면 모델까지 돌아갔을 것이다.
  - 요청이 일하는 중에 와도 그 요청 몫이 아니다. 그 뒤 Unity가 알려 오는 것도 가린다: 그 에셋의 가져오기 알림, 그리고 모델을 쓰는 씬 오브젝트가 새 모양이 됐다는 알림(`UpdatePrefabInstances`). 가리지 않으면 읽기만 한 요청에 되돌리기 버튼이 생긴다.
  - 바꾸기 전 파일은 `Library/ClaudeHand/replaced/follow/` 한 묶음에 최근 5개만 둔다. 전처럼 두면 저장 열 번에 요청들의 보관이 밀려난다.
  - 씬에 새로 놓지 않고, 지워진 모델을 다시 만들지 않고, 따라가기가 꺼진 모델은 바꾸지 않는다(Unity가 직접 확인한다).
- **[되돌리기]와의 관계.** Unity의 모델은 마지막으로 저장된 `.blend`를 따른다. 예외가 하나다: Blender에서 요청을 되돌리면 저장하지 않았어도 한 번 살펴본다. 그 요청이 모델을 바꿨고 그 사이 저장이 있었다면 Unity에 바뀐 모델이 남기 때문이다.
- **패널의 [선택한 오브젝트 보내기].** 처음 보내는 것도 같은 길로 된다(`receive`). Unity는 모델을 넣고 씬에 놓고 따라가기를 켠다. 이때는 지점을 하나 찍는다("Blender에서 보냄: …"). Unity 채팅창의 되돌리기로 뺄 수 있어야 하기 때문이다. 그 프로젝트에서 요청이 일하는 중이면 거절한다(그 요청의 지점 위에 지점이 올라가면 그 뒤의 일이 엉뚱한 지점 몫이 된다).
- **알림.** 바뀌었을 때만 두 채팅창에 한 줄("저장 → hand_test: Chair 반영": Blender 쪽은 오브젝트 이름으로, Unity 쪽은 파일 이름으로, 저장이 아니면 "되돌리기"나 "Unity 열림"으로). 저장이 이어지면 줄을 늘리지 않고 그 줄을 고친다("3번째"). 모델 둘이 번갈아 바뀌어도 줄은 모델마다 하나다. 살펴보다 오류가 나면 그 한 번만 잃는다: Blender는 함수가 예외를 낸 타이머를 없애는데, 그 타이머가 채팅 패널의 것이기도 하다. 감독은 이 일을 모르므로, 그 창의 다음 메시지에 한 줄을 붙여 알린다(되돌리기 때와 같은 방식).
- **하지 않은 것.** Unity → Blender로 따라가기(모델은 Blender에서 만든다). 저장하지 않은 변경을 그때그때 보내기. Unity가 닫혀 있던 동안의 저장을 쌓아 두기(Unity가 열리면 지금 저장된 것 하나를 보낸다).
- **한계.** 베벨이 붙은 모델은 파일을 연 뒤 한 번은 간다. 이름만 바꾼 자식 오브젝트나 재질은 다음 변경 때 함께 간다(무거운 모델과 베벨 모델에서만: 나머지는 파일로 견주므로 바로 간다). Blender가 저장과 동시에 닫히면 그 저장은 다음에 열 때 간다. 번호 고정은 Blender 내보내기 모듈의 안쪽에 기댄다(5.2에서 확인).
- **확인.** Blender 안 테스트 13개(가짜 Unity: 같은 줄 JSON 소켓과 등록 파일. 저장 · 사본 저장 · 다른 이름, 이름 바꾸기와 복제, 베벨, 무거운 모델, 되돌리기, 늦게 열린 Unity, 바쁜 · 사라진 · 옛 Unity, 패널의 보내기와 잇기, 모델 둘이 번갈아 바뀔 때의 줄, 실패한 살펴보기 뒤, 같은 파일이 나오는지). Unity 자체 테스트 12개(따라가기 전에는 안 바뀜, 켠 뒤, 요청이 일하는 중의 반영과 몇 틱 뒤, 구조가 바뀐 모델, 없는 모델, 다른 이름, 패널이 보낸 모델과 그 되돌리기). 그리고 창 없이 한 바퀴: 통로를 연 창 없는 Unity에 창 없는 Blender가 실제 소켓으로 보내고, 고쳐 저장하고, 그대로 다시 저장한다(`selftest --unity-project`의 마지막 부분). 열린 창으로는 아직 보지 않았다(TEST-PLAN 30): 패널의 칸, 편집 모드에서 저장한 뒤의 실행 취소, 두 채팅창의 줄.

### 전체 현황 한눈에 (2026-11-11)

창이 셋을 넘으면 누가 일하는 중이고 누가 막혀 있는지 알 수 없었다. 각 채팅창은 자기 창의 진행만 보여 줬다.

- **새로 모으지 않는다.** 채널은 이미 안다: 어느 창의 하위 세션이 동작을 보고하는지(작업 줄), 멈췄는지(Stop 훅), 어느 채팅창의 요청이 답을 기다리는지, 어느 창에 대화상자가 떠 있는지, 그 일이 누구의 요청인지(요청 번호). 창마다 이것을 상태 하나로 줄인다: `작업 중`, `답 기다림`(요청은 있는데 일하는 하위 세션이 없다: 감독이 답할 차례), `대화상자로 멈춤`, `한가함`. 누구에게도 묻지 않으니 Claude도 토큰도 들지 않는다.
- **어느 창을 보여 주나.** 채팅창이 소식을 묻고 있는 창(최근 90초), 지금 일하는 창, 답을 기다리는 창. 하위 세션 정의만 남아 있는 닫힌 프로젝트는 넣지 않는다.
- **채팅창에는 다른 창만, 바뀔 때만.** 소식을 묻는 응답(`/v1/updates`)에 다른 창들의 줄이 같이 가고, 패널은 자기가 가진 판 번호(`orev`)를 알려 준다. 어느 창의 **상태**가 바뀌면 판이 바뀌어 기다리던 패널이 깨어난다. 도구 호출 하나하나와 흐르는 초는 판에 넣지 않았다. 넣으면 한 창이 일할 때마다 모든 패널이 깨어난다. 그래서 다른 창의 줄에는 경과 시간과 지금 동작이 없고, 그것은 그 창의 채팅창과 `status` 명령에 있다.
- **글자로만.** 상태는 낱말로 가른다. 색으로 가르지 않는다(색을 구분하기 어려운 사용자). 줄을 만드는 곳은 한 군데(`handlib/overview.py`)이고 두 패널은 받은 줄을 그대로 그린다.
- **`claude_hand.py status`**는 같은 것을 경과 시간, 지금 동작과 함께 보여 준다(`/v1/overview`). 감독의 `current_context`에도 창마다 상태가 들어가서 "지금 다들 뭐 하고 있어?"에 답할 수 있다.
- **한계.** Unity는 채팅창이 열려 있어야 "열린 창"으로 잡힌다(일하는 동안은 채팅창이 없어도 잡힌다). 채팅창을 닫은 창은 길게는 90초 동안 더 보인다. 두 패널에 실제로 어떻게 보이는지는 창을 열어 확인하지 않았다(TEST-PLAN 26).

### Unity 안에서 도는 자체 테스트

C# 쪽에는 테스트가 없었다(점검 8번). `ChSelfTest.cs`가 파일을 옮기는 도구를 실제 Unity에서 돌린다: 새로 들어오기, 같은 원본이 다시 왔을 때 제자리에서 바뀌기, 씬 오브젝트가 새 메시를 보이기, 바꾸기 전 파일 남기기, 머티리얼 연결과 풀기, 따로 한 벌, 다시 내보내기, 요청을 되돌리면 바뀐 파일이 돌아오고 들어온 에셋이 프로젝트에서 빠져 보관되는 것, 씬 오브젝트를 패키지로 내보냈다가 다시 받는 것과 그 되돌리기, 바뀐 것이 없는 지점 건너뛰기, 요청이 일하는 동안 어떤 식으로든 바뀐 파일의 되돌리기, 요청 번호로 되돌리기, 저장을 따라가는 모델(59개 항목). 패키지 가져오기는 에디터가 몇 번 도는 동안 끝나는 일이라, 그 부분은 `EditorApplication.update`에서 이어서 확인한다. `selftest --unity-project <경로>`가 닫혀 있는 프로젝트를 창 없이(`-batchmode -executeMethod`) 열어 실행한다. Blender가 설치돼 있으면 시험 모델은 애드온의 `export_fbx`가 실제로 내보낸 것 두 벌이고, Unity가 내보낸 것을 Blender가 `import_model`로 다시 읽는다. 그래서 Blender → Unity → Blender가 창 없이 한 바퀴 돈다. Unity Test Framework를 쓰지 않은 이유는 프로젝트의 manifest에 `testables`를 넣어야 패키지 테스트가 보이기 때문이다. 이 방식은 프로젝트에 아무것도 더하지 않는다.

통로(`ChBridge`)는 배치 모드에서 열리지 않으므로, 이 테스트가 보는 것은 도구 자체다. 예외가 하나 있다(0.16.0): 끝난 뒤 Unity를 한 번 더 창 없이 열되 통로를 켜 두고(`CLAUDE_HAND_BRIDGE_IN_BATCH=1`, `ChSelfTest.Serve`), 창 없는 Blender의 애드온이 그 통로로 모델을 보내고 고쳐 저장한다. Blender가 Unity의 소켓을 직접 부르는 길은 이렇게 창 없이 본다. 하위 세션의 통로 호출, 채팅창, 중지 버튼의 화면은 열린 에디터에서 봐야 한다(TEST-PLAN 23).

## Blender에서 보낸 모델이 깔끔하게 들어오게 (0.17.0)

재 보니(2026-11-24) FBX로 보낸 모델은 Unity에 모양만 왔다. 텍스처는 파일 안에 들어가지만 Unity가 꺼내 쓰지 않아 재질이 전부 회색이었고, 꺼내게 해도 색 · 노멀 · 발광만 붙었다. 부품마다 X 270° 회전이 붙었고, 물리로만 움직이는 오브젝트는 클립 없이 왔고, 지오메트리 노드의 인스턴스는 빈 메시였다. 같은 모델을 glTF로 보내면(Blender의 내보내기 + Unity의 glTFast) 이 대부분이 풀려 있었다. 그래도 FBX를 유지하고 glTF가 푸는 방식을 옮겼다. glTF로 가면 쓰는 프로젝트마다 glTFast를 넣어야 하고, 재질이 URP Lit이 아닌 glTFast의 셰이더로 들어오고, Blender의 glTF 내보내기는 건네준 오브젝트 대신 실제 선택만 보며 편집 모드를 빠져나오게 해서 저장 순간에 부를 수 없다. FBX에는 Unity의 Humanoid와 클립 설정이 그대로 있다.

- **회전.** 내보내기는 아무것도 돌리지 않고(`use_space_transform=False`) 파일에 축만 적는다(`-Y` 앞, `Z` 위: Blender의 축 그대로). Unity가 가져올 때 `bakeAxisConversion`으로 변환을 데이터에 굽는다. 축 설정 24가지를 Unity 옵션 켬 · 끔으로 돌려 본 것 가운데 이 조합만 모양과 부품 자세가 둘 다 Blender와 같았다(4단 계층, 회전과 배율이 섞인 부품, 키프레임 오브젝트, 스킨 메시, 본에 매단 오브젝트, 셰이프 키, Humanoid). 내보내기의 "변환 적용"(`bake_space_transform`)은 3단부터 깨지고(같은 장면에서 3~6 m), 기본 내보내기에 Unity의 굽기를 켜면 모델이 Y로 180° 돈다. 장면을 건드리지 않으므로 선택 · 모드 · 되돌리기 기록을 지키는 내보내기 방식이 그대로다. Unity 쪽은 manifest가 `"axes": "blender"`라고 한 모델에만 켠다(예전 manifest는 예전대로). Unity 6000.3에서만 확인했다.
- **재질.** manifest에 재질마다 Principled BSDF가 읽는 것을 적는다(`materials`): 기본 색, 금속, 거칠기, 노멀과 세기, 발광과 세기, 투명도와 방식, 양면. 값이거나, Image Texture를 곧바로(또는 Separate Color의 한 채널로, Normal Map 노드를 거쳐) 읽는 이미지다. 이미지는 애드온이 직접 파일로 쓴다(`<id>.textures/`): 디스크의 파일은 그대로 복사하고, 팩된 것 · 저장한 적 없는 것 · 칠하고 저장하지 않은 것은 Blender가 든 것을 쓴다. 금속과 거칠기는 Unity의 표준 재질이 읽는 한 장(빨강 = 금속, 알파 = 매끈함)으로 합친다. Unity는 그것으로 프로젝트의 재질을 만든다(`ChModelLook`: URP Lit, 파이프라인이 없으면 Standard; 모델 옆 `Materials/`, `Textures/`; 노멀은 노멀 맵으로, 데이터 이미지는 색이 아닌 것으로 가져온다) 그리고 모델의 재질 칸을 거기에 잇는다. 이미지가 UV 맵에 그대로(또는 배율과 이동만 하는 Mapping 노드로) 놓였으면 그 배율 · 이동도 간다. Image Texture의 보간이 "Closest"면 Unity에서도 픽셀이 네모로 남게 가져온다. 그 밖의 노드를 거치는 입력과, UV가 아닌 좌표나 회전으로 놓은 이미지는 값이나 이미지로 적을 수 없어서 굽고(아래), 굽지도 못하면 `not_carried`에 사람이 읽을 글로 남겨 결과에 싣는다. 조용히 빠지는 것이 없게 하려는 것이다. 창 없는 Unity에서 같은 해와 같은 자리로 렌더한 그림을 Blender(Cycles)의 것과 나란히 놓아 봤다: 노드로만 만든 재질의 무늬와 요철, 이미지 재질의 색 띠가 같은 자리에 같은 모양으로 나왔다. (가져온 직후 첫 프레임에는 노멀 맵의 셰이더가 아직 없어 요철 없이 그려진다. 한 번 더 그리면 나온다.)
- **누구의 재질인가.** 만든 재질마다 쓴 직후의 파일 해시를 모델의 기록에 둔다(`generated`). 다시 받을 때 파일이 그 해시와 다르면 이 프로젝트에서 고친 것이라 덮어쓰지 않고 알린다(결과의 `materials_made.kept`; Claude를 거치지 않는 저장 따라가기와 패널 보내기에서는 Blender 패널에 한 줄: 안 그러면 Blender에서 고친 재질이 안 간 것처럼 보인다). 지우면 다시 만든다. `materials="project"`가 찾는 "같은 이름의 프로젝트 재질"에서 모델 자신의 `Materials/`는 뺀다.
- **따라가는 모델.** "Unity가 이미 가진 것인가"는 모델 파일의 해시에 재질 설명의 해시(`materials_hash`: 값과, 이미지마다 파일 시각 · 크기)를 더해 본다. 이미지 픽셀은 읽지 않으므로 살펴보기가 무거워지지 않고, 이미지 파일은 보낼 때만 쓴다.
- **클립.** 리그 하나에만 액션이 있으면(캐릭터) 액션마다 클립이고, Blender의 내보내기가 파일의 다른 액션까지 그 리그의 테이크로 쓰는 것은 manifest의 `clips`(리그 자신의 액션만, 액션 이름으로)로 걸러 낸다. 여러 오브젝트가 움직이거나 액션 없이 움직이는 것(강체, 셰이프 키)이 있으면 장면의 프레임 범위를 클립 하나로 보낸다: 오브젝트마다 액션마다 나눈 테이크는 어느 것도 장면이 재생되는 모습이 아니었다. Inspector에서 고친 클립 설정은 다시 받아도 남는다.
- **지오메트리 노드.** 인스턴스를 내는 오브젝트에는 내보내는 동안만 Realize Instances 모디파이어를 하나 더 얹는다(`_realized`). 끝나면 떼고 노드 그룹도 지운다.
- **Humanoid.** `rig="humanoid"`면 가져오기 설정을 Humanoid로 하고, 아바타가 유효하지 않으면 Generic으로 되돌리고 알린다(깨진 Humanoid는 아무것도 재생하지 못한다).
- **굽기.** 다른 노드를 거치는 입력은 그 입력만 이미지로 굽는다(`_bake_pixels`). 입력을 먹이는 소켓을 굽는 동안만 Emission 셰이더에 물려 Cycles의 `EMIT`으로 굽고(기본 색 · 금속 · 거칠기 · 발광 색: 조명과 상관없이 값 그대로 나온다), 노멀은 `NORMAL`로 굽는다(범프나 노멀 섞기가 낸 결과). 그 재질을 쓰는 보낼 메시 전부를 각자의 UV로 한 장에 굽는다. `temp_override`로 건네므로 선택과 활성 오브젝트는 그대로다. 바꾸는 것은 렌더 엔진(굽는 동안 Cycles), 그 재질의 노드(이미지 노드와 Emission 하나씩), 같은 오브젝트의 다른 재질(구울 곳이 있어야 해서 임시 이미지 노드)이고 `finally`에서 되돌린다. 큐브 하나에 1024² 한 장이 0.2~0.6초. Blender는 오브젝트 모드에서만 구우므로, 다른 모드에서는 이미지의 이름만 적고 파일은 쓰지 않는다: Unity는 빠진 이미지 파일이 프로젝트에 이미 있으면 그것을 그대로 둔다. 모드를 바꾸지는 않는다(편집 중인 메시의 되돌리기 기록 때문에).
- **구운 이미지가 바뀌었는지.** 굽지 않고 알아야 저장할 때마다 굽지 않는다. 노드 트리의 지문(`_tree_stamp`: 노드마다 종류 · 설정 · 입력의 기본값 · 무엇이 무엇을 먹이는지. 놓인 자리와 이름표는 뺀다)을 그 이미지의 `stamp`에 넣어 `materials_hash`가 이것으로 바뀌게 한다. 굽지 못한 모드에서는 `stamp`에 `pending`을 붙여, 오브젝트 모드로 돌아온 뒤의 저장에서 해시가 달라져 한 번 구워 보내게 한다.
- **모양 검사.** 회전 문제는 눈으로 보기 전에는 몰랐다. 그래서 보낼 때마다 잰다. Blender가 내보내는 순간(지오메트리 노드를 실체화한 채로) 오브젝트마다 자리와, 메시면 바운딩 박스 여덟 꼭짓점이 닿는 범위를 Unity의 축으로 적는다(`check`, `_shape`). Unity는 가져온 모델에서 같은 방식으로 재어 견준다(`ChModelLook.ShapeCheck` → 결과의 `shape_check`, 다르면 `warnings`). 바운딩 박스로 견주는 것은 Unity가 꼭짓점을 읽지 않고 같은 값을 낼 수 있어서다. Blender 쪽은 그 상자를 `Object.bound_box`에서 읽지 않고 내보내는 메시(`to_mesh`, 모디파이어 적용)의 꼭짓점에서 잰다: 열린 창에서는 `bound_box`가 섭디비전 전 메시의 상자여서(GPU 섭디비전), 섭디비전이 걸린 메시마다 "다르게 들어갔다"고 했다(창 없는 Blender에서는 드러나지 않았고 열린 창에서 찾았다). 리그나 셰이프 키로 휘는 메시는 자세에 따라 달라지므로 자리만 본다. 파일의 루트가 하나면 Unity가 그것을 파일 이름의 루트로 삼으므로, 이름이 안 맞는 부품이 하나뿐이면 그것을 루트로 본다. 저장을 따라가는 모델이 다르게 들어가면 Blender 패널에 한 줄이 뜬다. 클립 중간의 모습까지 견주는 것은 하지 않았다.
- **남은 것.** 노드 셰이더 번역(굽지 않고 Unity 셰이더로), 천 · 파티클 · 헤어 · 월드. HDRP 재질. Principled BSDF가 아닌 셰이더와 노드를 거친 투명도의 굽기. 구울 해상도를 패널에서 고르기. 다시 보낸 모델을 되돌릴 때 `Materials/` · `Textures/`의 파일. 패널의 Humanoid 체크 칸(지금은 Claude에게 말하거나 `export_fbx(..., humanoid=True)`).

## 창마다 보호 규칙 (0.18.0)

"건드리지 마"를 전하는 방법은 둘뿐이었다. **메모**(`ClaudeHand/NOTES.md`, `<파일>.claude-hand.md`)는 하위 세션이 읽고 지키려고 할 뿐, 어겨도 막는 것이 없다. **가드**는 하위 세션이 남의 창을 못 건드리게 막지만 자기 창 안에서 무엇을 건드리는지는 보지 않는다. 보호 규칙은 그 사이다: 사용자가 창마다 정해 두면, Claude가 지키기를 기대하지 않고 기계가 막거나 되돌린다. 푸는 것은 사용자만 한다.

- **규칙 넷.** 창 전체(`look` 보기만: 읽는 호출만 통과, `locked` 잠금: 그 창에는 일을 맡기지도 않음), 에셋 경로(Unity: 고치기 · 지우기 · 옮기기 · 이름 바꾸기 금지), 오브젝트(Unity의 씬 오브젝트, Blender의 오브젝트와 컬렉션: 바꾸기 · 지우기 금지), 금지 동작(Unity `save_scene` · `delete_asset` · `play_mode`, Blender `save_file`). 컴포넌트나 재질 단위 같은 더 잔 규칙은 두지 않았다.
- **규칙 파일.** 세 곳(가드의 `handlib/protect.py`, Unity의 `ChProtect.cs`, Blender 애드온)이 같은 파일을 같은 뜻으로 읽는다.
  ```json
  {"version": 1, "window": "open",
   "paths": ["Assets/Art/**", "Assets/Scenes/Main.unity"],
   "objects": ["Player", "Environment/**", "collection:Rig"],
   "actions": ["save_scene", "delete_asset", "play_mode", "save_file"]}
  ```
  - 계속 두는 규칙: Unity `<프로젝트>/ClaudeHand/protect.json`(메모 옆, `Assets` 밖), Blender `<파일 이름>.claude-hand.protect.json`(.blend 옆). 프로젝트와 함께 커밋하면 같은 프로젝트를 여는 다른 사람의 claude-hand에도 적용된다. "이번만" 규칙: `~/.claude-hand/protect/unity-<프로젝트 해시>.json`, `blender-<포트>.json`. 창이 누구에게 묻지 않고 알 수 있는 이름이다. 채널이 켜질 때 비운다. 두 파일은 합쳐서 본다(창 규칙은 더 센 쪽).
  - `paths`: 프로젝트 기준, `/`, 대소문자 안 가림. `*`는 한 마디 안, `**`는 깊이 상관없이. 와일드카드 없는 규칙은 그 아래 전부, 폴더 규칙은 폴더 자신과 `.meta`도 덮는다.
  - `objects`: 이름만 적으면(`Player`) 어디에 있든 그 이름의 오브젝트와 그 아래 전부, `A/**`는 A와 그 아래 전부, `A/B`는 그 오브젝트 하나. 대소문자를 가린다. Blender에서는 부모 사슬이 계층이고 `collection:<이름>`은 그 컬렉션(아래 컬렉션 포함)의 오브젝트 전부.
  - 못 읽는 파일(JSON이 깨짐)은 "규칙 없음"이 아니다: 가드가 바꾸는 호출을 막고 패널이 알린다. 뜻한 규칙이 조용히 안 지켜지면 안 되기 때문이다.
  - 세 구현이 어긋나지 않게 `tools/tests/protect_cases.json`의 같은 사례로 셋을 각각 시험한다(Python, Blender 안, Unity 자체 테스트).
- **누가 바꾸나.** 규칙을 적는 코드는 두 패널에만 있다(Unity 채팅창과 Blender 패널의 "보호": [선택한 것 보호], [이 창 보기만], [이 창 잠금], 동작 금지, 줄마다 [풀기], "이번만" 체크). 감독과 하위 세션에게는 적는 도구가 없다. 감독이 가진 것은 **제안**이다(`propose_protection`): 사용자가 말로 "Hero는 건드리지 마"라고 하면 그 창의 채팅창에 제안이 [추가] · [취소]와 함께 뜨고, [추가]를 눌렀을 때 그 패널이 적는다. 채널은 파일을 쓰지 않는다. 푸는 것은 패널의 [풀기]뿐이라, 감독은 "어디서 푸는지"만 알려 준다. 가드는 규칙 파일이나 패널의 적는 함수(`protect_add` 등, `ChProtect`)를 이름으로 가리키는 바꾸는 호출을 막는다.
- **막는 자리 1: 가드, 호출 직전** (`guard.protection_block` → `protect.refusal`). 규칙을 읽어(Unity는 프로젝트의 파일을 직접, Blender는 그 창의 애드온에게 물어서: 애드온이 자기 파일과, 컬렉션 · 부모 사슬로 "누가 보호되는지"를 안다) 호출의 글과 견준다.
  - 잠금: 모든 호출. 보기만: 읽는 호출이 아니면 거절. 읽는 호출인지는 되돌리기 지점에 쓰던 판정에 하나를 더했다: 여러 일을 하는 Unity 도구(`manage_*`)를 읽는 동작으로 부른 것(`get…`, `find`, `search` 등)도 읽기다. 안 그러면 보기만인 창에서 할 수 있는 것이 거의 없다.
  - 금지 동작: 도구와 동작 이름, 코드와 메뉴 경로의 모양(`SaveOpenScenes`, `DeleteAsset`, `isPlaying = true`, `save_mainfile`, "File/Save").
  - 에셋 경로: **에셋을 고치는 도구**(`manage_asset` · 스크립트 · 셰이더 · 머티리얼 · 프리팹 도구)나 **코드**가 보호 경로를 가리킬 때. 에셋을 **쓰는** 호출(`manage_gameobject`의 `prefab_path`, 씬 열기)은 통과시킨다. 보호한 아트를 씬에 놓는 것이 이 기능의 가장 흔한 쓰임인데, 이름이 나왔다는 이유로 막으면 쓸 수가 없다. 초안에는 "이름이 나오고 읽기가 아니면 거절"이라고만 적었고, 만들면서 이렇게 좁혔다.
  - 오브젝트: 도구의 대상 칸(`target`, `parent`, `name` 등)이나 코드의 따옴표 글이 보호 오브젝트를 가리킬 때. 코드의 경우 거절 글이 다르다: 바꾸려던 것이면 보고하고, 읽기만 하려던 것이면 읽는 호출과 바꾸는 호출을 나누라고 한다("Hero 옆에 상자를 놓아줘"는 Hero의 자리를 읽어야 한다). 태그 이름이 보호 오브젝트 이름과 같은 경우처럼 잘못 막는 일이 있을 수 있다.
  - 예외는 막는 쪽이다: 규칙을 확인하다 오류가 나면 호출을 거절한다. 창이 닫혀 있어 물을 수 없는 것은 예외가 아니다(그 호출은 어차피 닿지 않는다).
- **막는 자리 2: 창 안.** 가드가 글로 못 알아본 것을 잡는다. 요청이 일하는 동안만 작동한다.
  - Unity가 **거부**하는 것: 보호 경로의 저장(`OnWillSaveAssets`에서 그 경로를 빼고 돌려준다) · 삭제(`FailedDelete`) · 이동과 이름 바꾸기(`FailedMove`; 보호 폴더 안으로 옮기는 것, 보호된 것을 품은 위 폴더를 지우거나 옮기는 것도). 금지 동작 `씬 저장`은 같은 자리에서 씬 파일을 빼고, `에셋 삭제`는 모든 삭제를 거부하고, `플레이 모드`는 들어가는 길(`ExitingEditMode`)에서 취소한다. 어떤 길로 오든 AssetDatabase를 거치면 걸린다. 되돌리기 자신이 하는 파일 일과 패키지의 임시 폴더, 사용자의 저장을 따라 바뀌는 모델은 건드리지 않는다.
  - **거부할 수 없는 것은 알아채고 되돌린다.** 호출 뒤에 가드가 다시 묻는다(PostToolUse 훅 `guard.py --after` → Unity `ch_protect after`, Blender `protection_after()`). 창은 호출 직전에 적어 둔 것(`before`)과 견준다.
    - Blender: 보호 오브젝트의 지문(자리, 부모, 컬렉션, 모디파이어와 그 설정, 제약, 재질 칸과 노드 값, 보이기, 사용자 속성, 메시 꼭짓점, 셰이프 키). 이름으로 다시 찾으므로 이름 바꾸기와 삭제도 "없어짐"으로 잡힌다.
    - Unity: 보호 씬 오브젝트의 지문(이름, 켜짐 · 태그 · 레이어, 부모와 자리, 자식, 컴포넌트 종류, 컴포넌트 값). 여기에 요청 중에 일어난 것 가운데 거부할 수 없던 것을 더한다: 보호 폴더에 새로 만든 에셋, 메모리에서 바뀐 보호 에셋(저장은 거부되지만 다음 저장 때 써질 것이라), Unity를 거치지 않고 디스크에서 바뀌거나 지워진 보호 파일(그것이 보이도록 `after`가 프로젝트를 다시 읽힌다).
    - 다르면 **그 요청이 그 창에 한 일을 통째로 되돌린다**(그 요청의 되돌리기 지점으로: Blender `_undo_newest`, Unity `ChCheckpoint.UndoForProtection`). 지점은 Claude Hand가 요청마다 찍는다. Claude Hand 없이 쓸 때는 지켜볼 규칙이 있는 창의 바꾸는 호출마다 가드가 찍는다. 되돌린 뒤 다시 견줘서, 실행 취소를 거치지 않아 남은 변경이 있으면 "되돌리지 못했다"고 말한다.
  - 호출 뒤의 훅이 돌지 않았을 때를 대비해 같은 확인을 두 번 더 한다: 다음 바꾸는 호출 직전(창이 "앞 호출 뒤에 아무도 묻지 않았다"는 것을 알고 그때 견줘 `pending`으로 알리며, 가드는 그 호출을 거절한다)과 하위 세션이 끝날 때(Stop 훅이 한 번 더 묻고, 방금 되돌린 것이 있으면 하위 세션을 한 번 붙잡아 그 사실을 보고하게 한다).
  - 하위 세션은 그 호출의 결과와 함께 듣는다(훅의 `block` 사유): 무엇이 막혔거나 되돌려졌는지, 더 부르지 말고 감독에게 보고하라는 것. 채팅창에는 `보호 규칙이 막았어요: …` / `보호된 Hero (자리)이(가) 바뀌어 이 요청이 이 창에 한 일을 되돌렸어요` 한 줄이 뜨고(요청한 채팅창이 다른 창이면 거기에도), `trace`에 `막음 (보호 규칙)`으로 남는다.
- **막는 자리 3: 채널.** 잠금인 창에는 맡기는 호출을 거절한다(`assign` → 감독의 훅이 "잠긴 창"이라고 막음). 제안과 그 답을 패널과 감독 사이에서 나른다. `current_context`의 창마다 `protection`이 보인다.
- **왜 depsgraph 알림으로 하지 않았나 (Blender).** 먼저 실험했다(창 없는 Blender 5.2, 조합 13가지). `bpy.data`로 바꾼 것은 누가 평가를 요청하기 전까지 알림이 오지 않았고(`view_layer.update()`를 불러야 왔다), `frame_set`으로는 오지 않았다. 알림 안에서 되돌리면 연산자로 바꾸는 코드의 **중간**에서 되돌려져, 나머지 코드가 이어서 돌아 다른 오브젝트의 변경이 남았다(13가지 중 유일하게 원상태가 아니었던 경우). 지문은 평가 없이도 변경을 보였고, 코드가 끝난 뒤의 되돌리기는 모든 조합에서 원상태로 돌아갔다. 그래서 "호출 앞뒤로 지문"이다.
- **어디까지 보장되나.**

  | 무엇을 | Unity | Blender |
  |---|---|---|
  | 보기만 · 잠금 | 막음 (가드, 채널) | 막음 (가드, 채널) |
  | 금지 동작 | 막음 (가드 + Unity 거부) | 가드만: 코드에 저장 호출이 보이면 |
  | 보호 에셋을 Unity를 거쳐 저장 · 삭제 · 이동 | 막음 (가드 + Unity 거부) | 해당 없음 |
  | 보호 에셋을 메모리에서 고치기, 보호 폴더에 만들기 | 가드가 알아보면 막음, 아니면 되돌림 | 해당 없음 |
  | 보호 파일을 코드가 직접 쓰기 | 가드가 알아보면 막음, 아니면 알아채 되돌림(가드가 복사해 둔 파일만 내용이 돌아옴) | 해당 없음 |
  | 보호 오브젝트 바꾸기 | 가드가 알아보면 막음, 아니면 되돌림 | 가드가 알아보면 막음, 아니면 되돌림 |

- **한계.**
  - 일부러 숨긴 코드를 막는 자물쇠가 아니다. 하위 세션이 실수로, 또는 메모를 못 봐서 건드리는 것을 막는 장치다.
  - "요청이 일하는 동안"은 사용자의 손도 같이 걸린다: 그동안 직접 누른 저장 · 삭제 · 플레이도 규칙에 걸리면 막히고, 호출이 도는 사이에 보호 오브젝트를 직접 고치면 Claude의 변경으로 보고 되돌린다(호출 사이에 고친 것은 괜찮다: 호출 직전에 다시 적는다).
  - Unity에서 Undo를 거치지 않은 씬 변경은 알아채지만 되돌리지 못하고, 그렇게 말한다. Unity가 저장을 거부한 보호 에셋의 메모리 속 변경도 Undo에 없으면 남는다(다음 저장 때 써진다).
  - Blender의 `.blend 저장` 금지는 가드가 코드에서 알아볼 때만 막는다(Blender에는 저장을 취소할 문이 없다). 보호 컬렉션에 새 오브젝트를 넣는 것은 잡지 않는다.
  - 지문은 보호 오브젝트가 아주 많으면 무거워진다. Unity는 컴포넌트 3,000개까지만 값까지 견주고 그 뒤는 구성만 본다.
  - 호출마다 훅이 한 번 더 돈다(PostToolUse, 0.2초쯤). Blender는 호출 전에 규칙을 물어보는 왕복이 더 든다(0.05초쯤).
- **확인.** Python 테스트(규칙 읽기와 합치기, 사례 파일, 거절과 그 글, 호출 뒤에 찾은 것, 가드의 앞뒤 물음과 실패 시 거절, 끝날 때의 물음, 채널의 제안 · 답 · 알림 · 잠금, 훅), Blender 안 테스트 11개(지문이 잡는 변경 아홉 가지와 그 되돌리기, 아무도 묻지 않은 변경, 지점이 없을 때, 패널), Unity 자체 테스트 8개(사례 파일, 거부 셋과 씬 저장, 아무도 일하지 않을 때는 안 막음, 알아채 되돌리기, Undo를 거치지 않은 변경, 규칙 더하고 풀기). 열린 창과 실제 감독 세션으로 본 것은 TEST-PLAN 32 끝에 적었다.

## 설정 · 화면 언어 · 실험실 (1.1.0)

**설정은 한 파일, 쓰는 자리에서 다시 읽는다.** 설정창이 Unity와 Blender에 따로 있어도 값은 `~/.claude-hand/config.json` 하나에 있습니다. 채널 · 명령 · 안전장치 · 두 패널이 모두 이 파일을 읽고, 값이 필요한 자리에서 파일이 바뀌었는지(시각과 크기) 보고 다시 읽습니다. 채널은 며칠씩 켜져 있는 프로세스라, 시작할 때 한 번 읽으면 설정을 바꿀 때마다 Claude Hand를 다시 켜야 하고 그때마다 사용자가 개발용 채널 확인을 눌러야 합니다. 쓰는 쪽은 파일을 옆에 쓴 뒤 통째로 바꿔 넣고, 읽는 쪽은 쓰다 만 파일을 만나면 직전 값을 씁니다. 다시 켜야 하는 설정은 그 이유가 프로그램 밖에 있는 것뿐입니다(Remote Control은 Claude Code의 시작 인자이고, 켜진 세션에서는 사용자만 칠 수 있는 슬래시 명령으로만 바뀝니다).

**목록도 한 파일.** 무엇을 정할 수 있는지는 `settings/catalog.json`에 있고, 세 구현이 그 파일을 읽어 줄을 그립니다. 설정 하나를 더할 때 세 곳의 화면 코드를 고치지 않으려는 것입니다(그 설정대로 움직이는 코드는 그것을 쓰는 자리에 넣습니다). 패널이 이 파일을 못 찾으면(claude-hand 폴더의 위치를 모를 때) 언어만 고를 수 있습니다.

**한국어 원문은 코드에, 영어는 구현마다 표에.** 번역 열쇠를 따로 두지 않고 한국어 글 자체를 열쇠로 썼습니다: `T("이전 대화 {n}개 더 보기", n=first)`. 한국어일 때는 `T`가 받은 글을 그대로 돌려주므로 지금까지의 글이 달라질 수 없고, 코드를 읽을 때 무슨 말이 나오는지 그 자리에서 보입니다. 표는 구현마다 하나입니다(Python 도구, 안전장치, Unity 패키지, Blender 애드온). 한 파일을 셋이 읽게 하지 않은 것은 Blender 애드온이 파일 하나로 설치되고 Unity 패키지가 GitHub 주소로도 설치되어, 각자 혼자서도 말할 수 있어야 하기 때문입니다. 안전장치는 자기 글의 영어와 설정 읽기를 파일 안에 갖고 있습니다(다른 파일의 실수가 거절을 멈추지 못하게 한다는 원칙 그대로). 표가 어긋나지 않게 하는 것은 테스트입니다: 소스의 한글 문자열이 모두 `T`를 거치는지, 표에 영어가 있는지, 두 언어의 자리 표시가 같은지, 두 표에 같은 한국어가 있으면 영어도 같은지.

**상수로 둔 낱말.** 모듈이 읽힐 때 한 번 채워지는 표(`STATES`, 클래스의 `bl_label`)를 `T`로 채우면 그때의 언어로 굳습니다. 그런 글은 표시만 해 두고(`N_`) 말하는 자리에서 `T`를 거칩니다. Blender의 `bl_label`은 등록할 때 읽히는 값이라 언어가 바뀌면 클래스를 다시 등록합니다.

**하나일 때의 글.** 한국어는 수에 따라 글이 달라지지 않아 원문이 하나뿐인데, 그대로 옮긴 영어는 `1 actions`가 됩니다. 원문을 둘로 쪼개면 한국어 쪽 코드가 영어 때문에 갈라지므로, 표 옆에 "하나일 때의 영어" 표를 따로 두었습니다(`EN_ONE`, `_EN_ONE`, `EnglishOne`: 원문 → 세는 값의 이름과 그때의 글). `T`는 그 값이 정수 1일 때만 이 표를 씁니다.

**긴 글에는 화면이 맞춘다.** 영어 이름은 한국어보다 깁니다. 두 설정창은 지금 언어의 가장 긴 이름으로 폭을 정하고(Blender의 창 폭은 클래스를 등록할 때 읽히므로, 언어가 바뀌어 다시 등록할 때 함께 정합니다), Unity 보호 칸의 버튼 넷은 창이 좁으면 두 줄로 나뉩니다. 영어를 줄여서 맞추지 않은 것은, 줄인 이름이 README와 지침의 대조표와 어긋나기 때문입니다.

**오래 떠 있는 줄은 언어를 따라 다시 말한다.** 대화에 찍힌 줄은 그때의 언어로 남지만, 패널의 "담당: …" 줄과 "다른 창" 줄은 몇 시간씩 떠 있는 지금 상태입니다. Blender는 그 줄을 말하는 함수를 쥐고 있다가 언어가 바뀌면 다시 부르고(`restate_status`), 채널은 언어가 바뀌면 다른 창 줄의 판 번호를 올려 패널이 다시 받아 가게 합니다. Unity 채팅창은 받아 갈 때마다 담당 줄을 다시 적습니다.

**한 번만 다른 언어로 말하는 명령과, 써 둔 파일.** `CLAUDE_HAND_LANG`은 명령 하나가 이번에 어느 언어로 말할지만 정합니다. 하위 세션 정의와 스킬은 설정의 언어로 쓰여 있으므로, `doctor`가 "이전 버전으로 만든 것"인지 견줄 때는 그 변수를 빼고 설정의 언어로 다시 만들어 봅니다(`prefs.as_configured`). 그러지 않으면 영어로 한 번 본 `doctor`가 멀쩡한 파일을 모두 낡았다고 합니다.

**Claude Hand 창에는 늘 이름이 있다.** Claude Code는 터미널 창의 제목을 세션 이름으로 정합니다. 이름을 Remote Control과 함께만 주던 때에는 "데스크톱 앱에서 보기"를 끄면 창 제목이 `claude`가 되어, 안내가 말하는 "Claude Hand 창"을 작업 표시줄에서 찾을 수 없었습니다. 이제 `--name "Claude Hand"`를 늘 줍니다. 이어 쓸 지난 대화를 그 이름으로 찾는 것도 그대로 됩니다.

**지침은 번역하지 않고 이름을 알려 준다.** 지침은 실제 감독 세션에서 드러난 문제마다 한 줄씩 고쳐 온 글이라, 번역본이 같은 일을 한다고 볼 근거가 없습니다. 그래서 화면이 영어여도 지침은 한국어로 두고, 끝에 대조표를 붙입니다: 지침이 한국어로 부르는 버튼, 지침이 따옴표로 든 막힌 글이 화면과 도구 결과에서 영어로 무엇인지. Claude는 사용자가 쓴 언어로 답하므로 사용자에게는 영어 이름이 갑니다. 전체 영어판은 실험실에 두어, 써 본 사람이 켜고 고쳐 가게 했습니다.

**실험실은 목록의 한 칸.** 실험실 기능은 `catalog.json`의 `lab`에 한 줄이고(들어온 버전, 기본 꺼짐), 코드는 `prefs.lab_on("이름")`으로 묻습니다. 기본 기능으로 올리는 것은 그 줄의 `stage`를 `stable`로 바꾸는 한 번의 변경입니다: 그때부터 설정과 상관없이 켜지고 실험실 탭에서 빠집니다. 안전장치와 보호 규칙은 여기에 넣지 않습니다.

**한계.** 화면 언어를 바꿔도 이미 찍힌 줄은 그때의 언어로 남습니다(줄은 글로 저장됩니다). 감독이 처음 받은 지침은 다시 켜야 바뀌고, 그 사이는 채널의 알림 한 번에 기대고 있습니다. Blender 버튼의 도움말과 Unity 도구의 설명은 처음부터 영어라 언어 설정을 따르지 않습니다.

## 다음 단계 (2단계 후보)

- **세션 간 직접 대화:** Claude Code 에이전트 팀(실험 기능)을 쓰거나 Channels로 감독을 거치지 않고 메시지를 주고받는다. 지금은 감독이 중계한다.
- **대화상자 감지의 남은 곳:** Unity(Windows)는 위 "매일 쓰기"에서 만들었다. 남은 것은 Blender(자기 창 안에 그리는 팝업이라 밖에서 안 읽힘), Unity 자체 모달 창(`EditorWindow.ShowModal`, `#32770`이 아님), macOS.
- **macOS·Linux:** 작업 표시줄 알림, 한글 입력창, 대화상자 감지, 창 최소화는 Windows API로만 만들었다.
- **백그라운드 보완:** 실측 결과 Windows에서 막히면 Unity 쪽에서 메인 루프를 깨우는 장치를 넣는다.
