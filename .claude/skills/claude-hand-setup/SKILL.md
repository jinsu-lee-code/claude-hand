---
name: claude-hand-setup
description: claude-hand를 처음 설치하고 세팅할 때 쓴다. 사용자가 "claude-hand 세팅해줘", "처음 설정해줘", "설치해줘", "agent-desktop 설치", "Blender MCP 설치"라고 하거나, doctor 결과에 빠진 항목이 있을 때 사용한다. agent-desktop(vercel-labs)과 MCP for Blender(ahujasid/blender-mcp)를 설치하고, Unity와 Blender 설정은 명령어로 하고, 명령어로 안 되는 화면 조작은 agent-desktop으로 한다. 모든 설치와 변경은 사용자가 허락한 뒤에만 한다.
---

# claude-hand 처음 세팅

이 저장소(claude-hand) 폴더에서 실행 중이라고 가정한다. 명령은 저장소 루트에서 실행한다. `uv`가 없으면 `uv run` 대신 `python`을 쓴다.

## 지킬 원칙

1. **허락받고 나서 바꾼다.** 진단 뒤에 무엇을 설치하고 바꿀지 계획을 보여주고, `AskUserQuestion`으로 한 번에 고르게 한다(여러 개 선택). 고른 단계만 한다. 고르지 않은 단계는 사용자가 직접 할 방법만 알려준다.
2. **명령어 → 파일 → MCP → 화면 조작** 순서로 시도한다. 화면 조작(agent-desktop)은 앞의 방법으로 안 될 때만 쓴다.
3. **사용자에게 넘기는 것**이 있다. 다음은 대신 누르거나 입력하지 않는다.
   - UAC(관리자 권한) 창, Windows 방화벽·보안 창, Smart App Control
   - 로그인, 라이선스 동의, 결제
   - 비밀번호, API 키
   이런 창이 뜨면 무엇을 눌러야 하는지 설명하고 기다린다.
4. **열려 있는 작업을 지킨다.** 저장 안 된 Blender나 Unity 창을 닫거나 다시 열어야 하면, 사용자에게 먼저 저장하고 닫아 달라고 부탁한다. `close-app`으로 대신 닫지 않는다.
5. **단계마다 확인한다.** 끝날 때마다 `doctor`나 해당 확인 명령으로 결과를 보고, 실패하면 오류 문구를 그대로 보여준다.

## 이미 설치했던 경우 (갱신)

`doctor`에 [갱신] 항목이 있거나, 사용자가 새 버전을 받았다고 하면 처음 세팅 대신 이 순서로 한다.
1. `uv run tools/claude_hand.py update --dry-run`으로 바뀔 내용을 보여주고, 허락받으면 `update`를 실행한다.
   - 하위 세션 정의와 감독 스킬을 새 형식으로 다시 쓴다. 지금 열린 창이 있으면 그 창도 넣는다.
   - 이전 버전이 local 범위로 등록한 claude-hand-chat을 지운다. 남아 있으면 새 `.mcp.json`보다 먼저 쓰여서 채팅이 안 된다.
   - 채팅을 쓰고 있었다면 `chat-setup`을 다시 실행한다. `.mcp.json`과 허용 설정을 새 형식으로 고치고, 채널 서버를 미리 실행해 점검한다. **"점검 통과"가 출력돼야 한다.** 안 나오면 출력된 오류를 그대로 보여준다.
   - 설치된 Blender 채팅 애드온이 옛 버전이면 새 파일로 바꾼다. Blender를 다시 열어야 적용된다.
2. 감독 세션을 `start-supervisor.cmd`로 다시 열라고 안내한다.
3. `doctor`로 [갱신] 항목이 사라졌는지 확인한다.

## 0. 진단과 계획

```
uv run tools/claude_hand.py doctor
```
Unity 프로젝트 경로를 알면 `--unity-project "<경로>"`를 붙인다. 모르면 사용자에게 물어본다. 여러 개면 여러 번 붙인다.

결과를 짧은 표로 보여준다. 그다음 빠진 항목에 맞춰 아래 1~8단계 중 필요한 것만 계획으로 제시하고 고르게 한다. 각 선택지 설명에 무엇이 바뀌는지 적는다.
- 1단계: winget으로 프로그램 설치
- 2단계: npm 전역 설치
- 3단계: Blender 애드온 폴더와 MCP 클라이언트 설정 파일
- 4단계: Unity 프로젝트의 Packages/manifest.json, Claude Code MCP 설정
- 5단계: Unity 에디터 환경설정
- 6단계: `~/.claude/agents`, `~/.claude/skills`
- 7단계: 이 폴더의 `.mcp.json`과 `.claude/settings.local.json`(채팅 채널 등록, 답장 도구 허용, 채널 서버 사전 승인), Blender 애드온 폴더(채팅 애드온), 선택하면 하위 세션 도구 허용 규칙
- 8단계: 바탕화면과 시작 메뉴의 "Claude Hand" 바로가기, 선택하면 Windows 시작프로그램

## 1. 기본 도구 (빠진 것만)

- Claude Code 명령(`claude`): `winget install Anthropic.ClaudeCode`. 데스크톱 앱만 쓰던 사람은 이 명령이 없을 수 있다. 감독 세션이 이 명령으로 열리기 때문에 꼭 필요하다.
- uv: `winget install --id astral-sh.uv -e`
- git: `winget install --id Git.Git -e` (Unity가 MCP for Unity를 받을 때 필요)
- Node.js 18 이상: `winget install --id OpenJS.NodeJS.LTS -e` (agent-desktop용)

설치 중 UAC 창이 뜨면 사용자에게 승인을 부탁한다. 설치한 직후에는 이 세션의 PATH에 반영되지 않아 명령을 못 찾을 수 있다. 그럴 때는 전체 경로로 실행하거나(예: `"C:\Program Files\nodejs\npm.cmd"`), Claude Code를 다시 시작하고 "세팅 이어서 해줘"라고 말해 달라고 안내한다.

## 2. agent-desktop (vercel-labs/agent-desktop)

```
npm install -g agent-desktop
agent-desktop status
agent-desktop skills get desktop
agent-desktop skills get platform
```
- `skills get` 두 개의 출력은 화면 조작 전에 반드시 읽고 따른다.
- Windows에서는 따로 권한을 줄 필요가 없다(`permissions`가 `not_required`).
- 깨끗한 Windows 11에서는 Smart App Control이 막을 수 있다. 끌지는 사용자가 정한다. agent-desktop 없이도 나머지 단계는 진행할 수 있다. 화면 조작이 필요한 부분만 사용자에게 직접 부탁하게 된다.

## 3. MCP for Blender (ahujasid/blender-mcp)

1. 먼저 무엇이 바뀌는지 본다.
   ```
   uvx mcp-for-blender setup --dry-run
   ```
   출력을 사용자에게 보여준다. 애드온 설치 위치와 설정될 MCP 클라이언트 목록이 나온다. `--yes`는 **발견된 모든 MCP 클라이언트**(Claude Desktop, Claude Code, VS Code 등)를 설정한다는 점을 알린다.
2. 허락받았으면 실행한다.
   ```
   uvx mcp-for-blender setup --yes
   ```
   이 명령이 애드온을 복사하고, Blender를 화면 없이(headless) 실행해서 애드온을 켠다.
3. 이미 열려 있는 Blender는 다시 열어야 애드온이 로드된다. 사용자에게 저장하고 닫아 달라고 부탁한다.
4. Blender 창을 여러 개 쓸 거면 창마다 포트를 다르게 해서 연다(9876, 9877…). 이 명령은 Blender를 열고 그 포트에서 애드온이 응답할 때까지 기다린다.
   ```
   uv run tools/claude_hand.py blender-open "<파일.blend>" --port 9877
   ```
   포트를 파일에 남기려면 .blend를 저장해야 한다고 알린다. 저장은 사용자에게 맡긴다.
5. 확인: `uv run tools/claude_hand.py list`에 Blender 창이 포트와 함께 보여야 한다.
6. 이미 열린 Blender의 포트를 바꿔야 하는데 그 창을 닫을 수 없다면, 아래 "화면 조작 규칙"에 따라 agent-desktop으로 바꾼다. 3D 뷰포트에서 `N` → MCP for Blender 탭으로 가서 포트를 입력하고 서버를 켠다.

## 4. Unity

1. 프로젝트마다 패키지를 추가한다. 먼저 `--dry-run`으로 무엇이 바뀌는지 보여준다.
   ```
   uv run tools/claude_hand.py unity-install "<프로젝트 경로>" --dry-run
   uv run tools/claude_hand.py unity-install "<프로젝트 경로>"
   ```
   - MCP for Unity(git URL)와 Claude Hand Tools(이 저장소 폴더)를 `Packages/manifest.json`에 넣는다. 원래 파일은 `manifest.json.claude-hand.bak`으로 남는다.
   - MCP for Unity가 이미 있으면(Asset Store판 포함) 건드리지 않는다.
2. Unity가 닫혀 있으면 연다.
   ```
   uv run tools/claude_hand.py unity-open "<프로젝트 경로>"
   ```
   Unity가 이미 열려 있으면 Unity 창이 앞에 올 때 패키지를 받아 온다. 사용자에게 Unity 창을 한 번 클릭해 달라고 하거나, 허락을 받고 `agent-desktop focus-window --app Unity`로 앞으로 가져온다. focus-window는 사용자의 현재 창 포커스를 가져간다는 점을 알린다.
3. 패키지를 받는 동안 대화상자(예: API 업데이트, Safe Mode)가 뜰 수 있다. 화면 조작 규칙대로 snapshot으로 내용을 읽고 사용자에게 보여준 뒤, 고른 버튼만 누른다. Safe Mode는 컴파일 오류가 있다는 뜻이니 들어가기 전에 사용자에게 묻는다.
4. Claude Code에 MCP for Unity가 등록돼 있지 않으면 등록한다. MCP for Unity의 기본 방식인 HTTP에 맞춘다.
   ```
   claude mcp add --scope user --transport http unityMCP http://localhost:8080/mcp
   ```
   새 MCP 서버는 Claude Code를 다시 시작해야 보인다. 사용자에게 다시 시작하고 "세팅 이어서 해줘"라고 말해 달라고 한다. 이어서 할 때는 `doctor`부터 다시 실행해 진행 상황을 파악한다.

## 5. Unity 에디터 설정 (MCP로)

Unity가 Connected이고 Claude Code가 MCP for Unity를 보이는 상태에서 진행한다.
1. `ch_apply_settings(action="show")`로 현재 값을 보여준다.
2. 허락받은 계획에 들어 있으면 `ch_apply_settings(action="apply")`를 부른다. Interaction Mode가 No Throttling이 된다.
   - Unity가 여러 개 열려 있으면 `unity_instance`를 지정한다.
   - 이 설정은 같은 Unity 버전의 모든 프로젝트에 적용된다.
   - 되돌리려면 `action="revert"` 또는 메뉴 Tools > Claude Hand > Revert Recommended Settings를 쓴다.
3. 도구가 안 보이면 `manage_tools(action="sync")`를 먼저 부른다. 그래도 MCP가 안 되면 사용자에게 메뉴 **Tools > Claude Hand > Apply Recommended Settings**를 눌러 달라고 하거나, 허락을 받고 화면 조작으로 누른다.

## 6. 하위 세션 만들기

```
uv run tools/claude_hand.py sync
```
사용자에게 `/agents`에서 `unity-*`, `blender-*`가 보이는지 봐 달라고 한다. `~/.claude/agents` 폴더가 이번에 처음 생겼으면 Claude Code를 한 번 다시 시작해야 한다.

## 7. Unity·Blender 채팅창 (사용자가 원할 때)

Unity와 Blender 안에서 바로 채팅하려면 아래를 한다. 계획을 보여줄 때 이 단계도 선택지로 넣는다.

**먼저 `doctor`의 [채팅] 항목을 본다.**
- Claude Code가 2.1.234보다 낮으면, 허락받고 `claude update`를 한다. 이 버전부터 채팅창에 허용/거부 버튼이 뜬다.
- Bedrock, Vertex, Foundry를 쓰는 환경이면 Channels를 쓸 수 없다. 이 단계를 건너뛰고 이유를 알린다. Channels는 claude.ai 로그인이나 Console API 키가 필요하다.
- 회사 계정(Team/Enterprise)인지 물어본다. 그렇다면 관리자가 Channels(`channelsEnabled`)를 켜야 한다. 꺼져 있으면 개발용 채널 플래그도 막힌다.

1. **채널 등록과 미리 실행**
   ```
   uv run tools/claude_hand.py chat-setup --dry-run
   uv run tools/claude_hand.py chat-setup
   ```
   - 이 폴더에 두 파일을 만들거나 고친다. 기존 내용은 유지하고, 둘 다 git에 올라가지 않는다.
     - `.mcp.json`: 채널 등록
     - `.claude/settings.local.json`: 답장 도구 `mcp__claude-hand-chat__reply` 자동 허용. 채팅창에 답할 때마다 허락을 묻지 않게 한다.
   - 채널 서버를 한 번 미리 실행한다(`--check`). Claude Code는 MCP 서버가 30초 안에 떠야 하는데, 처음에는 uv가 Python을 내려받느라 몇 분 걸릴 수 있어서 미리 받아 두는 것이다. 포트와 저장 폴더도 함께 점검한다. 실패하면 출력된 오류를 그대로 보여준다.
   - Windows 방화벽 창이 뜨면 사용자에게 맡긴다. 채널은 내 PC 안(127.0.0.1)에서만 통신하므로 허용하지 않아도 동작한다.
2. **Blender 채팅 애드온:** 애드온 폴더에 복사하고, Blender를 화면 없이 실행해서 켠다. 열려 있는 Blender는 다시 열어야 탭이 보인다.
   ```
   uv run tools/claude_hand.py blender-install-chat --dry-run
   uv run tools/claude_hand.py blender-install-chat
   ```
3. **Unity 채팅창**은 Claude Hand Tools 패키지에 들어 있어서 따로 설치할 것이 없다. 메뉴는 **Window > Claude Hand > Chat**이다.
4. **(선택) 하위 세션 도구 자동 허용:** 채팅창으로 일을 시키면 하위 세션의 Unity·Blender 조작마다 채팅창에 허용 버튼이 뜬다. 사용자에게 장단점을 설명하고 고르게 한다.
   - 장점: 매번 누를 필요가 없다.
   - 단점: 그 하위 세션의 Unity·Blender 도구는 묻지 않고 실행된다.
   원하면 다음을 실행한다. 이 폴더의 `settings.local.json`에 각 하위 세션 서버의 도구(`mcp__unity_…__*`, `mcp__blender_…__*`)만 허용 규칙으로 넣는다. 다른 도구(Bash 등)는 계속 묻는다.
   ```
   uv run tools/claude_hand.py sync --allow-tools
   ```
5. **사용자에게 알린다.** 길게 설명하지 말고 이것만 말한다.
   - 앞으로는 Unity나 Blender 채팅창의 **[Claude Hand 시작]** 버튼, 또는 바탕화면의 **Claude Hand** 아이콘만 누르면 된다. 8단계에서 만든다.
   - 새로 뜨는 검은 창이 Claude Hand다. **닫지 말고 최소화**해 둔다.
   - 처음 한 번은 그 창에서 확인 질문 몇 개에 답한다. 폴더 신뢰, 개발용 채널 경고, 채널 서버 사용 같은 것이다. 무엇을 고르면 되는지 그 창에 안내가 나온다.
   - 새 Unity 프로젝트나 Blender 파일을 열어도 따로 할 일이 없다. 그 창에서 처음 메시지를 보내면 담당 하위 세션이 자동으로 만들어진다.
   - 참고(사용자가 궁금해하면): Channels는 리서치 프리뷰다. 감독 세션은 한 번에 하나만 열린다(두 번 누르면 "이미 실행 중"이라고 나온다). 지금 이 세팅용 세션은 채팅을 받지 않는다.
   - `start-supervisor`로 열었는데도 채팅이 들어오지 않을 수 있다. 예를 들어 `/mcp`에 claude-hand-chat이 없을 때다. 그러면 대체 방식으로 다시 등록한다.
     ```
     uv run tools/claude_hand.py chat-setup --register-local
     ```

## 8. 바로가기 만들기

Claude Hand를 켜는 아이콘을 만든다. 컴퓨터에 익숙하지 않은 사람에게 가장 중요한 단계다.
```
uv run tools/claude_hand.py shortcut --dry-run
uv run tools/claude_hand.py shortcut
```
- 바탕화면과 시작 메뉴에 "Claude Hand"가 생긴다. 지울 때는 `shortcut --remove`.
- 컴퓨터를 켤 때마다 자동으로 켜고 싶은지 물어본다. 원하면 `shortcut --startup`을 쓴다. 단점도 말한다. 컴퓨터를 켤 때마다 검은 창이 하나 떠서 계속 남아 있다.
- 바로가기는 `start-supervisor.cmd`를 실행한다. 이 파일은 실행할 때마다 다음을 알아서 한다(`preflight`).
  - 열린 창의 하위 세션 준비
  - 채팅 설정 맞추기
  - 이미 켜져 있는지 확인
  - 처음 한 번 안내

## 9. 마무리

`doctor`를 다시 실행해 남은 항목을 표로 보고한다. 끝으로 다음을 안내한다.
- `docs/TEST-PLAN.md`의 확인 순서
- 평소에는 감독 스킬(`claude-hand`)이 쓰인다는 점
- 채팅을 설치했다면 `start-supervisor.cmd`로 감독 세션을 다시 열어야 한다는 점

## 채팅창에 감독 세션이 없다고 나올 때

먼저 채팅창의 **[Claude Hand 시작]** 버튼을 눌러 보게 한다. 대부분은 감독 세션이 꺼져 있는 경우다. 그래도 안 되면 `uv run tools/claude_hand.py chat-status`를 실행하고, 출력에 적힌 대로 고친다. 흔한 원인은 이렇다.
1. **감독 세션을 `start-supervisor.cmd`가 아닌 방법으로 열었다.** 데스크톱 앱이나 그냥 실행한 `claude`가 이 경우다. 채널 서버가 대기 모드로 뜨고, 채팅창에 그 이유가 보인다.
2. **이전 버전의 local 범위 등록이 남아 있다** → `update`
3. **`.mcp.json` 서버가 승인 대기 중이거나 거절됐다** → 감독 세션에서 `/mcp`로 확인하고 승인한다. 거절했었다면 `claude mcp reset-project-choices` 후 다시 연다.
4. **채널 서버가 시작에 실패했다**(`/mcp`에서 failed) → `chat-setup`을 다시 실행해 점검 결과를 보고, `uv`가 PATH에 있는지 확인한다.

## 화면 조작 규칙 (agent-desktop)

- 처음 쓰기 전에 `agent-desktop skills get desktop`과 `skills get platform`을 읽고 그대로 따른다.
- **기본은 백그라운드(headless) 모드다.** 마우스를 움직이지 않고 포커스를 뺏지 않는다.
  1. `snapshot --app <exe> -i`
  2. ref 고르기
  3. `click <ref>`
  4. 다시 `snapshot`으로 결과 확인
- ref는 반드시 그 스냅샷의 `@sXXXX:eN`을 쓴다. 재시도는 `error.disposition.retry`가 `safe`일 때만 한다.
- **최소화된 창은 조작할 수 없다.** `restore --app <exe>`로 먼저 복원하고 다시 snapshot한다.
- **Unity·Blender 내부 화면**(패널, 인스펙터, N 패널)은 접근성 정보가 거의 없어 snapshot에 안 보일 수 있다. 그럴 때만 아래 순서로 한다.
  1. 사용자에게 "지금부터 약 N초 동안 마우스와 키보드를 만지지 마세요"라고 알리고 허락받는다.
  2. `screenshot`으로 화면을 확인한다. 정확한 옵션은 `agent-desktop screenshot --help`를 본다.
  3. `--headed mouse-click --xy <x>,<y>`로 누른다(형식은 `--help`로 확인). 입력은 `--headed type`으로 한다.
  4. 다시 `screenshot`으로 결과를 확인한다.
- 같은 조작을 두 번 시도해도 안 되면 멈추고, 사용자에게 직접 하는 방법을 안내한다.
- 스크린샷과 snapshot에는 화면의 개인 정보가 담긴다. 작업 폴더 밖(임시 폴더)에 저장하고, 끝나면 지우고, 대화에는 필요한 내용만 옮긴다.
