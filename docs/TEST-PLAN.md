# PC 검증 순서

순서대로 해 주시고, 각 단계의 **결과(성공/실패, 오류 문구)**를 알려주시면 이어서 고칠게요.
Unity 프로젝트는 두 개를 준비합니다. 예: `A` = 작업 중인 프로젝트, `B` = 빈 테스트 프로젝트.

새 버전을 받았으면 화면 확인 전에 `uv run tools/claude_hand.py update` 뒤 `uv run tools/claude_hand.py selftest`부터 돌려 주세요. 화면이 필요 없는 확인(테스트 두 묶음, 열린 Unity의 `ch_*` 도구·씬 뷰 찍기, 열린 Blender의 채팅 애드온 버전, 채팅 채널, 설치 파일 버전)을 한 번에 하고, 빠진 것은 고치는 방법과 함께 `!!`로 보여 줘요.

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
3. Claude Hand Tools가 자기 통로를 열었는지 봅니다. claude-hand 폴더에서 실행해요.
   ```
   uv run tools/unity_bridge.py --list
   uv run tools/claude_hand.py unity-call "<프로젝트 A 이름>" ch_editor_status
   ```
   - 기대 결과: `--list`에 두 프로젝트가 `answers`로 보이고, `unity-call`은 `project`, `focused`, `exchange_dir`가 담긴 응답을 보여요.
   - 이 통로는 MCP for Unity가 커스텀 도구를 내보내는지와 상관없이 동작해요(MCP for Unity 10.3.0 stdio는 ch_* 도구를 내보내지 않았어요).
   - claude-hand 폴더의 Claude Code 세션에서는 MCP for Unity 도구가 막혀 있어요. 감독이 하위 세션을 건너뛰지 않게 하려는 설정이에요. 그래서 이 확인은 명령으로 해요.
4. Unity에서 아무 스크립트나 고쳐 저장하고, 컴파일이 도는 동안 바로 3의 `unity-call`을 다시 실행합니다.
   - 기대 결과: 컴파일이 끝날 때까지 기다렸다가 응답이 와요. `EDITOR_BUSY`나 `UNREACHABLE`로 끝나면 걸린 시간과 함께 알려주세요.

> 결과 알려주세요: `--list` 출력, `unity-call` 응답 또는 오류 문구, 4에서 걸린 시간

## 2. 백그라운드 동작 실측 (가장 중요)

Interaction Mode를 **바꾸기 전과 후**에 각각 해 봅니다. Edit > Preferences > General > Interaction Mode예요. 바꾸기는 `uv run tools/claude_hand.py unity-call "<A>" ch_apply_settings action=apply`로, 되돌리기는 `action=revert`로 할 수 있어요.

| 상태 | 방법 | 응답 |
|---|---|---|
| 포커스 | Unity 창을 클릭해 둔 채 `unity-call "<A>" ch_editor_status` | 즉시 / 몇 초 / 30초 이상 / 실패 |
| 비활성 | 다른 창(브라우저 등)을 클릭해 둔 채 같은 명령 | |
| 최소화 | Unity 창을 최소화한 채 같은 명령 | |
| 최소화 + 변경 | 3단계를 마친 뒤, 최소화한 채 감독에게 "A에 큐브 하나 만들어줘" | |

응답의 `focused` 값이 실제 상태와 맞는지도 봐 주세요.

> 결과 알려주세요: 표를 채워서 (설정 변경 전/후)

## 3. 하위 세션 만들기

1. A와 B를 둘 다 연 상태로 `uv run tools/claude_hand.py list`를 실행합니다. 두 프로젝트가 **실행 중**으로 보여야 해요.
2. `uv run tools/claude_hand.py sync`를 실행합니다.
3. Claude Code를 다시 시작하고 `/agents`를 입력합니다. `unity-a`, `unity-b`가 보여야 해요.
4. 메인 세션에서 "unity-a에게 ch_editor_status 불러서 프로젝트 이름 알려달라고 해"라고 합니다.
   - 기대 결과: A의 이름이 나와요. 하위 세션이 쓴 도구 이름이 `mcp__unity_a_ch__ch_editor_status`처럼 `_ch`가 붙은 서버 것이어야 해요(claude-hand 통로).
   - `_ch` 도구가 없다고 하면 하위 세션에게 `ch_bridge_status`를 불러 달라고 해서 나온 이유를 알려주세요.
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
3. 그 창에 안내 문구(Unity·Blender 채팅 위치, "닫지 마세요")가 나오는지 봅니다. 처음이면 확인 질문 안내도 나와야 해요.
   - `.mcp.json` 서버 사용 승인 질문이 따로 뜨는지도 알려주세요. 미리 승인되도록 해 뒀어요.
4. 바탕화면 아이콘을 한 번 더 눌러 봅니다. "이미 실행 중입니다"가 나오고 새 감독 세션이 열리지 않아야 해요.
5. 감독 세션을 켠 상태에서 **새 Unity 프로젝트**(또는 새 Blender 파일)를 열고 바로 채팅합니다. `sync` 없이 담당이 자동으로 생기고 답이 와야 해요. 처음 메시지는 몇 초 더 걸릴 수 있어요.
6. Blender 채팅 패널에서도 2번처럼 버튼으로 켜지는지 봅니다.

> 결과 알려주세요: 어느 단계에서 막혔는지, 뜬 창의 문구, 질문이 몇 개 떴는지

## 12. 데스크톱 앱 + 채팅창 같이 쓰기 (다음 작업의 전제 확인)

cmd 창에서 도는 감독 세션을 데스크톱 앱에서 보고 쓰면서(Remote Control), Unity·Blender 채팅창도 함께 되는지 확인합니다. **이게 되는지에 따라 다음 작업이 정해져요.**

준비:
1. `git pull` 후 Claude Code를 다시 시작하고 `uv run tools/claude_hand.py update`를 실행합니다.
2. 채팅창 모드인지 확인해요. `uv run tools/claude_hand.py mode panels`를 실행하면 돼요(13단계를 해 봤다면 꼭 하세요).
3. `uv run tools/claude_hand.py remote-control on`을 실행합니다. "주의"가 나오면 그 문구를 적어 주세요.
4. 열려 있는 Claude Hand(cmd 창)가 있으면 닫고, 바탕화면 아이콘으로 다시 켭니다.

확인:
- **A. 시작:** cmd 창에서 Claude Code가 오류 없이 켜지나요? 안내 문구에 "진행 보기: …Code 탭…"이 보이나요? 오류가 나면 그 문구를 그대로 알려주세요. Remote Control과 채널 옵션을 같이 받지 못하는 경우가 여기서 드러나요.
- **B. 데스크톱 앱에 보이는지:** Claude 데스크톱 앱 → **Code** 탭의 세션 목록에 **Claude Hand**가 있나요? 열면 cmd 창과 같은 대화가 보이나요? 없으면 claude.ai/code에서도 찾아봐 주세요.
- **C. 채팅창 → 데스크톱:** Unity 채팅창에서 "큐브 하나 만들어줘"라고 보냅니다.
  - 그 메시지가 데스크톱 앱 화면에도 보이나요?
  - 하위 세션(unity-…)이 도는 모습이 보이나요?
  - 답이 **Unity 채팅창에** 오나요?
- **D. 데스크톱 → 작업:** 데스크톱 앱에서 직접 "MyGame 씬에 구 하나 만들어줘"라고 칩니다. 같은 세션이 받아서 처리하나요? cmd 창에도 같은 대화가 보이나요?
- **E. 허락 요청:** 허락이 필요한 일이 생기면 확인창이 어디에 뜨나요(cmd 창, 데스크톱 앱, 채팅창)? 각각에서 누를 수 있었나요?
- **F. 최소화:** cmd 창을 최소화한 채 C·D를 한 번 더 해 봅니다.
- **G. 상태:** `uv run tools/claude_hand.py chat-status`가 "OK 감독 세션이 채팅을 받고 있습니다"를 출력하나요?

> 결과 알려주세요: A~G 각각 됨/안 됨, 안 된 곳의 문구나 화면(스크린샷이 있으면 가장 좋아요)

## 13. 하나로 정리한 방식 (처음 메시지로 켜기, 자동 최소화, 현재 상황, 기록 맞추기)

준비: claude-hand를 `git pull`하고 `uv run tools/claude_hand.py update`를 실행합니다. Unity 프로젝트 하나와 Blender 파일 하나를 열어 둡니다. Blender는 채팅 애드온을 다시 설치합니다(`blender-install-chat`, 버전 0.2). Claude Hand는 꺼 둡니다.

- **A. 처음 메시지로 켜기:** Unity 채팅창에 "씬 구조 알려줘"라고 보냅니다. 채팅창에 "나 (Claude Hand가 켜지면 보냅니다)"가 보이고, Claude Hand 창이 열려야 해요. 확인 질문에 답하면 그 메시지가 전달돼서 답이 와야 해요.
- **B. 개발용 채널 경고:** (확인됨: 켤 때마다 뜸) A에서 경고에 "I am using this for local development"를 고르는 동안 창이 가려지지 않았는지만 봅니다.
- **C. 자동 최소화:** A에서 답이 오기 시작할 때 Claude Hand 창이 저절로 최소화됐나요? 확인 질문이 떠 있는 동안 창이 최소화되면 안 돼요. 그런 일이 있었으면 꼭 알려 주세요.
- **D. 현재 상황:** Blender에서 오브젝트 하나를 선택하고, 데스크톱 앱의 "Claude Hand" 세션에서 "Blender에서 선택한 거 이름 알려줘"라고 합니다. 이름을 묻지 않고 맞혀야 해요. Unity에서 다른 걸 선택한 뒤 "지금 선택한 거"라고 해도 마지막에 쓴 창 기준으로 답하는지 봅니다.
- **E. 기록 맞추기:** 데스크톱 앱에서 "Unity에 큐브 하나 만들어줘"라고 합니다. 끝난 뒤 Unity 채팅창에 "데스크톱 앱에서" 아래 요청과 결과가 보여야 해요. 채팅창을 한 번도 쓰지 않은 Blender 창에도 같은 식으로 시켜 봅니다.
- **F. 데스크톱 앱에서 켜기:** Claude Hand를 닫고, 데스크톱 앱에서 Code 탭 세션을 새로 열어 "Claude Hand 켜줘"라고 합니다. Claude Hand 창이 열리고, 세션 목록의 "Claude Hand"로 옮겨 가라는 안내가 나와야 해요. 이미 켜져 있을 때 한 번 더 말하면 "이미 켜져 있습니다"가 나와야 해요.
- **G. Remote Control 기본값:** `uv run tools/claude_hand.py remote-control`이 "켜짐 (기본값)"을 보여 주나요? 데스크톱 앱 세션 안에서 실행해도 `주의:`가 나오지 않아야 해요.

> 결과 알려주세요: A~G 각각 됨/안 됨, B·C에서 창이 가려진 적이 있는지

## 14. 대화 이어 쓰기 (며칠에 걸친 작업)

Claude Hand를 다시 켜면 새 대화가 아니라 **같은 감독 대화를 이어서** 엽니다(`claude --resume`). 감독 대화 ID는 `~/.claude-hand/config.json`의 `supervisor_session`에 있고, 처음 한 번은 이 폴더에서 가장 최근의 "Claude Hand" 대화를 이어받아요. 채팅창의 최근 대화(창마다 100개)도 `~/.claude-hand/chat/history/`에 남아요.

- **A. 이어서 열기:** Claude Hand 창을 닫고 다시 켭니다. 안내에 "지난 대화를 이어서 엽니다"가 나오고, 창에 이전 대화가 보이나요?
- **B. 기억:** 데스크톱 앱의 "Claude Hand" 세션에서 "아까 마지막으로 한 작업이 뭐였지?"라고 묻습니다. 다시 켜기 전의 일을 답해야 해요. 데스크톱 앱 화면에도 이전 대화가 보이는지 봅니다.
- **C. 채팅창 기록:** 다시 켠 뒤 Unity·Blender 채팅창에 이전 대화가 남아 있나요?
- **D. 하위 세션:** 다시 켠 뒤 같은 채팅창에서 일을 시킵니다. 이전 하위 세션을 이어 쓰거나, 안 되면 새로 맡겨서라도 처리돼야 해요.
- **E. 새로 시작:** `uv run tools/claude_hand.py new-session` 뒤 다시 켜면 "새 대화로 시작합니다"가 나오고 빈 대화로 시작하나요? 이전 대화는 `claude --resume` 목록에 남아 있어야 해요.

> 결과 알려주세요: A~E 각각 됨/안 됨, B에서 데스크톱 앱에 이전 대화가 보였는지

## 15. 작업 중 표시

`update` 뒤 Claude Hand를 다시 켭니다(새 채널 서버·지침). Blender는 채팅 애드온 0.3.1(다시 열기). 채팅창 입력칸 바로 위에 글 한 줄이 떠야 해요. 막대는 없어요.

- **A. 동작 표시:** Unity 채팅창에 "큐브 3개를 만들어서 x축으로 2칸씩 띄워 일렬로 놓아줘"라고 합니다. 일하는 동안 "작업 중 · N초 · 지금 하는 동작(예: `manage_gameobject create Cube`) · N번째 동작"이 나오고 바뀌어야 해요. 답이 오면 사라져야 해요. Blender 채팅창에서도 해 봅니다.
- **B. 막히지 않음:** 이 표시 때문에 작업이 느려지거나, 답 뒤에도 남아 있는 일이 없는지 봅니다.

이 표시는 하위 세션의 guard 훅이 채널 서버로 직접 보내서 Claude가 보지 않아요(토큰 안 씀). 감독이 단계를 세어 채우던 단계 막대(`progress` 도구)와, 그 뒤 남은 오가는 막대 그림도 사용자 요청으로 뺐어요.

> 결과 알려주세요: A·B 각각 됨/안 됨

## 16. 이번 요청 되돌리기

하위 세션이 요청마다 첫 동작 직전에 되돌리기 지점을 찍습니다(Unity는 `ch_checkpoint`, Blender는 채팅 애드온이 Blender 실행 취소 기록에). `update` 뒤 Claude Hand를 다시 켜고, Unity가 새 패키지를 컴파일한 뒤 해 봅니다.

- **A. 버튼:** Unity 채팅창에 "큐브 3개를 만들어서 일렬로 놓아줘"라고 합니다. 답이 온 뒤 입력칸 위에 "되돌리기: 큐브 3개를… (시각)" 버튼이 보이나요? 일하는 동안에는 안 보여야 해요.
- **B. 되돌리기:** 버튼을 누르면 확인창이 뜨고, [되돌리기]를 누르면 큐브 3개가 한 번에 없어지고 채팅창에 "되돌렸어요: …"가 나와야 해요. [취소]를 누르면 그대로여야 해요.
- **C. 경계:** 큐브를 만든 요청 다음에 "구 하나 더 놓아줘"를 시킨 뒤 되돌리기를 누릅니다. 구만 없어지고 큐브는 남아야 해요. 한 번 더 누르면 큐브까지 없어져요.
- **D. 말로 취소:** 일을 시킨 뒤 채팅창에 "방금 거 취소해줘"라고 합니다. 하위 세션이 `ch_checkpoint`로 되돌리고 무엇을 되돌렸는지 답해야 해요. 방금 시킨 일이 실제로 없어졌는지 꼭 봅니다(하위 세션이 다른 도구를 먼저 쓰면 빈 지점으로 되돌려 아무것도 안 바뀌던 문제를 고쳤어요).
- **E. 다음 메시지:** 버튼으로 되돌린 뒤 보내는 첫 메시지에 "되돌렸음" 안내가 같이 가서, 감독이 없어진 큐브를 아직 있다고 여기지 않는지 봅니다.

**Blender:** 채팅 애드온 0.3.0이 필요해요(`update` 뒤 Blender를 다시 엽니다).

- **F. Blender 버튼:** Blender 채팅창에서 "큐브 2개 만들어서 빨간 재질 입혀줘"라고 합니다. 답이 온 뒤 입력칸 위에 "되돌리기: … (시각)" 버튼이 보이고, 누르면 확인창 → [되돌리기]에 큐브와 재질이 한 번에 없어지고 "되돌렸어요: …"가 나와야 해요. Edit > Undo History에 "Claude Hand: …" 단계가 보여요.
- **G. Blender 경계·말로 취소:** C처럼 요청 두 개 뒤 한 번 누르면 뒤의 것만, D처럼 "방금 거 취소해줘"로도 되돌아가는지 봅니다.
- **H. 다시 살리기:** 되돌린 뒤 Blender에서 Ctrl+Shift+Z(다시 실행)를 몇 번 누르면 Claude가 한 일이 돌아와야 해요(끝은 Undo History의 "Claude Hand: 되돌리기 전" 단계).

> 결과 알려주세요: A~H 각각 됨/안 됨, 되돌아가지 않은 변경이 있었다면 무엇이었는지

## 17. 프로젝트·파일 메모

오래 지킬 규칙(이름 규칙, 폴더, 재질 설정, 하지 말 것)을 파일에 남겨, 다시 켜거나 하위 세션이 새로 떠도 지키게 합니다. Unity는 `<프로젝트>/ClaudeHand/NOTES.md`, Blender는 `.blend` 옆 `<파일 이름>.claude-hand.md`예요. `update` 뒤 다시 켭니다.

- **A. 남기기:** Unity 채팅창에 "앞으로 이 프로젝트에서 만드는 오브젝트 이름은 CH_로 시작하게 해줘. 기억해 둬"라고 합니다. 감독이 메모 파일에 적었다고 답하고, 파일이 생겼나요? 메모 파일 하나만 미리 허용해 두어서 허락 버튼은 뜨지 않아야 해요.
- **B. 지키기:** `uv run tools/claude_hand.py new-session` 뒤 다시 켜서(감독도 하위 세션도 새로) "큐브 하나 만들어줘"라고 합니다. 이름이 `CH_`로 시작해야 해요.
- **C. Blender:** 저장된 `.blend`를 연 Blender에서 같은 식으로 해 봅니다. 저장 안 된 파일에는 메모 자리가 없어요.

> 결과 알려주세요: A~C 각각 됨/안 됨, 메모 파일 내용

## 18. Blender 한글 입력창 (Windows)

Blender 입력칸은 Windows 한글 입력기와 잘 맞지 않아요. 조합 중인 글자가 다음 글자를 칠 때에야 보이고, 글자를 끝내는 띄어쓰기·기호가 빠져요. Blender가 Python에 키를 넘기기 전에 생기는 일이라 애드온으로 고칠 수 없어서, Windows 입력창을 따로 띄웁니다. 채팅 애드온 0.4.0이 필요해요(`update` 뒤 Blender 다시 열기).

- **A. 열기:** Blender 채팅창 입력칸 오른쪽의 **[한글]** 버튼을 누릅니다. 마우스 근처에 "Claude Hand · <파일 이름>" 창이 뜨고, Blender 위에 계속 떠 있나요?
- **B. 한글:** "안녕하세요, 큐브 하나 만들어 줄래? (x=2)"처럼 띄어쓰기·기호를 섞어 칩니다. 글자가 바로 보이고 빠지는 글자가 없어야 해요.
- **C. 보내기:** Enter를 누르면 입력창이 비고, Blender 채팅창에 그 메시지가 "나"로 올라가 감독에게 가야 해요. Shift+Enter는 줄바꿈이에요. "선택 항목 함께" 체크도 그대로 따라요.
- **D. 닫기:** Esc나 창 닫기로 닫히고, [한글]을 다시 누르면 다시 떠요. 열려 있을 때 누르면 새 창 없이 그 창이 앞으로 와요. Blender를 끄면 함께 닫혀요.

> 결과 알려주세요: A~D 각각 됨/안 됨, 입력창 크기·위치가 불편하지 않은지

## 19. 결과 미리보기

한 창에서 시킨 일이 다른 창을 바꾸면, 답이 올 때 채널 서버가 바뀐 창을 직접 찍어 그 채팅창에 그림으로 붙여요(Unity는 씬 뷰, Blender는 가장 큰 3D 뷰포트). Claude를 거치지 않아 토큰이 들지 않아요. `update` 뒤 **Claude Hand를 다시 켜고**(새 채널 서버), Unity가 새 패키지를 컴파일하고 Blender 채팅 애드온이 0.5.0이어야 해요.

- **A. Blender → Unity:** Blender 채팅창에서 "선택한 오브젝트를 Unity로 보내줘"라고 합니다. 답 아래에 "hand_test (Unity) 지금 모습" 그림이 붙고, [크게 보기]로 원래 크기로 열리나요?
- **B. Unity → Blender:** Unity 채팅창에서 "Blender의 구를 빨갛게 해줘"라고 합니다. 답 아래에 Blender 뷰포트 그림이 붙고, 누르면 크게 열리나요?
- **C. 같은 창:** Unity 채팅창에서 Unity 일만 시키면 그림이 붙지 않아야 해요(이미 보고 있으니까). "하는 중" 답에도 붙지 않고, 끝났다는 답에만 붙어야 해요.
- **D. 다시 켠 뒤:** Claude Hand를 다시 켜도 채팅창에 그림이 남아 있나요? (그림은 최근 60장만 남아요)

> 결과 알려주세요: A~D 각각 됨/안 됨, 그림 크기가 적당한지

## 20. 끝났다는 알림

다른 프로그램을 보는 동안 그 창에서 시킨 일의 답이나 허락 요청이 오면, Unity·Blender의 작업 표시줄 단추가 깜빡여요(누르거나 그 창으로 가면 멈춤). Unity 채팅 탭 이름에는 안 읽은 수가 "Claude Hand (1)"처럼 글자로 붙어요. Claude를 거치지 않아 토큰이 들지 않아요. Unity는 새 패키지 컴파일, Blender는 채팅 애드온 0.6.0이 필요해요.

- **A. Blender:** Blender 채팅창에서 시간이 좀 걸리는 일을 시키고 바로 다른 프로그램(브라우저 등)으로 갑니다. 답이 오면 Blender 작업 표시줄 단추가 깜빡이나요? Blender로 돌아가면 멈추나요?
- **B. Unity:** Unity 채팅창에서 같은 식으로 해 봅니다. 작업 표시줄 단추가 깜빡이나요?
- **C. 안 읽은 수:** Unity에서 채팅 탭이 아닌 다른 탭(Scene 등)을 누른 채 기다리면, 답이 왔을 때 탭 이름이 "Claude Hand (1)"이 되고, 채팅 탭을 누르면 "Claude Hand"로 돌아오나요?
- **D. 조용해야 할 때:** 그 창을 보고 있을 때, 데스크톱 앱에서 시킨 일의 답, Claude Hand를 다시 켜서 이전 대화가 다시 보일 때는 깜빡이지 않아야 해요.

> 결과 알려주세요: A~D 각각 됨/안 됨, 깜빡임이 거슬리지 않는지

## 21. 대화상자 자동 감지 (Unity, Windows)

Unity가 "씬을 저장할까요?" 같은 대화상자에서 멈추면, 시간 초과를 기다리지 않고 바로 알아채요. 하위 세션의 다음 호출은 대화상자 내용(제목·글·버튼)과 함께 막혀 감독에게 보고되고, 그 일을 시킨 다른 창의 채팅에는 "…멈춰 있어요. Unity에서 직접 골라 주세요"가 뜨고 작업 표시줄이 깜빡여요. 무엇을 누를지는 항상 사용자가 정해요. `update` 뒤 **Claude Hand를 다시 켜고**(새 채널 서버·하위 세션 지침), Blender 채팅 애드온 0.7.0이 필요해요.

- **A. 다른 창에서 시킨 일:** Unity 씬을 저장하지 않은 상태로 두고, Blender 채팅창에서 "Unity에서 새 씬을 열어줘"(저장할지 묻는 창이 뜨는 일)라고 합니다. Blender 채팅창에 Unity가 멈췄다는 안내와 버튼 이름이 뜨나요? 감독도 같은 내용으로 무엇을 누를지 묻나요?
- **B. Unity에서 고르기:** Unity에서 버튼을 직접 누른 뒤 "이어서 해줘"라고 하면 일이 이어지나요?
- **C. 같은 창:** Unity 채팅창에서 같은 일을 시키면, 감독이 대화상자 내용으로 물어 오나요? (Unity 채팅창 자체는 대화상자가 떠 있는 동안 멈춰 있어요)

> 결과 알려주세요: A~C 각각 됨/안 됨, 안내가 뜨기까지 걸린 시간
