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

| | MCP for Blender (ahujasid, GitHub판) | Blender Lab 공식 서버 |
|---|---|---|
| 구조 | 애드온(소켓 서버) + `mcp-for-blender` MCP 서버, TCP | 애드온 + 별도 MCP 서버, TCP (2차 자료 기준) |
| 포트 | **씬 속성**이라 .blend마다 저장된다. N 패널에서 바꾼다. 서버는 `--port` 또는 `BLENDER_PORT`로 정한다. README에 창 여러 개 예시가 있다 | 애드온 **환경설정**(Host/Port, 기본 localhost:9876, Auto Start) |
| 여러 창 동시 연결 | 공식 문서로 지원한다. 소스에서 확인했다 | 확인하지 못했다. 환경설정이 공유라서 창마다 따로 바꿔야 할 가능성이 크다 |
| Unity 연동 | MCP for Unity의 `blender_bridge`가 이 애드온 소켓에 직접 붙는다 | 없다 |
| 요구 사항 | Blender 3.x 이상 | Blender 5.1 이상, 실험 기능 |

이 시제품은 **GitHub판만 연결한다.**
- `sync`가 설정에서 다른 Blender MCP를 발견하면 경고만 하고 기본값 `uvx mcp-for-blender`를 쓴다.
- 포트를 확인할 때 공식 애드온처럼 `ping`을 모르는 상대는 건너뛴다.
- 공식 서버 소스(projects.blender.org)는 이 개발 환경의 네트워크 정책에서 막혀 있어 읽지 못했다.

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

## 다음 단계 (2단계 후보)

- **세션 간 직접 대화:** Claude Code 에이전트 팀(실험 기능)을 쓰거나 Channels로 감독을 거치지 않고 메시지를 주고받는다. 지금은 감독이 중계한다.
- **Unity 안 채팅 패널:** EditorWindow에서 감독 세션으로 말을 걸고, 지금 선택한 오브젝트와 씬 컨텍스트를 자동으로 붙인다.
- **모달 대화상자 처리:** Unity가 OS 대화상자로 멈추면 agent-desktop으로 버튼을 누른다. OS 기본 창이라 접근성 트리에 보일 가능성이 높다.
- **백그라운드 보완:** 실측 결과 Windows에서 막히면 Unity 쪽에서 메인 루프를 깨우는 장치를 넣는다.
