"""change set 트랜잭션 테스트 (명세 §6.1 전체, §6.2, §6.3).

적용/검증 단계는 가짜로 대체하고 오케스트레이션을 검증한다. 실제 rclpy 경로는
컨테이너에서 따로 확인해야 한다.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[3]
#: 서브시스템 3개를 가진 테스트용 설정. 실제 config/ 에는 장착된 센서만
#: 등록하므로 unit 여러 개와 오프라인 노드가 필요한 검증은 픽스처를 쓴다.
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"
sys.path.insert(0, str(REPO / "src" / "av_config"))

from av_config.changesets import ChangeSetRunner  # noqa: E402
from av_config.errors import ApplyError, ChangeSetBusy, VerifyError  # noqa: E402
from av_config.gitparams import GitParams  # noqa: E402
from av_config.models import (  # noqa: E402
    ChangeRequest,
    ChangeSetRequest,
    ChangeSetStatus,
    IssueCode,
    NodeDTO,
    NodeParamsDTO,
    ParamDTO,
    ParamType,
    Role,
    SafetyMode,
    SafetyRules,
    Tier,
)
from av_config.schema import SchemaRegistry  # noqa: E402
from av_config.services_map import ServicesMap  # noqa: E402
from av_config.store import ParamStore, ParamWriter, values_equal  # noqa: E402
from av_config.validator import SafetyGate  # noqa: E402

LIDAR = "/ouster/os_driver"
FILES = ["params/lidar_top.yaml", "params/global.yaml"]


# ---------------------------------------------------------------- 가짜 의존성


@dataclass
class FakeReadBack:
    value: Any
    source: str


@dataclass
class FakeHz:
    count: int = 100
    hz: float = 10.0


class FakeDiscovery:
    """store/schema 는 실제 코드를 쓰고 런타임 값만 가짜로 채운다."""

    def __init__(self, store: ParamStore, schemas: SchemaRegistry, services: ServicesMap):
        self.store = store
        self.schemas = schemas
        self.services = services
        self.runtime: dict[str, Any] = {
            "lidar_mode": "1024x10",
            "udp_dest": "192.168.1.1",
            "sensor_frame": "lidar_top_link",
            "sensor_hostname": "192.168.1.100",
            "timestamp_mode": "TIME_FROM_INTERNAL_OSC",
            "point_type": "original",
            "use_sim_time": False,
        }
        self.missing_nodes: set[str] = set()
        #: 서비스가 아직 뜨지 않은 노드. 재시작 직후를 흉내낸다.
        self.unready_nodes: set[str] = set()

    def live_nodes(self) -> list[str]:
        return [n for n in self.services.all_nodes() if n not in self.missing_nodes]

    def services_of(self, node: str) -> set[str]:
        """노드가 제공하는 서비스. 준비 완료 판정에 쓰인다(§5.3).

        unready_nodes 에 넣으면 스키마의 required_services 가 비어 보인다.
        """
        if node in self.unready_nodes:
            return set()
        schema = self.schemas.match(node, None)
        if schema is None:
            return set()
        return set(schema.match.required_services)

    async def collect_params(self, node: str) -> NodeParamsDTO:
        files = self.services.param_files_of(node)
        saved = self.store.saved_values(node, files)
        schema = self.schemas.match(node, None)
        found = self.services.subsystem_of(node)
        dto = NodeDTO(
            name=node,
            alive=node not in self.missing_nodes,
            subsystem=found[0] if found else None,
            unit=found[1].unit if found else None,
            param_files=list(files),
            display_mode="mapped" if schema else "raw",
            schema_driver=schema.driver if schema else None,
        )
        params: list[ParamDTO] = []
        for name in sorted(set(self.runtime) | set(saved)):
            ps = self.schemas.param_schema(schema, name)
            entry = saved.get(name)
            runtime = self.runtime.get(name)
            param = ParamDTO(
                name=name,
                type=ParamType.BOOL if isinstance(runtime, bool) else ParamType.STRING,
                runtime_value=runtime,
                saved_value=entry.value if entry else None,
                saved_source=entry.source if entry else None,
                drift=(not values_equal(entry.value, runtime)) if entry else False,
            )
            if ps is not None:
                param.has_schema = True
                param.layer = ps.layer
                param.apply = ps.apply
                param.tier = ps.tier
                param.enum_values = ps.enum_values
                param.global_ref = ps.global_ref
                param.lock_while_driving = ps.lock_while_driving
            else:
                param.tier = Tier.ENGINEER
            params.append(param)
        dto.param_count = len(params)
        dto.drift_count = sum(1 for p in params if p.drift)
        return NodeParamsDTO(node=dto, params=params)


class FakeApplier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.fail_on: str | None = None
        # 남은 실패 횟수. 1 이면 일시적 실패(롤백 재적용은 성공)를 흉내낸다.
        self.fail_times: int | None = None
        self.reject: dict[str, str] = {}

    def _should_fail(self, kind: str) -> bool:
        if self.fail_on != kind:
            return False
        if self.fail_times is None:
            return True
        if self.fail_times <= 0:
            return False
        self.fail_times -= 1
        return True

    async def set_parameters(self, node: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("set_parameters", (node, sorted(payload))))
        if self._should_fail("set_parameters"):
            raise ApplyError("가짜 set_parameters 실패")
        return {name: self.reject.get(name) for name in payload}

    async def call_driver_service(self, ref: Any, value: Any) -> None:
        self.calls.append(("reconfigure", (ref.service, value)))
        if self._should_fail("reconfigure"):
            raise ApplyError("가짜 서비스 실패")

    async def restart_units(self, units: list[str]) -> None:
        self.calls.append(("restart", tuple(sorted(units))))
        if self._should_fail("restart"):
            raise ApplyError("가짜 재시작 실패")


class FakeVerifier:
    def __init__(self) -> None:
        self.readback: dict[str, Any] = {}
        self.raise_on: set[str] = set()
        #: 파라미터 이름 -> 남은 일시적 실패 횟수. 재시작 직후를 흉내낸다.
        self.transient_failures: dict[str, int] = {}
        self.hz = FakeHz()
        #: 남은 0 Hz 표본 횟수. 발행이 늦게 시작되는 상황을 흉내낸다.
        self.transient_zero_hz = 0

    async def read_back(self, node: str, param: ParamDTO, ref: Any = None) -> FakeReadBack:
        if param.name in self.raise_on:
            raise VerifyError(f"{param.name} 를 읽을 수 없다")
        left = self.transient_failures.get(param.name, 0)
        if left > 0:
            self.transient_failures[param.name] = left - 1
            raise VerifyError(f"{param.name}: 아직 응답 없음")
        source = ref.service if ref is not None else f"{node}/get_parameters"
        return FakeReadBack(value=self.readback.get(param.name), source=source)

    @staticmethod
    def matches(expected: Any, actual: Any) -> bool:
        return values_equal(expected, actual)

    async def measure_hz(self, topic: str, duration: float = 2.0) -> FakeHz:
        if self.transient_zero_hz > 0:
            self.transient_zero_hz -= 1
            return FakeHz(count=0, hz=0.0)
        return self.hz


# ---------------------------------------------------------------- fixtures


@dataclass
class Rig:
    runner: ChangeSetRunner
    discovery: FakeDiscovery
    applier: FakeApplier
    verifier: FakeVerifier
    git: GitParams
    workspace: Path
    store: ParamStore


# 적용 메커니즘(runtime / reconfigure / restart, device / driver 계층)을 모두
# 거치게 만든 합성 스키마. 실제 드라이버 스키마에 기대면 그 드라이버가 어떤
# 기능을 지원하는지에 따라 테스트가 깨진다. 예컨대 ouster-ros 는
# add_on_set_parameters_callback 이 없어 reconfigure 가 하나도 없다.
SYNTHETIC_SCHEMA = """
driver: test_driver
match:
  node_name_regex: "^/ouster/os_driver$"
  required_services: ["/ouster/get_config"]
params:
  lidar_mode:
    label: "해상도 / 회전 속도"
    layer: device
    apply: reconfigure
    apply_via:    { service: "/ouster/set_config", request_field: "value" }
    readback_via: { service: "/ouster/get_metadata", json_path: "$.config.lidar_mode" }
    tier: engineer
    enum: ["512x10", "512x20", "1024x10", "1024x20", "2048x10"]
    lock_while_driving: true
  udp_dest:
    layer: device
    apply: restart
    readback_via: { service: "/ouster/get_config", json_path: "$.udp_dest" }
    tier: engineer
    checks: ["must_match_host_nic_ip"]
  sensor_hostname:
    layer: driver
    apply: restart
    tier: engineer
  timestamp_mode:
    layer: driver
    apply: runtime
    tier: operator
  sensor_frame:
    layer: driver
    apply: restart
    tier: engineer
    global_ref: frames.lidar_top
  point_type:
    layer: driver
    apply: restart
    tier: hidden
"""


@pytest.fixture(autouse=True)
def _fast_settle(monkeypatch):
    """반영 확인 수렴 대기를 짧게 줄인다.

    실제 값은 60초다(재시작한 드라이버가 올라오기를 기다린다). 테스트가 그 시간을
    실제로 기다리면 안 되므로 전부에 적용한다. 재시도 **동작** 자체는
    transient_failures / transient_zero_hz 로 따로 검증한다.
    """
    from av_config import changesets as cs

    monkeypatch.setattr(cs, "_VERIFY_SETTLE_TIMEOUT", 3.0)


@pytest.fixture
def rig(tmp_path: Path, monkeypatch) -> Rig:
    # The synthetic sensor's destination must not depend on the test PC's NIC.
    monkeypatch.setattr("av_config.hostcheck.host_ipv4_addresses", lambda: {"10.5.5.1"})
    shutil.copytree(
        FIXTURE_CONFIG,
        tmp_path / "cfg",
        # config/params 는 배포 시 git 저장소다(README 3단계). 개발 리포에서도
        # 그렇게 해두면 .git 이 같이 복사돼, 복사본에 이미 초기 commit 이 있는
        # 상태가 된다. 그러면 픽스처의 initial commit 이 "변경 없음"으로 실패한다.
        ignore=shutil.ignore_patterns(".git"),
    )
    workspace = tmp_path / "cfg"
    # 배포되는 실제 스키마 대신 합성 스키마를 쓴다 (위 주석 참고)
    (workspace / "schemas" / "ouster_ros.yaml").write_text(
        SYNTHETIC_SCHEMA, encoding="utf-8"
    )
    params = workspace / "params"
    for args in (
        ["init", "-q"],
        ["-c", "user.name=t", "-c", "user.email=t@t", "add", "--all"],
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "initial"],
    ):
        subprocess.run(["git", "-C", str(params), *args], check=True)

    services = ServicesMap.load(workspace / "services.yaml")
    schemas = SchemaRegistry.load_dir(workspace / "schemas")
    store = ParamStore(workspace)
    discovery = FakeDiscovery(store, schemas, services)
    applier = FakeApplier()
    verifier = FakeVerifier()
    git = GitParams(params)
    runner = ChangeSetRunner(
        discovery=discovery,
        services=services,
        schemas=schemas,
        store=store,
        git=git,
        applier=applier,
        verifier=verifier,
        gate=SafetyGate(SafetyRules(mode=SafetyMode.OPEN)),
    )
    return Rig(runner, discovery, applier, verifier, git, workspace, store)


def submit(rig: Rig, **kw) -> Any:
    request = ChangeSetRequest(**kw)
    return asyncio.run(rig.runner.submit(request, Role.ENGINEER, "engineer1"))


def saved(rig: Rig, param: str) -> Any:
    entry = ParamStore(rig.workspace).saved_values(LIDAR, FILES).get(param)
    return entry.value if entry else None


# ---------------------------------------------------------------- 검증 거부


def test_validation_failure_changes_nothing(rig: Rig):
    """검증에서 실패하면 아무것도 바뀌지 않는다 (§6.1-2)."""
    before_head = rig.git.head()
    result = submit(
        rig, changes=[ChangeRequest(node=LIDAR, param="lidar_mode", value="9999x99")]
    )
    assert result.status is ChangeSetStatus.REJECTED
    assert result.issues[0].code is IssueCode.NOT_IN_ENUM
    assert saved(rig, "lidar_mode") == "1024x10"   # 파일 그대로
    assert rig.git.head() == before_head           # commit 없음
    assert rig.applier.calls == []                 # 적용 시도도 없음


def test_unknown_param_is_rejected(rig: Rig):
    result = submit(rig, changes=[ChangeRequest(node=LIDAR, param="nope", value="x")])
    assert result.status is ChangeSetStatus.REJECTED
    assert result.issues[0].code is IssueCode.UNKNOWN_PARAM


def test_enforce_mode_rejects_when_vehicle_state_unknown(rig: Rig):
    rig.runner._gate = SafetyGate(SafetyRules(mode=SafetyMode.ENFORCE))
    result = submit(
        rig, changes=[ChangeRequest(node=LIDAR, param="lidar_mode", value="2048x10")]
    )
    assert result.status is ChangeSetStatus.REJECTED
    assert result.issues[0].code is IssueCode.SAFETY_UNKNOWN


def test_external_edit_is_detected_before_saving(rig: Rig):
    """YAML 이 조회 이후 외부에서 수정됐으면 거부한다 (§6.3)."""
    writer = ParamWriter(rig.store)
    stale = writer.file_hashes(FILES)
    path = rig.workspace / "params" / "lidar_top.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "\n# 손으로 고침\n", encoding="utf-8")

    result = submit(
        rig,
        changes=[ChangeRequest(node=LIDAR, param="lidar_mode", value="2048x10")],
        expected_hashes=stale,
    )
    assert result.status is ChangeSetStatus.REJECTED
    assert result.issues[0].code is IssueCode.FILE_CHANGED


# ---------------------------------------------------------------- dry run


def test_dry_run_returns_plan_without_writing(rig: Rig):
    before_head = rig.git.head()
    result = submit(
        rig,
        dry_run=True,
        changes=[ChangeRequest(node=LIDAR, param="lidar_mode", value="2048x10")],
    )
    assert result.status is ChangeSetStatus.DRY_RUN
    assert result.plan is not None
    assert result.plan.files_to_write == ["params/lidar_top.yaml"]
    assert saved(rig, "lidar_mode") == "1024x10"
    assert rig.git.head() == before_head
    assert rig.applier.calls == []


def test_dry_run_plan_shows_unit_impact(rig: Rig):
    """restart 파라미터는 unit 과 그 안의 노드 전체를 영향 범위로 낸다.

    udp_dest 대신 sensor_hostname 을 쓴다. udp_dest 는 must_match_host_nic_ip
    검증이 붙어 있어 호스트 NIC 설정에 따라 결과가 달라진다.
    """
    result = submit(
        rig,
        dry_run=True,
        changes=[
            ChangeRequest(node=LIDAR, param="sensor_hostname", value="192.168.1.200")
        ],
    )
    assert result.plan is not None
    assert result.plan.restart_units == ["av-lidar-top.service"]
    assert LIDAR in result.plan.impacted_nodes


# ---------------------------------------------------------------- 성공 경로


def test_success_writes_commits_and_verifies(rig: Rig):
    rig.verifier.readback["lidar_mode"] = "2048x10"
    result = submit(
        rig,
        comment="라이다 해상도 상향 테스트",
        changes=[ChangeRequest(node=LIDAR, param="lidar_mode", value="2048x10")],
    )
    assert result.status is ChangeSetStatus.SUCCEEDED, result.message
    assert saved(rig, "lidar_mode") == "2048x10"
    assert result.commit is not None
    assert len(rig.git.log()) == 2
    assert all(r.success for r in result.results)


def test_commit_message_carries_audit_fields(rig: Rig):
    """사용자, 코멘트, 변경 내용이 commit 에 남아야 한다 (§10.3)."""
    rig.verifier.readback["lidar_mode"] = "2048x10"
    submit(
        rig,
        comment="해상도 상향",
        changes=[ChangeRequest(node=LIDAR, param="lidar_mode", value="2048x10")],
    )
    sha = rig.git.log()[0].sha
    body = rig.git._run("log", "-1", "--format=%B", sha)
    assert "해상도 상향" in body
    assert "user: engineer1" in body
    assert "lidar_mode" in body
    assert rig.git.log()[0].author == "engineer1"


def test_reconfigure_uses_driver_service(rig: Rig):
    rig.verifier.readback["lidar_mode"] = "2048x10"
    submit(rig, changes=[ChangeRequest(node=LIDAR, param="lidar_mode", value="2048x10")])
    assert ("reconfigure", ("/ouster/set_config", "2048x10")) in rig.applier.calls


def test_restart_param_restarts_the_unit(rig: Rig):
    rig.verifier.readback["sensor_hostname"] = "192.168.1.200"
    result = submit(
        rig,
        changes=[ChangeRequest(node=LIDAR, param="sensor_hostname", value="192.168.1.200")],
    )
    assert result.status is ChangeSetStatus.SUCCEEDED, result.message
    assert ("restart", ("av-lidar-top.service",)) in rig.applier.calls


# ---------------------------------------------------------------- 롤백


def test_readback_mismatch_rolls_back_with_revert(rig: Rig):
    """장치가 값을 거부했으면 되돌린다. 이력은 지워지지 않고 늘어난다 (§6.2)."""
    # 요청은 2048x10 인데 장치는 512x10 을 보고한다
    rig.verifier.readback["lidar_mode"] = "512x10"
    result = submit(
        rig, changes=[ChangeRequest(node=LIDAR, param="lidar_mode", value="2048x10")]
    )

    assert result.status is ChangeSetStatus.ROLLED_BACK, result.message
    assert result.rollback_commit is not None
    assert saved(rig, "lidar_mode") == "1024x10"        # 파일이 돌아왔다
    log = rig.git.log()
    assert len(log) == 3                                 # 초기 + 변경 + revert
    assert log[0].subject.startswith("Revert")
    failed = [r for r in result.results if not r.success]
    assert "장치가 값을 거부했거나 보정했다" in (failed[0].reason or "")


def test_readback_source_is_the_device_path_for_device_layer(rig: Rig):
    """device 계층은 노드가 아니라 장치에서 읽어야 한다 (§6.1, §4.4)."""
    rig.verifier.readback["lidar_mode"] = "2048x10"
    result = submit(
        rig, changes=[ChangeRequest(node=LIDAR, param="lidar_mode", value="2048x10")]
    )
    entry = next(r for r in result.results if r.param == "lidar_mode")
    assert entry.readback_source == "/ouster/get_metadata"


def test_driver_layer_reads_back_from_the_node(rig: Rig):
    rig.verifier.readback["sensor_hostname"] = "192.168.1.200"
    result = submit(
        rig,
        changes=[ChangeRequest(node=LIDAR, param="sensor_hostname", value="192.168.1.200")],
    )
    entry = next(r for r in result.results if r.param == "sensor_hostname")
    assert entry.readback_source == f"{LIDAR}/get_parameters"


def test_transient_apply_failure_rolls_back(rig: Rig):
    """적용이 일시적으로 실패하면 되돌리고, 되돌린 값으로 재적용한다 (§6.1-7)."""
    rig.applier.fail_on = "reconfigure"
    rig.applier.fail_times = 1
    result = submit(
        rig, changes=[ChangeRequest(node=LIDAR, param="lidar_mode", value="2048x10")]
    )
    assert result.status is ChangeSetStatus.ROLLED_BACK, result.message
    assert saved(rig, "lidar_mode") == "1024x10"
    # 롤백 재적용에서 예전 값으로 서비스를 다시 불렀다
    assert ("reconfigure", ("/ouster/set_config", "1024x10")) in rig.applier.calls


def test_persistent_apply_failure_is_dangerous(rig: Rig):
    """적용이 계속 실패하면 롤백 재적용도 실패한다. 위험 상태다 (§6.2)."""
    rig.applier.fail_on = "reconfigure"
    result = submit(
        rig, changes=[ChangeRequest(node=LIDAR, param="lidar_mode", value="2048x10")]
    )
    assert result.status is ChangeSetStatus.ROLLBACK_FAILED
    assert "수동 조치가 필요하다" in (result.message or "")
    # 파일은 revert 되었으므로 이력에는 남는다
    assert saved(rig, "lidar_mode") == "1024x10"


def test_readback_error_rolls_back(rig: Rig):
    rig.verifier.raise_on.add("lidar_mode")
    result = submit(
        rig, changes=[ChangeRequest(node=LIDAR, param="lidar_mode", value="2048x10")]
    )
    assert result.status is ChangeSetStatus.ROLLED_BACK
    assert saved(rig, "lidar_mode") == "1024x10"


def test_zero_hz_after_restart_rolls_back(rig: Rig):
    """값은 맞아도 데이터가 끊기면 반영 실패로 본다 (§6.2)."""
    rig.verifier.readback["sensor_hostname"] = "192.168.1.200"
    rig.verifier.hz = FakeHz(count=0, hz=0.0)
    result = submit(
        rig,
        changes=[ChangeRequest(node=LIDAR, param="sensor_hostname", value="192.168.1.200")],
    )
    assert result.status is ChangeSetStatus.ROLLED_BACK
    topic_result = next(r for r in result.results if r.param == "/ouster/points")
    assert topic_result.success is False


def test_readback_retries_while_node_is_coming_up(rig: Rig):
    """재시작 직후의 "응답 없음" 은 실패로 단정하지 않는다.

    그래프 정보는 재시작 직후에 믿을 수 없다. 사라진 노드의 서비스가 DDS
    discovery 에 남아 있어서, 한 번 호출해보고 실패하면 **정상 적용이 롤백된다.**
    2026-11-09 에 실제로 그렇게 됐다 (사유: "/ouster/get_config 호출 실패:
    5.0초 안에 응답 없음").
    """
    rig.verifier.readback["lidar_mode"] = "2048x10"
    rig.verifier.transient_failures["lidar_mode"] = 2  # 두 번 실패 후 응답

    result = submit(
        rig, changes=[ChangeRequest(node=LIDAR, param="lidar_mode", value="2048x10")]
    )

    assert result.status is ChangeSetStatus.SUCCEEDED, result.message
    assert rig.verifier.transient_failures["lidar_mode"] == 0
    # 재시도 중의 일시적 실패 사유가 성공한 항목에 남으면 안 된다
    entry = next(r for r in result.results if r.param == "lidar_mode")
    assert entry.reason is None, entry.reason


def test_topic_hz_retries_while_publisher_starts(rig: Rig):
    """발행이 늦게 시작되는 것도 기다린다.

    lifecycle 드라이버는 on_configure 에서 서비스, on_activate 에서 publisher 를
    만든다. 서비스가 응답하는 시점에 토픽은 아직 0 Hz 일 수 있다.
    """
    # 토픽 확인은 재시작한 unit 에만 돈다. udp_dest 가 apply: restart 다.
    rig.verifier.readback["udp_dest"] = "10.5.5.1"
    rig.verifier.transient_zero_hz = 2

    result = submit(
        rig, changes=[ChangeRequest(node=LIDAR, param="udp_dest", value="10.5.5.1")]
    )

    assert result.status is ChangeSetStatus.SUCCEEDED, result.message
    assert rig.verifier.transient_zero_hz == 0


def test_restart_waits_for_services_not_just_node_name(rig: Rig, monkeypatch):
    """노드 이름이 보여도 서비스가 뜨기 전에는 되읽기로 넘어가지 않는다.

    드라이버는 노드를 먼저 만들고 장치에 접속한 뒤에야 서비스를 띄운다.
    ouster_ros 에서 그 간격이 20초가 넘는다. 이름만 보고 넘어가면 되읽기가
    "서비스를 찾을 수 없다"로 실패해 **정상 적용이 롤백된다.** 실제로 겪은
    회귀라서 테스트로 묶어 둔다.
    """
    from av_config import changesets as cs

    monkeypatch.setattr(cs, "_NODE_RETURN_TIMEOUT", 1.0)
    rig.verifier.readback["udp_dest"] = "10.5.5.1"
    rig.discovery.unready_nodes = {LIDAR}

    result = submit(
        rig, changes=[ChangeRequest(node=LIDAR, param="udp_dest", value="10.5.5.1")]
    )

    assert result.status is not ChangeSetStatus.SUCCEEDED
    assert "서비스가 아직 뜨지 않은" in (result.message or ""), result.message


def test_restart_proceeds_once_services_are_up(rig: Rig, monkeypatch):
    """서비스가 떠 있으면 대기 없이 통과한다 (위 테스트의 대조군)."""
    from av_config import changesets as cs

    monkeypatch.setattr(cs, "_NODE_RETURN_TIMEOUT", 1.0)
    rig.verifier.readback["udp_dest"] = "10.5.5.1"
    rig.discovery.unready_nodes = set()

    result = submit(
        rig, changes=[ChangeRequest(node=LIDAR, param="udp_dest", value="10.5.5.1")]
    )
    assert result.status is ChangeSetStatus.SUCCEEDED, result.message


def test_rollback_failure_is_reported_as_dangerous(rig: Rig):
    """롤백도 실패하면 위험 상태로 표시한다 (§6.2)."""
    rig.verifier.readback["sensor_hostname"] = "다른 값"
    rig.applier.fail_on = None

    original_restart = rig.applier.restart_units
    calls = {"n": 0}

    async def flaky(units):
        calls["n"] += 1
        await original_restart(units)
        if calls["n"] >= 2:  # 롤백 재적용에서 실패
            raise ApplyError("롤백 재적용 실패")

    rig.applier.restart_units = flaky  # type: ignore[assignment]
    result = submit(
        rig,
        changes=[ChangeRequest(node=LIDAR, param="sensor_hostname", value="192.168.1.200")],
    )
    assert result.status is ChangeSetStatus.ROLLBACK_FAILED
    assert "수동 조치가 필요하다" in (result.message or "")


# ---------------------------------------------------------------- 동시성


def test_second_changeset_is_rejected_not_queued(rig: Rig):
    """change set 은 한 번에 하나만. 두 번째는 대기열이 아니라 거부다 (§6.3)."""

    async def scenario():
        async def slow_restart(units):
            await asyncio.sleep(0.2)

        rig.applier.restart_units = slow_restart  # type: ignore[assignment]
        rig.verifier.readback["sensor_hostname"] = "192.168.1.200"

        first = asyncio.create_task(
            rig.runner.submit(
                ChangeSetRequest(
                    changes=[
                        ChangeRequest(
                            node=LIDAR, param="sensor_hostname", value="192.168.1.200"
                        )
                    ]
                ),
                Role.ENGINEER,
                "a",
            )
        )
        await asyncio.sleep(0.05)
        with pytest.raises(ChangeSetBusy):
            await rig.runner.submit(
                ChangeSetRequest(
                    changes=[ChangeRequest(node=LIDAR, param="lidar_mode", value="2048x10")]
                ),
                Role.ENGINEER,
                "b",
            )
        await first

    asyncio.run(scenario())


def test_dry_run_is_allowed_while_busy(rig: Rig):
    """검증만 하는 요청은 lock 을 기다리지 않아도 된다."""

    async def scenario():
        async def slow_restart(units):
            await asyncio.sleep(0.2)

        rig.applier.restart_units = slow_restart  # type: ignore[assignment]
        rig.verifier.readback["sensor_hostname"] = "192.168.1.200"
        first = asyncio.create_task(
            rig.runner.submit(
                ChangeSetRequest(
                    changes=[
                        ChangeRequest(
                            node=LIDAR, param="sensor_hostname", value="192.168.1.200"
                        )
                    ]
                ),
                Role.ENGINEER,
                "a",
            )
        )
        await asyncio.sleep(0.05)
        result = await rig.runner.submit(
            ChangeSetRequest(
                dry_run=True,
                changes=[ChangeRequest(node=LIDAR, param="lidar_mode", value="2048x10")],
            ),
            Role.ENGINEER,
            "b",
        )
        assert result.status is ChangeSetStatus.DRY_RUN
        await first

    asyncio.run(scenario())


def test_history_records_each_changeset(rig: Rig):
    rig.verifier.readback["lidar_mode"] = "2048x10"
    result = submit(rig, changes=[ChangeRequest(node=LIDAR, param="lidar_mode", value="2048x10")])
    assert rig.runner.history[result.id].status is ChangeSetStatus.SUCCEEDED


def test_progress_events_are_emitted_in_order(rig: Rig):
    stages: list[str] = []

    async def capture(message):
        stages.append(message["stage"])

    rig.verifier.readback["lidar_mode"] = "2048x10"

    async def scenario():
        await rig.runner.submit(
            ChangeSetRequest(
                changes=[ChangeRequest(node=LIDAR, param="lidar_mode", value="2048x10")]
            ),
            Role.ENGINEER,
            "a",
            progress=capture,
        )

    asyncio.run(scenario())
    # 검증 -> 계획 -> 저장 -> 적용 -> 확인 -> 완료
    assert stages[0] == "validate"
    assert "plan" in stages
    assert "save" in stages
    assert "apply" in stages
    assert "verify" in stages
    assert stages[-1] == "done"


# ---------------------------------------------------------------- 전역 fan-out


def test_fan_out_collects_nodes_that_share_the_file(rig: Rig):
    """공유 파일을 고칠 때 그 파일을 쓰는 다른 unit 의 노드도 수집해야
    재시작 범위를 정확히 좁힐 수 있다 (§6.1 전역 변경 처리)."""
    CAMERA = "/camera_front/camera"
    PERCEPTION = "/perception/lidar_segmentation"

    # 카메라는 살아 있고 sensor_frame 을 선언하지 않는다.
    # perception 은 꺼져 있어 선언 여부를 알 수 없다.
    original = rig.discovery.collect_params
    rig.discovery.missing_nodes.add(PERCEPTION)

    async def collect(node: str):
        result = await original(node)
        if node == CAMERA:
            result.params = [
                p for p in result.params if p.name not in ("sensor_frame",)
            ]
        return result

    rig.discovery.collect_params = collect  # type: ignore[assignment]

    result = submit(
        rig,
        dry_run=True,
        changes=[ChangeRequest(node=LIDAR, param="sensor_frame", value="new_link")],
    )
    assert result.plan is not None
    assert result.plan.files_to_write == ["params/global.yaml"]
    assert result.plan.touches_global_file is True
    # 선언하지 않는 카메라는 제외된다
    assert "av-camera-front.service" not in result.plan.restart_units
    # 꺼져 있어 알 수 없는 perception 은 보수적으로 포함된다
    assert "av-perception.service" in result.plan.restart_units
