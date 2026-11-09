# PC 검증 순서

순서대로 해 주시고, 각 단계의 **결과(성공/실패, 오류 문구)**를 알려주시면 이어서 고칠게요.
Unity 프로젝트는 두 개를 준비합니다. 예: `A` = 작업 중인 프로젝트, `B` = 빈 테스트 프로젝트.

## 0. 처음 세팅 (Claude에게 맡기기)

이미 손으로 설치해 둔 것이 있어도 괜찮아요. 세팅 스킬은 빠진 것만 하도록 돼 있어요.

1. claude-hand 폴더에서 Claude Code를 열고 "claude-hand 세팅해줘"라고 합니다.
2. 기대 결과:
   - `doctor` 진단표가 먼저 나와요.
   - 할 단계를 고르는 질문이 하나 와요. 각 선택지에 무엇이 바뀌는지 적혀 있어요.
   - 고른 단계만 진행되고, Claude Code의 명령 실행 허락 요청이 단계마다 와요.
   - UAC나 방화벽 창은 Claude가 누르지 않고 사용자에게 눌러 달라고 해요.
3. 확인할 것:
   - `npm install -g agent-desktop` 뒤에 `agent-desktop status`가 성공하는지(Smart App Control에 막히면 그 문구)
   - `uvx mcp-for-blender setup --dry-run` 출력이 사용자에게 보였는지, `--yes` 뒤에 Blender를 다시 열었을 때 애드온이 켜져 있는지
   - `unity-install` 뒤에 Unity가 패키지를 받아 오는지(Unity가 열려 있었다면 창을 클릭했을 때), 뜬 대화상자를 Claude가 읽어서 물어봤는지
   - `ch_apply_settings(apply)` 결과의 `applied_live` 값과 Preferences > General > Interaction Mode가 No Throttling으로 보이는지
   - 화면 조작을 했다면 마우스·키보드를 움직이기 전에 따로 물어봤는지
   - 마지막 `doctor`에서 남은 항목

> 결과 알려주세요: 단계마다 성공/실패와 오류 문구, Claude가 대신 했으면 안 되는 일을 했는지

## 1. 패키지 설치 확인

1. 두 프로젝트에 패키지를 추가합니다. 0단계에서 했다면 건너뛰어요. 직접 하려면 `uv run tools/claude_hand.py unity-install "<프로젝트>"`를 실행해요.
2. Console에 `ClaudeHand` 관련 빨간 오류가 없어야 합니다.
3. Claude Code 메인 세션에서 "ch_editor_status 불러줘"라고 해 봅니다.
   - Unity 창이 하나만 열려 있을 때 해야 합니다. 둘 이상이면 대상을 고르라는 오류가 나요.
   - 기대 결과: `project`, `focused`, `exchange_dir`가 담긴 응답이 와요.
   - 도구가 안 보이면 "manage_tools sync 해줘"라고 하거나, Claude Code를 다시 시작하거나, `/mcp`에서 다시 연결합니다. 그래도 안 되면 "execute_custom_tool로 ch_editor_status 불러줘"라고 해 봅니다.

> 결과 알려주세요: 오류 문구, 또는 도구가 직접 보였는지 execute_custom_tool로만 됐는지

## 2. 백그라운드 동작 실측 (가장 중요)

Interaction Mode를 **바꾸기 전과 후**에 각각 해 봅니다. Edit > Preferences > General > Interaction Mode예요. 바꾸기는 `ch_apply_settings(action="apply")`로, 되돌리기는 `action="revert"`로 할 수 있어요.

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

## 10. 창마다 채팅

1. `uv run tools/claude_hand.py chat-setup`, `uv run tools/claude_hand.py blender-install-chat`을 실행합니다. 세팅 스킬로 했다면 건너뛰어요.
   - `chat-setup`이 "점검 통과: Python …, 포트 … 사용 가능"을 출력해야 해요. 처음이라 Python을 받느라 오래 걸렸다면 걸린 시간도 알려주세요.
   - claude-hand 폴더에 `.mcp.json`과 `.claude/settings.local.json`이 생겼는지 봐 주세요.
2. 지금 열린 Claude Code를 닫고 `start-supervisor.cmd`로 다시 엽니다. 처음 뜨는 개발용 채널 경고와 `.mcp.json` 서버(claude-hand-chat) 승인을 읽고 승인해요.
   - Claude Code에서 `/mcp`를 입력하면 claude-hand-chat이 connected로 보여야 해요.
   - 같은 폴더에서 `start-supervisor` 없이 Claude Code를 하나 더 열어도, 채팅이 계속 감독 세션으로 가는지 확인해 주세요.
3. Unity A에서 **Window > Claude Hand > Chat**을 엽니다. 위쪽에 `담당: unity-a`처럼 보여야 해요.
4. 오브젝트 하나를 선택하고 "이거 이름 뭐야?"라고 보냅니다.
   - 기대 결과: Claude Code 창에 채널 메시지가 들어오고, 답이 **Unity 채팅창에** 와요. 선택한 오브젝트 이름이 맞아야 해요.
5. 이어서 "그거 위로 2만큼 올려줘"라고 보냅니다. 앞 대화를 기억해서 같은 오브젝트를 옮겨야 해요(같은 하위 세션을 이어 쓰는지 확인).
6. Blender에서 `N` → **Claude Hand** 탭을 열고 같은 식으로 해 봅니다. 위쪽에 `담당: blender-<파일>`이 보여야 해요.
7. Unity 채팅창에서 "Blender <파일>의 <오브젝트>를 이 씬에 넣어줘"라고 합니다. Blender 담당과 Unity 담당을 함께 써서 처리하고, 결과가 Unity 채팅창에 와야 해요.
8. 권한 확인이 필요한 일(예: 아직 허락하지 않은 명령)을 시켜 봅니다. 채팅창에 허용/거부 버튼이 뜨고, 누르면 Claude Code 쪽 확인창도 함께 처리돼야 해요. 답장할 때는 버튼이 뜨지 않아야 해요(reply 도구는 미리 허용됨).
   - `sync --allow-tools`를 했다면 하위 세션의 Unity·Blender 조작에도 버튼이 뜨지 않아야 해요.
9. Unity 창을 최소화했다가 다시 열었을 때, 그리고 스크립트를 고쳐 Unity가 다시 컴파일한 뒤에도 대화 기록이 남아 있는지 봅니다.

채팅창에 감독 세션이 없다고 나오면 `uv run tools/claude_hand.py chat-status` 출력과 채팅창에 뜬 문구를 그대로 알려주세요. 이전 버전을 쓰다가 받았다면 먼저 `uv run tools/claude_hand.py update`를 하세요.

> 결과 알려주세요: 3·6번의 담당 표시, 답이 어느 창으로 왔는지, 5번에서 맥락이 이어졌는지, 8번 권한 버튼이 동작했는지, 1분 넘게 답이 없었다면 Claude Code 창에 무엇이 떠 있었는지

## 11. 쉬운 시작

1. `uv run tools/claude_hand.py shortcut`을 실행합니다. 세팅 스킬로 했다면 건너뛰어요. 바탕화면에 **Claude Hand** 아이콘이 생겨야 해요.
2. 감독 세션이 꺼진 상태에서 Unity 채팅창을 엽니다. **[Claude Hand 시작]** 버튼이 보여야 해요. 누르면 "Claude Hand"라는 검은 창이 떠야 해요.
3. 그 창에 안내 문구(Unity·Blender 채팅 위치, "닫지 말고 최소화")가 나오는지 봅니다. 처음이면 확인 질문 안내도 나와야 해요.
   - `.mcp.json` 서버 사용 승인 질문이 따로 뜨는지도 알려주세요. 미리 승인되도록 해 뒀어요.
4. 바탕화면 아이콘을 한 번 더 눌러 봅니다. "이미 실행 중입니다"가 나오고 새 감독 세션이 열리지 않아야 해요.
5. 감독 세션을 켠 상태에서 **새 Unity 프로젝트**(또는 새 Blender 파일)를 열고 바로 채팅합니다. `sync` 없이 담당이 자동으로 생기고 답이 와야 해요. 처음 메시지는 몇 초 더 걸릴 수 있어요.
6. Blender 채팅 패널에서도 2번처럼 버튼으로 켜지는지 봅니다.

> 결과 알려주세요: 어느 단계에서 막혔는지, 뜬 창의 문구, 질문이 몇 개 떴는지

## 12. Claude 데스크톱 앱에서 진행 보기

1. `uv run tools/claude_hand.py remote-control on`을 실행합니다. "주의"가 나오면 그 문구를 알려주세요.
2. Claude Hand를 닫았다가 아이콘으로 다시 켭니다. 안내 문구에 "진행 보기: …Code 탭…"이 나와야 해요.
3. Claude 데스크톱 앱 → **Code** 탭의 세션 목록에서 **Claude Hand**를 찾아 엽니다.
4. Unity 채팅창에서 일을 하나 시키고(예: "큐브 3개 만들어줘"), 데스크톱 앱에서 다음이 보이는지 확인합니다.
   - 감독이 메시지를 받는 모습
   - 하위 세션(unity-…)이 도는 모습
   - 결과를 채팅창으로 답하는 모습
5. 채팅도 평소처럼 되는지(Remote Control과 채널을 같이 쓸 수 있는지)가 가장 중요해요.

> 결과 알려주세요: 세션이 목록에 보였는지, 하위 세션이 보였는지, 채팅이 계속 됐는지, 2번에서 Claude Hand가 정상적으로 켜졌는지

