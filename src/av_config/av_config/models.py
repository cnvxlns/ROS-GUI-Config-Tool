"""REST DTO 와 설정 파일 스키마 모델 (명세 §7.1, §4.4).

Pydantic 의 역할은 두 가지로 한정한다.
  1. REST 요청/응답 검증
  2. schemas/*.yaml 과 rules/*.yaml 의 메타스키마 검증

파라미터 자체의 진실 원천은 §4.4 스키마 파일과 런타임 ParameterDescriptor 이므로,
파라미터를 Pydantic 모델로 다시 정의하지 않는다.
"""

from __future__ import annotations

from enum import Enum, IntEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ParamType(IntEnum):
    """rcl_interfaces/msg/ParameterType 와 같은 값. 타입은 이 9종으로 닫혀 있다."""

    NOT_SET = 0
    BOOL = 1
    INTEGER = 2
    DOUBLE = 3
    STRING = 4
    BYTE_ARRAY = 5
    BOOL_ARRAY = 6
    INTEGER_ARRAY = 7
    DOUBLE_ARRAY = 8
    STRING_ARRAY = 9


class Layer(str, Enum):
    """§4.1 계층."""

    DEVICE = "device"
    DRIVER = "driver"
    GLOBAL = "global"
    HOST = "host"


class ApplyMethod(str, Enum):
    """§4.2 적용 방식."""

    RUNTIME = "runtime"
    RECONFIGURE = "reconfigure"
    RESTART = "restart"


class Tier(str, Enum):
    """§4.3 노출 등급."""

    OPERATOR = "operator"
    ENGINEER = "engineer"
    HIDDEN = "hidden"
    READ_ONLY = "read_only"


# --------------------------------------------------------------------------
# schemas/*.yaml 메타스키마 (§4.4)
# --------------------------------------------------------------------------


class ServiceRef(BaseModel):
    """드라이버 서비스 참조.

    서비스 타입은 ROS 그래프에서 조회해 동적으로 로드한다. 요청과 응답에서
    어느 필드를 쓸지는 드라이버마다 다르므로 스키마가 지정한다.

    - request_field: 값을 넣을 요청 필드. 없으면 빈 요청으로 호출한다
      (트리거형 서비스).
    - response_field: 값을 읽을 응답 필드. 없으면 응답의 첫 필드를 쓴다.
    - json_path: response_field 가 JSON 문자열일 때 값을 꺼낼 경로.
      "$.config.lidar_mode" 와 "$.azimuth_window.0" 같은 점 표기를 지원한다.
    """

    model_config = ConfigDict(extra="forbid")

    service: str
    request_field: str | None = None
    response_field: str | None = None
    json_path: str | None = None


class ParamSchema(BaseModel):
    """파라미터 하나의 의미 정보."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    label: str | None = None
    # 파라미터 설명. 드라이버가 ParameterDescriptor 를 채우지 않을 때 이 자리가
    # 유일한 설명 출처가 된다 (§12.2). 주석이 아니라 필드여야 UI 가 읽을 수 있다.
    description: str | None = None
    layer: Layer
    apply: ApplyMethod
    tier: Tier
    apply_via: ServiceRef | None = None
    readback_via: ServiceRef | None = None
    enum_values: list[str] | None = Field(default=None, alias="enum")
    checks: list[str] = Field(default_factory=list)
    global_ref: str | None = None
    affects_extra: list[str] = Field(default_factory=list)
    lock_while_driving: bool = True

    @model_validator(mode="after")
    def _require_paths(self) -> ParamSchema:
        # §4.4: layer: device 는 readback_via 필수. 노드 파라미터를 되읽으면 요청값이
        # 그대로 돌아오므로 장치가 값을 거부·보정한 경우를 감지할 수 없다.
        if self.layer is Layer.DEVICE and self.readback_via is None:
            raise ValueError(
                "layer: device 파라미터는 readback_via 가 필수다 (명세 §4.4)"
            )
        if self.apply is ApplyMethod.RECONFIGURE and self.apply_via is None:
            raise ValueError(
                "apply: reconfigure 파라미터는 apply_via 가 필수다 (명세 §4.4)"
            )
        return self


class SchemaMatch(BaseModel):
    """스키마를 노드에 매칭하는 조건 (§5.1-6)."""

    model_config = ConfigDict(extra="forbid")

    node_name_regex: str
    required_services: list[str] = Field(default_factory=list)
    hardware_id_regex: str | None = None


class DriverSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    driver: str
    match: SchemaMatch
    params: dict[str, ParamSchema]


# --------------------------------------------------------------------------
# rules/*.yaml
# --------------------------------------------------------------------------


class SafetyMode(str, Enum):
    OPEN = "open"
    ENFORCE = "enforce"


class VehicleStateRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str = ""
    type: str = ""
    stale_after_sec: float = 3.0


class SafetyRules(BaseModel):
    """§10.1 안전 게이트 설정."""

    model_config = ConfigDict(extra="forbid")

    mode: SafetyMode = SafetyMode.ENFORCE
    vehicle_state: VehicleStateRef = Field(default_factory=VehicleStateRef)
    moving_speed_threshold_mps: float = 0.05


# --------------------------------------------------------------------------
# services.yaml (§11.2)
# --------------------------------------------------------------------------


class TopicExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    expect_hz: float | None = None


class Subsystem(BaseModel):
    """재시작 단위. unit 하나가 노드 여러 개를 띄울 수 있다 (§4.2, §6.1)."""

    model_config = ConfigDict(extra="forbid")

    unit: str
    launch: str | None = None
    param_files: list[str]
    nodes: list[str]
    restart_companions: list[str] = Field(default_factory=list)
    topics: list[TopicExpectation] = Field(default_factory=list)


class ServicesFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subsystems: dict[str, Subsystem]


# --------------------------------------------------------------------------
# REST 응답 DTO
# --------------------------------------------------------------------------


class ValueRange(BaseModel):
    from_value: float
    to_value: float
    step: float


class ParamDTO(BaseModel):
    """파라미터 하나의 전체 상태. 프론트엔드 필드 렌더러의 입력 (§8.3)."""

    name: str
    type: ParamType

    # 자동 수집 결과 (§5.1-3)
    description: str = ""
    additional_constraints: str = ""
    read_only: bool = False
    dynamic_typing: bool = False
    integer_range: ValueRange | None = None
    floating_point_range: ValueRange | None = None

    # 값
    runtime_value: Any = None
    saved_value: Any = None
    saved_source: str | None = None
    drift: bool = False

    # 스키마에서 온 의미 정보. has_schema=False 면 원본 모드 기본값 (§5.2)
    has_schema: bool = False
    label: str | None = None
    layer: Layer | None = None
    apply: ApplyMethod = ApplyMethod.RESTART
    tier: Tier = Tier.ENGINEER
    enum_values: list[str] | None = None
    global_ref: str | None = None
    lock_while_driving: bool = True


class NodeDTO(BaseModel):
    """노드 하나의 요약. unit 과 param_files 는 services.yaml 에서 온다."""

    name: str
    alive: bool
    # services.yaml 에 등록됐는지. 등록되지 않으면 변경을 저장할 곳이 없어
    # 읽기 전용이 된다 (§11.2, §12.2).
    registered: bool = False
    # ROS 가 스스로 만든 내부 노드인지 (store.is_internal_node). 이름이 실행할
    # 때마다 바뀌어 등록 자체가 불가능하므로 목록에서 접어 둔다 (§8.1).
    internal: bool = False
    subsystem: str | None = None
    unit: str | None = None
    param_files: list[str] = Field(default_factory=list)

    display_mode: Literal["mapped", "raw"] = "raw"
    schema_driver: str | None = None

    param_count: int = 0
    drift_count: int = 0
    downstream: list[str] = Field(default_factory=list)

    # 수집 실패 사유 (§5.3). 예: start_parameter_services=false
    unreachable_reason: str | None = None
    # launch 인라인 파라미터 규칙 위반 (§12.1-8). drift 와 구분해 표시한다 (§8.2)
    launch_rule_violation: bool = False


class NodeParamsDTO(BaseModel):
    node: NodeDTO
    params: list[ParamDTO]


class SchemaLoadError(BaseModel):
    file: str
    error: str


class DiscoverySnapshotDTO(BaseModel):
    """GET /api/v1/nodes 응답."""

    collected_at: float
    nodes: list[NodeDTO]
    schema_errors: list[SchemaLoadError] = Field(default_factory=list)


# --------------------------------------------------------------------------
# change set (§6.1, §7.3)
# --------------------------------------------------------------------------


class Role(str, Enum):
    """§10.2 역할. 숫자가 큰 쪽이 더 넓은 권한이다."""

    VIEWER = "viewer"
    OPERATOR = "operator"
    ENGINEER = "engineer"
    ADMIN = "admin"


_ROLE_RANK = {Role.VIEWER: 0, Role.OPERATOR: 1, Role.ENGINEER: 2, Role.ADMIN: 3}
_TIER_REQUIRES = {Tier.OPERATOR: Role.OPERATOR, Tier.ENGINEER: Role.ENGINEER}


def role_allows(role: Role, tier: Tier) -> bool:
    """역할이 해당 등급 파라미터를 바꿀 수 있는지 (§10.2)."""
    if tier in (Tier.HIDDEN, Tier.READ_ONLY):
        return False
    required = _TIER_REQUIRES.get(tier)
    if required is None:
        return False
    return _ROLE_RANK[role] >= _ROLE_RANK[required]


class ChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node: str
    param: str
    value: Any


class ChangeSetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dry_run: bool = False
    comment: str = ""
    changes: list[ChangeRequest] = Field(min_length=1)

    # 저장 전 외부 수정 감지용. 프론트엔드가 조회 시 받은 해시를 되돌려준다 (§6.3).
    expected_hashes: dict[str, str] = Field(default_factory=dict)


class IssueCode(str, Enum):
    UNKNOWN_NODE = "unknown_node"
    UNKNOWN_PARAM = "unknown_param"
    READ_ONLY = "read_only"
    TYPE_MISMATCH = "type_mismatch"
    OUT_OF_RANGE = "out_of_range"
    NOT_IN_ENUM = "not_in_enum"
    FORBIDDEN_TIER = "forbidden_tier"
    LOCKED_WHILE_DRIVING = "locked_while_driving"
    SAFETY_UNKNOWN = "safety_unknown"
    CHECK_FAILED = "check_failed"
    FILE_CHANGED = "file_changed"
    NO_PARAM_FILE = "no_param_file"


class ValidationIssue(BaseModel):
    node: str
    param: str | None = None
    code: IssueCode
    message: str


class StepKind(str, Enum):
    SET_PARAMETERS = "set_parameters"
    RECONFIGURE = "reconfigure"
    RESTART_UNITS = "restart_units"


class PlanStep(BaseModel):
    kind: StepKind
    # set_parameters / reconfigure 는 노드 단위, restart 는 unit 묶음 단위다.
    node: str | None = None
    units: list[str] = Field(default_factory=list)
    params: list[str] = Field(default_factory=list)
    note: str | None = None


class ExecutionPlan(BaseModel):
    """§6.1-3 계획. 재시작 집합은 unit 단위로 계산한다."""

    steps: list[PlanStep] = Field(default_factory=list)
    restart_units: list[str] = Field(default_factory=list)
    # 영향 unit 에 속한 모든 노드. 사용자가 직접 바꾼 노드만 보여주면 실제 중단
    # 범위를 과소평가하게 된다.
    impacted_nodes: list[str] = Field(default_factory=list)
    # 전역 설정을 건드리는 변경. 영향이 센서 스택 전체로 번진다.
    global_params: list[str] = Field(default_factory=list)
    touches_global_file: bool = False
    files_to_write: list[str] = Field(default_factory=list)


class ParamResult(BaseModel):
    node: str
    param: str
    requested: Any = None
    actual: Any = None
    success: bool = False
    reason: str | None = None
    # read-back 을 어디서 읽었는지. device 계층은 장치 경로를 써야 한다 (§6.1).
    readback_source: str | None = None


class ChangeSetStatus(str, Enum):
    REJECTED = "rejected"          # 검증 실패. 아무것도 바뀌지 않았다
    DRY_RUN = "dry_run"            # 검증과 계획만 수행
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    ROLLED_BACK = "rolled_back"
    ROLLBACK_FAILED = "rollback_failed"  # 위험 상태 (§6.2)


class ChangeSetResult(BaseModel):
    id: str
    status: ChangeSetStatus
    stage: str = "validate"
    issues: list[ValidationIssue] = Field(default_factory=list)
    plan: ExecutionPlan | None = None
    results: list[ParamResult] = Field(default_factory=list)
    commit: str | None = None
    rollback_commit: str | None = None
    message: str | None = None


# --------------------------------------------------------------------------
# 온보딩 (§12.2)
# --------------------------------------------------------------------------


class OnboardingStepDTO(BaseModel):
    number: int
    title: str
    detail: str
    # None 이면 Config App 이 판단할 수 없는 단계다
    done: bool | None = None


class ParamChoiceDTO(BaseModel):
    """파라미터 하나에 대해 사람이 정한 값 (§12.2).

    None 은 "아직 정하지 않음"이다. 비워 두면 초안에 TODO 가 그대로 남는다.
    """

    label: str | None = None
    layer: Layer | None = None
    apply: ApplyMethod | None = None
    tier: Tier | None = None
    # ServiceRef 로 조립된다: { service: ..., json_path: ... }
    readback_service: str | None = None
    readback_json_path: str | None = None


class OnboardingChoicesDTO(BaseModel):
    """초안의 TODO 자리에 넣을 값.

    params 의 항목이 기본값(layer/apply/tier)을 덮는다.
    """

    launch: str | None = None
    driver: str | None = None
    # None = 아직 안 봤다, {} = 확인했고 감시할 토픽이 없다
    topic_hz: dict[str, float] | None = None
    required_services: list[str] | None = None

    layer: Layer | None = None
    apply: ApplyMethod | None = None
    tier: Tier | None = None
    readback_service: str | None = None

    params: dict[str, ParamChoiceDTO] = Field(default_factory=dict)


class OnboardingDTO(BaseModel):
    """신규 센서를 등록하는 데 필요한 초안과 절차."""

    node: str
    registered: bool
    has_schema: bool
    alive: bool

    subsystem: str
    unit: str
    param_file: str
    schema_file: str

    services_snippet: str
    param_file_draft: str
    schema_draft: str

    # 화면이 무엇을 물어봐야 하는지. 초안을 정규식으로 파싱하지 않게 서버가 준다.
    topics: list[str] = Field(default_factory=list)
    editable_params: list[str] = Field(default_factory=list)
    # 초안에 남은 TODO 자리 수. 0 이면 그대로 쓸 수 있다.
    todo_count: int = 0

    steps: list[OnboardingStepDTO] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
