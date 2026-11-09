"""change set 검증 (명세 §6.1-2).

하나라도 실패하면 아무것도 바꾸지 않고 거부한다. 타입, 범위, enum, 의존성 규칙,
권한(tier), 안전 게이트를 모두 확인한다.
"""

from __future__ import annotations

import logging
from typing import Any

from .hostcheck import CHECKS
from .models import (
    ChangeRequest,
    IssueCode,
    ParamDTO,
    ParamType,
    Role,
    SafetyMode,
    SafetyRules,
    Tier,
    ValidationIssue,
    role_allows,
)

_log = logging.getLogger(__name__)

# ParamType -> 허용되는 파이썬 타입
_SCALAR_TYPES: dict[int, tuple[type, ...]] = {
    ParamType.BOOL: (bool,),
    ParamType.INTEGER: (int,),
    ParamType.DOUBLE: (int, float),  # 정수를 double 로 올리는 것은 허용한다
    ParamType.STRING: (str,),
}
_ARRAY_ELEMENT_TYPES: dict[int, tuple[type, ...]] = {
    ParamType.BYTE_ARRAY: (int,),
    ParamType.BOOL_ARRAY: (bool,),
    ParamType.INTEGER_ARRAY: (int,),
    ParamType.DOUBLE_ARRAY: (int, float),
    ParamType.STRING_ARRAY: (str,),
}


def check_type(param: ParamDTO, value: Any) -> str | None:
    """값이 파라미터 타입에 맞는지. 맞으면 None, 아니면 사유."""
    ptype = int(param.type)

    if ptype == ParamType.NOT_SET:
        return (
            "노드가 이 파라미터를 선언하지 않았다. YAML 에 써도 적용되지 않으므로 "
            "드라이버를 먼저 고쳐야 한다."
        )

    if ptype in _SCALAR_TYPES:
        allowed = _SCALAR_TYPES[ptype]
        # bool 은 int 의 하위 타입이므로 명시적으로 걸러낸다
        if ptype != ParamType.BOOL and isinstance(value, bool):
            return f"bool 은 {_name(ptype)} 자리에 올 수 없다"
        if not isinstance(value, allowed):
            return f"{_name(ptype)} 이어야 하는데 {type(value).__name__} 이 왔다"
        return None

    if ptype in _ARRAY_ELEMENT_TYPES:
        if not isinstance(value, (list, tuple)):
            return f"{_name(ptype)} 이어야 하는데 배열이 아니다"
        allowed = _ARRAY_ELEMENT_TYPES[ptype]
        for index, item in enumerate(value):
            if ptype != ParamType.BOOL_ARRAY and isinstance(item, bool):
                return f"[{index}] 에 bool 이 올 수 없다"
            if not isinstance(item, allowed):
                return f"[{index}] 가 {type(item).__name__} 이다"
            if ptype == ParamType.BYTE_ARRAY and not 0 <= int(item) <= 255:
                return f"[{index}] 가 바이트 범위(0~255)를 벗어났다"
        return None

    return f"알 수 없는 파라미터 타입 {ptype}"


def check_range(param: ParamDTO, value: Any) -> str | None:
    """describe_parameters 가 준 범위를 확인한다."""
    rng = param.integer_range or param.floating_point_range
    if rng is None or not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    if value < rng.from_value or value > rng.to_value:
        return f"허용 범위 {rng.from_value} ~ {rng.to_value} 를 벗어났다"
    return None


def check_enum(param: ParamDTO, value: Any) -> str | None:
    """스키마가 선언한 허용 값 목록."""
    if not param.enum_values:
        return None
    if str(value) not in param.enum_values:
        return f"허용 값이 아니다. 가능한 값: {', '.join(param.enum_values)}"
    return None


def check_named_rules(param: ParamDTO, value: Any, checks: list[str]) -> list[str]:
    """스키마의 checks 에 적힌 규칙을 돌린다 (§4.4)."""
    reasons: list[str] = []
    for name in checks:
        func = CHECKS.get(name)
        if func is None:
            _log.error("스키마가 알 수 없는 check 를 참조한다: %s", name)
            reasons.append(f"알 수 없는 검증 규칙 {name}")
            continue
        reason = func(value)
        if reason:
            reasons.append(reason)
    return reasons


class SafetyGate:
    """§10.1 안전 게이트.

    M3 에서 차량 상태 토픽 구독이 붙는다. 지금은 모드와 보고된 상태만 본다.
    """

    def __init__(self, rules: SafetyRules) -> None:
        self.rules = rules
        self._driving: bool | None = None  # None = 알 수 없음

    def report_state(self, driving: bool | None) -> None:
        self._driving = driving

    @property
    def state_known(self) -> bool:
        return self._driving is not None

    def blocks_any(self) -> str | None:
        """lock_while_driving 기본값(True) 기준으로 지금 적용이 가능한지.

        §8.2 의 "주행 중이거나 권한이 없으면 적용 버튼을 비활성화" 표시에 쓴다.
        파라미터별 판정은 blocks() 가 한다.
        """
        if self._driving is True:
            return "주행 중이다. 잠긴 파라미터는 적용할 수 없다."
        if self._driving is None and self.rules.mode is SafetyMode.ENFORCE:
            return (
                "차량 상태를 알 수 없다. enforce 모드에서는 잠금으로 간주한다 "
                "(fail-safe)."
            )
        return None

    def blocks(self, param: ParamDTO) -> str | None:
        """이 파라미터의 적용을 막아야 하면 사유를, 아니면 None."""
        if not param.lock_while_driving:
            return None

        if self._driving is True:
            return "주행 중이다. 이 파라미터는 주행 중 변경이 금지되어 있다."

        if self._driving is None:
            if self.rules.mode is SafetyMode.ENFORCE:
                # 차량 상태를 알 수 없으면 잠금으로 간주한다 (fail-safe)
                return (
                    "차량 상태를 알 수 없다. enforce 모드에서는 잠금으로 간주한다 "
                    "(fail-safe)."
                )
            _log.warning(
                "safety.mode=open: 차량 상태를 모르는 채로 %s 적용을 허용한다",
                param.name,
            )
        return None


def validate(
    requests: list[ChangeRequest],
    params_by_node: dict[str, dict[str, ParamDTO]],
    checks_by_node: dict[str, dict[str, list[str]]],
    role: Role,
    gate: SafetyGate,
) -> list[ValidationIssue]:
    """change set 전체를 검증한다. 빈 리스트면 통과."""
    issues: list[ValidationIssue] = []

    for request in requests:
        node = request.node
        known = params_by_node.get(node)
        if known is None:
            issues.append(
                ValidationIssue(
                    node=node,
                    code=IssueCode.UNKNOWN_NODE,
                    message="수집된 노드가 아니다. services.yaml 등록과 노드 상태를 확인하라.",
                )
            )
            continue

        param = known.get(request.param)
        if param is None:
            issues.append(
                ValidationIssue(
                    node=node,
                    param=request.param,
                    code=IssueCode.UNKNOWN_PARAM,
                    message="노드가 선언하지 않은 파라미터다.",
                )
            )
            continue

        if param.read_only or param.tier is Tier.READ_ONLY:
            issues.append(
                ValidationIssue(
                    node=node,
                    param=param.name,
                    code=IssueCode.READ_ONLY,
                    message="드라이버가 read_only 로 선언한 값이다.",
                )
            )
            continue

        if not role_allows(role, param.tier):
            issues.append(
                ValidationIssue(
                    node=node,
                    param=param.name,
                    code=IssueCode.FORBIDDEN_TIER,
                    message=f"{param.tier.value} 등급은 {role.value} 역할이 바꿀 수 없다.",
                )
            )
            continue

        type_reason = check_type(param, request.value)
        if type_reason:
            issues.append(
                ValidationIssue(
                    node=node,
                    param=param.name,
                    code=IssueCode.TYPE_MISMATCH,
                    message=type_reason,
                )
            )
            continue

        range_reason = check_range(param, request.value)
        if range_reason:
            issues.append(
                ValidationIssue(
                    node=node, param=param.name,
                    code=IssueCode.OUT_OF_RANGE, message=range_reason,
                )
            )

        enum_reason = check_enum(param, request.value)
        if enum_reason:
            issues.append(
                ValidationIssue(
                    node=node, param=param.name,
                    code=IssueCode.NOT_IN_ENUM, message=enum_reason,
                )
            )

        named = checks_by_node.get(node, {}).get(param.name, [])
        for reason in check_named_rules(param, request.value, named):
            issues.append(
                ValidationIssue(
                    node=node, param=param.name,
                    code=IssueCode.CHECK_FAILED, message=reason,
                )
            )

        blocked = gate.blocks(param)
        if blocked:
            issues.append(
                ValidationIssue(
                    node=node,
                    param=param.name,
                    code=(
                        IssueCode.SAFETY_UNKNOWN
                        if not gate.state_known
                        else IssueCode.LOCKED_WHILE_DRIVING
                    ),
                    message=blocked,
                )
            )

    return issues


def _name(ptype: int) -> str:
    return {
        ParamType.BOOL: "bool",
        ParamType.INTEGER: "정수",
        ParamType.DOUBLE: "실수",
        ParamType.STRING: "문자열",
        ParamType.BYTE_ARRAY: "byte 배열",
        ParamType.BOOL_ARRAY: "bool 배열",
        ParamType.INTEGER_ARRAY: "정수 배열",
        ParamType.DOUBLE_ARRAY: "실수 배열",
        ParamType.STRING_ARRAY: "문자열 배열",
    }.get(ptype, f"타입{ptype}")
