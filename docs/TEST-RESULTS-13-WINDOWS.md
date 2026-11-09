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
- **단계 막대 뺌(16·17단계 확인 뒤):** 스킬 규칙을 넣은 뒤에도 단계 막대가 안 나옴. 사용자 판단: 단계 막대는 감독이 도구를 한 번 더 부를 때마다 토큰이 들어 싫어할 것, 동작 표시만으로 충분. `progress` 도구·미리 허용 규칙·채널 지침 문장·스킬 규칙과 패널의 비율 막대를 지움. 동작 표시(guard 훅 → 채널 서버, Claude는 보지 않음)는 그대로, 되돌리기 지점도 이 보고에 기대므로 그대로. Blender 애드온 0.2.3. 테스트 176개 OK, Blender 안 막대 테스트 OK, Unity 다시 컴파일 오류 없음.
- **막대 그림도 뺌(Blender 되돌리기 확인 뒤):** 사용자가 Blender에서 해 보니 오가는 막대가 여전히 보임 → 막대 자체를 없애 달라고 함. Unity·Blender 모두 입력칸 위 글 한 줄 "작업 중 · N초 · <동작> · N번째 동작"만 남김(Blender 애드온 0.3.1, 다시 그리기도 0.15초 → 0.5초, Unity는 0.1초 → 0.5초). 테스트 180개 OK, Blender 안 테스트 OK(설치 테스트만 예전 환경 문제), Unity 다시 컴파일 오류 없음, 열린 Blender에 0.3.1을 다시 불러옴. **글 한 줄 표시는 사용자 확인 전.**
- 덤: 다시 켠 뒤에도 감독이 이전 하위 세션을 `SendMessage`로 이어 씀(14단계 D 보강).

## 16단계: 이번 요청 되돌리기 (같은 날 추가, Unity만)

- `ch_checkpoint`(Unity 패키지, 새 도구): `mark`가 새 Undo 그룹을 열고 이름을 붙여 SessionState에 둠(20개, 스크립트 다시 불러와도 남고 에디터를 끄면 사라짐 — Undo 기록과 같음). `undo`는 가장 최근 지점까지 `Undo.RevertAllDownToGroup`, `list`.
- 채널 서버: 동작 보고(`/v1/progress`)가 그 요청의 첫 동작이면 `new_request`와 요청 앞부분(40자)을 돌려줌. 하위 세션의 guard가 그때 Claude Hand Tools 통로로 `ch_checkpoint mark`를 부름(첫 동작 전에, 실패하면 건너뜀).
- Unity 채팅창: 일이 끝나면 입력칸 위에 "되돌리기: <요청> (시각)" 버튼. 확인창 뒤 되돌리고 "되돌렸어요" 안내, 다음 메시지에 "되돌렸음"을 같이 보내 감독이 알게 함.
- 하위 세션 지침: `execute_code`로 바꿀 때 Undo에 남기기, "방금 거 취소해줘"면 `ch_checkpoint undo`.
- 실제 Unity 확인(스크립트로): mark → MCP for Unity `manage_gameobject create` → undo에 오브젝트 없어짐. 경계: 요청 A·B 뒤 undo 한 번에 B만, 두 번에 A까지. 확인용 오브젝트는 지움. 채팅창에서 사용자 확인: 됨.
- Blender는 아직 없음(Blender Undo는 다른 방식이라 따로 해야 함). → 아래 "Blender 되돌리기"로 추가.

### Blender 되돌리기 (같은 날 추가)

사용자 요청: Blender에도 실행 취소가 있으니 되돌리기 버튼을.

- Blender는 Python에서 실행 취소 기록 목록을 볼 수 없음. 그래서 지점을 데이터에 남김: 모든 씬의 숨은 속성 `claude_hand_undo`에 "이번 실행:번호"를 넣고 `ed.undo_push("Claude Hand: <요청>")`. 되돌릴 때는 "되돌리기 전" 단계를 하나 밀어 넣고(다시 실행으로 살릴 수 있게), 상태의 번호가 그 지점보다 작아질 때까지 `ed.undo`, 그다음 `ed.redo` 한 번 = 지점을 찍은 바로 그 상태. 실행 취소 단계 한도(기본 32)를 넘었거나 맨 아래에 닿으면 처음 자리로 다시 실행해 돌려놓고 "되돌리지 못했습니다". Ctrl+Z로 이미 지점 앞까지 갔으면 "이미 되돌려져 있습니다". 다른 파일을 열면 지점을 비움(`load_post`).
- 채팅 애드온 0.3.0: `mark_undo_point(label)`, `undo_last_request()`, 입력칸 위 "되돌리기: <요청> (시각)" 버튼(확인창 → 타이머에서 실행, 연산자 안에서 undo하면 연산자가 쥔 데이터가 풀림), 채팅창 안내 "되돌렸어요: …", 다음 메시지에 "되돌렸음" 안내.
- guard: 요청의 첫 Blender 동작 직전에 MCP for Blender `execute_code`로 `claude_hand_chat.mark_undo_point(...)`(애드온이 없으면 그냥 건너뜀). 하위 세션 지침: `ed.undo` 직접 쓰지 않기, "취소해줘"면 `undo_last_request()`.
- **Unity·Blender 공통으로 고친 것:** "방금 거 취소해줘"도 새 요청이라, 하위 세션이 스냅샷 같은 다른 도구를 먼저 쓰면 그 자리에 새 지점이 찍혀 되돌리기가 빈 지점으로 가서 아무것도 안 바뀜. guard가 되돌리기 호출(`ch_checkpoint`, `undo_last_request`) 앞에서는 지점을 찍지 않고, 지침에 "다른 도구보다 먼저 되돌린다"를 넣음. 16단계 D는 이 문제를 겪었을 수 있어 다시 확인 필요.
- 확인: 열린 Blender(5.2, GUI)에서 애드온을 껐다 켜서 0.3.0을 불러온 뒤 스크립트로 — guard와 같은 함수로 지점 A·B, 연산자(큐브)와 데이터 API(빈 오브젝트)로 바꿈, `undo_last_request()`에 B만 없어짐, 버튼 연산자(타이머 경로)에 A까지 없어짐, 다음 메시지 안내가 생김, 숨은 속성은 Custom Properties에 안 보임. 채팅창 안내도 남음. 확인용 오브젝트는 지움. Blender 안 테스트(background에서도 실행 취소가 동작함): 실제 되돌리기·경계·이미 되돌려짐·파일 열면 비움·걸음 실패 시 제자리. Python 테스트 180개 OK. 사용자 확인: Blender 채팅창에서 되돌리기 잘 됨. Ctrl+Shift+Z로 살리기(H)는 따로 확인 안 함.

## 17단계: 프로젝트·파일 메모 (같은 날 추가)

- 메모 자리: Unity `<프로젝트>/ClaudeHand/NOTES.md`(Assets 밖이라 Unity가 가져오지 않음), Blender `<파일>.claude-hand.md`(저장 안 된 파일은 없음). 하위 세션 정의에 경로와 "시작할 때 읽고 지킨다", 감독 스킬에 "앞으로도 지킬 규칙이면 날짜와 한 줄로 더한다".
- 허락: 메모는 이 폴더 밖이라 Read·Edit가 매번 허락을 물음(`claude -p`로 확인: 규칙 없으면 `DENIED`). sync·update·채팅 시작 때 창마다 그 메모 파일 하나만 `Read(//c/…)`·`Edit(//c/…)`로 미리 허용. 같은 방식으로 읽기·고치기·새로 만들기(폴더 없을 때도)까지 허락 없이 되는 것을 확인.
- 테스트 177개 OK. 실제 사용에서 사용자 확인: 메모를 남기고 지킴.

## 18단계: Blender 한글 입력창 (같은 날 추가)

사용자 지적: Blender 채팅 입력에서 스페이스바·특수기호가 씹히고, 한글이 한 박자 늦게 보임.

- 원인: Blender 5.2.1 자체의 Windows 한글 입력 처리. 이 PC의 Blender는 UI 언어 en_US지만, IME를 표시 언어에 묶던 검사(`BLT_lang_is_ime_supported`)는 2021~2022년에 없어져 언어 설정 문제는 아님. Blender 개발자들도 Windows에서 중국어·일본어·한국어 입력이 오래된 문제라고 함(bf-committers 2021-07 "Windows IME Text Entry Changes", 옛 버그 T43569). 조합을 끝내는 키(띄어쓰기·기호)와 조합 중 표시가 Python에 오기 전에 처리돼 애드온으로는 Blender 입력칸을 고칠 수 없음.
- 대신: 채팅 애드온 0.4.0, 입력칸 오른쪽 **[한글]** 버튼이 Windows 입력창(PowerShell WinForms 텍스트 상자, `-EncodedCommand`, 창 없는 PowerShell)을 띄움. 항상 위, 마우스 근처, Enter 보내기(IME가 마지막 글자를 넣은 뒤 보내도록 `BeginInvoke`), Shift+Enter 줄바꿈, Esc 닫기, 빈 메시지는 안 보냄, Blender 프로세스가 끝나면 1초 안에 닫힘. 메시지는 한 줄씩 base64(UTF-8)로 넘어와 그 창의 채팅으로 보냄("선택 항목 함께" 따름). 제목은 base64로 넣어 스크립트로 해석되지 않음. 이미 열려 있으면 그 창을 앞으로.
- 확인: 입력창 자체를 Blender 밖에서 실행해 그 창의 텍스트 상자에만 글자를 보내(PostMessage) — "안녕 하세요?! @#$% (테스트)"와 두 번째 메시지가 그대로 나옴, 공백만은 안 보냄, Esc로 닫힘, 부모가 끝나고 0.2초 뒤 닫힘. 열린 Blender에 0.4.0을 다시 불러와 Blender에서 열기 — 제목 "Claude Hand · test_blender", 두 번 눌러도 창 하나, 닫기 됨. Blender 안 테스트: 입력창이 보낸 줄이 그 창의 채팅으로 감, 깨진 줄·빈 줄은 버림, 제목에 스크립트를 넣어도 실행 안 됨. Python 181개 OK. **한글 입력기로 실제 타이핑은 사용자 확인 전**(자동 입력은 입력기를 거치지 않음).
- Blender 자체 입력칸은 그대로 둠(영어는 문제없음). macOS·Linux에는 버튼 없음.

## 남은 것·참고

- **`Bash(uv run *)` 미리 허용**: 새 `settings.local.json`이 이 폴더 세션의 `uv run …`을 허락 없이 실행하게 함. 12단계 E에서 본 `list` 같은 확인창은 이제 안 뜸. `uv run`은 아무 스크립트나 돌릴 수 있어 범위가 넓음.
- **최소화가 멈춘 정확한 지점**: 옛 방식에서 `AttachConsole`과 `ShowWindow` 중 어디서 멈췄는지는 모름. 새 방식은 둘 다 쓰지 않음.
- **E의 "한 번도 쓰지 않은 Blender 창"**: 감독을 다시 켠 뒤 Blender 채팅창을 건드리지 않고 데스크톱에서 시켜 봐야 함.
- **Blender 줄바꿈 여유(×1.2, 여백 50px)**: 배율 1.0, 사이드바 328px에서 사용자 확인. 다른 배율·글꼴 설정에서는 확인 안 함.
