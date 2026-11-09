# 12단계 결과: Windows (2026-11-09)

[TEST-PLAN.md](TEST-PLAN.md) 12단계(데스크톱 앱 + 채팅창 같이 쓰기)를 Windows PC에서 처음 돌린 결과입니다.
**결론: A~G 모두 됨.** Remote Control과 Channels를 한 cmd 세션에서 같이 쓸 수 있음(DESIGN.md 가정 20 확인). 다만 처음 설정과 claude-hand에서 막힌 곳이 여럿 있었고, 그중 넷은 이번에 고쳤습니다(아래 "이번에 고친 것").

## 환경

| 항목 | 값 |
|---|---|
| OS | Windows 10 Pro 19045 |
| Claude Code CLI | 2.1.272 → 테스트 중 자동 업데이트 2.1.319 (npm 전역) |
| Claude 데스크톱 앱 | 4.29.0 |
| Unity | 6000.3.25f1, 테스트 프로젝트 `hand_test` (씬 `SampleScene`) |
| MCP for Unity | 패키지 10.3.0 (`com.coplaydev.unity-mcp@4384f8b518e8`), 서버 `mcpforunityserver==10.3.0` (FastMCP 3.4.11), **stdio** |
| 그 밖 | Python 3.14.7 (uv), uv 0.12.21, Git 2.56.0 (테스트 중 설치), Blender 5.2.1 |
| 같이 깔린 것 | Unity AI Assistant (`com.unity.ai.assistant`). 사용자 설정에 `unity-mcp` → `~/.unity/relay/relay_win.exe --mcp` |

## 명령으로 한 확인

| # | 확인 | 결과 | 출력 / 메모 |
|---|---|---|---|
| 1 | `git pull` | 됨 | `Already up to date.` (a960d81). git이 PATH에 없어 GitHub Desktop 내장 git 사용 |
| 2 | `update` | 됨 | 처음엔 `채팅은 아직 설정하지 않았습니다(원하면 chat-setup).` → `chat-setup` 뒤 `점검 통과: Python 3.14.7, 포트 8765 사용 가능, 하위 세션 1개` |
| 3 | `mode panels` | 됨 | |
| 3 | `remote-control on` | 됨 | `주의: ANTHROPIC_BASE_URL이 설정돼 있으면 Remote Control을 쓸 수 없습니다` → 데스크톱 앱 세션 안에서 실행해서 생긴 가짜 경고(과제 5) |
| 4 | unittest | 됨 | 첫 실행 120개 OK (skipped 12). 수정 뒤 124개 OK (skipped 12) |
| 5 | `process_table` (Windows) | 됨 | 0.25~0.34초, 프로세스 274개. 부모 `claude.exe`를 찾고, 채널 플래그를 단 부모를 흉내 내면 `(True, 'Claude Code가 이 서버를 채널로 불러옴')` |
| 6 | `shortcut --dry-run` / `shortcut` | 됨 | 바탕화면·시작 메뉴 `Claude Hand.lnk` → `start-supervisor.cmd` |
| 6 | `doctor` | 됨 | 1.3초. 처음 빠진 항목 5개(git, agent-desktop, 채팅 설정, Blender 채팅 애드온, Remote Control 가짜 경고) |

## A~G

| 확인 | 결과 | 메모 |
|---|---|---|
| A. 시작 | 됨 | 처음엔 켜졌지만 대화하면 `oauth_org_not_allowed`: `Your organization has disabled Claude subscription access for Claude Code · Use an Anthropic API key instead, or ask your admin to enable access`. 상단 `API Usage Billing`, `Remote managed settings failed to load (access denied (403))`. cmd 창에서 `/logout` → `/login`(구독 계정) 뒤 `Claude Max`, `/rc active`로 정상. 안내 문구에 `진행 보기: …Code 탭…` 나옴, `주의:` 없음 |
| B. 데스크톱 앱에 보이는지 | 됨 | 재로그인 뒤 Code 탭 목록에 Claude Hand. cmd 창과 같은 대화 |
| C. 채팅창 → 데스크톱 | 됨 | 처음엔 `이 프로젝트의 하위 세션이 없습니다. uv run tools/claude_hand.py sync 를 실행하세요.`(과제 3, 고친 것 2). 고친 뒤: 메시지가 데스크톱 앱에 보이고, `unity-hand-test` 하위 세션이 `mcp__unity_hand_test__*`로 처리, 답은 Unity 채팅창 |
| D. 데스크톱 → 작업 | 됨 | 같은 세션이 받아 같은 하위 세션에 `SendMessage`로 이어서 맡김. cmd 창에도 같은 대화. 결과는 데스크톱에만 뜸(설계상, 과제 4). 하위 세션 보고: `ch_editor_status`·`ch_scene_snapshot`이 도구 목록에 없음(과제 1) |
| E. 허락 요청 | 됨 | manual mode에서 확인. 하위 세션 Unity 조작 → Unity 채팅창에서 허용, 감독 명령 실행 → 데스크톱 앱에서 허용. 다른 곳 확인창도 함께 처리됨. 같은 세션에서 이미 허락한 같은 명령은 다시 묻지 않음(Claude Code 동작). cmd 창에서 누르는 것은 결과 보고 없음 |
| F. 최소화 | 됨 | cmd 창 최소화 상태로 C(실린더)·D(평면) 다시 함. 둘 다 정상 |
| G. 상태 | 됨 | `OK 감독 세션이 채팅을 받고 있습니다 (포트 8765, 채팅 unity-hand-test)`. 감독 안에서 실행하면 연결이 끊기던 문제는 고친 것 4 |

## 이번에 고친 것

1. **Claude Hand Tools 컴파일 오류**: `unity/com.claudehand.tools/Editor/ChSceneSnapshot.cs:141`
   `PrefabUtility.GetPrefabAssetPathOfNearestPrefabInstanceRoot`는 없는 API(→ `GetPrefabAssetPathOfNearestInstanceRoot`). 어느 Unity에서도 컴파일되지 않았을 이름이라, 패키지를 넣으면 Safe Mode 창이 뜨고 채팅창도 안 열렸음.
2. **Unity AI Assistant의 relay를 MCP for Unity로 오인**: `tools/claude_hand.py`
   `looks_like_unity_server`가 이름에 "unity"가 들어간 사용자 설정 `unity-mcp`(relay_win.exe)를 골라, 하위 세션이 `relay_win.exe --mcp --default-instance …`로 만들어졌음. doctor의 `MCP for Unity 등록: OK`도 이것 때문에 잘못된 OK였음. `is_unity_ai_relay`로 `~/.unity/relay/relay_*`를 건너뜀. 테스트 `test_unity_ai_assistant_relay_is_skipped`.
3. **doctor 테스트가 실제 저장소 상태를 읽음**: `test_doctor_reports_gaps_with_fixes`가 실제 claude-hand 폴더의 채팅 설정을 읽어, `chat-setup`을 한 PC에서는 항상 실패. `plan_chat_config`를 목으로 고정.
4. **감독 세션 안에서 `chat-status`·`doctor`·`update`를 실행하면 채팅창 연결이 끊김**: `tools/chat_channel.py`
   - 원인: 셋 다 `claude mcp get claude-hand-chat`를 부르는데, 이 명령은 상태를 보려고 채널 서버를 하나 더 띄움(`Status: √ Connected`). 감독 안에서 실행되면 그 복사본이 `CLAUDE_HAND_SUPERVISOR=1`을 물려받아(`.mcp.json`의 `${CLAUDE_HAND_SUPERVISOR:-0}`) 자기가 감독이라고 보고 `server.json`을 새 토큰으로 덮어씀. Windows에서는 `ThreadingHTTPServer`의 SO_REUSEADDR 때문에 같은 8765 포트에 겹쳐 열림. 확인이 끝나면 강제 종료돼 `server.json`이 죽은 pid를 가리킨 채 남고, 채팅창은 진짜 서버에 `HTTP Error 401: Unauthorized`.
   - 고침: 가장 가까운 Claude 조상이 `claude mcp get/list`면 대기 모드(`status.json`도 안 씀) / 기존 `server.json`의 서버가 응답하면 대기 모드로 두고 `server.json`·`status.json`을 안 건드림 / `PanelServer`: Windows에서 `allow_reuse_address` 끔.
   - 테스트 3개 추가. 실제 `claude mcp get`을 감독 환경으로 실행해 재현해도 `server.json` 그대로, 패널 토큰 통과. 실사용: 감독이 `chat-status`를 실행한 뒤에도 연결 유지.

## 남은 과제

중요한 것부터.

1. **[중요] ch_* 커스텀 도구가 stdio 방식에서 노출되지 않음 (MCP for Unity 10.3.0)**
   하위 세션과 같은 명령으로 서버를 띄워 확인:
   - Unity `get_tool_states`: 42개 중 ch_* 6개 모두 `enabled=True`, `group=core`, `is_built_in=False`
   - 서버 `tools/list`: 48개 중 ch_* 0개. `manage_tools(action="sync")` 뒤에도 0개(`tools/list_changed` 알림은 옴)
   - `execute_custom_tool`: `Unknown tool: 'execute_custom_tool'`
   - `--project-scoped-tools`(또는 `UNITY_MCP_PROJECT_SCOPED_TOOLS=true`): `execute_custom_tool`은 생기지만 `Tool 'ch_editor_status' not found for project 49ac508c`

   DESIGN.md 가정 3의 대책(sync → execute_custom_tool)이 이 버전에서는 둘 다 안 통함. 방향: HTTP 방식(WebSocket으로 도구 등록) 시험 / stdio에서 되던 MCP for Unity 버전 고정 / 하위 세션이 `execute_code`로 `ClaudeHand.Editor.ChXxx.HandleCommand(...)` 직접 호출.
2. **[중요] 감독이 Unity AI Assistant 도구로 직접 조작할 수 있음**
   사용자 설정의 `unity-mcp`(relay)가 감독 세션에도 붙어 있어, 한 감독 세션은 하위 세션 없이 `mcp__unity-mcp__Unity_GetProjectData`·`Unity_ManageGameObject`를 직접 불렀음. 하위 세션의 guard를 거치지 않음. 감독 스킬에 금지 문구를 넣거나, 감독 세션에서 그 서버를 끄는 방법(예: start-supervisor에서 `--strict-mcp-config`류) 검토.
3. **MCP for Unity 첫 설정 창이 HTTP로 등록함**
   패키지를 넣고 처음 열면 뜨는 설정 창에서 Configure가 `UnityMCP → http://127.0.0.1:8080/mcp`(hand_test local 범위)로 등록. 그 상태로는 `~/.unity-mcp` 상태 파일이 안 생겨 자동 sync가 창을 못 찾음. Window > MCP for Unity에서 Transport를 Stdio로 바꾸고 Claude Code의 Configure를 다시 눌러야 했음. unity-install·세팅 스킬 안내와 doctor 점검(등록 방식 불일치)에 넣을 것.
4. **데스크톱에서 시킨 일이 그 창의 채팅에 안 남음**
   지금은 SKILL.md 23줄 "`reply` 도구는 채팅창에서 온 메시지에만 쓴다"대로 동작. DESIGN.md "만들 것"의 **대화 기록 맞추기**(데스크톱 앱에서 시킨 일을 "데스크톱 앱에서" 표시와 함께 채팅창에도 남기기)로 이미 계획돼 있음. 사용자도 테스트 중 이 점을 짚었음.
5. **Remote Control 가짜 경고**
   데스크톱 앱이 자기 세션에 `ANTHROPIC_BASE_URL=https://api.anthropic.com`을 넣어서, 그 안에서 `remote-control`·`doctor`를 실행하면 경고가 뜸. 탐색기에서 켠 프로그램에는 ANTHROPIC 변수가 없음을 확인. 공식 주소는 경고에서 빼는 것 검토.
6. **CLI 로그인 꼬임 안내**
   첫 실행 뒤 `~/.claude/.credentials.json`이 `{}`로 비고 `oauth_org_not_allowed`가 났음(재로그인으로 해결). 첫 실행 안내에 "`/status`로 로그인 방식이 Claude 구독인지 확인, 아니면 `/login`"을 넣는 것 검토.
7. **Stdio로 바꾼 뒤에도 MCP for Unity HTTP 서버가 남아 있음**: Unity가 11:59에 띄운 `mcp-for-unity --transport http`(8080)가 그대로 돎.
8. **doctor의 git 고치기 안내**: `winget install --id Git.Git -e`인데 이 PC는 winget이 없었음(App Installer 1.0.30251.0). git-scm.com 안내를 함께 둘 것.
9. **테스트 경고**: `test_chat_channel.py:287`에서 `encoding` 없이 한글 출력을 읽어 `UnicodeDecodeError: 'cp949' codec can't decode byte 0xed`(테스트는 통과).
10. **Unity 6.3 경고**: `ChRefs.cs(109,22) warning CS0618: 'EditorUtility.InstanceIDToObject(int)' is obsolete`.

## 후속 (남은 과제 처리)

| 과제 | 처리 |
|---|---|
| 1. ch_* 도구 안 보임 | Claude Hand Tools가 자기 통로를 엶(`ChBridge.cs` + `tools/unity_bridge.py`, 하위 세션의 `<서버키>_ch` 서버). MCP for Unity 버전과 상관없음. DESIGN 결정 6. 확인: TEST-PLAN 1단계 3·4, 3단계 4 |
| 2. 감독이 직접 조작 | `chat-setup`·`preflight`가 사용자 설정의 MCP for Unity·Unity AI Assistant relay·MCP for Blender 도구를 claude-hand 폴더에서 막음(`permissions.deny`). 감독 스킬에도 금지 문구. DESIGN 결정 7 |
| 3. 첫 설정 창이 HTTP로 등록 | `list`·`sync`가 통로 등록 파일로도 Unity를 찾음. `doctor`가 두 경로 결과가 다르면 알림. 세팅 스킬·README가 Stdio를 권함 |
| 4. 데스크톱 작업이 채팅창에 안 남음 | `reply`의 `origin: "desktop"`, `request`. 채팅창에 "데스크톱 앱에서"로 표시. TEST-PLAN 13단계 E |
| 5. Remote Control 가짜 경고 | `https://api.anthropic.com`은 문제로 보지 않음 |
| 6. 로그인 꼬임 안내 | 첫 실행 안내와 세팅 스킬에 `/status` → `/login` |
| 7. HTTP 서버가 남음 | 세팅 스킬에 안내(그대로 둬도 지장 없음) |
| 8. git 안내 | winget이 없을 때 git-scm.com(uv·Node.js도 각 공식 페이지) |
| 9. 테스트 cp949 경고 | `encoding="utf-8"` |
| 10. Unity 6.3 경고 | `ChCompat`(DESIGN 결정 5) |

### 참고: 과제 1의 원인은 Python 3.14

같은 PC에서 이어서 원인을 찾았습니다. MCP for Unity 서버는 stdio에서도 Unity가 알려 준 커스텀 도구를 등록하려 하지만, Python 3.14에서 실패합니다.

- 서버 로그 `%LOCALAPPDATA%\UnityMCP\Logs\unity_mcp_server.log`: `Failed to register custom tool 'ch_editor_status' globally: 'echo'` (6개 모두, 각 도구의 첫 매개변수 이름). 바로 뒤 `Registered 6 custom tool(s) from Unity via stdio sync`는 실패와 상관없이 찍힘.
- `services/custom_tool_service.py`의 `_build_global_tool_handler`가 처리기에 `__signature__`·`__annotations__`를 단 뒤 `log_execution`·`telemetry_tool`(`functools.wraps`)로 감싼다. Python 3.14의 `functools.wraps`는 `__annotations__` 대신 `__annotate__`를 복사해서 감싼 함수에는 매개변수 타입이 없고, FastMCP가 만드는 pydantic 스키마가 `KeyError: 'echo'`를 낸다. 감싸지 않은 처리기는 등록된다(서버 환경의 Python으로 직접 재현).
- 이 PC에서 uvx가 고른 Python은 3.14.7이었음. Python 3.12로 띄워 비교하려 했지만 uv가 pywin32 설치 중 `os error 32`(파일 잠김)로 실패해 확인하지 못함.
- 그동안 쓸 수 있던 우회: `execute_code`로 `ClaudeHand.Editor.ChEditorStatus.HandleCommand(JObject)`를 직접 부르면 됨(`ch_editor_status`, `ch_scene_snapshot` 확인).

`ChBridge` 통로는 이 문제와 상관없이 동작하므로 그대로 두면 됩니다. MCP for Unity에는 "Python 3.14에서 커스텀 도구 전역 등록 실패(`functools.wraps`가 `__annotations__`를 복사하지 않음)"로 보고할 만합니다.

