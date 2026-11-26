# claude-hand

[English](README.md) · **한국어**

Unity 에디터와 Blender 창을 여러 개 동시에 띄워 두고, **창마다 전담 Claude 하위 세션**을 붙여서 일을 나눠 맡기는 도구 모음입니다.
한 곳의 오브젝트를 다른 곳으로 옮길 수도 있습니다: Unity → Unity, Blender → Blender, Blender → Unity, Unity → Blender.
기존 MCP를 대체하지 않고, 그 위에 덧붙입니다.
- Unity: [MCP for Unity (CoplayDev)](https://github.com/CoplayDev/unity-mcp)
- Blender: [MCP for Blender (ahujasid/blender-mcp)](https://github.com/ahujasid/blender-mcp)
- 처음 세팅과 대화상자 처리: [agent-desktop (vercel-labs)](https://github.com/vercel-labs/agent-desktop)

```
Unity·Blender 안의 채팅창 ──(claude-hand-chat 채널)──┐
                                                    ▼
감독 세션 (Claude Code, 사용자와 대화)
 ├─ 하위 세션 unity-mygame      ── 전용 MCP 서버 (MyGame에 고정)  ── Unity 창: MyGame
 ├─ 하위 세션 unity-props       ── 전용 MCP 서버 (Props에 고정)   ── Unity 창: Props
 ├─ 하위 세션 blender-character ── 전용 MCP 서버 (포트 9876 고정) ── Blender 창: character.blend
 └─ 교환 폴더 %USERPROFILE%\.claude-hand\exchange  (.unitypackage / .blend / .fbx + manifest, 30일 지나면 켤 때 정리)
```

> **1.1.0** (2026-11-25) · 1.0.0까지는 Windows의 실제 Unity 6.3과 Blender 5.2에서, 열린 창과 실제 감독 세션으로 확인했습니다. 1.1.0에서 더한 설정창 · 화면 영어 · 실험실은 자동 테스트, 창 없는 Blender · Unity 안 테스트, 열린 창(따로 꾸민 시험 환경, 감독 자리는 흉내)으로 확인했고, 2026-11-26에 실제 감독 세션으로도 확인했습니다. macOS·Linux에서는 자동 테스트만 돌렸습니다.
> 무엇을 어떻게 확인했는지는 [검증 순서](docs/TEST-PLAN.md)와 [점검 기록](docs/REVIEW-2026-11-10.md)에, 버전마다 바뀐 것은 [버전별 변경 내용](#버전별-변경-내용)에 있습니다.

## 할 수 있는 것

- **창마다 전담 Claude.** 열려 있는 Unity 프로젝트와 Blender 파일마다 하위 세션이 하나씩 붙습니다. 자기 창 말고는 건드리지 못합니다.
- **창 안에서 채팅.** Unity와 Blender 안의 채팅창에서 바로 시키고 답을 받습니다. 지금 선택한 것이 함께 전해져서 "이거 복제해줘"라고 해도 됩니다. → [창마다 채팅하기](#창마다-채팅하기)
- **창 사이 옮기기.** Unity ↔ Unity, Blender ↔ Blender, Blender ↔ Unity. Blender의 모델은 재질 · 회전 · 움직임까지 갑니다. → [창 사이 옮기기](#창-사이-옮기기)
- **저장하면 따라오기.** Blender에서 저장할 때마다 Unity의 모델이 제자리에서 바뀝니다. Claude를 거치지 않아 토큰이 들지 않습니다. → [저장하면 Unity의 모델이 따라오게 하기](#저장하면-unity의-모델이-따라오게-하기)
- **요청 단위 되돌리기.** 버튼 하나로 그 요청이 바꾼 것을 되돌립니다. 파일까지, 그 요청이 바꾼 다른 창까지. → [되돌리기와 중지](#되돌리기와-중지)
- **중지 · 현황 · 기록.** 일하는 중에 멈추고, 다른 창이 무엇을 하는지 보고, 한 요청에 무슨 일이 있었는지와 든 시간 · 토큰을 봅니다(`status`, `trace`, `usage`).
- **보호 규칙.** "이건 건드리지 마"를 창마다 정해 두면 Claude가 지키기를 기대하지 않고 기계가 막거나 되돌립니다. → [건드리면 안 되는 것 정해 두기](#건드리면-안-되는-것-정해-두기-보호-규칙)
- **데스크톱 앱 · 모바일.** 같은 세션을 Claude 데스크톱 앱과 모바일 앱에서 보고 말합니다. → [Claude 데스크톱 앱에서 보고 말하기](#claude-데스크톱-앱에서-보고-말하기)
- **설정창 · 화면 영어 · 실험실.** 채팅창의 톱니바퀴 버튼으로 기능을 켜고 끄고, 화면 언어(한국어 · English)를 고르고, 아직 다듬는 중인 새 기능을 골라 켭니다. → [설정창](#설정창)

## 목차

- [필요한 것](#필요한-것) · [Team과 Enterprise 계정에서 쓸 때](#team과-enterprise-계정에서-쓸-때)
- [설치](#설치)
- [매일 쓰는 법](#매일-쓰는-법)
- [창마다 채팅하기](#창마다-채팅하기)
- [설정창](#설정창)
- [창 사이 옮기기](#창-사이-옮기기)
- [Claude 데스크톱 앱에서 보고 말하기](#claude-데스크톱-앱에서-보고-말하기)
- [새 버전 받기](#새-버전-받기)
- [들어 있는 것](#들어-있는-것)
- [안전장치](#안전장치)
- [알려진 한계](#알려진-한계)
- [버전별 변경 내용](#버전별-변경-내용)
- [개발](#개발)
- [라이선스](#라이선스)

## 필요한 것

- **Claude Code**와 **claude.ai 로그인**(Pro · Max · Team · Enterprise).
  - 채팅창은 Claude Code의 Channels 기능을 씁니다. claude.ai 로그인이나 Console API 키로 되고, Bedrock · Vertex · Foundry에서는 쓸 수 없습니다.
  - 채팅창의 허용/거부 버튼은 Claude Code 2.1.234 이상에서 동작합니다.
  - 데스크톱 앱에서 보기(Remote Control)는 claude.ai 로그인이어야 합니다. API 키로는 안 됩니다.
- **Windows.** macOS · Linux에서는 자동 테스트만 돌렸습니다.
- **Unity**와 [MCP for Unity](https://github.com/CoplayDev/unity-mcp)(시험한 버전 v10.3.0), **Blender**와 [MCP for Blender](https://github.com/ahujasid/blender-mcp). Unity 6.3과 Blender 5.2에서 확인했습니다. 둘 중 하나만 써도 됩니다.
- uv, git, Node.js 18 이상, agent-desktop. 세팅 스킬이 빠진 것을 설치해 줍니다.

`uv run tools/claude_hand.py doctor`가 이 가운데 무엇이 갖춰졌는지 알려 줍니다.

### Team과 Enterprise 계정에서 쓸 때

개인 계정(Pro · Max)과 달리 **조직 설정이 먼저 켜져 있어야** 합니다. 아래는 Claude Code 문서([Channels](https://code.claude.com/docs/en/channels), [Remote Control](https://code.claude.com/docs/en/remote-control), [설정](https://code.claude.com/docs/en/settings))에 적힌 내용이고, 이 저장소는 개인 계정에서만 돌려 봤습니다. 세팅하기 전에 조직 소유자에게 아래 셋을 확인하세요.

- **Channels가 켜져 있어야 채팅창이 됩니다.** Team · Enterprise는 기본으로 막혀 있습니다. 소유자(Owner)가 claude.ai의 조직 설정 > Claude Code > Channels에서 켜거나, 관리 설정에 `channelsEnabled: true`를 넣어야 합니다. 꺼져 있으면 채널 서버는 붙고 답장 도구도 보이지만 **채팅창에서 보낸 메시지가 감독에게 도착하지 않습니다.** 시작할 때 관리자에게 켜 달라는 경고가 뜹니다. claude-hand가 쓰는 개발용 채널 플래그도 이 스위치가 켜져 있어야 동작합니다.
- **Remote Control도 따로 켜야 합니다.** 기본으로 꺼져 있고, 소유자가 같은 페이지에서 켭니다. 꺼져 있어도 채팅창은 되고, 데스크톱 앱 · 모바일에서 보는 것만 안 됩니다. Zero Data Retention이나 HIPAA 조직은 켤 수 없습니다.
- **관리 설정(managed settings)이 claude-hand가 쓰는 것을 막을 수 있습니다.** 조직이 배포한 관리 설정은 사용자 · 프로젝트 설정보다 우선합니다.

  | 조직이 걸어 둔 것 | 막히는 것 |
  |---|---|
  | MCP 서버 제한 (`managed-mcp.json`, `allowManagedMcpServersOnly`, `strictPluginOnlyCustomization`의 `mcp`) | 이 폴더의 `.mcp.json`(채팅 채널)과 하위 세션의 전용 MCP 서버 |
  | 훅 제한 (`allowManagedHooksOnly`, `strictPluginOnlyCustomization`의 `hooks`) | 하위 세션의 안전장치와 감독의 훅. 안전장치가 돌지 않으면 다른 창 막기, 되돌리기 지점, 보호 규칙이 모두 동작하지 않습니다 |
  | 하위 세션 · 스킬 제한 (`strictPluginOnlyCustomization`의 `agents`, `skills`) | `~/.claude/agents`의 창 담당과 감독 스킬 |
  | 허용 규칙 제한 (`allowManagedPermissionRulesOnly`) | `chat-setup`이 넣는 답장 도구 자동 허용 |

  하위 세션 정의에 적힌 훅이 훅 제한에서 어떻게 되는지는 문서에 나와 있지 않습니다. 이런 제한이 있는 조직이라면 먼저 한 창으로 시험해 보세요: 채팅창에 요청을 보낸 뒤 `uv run tools/claude_hand.py trace`에 `동작` 줄과 `(이 요청의 되돌리기 지점)`이 남으면 훅이 돌고 있는 것입니다.

그 밖에:

- **로그인.** 관리자가 초대한 claude.ai 계정으로 로그인합니다. 조직이 로그인을 고정해 두면(`forceLoginOrgUUID`) API 키는 시작할 때 막힙니다.
- **사용량.** 자리(seat)마다 주어진 5시간 · 주간 한도에서 나가고, Claude 채팅과 함께 씁니다. 감독과 창마다의 하위 세션이 같은 한도를 쓰므로 창이 많을수록 빨리 찹니다([요청마다 든 시간과 토큰](#요청마다-든-시간과-토큰)으로 볼 수 있습니다). 관리자는 사용자별 사용량을 봅니다.

## 설치

### Claude에게 맡기기

필요한 건 **Claude Code**와 이 저장소뿐입니다.

1. 저장소의 `main` 브랜치를 받습니다. `git`이 없으면 GitHub 페이지에서 `main` 브랜치를 고른 뒤 ZIP으로 받아 풀어도 됩니다.
   ```powershell
   git clone -b main https://github.com/jinsu-lee-code/claude-hand.git
   cd claude-hand
   ```
2. 그 폴더에서 Claude Code를 엽니다(`claude`). 데스크톱 앱이면 이 폴더를 엽니다.
3. **"claude-hand 세팅해줘"**라고 말합니다.

그러면 `claude-hand-setup` 스킬이 다음 순서로 진행합니다.

1. **진단:** 무엇이 설치돼 있고 무엇이 빠졌는지 표로 보여 줍니다(`doctor`).
2. **계획과 허락:** 설치하거나 바꿀 것을 보여 주고, 할 단계를 고르게 합니다. 고른 단계만 진행합니다.
3. **설치와 세팅:**
   - 빠진 기본 도구(uv, git, Node.js)를 winget으로 설치
   - agent-desktop 설치
   - MCP for Blender 설치(애드온 설치와 활성화, Claude 설정까지)
   - Unity 프로젝트에 패키지 추가, Unity 절전 설정
   - 하위 세션 생성, 원하면 Unity · Blender 채팅창 연결
   - 명령어로 안 되는 화면 조작만 agent-desktop이 직접 합니다.
4. **확인:** 마지막에 다시 진단해서 남은 항목을 알려 줍니다.

Claude가 대신 하지 않고 **사용자에게 직접 부탁하는 것**도 있습니다.

- 관리자 권한(UAC) 창, Windows 방화벽 · 보안 창
- 로그인, 라이선스 동의, 비밀번호와 API 키 입력
- 저장 안 된 Blender · Unity 창 닫기
- Claude Code 재시작(새 MCP 서버를 등록한 뒤 한 번 필요합니다)

### 직접 설치하기

1. 기본 도구: uv, git, Node.js 18 이상(`winget install --id astral-sh.uv -e` / `Git.Git` / `OpenJS.NodeJS.LTS`)
2. agent-desktop: `npm install -g agent-desktop`
3. MCP for Blender: `uvx mcp-for-blender setup --dry-run`으로 확인한 뒤 `uvx mcp-for-blender setup --yes`
4. Unity 프로젝트마다 다음을 실행합니다. MCP for Unity와 Claude Hand Tools가 `Packages/manifest.json`에 들어가고, 원래 파일은 `.bak`으로 남습니다.
   ```powershell
   uv run tools/claude_hand.py unity-install "C:\경로\프로젝트"
   ```
   MCP for Unity는 시험한 버전(v10.3.0)으로 들어갑니다. 이미 `#main`으로 넣어 둔 프로젝트는 그대로 두고 알려 주며, 옮기려면 `--pin-mcp-for-unity`를 붙입니다.
5. MCP for Unity를 **Stdio** 방식으로 씁니다. 패키지를 넣고 처음 뜨는 MCP for Unity 설정 창은 HTTP로 등록하니, Unity의 **Window > MCP for Unity**에서 Transport를 **Stdio**로 바꾸고 Claude Code의 **Configure**를 누릅니다. 명령으로 등록해도 됩니다(아직 없을 때만). 그다음 Claude Code를 다시 시작합니다.
   ```powershell
   claude mcp add --scope user unityMCP -- uvx --from mcpforunityserver mcp-for-unity --transport stdio
   ```
6. Unity에서 메뉴 **Tools > Claude Hand > Apply Recommended Settings**를 누릅니다(Interaction Mode = No Throttling).
7. Blender 창을 여러 개 쓸 거면 창마다 포트를 다르게 해서 엽니다.
   ```powershell
   uv run tools/claude_hand.py blender-open "C:\Art\props.blend" --port 9877
   ```
   또는 3D 뷰포트에서 `N` → MCP for Blender 탭에서 포트를 바꾸고 서버를 켭니다. 포트는 .blend에 저장됩니다.
8. 작업할 창을 모두 연 상태에서 하위 세션을 만듭니다.
   ```powershell
   uv run tools/claude_hand.py sync
   ```
   Claude Code에서 `/agents`를 입력해 `unity-*`, `blender-*`가 보이면 끝입니다.
9. 채팅창을 쓰려면 채널을 등록하고 Blender 채팅 애드온을 넣습니다.
   ```powershell
   uv run tools/claude_hand.py chat-setup            # 채널 등록 + 답장 도구 허용 + 채널 서버 미리 실행·점검
   uv run tools/claude_hand.py blender-install-chat  # Blender 채팅 애드온 설치·활성화
   uv run tools/claude_hand.py sync --allow-tools    # (선택) 하위 세션의 Unity·Blender 도구를 묻지 않고 실행
   uv run tools/claude_hand.py shortcut              # 바탕화면과 시작 메뉴에 Claude Hand 아이콘 (--startup: 컴퓨터를 켤 때 자동으로)
   ```

`sync`에 대해 알아둘 것:

- 지금 쓰는 MCP 설정(`.mcp.json`, `~/.claude.json`, Claude Desktop 설정)을 찾아서 같은 방식으로 서버를 띄웁니다. Unity는 stdio와 HTTP 둘 다 됩니다.
- Blender는 포트 9876~9885를 확인하고, 응답한 창의 .blend 경로를 읽어(읽기만 합니다) 이름을 붙입니다. 다른 범위를 쓰려면 `--blender-ports 9900-9910`을 붙입니다.
- 창을 새로 열거나 닫았으면 다시 `sync`하세요. 닫은 창의 정의 파일까지 지우려면 `--prune`을 붙입니다. Claude Hand가 켜져 있으면 새 창은 처음 메시지를 보낼 때 저절로 만들어집니다.
- 저장소 폴더를 옮겼으면 다시 `sync`해야 합니다. 안전장치 경로가 정의 파일에 들어가 있어서요.

### 설치를 확인하기

```powershell
uv run tools/claude_hand.py doctor      # 빠진 항목과 고치는 명령
uv run tools/claude_hand.py selftest    # 실제로 동작하는지 (1분쯤, 아무것도 바꾸지 않음)
```

- `selftest`는 테스트(Python, Blender 안 애드온)를 돌리고, 열린 Unity · Blender와 채팅 채널이 이 버전으로 응답하는지 확인합니다. 테스트를 빼려면 `--no-tests`.
- 모델을 옮기는 Unity 도구까지 실제로 돌려 보려면 **닫혀 있는** 프로젝트를 알려 줍니다(3~4분).
  ```powershell
  uv run tools/claude_hand.py selftest --unity-project "C:\경로\프로젝트"
  ```
  Unity를 창 없이 열어 Blender가 실제로 내보낸 모델을 받고, 다시 온 모델을 제자리에서 바꾸고, 되돌리기 · 저장 따라가기 · 보호 규칙, 그리고 설정과 화면 언어까지 확인합니다(79개 항목). 저장하지 않는 새 씬에서 하고, 만든 에셋은 끝나면 지웁니다. 바꾸기 전 파일 사본과 되돌리기를 확인하며 뺀 시험용 에셋이 `Library/ClaudeHand/replaced/`와 `removed/`에 남습니다(둘 다 최근 10개만).

## 매일 쓰는 법

1. Unity(**Window > Claude Hand > Chat**)나 Blender(3D 뷰포트 `N` > **Claude Hand**)의 채팅창에 바로 말합니다.
   - Claude Hand가 꺼져 있으면 채팅창이 켜고, 연결되면 그 메시지를 보냅니다.
   - 바탕화면의 **Claude Hand** 아이콘이나 채팅창의 **[Claude Hand 시작]** 버튼으로 먼저 켜도 됩니다.
2. 검은 Claude Hand 창이 뜹니다. **켤 때마다** 개발용 채널 경고가 나오니 "I am using this for local development"를 고르고 Enter를 누릅니다. 직접 만든 채널에 Claude Code가 늘 묻는 보안 확인이라 claude-hand가 대신 답하지 않고, 개인 계정에서는 끌 수도 없습니다.
3. 세션이 일을 시작하면 그 창은 저절로 최소화됩니다(Windows). **닫지는 마세요.** 닫으면 채팅이 멈춥니다.

이게 전부입니다.

- 새 프로젝트나 파일을 열어도 따로 할 일이 없습니다. 처음 메시지를 보내면 그 창 담당이 자동으로 만들어집니다.
- 아이콘을 두 번 눌러도 "이미 실행 중"이라고 알려 줄 뿐 둘이 열리지 않습니다.
- 켤 때마다 같은 대화를 이어 갑니다. 새 대화로 시작하려면 `uv run tools/claude_hand.py new-session`.

## 창마다 채팅하기

Unity와 Blender 안에서 바로 채팅할 수 있습니다. **그 창에서 친 메시지는 그 창 담당 하위 세션에게 가고, 답도 그 창으로 옵니다.**

- Unity: **Window > Claude Hand > Chat**
- Blender: 3D 뷰포트 `N` 패널 > **Claude Hand** 탭

```
Unity 채팅창 ─┐            ┌─ unity-mygame (이 창의 대화를 계속 이어감)
Blender 패널 ─┼─ 채널 ─ 감독 ┼─ blender-props
             │  (토큰)     └─ 다른 창이 필요하면 감독이 함께 조율
             └── 답장·권한 요청(허용/거부)이 같은 창으로 돌아옴
```

### 기본

- **선택한 것이 함께 갑니다.** 메시지에 지금 선택한 오브젝트와 씬 정보가 붙어서 "이거 복제해줘"처럼 말해도 됩니다. 끄는 체크박스가 있습니다.
- **다른 창이 필요한 일도 그 창에서 말합니다.** Unity 창에서 "Blender props.blend의 Rock 가져와"라고 하면 감독이 두 담당을 함께 써서 처리하고, 결과를 그 Unity 창에 알려 줍니다.
- **바뀐 다른 창의 모습이 그림으로 붙습니다.** Blender 창에서 "Unity로 보내줘"라고 했다면 답 아래에 그 Unity 창의 지금 모습이 붙습니다. 채널이 직접 찍어서 토큰이 들지 않고, 들여다보기만 한 창의 그림은 붙지 않습니다.
- **허락이 필요하면 버튼이 뜹니다.** Claude가 도구를 실행하려고 허락을 구하면 채팅창에 **허용/거부**가 뜹니다.
- **답이 오면 알려 줍니다.** 다른 프로그램을 보는 동안 답이나 허락 요청이 오면 작업 표시줄 단추가 깜빡이고, Unity 채팅 탭 이름에 안 읽은 수가 붙습니다.
- **답이 안 오면 다시 알립니다.** 일이 끝났는데 답이 없으면 채널이 1분 뒤 감독에게 한 번 다시 알리고, 채팅창에 그렇게 했다고 보여 줍니다.
- **대화상자에서 멈추면 바로 압니다(Windows).** Unity가 "씬을 저장할까요?" 같은 대화상자에서 멈추면 시간 초과를 기다리지 않고 그 글과 버튼을 감독과 채팅창에 알립니다. 무엇을 누를지는 사용자가 정합니다.

### 되돌리기와 중지

일이 끝나면 입력칸 위에 **되돌리기** 버튼이 뜹니다. 그 요청이 시작되기 직전으로 돌아갑니다.

- Unity Undo나 Blender 실행 취소 기록으로 되돌립니다.
- Unity에서는 그 요청이 일하는 동안 바뀐 **파일도** 함께 돌립니다: 고친 스크립트와 에셋은 이전 것으로, 만든 것은 프로젝트에서 빠지고, 지운 것은 되살아납니다.
- 그 요청이 **다른 창도 바꿨으면**(Blender에서 시킨 "Unity로 보내줘") 요청한 채팅창의 버튼 하나로 그 창들까지 되돌립니다. 버튼에 되돌릴 요청의 글과 다른 창의 이름이 적혀 있습니다.
- 물어보기만 한 요청은 되돌릴 것이 없으니 건너뛰고, 그 앞의 바꾼 요청을 가리킵니다.
- "방금 거 취소해줘"라고 말해도 됩니다.

일하는 동안에는 "작업 중" 줄 옆에 **중지** 버튼이 있습니다. 누르면 그 요청으로 일하던 하위 세션의 다음 동작부터 막히고(하던 동작 하나는 끝까지 갑니다), 감독이 어디까지 했는지 알려 줍니다. 한 것은 되돌리지 않습니다. 되돌리려면 그다음에 **되돌리기**를 누릅니다.

### 다른 창이 무엇을 하는지 보기

입력칸 위의 **다른 창** 줄이 채팅창이 열려 있는 다른 창들을 한 줄씩 보여 줍니다: `작업 중`(다른 창의 요청으로 일하면 "○○의 요청"), `답 기다림`, `대화상자로 멈춤`. 한가한 창은 한 줄에 모읍니다. 채널이 이미 아는 것을 보여 주는 것이라 토큰이 들지 않습니다.

명령으로도 봅니다. 경과 시간과 지금 하는 동작이 함께 나옵니다.

```powershell
uv run tools/claude_hand.py status
```
```
Level (Unity) · 작업 중 · 1분 15초 · props의 요청 · ch_import_model · “의자를 Level로 보내줘”
props (Blender) · 답 기다림 · 1분 20초 · “의자를 Level로 보내줘”
한가함: MyGame (Unity)
```

### 한 요청에 무슨 일이 있었는지 보기

```powershell
uv run tools/claude_hand.py trace                   # 최근 이틀의 채팅창 요청 목록
uv run tools/claude_hand.py trace unity-mygame      # 그 창의 마지막 요청을 한 줄씩
uv run tools/claude_hand.py trace --request <번호>  # 목록에 나온 번호의 요청
```

채팅창에서 보낸 요청마다 번호가 붙고, 그 뒤의 일이 `%USERPROFILE%\.claude-hand\logs\<날짜>.jsonl`에 한 줄씩 남습니다. "왜 답이 늦었지", "어디서 막혔지"를 뒤에서 볼 때 씁니다.

- 남는 것: 감독이 어느 창에 일을 맡겼는지(`맡김`), 하위 세션의 도구 호출, 안전장치와 보호 규칙이 막은 호출과 이유, `ch_*` 호출의 성공 여부와 걸린 시간, 중지를 누른 때, 하위 세션이 멈춘 때, 답, 그리고 마지막 줄에 든 시간 · 토큰.
- 다른 창이 한 일도 그 일을 시킨 채팅창의 요청 번호 아래에 남습니다.
- 메시지와 답은 앞 200자만 남고, 2주가 지난 파일은 켤 때 지워집니다.
- 데스크톱 앱에서 시킨 일은 요청 번호 없이 남아 목록에는 나오지 않습니다.

### 요청마다 든 시간과 토큰

결과 답 아래에 한 줄이 붙습니다: `이 요청: 32초 · 동작 2번 · 토큰 출력 2.9천 · 새 입력 4.5천 · 다시 읽기 66만`. 감독과 그 요청으로 일한 창들의 것을 더한 수입니다. 답이 오고 몇 초 뒤, 감독이 그 차례를 마치면 붙습니다. Claude Code가 남기는 대화 기록에서 읽어 오는 것이라 이것 때문에 토큰이 더 들지는 않습니다.

```powershell
uv run tools/claude_hand.py usage             # 최근 이틀의 요청마다 든 시간·동작·토큰과 합계
uv run tools/claude_hand.py usage --days 7    # 일주일 치 (날짜별 합계가 함께 나옵니다)
```
```
11-23 14:32:41  unity-hand-test          32초 · 동작 2번 · 출력 2.9천 · 새 입력 4.5천 · 다시 읽기 66만  "빨간 큐브 하나 만들어줘"
11-23 14:40:02  blender-test-blender     1분 9초 · 동작 8번 · 출력 6.4천 · 새 입력 9.7만 · 다시 읽기 157만  "의자를 고쳐서 Unity로 보내줘"

합계: 요청 2개 · 기다린 시간 1분 41초 · 출력 9.3천 · 새 입력 10만 · 다시 읽기 223만
  감독                     API 9번 · 출력 2.6천 · 새 입력 7.1만 · 다시 읽기 101만
  blender-test-blender     API 7번 · 출력 5.2천 · 새 입력 2.6만 · 다시 읽기 84만
  unity-hand-test          API 4번 · 출력 1.5천 · 새 입력 4.3천 · 다시 읽기 38만
```

- **시간**은 채팅창에서 보낸 때부터 결과 답이 온 때까지, **동작**은 하위 세션이 Unity · Blender 도구를 부른 횟수입니다.
- **출력**은 Claude가 써낸 토큰, **새 입력**은 새로 읽은 토큰(캐시에 새로 쓴 것 포함), **다시 읽기**는 캐시에서 다시 읽은 토큰입니다. 다시 읽기가 대부분이고 훨씬 싸서 합치지 않고 따로 보여 줍니다.
- 한참 쉬었다가 보낸 요청은 캐시가 식어서 대화를 새로 읽으므로 `새 입력`이 수십 배로 뜁니다. 비용을 줄일 때 볼 곳이 거기입니다.
- 달러로는 바꿔 보여 주지 않습니다(요금제마다 뜻이 다릅니다).
- `usage`는 Claude Hand가 꺼져 있어도 됩니다. 채팅창의 줄은 [설정창](#설정창)의 **요청마다 든 시간 · 토큰 줄**로 끕니다(바로 적용됩니다). 기록과 `usage`에는 그대로 남습니다.

### 저장하면 Unity의 모델이 따라오게 하기

모델을 Blender에서 고쳐 가며 Unity에서 볼 때, 고칠 때마다 "다시 보내줘"라고 하지 않아도 됩니다.

1. 한 번은 평소처럼 보냅니다("의자를 Unity로 보내줘"). 또는 Blender의 Claude Hand 패널 아래 **Unity로 보낸 모델** 칸에서 오브젝트를 고르고 **[선택한 오브젝트 보내기 → 프로젝트 이름]**을 누릅니다. 이 버튼은 Claude 없이 바로 보내고, 따라가기도 함께 켭니다.
2. 따라가게 켭니다. 같은 칸에서 그 모델의 체크를 켜거나, "의자는 저장할 때마다 Unity에 반영해줘"라고 말합니다.
3. 그 뒤로는 Blender에서 **저장할 때마다**(Ctrl+S) Unity의 모델이 1초쯤 뒤에 제자리에서 바뀝니다. 씬과 프리팹에 놓인 것이 그 자리에서 새 모양이 됩니다.

- **Claude를 거치지 않습니다.** Blender가 Unity에 직접 보내서 토큰이 들지 않고, Claude Hand가 꺼져 있어도 됩니다. Unity는 열려 있어야 하고, 뒤에 있어도 됩니다.
- **바뀐 것이 없으면 보내지 않습니다.** 다른 오브젝트만 고치고 저장하면 Unity는 그대로입니다.
- **바뀌었을 때만 한 줄이 남습니다**(`저장 → hand_test: Chair 반영`). 저장이 이어지면 줄이 늘지 않고 그 줄이 `3번째`로 바뀝니다.
- **따라가는지는 Unity 쪽 모델에 적힙니다**(모델의 `.meta`). 모델을 지우면 함께 사라지고, `.blend`를 남에게 줘도 그쪽 Unity로 가지 않습니다.
- **이름을 바꿔도 따라갑니다.** 보낸 오브젝트에 보낸 이름이 사용자 속성(`claude_hand_sent_as`)으로 남기 때문입니다. 그 오브젝트를 복제해 둘 다 이름을 바꾸면 어느 쪽인지 알 수 없어서 패널이 묻습니다(**[선택한 오브젝트로 잇기]**).
- **다른 이름으로 저장해도 따라갑니다**(사본 저장은 아닙니다). 탐색기에서 파일을 옮기거나 이름을 바꿨으면 패널에 "다른 파일에서 온 것으로 적혀 있음"으로 나오고, **[이 파일로 잇기]**를 누르면 됩니다.
- **Unity가 닫혀 있는 동안 저장한 것**은 Unity를 열었을 때, 또는 그 `.blend`를 다시 열었을 때 한 번 맞춰집니다.
- **되돌리기 대상이 아닙니다.** 요청이 아니라서요. Unity의 모델은 마지막으로 저장된 `.blend`를 따릅니다. Blender에서 **되돌리기**를 누르면 되돌린 모양이 Unity로 다시 갑니다. 패널의 보내기 버튼으로 처음 보낸 모델은 Unity 채팅창의 **되돌리기**로 뺄 수 있습니다.
- 끄려면 체크를 끄거나 "의자는 이제 따라오지 않게 해줘"라고 합니다.

### 건드리면 안 되는 것 정해 두기 (보호 규칙)

"이건 바꾸지 마"를 창마다 정해 둘 수 있습니다. 메모(`NOTES.md`)는 Claude가 읽고 지키려고 할 뿐이지만, 보호 규칙은 **기계가 막거나 되돌립니다.**

**정하기.** Unity 채팅창과 Blender 패널의 **보호** 칸에서 합니다.

| 버튼 | 뜻 |
|---|---|
| **[선택한 것 보호]** | 고른 오브젝트와 그 아래 전부. Unity에서는 Project 창에서 고른 에셋 · 폴더도 |
| **[이 창 보기만]** | Claude가 이 창을 읽기만 합니다 |
| **[이 창 잠금]** | 이 창에는 일을 맡기지도 않습니다 |
| **동작 금지** | Unity의 씬 저장 · 에셋 삭제 · 플레이 모드, Blender의 .blend 저장 |

- **이번만**을 켜고 더하면 Claude Hand를 끌 때까지만 둡니다. 끄고 더하면 프로젝트(`ClaudeHand/protect.json`)나 .blend 옆(`<이름>.claude-hand.protect.json`)에 적혀 계속 남습니다.
- 말로 해도 됩니다: "Hero는 건드리지 마", "Assets/Art는 손대지 마", "이 창은 보기만 해". Claude가 규칙을 **제안**하고, 채팅창에 뜬 **[추가]**를 눌러야 규칙이 됩니다.
- **푸는 것은 사용자만 합니다.** 보호 칸에서 그 줄의 **[풀기]**를 누릅니다. Claude에게는 규칙을 고치거나 푸는 도구가 없습니다.
- 상태는 색이 아니라 글로 보입니다(보호 칸 제목의 `[잠김]` · `[열림]`, Blender는 자물쇠 모양).

**규칙에 걸리면.**

1. 하위 세션은 일을 시작할 때 규칙을 읽습니다. 보호된 것은 빼고 하거나, 하지 않고 "보호 칸에서 [풀기]를 누른 뒤 다시 말해 달라"고 답합니다.
2. 그래도 건드리려는 호출은 **실행 전에 막힙니다**: 보호한 에셋을 고치는 도구, 보호한 오브젝트를 가리키는 호출, 금지한 동작, 보기만인 창의 바꾸는 호출.
3. Unity에서는 보호한 에셋의 저장 · 삭제 · 이동을 **Unity가 거부합니다.** 어떤 코드로 하든 걸립니다.
4. 그래도 보호한 것이 바뀌었으면 호출 직후에 알아채 **그 요청이 그 창에 한 일을 통째로 되돌립니다.**

채팅창에는 `보호 규칙이 막았어요: …`나 `보호된 Hero (자리)이(가) 바뀌어 이 요청이 이 창에 한 일을 되돌렸어요` 한 줄이 뜹니다.

보호한 에셋을 **쓰는 것**은 됩니다(보호한 프리팹을 씬에 놓기, 보호한 머티리얼을 다른 오브젝트에 입히기). 막는 것은 그 에셋 자체를 바꾸는 일입니다.

### 채팅이 연결되지 않을 때

```powershell
uv run tools/claude_hand.py chat-status
```

지금 상태와 고치는 방법을 알려 줍니다. 흔한 원인은 이렇습니다.

- 감독 세션을 `start-supervisor.cmd`가 아닌 방법(데스크톱 앱, 그냥 `claude`)으로 열었다. 채널 서버가 대기 모드로 뜨고, 채팅창에 그 이유가 보입니다.
- 이전 버전의 등록이 남아 있다 → [새 버전 받기](#새-버전-받기)를 하세요.
- `.mcp.json` 서버 승인 대기나 거절 → `/mcp`에서 승인하세요. 거절했었다면 `claude mcp reset-project-choices`를 하세요.

알아둘 점:

- Claude Code의 **Channels는 아직 리서치 프리뷰**입니다. 직접 만든 채널이라 `--dangerously-load-development-channels`로 켜야 하고, 처음에 개발용 채널 경고와 `.mcp.json` 서버 승인 창이 뜹니다.
- **채팅을 받는 건 `start-supervisor`로 연 세션뿐입니다.** 이 폴더에서 그냥 연 Claude Code도 채널 서버를 띄우지만 채팅은 받지 않습니다. 채널 없는 세션이 채팅을 가로채지 않게 하기 위해서입니다. 감독 세션은 하나만 여세요.
- 채널 서버가 채팅을 받는 조건은 둘 중 하나입니다: `start-supervisor`가 넘긴 표시(`CLAUDE_HAND_SUPERVISOR=1`)가 있거나, 자기를 띄운 Claude Code가 이 서버를 채널로 불러왔을 때.
- 채팅창과 채널은 내 PC 안(127.0.0.1)에서만, 토큰을 아는 프로그램끼리만 통신합니다. 토큰은 `%USERPROFILE%\.claude-hand\chat\server.json`에 있습니다. 방화벽 규칙은 필요 없고, 방화벽 창이 떠도 허용하지 않아도 됩니다.
- `chat-setup`은 이 폴더에 `.mcp.json`(채널 등록)과 `.claude/settings.local.json`(답장 · 현재 상황 도구 자동 허용, 감독이 Unity · Blender를 직접 조작하는 도구 막기)을 만듭니다. 기존 내용은 유지하고, 둘 다 git에 올라가지 않습니다. 채널 서버도 한 번 미리 실행해 점검합니다(처음에는 uv가 Python을 내려받느라 늦을 수 있어서).

## 설정창

채팅창의 **톱니바퀴 버튼**을 누르면 설정창이 뜹니다. Unity는 채팅창 맨 위 오른쪽에, Blender는 Claude Hand Chat 패널 제목 줄의 오른쪽 끝에 있습니다. 탭이 둘입니다.

### 기능 켜고 끄기

| 설정 | 하는 일 | 바꾸면 |
|---|---|---|
| **언어 · Language** | 화면의 글을 한국어나 English로 | 바로 |
| **요청마다 든 시간 · 토큰 줄** | 결과 답 아래의 `이 요청: …` 줄 | 바로 |
| **바뀐 다른 창의 그림 붙이기** | 요청이 다른 창을 바꿨을 때 결과 아래에 붙는 그 창의 그림 | 바로 |
| **답이 오면 작업 표시줄 깜빡임** | 다른 프로그램을 보는 동안 온 답 · 허락 요청 알림 (Windows) | 바로 |
| **Claude 데스크톱 앱에서 보기 (Remote Control)** | 데스크톱 앱 · 모바일에서 세션 보기 | 다음에 켤 때 |
| **Claude Hand 창 자동 최소화** | 세션이 일을 시작하면 검은 Claude Hand 창 최소화 (Windows) | 다음에 켤 때 |
| **대화 내용 보관 일수** | 채팅창에 다시 보여 주는 지난 대화와 그 그림을 며칠 둘지. 0이면 날짜로는 지우지 않습니다(창마다 최근 100개) | 다음에 켤 때 |
| **옮긴 파일 보관 일수** | 교환 폴더에 남은 옮긴 파일 사본을 며칠 둘지. 0이면 지우지 않습니다 | 다음에 켤 때 |

- **상태는 글로 보입니다**: `켜짐` · `꺼짐`, `30일`, `지우지 않음`. `*`가 붙은 것은 다음에 Claude Hand를 켤 때부터 적용됩니다.
- **한 곳에 적힙니다.** 설정은 `%USERPROFILE%\.claude-hand\config.json`에 있고, Unity · Blender · 채널 · 명령 · 안전장치가 모두 이 파일을 쓸 때마다 다시 읽습니다. 한 창에서 바꾸면 다른 창에도 바로 반영되고, Claude Hand를 다시 켤 필요가 없습니다(표에서 "다음에 켤 때"인 넷은 빼고).
- **끄는 스위치가 없는 것:** 안전장치, 보호 규칙, 되돌리기 지점.
- "선택 항목 함께 보내기"는 메시지마다 고르는 것이라 보내기 버튼 옆에 그대로 있습니다.

### 화면 언어

**English**를 고르면 채팅창과 Blender 패널의 글, 채널이 채팅창에 쓰는 줄, `claude_hand.py` 명령의 출력, 막힌 호출의 이유가 영어로 나옵니다. 고르지 않으면 지금까지처럼 한국어입니다. 요청은 어느 언어로 써도 되고, Claude는 사용자가 쓴 언어로 답합니다.

- **켜져 있는 Claude Hand에도 바로 적용됩니다.** 채널이 감독에게 "화면 언어가 바뀌었다"는 알림과 버튼 이름 대조표를 보내고, 하위 세션 정의와 감독 스킬을 그 언어로 다시 씁니다.
- 이미 채팅창에 찍힌 줄은 찍힐 때의 언어로 남습니다.
- **Claude에게 주는 지침(`prompts/`)은 한국어 그대로입니다.** 화면이 영어일 때는 지침 끝에 영어 버튼 이름 대조표가 붙어, Claude가 버튼을 화면에 보이는 이름으로 말합니다. 지침까지 영어판으로 주는 것은 아래 실험실에 있습니다.
- `docs/` 아래 문서는 한국어입니다.

### 실험실

새 기능이 먼저 들어오는 자리입니다. 여기 있는 기능은 **켜야 쓰이고**, 써 보며 다듬은 뒤 기본 기능이 되면 실험실에서 빠집니다. 지금 있는 것:

| 기능 | 들어온 버전 | 하는 일 |
|---|---|---|
| **Claude 지침 영어판** | 1.1.0 | 언어가 English일 때 Claude에게 주는 지침(하위 세션, 감독 규칙)도 영어판(`prompts/en/`)으로 줍니다. 한국어판을 줄마다 옮긴 것이고 아직 실제 작업으로 다듬지 않았습니다. 다음에 켤 때부터 적용됩니다. |

### 명령으로 바꾸기

```powershell
uv run tools/claude_hand.py settings                          # 설정과 실험실 목록, 지금 값
uv run tools/claude_hand.py settings language en              # 화면을 영어로 (ko: 한국어로)
uv run tools/claude_hand.py settings history_keep_days 30     # 대화 내용을 30일 동안
uv run tools/claude_hand.py settings lab english_prompts on   # 실험실 기능 켜기
```

예전 명령(`remote-control on|off`, `minimize on|off`, `clean-exchange --keep-days N`)도 그대로 되고, 같은 설정을 바꿉니다.

## 창 사이 옮기기

채팅창이나 감독 세션에 그냥 말하면 됩니다.

| 이렇게 말하면 | 이렇게 됩니다 |
|---|---|
| "MyGame이랑 Props 씬 구조 동시에 봐줘" | 두 하위 세션이 각자 자기 프로젝트를 스냅샷합니다. |
| "Props에 있는 Rock_A 프리팹을 MyGame 씬의 Environment 아래에 넣어줘" | unity-props가 내보내고, unity-mygame이 dry run으로 확인한 뒤(덮어쓸 게 있으면 감독이 먼저 묻습니다) 가져와서 배치합니다. 씬은 저장하지 않은 상태로 보고합니다. |
| "character.blend의 Hero를 MyGame이랑 Props 씬에 둘 다 넣어줘" | blender-character가 FBX로 한 번 내보내고, 두 Unity 담당이 동시에 가져옵니다. |
| "props.blend의 Rock 세트를 character.blend로 가져와" | blender-props가 .blend로 내보내고, blender-character가 Append합니다. |
| (Blender에서 Hero를 고친 뒤) "Hero 다시 보내줘" | 같은 .blend의 같은 오브젝트라 새로 넣지 않고 있던 모델을 바꿉니다. 씬에 놓아 둔 Hero들이 그 자리에서 새 모양이 됩니다. |
| "Hero는 저장할 때마다 MyGame에 반영해줘" | 그 모델의 따라가기를 켭니다. 그 뒤로는 Blender에서 저장하면 Claude 없이 바로 바뀝니다. |
| "MyGame 씬의 Rock_A를 character.blend로 보내줘" | unity-mygame이 모델 파일로 내보내고, blender-character가 가져옵니다. |

Blender에서 Unity로 가는 모델은 재질(Principled BSDF)이 URP Lit으로 만들어지고, 부품의 회전이 Blender 그대로이고, 클립과 셰이프 키가 함께 갑니다. 무엇이 가고 무엇이 못 가는지는 [알려진 한계](#알려진-한계)에 있습니다.

## Claude 데스크톱 앱에서 보고 말하기

Claude Hand는 기본으로 Claude Code의 **Remote Control**로 열립니다. 쓰는 방식은 하나입니다. **검은 Claude Hand 창이 일을 하고, Unity · Blender 채팅창과 데스크톱 앱은 같은 세션에 말하는 입구**입니다.

- **Claude 데스크톱 앱의 Code 탭**(그리고 claude.ai/code, 모바일 앱) 세션 목록에 **"Claude Hand"**가 보입니다. 거기서 보고 말할 수 있습니다.
- 백그라운드로 도는 하위 세션도 보이고, 거기서 멈출 수 있습니다.
- "선택한 거 Blender로 보내줘"처럼 말해도 됩니다. Unity · Blender가 지금 무엇이 선택돼 있는지 Claude Hand에 알려 주고 있어서 감독이 대상을 찾습니다.
- 데스크톱 앱에서 시킨 일도 그 창의 채팅창에 **"데스크톱 앱에서"** 표시와 함께 남습니다.
- Claude Hand가 꺼져 있을 때는 데스크톱 앱의 아무 Code 탭 세션에서 **"Claude Hand 켜줘"**라고 하면 됩니다. 검은 창이 뜨면 확인 질문에 답하고, 세션 목록에서 "Claude Hand"로 옮겨 가세요.

알아둘 점:

- 데스크톱 앱에서 새로 연 Code 탭 세션은 Claude Hand가 아닙니다. 채팅창과 연결되지 않고 기록도 남지 않습니다. 작업은 "Claude Hand" 세션에서 하세요.
- 일반 **채팅** 탭에서는 동작하지 않습니다. **Code** 탭에서만 됩니다.
- Team · Enterprise 계정은 조직에서 Remote Control을 켜야 합니다. → [Team과 Enterprise 계정에서 쓸 때](#team과-enterprise-계정에서-쓸-때)
- 조건이 안 맞으면(API 키, Bedrock 등) Claude Hand는 Remote Control을 빼고 켜고 그 이유를 창에 보여 줍니다. 채팅창은 그대로 됩니다. 로그인 방식은 Claude Hand 창에서 `/status`로 볼 수 있습니다.

이 설정들은 [설정창](#설정창)에 있고, 명령으로도 바꿉니다.

```powershell
uv run tools/claude_hand.py remote-control off   # 데스크톱 앱에서 보기 끄기 (on으로 다시 켜기)
uv run tools/claude_hand.py minimize off         # Claude Hand 창 자동 최소화 끄기
uv run tools/claude_hand.py clean-exchange --keep-days 30   # 교환 폴더의 옮긴 파일을 며칠 둘지 (기본 30일, 0이면 지우지 않음)
```

## 새 버전 받기

```powershell
git pull
uv run tools/claude_hand.py update     # 하위 세션·감독 스킬·채팅 설정·Blender 애드온을 새 버전으로 (--dry-run으로 미리 보기)
```

- 그다음 Claude Hand를 다시 켭니다. Blender는 다시 열어야 새 애드온을 씁니다. Claude Hand Tools를 폴더 경로로 넣은 Unity 프로젝트는 다시 열거나 그 창으로 돌아가면 새 패키지를 컴파일합니다.
- 세팅 스킬로 할 때는 **`git pull` 뒤에 Claude Code를 다시 시작한 다음** "세팅 이어서 해줘"라고 하세요. 스킬 내용은 세션을 시작할 때 읽혀서, 받기 전에 연 세션은 옛 순서를 따릅니다.
- `update`는 이전 버전이 남긴 등록도 정리합니다. 이전 `chat-setup`이 local 범위로 등록한 채널은 새 `.mcp.json`보다 먼저 쓰여서 채팅을 막습니다.
- 받은 버전에서 무엇이 바뀌었는지는 [버전별 변경 내용](#버전별-변경-내용)에 있습니다.

## 들어 있는 것

| 위치 | 내용 |
|---|---|
| `.claude/skills/claude-hand-setup/` | 처음 세팅 스킬. 이 폴더에서 Claude Code를 열면 바로 쓸 수 있습니다. |
| `skills/orchestrator/` | 평소에 감독 세션이 따르는 규칙(스킬). `sync`가 `~/.claude/skills/claude-hand/`에 설치합니다. |
| `prompts/` | Claude에게 주는 지침의 원문. 하위 세션용(`unity-subagent.md`, `blender-subagent.md`)과 감독이 채팅창을 다루는 규칙(`chat-rules.md`). 고친 뒤에는 `update`로 다시 설치하고 Claude Hand를 다시 켭니다. `en/`은 영어판(실험실)이고, `glossary-en.md`는 화면이 영어일 때 한국어 지침 끝에 붙는 이름 대조표의 머리말입니다. |
| `settings/catalog.json` | 설정창이 보여 주는 설정과 실험실 기능의 목록(이름 · 설명 · 기본값 · 언제 적용되는지). Python · Unity · Blender가 이 한 파일을 읽습니다. |
| `tools/claude_hand.py` | 명령 모음. 하위 세션 생성(`list`, `sync`), 세팅(`doctor`, `selftest`, `update`, `unity-install`, `unity-open`, `blender-open`, `chat-setup`, `blender-install-chat`, `shortcut`), 설정(`settings`, `mode`, `remote-control`, `minimize`, `new-session`), 보기(`status`, `trace`, `usage`, `chat-status`), 정리(`clean-exchange`). |
| `tools/guard.py` | 하위 세션의 안전장치(훅). 호출 전에 다른 대상으로 가는 호출과 보호 규칙을 어기는 호출을 막고, 통과시킨 호출은 채팅창의 작업 줄에 보이게 하고, 요청의 첫 변경 앞에 되돌리기 지점을 찍고, Unity 호출이 이름 붙인 파일을 미리 복사합니다. 호출 뒤에는 창이 막았거나 되돌린 것을 하위 세션에게 알립니다. 표준 라이브러리만 쓰는 작은 파일로 따로 두어, 다른 파일의 실수가 안전장치를 멈추지 못하게 했습니다. |
| `tools/supervisor_hook.py` | 감독 세션의 훅. 감독이 하위 세션에게 일을 맡길 때마다 그 일이 어느 채팅창 요청의 것인지(`[요청 <번호>]`) 채널에 알립니다. 번호가 빠진 맡기기, 아직 다른 요청으로 일하는 창이나 잠긴 창에 맡기는 것은 거부합니다. |
| `tools/chat_channel.py` | 채팅 채널 서버. Claude Code가 실행하는 MCP 서버이고, Unity · Blender 채팅창과는 로컬 HTTP로 통신합니다. |
| `tools/unity_bridge.py` | 하위 세션이 `ch_*` 도구를 부를 때 쓰는 MCP 서버. `--list`로 통로를 연 Unity를 볼 수 있습니다. |
| `tools/handlib/` | 위 스크립트들을 이루는 모듈: 열린 창 찾기(`discovery`), 하위 세션 정의(`agents`), 진단(`checks`), 요청 기록(`eventlog`), 현황(`overview`), 코드가 읽기만 하는지 보기(`looks`), 바뀔 파일 미리 보관(`keeps`), 토큰 읽기(`usage`), 보호 규칙(`protect`), 설정과 실험실(`prefs`), 화면 언어(`lang`, 영어 표 `english`) 등. 목록은 `tools/handlib/__init__.py`에 있습니다. |
| `unity/com.claudehand.tools/` | Unity 패키지. `ch_*` 도구 11개와 채팅창 · 설정창을 추가합니다. `ch_*` 도구는 이 패키지가 여는 자기 통로로 불려서, MCP for Unity 없이도 컴파일되고 그 버전에 상관없이 동작합니다. |
| `blender/claude_hand_chat.py` | Blender 채팅 패널 애드온(설정창 포함). |
| `start-supervisor.cmd` / `.sh` | 감독 세션을 엽니다. 바탕화면 아이콘과 채팅창 버튼이 이 파일을 실행합니다. 열 때마다 `preflight`가 하위 세션과 설정을 맞추고, 이미 켜져 있으면 알려 줍니다. |
| `docs/` | [설계와 그 이유](docs/DESIGN.md), [버전별 자세한 기록](docs/CHANGELOG.md), [PC 검증 순서](docs/TEST-PLAN.md), 검증 결과([12단계](docs/TEST-RESULTS-12-WINDOWS.md), [13단계부터](docs/TEST-RESULTS-13-WINDOWS.md)), [구조 점검과 그 뒤의 기록](docs/REVIEW-2026-11-10.md) |

### 추가되는 Unity 도구

| 도구 | 하는 일 |
|---|---|
| `ch_editor_status` | 연결된 프로젝트, 포커스 여부, 컴파일 중인지를 알려 줍니다. 아무것도 바꾸지 않습니다. |
| `ch_scene_snapshot` | 씬 구조를 JSON으로 돌려주고, 오브젝트마다 `@s1a2b3c4:e5` 형태의 ref를 붙입니다. 씬이 바뀐 뒤 옛 ref를 쓰면 `STALE_REF`가 납니다. |
| `ch_export_objects` | 씬 오브젝트(ref)나 에셋을 의존 에셋과 함께 `.unitypackage`로 묶어 교환 폴더에 둡니다. 원본 씬은 바뀌지 않습니다. |
| `ch_import_package` | 위 패키지를 가져와 씬에 배치합니다. `dry_run=true`로 덮어쓸 에셋을 먼저 보여 주고, 덮어쓸 게 있으면 허락 없이는 진행하지 않습니다. |
| `ch_import_model` | Blender가 내보낸 FBX(또는 OBJ, GLB)를 `Assets/ClaudeHand/Imports/<이름>/` 아래로 가져와 씬에 배치합니다. **같은 .blend의 같은 오브젝트가 다시 오면 있던 모델 파일을 제자리에서 바꿉니다.** 바꾸기 전 파일은 `Library/ClaudeHand/replaced/`에 남습니다. `dry_run=true`로 어느 쪽이 될지 먼저 보고, `new_copy=true`면 따로 한 벌을 넣고, `materials="project"`면 프로젝트의 같은 이름 머티리얼에 연결합니다. |
| `ch_model_follow` | Blender에서 온 모델이 그 `.blend`의 저장을 따라가게 합니다. `list`는 받은 모델과 따라가는지를 보여 주고, `set`은 켜고 끕니다. 켜 두면 Blender 패널이 저장 때 스스로 다시 보냅니다. |
| `ch_export_model` | 씬 오브젝트(ref)나 모델 에셋을 Blender(또는 다른 Unity 프로젝트)가 받을 모델 파일로 교환 폴더에 둡니다. 모델 파일에서 온 오브젝트는 그 파일 그대로 보내고, Unity에서 만든 메시는 FBX Exporter 패키지(`com.unity.formats.fbx`)가 있을 때 FBX로 씁니다. |
| `ch_checkpoint` | 요청마다 되돌리기 지점을 둡니다. 요청의 첫 변경 직전에 저절로 찍히고, 채팅창의 **되돌리기**나 `action=undo`로 그 뒤의 변경을 되돌립니다. 그 요청이 일하는 동안 파일에 생긴 일도 함께 돌립니다. |
| `ch_protect` | 이 프로젝트의 보호 규칙을 보여 줍니다(`state`). 규칙을 바꾸지는 못합니다. |
| `ch_apply_settings` | Interaction Mode를 **No Throttling**으로 바꿉니다. Unity가 뒤에 있어도 명령을 처리하게 하는 설정입니다. 메뉴 **Tools > Claude Hand**에도 있습니다. |
| `ch_capture` | 씬 뷰를 지금 모습 그대로 PNG로 저장합니다(기즈모 없이). 채팅창의 결과 미리보기에 씁니다. |

- 명령줄에서도 부를 수 있습니다: `uv run tools/claude_hand.py unity-call MyGame ch_editor_status`
- 모든 오류에는 `retry` 값이 붙습니다. `safe`는 그대로 다시 호출, `after_fix`는 인자를 고친 뒤, `check_first`는 이미 반영됐을 수 있으니 상태부터 확인합니다.

### Blender 쪽

Blender 쪽 일은 MCP for Blender의 Python 실행 도구로 합니다. 옮기기 · 되돌리기 · 보호 규칙은 Claude Hand Chat 애드온의 함수이고, 하위 세션은 그것을 한 줄로 부릅니다.

| 함수 | 하는 일 |
|---|---|
| `export_blend`, `import_blend` | 다른 Blender로 보낼 `.blend`를 쓰고, 받은 것을 Append로 가져옵니다. |
| `export_fbx` | Unity로 보낼 FBX를 씁니다. 저장된 .blend의 같은 오브젝트를 다시 보내면 Unity가 있던 모델을 제자리에서 바꿉니다(이름을 바꾼 뒤에도). 선택과 편집 모드는 건드리지 않습니다. |
| `import_model` | Unity가 `ch_export_model`로 보낸 모델(FBX, OBJ, GLB)을 가져옵니다. |
| `undo_last_request`, `undo_request` | 요청 단위 되돌리기. 뒤의 것은 요청 번호로 되돌립니다(여러 창을 한 번에 되돌릴 때 채널이 창마다 부릅니다). |
| `protection_rules` | 이 창의 보호 규칙을 보여 줍니다. |

애드온은 하위 세션이 부르지 않는 일도 둘 합니다. 저장을 따라가게 켠 모델을 파일이 저장될 때 Unity로 직접 다시 보내고, 호출 앞뒤로 보호한 오브젝트가 그대로인지 봅니다.

되돌리기 지점은 요청에서 무언가를 바꿀 수 있는 첫 동작 앞에 찍힙니다. Python 실행 도구는 아무 코드나 돌리므로, 안전장치가 그 코드를 읽어 보고 읽기만 하는 것이 분명할 때(속성 읽기, `print`, 직접 만든 리스트 채우기, 내보내기)만 지점 없이 지나가게 합니다.

### agent-desktop은 어디에 쓰나

- **처음 세팅:** 명령어로 할 수 없는 클릭을 대신합니다. 예를 들어 Unity가 패키지를 받는 동안 뜨는 대화상자, 이미 열린 Blender의 포트 변경입니다.
- **평소:** Unity나 Blender가 대화상자 때문에 멈추면 그 내용을 읽어 사용자에게 보여 주고, 고른 버튼을 눌러 줍니다.
- **한계:** 최소화된 창은 조작하지 못합니다(먼저 복원해야 합니다). Unity · Blender의 내부 패널은 접근성 정보가 거의 없어서 읽히지 않을 수 있고, 그때는 허락을 받은 뒤 화면을 캡처하고 좌표로 클릭합니다(이 동안 마우스와 키보드가 실제로 움직입니다). 그래서 실제 작업(씬 수정, 모델 옮기기)은 agent-desktop이 아니라 MCP가 맡습니다.

## 안전장치

- **하위 세션은 자기 창만.** 정의 파일의 `tools` 허용 목록에 자기 전용 MCP 서버만 들어 있습니다. Unity 서버는 `--default-instance <Name@hash>`로, Blender 서버는 `--port <포트>`로 고정됩니다. Unity 서버(Stdio)에는 장치가 하나 더 걸립니다(`tools/unity_pin`): 맡은 창만 보고, 다른 창의 포트에는 연결하지 않고, 새 연결은 그 창이 맡은 프로젝트라고 답할 때만 씁니다. `--default-instance`는 기본값일 뿐이어서, 맡은 창의 연결이 잠깐 꺼지거나 응답이 늦으면 서버가 다른 Unity 창을 대신 골랐기 때문입니다. 이제 그런 호출은 실패하고, 창이 돌아오면 이어집니다. `doctor`가 이 장치가 걸려 있는지 확인합니다.
- **호출마다 검사.** 안전장치 훅(`tools/guard.py`)이 Unity 호출을 하나하나 검사해, 다른 프로젝트를 가리키는 `unity_instance`와 대상 변경(`set_active_instance`)을 막습니다. `batch_execute` 안에 들어 있는 명령도 봅니다.
- **보호 규칙.** 같은 훅이 호출을 그 창의 [보호 규칙](#건드리면-안-되는-것-정해-두기-보호-규칙)과 견줍니다. 호출이 끝난 뒤에도 한 번 더 돌아, 창이 막았거나 되돌린 것을 하위 세션에게 알립니다.
- **잘못되면 막는 쪽.** 훅 안에서 무엇이 잘못되든 호출을 막습니다(잘못된 인자, 읽을 수 없는 입력, 판단 중 오류, 확인할 수 없는 보호 규칙).
- **훅이 시작조차 못 하는 경우.** 그때는 Claude Code가 호출을 막지 않습니다. 그래서 `sync`가 훅 명령에 uv의 전체 경로를 적고, `selftest`가 설치된 훅을 Claude Code와 같은 방법으로 실제로 돌려 봅니다.
- **한 창에 한 하위 세션.** 같은 창에 둘이 동시에 일하면 서로 덮어쓰므로, Claude Hand가 켜져 있는 동안에는 나중에 온 하위 세션의 호출을 막습니다.
- **세팅은 허락받은 단계만.** 화면 조작 중 마우스와 키보드를 실제로 움직여야 할 때는 따로 다시 묻습니다.

## 알려진 한계

**환경**

- 작업 표시줄 알림, 한글 입력창, 대화상자 감지, Claude Hand 창 자동 최소화는 Windows에서만 됩니다. macOS · Linux에서는 실제 창으로 확인하지 않았습니다.
- 개발 중인 체크아웃에서 `unity-install`을 하면 Claude Hand Tools가 이 PC의 폴더 경로(`file:`)로 들어가서, 그 프로젝트는 다른 PC에서 열리지 않습니다. 릴리스 태그(예: `v1.0.0`)를 받은 체크아웃에서는 GitHub 주소로 들어갑니다. `unity-install --dry-run`이 어느 쪽인지 알려 줍니다.
- Blender 하위 세션이 처음 연결을 확인할 때와 `sync` · `doctor`가 파일 이름을 읽을 때, Blender 안에서 읽기 전용 Python(`print(bpy.data.filepath)`)이 실행됩니다.
- 한 Unity 창의 MCP for Unity 연결이 꺼지면(Window > MCP for Unity가 Connected가 아님) 그 창의 하위 세션 호출은 실패합니다. 다른 창으로 가지는 않습니다. Claude Hand Tools가 20초쯤 뒤에 다시 켜고, 안 켜지면 그 창의 Window > MCP for Unity에서 연결합니다. 감독 세션이나 다른 Claude Code 세션에 붙은 MCP for Unity 서버가 시작하거나 쓰일 때 Unity 쪽 연결이 한 번 끊길 수 있습니다(그 서버들은 장치 밖입니다).
- 하위 세션의 서버가 맡은 Unity 창만 보게 하는 장치는 MCP for Unity를 Stdio로 쓸 때만 걸립니다. HTTP 방식에서는 하위 세션이 첫 호출로 대상을 정하는 것에 기대고, 그 길은 Unity 창 둘로 시험하지 않았습니다. Unity 창을 여럿 쓰면 Stdio를 쓰세요.

**Unity ↔ Unity**

- 내보내는 스크립트가 asmdef에 속해 있어도 asmdef는 같이 가지 않습니다. 받는 쪽에서는 기본 어셈블리로 컴파일됩니다.
- `Packages/` 안의 에셋(URP 셰이더 등)은 `.unitypackage`에 들어가지 않습니다. 받는 프로젝트에 같은 패키지가 있어야 하고, dry run의 `missing_packages`에 표시됩니다.

**Blender → Unity**

- **가는 것:** 메시, UV 두 벌, 버텍스 컬러, 리그와 가중치, 클립, 셰이프 키, 라이트, 카메라, 지오메트리 노드 인스턴스(메시로), Principled BSDF 재질(값이나 이미지를 곧바로 읽는 입력은 그대로, 다른 노드를 거치는 입력은 텍스처로 구워서).
- **구운 것은 굳은 이미지입니다.** 보는 각도나 시간에 따라 달라지는 노드 효과는 담기지 않습니다. 한 재질을 UV가 서로 겹치는 여러 오브젝트가 함께 쓰면 구운 이미지가 겹쳐 그려집니다.
- **아직 못 가는 것:** Principled BSDF가 아닌 셰이더, 노드를 거친 투명도(받을 때 무엇이 빠졌는지 알려 줍니다), 천 · 소프트 보디처럼 꼭짓점이 움직이는 시뮬레이션, 파티클, 헤어, 그리스 펜슬, 월드.
- 재질은 URP와 렌더 파이프라인 없는 프로젝트에서만 만들어집니다(HDRP는 Unity가 만든 기본 재질 그대로). 프로젝트에 같은 이름의 머티리얼을 만들어 두고 `materials="project"`로 받는 방법도 됩니다.
- **제자리에서 바꾸는 조건:** 저장된 .blend에서 같은 오브젝트를 보냈을 때입니다. 오브젝트 이름을 바꿨거나 Blender에서 다른 이름으로 저장한 뒤에도 됩니다. 탐색기에서 .blend를 옮겼으면 Blender 패널의 **[이 파일로 잇기]**를 눌러야 하고, 누르지 않으면 새 모델로 들어옵니다.

**Unity → Blender**

- 모델 파일에서 온 오브젝트는 그 파일이 그대로 갑니다. 씬에서 바꾼 것(머티리얼 교체, 덧붙인 오브젝트, 위치)은 가지 않습니다.
- Unity에서 만든 메시는 FBX Exporter 패키지가 있어야 하고, 이 경로는 그 패키지의 소스를 보고 만들었을 뿐 실제로 돌려 보지는 않았습니다.

**되돌리기**

- 파일까지 돌리는 것은 그 요청이 **일하는 동안** 바뀐 것입니다. 하위 세션이 끝난 뒤에 직접 만들거나 고친 파일은 건드리지 않습니다. 씬은 다릅니다: 그 뒤에 직접 고친 것도 함께 되돌아갑니다.
- 돌아가지 않는 파일: 열려 있는 씬의 파일(다시 저장해야 바뀝니다), `Assets` 밖의 파일(`Packages`, `ProjectSettings`), 하위 세션이 코드 실행으로 직접 덮어쓴 파일, 64 MB가 넘는 파일. 뒤의 둘은 이전 내용이 남아 있지 않아서, 되돌릴 때 그 파일 이름을 알려 줍니다. 다시 보낸 모델을 되돌릴 때 `Materials/`와 `Textures/`의 파일도 되돌아가지 않습니다.
- 바꾸기 전 모델 파일은 `Library/ClaudeHand/replaced/`에 최근 요청 10개까지 있어서, 그보다 오래된 것은 되돌리기로도 돌아오지 않습니다.
- 요청이 일하는 동안 Unity에서 지운 에셋은 휴지통이 아니라 그 프로젝트의 `Library/ClaudeHand/removed/req_<요청>/`으로 갑니다(되돌리면 돌아옵니다). 그동안 Project 창에서 직접 지운 것도 같습니다. 휴지통이 "바로 삭제"로 설정돼 있으면 그대로 사라지기 때문입니다. Unity 6.3에서만 확인했습니다.
- 되돌리기로 빠진 에셋은 Unity의 다시 실행(Ctrl+Y)으로 돌아오지 않습니다. 필요하면 `Library/ClaudeHand/removed/undo_<빠진 때>/`에 있는 것을 `.meta`와 함께 원래 자리로 옮기면 됩니다(최근 10번 치만 남습니다).
- 여러 창을 한 번에 되돌리는 것은 **채팅창에서 시킨 요청**만 됩니다(데스크톱 앱이나 터미널에서 시킨 일은 창마다 되돌립니다). 창마다 그 요청이 가장 새 변경일 때만 되돌리고, 그 뒤에 다른 요청이 그 창을 바꿨거나 그 창이 작업 중이면 건너뛰고 알려 줍니다. 그 창이 풀린 뒤 같은 버튼을 다시 누르면 남은 창만 되돌립니다. 한 창이 실패해도 다른 창은 되돌아갑니다.
- 물어보기만 한 요청을 건너뛰는 방법은 두 앱이 다릅니다. Unity는 지점을 찍은 뒤에 바뀐 것이 있는지 봅니다(Undo를 거치지 않은 변경은 "바뀐 것 없음"으로 보는데, 그런 변경은 되돌리기로도 돌아가지 않습니다). Blender는 돌리기 전에 코드를 읽습니다. 읽기만 하는 코드인데도 알아보지 못하면 되돌릴 것이 없는 지점이 남아, 되돌리기를 처음 눌렀을 때 아무 일도 없습니다. 한 번 더 누르면 앞 요청이 돌아갑니다.

**중지**

- 하위 세션의 다음 Unity · Blender 호출부터 막습니다. 이미 시작한 동작 하나(긴 가져오기 등)는 끝까지 갑니다.
- 그 요청에 답이 오면 풀리고, 답이 없어도 3분 뒤에는 풀립니다. 그 사이에 데스크톱 앱에서 다른 창에 시킨 일이 있으면 그 일도 함께 막힐 수 있습니다.

**시간 · 토큰**

- Claude Code가 남기는 대화 기록 파일에서 읽습니다. 문서에 없는 내부 형식이라(2.1.322에서 확인) Claude Code가 바뀌면 줄에 토큰이 빠지고 시간과 동작만 나올 수 있습니다. `selftest`의 "감독 세션 기록에서 토큰 읽기" 항목이 그것을 알려 줍니다.
- 줄이 나온 뒤에 그 요청으로 더 쓴 토큰은 들어가지 않습니다. 감독이 한 차례에 두 요청의 일을 이어 하면 감독의 토큰은 그 차례를 시작시킨 요청 쪽으로 갑니다.
- 데스크톱 앱이나 터미널에서 시킨 일은 요청별로 나오지 않습니다. 채팅창의 줄은 Claude Hand를 다시 켜면 사라지고, 기록(`trace`, `usage`)에는 2주 동안 남습니다.

**저장을 따라가는 모델**

- Unity가 열려 있을 때만 갑니다. 요청 기록(`trace`)에는 남지 않습니다.
- Bevel 모디파이어가 붙은 메시는 Blender가 계산할 때마다 UV를 조금씩 다르게 내놓아서, 파일만으로는 바뀌었는지 알 수 없습니다. 그런 모델은 Blender가 "건드렸다"고 알려 줄 때만 보내고, `.blend`를 연 뒤 첫 번에는 한 번 보냅니다.
- 저장할 때마다 따라가는 모델을 내보내므로(면 8만 개에 0.1초쯤) 아주 무거운 모델은 저장이 그만큼 길어집니다. 내보내기가 1초를 넘는 모델은 건드렸을 때만 내보냅니다.

**보호 규칙**

- 일부러 숨긴 코드를 막는 자물쇠가 아닙니다. 하위 세션이 실수로, 또는 메모를 못 봐서 건드리는 것을 막습니다. claude-hand를 거치지 않는 다른 도구는 대상이 아닙니다.
- 요청이 일하는 동안에는 사용자가 직접 한 저장 · 삭제 · 플레이도 규칙에 걸리면 막히고, 호출이 도는 사이에 보호 오브젝트를 직접 고치면 Claude의 변경으로 보고 되돌립니다.
- Unity에서 Undo를 거치지 않은 변경은 알아채지만 되돌리지 못하고, 그렇게 알려 줍니다.
- Blender의 `.blend 저장` 금지는 코드에 저장 호출이 보일 때만 막습니다.
- 코드에 보호 오브젝트의 이름이 따옴표로 나오면, 읽기만 하려던 것이어도 한 번 막히고 "읽는 호출과 바꾸는 호출을 나누라"는 안내가 갑니다.

**설정 · 언어**

- 데스크톱 앱에서 보기(Remote Control)는 Claude Code를 켤 때 정해집니다. 켜져 있는 Claude Hand에서 바꾸려면 그 창에 `/remote-control`을 직접 입력해야 합니다(슬래시 명령은 사용자만 칠 수 있어서, 설정창은 다음에 켤 때부터 적용합니다).
- 화면 언어를 바꿔도 이미 찍힌 채팅창 줄과 기록(`trace`)의 지난 줄은 그때의 언어로 남습니다.
- 감독이 처음 받은 지침은 다시 켜야 바뀝니다. 그 사이에 버튼 이름을 화면대로 말하는 것은 채널이 보내는 알림에 달려 있습니다.
- 지침 영어판(실험실)은 한국어판을 줄마다 옮긴 것이고, 실제 감독 세션으로는 한 번 다듬었습니다. 그때 감독이 시키지 않은 씬 저장을 Unity 담당에게 맡겼고(한국어판에서는 저장하지 말라고 맡깁니다), 영어 채팅 규칙에 한 문장을 더해 막았습니다. 더 써 볼 때까지 실험실에 둡니다.
- Blender 버튼의 도움말(마우스를 올리면 뜨는 글)과 Unity 도구의 설명 · 오류는 처음부터 영어이고, 언어 설정을 따르지 않습니다.
- 설정창의 목록은 claude-hand 폴더의 `settings/catalog.json`에서 읽습니다. `chat-setup`을 한 적이 없어 폴더 위치를 모르면 언어만 고를 수 있습니다.
- 영어 화면은 자동 테스트, 열린 창(감독 자리는 흉내), 실제 감독 세션으로 확인했습니다. 실제 세션에서 Claude는 요청이 쓰인 언어로 답하고 버튼을 화면의 이름(`Undo`, `Release`)으로 불렀습니다([검증 순서](docs/TEST-PLAN.md) 34단계).

## 버전별 변경 내용

번호는 저장소 루트의 `VERSION`을 따릅니다. 태그는 `v0.9.0`과 `v1.0.0`에 붙어 있습니다. 버전마다 무엇이 왜 바뀌었는지 자세한 기록은 [docs/CHANGELOG.md](docs/CHANGELOG.md)에 있습니다.

### 1.1.0 (2026-11-25): 설정창 · 화면 영어 · 실험실

- **설정창.** Unity 채팅창과 Blender 패널의 톱니바퀴 버튼으로 엽니다. "기능 켜고 끄기"와 "실험실" 두 탭이고, 바꾼 것은 `config.json`에 바로 적혀 모든 창 · 명령 · 안전장치에 반영됩니다. 대화 내용 보관 일수를 정할 수 있게 됐고, 다른 창 그림 붙이기와 작업 표시줄 깜빡임에 스위치가 생겼습니다. 명령은 `settings`입니다.
- **화면 영어.** 설정창의 언어에서 English를 고르면 채팅창 · Blender 패널 · 명령 출력 · 막힌 호출의 이유가 영어로 나옵니다. Claude Hand를 다시 켜지 않아도 됩니다. 한국어(기본)로 쓸 때의 글은 그대로입니다.
- **1.0.1 위에.** 1.0.1이 고친 것(하위 세션의 서버가 맡은 Unity 창에만 닿음; 아래 표와 [변경 기록](docs/CHANGELOG.md))이 들어 있고, 1.0.1이 새로 하는 말(`doctor`의 항목, Unity 지침의 한 줄, Claude Hand Tools가 Console에 남기는 줄)에도 영어가 있습니다.
- **실험실.** 새 기능이 먼저 들어오는 자리입니다. 첫 기능은 Claude에게 주는 지침의 영어판입니다.
- 확인: Python 테스트 559개(한글 문자열마다 영어가 있는지, 구현마다 둔 영어 표가 서로 어긋나지 않는지, 지침 영어판이 한국어판과 줄마다 맞는지 포함), 창 없는 Blender 안 테스트 120개, 창 없는 Unity 안 자가 테스트 79개(새 7개: 언어 · 설정). 열린 창으로도 봤습니다: 따로 꾸민 시험 환경에서 두 설정창과 영어 화면을 그림으로 떠서 보고, 실제 채널과 훅에 감독 자리만 흉내를 세워 요청을 돌렸습니다. 이때 영어에서 글이 잘리던 곳과, Unity 채팅창이 다시 읽힌 뒤 보내기가 막히던 것(1.0.0부터 있던 것)을 찾아 고쳤습니다.
- 실제 감독 세션으로 확인(2026-11-26, 실제 `hand_test`와 `test_blender`를 열고 Claude Hand를 두 번 켬): 한국어 요청은 전과 같음. 감독이 켜진 채 언어를 English로 바꾸면 알림이 가고 두 창이 그 자리에서 바뀜. 영어 요청은 Unity · Blender 모두 영어로 답하고 작업 줄 · 시간과 토큰 줄 · `Undo: …`가 영어. 한국어로 되돌리기 버튼을 물으면 한국어로 답하되 `[Undo]`라고 부름. 보호 규칙에 막히면 "open the Protect section … press [Release]"라고 안내. Blender에서 Unity로 간 요청을 실제 되돌리기 버튼으로 두 창 함께 되돌림. 실험실의 지침 영어판에서는 정의와 스킬이 영어이고 요청이 `[request <번호>]`와 함께 끝까지 됨. 데스크톱 앱에서 보기와 자동 최소화를 끄면 `--remote-control` 없이 켜지고 창이 그대로 있음. 한국어로 돌리면 정의 두 파일이 전과 바이트까지 같음.
- 그 세션에서 찾아 고친 것: 지침 영어판에서 감독이 시키지 않은 씬 저장을 Unity 담당에게 맡기던 것(창 없이 감독만 따로 돌려 재 봄: 한 문장을 더하기 전 5번 중 5번, 더한 뒤 5번 중 0번, 한국어판은 4번 중 0번). 데스크톱 앱에서 보기를 끄면 Claude Hand 창의 제목이 `claude`이던 것(1.0.0부터): 이제 세션에 늘 이름을 줍니다. 자동 최소화를 꺼도 `start`와 창 첫머리가 "저절로 최소화됩니다"라고 하던 것. `CLAUDE_HAND_LANG=en`으로 `doctor`를 한 번만 영어로 보면 설치된 정의와 스킬을 이전 버전 것이라고 하던 것. `settings` 목록의 칸 하나, 언어 알림의 "한국어(으)로".
- 아직 보지 않은 것: 데스크톱 앱에서 보기를 껐을 때 앱의 세션 목록에서 빠지는지(명령줄까지만 봄), 작업 표시줄 깜빡임, 설정창 줄에 마우스를 올리면 뜨는 설명.

| 버전 | 날짜 | 무엇이 바뀌었나 |
|---|---|---|
| 1.0.1 | 2026-11-25 | 하위 세션의 서버가 맡은 Unity 창에만 닿음 · claude-hand가 일하던 Unity 연결을 끊지 않음 · 꺼진 MCP for Unity 연결 다시 켜기 (`update`로 정의를 다시 쓰고 Claude Hand를 다시 켜면 적용) |
| 1.0.0 | 2026-11-24 | 첫 정식 버전: 번호와 문서 (태그 `v1.0.0`) |
| 0.18.0 | 2026-11-24 | 창마다 보호 규칙 |
| 0.17.1 | 2026-11-24 | 열린 창에서 더 찾은 셋 |
| 0.17.0 | 2026-11-24 | Blender에서 보낸 모델이 깔끔하게 들어오게 |
| 0.16.0 | 2026-11-24 | 저장하면 자동으로 다시 보내기 |
| 0.15.0 | 2026-11-23 | 요청마다 시간 · 토큰 |
| 0.14.0 | 2026-11-23 | 여러 창을 한 번에 되돌리기 |
| 0.13.0 | 2026-11-16 | 어떤 도구가 바꿨든 파일까지 되돌리기 |
| 0.12.1 | 2026-11-12 | 실제 감독 세션에서 찾은 것 고침 |
| 0.12.0 | 2026-11-11 | 창들의 현황 한눈에 |
| 0.11.0 | 2026-11-11 | 파일까지 되돌리기 |
| 0.10.0 | 2026-11-11 | 요청 번호 잇기 |
| 0.9.1 | 2026-11-11 | 미리보기 그림이 다른 채팅창에 붙던 문제 |
| 0.9.0 | 2026-11-10 | 모델 다시 보내기, 요청 기록, 중지 (태그 `v0.9.0`) |
| 0.8.0 | 2026-11-10 | 구조 개편 |

## 개발

```bash
python -m unittest discover -s tools/tests -v     # Python 3.10 이상, 따로 설치할 것 없음
```

- GitHub에 올리면 같은 테스트가 Windows와 Linux, Python 3.10과 3.13에서 돕니다(`.github/workflows/tests.yml`).
- 버전은 저장소 루트의 `VERSION` 한 곳에서 정하고, Unity 패키지와 Blender 애드온에 적힌 번호가 같은지 테스트가 확인합니다.
- `tools/tests/test_blender_chat_addon.py`는 Blender 애드온을 실제 Blender 안에서 실행합니다. 위 명령으로는 건너뛰고(Python에 `bpy`가 없으므로), `uv run tools/claude_hand.py selftest`가 설치된 Blender를 화면 없이 띄워 돌립니다.
- Unity 쪽은 패키지 안의 `ChSelfTest.cs`가 도구들을 실제 Unity에서 돌립니다. `selftest --unity-project <닫힌 프로젝트>`가 Unity를 창 없이 열어 실행합니다.
- 테스트는 진짜 이벤트 기록을 건드리지 않게 `CLAUDE_HAND_LOG=off`로, 이 PC의 언어 설정과 상관없이 한국어로(`CLAUDE_HAND_LANG=ko`) 돕니다.
- **화면에 보이는 글을 더하면 영어도 함께 넣습니다.** 한국어 글은 코드에 `T("…")`로 두고, 영어는 구현마다 있는 표에 넣습니다: Python 도구 `tools/handlib/english.py`, 안전장치 `tools/guard.py`, Unity `ChText.cs`, Blender 애드온 끝의 `_EN`. 수를 세는 글이면 하나일 때의 영어(`1 action`)도 그 옆의 표(`EN_ONE` · `EnglishOne` · `_EN_ONE`)에 넣습니다. `tools/tests/test_lang.py`가 영어가 빠진 글, 표끼리 어긋난 글을 잡습니다. 지침(`prompts/`)을 고치면 `prompts/en/`의 같은 줄도 고칩니다(같은 테스트가 줄 수와 자리 표시를 견줍니다).
- **새 설정이나 실험실 기능**은 `settings/catalog.json`에 한 줄 넣으면 두 설정창과 `settings` 명령에 나옵니다. 실험실 기능을 기본 기능으로 올릴 때는 그 줄의 `stage`를 `lab`에서 `stable`로 바꿉니다(모두에게 켜지고 실험실 탭에서 빠집니다).

## 라이선스

[MIT](LICENSE)입니다. 함께 쓰는 MCP for Unity, MCP for Blender, agent-desktop은 이 저장소에 들어 있지 않고 각자의 라이선스를 따릅니다.
