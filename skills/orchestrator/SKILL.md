---
name: claude-hand
description: Unity 프로젝트나 Blender 창 여러 개를 동시에 다룰 때 쓰는 감독 규칙. 사용자가 둘 이상의 Unity 프로젝트나 Blender 파일을 언급하거나, 오브젝트·프리팹·모델을 한 곳에서 다른 곳(Unity↔Unity, Blender↔Blender, Blender↔Unity)으로 옮기라고 하거나, unity-*/blender-* 하위 세션에게 일을 나눠 맡겨야 할 때, 또는 "Claude Hand 켜줘", "Claude Hand 시작해줘"라고 할 때 사용한다.
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
4. 각 하위 세션에게는 목표, 대상(이름이나 ref), 끝났을 때 돌려줄 값을 분명히 적어서 맡긴다. Claude Hand(감독 세션)에서는 맡기는 글 첫 줄에 어느 요청의 일인지도 적는다: 채팅창 요청이면 `[요청 <번호>]`, 아니면 `[요청 없음]`(아래 "Unity·Blender 채팅창에서 온 메시지").

## "Claude Hand 켜줘"

사용자가 Claude Hand를 켜 달라고 하면(데스크톱 앱의 아무 Code 탭 세션이든, 터미널이든) 이 명령을 실행하고 출력을 그대로 전한다.
```
uv run "{{CLAUDE_HAND}}/tools/claude_hand.py" start
```
- 이미 켜져 있거나 이 세션이 Claude Hand면 그렇다고 알려 주고 아무것도 열지 않는다.
- 새로 열었으면 두 가지를 안내한다. ① 새로 뜬 "Claude Hand" 창에 확인 질문이 보이면 화살표 키로 고르고 Enter(개발용 채널 경고는 계속하는 쪽). ② 데스크톱 앱 Code 탭 세션 목록에서 "Claude Hand"를 열고 거기서 이어서 말하기. 그 창은 일을 시작하면 저절로 최소화된다.
- 이 세션이 Claude Hand가 아니면, 작업은 "Claude Hand" 세션에서 하는 게 좋다고 한 번 알린다. 채팅창 연결, 채팅창 기록 남기기, `current_context`는 그 세션에만 있다. 사용자가 여기서 하길 원하면 아래 규칙대로 하되 채팅창 기록은 남지 않는다.

{{CHAT_RULES}}
## 프로젝트·파일 메모 (며칠에 걸친 작업의 규칙)
- 창마다 메모 파일이 있다. Unity는 `<프로젝트 경로>/ClaudeHand/NOTES.md`, Blender는 `.blend`와 같은 폴더의 `<파일 이름>.claude-hand.md`다. 담당 하위 세션이 시작할 때 읽는다(경로는 하위 세션 정의에도 있다).
- 사용자가 그 프로젝트나 파일에 대해 **앞으로도** 지킬 규칙이나 선호를 말하면(예: "이 프로젝트 머티리얼은 URP Lit만", "오브젝트 이름은 영어 PascalCase로") 그 메모에 날짜와 함께 한 줄로 더한다. 파일이 없으면 `# <이름> 메모` 제목으로 만든다. 이미 있는 줄과 겹치면 그 줄을 고친다. 한 번만 할 일은 적지 않는다.
- 하위 세션 보고에 "메모에 남길 것:"이 있으면 사용자에게 물어보고 더한다.
- 메모를 고쳤으면 그 창에 "메모에 남겼어요: …"라고 알린다. 다음 요청부터 담당 하위 세션이 읽는다.

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
4. **고친 모델을 다시 보낼 때도 1~2번을 그대로 한다.** 저장된 같은 .blend의 같은 오브젝트가 다시 오면 Unity는 새로 넣지 않고, 프로젝트에 있던 모델 파일을 제자리에서 바꾼다(결과의 `updated`가 true). 씬과 프리팹에 놓인 것이 그 자리에서 새 모양이 된다. 사용자에게 "새로 넣지 않고 있던 모델을 바꿨고, 씬의 N개가 새 모양이 됐다"고 알린다. 이 파일 교체도 채팅창의 [되돌리기]로 돌아간다(바꾸기 전 파일이 다시 놓인다). 새로 들어온 모델이면 [되돌리기]가 그 에셋을 프로젝트에서 뺀다(지우지 않고 그 프로젝트의 `Library/ClaudeHand/removed`에 보관한다). 사용자가 따로 한 벌을 원할 때만 `new_copy=true`로 받게 한다.
5. 재질이 다르게 보이면 FBX 머티리얼의 한계라고 알린다. 프로젝트에 같은 이름의 머티리얼이 있으면 `ch_import_model(..., materials="project")`로 다시 받아 그 머티리얼에 연결할 수 있다고 알리고, 그렇게 할지 묻는다. 사용자가 "이 프로젝트는 항상 그렇게"라고 하면 프로젝트 메모에 적어 둔다.

## Unity → Blender
1. `unity-a`에게: "<대상>을 `ch_export_model`로 내보내고 `manifest_path`, `via`, `warnings`를 돌려줘." 씬 오브젝트면 먼저 스냅샷으로 ref를 찾게 하고, 모델 에셋 그대로면 `asset_path`를 쓰게 한다.
2. 결과의 `via`가 `source_file`이면 그 오브젝트의 원본 모델 파일이 가져올 때 모습 그대로 간 것이다. `warnings`에 "씬에서 바꾼 것은 가지 않는다"가 있으면 사용자에게 알린다.
3. "FBX Exporter가 필요하다"는 오류면 그 오브젝트는 Unity에서 만든 메시라는 뜻이다. Package Manager에서 FBX Exporter(`com.unity.formats.fbx`)를 설치할지 사용자에게 묻는다. 혼자 설치하지 않는다.
4. `blender-b`에게: "<manifest 경로>를 'Unity에서 받기' 방법으로 가져오고, 추가된 오브젝트 이름을 돌려줘."
5. 무엇이 어떤 이름으로 들어왔는지 보고한다. .blend 파일은 사용자가 원할 때만 저장하게 한다.

## 하위 세션이 막혔을 때
- `STALE_REF`: 스냅샷을 다시 찍게 한다.
- `EDITOR_BUSY`: 잠시 뒤 다시 맡긴다.
- `CONFLICT`: 위 Unity → Unity 4번처럼 사용자에게 묻는다.
- Unity 연결 오류나 "No Unity instances": 그 Unity 창이 열려 있는지, Window > MCP for Unity가 Connected인지 사용자에게 확인을 부탁한다.
- Blender 연결 오류: 그 Blender 창이 열려 있는지, MCP for Blender 서버가 켜져 있는지, 포트가 정의 파일과 같은지 확인을 부탁한다.
- 하위 세션이 다른 대상을 건드리려다 안전장치에 막혔다면, 그 일을 맞는 담당에게 다시 맡긴다.
- 하위 세션의 호출이 "이 창은 지금 다른 하위 세션이 작업 중"으로 막혔다면, 같은 대상에 하위 세션 둘을 붙인 것이다. 먼저 맡긴 쪽이 끝난 뒤에 다시 맡긴다.
- 하위 세션의 호출이 "사용자가 채팅창에서 [중지]를 눌러"로 막혔다면, 그 일을 다시 맡기지 않는다. 어디까지 했고 무엇이 남았는지 요청한 채팅창에 `reply`로 알린다. 중지는 그 요청에 답하면 풀린다.

## Unity나 Blender가 대화상자 때문에 멈췄을 때

Unity는 "씬을 저장할까요?", "API 업데이트", 가져오기 확인 같은 대화상자가 뜨면 그 자리에서 멈춘다. Windows에서는 claude-hand가 이것을 저절로 알아챈다.
- 하위 세션의 Unity 호출이 "대화상자 때문에 멈춰 있어 이 호출을 막았습니다" 또는 `DIALOG` 오류로 돌아온다. 대화상자의 제목·글·버튼이 그 안에 있다.
- 요청한 채팅창에는 채널이 "…대화상자 때문에 멈춰 있어요. Unity에서 직접 골라 주세요"를 띄운다(따로 할 일 없음).

하위 세션이 이것을 보고하면 **제목·글·버튼을 사용자에게 그대로 보여주고, 무엇을 누를지 묻는다.** 저장·삭제·덮어쓰기에 관한 선택은 절대 혼자 정하지 않는다. 사용자가 Unity에서 직접 누르면 `ch_editor_status`로 풀렸는지 본 뒤 하위 세션에 이어서 맡긴다. 막히기 직전의 호출은 대화상자가 닫힐 때 실행됐을 수 있으니, 다시 시키기 전에 상태부터 확인하게 한다.

사용자가 대신 눌러 달라고 하거나, 위 안내 없이 명령이 계속 시간 초과되면(Blender, 읽히지 않는 창) agent-desktop이 설치돼 있을 때 다음 순서로 한다(`agent-desktop version`으로 확인).
1. `agent-desktop list-windows`로 그 앱의 창 제목을 본다. 대화상자는 보통 별도 창으로 보인다. 창이 최소화돼 있으면 `restore`부터 한다.
2. `agent-desktop snapshot --app <exe> -i`로 대화상자의 글과 버튼을 읽는다. 대화상자는 OS 기본 창이라 대개 읽힌다.
3. **내용과 버튼을 사용자에게 그대로 보여주고, 무엇을 누를지 묻는다.** 저장·삭제·덮어쓰기에 관한 선택은 절대 혼자 정하지 않는다.
4. 사용자가 고른 버튼만 `click <ref>`로 누르고, 다시 snapshot이나 `ch_editor_status`로 풀렸는지 확인한다.

agent-desktop이 없거나 읽히지 않으면 사용자에게 그 창을 직접 확인해 달라고 한다. 처음 세팅 방법은 claude-hand 저장소의 `claude-hand-setup` 스킬을 따른다.
