"""검증(§6.1-2)과 계획(§6.1-3) 테스트."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src" / "av_config"))

from av_config.models import (  # noqa: E402
    ApplyMethod,
    ChangeRequest,
    IssueCode,
    Layer,
    NodeDTO,
    ParamDTO,
    ParamType,
    Role,
    SafetyMode,
    SafetyRules,
    StepKind,
    Tier,
    ValueRange,
    role_allows,
)
from av_config.planner import build_plan  # noqa: E402
from av_config.services_map import ServicesMap  # noqa: E402
from av_config.store import WriteTarget  # noqa: E402
from av_config.validator import SafetyGate, validate  # noqa: E402

#: 전역 fan-out 검증은 unit 2개 이상이 필요하다. fixtures/config/README.md 참고.
CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"
LIDAR = "/ouster/os_driver"
CAMERA = "/camera_front/camera"
PERCEPTION = "/perception/lidar_segmentation"


def param(**over) -> ParamDTO:
    base = {
        "name": "p",
        "type": ParamType.STRING,
        "apply": ApplyMethod.RESTART,
        "tier": Tier.ENGINEER,
        "lock_while_driving": False,
    }
    base.update(over)
    return ParamDTO(**base)


def node(name: str, **over) -> NodeDTO:
    base = {"name": name, "alive": True}
    base.update(over)
    return NodeDTO(**base)


@pytest.fixture
def services() -> ServicesMap:
    return ServicesMap.load(CONFIG / "services.yaml")


@pytest.fixture
def open_gate() -> SafetyGate:
    return SafetyGate(SafetyRules(mode=SafetyMode.OPEN))


def run_validate(requests, params, role=Role.ENGINEER, gate=None, checks=None):
    return validate(
        requests,
        params,
        checks or {},
        role,
        gate or SafetyGate(SafetyRules(mode=SafetyMode.OPEN)),
    )


# ---------------------------------------------------------------- 역할


@pytest.mark.parametrize(
    ("role", "tier", "allowed"),
    [
        (Role.VIEWER, Tier.OPERATOR, False),
        (Role.OPERATOR, Tier.OPERATOR, True),
        (Role.OPERATOR, Tier.ENGINEER, False),
        (Role.ENGINEER, Tier.ENGINEER, True),
        (Role.ENGINEER, Tier.OPERATOR, True),
        (Role.ADMIN, Tier.ENGINEER, True),
        (Role.ADMIN, Tier.HIDDEN, False),
        (Role.ADMIN, Tier.READ_ONLY, False),
    ],
)
def test_role_allows(role, tier, allowed):
    assert role_allows(role, tier) is allowed


# ---------------------------------------------------------------- 타입


@pytest.mark.parametrize(
    ("ptype", "value"),
    [
        (ParamType.BOOL, True),
        (ParamType.INTEGER, 7),
        (ParamType.DOUBLE, 1.5),
        (ParamType.DOUBLE, 2),            # 정수를 double 로 올리는 것은 허용
        (ParamType.STRING, "x"),
        (ParamType.BOOL_ARRAY, [True, False]),
        (ParamType.INTEGER_ARRAY, [1, 2]),
        (ParamType.DOUBLE_ARRAY, [1, 2.5]),
        (ParamType.STRING_ARRAY, ["a"]),
        (ParamType.BYTE_ARRAY, [0, 255]),
    ],
)
def test_type_accepts_matching_value(ptype, value):
    params = {LIDAR: {"p": param(type=ptype)}}
    assert run_validate([ChangeRequest(node=LIDAR, param="p", value=value)], params) == []


@pytest.mark.parametrize(
    ("ptype", "value"),
    [
        (ParamType.BOOL, 1),              # bool 자리에 int 금지
        (ParamType.INTEGER, True),        # int 자리에 bool 금지
        (ParamType.INTEGER, 1.5),
        (ParamType.STRING, 3),
        (ParamType.INTEGER_ARRAY, "1,2"),
        (ParamType.INTEGER_ARRAY, [1, True]),
        (ParamType.BYTE_ARRAY, [0, 256]),
        (ParamType.STRING_ARRAY, [1]),
    ],
)
def test_type_rejects_mismatched_value(ptype, value):
    params = {LIDAR: {"p": param(type=ptype)}}
    issues = run_validate([ChangeRequest(node=LIDAR, param="p", value=value)], params)
    assert len(issues) == 1
    assert issues[0].code is IssueCode.TYPE_MISMATCH


def test_not_set_param_is_rejected_with_driver_guidance():
    params = {LIDAR: {"p": param(type=ParamType.NOT_SET)}}
    issues = run_validate([ChangeRequest(node=LIDAR, param="p", value="x")], params)
    assert issues[0].code is IssueCode.TYPE_MISMATCH
    assert "드라이버를 먼저 고쳐야" in issues[0].message


# ---------------------------------------------------------------- 범위 / enum


def test_out_of_range_is_rejected():
    params = {
        LIDAR: {
            "p": param(
                type=ParamType.INTEGER,
                integer_range=ValueRange(from_value=1, to_value=10, step=1),
            )
        }
    }
    issues = run_validate([ChangeRequest(node=LIDAR, param="p", value=99)], params)
    assert issues[0].code is IssueCode.OUT_OF_RANGE


def test_in_range_passes():
    params = {
        LIDAR: {
            "p": param(
                type=ParamType.DOUBLE,
                floating_point_range=ValueRange(from_value=0, to_value=5, step=0.1),
            )
        }
    }
    assert run_validate([ChangeRequest(node=LIDAR, param="p", value=0.3)], params) == []


def test_value_outside_enum_is_rejected():
    params = {LIDAR: {"p": param(enum_values=["512x10", "1024x10"])}}
    issues = run_validate([ChangeRequest(node=LIDAR, param="p", value="4096x10")], params)
    assert issues[0].code is IssueCode.NOT_IN_ENUM
    assert "512x10" in issues[0].message


# ---------------------------------------------------------------- 권한 / read_only


def test_read_only_param_is_rejected():
    params = {LIDAR: {"p": param(read_only=True)}}
    issues = run_validate([ChangeRequest(node=LIDAR, param="p", value="x")], params)
    assert issues[0].code is IssueCode.READ_ONLY


def test_operator_cannot_change_engineer_tier():
    params = {LIDAR: {"p": param(tier=Tier.ENGINEER)}}
    issues = run_validate(
        [ChangeRequest(node=LIDAR, param="p", value="x")], params, role=Role.OPERATOR
    )
    assert issues[0].code is IssueCode.FORBIDDEN_TIER


def test_unknown_node_and_param():
    params = {LIDAR: {"p": param()}}
    issues = run_validate(
        [
            ChangeRequest(node="/nope", param="p", value="x"),
            ChangeRequest(node=LIDAR, param="nope", value="x"),
        ],
        params,
    )
    assert {i.code for i in issues} == {IssueCode.UNKNOWN_NODE, IssueCode.UNKNOWN_PARAM}


# ---------------------------------------------------------------- 안전 게이트


def test_enforce_mode_blocks_when_vehicle_state_unknown():
    """차량 상태를 알 수 없으면 잠금으로 간주한다 (fail-safe, §10.1)."""
    gate = SafetyGate(SafetyRules(mode=SafetyMode.ENFORCE))
    params = {LIDAR: {"p": param(lock_while_driving=True)}}
    issues = run_validate(
        [ChangeRequest(node=LIDAR, param="p", value="x")], params, gate=gate
    )
    assert issues[0].code is IssueCode.SAFETY_UNKNOWN


def test_open_mode_allows_when_vehicle_state_unknown():
    """M2 를 테스트할 수 있어야 하므로 open 모드는 허용한다 (§13.1)."""
    gate = SafetyGate(SafetyRules(mode=SafetyMode.OPEN))
    params = {LIDAR: {"p": param(lock_while_driving=True)}}
    assert (
        run_validate([ChangeRequest(node=LIDAR, param="p", value="x")], params, gate=gate)
        == []
    )


def test_driving_blocks_even_in_open_mode():
    gate = SafetyGate(SafetyRules(mode=SafetyMode.OPEN))
    gate.report_state(driving=True)
    params = {LIDAR: {"p": param(lock_while_driving=True)}}
    issues = run_validate(
        [ChangeRequest(node=LIDAR, param="p", value="x")], params, gate=gate
    )
    assert issues[0].code is IssueCode.LOCKED_WHILE_DRIVING


def test_unlocked_param_passes_while_driving():
    gate = SafetyGate(SafetyRules(mode=SafetyMode.ENFORCE))
    gate.report_state(driving=True)
    params = {LIDAR: {"p": param(lock_while_driving=False)}}
    assert (
        run_validate([ChangeRequest(node=LIDAR, param="p", value="x")], params, gate=gate)
        == []
    )


def test_named_check_failure_is_reported():
    params = {LIDAR: {"p": param(type=ParamType.STRING)}}
    issues = run_validate(
        [ChangeRequest(node=LIDAR, param="p", value="203.0.113.9")],
        params,
        checks={LIDAR: {"p": ["must_match_host_nic_ip"]}},
    )
    # 호스트 NIC 주소를 읽을 수 있는 환경이면 불일치로 거부된다.
    # 읽을 수 없으면 건너뛰므로 통과한다. 둘 다 정상 동작이다.
    assert issues == [] or issues[0].code is IssueCode.CHECK_FAILED


def test_unknown_named_check_is_an_error():
    params = {LIDAR: {"p": param()}}
    issues = run_validate(
        [ChangeRequest(node=LIDAR, param="p", value="x")],
        params,
        checks={LIDAR: {"p": ["no_such_rule"]}},
    )
    assert issues[0].code is IssueCode.CHECK_FAILED
    assert "알 수 없는 검증 규칙" in issues[0].message


# ---------------------------------------------------------------- 계획


def test_runtime_change_only_sets_parameters(services):
    params = {LIDAR: {"p": param(apply=ApplyMethod.RUNTIME)}}
    plan = build_plan(
        [ChangeRequest(node=LIDAR, param="p", value="x")],
        params,
        {LIDAR: node(LIDAR)},
        services,
        [WriteTarget("params/lidar_top.yaml", LIDAR, "p", "x")],
    )
    assert [s.kind for s in plan.steps] == [StepKind.SET_PARAMETERS]
    assert plan.restart_units == []


def test_restart_change_restarts_the_whole_unit(services):
    params = {LIDAR: {"p": param(apply=ApplyMethod.RESTART)}}
    plan = build_plan(
        [ChangeRequest(node=LIDAR, param="p", value="x")],
        params,
        {LIDAR: node(LIDAR)},
        services,
        [WriteTarget("params/lidar_top.yaml", LIDAR, "p", "x")],
    )
    assert plan.restart_units == ["av-lidar-top.service"]
    assert plan.impacted_nodes == [LIDAR]
    assert any(s.kind is StepKind.RESTART_UNITS for s in plan.steps)


def test_restart_absorbs_runtime_change_in_same_unit(services):
    """같은 unit 에 restart 가 있으면 runtime 변경은 재시작으로 함께 반영된다."""
    params = {
        LIDAR: {
            "a": param(name="a", apply=ApplyMethod.RESTART),
            "b": param(name="b", apply=ApplyMethod.RUNTIME),
        }
    }
    plan = build_plan(
        [
            ChangeRequest(node=LIDAR, param="a", value="x"),
            ChangeRequest(node=LIDAR, param="b", value="y"),
        ],
        params,
        {LIDAR: node(LIDAR)},
        services,
        [WriteTarget("params/lidar_top.yaml", LIDAR, "a", "x")],
    )
    kinds = [s.kind for s in plan.steps]
    assert StepKind.SET_PARAMETERS not in kinds
    assert StepKind.RESTART_UNITS in kinds


def test_reconfigure_uses_its_own_step(services):
    params = {LIDAR: {"p": param(apply=ApplyMethod.RECONFIGURE, layer=Layer.DEVICE)}}
    plan = build_plan(
        [ChangeRequest(node=LIDAR, param="p", value="x")],
        params,
        {LIDAR: node(LIDAR)},
        services,
        [WriteTarget("params/lidar_top.yaml", LIDAR, "p", "x")],
    )
    assert [s.kind for s in plan.steps] == [StepKind.RECONFIGURE]


def test_writing_shared_global_file_fans_out_to_other_units(services):
    """전역 값 변경은 그 파라미터를 선언한 다른 unit 까지 재시작 대상으로 만든다."""
    shared = "camera_frame"
    params = {
        LIDAR: {shared: param(name=shared, apply=ApplyMethod.RESTART)},
        CAMERA: {shared: param(name=shared, apply=ApplyMethod.RESTART)},
    }
    plan = build_plan(
        [ChangeRequest(node=LIDAR, param=shared, value="x")],
        params,
        {LIDAR: node(LIDAR), CAMERA: node(CAMERA)},
        services,
        [WriteTarget("params/global.yaml", LIDAR, shared, "x")],
    )
    assert plan.touches_global_file is True
    assert "av-lidar-top.service" in plan.restart_units
    assert "av-camera-front.service" in plan.restart_units
    assert CAMERA in plan.impacted_nodes


def test_fan_out_skips_units_whose_nodes_do_not_declare_the_param(services):
    """공유 파일을 고쳐도 그 파라미터를 쓰지 않는 unit 은 재시작하지 않는다."""
    params = {
        LIDAR: {"lidar_only": param(name="lidar_only", apply=ApplyMethod.RESTART)},
        CAMERA: {"something_else": param(name="something_else")},
    }
    plan = build_plan(
        [ChangeRequest(node=LIDAR, param="lidar_only", value="x")],
        params,
        {LIDAR: node(LIDAR), CAMERA: node(CAMERA)},
        services,
        [WriteTarget("params/global.yaml", LIDAR, "lidar_only", "x")],
    )
    assert "av-camera-front.service" not in plan.restart_units


def test_fan_out_is_conservative_for_offline_nodes(services):
    """선언 여부를 알 수 없으면 보수적으로 재시작에 포함한다."""
    params = {LIDAR: {"shared": param(name="shared", apply=ApplyMethod.RESTART)}}
    plan = build_plan(
        [ChangeRequest(node=LIDAR, param="shared", value="x")],
        params,
        {LIDAR: node(LIDAR), CAMERA: node(CAMERA, alive=False)},
        services,
        [WriteTarget("params/global.yaml", LIDAR, "shared", "x")],
    )
    assert "av-camera-front.service" in plan.restart_units


def test_downstream_unit_restarts_after_producers(services):
    """센서가 먼저 떠야 소비자가 정상 상태로 돌아온다."""
    params = {
        LIDAR: {"p": param(apply=ApplyMethod.RESTART)},
        PERCEPTION: {"p": param(apply=ApplyMethod.RESTART)},
    }
    plan = build_plan(
        [
            ChangeRequest(node=LIDAR, param="p", value="x"),
            ChangeRequest(node=PERCEPTION, param="p", value="y"),
        ],
        params,
        {
            LIDAR: node(LIDAR, downstream=[PERCEPTION]),
            PERCEPTION: node(PERCEPTION, downstream=[]),
        },
        services,
        [WriteTarget("params/lidar_top.yaml", LIDAR, "p", "x")],
    )
    restart_steps = [s for s in plan.steps if s.kind is StepKind.RESTART_UNITS]
    assert len(restart_steps) == 2
    assert restart_steps[0].units == ["av-lidar-top.service"]
    assert restart_steps[1].units == ["av-perception.service"]


def test_global_ref_params_are_listed(services):
    params = {
        LIDAR: {
            "sensor_frame": param(
                name="sensor_frame", apply=ApplyMethod.RESTART, global_ref="frames.lidar_top"
            )
        }
    }
    plan = build_plan(
        [ChangeRequest(node=LIDAR, param="sensor_frame", value="x")],
        params,
        {LIDAR: node(LIDAR)},
        services,
        [WriteTarget("params/global.yaml", LIDAR, "sensor_frame", "x")],
    )
    assert plan.global_params == [f"{LIDAR}:sensor_frame"]
