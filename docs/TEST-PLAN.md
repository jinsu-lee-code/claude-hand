# PC 검증 순서

순서대로 해 주시고, 각 단계의 **결과(성공/실패, 오류 문구)**를 알려주시면 이어서 고칠게요.
Unity 프로젝트는 두 개를 준비합니다. 예: `A` = 작업 중인 프로젝트, `B` = 빈 테스트 프로젝트.

## 1. 패키지 설치 확인

1. 두 프로젝트에 패키지를 추가합니다. README 설치 2번 방법이에요.
2. Console에 `ClaudeHand` 관련 빨간 오류가 없어야 합니다.
3. Claude Code 메인 세션에서 "ch_editor_status 불러줘"라고 해 봅니다.
   - Unity 창이 하나만 열려 있을 때 해야 합니다. 둘 이상이면 대상을 고르라는 오류가 나요.
   - 기대 결과: `project`, `focused`, `exchange_dir`가 담긴 응답이 와요.
   - 도구가 안 보이면 "manage_tools sync 해줘"라고 하거나, Claude Code를 다시 시작하거나, `/mcp`에서 다시 연결합니다. 그래도 안 되면 "execute_custom_tool로 ch_editor_status 불러줘"라고 해 봅니다.

> 결과 알려주세요: 오류 문구, 또는 도구가 직접 보였는지 execute_custom_tool로만 됐는지

## 2. 백그라운드 동작 실측 (가장 중요)

Interaction Mode를 **바꾸기 전과 후**에 각각 해 봅니다. Edit > Preferences > General > Interaction Mode예요.

| 상태 | 방법 | 응답 |
|---|---|---|
| 포커스 | Unity 창을 클릭해 둔 채 Claude에게 "ch_editor_status 불러줘" | 즉시 / 몇 초 / 30초 이상 / 실패 |
| 비활성 | 다른 창(브라우저 등)을 클릭해 둔 채 같은 요청 | |
| 최소화 | Unity 창을 최소화한 채 같은 요청 | |
| 최소화 + 변경 | 최소화한 채 "씬에 큐브 하나 만들어줘" | |

응답의 `focused` 값이 실제 상태와 맞는지도 봐 주세요.

> 결과 알려주세요: 표를 채워서 (설정 변경 전/후)

## 3. 하위 세션 만들기

1. A와 B를 둘 다 연 상태로 `uv run tools/claude_hand.py list`를 실행합니다. 두 프로젝트가 **실행 중**으로 보여야 해요.
2. `uv run tools/claude_hand.py sync`를 실행합니다.
3. Claude Code를 다시 시작하고 `/agents`를 입력합니다. `unity-a`, `unity-b`가 보여야 해요.
4. 메인 세션에서 "unity-a에게 ch_editor_status 불러서 프로젝트 이름 알려달라고 해"라고 합니다.
   - 기대 결과: A의 이름이 나와요.
   - 하위 세션이 Unity 도구가 없다고 하면, 위 가정 1~2가 틀린 거예요. 그 문구를 그대로 알려주세요. `sync --no-allowlist`로 다시 해 볼 수 있어요.

## 4. 안전장치 확인

메인 세션에서 이렇게 요청합니다. "unity-a에게, manage_scene get_hierarchy를 unity_instance를 B의 ID로 넣어서 불러보라고 해"

- 기대 결과: "...이 하위 세션의 프로젝트가 아닙니다"라는 차단 메시지가 나오고, B는 아무 영향이 없어요.

## 5. 동시 작업

"unity-a랑 unity-b에게 동시에 씬 스냅샷 찍어서 루트 오브젝트 목록 받아와"

- 기대 결과: 둘 다 자기 프로젝트 목록을 돌려줘요. Unity 창을 둘 다 최소화한 상태에서도 해 봐 주세요.

## 6. 프로젝트 간 옮기기

1. A 씬에 큐브를 하나 만들고 머티리얼 하나를 입혀 둡니다.
2. 메인 세션에 "A 씬의 그 큐브를 B 씬으로 옮겨줘"라고 합니다.
3. 기대 결과:
   - `%USERPROFILE%\.claude-hand\exchange`에 `.unitypackage`와 `.claudehand.json`이 생겨요.
   - B의 `Assets/ClaudeHand/Transfers/...`에 프리팹과 머티리얼이 들어오고, B 씬에 큐브가 놓여요.
   - A 프로젝트에는 `Assets/ClaudeHand` 폴더가 남지 않아요.
4. 같은 요청을 한 번 더 합니다. 이번에는 dry run에서 덮어쓰기 확인을 물어봐야 해요.
5. 직접 짠 MonoBehaviour 스크립트가 붙은 오브젝트로도 한 번 해 봅니다. B에서 컴파일이 끝난 뒤 배치되고, 컴포넌트가 Missing Script가 아니어야 해요.

> 결과 알려주세요: 각 단계 성공 여부, 하위 세션이 보고한 warnings, Console 오류

## 7. Blender 창 찾기와 하위 세션

1. Blender 두 개를 열고 각각 .blend를 저장합니다. 예: `character.blend`, `props.blend`.
2. 각 창에서 `N` → MCP for Blender 탭으로 가서 포트를 **9876**, **9877**로 정하고 서버를 켭니다.
3. `uv run tools/claude_hand.py list`를 실행합니다. Blender 두 개가 파일 경로와 함께 보여야 해요.
4. `uv run tools/claude_hand.py sync` 후 `/agents`를 입력합니다. `blender-character`, `blender-props`가 보여야 해요.
5. "blender-props에게 지금 열린 파일 경로랑 오브젝트 목록 알려달라고 해"라고 합니다. props.blend가 나와야 해요.
6. 두 Blender 창을 **최소화한 채로** 5번을 다시 합니다.

> 결과 알려주세요: 3번 출력, 5·6번이 됐는지와 걸린 시간, 하위 세션이 Blender 도구가 없다고 했는지

## 8. Blender → Blender

1. props.blend에 머티리얼을 입힌 오브젝트(예: `Rock`)를 하나 둡니다.
2. "props.blend의 Rock을 character.blend로 가져와"라고 합니다.
3. 기대 결과:
   - 교환 폴더에 `.blend`와 `.claudehand.json`이 생겨요.
   - character.blend 씬에 Rock이 머티리얼과 함께 나타나요.
   - 이미 같은 이름이 있으면 `Rock.001` 같은 이름으로 들어오고, 그 이름이 보고돼요.

## 9. Blender → Unity

1. "character.blend의 <캐릭터 오브젝트>를 Unity A 씬에 넣어줘"라고 합니다.
2. 기대 결과:
   - 교환 폴더에 `.fbx`가 생겨요.
   - A의 `Assets/ClaudeHand/Imports/...`에 FBX가 들어오고, 씬에 배치돼요.
   - 크기와 방향이 Blender와 같게 보여야 해요(앞이 -Z, 위가 Y).
3. "같은 걸 B 프로젝트에도 넣어줘"라고 합니다. FBX를 다시 내보내지 않고 같은 manifest로 B에 가져와야 해요.

> 결과 알려주세요: 크기·방향·재질이 어떻게 보였는지, 하위 세션의 warnings, Blender나 Unity Console 오류
