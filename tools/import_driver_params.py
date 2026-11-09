#!/usr/bin/env python3
"""드라이버가 배포하는 parameter file 로 스키마 초안을 만든다 (명세 §12.2).

노드를 띄우지 않고 파일만으로 동작한다. 장치가 아직 없거나, 드라이버를 받아만 두고
구성을 미리 잡아 둘 때 쓴다.

    python tools/import_driver_params.py \\
        ~/ros2_ws/src/ouster-ros/ouster-ros/config/driver_params.yaml \\
        --node /ouster/os_driver \\
        --driver ouster_ros \\
        --service /ouster/get_metadata --service /ouster/get_config \\
        --out config/schemas/ouster_ros.yaml

왜 이 경로가 필요한가. 상당수 드라이버는 declare_parameter 를 호출하면서
ParameterDescriptor 를 채우지 않는다. 그러면 실행 중 수집(§5.1)으로는 이름과 타입만
얻는다. 반면 패키지 config/ 의 parameter file 주석에는 설명과 허용 값이 적혀 있다.

생성 결과는 **초안**이다. layer / apply / tier 는 TODO 로 남는다. 어떤 파라미터가
장치 펌웨어에 쓰이는지, 실행 중 바꿀 수 있는지는 드라이버 소스와 장치 명세를 봐야
한다.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "av_config"))

from av_config.models import ParamDTO, Tier  # noqa: E402
from av_config.onboarding import param_file_draft, schema_draft  # noqa: E402
from av_config.paramdoc import ParamDoc, parse_params_file  # noqa: E402
from ruamel.yaml import YAML  # noqa: E402

# "ouster/os_driver:" 처럼 파일 맨 위에 적힌 노드 키
_NODE_KEY = re.compile(r"^(?P<name>/?[A-Za-z_][A-Za-z0-9_/*]*)\s*:\s*$")


def infer_node_name(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith((" ", "\t", "#")) or not line.strip():
            continue
        match = _NODE_KEY.match(line.strip())
        if match:
            name = match.group("name")
            return name if name.startswith("/") else "/" + name
    return None


def to_param_dtos(docs: dict[str, ParamDoc]) -> list[ParamDTO]:
    """문서만으로 ParamDTO 를 만든다. 타입은 기본값에서 추론한다.

    실행 중인 노드에서 수집할 수 있다면 그쪽이 정확하다. 여기서는 기본값이
    빈 문자열인 enum 파라미터를 string 으로 보는 등 추론 한계가 있다.
    """
    out: list[ParamDTO] = []
    for doc in docs.values():
        out.append(
            ParamDTO(
                name=doc.name,
                type=doc.inferred_type(),
                description="",          # 설명은 schema_draft 가 doc 에서 가져온다
                runtime_value=doc.default,
                saved_value=doc.default,
                tier=Tier.ENGINEER,
            )
        )
    return out


def apply_overlay(draft: str, overlay: dict) -> str:
    """ROS 가 알려줄 수 없는 정보를 오버레이로 채운다.

    layer / apply / tier / readback_via / global_ref 가 대상이다. 어떤 파라미터가
    장치 펌웨어에 쓰이는지, 실행 중 바꿀 수 있는지는 드라이버 소스와 장치 명세를
    봐야 알 수 있다.
    """
    defaults = overlay.get("defaults") or {}
    device = set(overlay.get("device_params") or [])
    readback = overlay.get("readback") or {}
    overrides = readback.get("overrides") or {}
    notes = overlay.get("notes") or {}
    global_ref = overlay.get("global_ref") or {}

    apply_note = defaults.get("apply_note", "")
    apply_value = defaults.get("apply", "restart")
    tier_value = defaults.get("tier", "engineer")
    layer_value = defaults.get("layer", "driver")

    out: list[str] = []
    current: str | None = None
    for line in draft.splitlines():
        match = re.match(r"^  ([A-Za-z_][A-Za-z0-9_]*):$", line)
        if match:
            current = match.group(1)

        if "layer: driver        # TODO" in line:
            if current in device:
                out.append("    layer: device")
                if current in notes:
                    out.append(f"    # {notes[current]}")
                path = overrides.get(
                    current, readback.get("json_path", "$.{name}").format(name=current)
                )
                service = readback.get("service", "")
                out.append(
                    f'    readback_via: {{ service: "{service}", json_path: "{path}" }}'
                )
            else:
                out.append(f"    layer: {layer_value}")
            continue

        if "apply: restart       # TODO" in line:
            suffix = f"       # {apply_note}" if apply_note else ""
            out.append(f"    apply: {apply_value}{suffix}")
            continue

        if "tier: engineer       # TODO" in line:
            out.append(f"    tier: {tier_value}")
            if current in global_ref:
                out.append(f"    global_ref: {global_ref[current]}")
            continue

        if 'label: "TODO"' in line:
            continue  # label 이 없으면 UI 가 파라미터 이름을 쓴다

        out.append(line)

    text = "\n".join(out) + "\n"
    header = overlay.get("header")
    if header:
        text = re.sub(r"\A(#[^\n]*\n)+\n?", "", text)
        text = header.rstrip() + "\n\n" + text
    return text


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("params_file", type=Path, help="드라이버의 parameter file")
    parser.add_argument(
        "--node", help="노드 이름. 생략하면 파일 맨 위의 키에서 추론한다"
    )
    parser.add_argument(
        "--driver", help="스키마의 driver 필드. 생략하면 노드 이름에서 추론한다"
    )
    parser.add_argument(
        "--service",
        action="append",
        default=[],
        dest="services",
        help="match.required_services 에 넣을 서비스. 여러 번 쓸 수 있다",
    )
    parser.add_argument(
        "--overlay",
        type=Path,
        help="ROS 가 알려줄 수 없는 정보(layer, apply, readback_via 등)를 채우는 "
        "오버레이 YAML. 예: config/schemas/ouster_ros.overlay.yaml",
    )
    parser.add_argument("--out", type=Path, help="스키마를 쓸 경로. 생략하면 표준출력")
    parser.add_argument(
        "--params-out", type=Path, help="parameter file 초안을 쓸 경로 (선택)"
    )
    args = parser.parse_args()

    if not args.params_file.is_file():
        print(f"파일이 없다: {args.params_file}", file=sys.stderr)
        return 2

    text = args.params_file.read_text(encoding="utf-8")
    docs = parse_params_file(text)
    if not docs:
        print(
            "파라미터를 찾지 못했다. ros__parameters 블록이 있는 ROS 2 parameter "
            "file 인지 확인하라.",
            file=sys.stderr,
        )
        return 1

    node = args.node or infer_node_name(text)
    if not node:
        print("노드 이름을 추론할 수 없다. --node 로 지정하라.", file=sys.stderr)
        return 1

    overlay = {}
    if args.overlay:
        if not args.overlay.is_file():
            print(f"오버레이가 없다: {args.overlay}", file=sys.stderr)
            return 2
        with args.overlay.open(encoding="utf-8") as fh:
            overlay = YAML(typ="safe").load(fh) or {}
        node = args.node or overlay.get("node") or node
        services = args.services or overlay.get("required_services") or []
    else:
        services = args.services

    params = to_param_dtos(docs)
    draft = schema_draft(node, params, sorted(services), docs=docs)
    driver = args.driver or overlay.get("driver")
    if driver:
        draft = re.sub(r"^driver: \S+.*$", f"driver: {driver}", draft, count=1, flags=re.M)
    if overlay:
        draft = apply_overlay(draft, overlay)

    if not args.overlay:
        services = args.services
    enum_count = sum(1 for d in docs.values() if d.enum)
    todo_count = draft.count("TODO")
    print(
        f"{args.params_file.name}: 파라미터 {len(docs)}개, "
        f"허용 값 목록 {enum_count}개, 채워야 할 TODO {todo_count}개",
        file=sys.stderr,
    )
    if not services:
        print(
            "경고: required_services 가 비었다. --service 나 오버레이로 채우면 "
            "매칭이 정확해진다. ros2 service list 로 확인하라.",
            file=sys.stderr,
        )

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(draft, encoding="utf-8")
        print(f"스키마 초안: {args.out}", file=sys.stderr)
    else:
        print(draft)

    if args.params_out:
        args.params_out.parent.mkdir(parents=True, exist_ok=True)
        args.params_out.write_text(param_file_draft(node, params), encoding="utf-8")
        print(f"parameter file 초안: {args.params_out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
