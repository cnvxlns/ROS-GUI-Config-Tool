#!/usr/bin/env python3
"""컨테이너 없이 UI 를 띄우는 개발용 백엔드.

실제 FastAPI 앱(av_config.api.build_app)과 실제 오케스트레이터
(av_config.changesets.ChangeSetRunner)를 그대로 쓰고, rclpy 가 필요한 세 계층만
가짜로 끼운다.

  진짜로 동작하는 것: parameter file 머지, 스키마 검증, change set 검증, 계획 수립,
                      atomic write, git commit/revert, REST/WebSocket
  가짜인 것:          노드 탐색, set_parameters, 드라이버 서비스, systemctl,
                      read-back, 토픽 Hz

그래서 UI 흐름과 트랜잭션 동작을 전부 눌러볼 수 있다. 다만 실제 센서 반영은
확인할 수 없으므로, 컨테이너 검증을 대체하지는 않는다.

    python tools/mock_backend.py [--port 8080] [--keep]

config/ 를 임시 디렉터리로 복사해 쓴다. 따라서 리포의 설정 파일은 바뀌지 않는다.
--keep 을 주면 종료 후에도 임시 디렉터리를 남긴다.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ruamel.yaml import YAML

REPO = Path(__file__).resolve().parents[1]
#: UI 데모는 서브시스템 3개를 보여준다. 실제 config/ 에는 장착된 센서만
#: 등록되므로 픽스처를 쓴다 (src/av_config/test/fixtures/config/README.md).
FIXTURE_CONFIG = REPO / "src" / "av_config" / "test" / "fixtures" / "config"
sys.path.insert(0, str(REPO / "src" / "av_config"))

from av_config.api import build_app  # noqa: E402
from av_config.changesets import ChangeSetRunner  # noqa: E402
from av_config.gitparams import GitParams, enclosing_worktree  # noqa: E402
from av_config.models import (  # noqa: E402
    NodeDTO,
    NodeParamsDTO,
    ParamDTO,
    ParamType,
    SafetyRules,
    ValueRange,
)
from av_config.paramdto import build_param_dto, undeclared_param_dto  # noqa: E402
from av_config.schema import SchemaRegistry  # noqa: E402
from av_config.services_map import ServicesMap  # noqa: E402
from av_config.store import ParamStore, values_equal  # noqa: E402
from av_config.validator import SafetyGate  # noqa: E402

_log = logging.getLogger("mock_backend")

# ---------------------------------------------------------------------------
# 가짜 런타임 상태. 노드가 실제로 보고할 값들.
# lidar_mode 를 저장값과 다르게 둬서 drift 표시를 바로 볼 수 있게 한다.
# ---------------------------------------------------------------------------

RUNTIME: dict[str, dict[str, tuple[Any, int]]] = {
    # ouster-ros 의 실제 파라미터다. 이름과 기본값은
    # https://github.com/ouster-lidar/ouster-ros 의 config/driver_params.yaml 에서 가져왔다.
    # 이 드라이버는 배열 타입 파라미터를 쓰지 않는다. 9종 렌더링 확인은
    # frontend 의 ValueEditor 컴포넌트 테스트가 담당한다.
    "/ouster/os_driver": {
        "sensor_hostname": ("192.168.1.100", ParamType.STRING),
        "udp_dest": ("192.168.1.1", ParamType.STRING),
        "lidar_mode": ("512x10", ParamType.STRING),   # 저장값 1024x10 -> drift
        "timestamp_mode": ("TIME_FROM_INTERNAL_OSC", ParamType.STRING),
        "udp_profile_lidar": ("RNG19_RFL8_SIG16_NIR16", ParamType.STRING),
        "multipurpose_io_mode": ("OFF", ParamType.STRING),
        "operating_mode": ("NORMAL", ParamType.STRING),
        "point_type": ("original", ParamType.STRING),
        "proc_mask": ("IMU|PCL|SCAN|IMG|RAW|TLM", ParamType.STRING),
        "sensor_frame": ("lidar_top_link", ParamType.STRING),
        "lidar_frame": ("os_lidar", ParamType.STRING),
        "imu_frame": ("os_imu", ParamType.STRING),
        "lidar_port": (0, ParamType.INTEGER),
        "imu_port": (0, ParamType.INTEGER),
        "azimuth_window_start": (0, ParamType.INTEGER),
        "azimuth_window_end": (360000, ParamType.INTEGER),
        "columns_per_packet": (0, ParamType.INTEGER),
        "ptp_utc_tai_offset": (-37.0, ParamType.DOUBLE),
        "min_range": (0.0, ParamType.DOUBLE),
        "max_range": (1000.0, ParamType.DOUBLE),
        "pub_static_tf": (True, ParamType.BOOL),
        "organized": (True, ParamType.BOOL),
        "destagger": (True, ParamType.BOOL),
        "nan_is_inf": (False, ParamType.BOOL),
        "persist_config": (False, ParamType.BOOL),
        "use_sim_time": (False, ParamType.BOOL),
    },
    # 아래는 실물 확인을 하지 않은 가상의 노드다. 화면 흐름 확인용이다.
    "/camera_front/camera": {
        "serial_no": ("", ParamType.STRING),
        "rgb_camera.profile": ("1280x720x30", ParamType.STRING),
        "enable_depth": (False, ParamType.BOOL),
        "camera_frame": ("camera_front_link", ParamType.STRING),
        "use_sim_time": (False, ParamType.BOOL),
    },
    # perception 은 일부러 꺼 둔다 -> 오프라인 편집 화면 확인용
}

# --new-sensor 로 켠다. services.yaml 에도 schemas/ 에도 없는 노드를 하나 띄워
# 온보딩 화면을 확인할 수 있다 (명세 §12.2).
NEW_SENSOR_NODE = "/gnss_imu/driver"
NEW_SENSOR_PARAMS = {
    "serial_port": ("/dev/ttyUSB0", ParamType.STRING),
    "baudrate": (460800, ParamType.INTEGER),
    "frame_id": ("gnss_link", ParamType.STRING),
    "rtk_enabled": (True, ParamType.BOOL),
    "imu_rate_hz": (200.0, ParamType.DOUBLE),
    "antenna_offset": ([0.0, 0.0, 1.2], ParamType.DOUBLE_ARRAY),
    "firmware": ("2.1.0", ParamType.STRING),
}
NEW_SENSOR_TOPICS = ["/gnss_imu/fix", "/gnss_imu/imu"]

# ouster-ros 는 ParameterDescriptor 를 채우지 않으므로 실제로는 범위가 비어 있다.
# 슬라이더 렌더링을 확인할 수 있도록 두 개만 범위를 넣어 둔다.
RANGES = {
    "azimuth_window_end": ("int", ValueRange(from_value=0, to_value=360000, step=1000)),
    "min_range": ("float", ValueRange(from_value=0.0, to_value=100.0, step=0.5)),
}
READ_ONLY: set[str] = set()
DOWNSTREAM = {"/ouster/os_driver": ["/perception/lidar_segmentation"]}
TOPICS = {
    "/ouster/os_driver": ["/ouster/points", "/ouster/imu"],
    "/camera_front/camera": ["/camera_front/image_raw"],
}
SERVICES = {
    # 실제 ouster-ros 가 만드는 서비스 (os_sensor_node.cpp / os_sensor_node_base.cpp)
    "/ouster/os_driver": {
        "/ouster/get_metadata",
        "/ouster/get_config",
        "/ouster/set_config",
        "/ouster/reset",
    },
}


# ---------------------------------------------------------------------------
# 가짜 ROS 계층
# ---------------------------------------------------------------------------


class FakeDiscovery:
    def __init__(self, store: ParamStore, schemas: SchemaRegistry, services: ServicesMap):
        self._store = store
        self._schemas = schemas
        self._services = services

    def live_nodes(self) -> list[str]:
        return sorted(RUNTIME)

    def published_topics(self, node: str) -> list[str]:
        return sorted(TOPICS.get(node, []))

    def services_of(self, node: str) -> set[str]:
        # 실제 Discovery 는 표준 파라미터 서비스도 돌려준다. 온보딩이 그것을
        # 걸러내는지 확인할 수 있도록 똑같이 섞어 둔다.
        standard = {f"{node}/{n}" for n in ("get_parameters", "describe_parameters")}
        return SERVICES.get(node, set()) | standard

    async def collect_params(self, node: str) -> NodeParamsDTO:
        alive = node in RUNTIME
        runtime = RUNTIME.get(node, {})
        files = self._services.param_files_of(node)
        # 캐시를 비워 방금 쓴 파일이 반영되게 한다
        for rel in files:
            self._store._cache.pop(rel, None)
        saved = self._store.saved_values(node, files)
        schema = self._schemas.match(node, self.services_of(node) if alive else None)
        found = self._services.subsystem_of(node)

        dto = NodeDTO(
            name=node,
            alive=alive,
            registered=found is not None,
            subsystem=found[0] if found else None,
            unit=found[1].unit if found else None,
            param_files=list(files),
            display_mode="mapped" if schema else "raw",
            schema_driver=schema.driver if schema else None,
            downstream=DOWNSTREAM.get(node, []),
            unreachable_reason=None if alive else "노드가 실행 중이 아니다",
        )

        params: list[ParamDTO] = []
        for name in sorted(set(runtime) | set(saved)):
            value, ptype = runtime.get(name, (None, ParamType.NOT_SET))
            entry = saved.get(name)
            if name not in runtime and entry is not None:
                params.append(undeclared_param_dto(name, entry))
                continue
            kind, rng = RANGES.get(name, (None, None))
            # 실제 Discovery 와 같은 조립 함수를 쓴다. 각자 조립하면 화면에서 본
            # 것이 실제 동작과 달라진다.
            params.append(
                build_param_dto(
                    name=name,
                    descriptor=SimpleNamespace(
                        type=ptype,
                        description="",   # ouster-ros 는 descriptor 를 채우지 않는다
                        additional_constraints="",
                        read_only=name in READ_ONLY,
                        dynamic_typing=False,
                        integer_range=[rng] if kind == "int" else [],
                        floating_point_range=[rng] if kind == "float" else [],
                    ),
                    runtime_value=value,
                    saved=entry,
                    param_schema=self._schemas.param_schema(schema, name),
                    reachable=alive,
                )
            )

        dto.param_count = len(params)
        dto.drift_count = sum(1 for p in params if p.drift)
        return NodeParamsDTO(node=dto, params=params)

    async def snapshot(self):
        import time

        from av_config.models import DiscoverySnapshotDTO

        registered = set(self._services.all_nodes())
        targets = sorted(registered | set(RUNTIME))
        nodes = [(await self.collect_params(n)).node for n in targets]
        return DiscoverySnapshotDTO(
            collected_at=time.time(), nodes=nodes, schema_errors=self._schemas.errors
        )


class FakeApplier:
    """적용된 값을 RUNTIME 에 반영한다. 그래서 적용 후 화면이 실제로 바뀐다."""

    def __init__(self) -> None:
        self.log: list[str] = []

    async def set_parameters(self, node: str, payload: dict[str, Any]) -> dict[str, Any]:
        for name, (ptype, value) in payload.items():
            RUNTIME.setdefault(node, {})[name] = (value, ptype)
        self.log.append(f"set_parameters {node} {sorted(payload)}")
        _log.info("[가짜] %s", self.log[-1])
        return dict.fromkeys(payload)

    async def call_driver_service(self, ref: Any, value: Any) -> None:
        self.log.append(f"{ref.service} <- {value!r}")
        _log.info("[가짜] 드라이버 서비스 %s", self.log[-1])

    async def restart_units(self, units: list[str]) -> None:
        self.log.append(f"systemctl restart {' '.join(units)}")
        _log.info("[가짜] %s", self.log[-1])


class FakeVerifier:
    """저장된 YAML 값을 그대로 되읽는다. 즉 정상 반영된 센서를 흉내낸다."""

    def __init__(self, store: ParamStore, services: ServicesMap) -> None:
        self._store = store
        self._services = services

    async def read_back(self, node: str, param: ParamDTO, ref: Any = None):
        files = self._services.param_files_of(node)
        for rel in files:
            self._store._cache.pop(rel, None)
        saved = self._store.saved_values(node, files)
        entry = saved.get(param.name)
        # 재시작 계열은 노드가 YAML 을 다시 읽으므로 RUNTIME 도 맞춰 준다
        if entry is not None and node in RUNTIME and param.name in RUNTIME[node]:
            RUNTIME[node][param.name] = (entry.value, RUNTIME[node][param.name][1])

        @dataclass
        class R:
            value: Any
            source: str

        return R(
            value=entry.value if entry else None,
            source=ref.service if ref is not None else f"{node}/get_parameters",
        )

    @staticmethod
    def matches(expected: Any, actual: Any) -> bool:
        return values_equal(expected, actual)

    async def measure_hz(self, topic: str, duration: float = 2.0):
        @dataclass
        class S:
            topic: str
            count: int
            duration: float
            hz: float

        return S(topic=topic, count=60, duration=duration, hz=30.0)


# ---------------------------------------------------------------------------


def ensure_git_repo(params: Path) -> None:
    """parameter file 디렉터리를 git 저장소로 만든다. 이력과 롤백이 여기 의존한다."""
    for args in (
        ["init", "-q"],
        ["-c", "user.name=mock", "-c", "user.email=mock@localhost", "add", "--all"],
        [
            "-c", "user.name=mock", "-c", "user.email=mock@localhost",
            "commit", "-q", "-m", "initial: mock backend 기준 상태",
        ],
    ):
        subprocess.run(["git", "-C", str(params), *args], check=True)


def strip_configuration(workspace: Path) -> None:
    """설정을 전부 비운다. 갓 배포한 상태를 흉내낸다."""
    (workspace / "services.yaml").write_text(
        "# 아무것도 등록되지 않은 상태\nsubsystems: {}\n", encoding="utf-8"
    )
    for schema in (workspace / "schemas").glob("*.yaml"):
        schema.unlink()
    _log.info("설정을 비웠다: services.yaml 등록 0건, 스키마 0개")


def prepare_workspace(keep: bool) -> Path:
    """config/ 를 임시 디렉터리로 복사하고 params/ 를 git 저장소로 만든다."""
    tmp = Path(tempfile.mkdtemp(prefix="av_config_mock_"))
    workspace = tmp / "config"
    shutil.copytree(
        FIXTURE_CONFIG,
        workspace,
        # config/params 는 배포 시 git 저장소다(README 3단계). 개발 리포에서도
        # 그렇게 해두면 .git 이 같이 복사돼, 복사본에 이미 초기 commit 이 있는
        # 상태가 된다. 그러면 픽스처의 initial commit 이 "변경 없음"으로 실패한다.
        ignore=shutil.ignore_patterns(".git"),
    )
    ensure_git_repo(workspace / "params")
    _log.info("임시 설정 루트: %s%s", workspace, "" if keep else " (종료 시 삭제)")
    return workspace


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--keep", action="store_true", help="임시 설정 루트를 남긴다")
    parser.add_argument(
        "--config-root",
        type=Path,
        help="임시 복사본 대신 이 설정 디렉터리를 직접 쓴다. 설정을 고쳐 가며 "
        "결과를 바로 확인할 때 유용하다. 이 디렉터리는 실제로 수정되므로 git 저장소 "
        "밖이어야 한다 (tools/init_config_root.py 로 만든다)",
    )
    parser.add_argument(
        "--bare",
        action="store_true",
        help="services.yaml 을 비우고 스키마를 제거한다. 아무것도 설정하지 않은 "
        "상태에서 코드베이스가 스스로 무엇을 보여주는지 확인할 때 쓴다",
    )
    parser.add_argument(
        "--new-sensor",
        action="store_true",
        help="services.yaml 에 없는 신규 센서 노드를 하나 띄워 온보딩 화면을 확인한다",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s"
    )

    if args.new_sensor:
        RUNTIME[NEW_SENSOR_NODE] = dict(NEW_SENSOR_PARAMS)
        TOPICS[NEW_SENSOR_NODE] = list(NEW_SENSOR_TOPICS)
        READ_ONLY.add("firmware")
        _log.info(
            "신규 센서 데모: %s (services.yaml 미등록 상태)", NEW_SENSOR_NODE
        )

    if args.config_root:
        workspace = args.config_root.expanduser().resolve()
        worktree = enclosing_worktree(workspace)
        if worktree is not None:
            print(
                f"{workspace} 가 git 저장소 {worktree} 안에 있다. 리포의 설정은 템플릿이라 "
                "직접 쓰지 않는다. tools/init_config_root.py 로 복사본을 만들어 지정하라.",
                file=sys.stderr,
            )
            raise SystemExit(2)
        if not (workspace / "services.yaml").is_file():
            print(f"{workspace}/services.yaml 이 없다", file=sys.stderr)
            raise SystemExit(2)
        params = workspace / "params"
        if not (params / ".git").is_dir():
            ensure_git_repo(params)
        _log.info("설정 루트(직접 사용): %s", workspace)
    else:
        workspace = prepare_workspace(args.keep)
    if args.bare:
        strip_configuration(workspace)
    try:
        services = ServicesMap.load(workspace / "services.yaml")
        schemas = SchemaRegistry.load_dir(workspace / "schemas")
        store = ParamStore(workspace)
        git = GitParams(workspace / "params")
        with (workspace / "rules" / "safety.yaml").open(encoding="utf-8") as fh:
            safety = SafetyRules.model_validate(YAML(typ="safe").load(fh))
        gate = SafetyGate(safety)
        discovery = FakeDiscovery(store, schemas, services)

        runner = ChangeSetRunner(
            discovery=discovery,
            services=services,
            schemas=schemas,
            store=store,
            git=git,
            applier=FakeApplier(),
            verifier=FakeVerifier(store, services),
            gate=gate,
        )

        static_dir = REPO / "src" / "av_config" / "av_config" / "static"
        app = build_app(discovery, safety, gate, runner, git, static_dir)

        import uvicorn

        print()
        print(f"  UI       http://{args.host}:{args.port}/")
        print(f"  API 문서 http://{args.host}:{args.port}/docs")
        print(f"  설정 루트 {workspace}")
        if args.new_sensor:
            print(f"  신규 센서  {NEW_SENSOR_NODE} (미등록 -> 온보딩 화면)")
        print()
        if not static_dir.is_dir():
            print("  경고: frontend 가 빌드되지 않았다. cd frontend && npm run build")
            print()

        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    finally:
        if not args.keep and not args.config_root:
            shutil.rmtree(workspace.parent, ignore_errors=True)


if __name__ == "__main__":
    main()
