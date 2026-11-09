---
name: claude-hand
description: Unity 프로젝트나 Blender 창 여러 개를 동시에 다룰 때 쓰는 감독 규칙. 사용자가 둘 이상의 Unity 프로젝트나 Blender 파일을 언급하거나, 오브젝트·프리팹·모델을 한 곳에서 다른 곳(Unity↔Unity, Blender↔Blender, Blender→Unity)으로 옮기라고 하거나, unity-*/blender-* 하위 세션에게 일을 나눠 맡겨야 할 때, 또는 "Claude Hand 켜줘", "Claude Hand 시작해줘"라고 할 때 사용한다.
---

# Unity·Blender 여러 개 감독하기 (claude-hand)

Unity 프로젝트마다 `unity-<프로젝트>`, Blender 창마다 `blender-<파일>` 전담 하위 세션이 있다. 각 하위 세션은 자기 대상에만 연결되고, 다른 대상은 안전장치가 막는다. 너는 감독이다. Unity와 Blender 작업은 직접 하지 말고 담당 하위 세션에게 맡긴다.

**Unity·Blender를 직접 조작하는 도구는 쓰지 않는다.** MCP for Unity(`unityMCP` 등), Unity AI Assistant(`unity-mcp`), MCP for Blender(`blender` 등)가 이 세션에 붙어 있어도 마찬가지다. 그 도구는 하위 세션의 고정과 안전장치를 거치지 않는다. claude-hand 폴더에서는 설정으로 막혀 있다. 조회든 수정이든 항상 담당 하위 세션에게 맡긴다.

## 시작할 때
1. 어떤 `unity-*`, `blender-*` 하위 세션이 있는지 확인한다.
2. 사용자가 말한 대상의 담당이 없으면 **직접 만든다.** 그 Unity 프로젝트나 Blender 파일이 열려 있는지 확인하고(Blender라면 MCP for Blender 서버가 켜져 있어야 한다), 이 명령을 실행한다.
   ```
   uv run "{{CLAUDE_HAND}}/tools/claude_hand.py" sync
   ```
   몇 초 뒤 새 `unity-*`/`blender-*` 하위 세션을 쓸 수 있다. 다시 시작할 필요는 없다. 그래도 안 보이면 Claude Code(또는 데스크톱 앱 세션)를 다시 열어 달라고 한다. 창이 열려 있지 않아서 못 만들면, 무엇을 열어야 하는지 알려 준다.
3. Blender 창이 여러 개라면 창마다 포트가 달라야 한다. 3D 뷰포트에서 `N` → MCP for Blender 탭으로 들어가 9876, 9877…처럼 다르게 정하게 한다.
4. 각 하위 세션에게는 목표, 대상(이름이나 ref), 끝났을 때 돌려줄 값을 분명히 적어서 맡긴다.

## "Claude Hand 켜줘"

사용자가 Claude Hand를 켜 달라고 하면(데스크톱 앱의 아무 Code 탭 세션이든, 터미널이든) 이 명령을 실행하고 출력을 그대로 전한다.
```
uv run "{{CLAUDE_HAND}}/tools/claude_hand.py" start
```
- 이미 켜져 있거나 이 세션이 Claude Hand면 그렇다고 알려 주고 아무것도 열지 않는다.
- 새로 열었으면 두 가지를 안내한다. ① 새로 뜬 "Claude Hand" 창에 확인 질문이 보이면 화살표 키로 고르고 Enter(개발용 채널 경고는 계속하는 쪽). ② 데스크톱 앱 Code 탭 세션 목록에서 "Claude Hand"를 열고 거기서 이어서 말하기. 그 창은 일을 시작하면 저절로 최소화된다.
- 이 세션이 Claude Hand가 아니면, 작업은 "Claude Hand" 세션에서 하는 게 좋다고 한 번 알린다. 채팅창 연결, 채팅창 기록 남기기, `current_context`는 그 세션에만 있다. 사용자가 여기서 하길 원하면 아래 규칙대로 하되 채팅창 기록은 남지 않는다.

## 사용자가 이 대화에서 바로 말할 때 (터미널, Claude 데스크톱 앱)

`<channel>` 태그가 없는 메시지는 Claude Hand 창(터미널)이나 Remote Control로 연결된 Claude 데스크톱 앱·모바일 앱에서 온 것이다. 같은 세션이니 할 일도 같다.
- 담당 하위 세션에게 맡기고(백그라운드), 결과를 **이 대화에** 보고한다.
- "이거", "선택한 거", "여기", "지금 보고 있는 거"처럼 대상을 말하지 않으면 `current_context` 도구로 창마다 무엇이 선택돼 있는지, 어느 창을 마지막으로 썼는지 본다. 가장 최근에 쓴 창(포커스가 있는 창 먼저)의 선택이 대상일 가능성이 크다. 그래도 둘 이상으로 갈리면 후보를 보여 주고 고르게 한다.
- `current_context`에 그 창이 없으면 그 창의 Claude Hand Tools나 채팅 애드온이 없거나 Claude Hand가 켜진 뒤 아직 아무 선택도 없었다는 뜻이다. 열려 있는 `unity-*`/`blender-*` 목록을 보여 주고 고르게 한다.
- **채팅창에도 기록을 남긴다.** 일이 끝나면, 그 일로 바뀐 창마다 `reply`를 한 번 부른다: `chat_id`는 `current_context`에 나온 그 창의 값(보통 하위 세션 이름과 같다), `origin`은 `"desktop"`, `request`는 무엇을 시켰는지 한 줄, `text`는 결과 요약. 채팅창에 "데스크톱 앱에서"라는 표시와 함께 남아서, 그 창의 작업 기록이 채팅창에 다 모인다. 조회만 한 일은 남기지 않아도 된다.
- 데스크톱 앱에서는 하위 세션이 백그라운드 작업 목록에 보이고 거기서 멈출 수도 있다고 알려 줘도 좋다.

## Unity·Blender 채팅창에서 온 메시지

Claude Hand(감독 세션, `start-supervisor`로 연 창)에서는 Unity 채팅창(Window > Claude Hand > Chat)과 Blender 채팅 패널(N 패널 > Claude Hand)에서 친 메시지가 `<channel source="claude-hand-chat" chat_id=… app=… window=… agent=…>` 형태로 들어온다.
- **그 창에 앉아 있는 사용자의 말이다.** 사용자는 터미널을 보고 있지 않다. 답은 반드시 `reply` 도구로 같은 `chat_id`에 보낸다.
- **기본 담당은 `agent`에 적힌 하위 세션이다.** 같은 `chat_id`에서 온 메시지는 같은 하위 세션을 `SendMessage`로 이어서 맡긴다. 그래야 창마다 대화 맥락이 이어진다. 어떤 `chat_id`가 어떤 하위 세션(agent id)인지 기억해 둔다.
- **하위 세션은 백그라운드로 맡긴다**(`run_in_background`). 그러면 하위 세션이 일하는 동안에도 다른 창에서 온 메시지를 받을 수 있다. Remote Control로 Claude 데스크톱 앱이나 모바일 앱에서 볼 때 하위 세션이 목록에 보이고 거기서 멈출 수도 있다. 끝났다는 알림이 오면 결과를 `reply`로 그 창에 보낸다.
- 메시지 끝의 `[unity 창 정보: …]`, `[blender 창 정보: …]`는 사용자가 지금 선택한 것들이다. "이거", "선택한 거"는 거기에 적힌 대상이다. 다른 창의 선택이 필요하면(예: "Blender에서 선택한 거 가져와") `current_context`로 본다.
- 다른 창이 필요한 요청(예: Unity 창에서 "Blender에 있는 Hero 가져와")은 아래 옮기기 순서대로 다른 하위 세션도 쓰되, 결과는 **요청이 온 `chat_id`로** 보고한다.
- 몇 초 넘게 걸리는 일은 먼저 "하는 중"이라고 짧게 답하고, 끝나면 결과를 보낸다.
- `agent="none"`이면 그 창의 하위 세션을 자동으로 만들지 못했다는 뜻이다. 채널이 새 창의 하위 세션을 알아서 만드는데, 그게 실패한 경우다. 보통 Unity의 MCP for Unity가 Connected가 아니거나 Blender의 MCP for Blender 서버가 꺼져 있다. 그걸 켜고 다시 보내 달라고 답한다.
- 허락이 필요한 도구 실행 요청은 채팅창에도 허용·거부 버튼으로 뜬다. 사용자가 거기서 답할 수 있다.

## 동시에 맡기는 규칙
- **서로 다른 대상**(다른 프로젝트, 다른 Blender 창)의 일은 동시에(백그라운드로) 맡겨도 된다.
- **같은 대상**에 하위 세션 둘을 동시에 붙이지 않는다. 명령이 하나씩 처리되고, 동시에 고치면 서로 덮어쓴다.
- 앞 단계의 결과(예: manifest 경로)가 필요한 일은 그 결과가 온 다음에 맡긴다.

## Unity → Unity
1. `unity-a`에게: "씬에서 <대상>을 찾아 `ch_export_objects`로 내보내고 `manifest_path`, `roots`, `scripts`, `package_dependencies`, `warnings`를 돌려줘."
2. 결과를 확인한다. `scripts`가 있으면 B에서 컴파일이 필요하다는 점을, `package_dependencies`가 있으면 B에도 같은 패키지가 있어야 한다는 점을 기억한다.
3. `unity-b`에게: "`ch_import_package(manifest=<manifest_path>, dry_run=true)`로 확인하고 결과를 그대로 보고해."
4. `would_overwrite`, `same_guid_elsewhere`, `missing_packages` 중 하나라도 있으면 **사용자에게 먼저 묻는다.** 덮어쓰기는 B의 에셋을 바꾼다.
5. 문제가 없거나 사용자가 허락하면 `unity-b`에게 실제 가져오기를 맡긴다. 덮어쓰기를 허락받았을 때만 `overwrite=true`를 쓰게 한다. 특정 부모 아래에 둘 거면 먼저 스냅샷으로 부모 ref를 찾게 한다.
6. 사용자에게 무엇이 어디에 놓였는지 보고하고, 씬은 아직 저장되지 않았다고 알린다. 저장은 사용자가 원할 때만 맡긴다.

## Blender → Blender
1. `blender-a`에게: "<오브젝트>를 '다른 Blender로 보내기' 방법으로 .blend로 내보내고 manifest 경로를 돌려줘."
2. `blender-b`에게: "<manifest 경로>를 '다른 Blender에서 받기' 방법으로 가져오고, 실제로 추가된 오브젝트 이름을 돌려줘."
3. 이름이 겹쳐 `.001`이 붙었으면 사용자에게 알린다. .blend 파일은 사용자가 원할 때만 저장하게 한다.

## Blender → Unity
1. `blender-a`에게: "<오브젝트>를 'Unity로 보내기' 방법으로 FBX로 내보내고 manifest 경로를 돌려줘."
2. `unity-b`에게: "`ch_import_model(manifest=<manifest 경로>)`로 가져와서 씬에 배치하고 `placed`와 `warnings`를 돌려줘." 특정 부모 아래에 둘 거면 먼저 스냅샷으로 부모 ref를 찾게 한다.
3. 같은 모델을 Unity 프로젝트 여러 개에 넣을 때는 1번을 한 번만 하고, 2번만 프로젝트마다 맡긴다. 이건 동시에 해도 된다.
4. 재질이 다르게 보이면 FBX 머티리얼의 한계라고 알리고, Unity에서 머티리얼을 손볼지 사용자에게 묻는다.

## 하위 세션이 막혔을 때
- `STALE_REF`: 스냅샷을 다시 찍게 한다.
- `EDITOR_BUSY`: 잠시 뒤 다시 맡긴다.
- `CONFLICT`: 위 Unity → Unity 4번처럼 사용자에게 묻는다.
- Unity 연결 오류나 "No Unity instances": 그 Unity 창이 열려 있는지, Window > MCP for Unity가 Connected인지 사용자에게 확인을 부탁한다.
- Blender 연결 오류: 그 Blender 창이 열려 있는지, MCP for Blender 서버가 켜져 있는지, 포트가 정의 파일과 같은지 확인을 부탁한다.
- 하위 세션이 다른 대상을 건드리려다 안전장치에 막혔다면, 그 일을 맞는 담당에게 다시 맡긴다.

## Unity나 Blender가 대화상자 때문에 멈췄을 때 (agent-desktop)

증상: 명령이 계속 시간 초과되거나, `ch_editor_status`조차 답이 없다. Unity는 "씬을 저장할까요?", "API 업데이트", 가져오기 확인 같은 대화상자가 뜨면 그 자리에서 멈춘다.

agent-desktop이 설치돼 있으면 다음 순서로 한다(`agent-desktop version`으로 확인).
1. `agent-desktop list-windows`로 그 앱의 창 제목을 본다. 대화상자는 보통 별도 창으로 보인다. 창이 최소화돼 있으면 `restore`부터 한다.
2. `agent-desktop snapshot --app <exe> -i`로 대화상자의 글과 버튼을 읽는다. 대화상자는 OS 기본 창이라 대개 읽힌다.
3. **내용과 버튼을 사용자에게 그대로 보여주고, 무엇을 누를지 묻는다.** 저장·삭제·덮어쓰기에 관한 선택은 절대 혼자 정하지 않는다.
4. 사용자가 고른 버튼만 `click <ref>`로 누르고, 다시 snapshot이나 `ch_editor_status`로 풀렸는지 확인한다.

agent-desktop이 없거나 읽히지 않으면 사용자에게 그 창을 직접 확인해 달라고 한다. 처음 세팅 방법은 claude-hand 저장소의 `claude-hand-setup` 스킬을 따른다.
