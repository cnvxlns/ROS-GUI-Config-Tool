"""신규 센서 온보딩 보조 (명세 §12.2).

자동 수집(§5.1)으로 얻은 파라미터 정보를 바탕으로 사람이 채워야 할 설정 파일의
초안을 만든다. 손으로 ros2 param describe 를 돌려 YAML 을 쓰는 공수를 줄이는 것이
목적이다.

**초안은 초안이다.** 계층(layer), 적용 방식(apply), 노출 등급(tier) 은 ROS 가
알려주지 않는다. 어떤 파라미터가 장치 펌웨어에 쓰이는지, 실행 중 바꿀 수 있는지는
드라이버 문서와 장치 명세를 봐야 한다. 그래서 그 자리는 TODO 로 남긴다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .models import ParamDTO, ParamType, Tier
from .paramdoc import ParamDoc

# 사람이 반드시 확인해야 하는 자리
TODO = "TODO"

_TYPE_NAME = {
    ParamType.NOT_SET: "미설정",
    ParamType.BOOL: "bool",
    ParamType.INTEGER: "int",
    ParamType.DOUBLE: "double",
    ParamType.STRING: "string",
    ParamType.BYTE_ARRAY: "byte[]",
    ParamType.BOOL_ARRAY: "bool[]",
    ParamType.INTEGER_ARRAY: "int[]",
    ParamType.DOUBLE_ARRAY: "double[]",
    ParamType.STRING_ARRAY: "string[]",
}


@dataclass
class OnboardingStep:
    """§12.2 절차의 한 단계."""

    number: int
    title: str
    detail: str
    done: bool | None = None  # None = Config App 이 판단할 수 없다


@dataclass
class OnboardingDraft:
    node: str
    subsystem: str
    unit: str
    param_file: str
    schema_file: str
    services_snippet: str
    param_file_draft: str
    schema_draft: str
    steps: list[OnboardingStep] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# 이름 추론
# --------------------------------------------------------------------------


def subsystem_name(node: str) -> str:
    """노드 이름에서 서브시스템 이름을 추론한다.

    /gnss_imu/driver -> gnss_imu
    /talker          -> talker
    네임스페이스가 여러 단계면 첫 단계를 쓴다.
    """
    parts = [p for p in node.split("/") if p]
    if not parts:
        return "unnamed"
    name = parts[0] if len(parts) > 1 else parts[-1]
    return re.sub(r"[^a-z0-9_]", "_", name.lower())


def unit_name(subsystem: str) -> str:
    return "av-" + subsystem.replace("_", "-") + ".service"


def driver_name(node: str) -> str:
    """스키마의 driver 필드 후보. 실제 패키지 이름은 사람이 확인해야 한다."""
    return subsystem_name(node) + "_driver"


# --------------------------------------------------------------------------
# YAML 스칼라
# --------------------------------------------------------------------------

_PLAIN_SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.\-]*$")
# YAML 1.1 에서 따옴표 없이 쓰면 bool 로 읽히는 값들
_AMBIGUOUS = {
    "y", "n", "yes", "no", "true", "false", "on", "off",
    "null", "~", "none",
}


def yaml_scalar(value: Any) -> str:
    """값을 YAML 리터럴로 바꾼다. 애매하면 따옴표를 씌운다."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(yaml_scalar(v) for v in value) + "]"

    text = str(value)
    if text == "":
        return '""'
    if text.lower() in _AMBIGUOUS or not _PLAIN_SAFE.match(text):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


# --------------------------------------------------------------------------
# 초안 생성
# --------------------------------------------------------------------------


def services_snippet(
    node: str,
    topics: list[str],
    existing_subsystem: str | None = None,
    existing_unit: str | None = None,
    existing_param_files: list[str] | None = None,
) -> str:
    """services.yaml 의 subsystems 아래에 붙여 넣을 조각 (§11.2).

    이미 등록된 노드면 실제 등록 이름을 쓴다. 추론한 이름을 보여주면 그대로
    붙여 넣었을 때 같은 노드가 두 서브시스템에 중복 등록된다.
    """
    sub = existing_subsystem or subsystem_name(node)
    unit = existing_unit or unit_name(sub)
    param_files = existing_param_files or [f"params/{sub}.yaml", "params/global.yaml"]
    registered = existing_subsystem is not None

    header = (
        "# 이미 등록된 노드다. 현재 services.yaml 의 내용을 그대로 보여준다."
        if registered
        else "# config/services.yaml 의 subsystems: 아래에 붙여 넣는다."
    )
    lines = [
        header,
        "# unit, launch 는 실제 배포 구성에 맞게 고쳐야 한다.",
        f"  {sub}:",
        f"    unit: {unit}",
        f'    launch: "ros2 launch av_bringup {sub}.launch.py"   # {TODO}: 실제 launch 파일',
        "    # 순서가 저장값을 결정한다. global.yaml 이 뒤에 와서 전역 값이 이긴다.",
        f"    param_files: [{', '.join(param_files)}]",
        f'    nodes: ["{node}"]',
    ]
    if topics:
        lines.append("    topics:")
        for topic in topics:
            lines.append(
                f'      - {{ name: "{topic}", expect_hz: 0.0 }}   # {TODO}: ros2 topic hz 로 확인'
            )
    else:
        lines.append(f"    # topics: 점검 규칙이 감시할 주요 토픽을 적는다 ({TODO})")
    return "\n".join(lines) + "\n"


def param_file_draft(node: str, params: list[ParamDTO]) -> str:
    """현재 런타임 값으로 parameter file 초안을 만든다.

    read_only 파라미터는 넣지 않는다. YAML 에 써도 드라이버가 거부한다.
    """
    sub = subsystem_name(node)
    lines = [
        f"# config/params/{sub}.yaml",
        f"# {node} 의 현재 런타임 값으로 만든 초안이다.",
        "# 드라이버 기본값과 같은 값은 지워도 된다. 적어 두면 의도가 드러난다.",
        "",
        f"{node}:",
        "  ros__parameters:",
    ]
    written = 0
    for param in sorted(params, key=lambda p: p.name):
        if param.read_only or param.tier is Tier.READ_ONLY:
            continue
        if param.type == ParamType.NOT_SET:
            continue
        if param.runtime_value is None:
            continue
        lines.append(f"    {param.name}: {yaml_scalar(param.runtime_value)}")
        written += 1
    if written == 0:
        lines.append("    # 수집된 값이 없다. 노드가 실행 중인지 확인하라.")
    return "\n".join(lines) + "\n"


def schema_draft(
    node: str,
    params: list[ParamDTO],
    services: list[str],
    docs: dict[str, ParamDoc] | None = None,
) -> str:
    """스키마 초안 (§4.4).

    이름, 타입, 범위는 자동 수집 결과로 채운다. layer / apply / tier 는 ROS 가
    알려줄 수 없으므로 TODO 로 둔다.

    docs 는 드라이버가 배포하는 parameter file 주석에서 뽑아낸 문서다(§12.2).
    ParameterDescriptor 를 채우지 않는 드라이버에서는 설명과 허용 값을 여기서만
    얻을 수 있다. 다만 드라이버 문서가 틀렸을 수 있으므로 사람이 확인해야 한다.
    """
    driver = driver_name(node)
    lines = [
        f"# config/schemas/{driver}.yaml",
        "#",
        f"# {node} 의 자동 수집 결과로 만든 초안이다.",
        "# 이름, 타입, 범위, 설명은 describe_parameters 에서 가져왔다.",
        "#",
        f"# 아래 {TODO} 를 모두 채워야 쓸 수 있다. 특히:",
        "#   layer: device 로 두면 readback_via 가 필수다 (명세 §4.4).",
        "#          노드 파라미터를 되읽으면 요청값이 그대로 돌아오므로,",
        "#          장치가 값을 거부·보정한 경우를 감지할 수 없기 때문이다.",
        "#   apply: runtime / reconfigure / restart 중 하나.",
        "#          드라이버가 add_on_set_parameters_callback 을 구현했는지 확인한다.",
        "",
        f"driver: {driver}   # {TODO}: 실제 드라이버 패키지 이름",
        "match:",
        f'  node_name_regex: "^{re.escape(node)}$"',
    ]
    if services:
        lines.append("  required_services:")
        for service in services:
            lines.append(f'    - "{service}"')
    else:
        lines.append(
            f"  required_services: []   # {TODO}: ros2 service list 로 확인"
        )
    lines.append("")
    lines.append("params:")

    if not params:
        lines.append("  # 수집된 파라미터가 없다. 노드가 실행 중인지 확인하라.")
        return "\n".join(lines) + "\n"

    for param in sorted(params, key=lambda p: p.name):
        lines.extend(_param_block(param, (docs or {}).get(param.name)))

    return "\n".join(lines) + "\n"


def _param_block(param: ParamDTO, doc: ParamDoc | None = None) -> list[str]:
    type_name = _TYPE_NAME.get(param.type, str(param.type))
    facts = [f"타입 {type_name}"]
    if param.runtime_value is not None:
        facts.append(f"현재값 {param.runtime_value!r}")
    rng = param.integer_range or param.floating_point_range
    if rng is not None:
        facts.append(f"범위 {rng.from_value} ~ {rng.to_value}")
    if param.read_only:
        facts.append("드라이버가 read_only 로 선언")

    if doc is not None and doc.required:
        facts.append("드라이버 문서상 필수")

    lines = [f"  {param.name}:", f"    # {', '.join(facts)}"]

    lines.append("")  # 설명 필드는 아래에서 넣는다 (자리 표시)

    if param.read_only:
        # 드라이버가 못 바꾼다고 선언했으므로 등급만 정해 주면 된다
        lines.append("    layer: driver")
        lines.append("    apply: restart")
        lines.append("    tier: read_only")
        lines.append("")
        return lines

    # 설명은 수집 결과를 우선하고, 비어 있으면 드라이버 문서를 쓴다.
    # 주석이 아니라 필드로 넣어야 UI 가 읽을 수 있다.
    description = param.description or (doc.description if doc else "")
    if description:
        lines[-1] = "    description: " + yaml_scalar(description)
        if not param.description and doc is not None:
            lines.insert(
                len(lines) - 1, "    # 설명 출처: 드라이버의 parameter file 주석"
            )
    else:
        lines.pop()

    lines.append(f'    label: "{TODO}"')
    enum_values = param.enum_values or (doc.enum if doc else None)
    lines.append(f"    layer: driver        # {TODO}: device 면 readback_via 필수")
    lines.append(f"    apply: restart       # {TODO}: runtime / reconfigure / restart")
    lines.append(f"    tier: engineer       # {TODO}: operator / engineer / hidden")
    if enum_values:
        # 드라이버 문서에서 가져온 값이다. 펌웨어 버전에 따라 다를 수 있으므로
        # 장치에서 한 번 확인하는 편이 안전하다.
        lines.append("    # 허용 값 출처: 드라이버의 parameter file 주석")
        lines.append("    enum: [" + ", ".join(_quote(v) for v in enum_values) + "]")
    elif param.type == ParamType.STRING:
        lines.append("    # enum: []          # 허용 값이 정해져 있으면 적는다")
    if _looks_like_frame(param.name):
        lines.append(f"    # global_ref: frames.{TODO}   # 전역 frame 규칙을 따른다면")
    lines.append("    lock_while_driving: true")
    lines.append("")
    return lines


def _looks_like_frame(name: str) -> bool:
    """TF 프레임 파라미터로 보이는 이름.

    명세 §12.1-6 은 *_frame 접미사를 규칙으로 두지만, ROS 표준인 frame_id 와
    *_frame_id 도 흔하다. 셋 다 전역 frame 규칙의 후보로 본다.
    """
    leaf = name.rsplit(".", 1)[-1]
    return leaf == "frame_id" or leaf.endswith(("_frame", "_frame_id"))


def build_steps(
    registered: bool, has_schema: bool, alive: bool, has_param_file: bool
) -> list[OnboardingStep]:
    """§12.2 온보딩 절차. Config App 이 판단할 수 있는 것만 done 을 채운다."""
    return [
        OnboardingStep(
            1,
            "ROS 2 드라이버 확보",
            "제조사 문서 → 제조사 GitHub → ROS Index 순으로 확인한다. "
            "대상 배포판과 ARM64(Jetson) 빌드 가능 여부를 본다.",
            done=None,
        ),
        OnboardingStep(
            2,
            "드라이버 작성 규칙 적용",
            "직접 만든 드라이버라면 §12.1 의 9개 규칙을 따른다. "
            "특히 장치 값을 되읽을 조회 서비스를 제공해야 한다.",
            done=None,
        ),
        OnboardingStep(
            3,
            "실제 장치로 파라미터 수집",
            "장치를 연결한 상태에서 수집한다. 이 화면의 파라미터 목록이 그 결과다.",
            done=alive,
        ),
        OnboardingStep(
            4,
            "드라이버로 못 바꾸는 설정이 있는지 확인",
            "장치 매뉴얼과 비교한다. 있으면 드라이버를 고친다. "
            "Config App 이 장치 API 를 직접 부르는 예외는 두지 않는다.",
            done=None,
        ),
        OnboardingStep(
            5,
            "스키마 파일 작성",
            "아래 초안을 내려받아 TODO 를 채우고 config/schemas/ 에 둔다.",
            done=has_schema,
        ),
        OnboardingStep(
            6,
            "parameter file 과 services.yaml 등록, systemd 유닛 생성",
            "아래 초안을 쓴다. unit 에 포함되는 노드 전체를 nodes 에 적는다.",
            done=registered and has_param_file,
        ),
        OnboardingStep(
            7,
            "점검 규칙에 주요 토픽과 기대 주기 등록",
            "config/rules/health.yaml 과 services.yaml 의 topics 를 채운다.",
            done=None,
        ),
        OnboardingStep(
            8,
            "실제 장치로 테스트",
            "각 적용 방식별 적용, read-back(장치 경로 포함), 롤백을 확인한다.",
            done=None,
        ),
    ]


def _wrap(text: str, width: int) -> list[str]:
    """설명을 주석 폭에 맞게 접는다."""
    if not text:
        return []
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _quote(value: str) -> str:
    """enum 항목을 YAML 문자열로. 숫자처럼 보이면 반드시 따옴표가 필요하다."""
    return yaml_scalar(value) if not _PLAIN_SAFE.match(value) else f'"{value}"'
