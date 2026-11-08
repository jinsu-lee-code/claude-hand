---
name: claude-hand-unity
description: 여러 Unity 프로젝트를 동시에 다룰 때 쓰는 감독 규칙. 사용자가 둘 이상의 Unity 프로젝트를 언급하거나, 한 프로젝트의 오브젝트·프리팹·에셋을 다른 프로젝트로 옮기라고 하거나, unity-* 하위 세션에게 일을 나눠 맡겨야 할 때 사용한다.
---

# 여러 Unity 프로젝트 감독하기 (claude-hand)

Unity 프로젝트마다 전담 하위 세션 `unity-<프로젝트>`가 있다. 각 하위 세션은 자기 프로젝트에만 연결되고, 다른 프로젝트는 안전장치가 막는다. 너는 감독이다. Unity 작업은 직접 하지 말고 담당 하위 세션에게 맡긴다.

## 시작할 때
1. 어떤 `unity-*` 하위 세션이 있는지 확인한다. 사용자가 말한 프로젝트의 담당이 없으면, 그 프로젝트를 Unity로 연 뒤 `uv run <claude-hand 경로>/tools/claude_hand.py sync`를 실행하라고 안내한다.
2. 각 하위 세션에게는 목표, 대상(이름이나 ref), 끝났을 때 돌려줄 값을 분명히 적어서 맡긴다.

## 동시에 맡기는 규칙
- **서로 다른 프로젝트**의 일은 동시에(백그라운드로) 맡겨도 된다.
- **같은 프로젝트**에 하위 세션 둘을 동시에 붙이지 않는다. Unity는 명령을 하나씩 처리하고, 동시에 고치면 서로 덮어쓴다.
- 앞 단계의 결과(예: `manifest_path`)가 필요한 일은 그 결과가 온 다음에 맡긴다.

## 프로젝트 간 오브젝트 옮기기 (A → B)
1. `unity-a`에게: "씬에서 <대상>을 찾아 `ch_export_objects`로 내보내고 `manifest_path`, `roots`, `scripts`, `package_dependencies`, `warnings`를 돌려줘."
2. 결과를 확인한다. `scripts`가 있으면 B에서 컴파일이 필요하다는 점을, `package_dependencies`가 있으면 B에도 같은 패키지가 있어야 한다는 점을 기억한다.
3. `unity-b`에게: "`ch_import_package(manifest=<manifest_path>, dry_run=true)`로 확인하고 결과를 그대로 보고해."
4. `would_overwrite`, `same_guid_elsewhere`, `missing_packages` 중 하나라도 있으면 **사용자에게 먼저 묻는다.** 덮어쓰기는 B의 에셋을 바꾼다.
5. 문제가 없거나 사용자가 허락하면 `unity-b`에게 실제 가져오기를 맡긴다. 덮어쓰기를 허락받았을 때만 `overwrite=true`를 쓰게 한다. 특정 부모 아래에 둘 거면 먼저 스냅샷으로 부모 ref를 찾게 한다.
6. 사용자에게 무엇이 어디에 놓였는지 보고하고, 씬은 아직 저장되지 않았다고 알린다. 저장은 사용자가 원할 때만 맡긴다.

## 하위 세션이 막혔을 때
- `STALE_REF`: 스냅샷을 다시 찍게 한다.
- `EDITOR_BUSY`: 잠시 뒤 다시 맡긴다.
- `CONFLICT`: 위 4번처럼 사용자에게 묻는다.
- 연결 오류나 "No Unity instances": 그 Unity 창이 열려 있는지, Window > MCP for Unity가 Connected인지 사용자에게 확인을 부탁한다.
- 하위 세션이 다른 프로젝트를 건드리려다 안전장치에 막혔다면, 그 일을 맞는 담당에게 다시 맡긴다.
