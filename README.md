# claude-hand

Unity 에디터와 Blender 창을 여러 개 동시에 띄워 두고, **창마다 전담 Claude 하위 세션**을 붙여서 일을 나눠 맡기는 도구 모음입니다.
한 곳의 오브젝트를 다른 곳으로 옮길 수도 있습니다: Unity → Unity, Blender → Blender, Blender → Unity.
기존 MCP를 대체하지 않고, 그 위에 덧붙입니다.
- Unity: [MCP for Unity (CoplayDev)](https://github.com/CoplayDev/unity-mcp)
- Blender: [MCP for Blender (ahujasid, GitHub판)](https://github.com/ahujasid/blender-mcp)

```
감독 세션 (Claude Code, 사용자와 대화)
 ├─ 하위 세션 unity-mygame      ── 전용 MCP 서버 (MyGame에 고정)  ── Unity 창: MyGame
 ├─ 하위 세션 unity-props       ── 전용 MCP 서버 (Props에 고정)   ── Unity 창: Props
 ├─ 하위 세션 blender-character ── 전용 MCP 서버 (포트 9876 고정) ── Blender 창: character.blend
 └─ 교환 폴더 %USERPROFILE%\.claude-hand\exchange  (.unitypackage / .blend / .fbx + manifest)
```

> 상태: **0.1 시제품.** C# 코드는 Unity API 스텁으로 컴파일만 확인했고, 실제 Unity에서는 아직 실행하지 않았습니다.
> [docs/TEST-PLAN.md](docs/TEST-PLAN.md)의 순서대로 확인해 주세요.

## 들어 있는 것

| 위치 | 내용 |
|---|---|
| `unity/com.claudehand.tools/` | Unity 패키지. MCP for Unity에 `ch_*` 도구 5개를 추가합니다. |
| `tools/claude_hand.py` | 열려 있는 Unity 프로젝트와 Blender 창마다 하위 세션 정의 파일을 만듭니다(`sync`). 하위 세션이 다른 대상을 건드리면 막는 안전장치(`guard`)도 들어 있습니다. |
| `skills/orchestrator/` | 감독 세션이 따르는 규칙(스킬)입니다. `sync`가 `~/.claude/skills/claude-hand/`에 설치합니다. |
| `docs/` | [설계](docs/DESIGN.md), [PC 검증 순서](docs/TEST-PLAN.md) |

### 추가되는 Unity 도구

| 도구 | 하는 일 |
|---|---|
| `ch_editor_status` | 연결된 프로젝트, 포커스 여부, 컴파일 중인지를 알려줍니다. 아무것도 바꾸지 않습니다. 백그라운드 동작을 시험할 때도 씁니다. |
| `ch_scene_snapshot` | 씬 구조를 JSON으로 돌려주고, 오브젝트마다 `@s1a2b3c4:e5` 형태의 ref를 붙입니다. 씬이 바뀐 뒤 옛 ref를 쓰면 `STALE_REF`가 납니다. |
| `ch_export_objects` | 씬 오브젝트(ref)나 에셋을 의존 에셋과 함께 `.unitypackage`로 묶어 교환 폴더에 둡니다. 원본 씬은 바뀌지 않습니다. |
| `ch_import_package` | 위 패키지를 가져와서 씬에 배치합니다. `dry_run=true`로 덮어쓰게 될 에셋을 먼저 보여줍니다. 덮어쓸 에셋이 있으면 허락 없이는 진행하지 않습니다. |
| `ch_import_model` | Blender가 교환 폴더에 내보낸 FBX(또는 OBJ, GLB)를 `Assets/ClaudeHand/Imports/` 아래로 가져와 씬에 배치합니다. |

### Blender 쪽

Blender에는 따로 설치할 것이 없습니다. MCP for Blender의 Python 실행 도구(`execute_code`)를 그대로 씁니다. 하위 세션 지침에 세 가지 방법이 들어 있습니다.
- 다른 Blender로 보내기: `.blend`로 내보냅니다
- 다른 Blender에서 받기: Append로 가져옵니다
- Unity로 보내기: FBX로 내보냅니다

모든 오류에는 `retry` 값이 붙습니다.
- `safe`: 그대로 다시 호출해도 됩니다.
- `after_fix`: 인자를 고친 뒤 다시 호출합니다.
- `check_first`: 이미 반영됐을 수 있으니 상태부터 확인합니다.

## 설치 (Windows)

준비물:
- Unity 6.3 LTS
- 각 프로젝트에 MCP for Unity 설치 (Window > MCP for Unity가 **Connected** 상태)
- Claude Code (CLI 또는 데스크톱 앱의 Code 탭)
- [uv](https://docs.astral.sh/uv/) (MCP for Unity 설치 때 이미 깔려 있습니다)

1. **저장소 받기**
   ```powershell
   git clone https://github.com/jinsu-lee-code/claude-hand.git
   cd claude-hand
   git checkout ccr-772f74ad-modcbm   # main에 합치기 전까지
   ```
2. **Unity 프로젝트마다 패키지 추가:** Window > Package Manager > **+** > *Add package from disk...*를 누르고 `claude-hand\unity\com.claudehand.tools\package.json`을 고릅니다.
   Console에 빨간 오류가 없어야 합니다.
3. **Unity 절전 끄기 (한 번만):** Edit > Preferences > General > **Interaction Mode**를 **No Throttling**으로 바꿉니다.
4. **Blender 창마다 포트 정하기:** 3D 뷰포트에서 `N` → **MCP for Blender** 탭으로 갑니다. 창마다 포트를 다르게 정하고(9876, 9877…) 서버를 켭니다.
   이 포트 값은 .blend 파일에 저장되니, 저장해 두면 다음에 열 때도 그대로입니다.
5. **하위 세션 만들기:** 작업할 Unity와 Blender 창을 모두 연 상태에서 실행합니다.
   ```powershell
   uv run tools/claude_hand.py list    # 보이는 Unity 프로젝트와 Blender 창 확인
   uv run tools/claude_hand.py sync    # %USERPROFILE%\.claude\agents\unity-*.md, blender-*.md 생성 + 감독 스킬 설치
   ```
   - 지금 쓰는 MCP 설정(`.mcp.json`, `~/.claude.json`, Claude Desktop 설정)을 자동으로 찾아서 같은 방식으로 서버를 띄웁니다. Unity는 stdio와 HTTP 둘 다 됩니다.
   - Blender는 포트 9876~9885를 확인합니다. `ping`에 응답한 창의 .blend 경로를 읽어(읽기만 합니다) 이름을 붙입니다. 다른 범위를 쓰려면 `--blender-ports 9900-9910`을 붙입니다.
   - 프로젝트를 새로 열었거나 닫았으면 다시 `sync`하세요. 닫은 프로젝트의 파일까지 지우려면 `--prune`을 붙입니다.
   - 저장소 폴더를 옮겼으면 `sync`를 다시 해야 합니다. 안전장치 경로가 정의 파일에 들어가 있어서요.
6. **Claude Code에서 확인:** `/agents`를 입력해 `unity-*`, `blender-*`가 보이면 준비 끝입니다. `agents` 폴더가 처음 생긴 경우에는 Claude Code를 한 번 다시 시작하세요.

### Blender 공식 MCP(Blender Lab)는?

공식 서버도 구조는 같습니다. Blender 애드온과 별도 MCP 서버가 TCP 소켓으로 통신하고, 기본 포트도 9876입니다. 다만 아직 `sync`가 하위 세션을 만들어 주지 않습니다.
- 서버에 포트를 지정하는 방법을 확인하지 못했습니다. 이 개발 환경에서는 blender.org에 접속할 수 없었습니다.
- 포트가 애드온 환경설정에 있어서 Blender 창 여러 개가 같은 포트를 쓰게 될 수 있습니다.

그래서 지금은 GitHub판을 권장합니다. 공식 서버 설정 JSON을 알려주시면 연결 방법을 추가하겠습니다.
두 애드온을 한 Blender에서 같이 켜면 둘 다 9876을 쓰려 해서 충돌합니다. 하나만 켜 두세요.

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

## 안전장치

- 하위 세션은 자기 전용 MCP 서버만 씁니다. 정의 파일의 `tools` 허용 목록에 그 서버만 들어 있습니다.
  - Unity 서버는 `--default-instance <Name@hash>`로 고정됩니다.
  - Blender 서버는 `--port <포트>`로 고정됩니다. Blender는 서버 하나가 창 하나에만 붙기 때문에, 이것만으로도 다른 창에 닿지 않습니다.
- `guard` 훅은 Unity 호출을 하나하나 검사합니다. 다른 프로젝트를 가리키는 `unity_instance`와 대상 변경(`set_active_instance`)을 막습니다. `batch_execute` 안에 들어 있는 명령도 검사합니다.
- 훅 자체에 문제가 생기면 호출을 막는 쪽으로 동작합니다.

## 알려진 한계

- 실제 Unity에서 아직 검증하지 않았습니다. 특히 아래 세 가지는 직접 확인이 필요합니다.
  - Windows에서 창을 최소화한 상태로 동작하는지
  - 하위 세션 전용 MCP 서버의 도구 이름 형식
  - 패키지 가져오기
- 내보내는 스크립트가 asmdef에 속해 있어도 asmdef는 같이 가지 않습니다. 받는 쪽에서는 기본 어셈블리로 컴파일됩니다.
- `Packages/` 안의 에셋(URP 셰이더 등)은 `.unitypackage`에 들어가지 않습니다. 받는 프로젝트에 같은 패키지가 있어야 합니다. dry run의 `missing_packages`에 표시됩니다.
- 같은 프로젝트에 하위 세션 둘을 동시에 붙이면 안 됩니다. Unity가 명령을 하나씩 처리하기 때문이고, 감독 스킬에도 이 규칙이 들어 있습니다. Blender 창도 마찬가지입니다.
- Blender → Unity는 FBX라서 Blender 셰이더 노드가 그대로 옮겨지지 않습니다. 기본 색, 텍스처 정도만 옮겨집니다. 복잡한 재질은 Unity에서 다시 손봐야 합니다.
- Blender 하위 세션이 처음 연결을 확인할 때와 `sync`가 파일 이름을 읽을 때, Blender 안에서 읽기 전용 Python(`print(bpy.data.filepath)`)이 실행됩니다.

## 개발

```bash
python -m unittest discover -s tools/tests -v     # 생성기·안전장치 테스트
```
