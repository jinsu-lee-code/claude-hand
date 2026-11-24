# claude-hand

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

> **1.0.0** (2026-11-24) · Windows의 실제 Unity 6.3과 Blender 5.2에서, 열린 창과 실제 감독 세션으로 확인했습니다. macOS·Linux에서는 자동 테스트만 돌렸습니다.
> 무엇을 어떻게 확인했는지는 [검증 순서](docs/TEST-PLAN.md)와 [점검 기록](docs/REVIEW-2026-11-10.md)에, 버전마다 바뀐 것은 [버전별 변경 내용](#버전별-변경-내용)에 있습니다.

## 할 수 있는 것

- **창마다 전담 Claude.** 열려 있는 Unity 프로젝트와 Blender 파일마다 하위 세션이 하나씩 붙습니다. 자기 창 말고는 건드리지 못합니다.
- **창 안에서 채팅.** Unity와 Blender 안의 채팅창에서 바로 시키고 답을 받습니다. 지금 선택한 것이 함께 전해져서 "이거 복제해줘"라고 해도 됩니다. → [창마다 채팅하기](#창마다-채팅하기)
- **창 사이 옮기기.** Unity ↔ Unity, Blender ↔ Blender, Blender ↔ Unity. Blender의 모델은 재질 · 회전 · 움직임까지 갑니다. → [이렇게 씁니다](#이렇게-씁니다)
- **저장하면 따라오기.** Blender에서 저장할 때마다 Unity의 모델이 제자리에서 바뀝니다. Claude를 거치지 않아 토큰이 들지 않습니다. → [저장하면 Unity의 모델이 따라오게 하기](#저장하면-unity의-모델이-따라오게-하기)
- **요청 단위 되돌리기.** 버튼 하나로 그 요청이 바꾼 것을 되돌립니다. 파일까지, 그 요청이 바꾼 다른 창까지.
- **중지 · 현황 · 기록.** 일하는 중에 멈추고, 다른 창이 무엇을 하는지 보고, 한 요청에 무슨 일이 있었는지와 든 시간 · 토큰을 봅니다(`status`, `trace`, `usage`).
- **보호 규칙.** "이건 건드리지 마"를 창마다 정해 두면 Claude가 지키기를 기대하지 않고 기계가 막거나 되돌립니다. → [건드리면 안 되는 것 정해 두기](#건드리면-안-되는-것-정해-두기-보호-규칙)
- **데스크톱 앱 · 모바일.** 같은 세션을 Claude 데스크톱 앱과 모바일 앱에서 보고 말합니다. → [Claude 데스크톱 앱에서 보고 말하기](#claude-데스크톱-앱에서-보고-말하기)

## 목차

- [처음 설치: Claude에게 맡기기](#처음-설치-claude에게-맡기기)
- [직접 설치하기 (Claude 없이)](#직접-설치하기-claude-없이)
- [매일 쓰는 법](#매일-쓰는-법)
- [Claude 데스크톱 앱에서 보고 말하기](#claude-데스크톱-앱에서-보고-말하기)
- [창마다 채팅하기](#창마다-채팅하기)
- [이렇게 씁니다](#이렇게-씁니다)
- [새 버전 받기 (이미 설치한 경우)](#새-버전-받기-이미-설치한-경우)
- [들어 있는 것](#들어-있는-것)
- [안전장치](#안전장치)
- [알려진 한계](#알려진-한계)
- [버전별 변경 내용](#버전별-변경-내용)
- [개발](#개발)
- [라이선스](#라이선스)

## 처음 설치: Claude에게 맡기기

필요한 건 **Claude Code**와 이 저장소뿐입니다.

1. 저장소의 `main` 브랜치를 받습니다. `git`이 없으면 GitHub 페이지에서 `main` 브랜치를 고른 뒤 ZIP으로 받아 풀어도 됩니다.
   ```powershell
   git clone -b main https://github.com/jinsu-lee-code/claude-hand.git
   cd claude-hand
   ```
2. 그 폴더에서 Claude Code를 엽니다(`claude`). 데스크톱 앱이면 이 폴더를 엽니다.
3. **"claude-hand 세팅해줘"**라고 말합니다.

그러면 `claude-hand-setup` 스킬이 다음 순서로 진행합니다.
1. **진단:** 무엇이 설치돼 있고 무엇이 빠졌는지 표로 보여줍니다(`doctor`).
2. **계획과 허락:** 설치하거나 바꿀 것을 보여주고, 할 단계를 고르게 합니다. 고른 단계만 진행합니다.
3. **설치와 세팅:** 아래 순서로 진행합니다.
   - 빠진 기본 도구(uv, git, Node.js)를 winget으로 설치
   - **agent-desktop** 설치
   - **MCP for Blender** 설치: 애드온 설치와 활성화, Claude 설정까지
   - Unity 프로젝트에 패키지 추가
   - Unity 절전 설정
   - 하위 세션 생성
   - 원하면 Unity·Blender 채팅창 연결
   - 명령어로 안 되는 화면 조작만 agent-desktop이 직접 합니다.
4. **확인:** 마지막에 다시 진단해서 남은 항목을 알려줍니다.

Claude가 대신 하지 않고 **사용자에게 직접 부탁하는 것**도 있습니다.
- 관리자 권한(UAC) 창, Windows 방화벽·보안 창
- 로그인, 라이선스 동의
- 비밀번호, API 키 입력
- 저장 안 된 Blender·Unity 창 닫기
- Claude Code 재시작 (새 MCP 서버를 등록한 뒤 한 번 필요합니다)

## 직접 설치하기 (Claude 없이)

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

`uv run tools/claude_hand.py doctor`를 실행하면 언제든 빠진 항목과 고치는 명령을 볼 수 있습니다.
설치가 아니라 **실제로 동작하는지**는 `uv run tools/claude_hand.py selftest`로 봅니다. 테스트(Python, Blender 안 애드온)를 돌리고, 열린 Unity·Blender와 채팅 채널이 이 버전으로 응답하는지 확인합니다(1분쯤, 아무것도 바꾸지 않음). 테스트를 빼려면 `--no-tests`.
모델을 옮기는 Unity 도구까지 실제로 돌려 보려면 **닫혀 있는** 프로젝트를 알려 줍니다.
```powershell
uv run tools/claude_hand.py selftest --unity-project "C:\경로\프로젝트"
```
Unity를 창 없이 열어, Blender가 실제로 내보낸 모델을 받고, 같은 모델이 다시 왔을 때 제자리에서 바꾸고, 프로젝트 머티리얼에 연결하고, 다시 Blender로 보내는 것까지 확인합니다(3~4분). 요청을 되돌렸을 때 바뀐 파일이 돌아오고 들어온 에셋이 프로젝트에서 빠지는지, 다른 프로젝트에서 온 패키지를 받고 되돌리는 것, 바뀐 것이 없는 되돌리기 지점을 건너뛰는 것, 요청이 일하는 동안 만들고 저장하고 덮어쓰고 지우고 이름을 바꾼 에셋이 되돌리기로 돌아오는 것, 요청 번호로 되돌릴 때 더 새 요청이 있으면 건너뛰는 것, 저장을 따라가는 모델이 요청의 일과 섞이지 않는 것도 봅니다(59개 항목). 끝으로 Unity를 한 번 더 창 없이 열어 통로를 켜고, 창 없는 Blender가 그 통로로 모델을 보내고 고쳐 저장하는 한 바퀴를 실제로 돌립니다. 저장하지 않는 새 씬에서 하고, 만든 에셋은 끝나면 지웁니다. 바꾸기 전 파일 사본이 `Library/ClaudeHand/replaced/`에 몇 개 남고, 되돌리기를 확인하며 뺀 시험용 에셋이 `Library/ClaudeHand/removed/`에 남습니다(둘 다 최근 10개만 남습니다).

`sync`에 대해 알아둘 것:
- 지금 쓰는 MCP 설정(`.mcp.json`, `~/.claude.json`, Claude Desktop 설정)을 찾아서 같은 방식으로 서버를 띄웁니다. Unity는 stdio와 HTTP 둘 다 됩니다.
- Blender는 포트 9876~9885를 확인하고, 응답한 창의 .blend 경로를 읽어(읽기만 합니다) 이름을 붙입니다. 다른 범위를 쓰려면 `--blender-ports 9900-9910`을 붙입니다.
- 창을 새로 열거나 닫았으면 다시 `sync`하세요. 닫은 창의 정의 파일까지 지우려면 `--prune`을 붙입니다.
- 저장소 폴더를 옮겼으면 다시 `sync`해야 합니다. 안전장치 경로가 정의 파일에 들어가 있어서요.

## 매일 쓰는 법

1. Unity(**Window > Claude Hand > Chat**)나 Blender(3D 뷰포트 `N` > **Claude Hand**)의 채팅창에 바로 말합니다.
   - Claude Hand가 꺼져 있으면 채팅창이 켜고, 연결되면 그 메시지를 보냅니다.
   - 바탕화면의 **Claude Hand** 아이콘이나 채팅창의 **[Claude Hand 시작]** 버튼으로 먼저 켜도 됩니다.
2. 검은 Claude Hand 창이 뜹니다. **켤 때마다** 개발용 채널 경고가 나오니 "I am using this for local development"를 고르고 Enter를 누릅니다. 직접 만든 채널에 Claude Code가 늘 묻는 보안 확인이라 claude-hand가 대신 답하지 않고, 개인 계정에서는 끌 수도 없습니다.
3. 세션이 일을 시작하면 그 창은 저절로 최소화됩니다(Windows). **닫지는 마세요.** 닫으면 채팅이 멈춥니다.

이게 전부입니다.
- 새 프로젝트나 파일을 열어도 따로 할 일이 없습니다. 처음 메시지를 보내면 그 창 담당이 자동으로 만들어집니다.
- 아이콘을 두 번 눌러도 "이미 실행 중"이라고 알려 줄 뿐 둘이 열리지 않습니다.

## Claude 데스크톱 앱에서 보고 말하기

Claude Hand는 기본으로 Claude Code의 **Remote Control**로 열립니다. 쓰는 방식은 하나입니다. **검은 Claude Hand 창이 일을 하고, Unity·Blender 채팅창과 데스크톱 앱은 같은 세션에 말하는 입구**입니다.

- **Claude 데스크톱 앱의 Code 탭**(그리고 claude.ai/code, 모바일 앱) 세션 목록에 **"Claude Hand"**가 보입니다. 거기서 보고 말할 수 있습니다.
- 백그라운드로 도는 하위 세션도 보이고, 거기서 멈출 수 있습니다.
- "선택한 거 Blender로 보내줘"처럼 말해도 됩니다. Unity·Blender가 지금 무엇이 선택돼 있는지 Claude Hand에 알려 주고 있어서, 감독이 대상을 찾습니다(`current_context`).
- 데스크톱 앱에서 시킨 일도 그 창의 채팅창에 **"데스크톱 앱에서"** 표시와 함께 남습니다. 창마다 작업 기록이 채팅창에 다 모입니다.
- Claude Hand가 꺼져 있을 때는 데스크톱 앱의 아무 Code 탭 세션에서 **"Claude Hand 켜줘"**라고 하면 됩니다. 검은 창이 뜨면 확인 질문에 답하고, 세션 목록에서 "Claude Hand"로 옮겨 가세요.

필요한 것:
- Pro·Max·Ultra·Team·Enterprise 구독의 **claude.ai 로그인**. API 키로는 안 됩니다. 로그인 방식은 Claude Hand 창에서 `/status`로 볼 수 있습니다.
- Team·Enterprise는 관리자가 Remote Control을 켜야 합니다.
- 조건이 안 맞으면(API 키, Bedrock 등) Claude Hand는 Remote Control을 빼고 켜고 그 이유를 창에 보여 줍니다. 채팅창은 그대로 됩니다.

설정:
```powershell
uv run tools/claude_hand.py remote-control off   # 데스크톱 앱에서 보기 끄기 (on으로 다시 켜기)
uv run tools/claude_hand.py minimize off         # Claude Hand 창 자동 최소화 끄기
uv run tools/claude_hand.py clean-exchange --keep-days 30   # 교환 폴더의 옮긴 파일을 며칠 둘지 (기본 30일, 0이면 지우지 않음)
```

알아둘 점:
- 데스크톱 앱에서 새로 연 Code 탭 세션은 Claude Hand가 아닙니다. Unity·Blender 채팅창과 연결되지 않고, 채팅창에 기록도 남지 않습니다. 작업은 "Claude Hand" 세션에서 하세요.
- 일반 **채팅** 탭에서는 이 스킬이 동작하지 않습니다. **Code** 탭에서만 동작합니다.

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

- 메시지에는 지금 선택한 오브젝트와 씬 정보가 함께 붙습니다. 그래서 "이거 복제해줘"처럼 말해도 됩니다. 끄는 체크박스가 있습니다.
- 다른 창이 필요한 일도 그 창에서 말하면 됩니다. 예를 들어 Unity 창에서 "Blender props.blend의 Rock 가져와"라고 하면, 감독이 Blender 담당과 Unity 담당을 함께 써서 처리하고 결과를 그 Unity 창에 알려줍니다.
- Claude가 도구를 실행하려고 허락을 구하면 채팅창에 **허용/거부** 버튼이 뜹니다.
- 일이 끝났는데 답이 오지 않으면 채널이 1분 뒤 Claude Hand에 한 번 다시 알리고, 채팅창에 그렇게 했다고 보여 줍니다.
- 한 창에서 시킨 일이 다른 창을 바꾸면(Blender 창에서 "Unity로 보내줘"), 답과 함께 바뀐 창의 **지금 모습**이 그림으로 붙습니다. Claude를 거치지 않고 채널이 직접 찍어서 토큰이 들지 않습니다. 들여다보기만 한 창의 그림은 붙지 않습니다.
- 다른 프로그램을 보는 동안 답이나 허락 요청이 오면 Unity·Blender의 작업 표시줄 단추가 깜빡이고, Unity 채팅 탭 이름에 안 읽은 수가 붙습니다.
- Unity가 "씬을 저장할까요?" 같은 대화상자에서 멈추면(Windows) 시간 초과를 기다리지 않고 바로 알아채, 대화상자의 글과 버튼을 감독과 채팅창에 알립니다. 무엇을 누를지는 사용자가 정합니다.
- 일이 끝나면 입력칸 위에 **되돌리기** 버튼이 뜹니다. 그 요청이 시작되기 직전으로 Unity Undo나 Blender 실행 취소 기록을 되돌립니다. Unity에서는 그 요청이 일하는 동안 바뀐 파일도 함께 돌립니다: 고친 스크립트와 에셋은 이전 것으로, 만든 것은 프로젝트에서 빠지고, 지운 것은 되살아납니다. 그 요청이 다른 창도 바꿨으면(예: Blender에서 시킨 "Unity로 보내줘") 요청한 채팅창의 버튼 하나로 그 창들까지 되돌립니다. 버튼에는 되돌릴 요청의 글과, 다른 창이 있으면 그 창 이름이 적혀 있습니다. 물어보기만 한 요청은 되돌릴 것이 없으니 건너뛰고, 그 앞의 바꾼 요청을 가리킵니다. "방금 거 취소해줘"라고 해도 됩니다.
- 일하는 동안에는 "작업 중" 줄 옆에 **중지** 버튼이 있습니다. 누르면 그 요청으로 일하던 하위 세션의 다음 동작부터 막히고(하던 동작 하나는 끝까지 갑니다), 감독이 어디까지 했는지 알려 줍니다. 한 것은 되돌리지 않습니다. 되돌리려면 그다음에 **되돌리기**를 누릅니다.
- 입력칸 위의 **다른 창** 줄이 채팅창이 열려 있는 다른 창들을 한 줄씩 보여 줍니다: `작업 중`(다른 창의 요청으로 일하면 "○○의 요청"), `답 기다림`, `대화상자로 멈춤`. 한가한 창은 한 줄에 모읍니다. 그 창으로 가 보지 않아도 누가 무엇을 하는지 압니다. 채널이 이미 아는 것을 보여 주는 것이라 토큰이 들지 않습니다.
- 결과 답 아래에 그 요청에 든 시간과 토큰이 한 줄로 붙습니다: `이 요청: 32초 · 동작 2번 · 토큰 출력 2.9천 · 새 입력 4.5천 · 다시 읽기 66만`. 감독과 그 요청으로 일한 창들의 것을 더한 수입니다. 답이 오고 몇 초 뒤, 감독이 그 차례를 마치면 붙습니다. Claude Code가 남기는 대화 기록에서 읽어 오는 것이라 이것 때문에 토큰이 더 들지는 않습니다. 세 수의 뜻과 지난 요청들의 합계는 아래 [요청마다 시간과 토큰이 얼마나 들었는지 보기](#요청마다-시간과-토큰이-얼마나-들었는지-보기)에 있습니다.
- Blender에서 Unity로 보낸 모델은 **저장할 때마다 따라오게** 켤 수 있습니다. 켜 두면 Blender에서 Ctrl+S를 누를 때 Unity의 그 모델이 1초쯤 뒤에 제자리에서 바뀝니다. Claude를 거치지 않아 토큰이 들지 않습니다. 아래 [저장하면 Unity의 모델이 따라오게 하기](#저장하면-unity의-모델이-따라오게-하기)에 있습니다.

준비(세팅 스킬이 대신 해 줍니다):
```powershell
uv run tools/claude_hand.py chat-setup            # 채널 등록 + 답장 도구 허용 + 채널 서버 미리 실행·점검
uv run tools/claude_hand.py blender-install-chat  # Blender 채팅 애드온 설치·활성화
uv run tools/claude_hand.py sync --allow-tools    # (선택) 하위 세션의 Unity·Blender 도구를 묻지 않고 실행
```
그다음 감독 세션을 엽니다. 바탕화면 아이콘, 채팅창의 **[Claude Hand 시작]** 버튼, `start-supervisor.cmd` 중 어느 것이든 됩니다. 아이콘은 `uv run tools/claude_hand.py shortcut`으로 만들고, 컴퓨터를 켤 때 자동으로 켜려면 `--startup`을 붙입니다.

`chat-setup`이 하는 일:
- 이 폴더에 `.mcp.json`(채널 등록)과 `.claude/settings.local.json`(답장·현재 상황 도구 `mcp__claude-hand-chat__reply`, `mcp__claude-hand-chat__current_context` 자동 허용, 감독이 Unity·Blender를 직접 조작하는 도구 막기)을 만듭니다. 기존 내용은 유지하고, 둘 다 git에 올라가지 않습니다.
- 채널 서버를 한 번 미리 실행해서 점검합니다(`--check`). Claude Code는 MCP 서버가 30초 안에 떠야 하는데, 처음에는 uv가 Python을 내려받느라 늦을 수 있어서 미리 받아 둡니다. `start-supervisor`는 여유를 두려고 `MCP_TIMEOUT=60000`도 설정합니다.

알아둘 점:
- Claude Code의 **Channels는 아직 리서치 프리뷰**입니다. 직접 만든 채널이라 `--dangerously-load-development-channels`로 켜야 하고, 처음에 개발용 채널 경고와 `.mcp.json` 서버 승인 창이 뜹니다.
- 채팅창의 **허용/거부 버튼은 Claude Code 2.1.234 이상**에서 동작합니다. Channels는 claude.ai 로그인이나 Console API 키가 필요하고, Bedrock·Vertex·Foundry에서는 쓸 수 없습니다. 회사 계정(Team/Enterprise)은 관리자가 Channels를 켜야 합니다. `doctor`가 버전과 환경을 확인합니다.
- **채팅을 받는 건 `start-supervisor`로 연 세션뿐입니다.** 이 폴더에서 그냥 연 Claude Code도 채널 서버를 띄우지만 채팅은 받지 않습니다. 채널 없는 세션이 채팅을 가로채지 않게 하기 위해서입니다. 감독 세션은 하나만 여세요.
- 채팅창과 채널은 내 PC 안(127.0.0.1)에서만, 토큰을 아는 프로그램끼리만 통신합니다. 토큰은 `%USERPROFILE%\.claude-hand\chat\server.json`에 있습니다. 방화벽 규칙은 필요 없고, 방화벽 창이 떠도 허용하지 않아도 됩니다.

### 채팅창에 "감독 세션" 관련 메시지가 뜰 때

```powershell
uv run tools/claude_hand.py chat-status
```
지금 상태와 고치는 방법을 알려줍니다. 흔한 원인은 이렇습니다.
- 감독 세션을 `start-supervisor.cmd`가 아닌 방법(데스크톱 앱, 그냥 `claude`)으로 열었다. 채널 서버가 대기 모드로 뜨고, 채팅창에 그 이유가 보입니다.
- 이전 버전의 등록이 남아 있다 → 아래 **새 버전 받기**를 하세요.
- `.mcp.json` 서버 승인 대기나 거절 → `/mcp`에서 승인하세요. 거절했었다면 `claude mcp reset-project-choices`를 하세요.

채널 서버는 다음 경우에만 채팅을 받습니다.
- `start-supervisor`가 넘긴 표시(`CLAUDE_HAND_SUPERVISOR=1`, `.mcp.json`의 `env`로 전달)가 있을 때
- 자기를 띄운 Claude Code가 이 서버를 채널로 불러왔을 때(`--dangerously-load-development-channels server:claude-hand-chat`)

### 지금 창들이 무엇을 하고 있는지 보기

```powershell
uv run tools/claude_hand.py status
```
Claude Hand가 켜져 있는 동안, 채팅창이 열려 있거나 일하고 있는 창을 한 줄씩 보여 줍니다. 채팅창의 **다른 창** 줄과 같은 내용에 경과 시간과 지금 하는 동작이 붙습니다.
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
채팅창에서 보낸 요청마다 번호가 붙고, 그 뒤의 일이 `%USERPROFILE%\.claude-hand\logs\<날짜>.jsonl`에 한 줄씩 남습니다: 감독이 어느 창에 일을 맡겼는지(`맡김`), 하위 세션의 도구 호출, 안전장치가 막은 호출과 이유, `ch_*` 호출의 성공 여부와 걸린 시간, 중지를 누른 때, 하위 세션이 멈춘 때, 답. 다른 창이 한 일도 그 일을 시킨 채팅창의 요청 번호 아래에 남습니다. "왜 답이 늦었지", "어디서 막혔지"를 뒤에서 볼 때 씁니다. 요청의 마지막 줄(`시간·토큰`)에는 감독과 창마다 일한 시간, API를 부른 횟수, 토큰이 따로 나옵니다. 메시지와 답은 앞 200자만 남고, 2주가 지난 파일은 켤 때 지워집니다. 데스크톱 앱에서 시킨 일은 요청 번호 없이 남아 `trace` 목록에는 나오지 않습니다.

### 요청마다 시간과 토큰이 얼마나 들었는지 보기

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
채팅창의 "이 요청" 줄과 같은 것을 요청 기록에서 읽어 보여 줍니다. Claude Hand가 꺼져 있어도 됩니다.
- **시간**은 채팅창에서 보낸 때부터 결과 답이 온 때까지입니다. **동작**은 하위 세션이 Unity·Blender 도구를 부른 횟수입니다.
- **출력**은 Claude가 써낸 토큰, **새 입력**은 새로 읽은 토큰(캐시에 새로 쓴 것 포함), **다시 읽기**는 캐시에서 다시 읽은 토큰입니다. 다시 읽기가 대부분이고 훨씬 쌉니다. 그래서 합치지 않고 따로 보여 줍니다. 한참 쉬었다가 보낸 요청은 캐시가 식어서 대화를 새로 읽으므로 `새 입력`이 수십 배로 뜁니다. 비용을 줄일 때 볼 곳이 거기입니다.
- 달러로는 바꿔 보여 주지 않습니다(요금제마다 뜻이 다릅니다).
- 채팅창의 줄을 끄려면 `%USERPROFILE%\.claude-hand\config.json`에 `"show_cost": false`를 넣고 Claude Hand를 다시 켭니다. 기록과 `usage`에는 그대로 남습니다.

### 저장하면 Unity의 모델이 따라오게 하기

모델을 Blender에서 고쳐 가며 Unity에서 볼 때, 고칠 때마다 "다시 보내줘"라고 하지 않아도 됩니다.

1. 한 번은 평소처럼 보냅니다("의자를 Unity로 보내줘"). 또는 Blender의 Claude Hand 패널 아래 **Unity로 보낸 모델** 칸에서 오브젝트를 고르고 **[선택한 오브젝트 보내기 → 프로젝트 이름]**을 누릅니다. 이 버튼은 Claude 없이 바로 보내고, 따라가기도 함께 켭니다.
2. 따라가게 켭니다. 같은 칸에서 그 모델의 체크를 켜거나, "의자는 저장할 때마다 Unity에 반영해줘"라고 말합니다.
3. 그 뒤로는 Blender에서 **저장할 때마다**(Ctrl+S) Unity의 모델이 제자리에서 바뀝니다. 씬과 프리팹에 놓인 것이 그 자리에서 새 모양이 됩니다.

- Blender가 Unity에 직접 보냅니다. 감독도 하위 세션도 돌지 않아서 토큰이 들지 않고, Claude Hand가 꺼져 있어도 됩니다. Unity가 열려 있어야 하고, 뒤에 있어도 됩니다.
- **바뀐 것이 없으면 보내지 않습니다.** 다른 오브젝트만 고치고 저장하면 Unity는 그대로입니다.
- 바뀌었을 때만 두 채팅창에 한 줄이 남습니다(`저장 → hand_test: Chair 반영`). 저장이 이어지면 줄이 늘지 않고 그 줄이 `3번째`로 바뀝니다.
- 따라가는지는 Unity 쪽 모델에 적힙니다(모델의 `.meta`). 모델을 지우면 함께 사라지고, `.blend`를 남에게 줘도 그쪽 Unity로 가지 않습니다.
- 오브젝트 이름을 바꿔도 따라갑니다. 보낸 오브젝트에 보낸 이름이 사용자 속성(`claude_hand_sent_as`)으로 남기 때문입니다. 그 오브젝트를 복제해 둘 다 이름을 바꾸면 어느 쪽인지 알 수 없어서, 패널이 묻습니다(**[선택한 오브젝트로 잇기]**).
- **다른 이름으로 저장**해도 따라갑니다(사본 저장은 아닙니다: 작업 파일이 그대로이므로). 탐색기에서 파일을 옮기거나 이름을 바꿨으면 패널에 "다른 파일에서 온 것으로 적혀 있음"으로 나오고, **[이 파일로 잇기]**를 누르면 됩니다.
- Unity가 닫혀 있는 동안 저장한 것은 Unity를 열었을 때, 또는 그 `.blend`를 다시 열었을 때 한 번 맞춰집니다.
- 이것은 요청이 아니라서 채팅창의 **되돌리기** 대상이 아닙니다. Unity의 모델은 마지막으로 저장된 `.blend`를 따릅니다. Blender에서 **되돌리기**를 누르면 되돌린 모양이 Unity로 다시 갑니다. 패널의 보내기 버튼으로 처음 보낸 모델은 Unity 채팅창의 **되돌리기**로 뺄 수 있습니다.
- 끄려면 체크를 끄거나 "의자는 이제 따라오지 않게 해줘"라고 합니다.

### 건드리면 안 되는 것 정해 두기 (보호 규칙)

"이건 바꾸지 마"를 창마다 정해 둘 수 있습니다. 메모(`NOTES.md`)는 Claude가 읽고 지키려고 할 뿐이지만, 보호 규칙은 **기계가 막거나 되돌립니다.**

- Unity 채팅창과 Blender 패널의 **보호** 칸에서 정합니다.
  - **[선택한 것 보호]**: 고른 오브젝트(와 그 아래 전부), Unity에서는 Project 창에서 고른 에셋 · 폴더도.
  - **[이 창 보기만]**: Claude가 이 창을 읽기만 합니다. **[이 창 잠금]**: 이 창에는 일을 맡기지도 않습니다.
  - **동작 금지**: Unity의 씬 저장 · 에셋 삭제 · 플레이 모드, Blender의 .blend 저장.
  - **이번만**을 켜고 더하면 Claude Hand를 끌 때까지만 둡니다. 끄고 더하면 프로젝트(`ClaudeHand/protect.json`)나 .blend 옆(`<이름>.claude-hand.protect.json`)에 적혀 계속 남습니다.
- 말로 해도 됩니다: "Hero는 건드리지 마", "Assets/Art는 손대지 마", "이 창은 보기만 해". Claude가 규칙을 **제안**하고, 채팅창에 뜬 **[추가]**를 눌러야 규칙이 됩니다.
- **푸는 것은 사용자만 합니다.** 보호 칸에서 그 줄의 **[풀기]**를 누릅니다. Claude에게는 규칙을 고치거나 푸는 도구가 없습니다.
- 규칙에 걸리면:
  - 호출의 글로 알 수 있는 것은 **실행 전에 막힙니다**(보호한 에셋을 고치는 도구, 보호한 오브젝트를 가리키는 호출, 금지한 동작, 보기만인 창의 바꾸는 호출).
  - Unity에서는 보호한 에셋의 저장 · 삭제 · 이동을 **Unity가 거부합니다.** 어떤 코드로 하든 걸립니다.
  - 그래도 보호한 것이 바뀌었으면 호출 직후에 알아채 **그 요청이 그 창에 한 일을 통째로 되돌립니다.**
  - 채팅창에 한 줄이 뜹니다: `보호 규칙이 막았어요: …` 또는 `보호된 Hero (자리)이(가) 바뀌어 이 요청이 이 창에 한 일을 되돌렸어요`.
- 보호한 에셋을 **쓰는 것**은 됩니다(보호한 프리팹을 씬에 놓기, 보호한 머티리얼을 다른 오브젝트에 입히기). 막는 것은 그 에셋 자체를 바꾸는 일입니다.
- 상태는 색이 아니라 글로 보입니다(보호 칸 제목의 `[잠김]` · `[열림]`, Blender는 자물쇠 모양).

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
- "Hero는 저장할 때마다 MyGame에 반영해줘"
  - unity-mygame이 그 모델의 따라가기를 켭니다. 그 뒤로는 Blender에서 저장하면 Claude 없이 바로 바뀝니다.
- (Blender에서 Hero를 고친 뒤) "Hero 다시 보내줘"
  - blender-character가 다시 내보내고, unity-mygame이 `ch_import_model`로 받습니다. 같은 .blend의 같은 오브젝트라 새로 넣지 않고 있던 모델을 바꿉니다. 씬에 놓아 둔 Hero들이 그 자리에서 새 모양이 됩니다.
- "MyGame 씬의 Rock_A를 character.blend로 보내줘"
  - unity-mygame이 `ch_export_model`로 내보내고, blender-character가 `import_model`로 가져옵니다.

## 새 버전 받기 (이미 설치한 경우)

```powershell
git pull
uv run tools/claude_hand.py update     # 하위 세션·감독 스킬·채팅 설정·Blender 애드온을 새 버전으로 (--dry-run으로 미리 보기)
```
- 그다음 감독 세션을 `start-supervisor.cmd`로 다시 엽니다.
- 세팅 스킬로 할 때는 **`git pull` 뒤에 Claude Code를 다시 시작한 다음** "세팅 이어서 해줘"라고 하세요. 스킬 내용은 세션을 시작할 때 읽혀서, 받기 전에 연 세션은 옛 순서를 따릅니다.
- `update`는 이전 버전이 남긴 등록도 정리합니다. 이전 `chat-setup`이 local 범위로 등록한 채널은 새 `.mcp.json`보다 먼저 쓰여서 채팅을 막습니다.
- Blender는 다시 열어야 새 애드온을 씁니다. Claude Hand Tools를 폴더 경로로 넣은 Unity 프로젝트는 다시 열거나 그 창으로 돌아가면 새 패키지를 컴파일합니다.
- 받은 버전에서 무엇이 바뀌었는지는 [버전별 변경 내용](#버전별-변경-내용)에 있습니다.

## 들어 있는 것

| 위치 | 내용 |
|---|---|
| `.claude/skills/claude-hand-setup/` | 처음 세팅 스킬입니다. 이 폴더에서 Claude Code를 열면 바로 쓸 수 있습니다. |
| `skills/orchestrator/` | 평소에 감독 세션이 따르는 규칙(스킬)입니다. `sync`가 `~/.claude/skills/claude-hand/`에 설치합니다. |
| `tools/claude_hand.py` | 하위 세션 생성(`list`, `sync`), 세팅 도우미(`doctor`, `selftest`, `update`, `mode`, `unity-install`, `unity-open`, `blender-open`, `chat-setup`, `blender-install-chat`, `chat-status`, `shortcut`, `remote-control`), 창들의 현황 보기(`status`), 요청 기록 보기(`trace`), 요청마다 든 시간·토큰 보기(`usage`), 교환 폴더 정리(`clean-exchange`)가 들어 있습니다. |
| `tools/guard.py` | 하위 세션의 안전장치(PreToolUse 훅)입니다. 다른 대상으로 가는 호출을 막습니다. 통과시킨 호출은 채널에 알려 채팅창의 작업 줄에 보이게 하고, 요청에서 무언가를 바꿀 수 있는 첫 동작 앞에 되돌리기 지점을 찍습니다. Unity 호출이면 그 호출이 이름 붙인 파일을 미리 복사해 둡니다(되돌리기가 스크립트까지 돌리게). 표준 라이브러리만 쓰는 작은 파일로 따로 두어, 다른 파일의 실수가 안전장치를 멈추지 못하게 했습니다. |
| `tools/supervisor_hook.py` | 감독 세션의 훅입니다. 감독이 하위 세션에게 일을 맡길 때마다 그 일이 어느 채팅창 요청의 것인지(`[요청 <번호>]`) 채널에 알립니다. 번호가 빠진 맡기기는 거부하고 적는 법을 알려 줍니다. 아직 다른 요청으로 일하는 창에 맡기는 것도 거부합니다. `start-supervisor`로 연 세션에만 달립니다. |
| `tools/handlib/` | 위 스크립트들을 이루는 모듈입니다: 열린 창 찾기(`discovery`), 하위 세션 정의 만들기(`agents`), 채팅 설정(`chatsetup`), 시작 준비(`launch`), 진단(`checks`), 요청 기록(`eventlog`), 창들의 현황(`overview`), Blender에서 돌릴 코드가 읽기만 하는지 읽어 보기(`looks`), Unity 호출이 바꿀 파일을 미리 보관하기(`keeps`), 요청마다 든 토큰을 Claude Code의 대화 기록에서 읽기(`usage`) 등. 목록은 `tools/handlib/__init__.py`에 있습니다. |
| `prompts/` | Claude에게 주는 지침의 원문입니다. 하위 세션용(`unity-subagent.md`, `blender-subagent.md`)에는 `{{이름}}` 자리에 창마다의 값이 들어갑니다. `chat-rules.md`는 감독이 채팅창을 다루는 규칙으로, 채널 서버와 감독 스킬이 같은 글을 씁니다. 고친 뒤에는 `update`로 다시 설치하고 Claude Hand를 다시 켭니다. |
| `tools/chat_channel.py` | 채팅 채널 서버입니다. Claude Code가 실행하는 MCP 서버이고, Unity·Blender 채팅창과는 로컬 HTTP로 통신합니다. |
| `unity/com.claudehand.tools/` | Unity 패키지입니다. `ch_*` 도구 10개와 채팅창(Window > Claude Hand > Chat)을 추가합니다. `ch_*` 도구는 이 패키지가 여는 자기 통로로 불립니다. MCP for Unity 없이도 컴파일되고, 그 버전에 상관없이 동작합니다. |
| `tools/unity_bridge.py` | 하위 세션이 그 통로로 `ch_*` 도구를 부를 때 쓰는 MCP 서버입니다. `--list`로 통로를 연 Unity를 볼 수 있습니다. |
| `blender/claude_hand_chat.py` | Blender 채팅 패널 애드온입니다. |
| `start-supervisor.cmd` / `.sh` | 감독 세션을 엽니다. 바탕화면 아이콘과 채팅창 버튼이 이 파일을 실행합니다. 열 때마다 `preflight`가 하위 세션과 설정을 맞추고, 이미 켜져 있으면 알려 줍니다. |
| `docs/` | [설계와 그 이유](docs/DESIGN.md), [PC 검증 순서](docs/TEST-PLAN.md), 검증 결과([12단계](docs/TEST-RESULTS-12-WINDOWS.md), [13단계부터](docs/TEST-RESULTS-13-WINDOWS.md)), [구조 점검과 그 뒤의 기록](docs/REVIEW-2026-11-10.md) |

### 추가되는 Unity 도구

| 도구 | 하는 일 |
|---|---|
| `ch_editor_status` | 연결된 프로젝트, 포커스 여부, 컴파일 중인지를 알려줍니다. 아무것도 바꾸지 않습니다. |
| `ch_scene_snapshot` | 씬 구조를 JSON으로 돌려주고, 오브젝트마다 `@s1a2b3c4:e5` 형태의 ref를 붙입니다. 씬이 바뀐 뒤 옛 ref를 쓰면 `STALE_REF`가 납니다. |
| `ch_export_objects` | 씬 오브젝트(ref)나 에셋을 의존 에셋과 함께 `.unitypackage`로 묶어 교환 폴더에 둡니다. 원본 씬은 바뀌지 않습니다. |
| `ch_import_package` | 위 패키지를 가져와 씬에 배치합니다. `dry_run=true`로 덮어쓸 에셋을 먼저 보여주고, 덮어쓸 게 있으면 허락 없이는 진행하지 않습니다. |
| `ch_import_model` | Blender가 내보낸 FBX(또는 OBJ, GLB)를 `Assets/ClaudeHand/Imports/<이름>/` 아래로 가져와 씬에 배치합니다. **같은 .blend의 같은 오브젝트가 다시 오면 새로 넣지 않고 있던 모델 파일을 제자리에서 바꿉니다.** 씬과 프리팹에 놓인 것이 그 자리에서 새 모양이 됩니다. 바꾸기 전 파일은 `Library/ClaudeHand/replaced/`에 남습니다. `dry_run=true`로 어느 쪽이 될지 먼저 볼 수 있고, `new_copy=true`면 따로 한 벌을 넣습니다. `materials="project"`면 프로젝트에 같은 이름의 머티리얼이 하나 있을 때 그것으로 연결합니다. |
| `ch_model_follow` | Blender에서 온 모델이 그 `.blend`의 저장을 따라가게 합니다. `list`는 이 프로젝트가 Blender에서 받은 모델과 따라가는지를 보여 주고, `set`은 켜고 끕니다. 켜 두면 Blender의 Claude Hand 패널이 저장 때 스스로 다시 보내고(`update`), 이 도구가 제자리에서 바꿉니다. 이미 있는 모델만 바꾸고, 같은 파일이면 아무것도 하지 않고, 씬에 놓지 않고, 되돌리기 지점에 적지 않습니다. |
| `ch_export_model` | 씬 오브젝트(ref)나 모델 에셋을 Blender(또는 다른 Unity 프로젝트)가 받을 모델 파일로 교환 폴더에 둡니다. 모델 파일(FBX, OBJ, GLB)에서 온 오브젝트는 그 파일을 가져올 때 모습 그대로 보냅니다. Unity에서 만든 메시는 프로젝트에 FBX Exporter 패키지(`com.unity.formats.fbx`)가 있을 때 FBX로 씁니다. 프로젝트는 바뀌지 않습니다. |
| `ch_apply_settings` | Interaction Mode를 **No Throttling**으로 바꿉니다. Unity가 뒤에 있어도 명령을 처리하게 하는 설정입니다. 보기(`show`), 적용(`apply`), 되돌리기(`revert`)를 할 수 있습니다. 메뉴 **Tools > Claude Hand**에도 있습니다. |
| `ch_checkpoint` | 요청마다 되돌리기 지점을 둡니다. 하위 세션이 그 요청에서 무언가를 바꾸는 첫 동작을 하기 직전에 저절로 찍히고(요청마다, 창마다 하나), 채팅창의 **되돌리기** 버튼이나 `action=undo`로 그 지점 이후의 변경을 Unity Undo로 되돌립니다. 읽기만 한 요청처럼 그 뒤로 바뀐 것이 없는 지점은 건너뜁니다. 그 요청이 일하는 동안 파일에 생긴 일도 함께 돌립니다. 어떤 도구로 했든 같습니다: 고친 파일(스크립트, 머티리얼, 프리팹, 모델)은 이전 것으로 돌아가고, 만들거나 가져온 에셋은 프로젝트에서 빠지고(지우지 않고 `Library/ClaudeHand/removed`에 보관), 지운 에셋은 되살아나고, 옮기거나 이름을 바꾼 에셋은 원래 자리로 갑니다. 씬 쪽은 Undo에 남는 변경만 되돌아갑니다. |
| `ch_capture` | 씬 뷰를 지금 모습 그대로 PNG로 저장합니다(기즈모 없이). 씬은 바꾸지 않습니다. 채팅창의 결과 미리보기에 씁니다. |

명령줄에서도 부를 수 있습니다. 예: `uv run tools/claude_hand.py unity-call MyGame ch_editor_status`

모든 오류에는 `retry` 값이 붙습니다.
- `safe`: 그대로 다시 호출해도 됩니다.
- `after_fix`: 인자를 고친 뒤 다시 호출합니다.
- `check_first`: 이미 반영됐을 수 있으니 상태부터 확인합니다.

### Blender 쪽

Blender 쪽 일은 MCP for Blender의 Python 실행 도구(`execute_code`)로 합니다. 옮기기와 되돌리기는 Claude Hand Chat 애드온(`blender-install-chat`으로 설치)의 함수이고, 하위 세션은 그것을 한 줄로 부릅니다.
- `export_blend`: 다른 Blender로 보낼 `.blend`를 씁니다
- `import_blend`: 다른 Blender가 보낸 것을 Append로 가져옵니다
- `export_fbx`: Unity로 보낼 FBX를 씁니다. 저장된 .blend의 같은 오브젝트를 다시 보내면 Unity가 있던 모델을 제자리에서 바꿉니다(이름을 바꾼 뒤에도). 선택과 편집 모드는 건드리지 않습니다
- `import_model`: Unity가 `ch_export_model`로 보낸 모델(FBX, OBJ, GLB)을 가져옵니다
- `mark_undo_point`, `undo_last_request`: 요청 단위 되돌리기
- `undo_request`: 요청 번호로 되돌리기. 그 요청의 지점이 가장 새 것일 때만 되돌립니다(채널이 여러 창을 한 번에 되돌릴 때 창마다 부릅니다)

애드온은 하위 세션이 부르지 않는 일도 하나 합니다. 저장을 따라가게 켠 모델을, 파일이 저장될 때 Unity의 통로로 직접 다시 보냅니다(위 [저장하면 Unity의 모델이 따라오게 하기](#저장하면-unity의-모델이-따라오게-하기)).

되돌리기 지점은 요청에서 무언가를 바꿀 수 있는 첫 동작 앞에 찍힙니다. `execute_code`는 아무 코드나 돌리므로, 안전장치가 그 코드를 읽어 보고 읽기만 하는 것이 분명할 때(속성 읽기, `print`, 직접 만든 리스트 채우기)만 지점 없이 지나가게 합니다.

### agent-desktop은 어디에 쓰나

- **처음 세팅:** 명령어로 할 수 없는 클릭을 대신합니다. 예를 들어 Unity가 패키지를 받는 동안 뜨는 대화상자, 이미 열린 Blender의 포트 변경입니다.
- **평소:** Unity나 Blender가 대화상자 때문에 멈추면 그 내용을 읽어 사용자에게 보여주고, 고른 버튼을 눌러 줍니다.
- **한계:**
  - **최소화된 창은 조작하지 못합니다.** 먼저 복원해야 합니다. 다른 창 뒤에 가려진 창은 백그라운드로 조작할 수 있습니다.
  - Unity·Blender의 내부 패널은 접근성 정보가 거의 없어서 읽히지 않을 수 있습니다. 그때는 허락을 받은 뒤 화면을 캡처하고 좌표로 클릭합니다. 이 동안에는 마우스와 키보드가 실제로 움직입니다.
  - 그래서 창이 최소화된 상태에서의 실제 작업(씬 수정, 모델 옮기기)은 agent-desktop이 아니라 MCP가 맡습니다.

## 안전장치

- 하위 세션은 자기 전용 MCP 서버만 씁니다. 정의 파일의 `tools` 허용 목록에 그 서버만 들어 있습니다.
  - Unity 서버는 `--default-instance <Name@hash>`로 고정됩니다.
  - Blender 서버는 `--port <포트>`로 고정됩니다. Blender는 서버 하나가 창 하나에만 붙기 때문에, 이것만으로도 다른 창에 닿지 않습니다.
- 안전장치 훅(`tools/guard.py`)은 Unity 호출을 하나하나 검사합니다. 다른 프로젝트를 가리키는 `unity_instance`와 대상 변경(`set_active_instance`)을 막습니다. `batch_execute` 안에 들어 있는 명령도 검사합니다.
- 같은 훅이 호출을 그 창의 **보호 규칙**과 견줍니다(위 [건드리면 안 되는 것 정해 두기](#건드리면-안-되는-것-정해-두기-보호-규칙)). 호출이 끝난 뒤에도 한 번 더 돌아, 창이 막았거나 되돌린 것을 하위 세션에게 알립니다.
- 훅 안에서 무엇이 잘못되든 호출을 막습니다(잘못된 인자, 읽을 수 없는 입력, 판단 중 오류, 확인할 수 없는 보호 규칙).
- 훅이 시작조차 못 하면 Claude Code는 호출을 막지 않습니다. 그래서 `sync`가 훅 명령에 uv의 전체 경로를 적고, `selftest`가 설치된 훅을 Claude Code와 같은 방법으로 실제로 돌려 봅니다.
- 세팅 스킬은 허락받은 단계만 진행합니다. 화면 조작 중 마우스와 키보드를 실제로 움직여야 할 때는 따로 다시 묻습니다.

## 알려진 한계

- 작업 표시줄 알림, 한글 입력창, 대화상자 감지, Claude Hand 창 자동 최소화는 Windows에서만 됩니다. macOS·Linux에서는 실제 창으로 확인하지 않았습니다.
- 개발 중인 체크아웃에서 `unity-install`을 하면 Claude Hand Tools가 이 PC의 폴더 경로(`file:`)로 들어가서, 그 프로젝트는 다른 PC에서 열리지 않습니다. 릴리스 태그(예: `v0.9.0`)를 받은 체크아웃에서는 GitHub 주소로 들어갑니다. `unity-install --dry-run`이 어느 쪽인지 알려 줍니다.
- 내보내는 스크립트가 asmdef에 속해 있어도 asmdef는 같이 가지 않습니다. 받는 쪽에서는 기본 어셈블리로 컴파일됩니다.
- `Packages/` 안의 에셋(URP 셰이더 등)은 `.unitypackage`에 들어가지 않습니다. 받는 프로젝트에 같은 패키지가 있어야 하고, dry run의 `missing_packages`에 표시됩니다.
- 같은 프로젝트나 같은 Blender 창에 하위 세션 둘을 동시에 붙이면 안 됩니다. 명령이 하나씩 처리되기 때문입니다. 감독 스킬에 이 규칙이 들어 있고, Claude Hand가 켜져 있는 동안에는 안전장치가 나중에 온 하위 세션의 호출을 막습니다.
- Blender → Unity로 가는 것(0.17.0부터): 메시, UV 두 벌, 버텍스 컬러, 리그와 가중치, 클립, 셰이프 키, 라이트, 카메라, 지오메트리 노드 인스턴스(메시로), 그리고 **Principled BSDF 재질**입니다(값이나 이미지를 곧바로 읽는 입력은 그대로, 다른 노드를 거치는 입력은 텍스처로 구워서). 구운 것은 그때의 모습으로 굳은 이미지이고, 보는 각도나 시간에 따라 달라지는 노드 효과는 담기지 않습니다. Principled BSDF가 아닌 셰이더, 노드를 거친 투명도는 아직 옮기지 못하고, 받을 때 무엇이 빠졌는지 알려 줍니다. 한 재질을 UV가 서로 겹치는 여러 오브젝트가 함께 쓰면 구운 이미지가 겹쳐 그려집니다. 천 · 소프트 보디처럼 꼭짓점이 움직이는 시뮬레이션, 파티클, 헤어, 그리스 펜슬, 월드도 아직 보내지 않습니다. 재질은 URP와 렌더 파이프라인 없는 프로젝트에서만 만들어지고(HDRP는 Unity가 만든 기본 재질 그대로), 다시 보낸 모델을 되돌리기로 되돌릴 때 `Materials/`와 `Textures/`의 파일은 되돌아가지 않습니다. 프로젝트에 같은 이름의 머티리얼을 만들어 두고 `materials="project"`로 받는 방법도 그대로 됩니다.
- 다시 보낸 모델을 제자리에서 바꾸는 것은 **저장된 .blend**에서 **같은 오브젝트**를 보냈을 때 됩니다. 0.16.0부터는 오브젝트 이름을 바꿨거나 Blender에서 다른 이름으로 저장한 뒤에도 됩니다(그 전에 보낸 오브젝트는 한 번 다시 보낸 뒤부터). 탐색기에서 .blend를 옮겼으면 Blender 패널의 **[이 파일로 잇기]**를 눌러야 하고, 누르지 않으면 새 모델로 들어옵니다. 모델 파일을 바꾼 것은 그 요청의 **되돌리기**로 돌아갑니다(바꾸기 전 파일이 다시 놓입니다). 바꾸기 전 파일은 `Library/ClaudeHand/replaced/`에 최근 요청 10개까지 있어서, 그보다 오래된 것은 되돌리기로도 돌아오지 않습니다.
- **되돌리기**가 파일까지 돌리는 것은 그 요청이 **일하는 동안** 바뀐 것입니다. 하위 세션이 끝난 뒤에 직접 만들거나 고친 파일은 건드리지 않습니다(씬은 다릅니다: 그 뒤에 직접 고친 것도 함께 되돌아갑니다). 돌아가지 않는 파일은 셋입니다. 열려 있는 씬의 파일(다시 저장해야 바뀝니다), `Assets` 밖의 파일(`Packages`, `ProjectSettings`), 하위 세션이 코드 실행으로 직접 덮어쓴 파일. 마지막 것은 이전 내용이 남아 있지 않아서, 되돌릴 때 그 파일 이름을 알려 줍니다. 64 MB가 넘는 파일도 미리 복사하지 않아 같은 식으로 알려 줍니다.
- 요청이 일하는 동안 Unity에서 지운 에셋은 휴지통으로 가지 않고 그 프로젝트의 `Library/ClaudeHand/removed/req_<요청>/`으로 갑니다(그 요청을 되돌리면 돌아옵니다). 그동안 Project 창에서 직접 지운 것도 같습니다. Windows 휴지통을 쓰지 않는 이유는, 휴지통이 "바로 삭제"로 설정돼 있으면 그대로 사라지기 때문입니다. 삭제를 이렇게 넘겨받는 것은 Unity 6.3에서만 확인했습니다.
- 여러 창을 한 번에 되돌리는 것은 **채팅창에서 시킨 요청**만 됩니다(Claude Hand가 켜져 있어야 하고, 데스크톱 앱이나 터미널에서 시킨 일은 전처럼 창마다 되돌립니다). 창마다 그 요청이 가장 새 변경일 때만 되돌립니다. 그 뒤에 다른 요청이 그 창을 바꿨거나 그 창이 작업 중이면 그 창은 건너뛰고 채팅창에 그렇게 알려 줍니다. 그 창이 풀린 뒤 같은 버튼을 다시 누르면 남은 창만 되돌립니다. 한 창이 실패해도 다른 창은 되돌아갑니다(모두 아니면 전무가 아닙니다).
- 되돌리기로 빠진 에셋은 Unity의 다시 실행(Ctrl+Y)으로 돌아오지 않습니다. 필요하면 `Library/ClaudeHand/removed/undo_<빠진 때>/`에 있는 것을 `.meta`와 함께 `Assets` 아래 원래 자리로 옮기면 됩니다(`removed`에는 최근 10번 치만 남습니다).
- 물어보기만 한 요청을 **되돌리기**가 건너뛰는 방법은 두 앱이 다릅니다. Unity는 지점을 찍은 뒤에 바뀐 것이 있는지 봅니다. Undo를 거치지 않은 변경은 "바뀐 것 없음"으로 보는데, 그런 변경은 되돌리기로도 돌아가지 않습니다. Blender는 돌리기 전에 코드를 읽습니다. 읽기만 하는 코드인데도 알아보지 못하면(예: `bpy.ops`를 언급한 조회) 되돌릴 것이 없는 지점이 남아, 되돌리기를 처음 눌렀을 때 아무 일도 없습니다. 한 번 더 누르면 앞 요청이 돌아갑니다.
- Unity → Blender에서 모델 파일에서 온 오브젝트는 그 파일이 그대로 갑니다. 씬에서 바꾼 것(머티리얼 교체, 덧붙인 오브젝트, 위치)은 가지 않습니다. Unity에서 만든 메시는 FBX Exporter 패키지가 있어야 하고, 이 경로는 그 패키지의 소스를 보고 만들었을 뿐 실제로 돌려 보지는 않았습니다.
- **중지** 버튼은 하위 세션의 다음 Unity·Blender 호출부터 막습니다. 이미 시작한 동작 하나(긴 가져오기 등)는 끝까지 갑니다. 중지는 그 요청에 답이 오면 풀리고, 답이 없어도 3분 뒤에는 풀립니다. 그 사이에 데스크톱 앱에서 다른 창에 시킨 일이 있으면 그 일도 함께 막힐 수 있습니다.
- Blender 하위 세션이 처음 연결을 확인할 때와 `sync`·`doctor`가 파일 이름을 읽을 때, Blender 안에서 읽기 전용 Python(`print(bpy.data.filepath)`)이 실행됩니다.
- 요청마다 든 토큰은 Claude Code가 남기는 대화 기록 파일에서 읽습니다. 문서에 없는 내부 형식이라(2.1.322에서 확인) Claude Code가 바뀌면 줄에 토큰이 빠지고 시간과 동작만 나올 수 있습니다. `selftest`의 "감독 세션 기록에서 토큰 읽기" 항목이 그것을 알려 줍니다. 그 밖에: 줄이 나온 뒤에 그 요청으로 더 쓴 토큰은 들어가지 않습니다. 감독이 한 차례에 두 요청의 일을 이어 하면 감독의 토큰은 그 차례를 시작시킨 요청 쪽으로 갑니다. 데스크톱 앱이나 터미널에서 시킨 일은 요청별로 나오지 않습니다. 채팅창의 줄은 Claude Hand를 다시 켜면 채팅창에서는 사라지고 기록(`trace`, `usage`)에 2주 동안 남습니다.
- 저장을 따라가는 모델: Unity가 열려 있을 때만 갑니다. Bevel 모디파이어가 붙은 메시는 Blender가 계산할 때마다 UV를 조금씩 다르게 내놓아서, 파일만으로는 바뀌었는지 알 수 없습니다. 그런 모델은 Blender가 "건드렸다"고 알려 줄 때만 보내고, `.blend`를 연 뒤 첫 번에는 한 번 보냅니다. 저장할 때마다 따라가는 모델을 내보내므로(면 8만 개에 0.1초쯤) 아주 무거운 모델은 저장이 그만큼 길어집니다. 내보내기가 1초를 넘는 모델은 건드렸을 때만 내보냅니다. 요청 기록(`trace`)에는 남지 않습니다. 열린 Blender 화면에서 편집 모드로 저장한 뒤의 실행 취소가 평소와 같은지는 아직 눈으로 확인하지 않았습니다.

- 보호 규칙은 일부러 숨긴 코드를 막는 자물쇠가 아닙니다. 하위 세션이 실수로, 또는 메모를 못 봐서 건드리는 것을 막습니다. 그 밖에: 요청이 일하는 동안에는 사용자가 직접 한 저장 · 삭제 · 플레이도 규칙에 걸리면 막히고, 호출이 도는 사이에 보호 오브젝트를 직접 고치면 Claude의 변경으로 보고 되돌립니다. Unity에서 Undo를 거치지 않은 변경은 알아채지만 되돌리지 못하고 그렇게 알려 줍니다. Blender의 `.blend 저장` 금지는 코드에 저장 호출이 보일 때만 막습니다. 코드에 보호 오브젝트의 이름이 따옴표로 나오면, 읽기만 하려던 것이어도 한 번 막히고 "읽는 호출과 바꾸는 호출을 나누라"는 안내가 갑니다. claude-hand를 거치지 않는 다른 도구는 대상이 아닙니다.

## 버전별 변경 내용

번호는 저장소 루트의 `VERSION`을 따릅니다. 태그는 `v0.9.0`과 `v1.0.0`에 붙어 있습니다. 버전마다 무엇이 왜 바뀌었는지 자세한 기록은 [docs/CHANGELOG.md](docs/CHANGELOG.md)에 있습니다.

### 1.0.0 (2026-11-24, 태그 `v1.0.0`): 첫 정식 버전

1.0에 넣기로 한 것이 모두 들어갔습니다: 어떤 도구가 바꿨든 파일까지 되돌리기(0.13.0), 여러 창을 한 번에 되돌리기(0.14.0), 요청마다 시간 · 토큰(0.15.0), 저장하면 자동으로 다시 보내기(0.16.0), Blender에서 보낸 모델이 깔끔하게 들어오게(0.17.0), 창마다 보호 규칙(0.18.0). 기능은 0.18.0과 같고, 이 버전에서 바뀐 것은 번호와 문서입니다.

- README를 정리했습니다: 첫머리에 할 수 있는 것과 목차를 두고, 설치를 앞으로 옮기고, 버전별 자세한 기록은 따로 옮겼습니다.
- 확인: 0.13.0부터 0.18.0까지 열린 창과 실제 감독 세션으로 봤습니다([검증 순서](docs/TEST-PLAN.md) 27~32단계). 다른 PC에서 `update`로 예전 버전에서 올라오는 것도 됐습니다.
- 아직 보지 않은 것: 28단계 H, 29단계 E · G. 써 가며 볼 항목들입니다.

| 버전 | 날짜 | 무엇이 바뀌었나 |
|---|---|---|
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
GitHub에 올리면 같은 테스트가 Windows와 Linux, Python 3.10과 3.13에서 돕니다(`.github/workflows/tests.yml`). 버전은 저장소 루트의 `VERSION` 한 곳에서 정하고, Unity 패키지와 Blender 애드온에 적힌 번호가 같은지 테스트가 확인합니다.
`tools/tests/test_blender_chat_addon.py`는 Blender 애드온(채팅, 되돌리기, 중지, .blend 보내기·받기, FBX 내보내기, 모델 받기)을 실제 Blender 안에서 실행합니다. 위 명령으로는 건너뛰고(Python에 `bpy`가 없으므로), `uv run tools/claude_hand.py selftest`가 설치된 Blender를 화면 없이 띄워 돌립니다.
Unity 쪽은 패키지 안의 `ChSelfTest.cs`가 모델 도구를 실제 Unity에서 돌립니다. `selftest --unity-project <닫힌 프로젝트>`가 Unity를 창 없이 열어 실행합니다. 테스트는 진짜 이벤트 기록을 건드리지 않게 `CLAUDE_HAND_LOG=off`로 돕니다.

## 라이선스

[MIT](LICENSE)입니다. 함께 쓰는 MCP for Unity, MCP for Blender, agent-desktop은 이 저장소에 들어 있지 않고 각자의 라이선스를 따릅니다.
