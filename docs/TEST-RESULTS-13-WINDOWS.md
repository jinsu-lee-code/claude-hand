# 13단계 결과: Windows (2026-11-09)

[TEST-PLAN.md](TEST-PLAN.md) 13단계(처음 메시지로 켜기, 자동 최소화, 현재 상황, 기록 맞추기, 데스크톱 앱에서 켜기)를 [12단계](TEST-RESULTS-12-WINDOWS.md)와 같은 Windows PC에서 돌린 결과입니다. 기준 코드는 `ae9c283`(원격 `40c0ca4`~`a9474e1` 포함)입니다.
**결론: A~G 모두 됨.** 다만 C와 F는 이번에 고친 뒤에 됐고, 테스트 중에 Blender 패널 줄바꿈과 긴 대화 처리도 손봤습니다(아래 "이번에 고친 것").

## 준비

| 준비 | 결과 |
|---|---|
| `update` | 됨. 하위 세션에 `unity_hand_test_ch`(ChBridge 통로) 추가. `.claude/settings.local.json`에 `Bash(uv run *)`·`current_context` 허용, `mcp__UnityMCP__*`·`mcp__blender__*`·`mcp__unity-mcp__*` 차단 |
| `blender-install-chat` | 됨(0.2.0 → 이번 수정 뒤 0.2.1). 이 PC에서 처음 설치 |
| Blender 안 자동 테스트 (Blender 5.2, Python 3.13) | 14개 중 13개 통과. 실패 1개(`HeadlessEnableTests`)는 Blender 안에서 테스트를 돌릴 때 하위 Python에 `bpy`가 없어서 생기는 실행 환경 문제이고, 같은 일을 하는 실제 `blender-install-chat`은 성공 |
| ChBridge | Unity가 새 코드를 오류 없이 컴파일. `unity-call hand_test ch_editor_status`가 0.3초에 응답(Unity가 뒤에 있을 때, `focused: false`). ch_* 6개 등록 |
| 참고 | 새 코드를 처음 컴파일한 직후 hand_test 에디터가 정상 종료됨(충돌 흔적 없음, 원인 모름). 다시 열자 정상. MCP for Unity 포트는 6400이 안 풀려 6401로 이동(`Port 6400 still occupied after 3s`) |

## A~G

| 확인 | 결과 | 메모 |
|---|---|---|
| A. 처음 메시지로 켜기 | 됨 | 남아 있던 `server.json`(이전 감독이 창 닫기로 끝나 정리 안 됨)이 있는 상태에서 시작해도 정상 |
| B. 개발용 채널 경고 | 됨 | 창이 가려지지 않음 |
| C. 자동 최소화 | 고친 뒤 됨 | 처음엔 안 됨. 최소화 하위 프로세스가 감독 안에서 15초 시간 초과(아래 1) |
| D. 현재 상황 | 됨 | 데스크톱 앱에서 물으니 감독이 `current_context`로 Blender의 Cube(18초 전 보고), 이어서 마지막에 쓴 Unity의 Capsule을 맞힘. 채팅창에서 물으면 채팅창이 선택 정보를 같이 보내 그것으로 답함(이 PC에서 Blender 채팅창 첫 사용도 이때 확인) |
| E. 기록 맞추기 | 됨 | Unity·Blender 채팅창 모두 "데스크톱 앱에서" 아래 요청과 결과. Blender 쪽은 이번 감독 세션에서 이미 쓴 채팅창이라 "한 번도 쓰지 않은 창" 경우는 확인 안 함. Blender 사이드바에서 긴 줄이 "..."로 잘림(아래 2) |
| F. 데스크톱 앱에서 켜기 | 됨, 고친 뒤 다시 확인 | 창이 열리고 안내, 두 번째엔 "이미 켜져 있습니다". 그런데 그렇게 켠 감독이 데스크톱 세션의 Claude Code 변수를 물려받음(아래 4) |
| G. Remote Control 기본값 | 됨 | 데스크톱 앱 세션 안에서 실행해도 `주의:` 없음. 이 PC는 `remote-control on`을 직접 해 둬서 "(기본값)" 대신 "켜짐" |

## 이번에 고친 것

1. **자동 최소화가 안 됨** (`tools/chat_channel.py`)
   - 원인: 최소화용 하위 프로세스(`AttachConsole` → `ShowWindow`)가 Claude Code 아래에서 돌 때 15초 시간 초과로 강제 종료됨(`TimeoutExpired`, 두 번 재현). 같은 코드를 밖에서 돌리면 0.05초. 실패해도 아무 기록이 없었음. 어느 호출에서 멈추는지는 특정하지 못함(입력 파이프 조건은 재현 안 됨).
   - 고침: 하위 프로세스 없이 서버 안에서 `EnumWindows`로 claude와 그 부모 cmd가 주인인 **보이는** `ConsoleWindowClass` 창을 찾아 `ShowWindowAsync(SW_MINIMIZE)`. 기다리는 호출이 없어 멈출 수 없음. 숨은 콘솔(Unity·relay 등)은 건드리지 않음(최소화하면 작업 표시줄에 튀어나옴), Windows Terminal 탭도 그대로 둠. 실제 창에서 0.001초에 최소화, 감독 안에서도 확인.
2. **Blender 사이드바에서 긴 줄이 "..."로 잘림** (`blender/claude_hand_chat.py`)
   - 원인: 글자 수(38자)로만 나눔. 한글은 영문보다 두 배쯤 넓음.
   - 고침: 사이드바 실제 폭(region 폭, UI 배율)과 Blender가 직접 잰 글자 폭(`blf`, 그려지는 라벨과의 차이를 위해 ×1.2)으로 줄바꿈. 띄어쓰기에서 나누고, 띄어쓰기 없는 긴 글(경로 등)은 글자 단위. `blf`가 못 재면 보정한 추정치. 실측(배율 1.0, 11pt): 사이드바 328px, 한글 10px, 영문 소문자 5.6px, 띄어쓰기 3px. 처음 추정치(한글 14px)는 오른쪽이 많이 남아 실측으로 바꿈.
3. **긴 대화 대비** (사용자 요청: 대화의 원본은 Claude 쪽에 있으니 채팅창은 최근 것만)
   - 채널 서버: 창마다 보관 500 → 100개. 8000자를 넘는 메시지·답은 그냥 잘리던 것을 `…(너무 길어 앞의 8000자만 보냅니다)`로 표시하고, `reply`가 잘렸을 때 Claude에게 "요약하거나 나눠 보내라"고 알림.
   - Blender 패널(0.2.1): 보관 60개. 대화는 40줄까지만 그림(최신은 항상, 오래된 건 "이전 대화 N개는 접어 두었습니다"). 그래서 입력칸이 화면 밖으로 밀리지 않음. 한 메시지는 14줄까지 + "…N줄 더 있음 [전체 복사]".
   - Unity 채팅창: 보관 100개, 40개씩 그림("이전 대화 N개 더 보기"), 메시지는 1500자까지 + "전체 복사". 새 메시지는 맨 아래를 보고 있을 때만 따라 내려감(보낼 때는 항상).
4. **"Claude Hand 켜줘"로 켠 감독이 데스크톱 세션의 변수를 물려받음** (`tools/claude_hand.py`, `start-supervisor.cmd/.sh`)
   - 확인: 그렇게 켠 감독 프로세스에 `CLAUDECODE`, `CLAUDE_CODE_CHILD_SESSION`, `CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_HOST_SESSION_ID`, `CLAUDE_CODE_MESSAGING_SOCKET`·`TOKEN`, `CLAUDE_CODE_ENTRYPOINT=claude-desktop`, `CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH` 등 31개가 그대로 있었음. Claude Code는 이 값들로 자신을 하위 세션으로 취급함. Claude Code가 띄운 Unity·Blender의 시작 버튼으로 켤 때도 같음.
   - 고침: preflight가 물려받은 `CLAUDECODE`·`CLAUDE_PID`·`CLAUDE_CODE_*`·`CLAUDE_AGENT_SDK_*`·`CLAUDE_PREVIEW_*` 이름을 `.launch-unset`에 쓰고, start-supervisor가 claude를 켜기 직전에 지움. 사용자가 저장해 둔 환경 변수(Windows 사용자·시스템 환경)와 `CLAUDE_CODE_USE_*`·`CLAUDE_CODE_GIT_BASH_PATH`·`CLAUDE_CODE_OAUTH_TOKEN`은 남김. 시작 안내에 "다른 Claude Code 세션에서 켜져서… N개를 지우고 따로 시작합니다".
   - 다시 확인: 새 감독에는 물려받은 값이 없고, 남은 `CLAUDE_CODE_ENTRYPOINT`(`cli`)·메시지 통로(`cc-msg-fee3…`, 데스크톱 세션은 `cc-msg-3d92…`)는 감독이 스스로 만든 값.

테스트: `tools/tests` 160개 OK (skipped 17). Blender 안 14개 중 13개(나머지 1개는 위 실행 환경 문제). Unity 컴파일 오류 없음.

## 14단계: 대화 이어 쓰기 (같은 날 추가)

테스트 중 사용자 지적: 작업은 며칠씩 이어지는데, Claude Hand를 켤 때마다 새 Claude Code 세션이 생겨(이날만 "Claude Hand" 세션 18개) 감독이 이전 대화를 모르고, 데스크톱 앱에도 따로 뜨고, 채팅창 기록도 비워짐. 대화 파일은 디스크에 남지만 이어지지 않음.

- `tools/claude_hand.py`: preflight가 `~/.claude-hand/config.json`의 `supervisor_session`을 `--resume`으로 엶. 처음 한 번은 이 폴더에서 가장 최근의 "Claude Hand" 대화(Remote Control 이름)를 이어받고, 이전 것이 없으면 `--session-id`로 정한 새 ID. 정해 두고 아직 메시지가 없는 ID는 그대로 `--session-id`. `new-session` 명령으로 다음 시작을 새 대화로(이전 대화는 `claude --resume` 목록에 남음). 시작 안내에 "지난 대화를 이어서 엽니다" / "새 대화로 시작합니다".
- `tools/chat_channel.py`: 감독 채널 서버가 창마다 최근 대화(나·Claude, 100개)를 `~/.claude-hand/chat/history/<chat_id>.json`에 저장하고 시작할 때 불러옴(번호는 새로 매김, 허락 요청은 저장 안 함). 감독 지침에 "다시 켠 뒤 이전 하위 세션이 없으면 새로 맡긴다" 추가.
- TEST-PLAN 14단계 추가. 테스트 165개 OK.

| 확인 | 결과 | 메모 |
|---|---|---|
| A. 이어서 열기 | 됨 | 첫 재시작에 이날 마지막 감독 대화 `2bfcab59…`를 이어받음(`--resume`). 이후 재시작도 같은 ID, 대화 파일 하나로 이어짐 |
| B. 기억 | 됨 | 다시 켠 뒤 "마지막으로 한 작업"에 재시작 전 작업으로 답함. 데스크톱 앱에도 이전 대화 보임 |
| C. 채팅창 기록 | 됨 | Unity 3개·Blender 5개 저장, 다시 켠 뒤 채팅창에 남아 있음 |
| D. 하위 세션 | 됨 | 이어 연 뒤 시킨 일은 감독이 하위 세션을 새로 띄워(`Agent`) 처리 |
| E. 새로 시작 | 확인 안 함 | `new-session`은 단위 테스트만 |

## 15단계: 진행 막대 (같은 날 추가)

사용자 요청: 하위 세션이 일하는 동안 채팅창에서 진행이 보이게, 입력칸 바로 위에 막대로.

- 하위 세션의 PreToolUse guard가 허락한 Unity·Blender 호출마다 채널 서버 `/v1/progress`로 도구 이름과 요약(`create Cube` 등)을 보냄(0.5초 안에 못 보내면 건너뜀, 호출은 기다리지 않음). 감독은 `progress(chat_id, step, total, label)`로 단계를 알릴 수 있음(미리 허용). 서버는 채팅마다 상태를 들고, 패널의 long-poll이 바뀔 때 바로 깸(`prev` 리비전). 답(`reply`)이 막대를 지우고, 5분 동안 소식이 없으면 사라짐.
- Unity 채팅창·Blender 패널(0.2.2): 입력칸 바로 위에 막대. 단계가 있으면 비율대로, 없으면 오가는 막대 + "작업 중 · N초", 위에 지금 동작과 몇 번째인지. 진행 중에만 자주 다시 그림.
- 테스트: Python 172개 OK, Blender 안 14/15(나머지는 실행 환경 문제, 새 테스트는 실제 채널 서버로 동작 → 막대 → 2/4=50% → 답에 사라짐까지).
- 실제 확인: Unity 큐브 3개, Blender 실린더 작업에서 막대가 잘 보임(사용자). **단계 막대는 안 쓰였음**: Blender→Unity 2단계 요청에서 감독이 `progress`를 부르지 않음(지침이 채널 설명에만 있었음) → 감독 스킬의 채팅창 규칙에 "단계가 둘 이상이면 '하는 중' 답 뒤 단계마다 progress"를 추가. 다시 확인 필요.
- 덤: 다시 켠 뒤에도 감독이 이전 하위 세션을 `SendMessage`로 이어 씀(14단계 D 보강).

## 남은 것·참고

- **`Bash(uv run *)` 미리 허용**: 새 `settings.local.json`이 이 폴더 세션의 `uv run …`을 허락 없이 실행하게 함. 12단계 E에서 본 `list` 같은 확인창은 이제 안 뜸. `uv run`은 아무 스크립트나 돌릴 수 있어 범위가 넓음.
- **최소화가 멈춘 정확한 지점**: 옛 방식에서 `AttachConsole`과 `ShowWindow` 중 어디서 멈췄는지는 모름. 새 방식은 둘 다 쓰지 않음.
- **E의 "한 번도 쓰지 않은 Blender 창"**: 감독을 다시 켠 뒤 Blender 채팅창을 건드리지 않고 데스크톱에서 시켜 봐야 함.
- **Blender 줄바꿈 여유(×1.2, 여백 50px)**: 배율 1.0, 사이드바 328px에서 사용자 확인. 다른 배율·글꼴 설정에서는 확인 안 함.
