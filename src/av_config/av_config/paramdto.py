"""수집 결과를 ParamDTO 로 조립한다 (명세 §5.1, §5.2).

ROS 를 import 하지 않는다. descriptor 는 덕 타이핑으로 받으므로 실제
rcl_interfaces 메시지든 테스트용 가짜든 똑같이 동작한다.

조립 규칙이 한 군데에만 있어야 한다. 실제 Discovery 와 개발용 목 서버가 각자
조립하면 둘이 어긋나고, 화면에서 본 것이 실제 동작과 다르게 된다.
"""

from __future__ import annotations

from typing import Any, Protocol

from .models import ApplyMethod, Layer, ParamDTO, ParamSchema, ParamType, Tier, ValueRange
from .store import SavedEntry, is_drift


class Descriptor(Protocol):
    """rcl_interfaces/msg/ParameterDescriptor 에서 쓰는 부분."""

    name: str
    type: int
    description: str
    additional_constraints: str
    read_only: bool
    dynamic_typing: bool


def first_range(ranges: Any) -> ValueRange | None:
    if not ranges:
        return None
    first = next(iter(ranges))
    return ValueRange(
        from_value=float(first.from_value),
        to_value=float(first.to_value),
        step=float(first.step),
    )


def build_param_dto(
    name: str,
    descriptor: Any | None,
    runtime_value: Any,
    saved: SavedEntry | None,
    param_schema: ParamSchema | None,
    reachable: bool = True,
) -> ParamDTO:
    """파라미터 하나의 전체 상태를 만든다.

    설명은 describe_parameters 를 우선하고, 비어 있으면 스키마의 설명을 쓴다.
    ParameterDescriptor 를 채우지 않는 드라이버가 많아서 그 경우 스키마가
    유일한 설명 출처다 (§12.2).
    """
    dto = ParamDTO(
        name=name,
        type=ParamType(getattr(descriptor, "type", 0)),
        description=getattr(descriptor, "description", "") or "",
        additional_constraints=getattr(descriptor, "additional_constraints", "") or "",
        read_only=bool(getattr(descriptor, "read_only", False)),
        dynamic_typing=bool(getattr(descriptor, "dynamic_typing", False)),
        integer_range=first_range(getattr(descriptor, "integer_range", None)),
        floating_point_range=first_range(
            getattr(descriptor, "floating_point_range", None)
        ),
        runtime_value=runtime_value,
    )

    if saved is not None:
        dto.saved_value = saved.value
        dto.saved_source = saved.source
    dto.drift = is_drift(saved, runtime_value, reachable)

    if param_schema is not None:
        dto.has_schema = True
        dto.label = param_schema.label
        dto.layer = param_schema.layer
        dto.apply = param_schema.apply
        dto.tier = param_schema.tier
        dto.enum_values = param_schema.enum_values
        dto.global_ref = param_schema.global_ref
        dto.lock_while_driving = param_schema.lock_while_driving
        if not dto.description and param_schema.description:
            dto.description = param_schema.description
    else:
        # 원본 모드 기본값 (§5.2): engineer 등급, 적용 방식은 restart 로 간주.
        dto.has_schema = False
        dto.tier = Tier.READ_ONLY if dto.read_only else Tier.ENGINEER
        dto.apply = ApplyMethod.RESTART
        dto.layer = None
        dto.lock_while_driving = True

    return dto


def undeclared_param_dto(name: str, saved: SavedEntry) -> ParamDTO:
    """YAML 에만 있고 노드가 선언하지 않은 값 (§5.3).

    설정은 했지만 효력이 없는 상태이므로 drift 로 표시한다.
    """
    return ParamDTO(
        name=name,
        type=ParamType.NOT_SET,
        description=(
            "노드가 declare_parameter 로 선언하지 않은 값이다. "
            "YAML 에 있으나 적용되지 않는다 (명세 §5.3)."
        ),
        saved_value=saved.value,
        saved_source=saved.source,
        runtime_value=None,
        drift=True,
        has_schema=False,
    )


# Layer 를 re-export 해 호출자가 models 를 따로 import 하지 않아도 되게 한다
__all__ = ["Descriptor", "Layer", "build_param_dto", "first_range", "undeclared_param_dto"]
