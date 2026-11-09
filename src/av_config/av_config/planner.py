"""change set -> 실행 계획 (명세 §6.1-3).

핵심은 재시작 집합을 **unit 단위**로 계산하는 것이다. 재시작 수단이
systemctl restart <unit> 이고 unit 하나가 노드 여러 개를 띄우므로, 노드 단위로
묶으면 실제 중단 범위를 과소평가한다.
"""

from __future__ import annotations

import logging

from .models import (
    ApplyMethod,
    ChangeRequest,
    ExecutionPlan,
    NodeDTO,
    ParamDTO,
    PlanStep,
    StepKind,
)
from .services_map import ServicesMap
from .store import WriteTarget, normalize_node_name

_log = logging.getLogger(__name__)


def build_plan(
    requests: list[ChangeRequest],
    params_by_node: dict[str, dict[str, ParamDTO]],
    nodes_by_name: dict[str, NodeDTO],
    services: ServicesMap,
    write_targets: list[WriteTarget],
) -> ExecutionPlan:
    """검증을 통과한 change set 으로 실행 계획을 만든다."""
    plan = ExecutionPlan()
    plan.files_to_write = sorted({t.rel_path for t in write_targets})

    runtime: dict[str, list[str]] = {}
    reconfigure: dict[str, list[str]] = {}
    restart_nodes: set[str] = set()

    for request in requests:
        node = normalize_node_name(request.node)
        param = params_by_node.get(node, {}).get(request.param)
        apply = param.apply if param else ApplyMethod.RESTART
        if param is not None and param.global_ref:
            plan.global_params.append(f"{node}:{param.name}")

        if apply is ApplyMethod.RUNTIME:
            runtime.setdefault(node, []).append(request.param)
        elif apply is ApplyMethod.RECONFIGURE:
            reconfigure.setdefault(node, []).append(request.param)
        else:
            restart_nodes.add(node)

    plan.global_params = sorted(set(plan.global_params))

    # ------------------------------------------------------------------
    # 재시작 unit 집합
    # ------------------------------------------------------------------

    units: set[str] = set()
    for node in restart_nodes:
        unit = services.unit_of(node)
        if unit is None:
            _log.error(
                "%s 가 services.yaml 에 없어 재시작할 unit 을 알 수 없다", node
            )
            continue
        units.add(unit)

    shared_units, touches_shared = _fan_out_units(
        plan.files_to_write, write_targets, params_by_node, nodes_by_name, services
    )
    units |= shared_units
    plan.touches_global_file = touches_shared

    # 같은 unit 에 restart 가 하나라도 있으면 그 unit 의 runtime/reconfigure 변경은
    # 재시작으로 함께 반영되므로 별도 적용을 생략한다.
    for node in list(runtime):
        if services.unit_of(node) in units:
            runtime.pop(node)
    for node in list(reconfigure):
        if services.unit_of(node) in units:
            reconfigure.pop(node)

    # ------------------------------------------------------------------
    # 단계 조립: runtime -> reconfigure -> restart (§6.1-5)
    # ------------------------------------------------------------------

    for node in sorted(runtime):
        plan.steps.append(
            PlanStep(
                kind=StepKind.SET_PARAMETERS,
                node=node,
                params=sorted(runtime[node]),
            )
        )

    for node in sorted(reconfigure):
        plan.steps.append(
            PlanStep(
                kind=StepKind.RECONFIGURE,
                node=node,
                params=sorted(reconfigure[node]),
                note="드라이버 전용 서비스로 장치를 재설정한다",
            )
        )

    if units:
        upstream, downstream = _split_by_dependency(units, nodes_by_name, services)
        # 재시작은 순차가 아니라 일괄로 한다. 전역 값은 노드 간 일관성이 전제이므로
        # 순차 재시작은 일부 노드가 구 값, 일부가 신 값인 중간 상태를 만든다.
        if upstream:
            plan.steps.append(
                PlanStep(
                    kind=StepKind.RESTART_UNITS,
                    units=sorted(upstream),
                    note="영향 unit 을 일괄 재시작한다",
                )
            )
        if downstream:
            plan.steps.append(
                PlanStep(
                    kind=StepKind.RESTART_UNITS,
                    units=sorted(downstream),
                    note="다운스트림은 센서 unit 기동 이후에 올린다",
                )
            )

    plan.restart_units = sorted(units)
    impacted: set[str] = set()
    for unit in units:
        impacted.update(services.nodes_in_unit(unit))
    plan.impacted_nodes = sorted(impacted)
    return plan


def _fan_out_units(
    files: list[str],
    targets: list[WriteTarget],
    params_by_node: dict[str, dict[str, ParamDTO]],
    nodes_by_name: dict[str, NodeDTO],
    services: ServicesMap,
) -> tuple[set[str], bool]:
    """여러 unit 이 공유하는 파일을 고치면 그 unit 들도 재시작 대상이다.

    global.yaml 은 모든 unit 의 param_files 에 들어가므로 전역 값 한 줄 변경이
    센서 스택 전체로 번진다 (§6.1 전역 변경 처리).

    다만 무조건 전부 재시작하지는 않는다. 공유 파일에 쓴 파라미터를 실제로 선언한
    노드가 있는 unit 만 고른다. 노드가 꺼져 있어 선언 여부를 알 수 없으면
    보수적으로 포함한다.
    """
    written_params = {t.param for t in targets}
    fan_out: set[str] = set()
    touches_shared = False

    for rel in files:
        owners = [
            (name, sub)
            for name, sub in services.subsystems.items()
            if rel in sub.param_files
        ]
        if len(owners) <= 1:
            continue
        touches_shared = True
        for _name, sub in owners:
            for node in sub.nodes:
                fq = normalize_node_name(node)
                node_dto = nodes_by_name.get(fq)
                declared = params_by_node.get(fq, {})
                unknown = node_dto is None or not node_dto.alive
                if unknown or (written_params & set(declared)):
                    fan_out.add(sub.unit)
                    break

    return fan_out, touches_shared


def _split_by_dependency(
    units: set[str],
    nodes_by_name: dict[str, NodeDTO],
    services: ServicesMap,
) -> tuple[set[str], set[str]]:
    """다운스트림 unit 을 뒤로 보낸다.

    어떤 unit 의 노드가 다른 영향 unit 의 노드의 다운스트림이면 나중에 올린다.
    센서가 먼저 떠야 소비자가 정상 상태로 돌아온다.
    """
    producers: set[str] = set()
    for unit in units:
        for node in services.nodes_in_unit(unit):
            dto = nodes_by_name.get(node)
            if dto:
                producers.update(dto.downstream)

    downstream: set[str] = set()
    for unit in units:
        nodes = set(services.nodes_in_unit(unit))
        if nodes & producers:
            # 자기 자신만 생산자인 경우는 제외한다
            own_downstream: set[str] = set()
            for node in nodes:
                dto = nodes_by_name.get(node)
                if dto:
                    own_downstream.update(dto.downstream)
            if not nodes & own_downstream:
                downstream.add(unit)

    return units - downstream, downstream
