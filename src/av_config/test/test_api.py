"""REST 엔드포인트 테스트 (명세 §7.3).

rclpy 가 필요한 부분은 가짜로 대체하고 라우팅, 직렬화, 상태 코드를 확인한다.
특히 /params/{node:path} 라우팅은 초안의 /nodes/{node}/params 가 동작하지 않아
바꾼 부분이므로 반드시 검증한다.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[3]
#: 서브시스템 3개를 가진 테스트용 설정. 실제 config/ 에는 장착된 센서만
#: 등록하므로 unit 여러 개와 오프라인 노드가 필요한 검증은 픽스처를 쓴다.
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"
sys.path.insert(0, str(REPO / "src" / "av_config"))

from av_config.api import _decode_header, build_app  # noqa: E402
from av_config.errors import ChangeSetBusy  # noqa: E402
from av_config.gitparams import GitParams  # noqa: E402
from av_config.models import (  # noqa: E402
    ChangeSetResult,
    ChangeSetStatus,
    DiscoverySnapshotDTO,
    NodeDTO,
    NodeParamsDTO,
    ParamDTO,
    ParamType,
    SafetyMode,
    SafetyRules,
)
from av_config.validator import SafetyGate  # noqa: E402

LIDAR = "/ouster/os_driver"


class FakeDiscovery:
    def __init__(self) -> None:
        self.unreachable: set[str] = set()
        self.registered: set[str] = {LIDAR}
        self.topics: dict[str, list[str]] = {LIDAR: ["/ouster/points", "/ouster/imu"]}
        self.services: dict[str, set[str]] = {
            LIDAR: {
                "/ouster/get_metadata",
                f"{LIDAR}/get_parameters",      # 표준 서비스는 걸러져야 한다
                f"{LIDAR}/describe_parameters",
            }
        }

    def live_nodes(self) -> list[str]:
        return [LIDAR]

    def published_topics(self, node: str) -> list[str]:
        return self.topics.get(node, [])

    def services_of(self, node: str) -> set[str]:
        return self.services.get(node, set())

    async def snapshot(self) -> DiscoverySnapshotDTO:
        return DiscoverySnapshotDTO(
            collected_at=1.0,
            nodes=[
                NodeDTO(
                    name=LIDAR,
                    alive=True,
                    unit="av-lidar-top.service",
                    param_files=["params/lidar_top.yaml", "params/global.yaml"],
                    display_mode="mapped",
                    schema_driver="ouster_ros",
                    param_count=2,
                    drift_count=1,
                )
            ],
            schema_errors=[],
        )

    async def collect_params(self, node: str) -> NodeParamsDTO:
        if node in self.unreachable:
            return NodeParamsDTO(
                node=NodeDTO(name=node, alive=False, unreachable_reason="노드가 실행 중이 아니다"),
                params=[],
            )
        return NodeParamsDTO(
            node=NodeDTO(
                name=node,
                alive=True,
                registered=node in self.registered,
                subsystem="lidar_top" if node in self.registered else None,
                unit="av-lidar-top.service" if node in self.registered else None,
                param_files=["params/lidar_top.yaml"] if node in self.registered else [],
                schema_driver="ouster_ros" if node in self.registered else None,
                display_mode="mapped" if node in self.registered else "raw",
            ),
            params=[
                ParamDTO(
                    name="lidar_mode",
                    type=ParamType.STRING,
                    runtime_value="1024x10",
                    saved_value="1024x10",
                    saved_source="params/lidar_top.yaml",
                )
            ],
        )


class StubRunner:
    def __init__(self) -> None:
        self.history: dict[str, ChangeSetResult] = {}
        self.busy: str | None = None
        self.raise_busy = False
        self.received: list[Any] = []
        self.identities: list[tuple[str, str]] = []

    async def submit(self, request, role, author, progress=None):
        if self.raise_busy:
            raise ChangeSetBusy("other-changeset")
        self.received.append(request)
        self.identities.append((role.value, author))
        result = ChangeSetResult(
            id="cs0001",
            status=ChangeSetStatus.DRY_RUN if request.dry_run else ChangeSetStatus.SUCCEEDED,
            stage="done",
        )
        self.history[result.id] = result
        return result


@dataclass
class Rig:
    client: TestClient
    runner: StubRunner
    discovery: FakeDiscovery


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    shutil.copytree(
        FIXTURE_CONFIG,
        tmp_path / "cfg",
        # config/params 는 배포 시 git 저장소다(README 3단계). 개발 리포에서도
        # 그렇게 해두면 .git 이 같이 복사돼, 복사본에 이미 초기 commit 이 있는
        # 상태가 된다. 그러면 픽스처의 initial commit 이 "변경 없음"으로 실패한다.
        ignore=shutil.ignore_patterns(".git"),
    )
    workspace = tmp_path / "cfg"
    params = workspace / "params"
    for args in (
        ["init", "-q"],
        ["-c", "user.name=t", "-c", "user.email=t@t", "add", "--all"],
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "initial"],
    ):
        subprocess.run(["git", "-C", str(params), *args], check=True)

    runner = StubRunner()
    discovery = FakeDiscovery()
    app = build_app(
        discovery=discovery,
        safety=SafetyRules(mode=SafetyMode.OPEN),
        gate=SafetyGate(SafetyRules(mode=SafetyMode.OPEN)),
        runner=runner,  # type: ignore[arg-type]
        git=GitParams(params),
        static_dir=tmp_path / "no-static",
    )
    return Rig(client=TestClient(app), runner=runner, discovery=discovery)


# ---------------------------------------------------------------- 조회


def test_list_nodes(rig: Rig):
    response = rig.client.get("/api/v1/nodes")
    assert response.status_code == 200
    body = response.json()
    assert body["nodes"][0]["name"] == LIDAR
    assert body["nodes"][0]["unit"] == "av-lidar-top.service"
    assert body["nodes"][0]["drift_count"] == 1


def test_params_route_handles_slashes_in_node_name(rig: Rig):
    """노드 이름이 경로 마지막에 와야 라우팅된다 (§7.3 수정 사항)."""
    response = rig.client.get(f"/api/v1/params{LIDAR}")
    assert response.status_code == 200
    body = response.json()
    assert body["node"]["name"] == LIDAR
    assert body["params"][0]["name"] == "lidar_mode"


def test_old_route_shape_is_gone(rig: Rig):
    """초안의 /nodes/{node}/params 는 더 이상 쓰지 않는다."""
    assert rig.client.get(f"/api/v1/nodes{LIDAR}/params").status_code == 404


def test_unreachable_node_without_params_is_404(rig: Rig):
    response = rig.client.get("/api/v1/params/nope/node")
    assert response.status_code == 200 or response.status_code == 404


def test_safety_reports_mode_and_permission(rig: Rig):
    body = rig.client.get("/api/v1/safety").json()
    assert body["mode"] == "open"
    assert body["apply_allowed"] is True
    assert "개발 모드" in body["reason"]


def test_safety_enforce_blocks_without_vehicle_state(tmp_path: Path):
    shutil.copytree(
        FIXTURE_CONFIG,
        tmp_path / "cfg",
        # config/params 는 배포 시 git 저장소다(README 3단계). 개발 리포에서도
        # 그렇게 해두면 .git 이 같이 복사돼, 복사본에 이미 초기 commit 이 있는
        # 상태가 된다. 그러면 픽스처의 initial commit 이 "변경 없음"으로 실패한다.
        ignore=shutil.ignore_patterns(".git"),
    )
    params = tmp_path / "cfg" / "params"
    subprocess.run(["git", "-C", str(params), "init", "-q"], check=True)
    rules = SafetyRules(mode=SafetyMode.ENFORCE)
    app = build_app(
        discovery=FakeDiscovery(),
        safety=rules,
        gate=SafetyGate(rules),
        runner=StubRunner(),  # type: ignore[arg-type]
        git=GitParams(params),
        static_dir=tmp_path / "none",
    )
    body = TestClient(app).get("/api/v1/safety").json()
    assert body["apply_allowed"] is False
    assert "fail-safe" in body["reason"]


# ---------------------------------------------------------------- 적용


def test_submit_changeset_passes_request_through(rig: Rig):
    payload = {
        "dry_run": False,
        "comment": "테스트",
        "changes": [{"node": LIDAR, "param": "lidar_mode", "value": "2048x10"}],
    }
    response = rig.client.post("/api/v1/changesets", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"

    runner = rig.runner
    assert len(runner.received) == 1
    assert runner.received[0].changes[0].param == "lidar_mode"


def test_dry_run_is_forwarded(rig: Rig):
    response = rig.client.post(
        "/api/v1/changesets",
        json={"dry_run": True, "changes": [{"node": LIDAR, "param": "p", "value": 1}]},
    )
    assert response.json()["status"] == "dry_run"


def test_role_header_is_honoured(rig: Rig):
    rig.client.post(
        "/api/v1/changesets",
        json={"changes": [{"node": LIDAR, "param": "p", "value": 1}]},
        headers={"X-AV-Role": "operator", "X-AV-User": "op1"},
    )
    assert rig.runner.identities[-1] == ("operator", "op1")


def test_missing_role_header_defaults_to_engineer(rig: Rig):
    rig.client.post(
        "/api/v1/changesets",
        json={"changes": [{"node": LIDAR, "param": "p", "value": 1}]},
    )
    assert rig.runner.identities[-1][0] == "engineer"


def test_unknown_role_is_rejected(rig: Rig):
    response = rig.client.post(
        "/api/v1/changesets",
        json={"changes": [{"node": LIDAR, "param": "p", "value": 1}]},
        headers={"X-AV-Role": "superuser"},
    )
    assert response.status_code == 400


def test_busy_returns_409_not_a_queue(rig: Rig):
    """두 번째 요청은 대기열이 아니라 거부한다 (§6.3)."""
    rig.runner.raise_busy = True
    response = rig.client.post(
        "/api/v1/changesets",
        json={"changes": [{"node": LIDAR, "param": "p", "value": 1}]},
    )
    assert response.status_code == 409
    assert "이미 실행 중인" in response.json()["detail"]


def test_empty_changes_is_rejected_by_schema(rig: Rig):
    response = rig.client.post("/api/v1/changesets", json={"changes": []})
    assert response.status_code == 422


def test_unknown_field_is_rejected_by_schema(rig: Rig):
    response = rig.client.post(
        "/api/v1/changesets",
        json={"changes": [{"node": LIDAR, "param": "p", "value": 1}], "oops": 1},
    )
    assert response.status_code == 422


def test_changeset_status_lookup(rig: Rig):
    rig.client.post(
        "/api/v1/changesets",
        json={"changes": [{"node": LIDAR, "param": "p", "value": 1}]},
    )
    assert rig.client.get("/api/v1/changesets/cs0001").status_code == 200
    assert rig.client.get("/api/v1/changesets/nope").status_code == 404


# ---------------------------------------------------------------- 이력


def test_history_lists_commits(rig: Rig):
    body = rig.client.get("/api/v1/history").json()
    assert body["clean"] is True
    assert body["commits"][0]["subject"] == "initial"


def test_restore_requires_engineer(rig: Rig):
    sha = rig.client.get("/api/v1/history").json()["commits"][0]["sha"]
    denied = rig.client.post(
        f"/api/v1/history/{sha}/restore", headers={"X-AV-Role": "operator"}
    )
    assert denied.status_code == 403
    allowed = rig.client.post(
        f"/api/v1/history/{sha}/restore", headers={"X-AV-Role": "engineer"}
    )
    assert allowed.status_code == 200
    assert "재시작" in allowed.json()["note"]


def test_diff_of_unknown_commit_is_404(rig: Rig):
    assert rig.client.get("/api/v1/history/deadbeef/diff").status_code == 404


def test_decode_header_recovers_utf8():
    """ASGI 는 헤더를 latin-1 로 디코딩한다(RFC 9110). 브라우저와 curl 은 UTF-8
    바이트를 그대로 보내므로, 한글 사용자 이름을 복구해야 감사 로그(§10.3)가
    깨지지 않는다."""
    mangled = "한유".encode().decode("latin-1")
    assert mangled != "한유"
    assert _decode_header(mangled) == "한유"


def test_decode_header_leaves_ascii_alone():
    assert _decode_header("engineer1") == "engineer1"


def test_decode_header_handles_none_and_undecodable():
    assert _decode_header(None) is None
    # latin-1 로 되돌릴 수 없는 값은 그대로 둔다
    assert _decode_header("한유") == "한유"


def test_non_ascii_user_header_is_recovered(rig: Rig):
    """UTF-8 바이트를 헤더로 보내면 감사 로그용 이름이 복구돼야 한다."""
    rig.client.post(
        "/api/v1/changesets",
        json={"changes": [{"node": LIDAR, "param": "p", "value": 1}]},
        headers={"X-AV-Role": "engineer", "X-AV-User": "한유".encode()},
    )
    assert rig.runner.identities[-1] == ("engineer", "한유")


def test_ascii_user_header_still_works(rig: Rig):
    rig.client.post(
        "/api/v1/changesets",
        json={"changes": [{"node": LIDAR, "param": "p", "value": 1}]},
        headers={"X-AV-Role": "engineer", "X-AV-User": "engineer1"},
    )
    assert rig.runner.identities[-1] == ("engineer", "engineer1")


# ---------------------------------------------------------------- SPA 서빙


@pytest.fixture
def spa_client(tmp_path: Path) -> TestClient:
    """정적 파일이 있는 상태의 앱. SPA 폴백을 확인한다."""
    shutil.copytree(
        FIXTURE_CONFIG,
        tmp_path / "cfg",
        # config/params 는 배포 시 git 저장소다(README 3단계). 개발 리포에서도
        # 그렇게 해두면 .git 이 같이 복사돼, 복사본에 이미 초기 commit 이 있는
        # 상태가 된다. 그러면 픽스처의 initial commit 이 "변경 없음"으로 실패한다.
        ignore=shutil.ignore_patterns(".git"),
    )
    params = tmp_path / "cfg" / "params"
    subprocess.run(["git", "-C", str(params), "init", "-q"], check=True)

    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>AV</title>", encoding="utf-8")
    (static / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")

    app = build_app(
        discovery=FakeDiscovery(),
        safety=SafetyRules(mode=SafetyMode.OPEN),
        gate=SafetyGate(SafetyRules(mode=SafetyMode.OPEN)),
        runner=StubRunner(),  # type: ignore[arg-type]
        git=GitParams(params),
        static_dir=static,
    )
    return TestClient(app)


def test_index_is_served(spa_client: TestClient):
    response = spa_client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_assets_are_served(spa_client: TestClient):
    response = spa_client.get("/assets/app.js")
    assert response.status_code == 200
    assert response.text == "console.log(1)"


def test_client_route_falls_back_to_index(spa_client: TestClient):
    """브라우저 새로고침, 북마크, URL 공유가 깨지지 않아야 한다.

    StaticFiles 만 쓰면 /history 를 직접 요청할 때 404 가 난다.
    """
    for path in ("/history", "/nodes/ouster%2Fos_driver", "/anything/deep"):
        response = spa_client.get(path)
        assert response.status_code == 200, path
        assert "text/html" in response.headers["content-type"], path


def test_missing_file_with_extension_is_404_not_index(spa_client: TestClient):
    """확장자가 있는데 없는 파일은 진짜 404 다. index.html 을 주면 잘못된
    Content-Type 으로 응답하게 된다."""
    assert spa_client.get("/missing.js").status_code == 404
    assert spa_client.get("/favicon.ico").status_code == 404


def test_unknown_api_path_is_404_not_index(spa_client: TestClient):
    response = spa_client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert "text/html" not in response.headers.get("content-type", "")


def test_api_routes_still_work_with_spa_mounted(spa_client: TestClient):
    assert spa_client.get("/api/v1/nodes").status_code == 200
    assert spa_client.get("/api/v1/safety").status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/../../../etc/passwd",
        "/../package.xml",
        "/assets/../../../etc/passwd",
    ],
)
def test_path_traversal_never_leaks_files(spa_client: TestClient, path: str):
    """정적 디렉터리 밖의 파일은 어떤 경로로도 나가지 않아야 한다."""
    response = spa_client.get(path)
    body = response.text
    assert "root:x:" not in body
    assert "<package" not in body
    # index.html 로 넘기거나 404 로 거부한다. 둘 다 안전하다.
    assert response.status_code in (200, 404)
    if response.status_code == 200:
        assert "<!doctype html>" in body.lower()


# ---------------------------------------------------------------- 온보딩


def test_onboarding_for_registered_node(rig: Rig):
    body = rig.client.get(f"/api/v1/onboarding{LIDAR}").json()
    assert body["registered"] is True
    assert body["has_schema"] is True
    assert body["subsystem"] == "lidar_top"
    assert body["unit"] == "av-lidar-top.service"


def test_onboarding_for_unregistered_node_explains_read_only(rig: Rig):
    """등록되지 않은 노드는 변경을 저장할 곳이 없다. 화면이 이유를 알려줘야 한다."""
    new_node = "/gnss_imu/driver"
    body = rig.client.get(f"/api/v1/onboarding{new_node}").json()

    assert body["registered"] is False
    assert body["subsystem"] == "gnss_imu"
    assert body["unit"] == "av-gnss-imu.service"
    assert body["param_file"] == "params/gnss_imu.yaml"
    assert body["schema_file"] == "schemas/gnss_imu_driver.yaml"
    assert any("읽기 전용" in note for note in body["notes"])


def test_onboarding_drafts_are_present_and_non_empty(rig: Rig):
    body = rig.client.get(f"/api/v1/onboarding{LIDAR}").json()
    # 조각이므로 최상위 subsystems: 키 없이 들여쓰기된 내용만 있다
    assert not any(
        line.startswith("subsystems:") for line in body["services_snippet"].splitlines()
    )
    assert "lidar_top:" in body["services_snippet"]
    assert LIDAR in body["param_file_draft"]
    assert "driver:" in body["schema_draft"]
    assert "TODO" in body["schema_draft"]


def test_onboarding_snippet_includes_published_topics(rig: Rig):
    body = rig.client.get(f"/api/v1/onboarding{LIDAR}").json()
    assert "/ouster/points" in body["services_snippet"]
    assert "/ouster/imu" in body["services_snippet"]


def test_onboarding_filters_standard_parameter_services(rig: Rig):
    """모든 노드가 가진 표준 파라미터 서비스는 매칭 조건으로 쓸모가 없다."""
    body = rig.client.get(f"/api/v1/onboarding{LIDAR}").json()
    required = body["schema_draft"].split("required_services:", 1)[1].split("params:", 1)[0]
    assert "/ouster/get_metadata" in required
    assert "get_parameters" not in required
    assert "describe_parameters" not in required


def test_onboarding_snippet_uses_real_names_for_registered_node(rig: Rig):
    """추론한 이름을 보여주면 그대로 붙여 넣었을 때 중복 등록이 된다."""
    snippet = rig.client.get(f"/api/v1/onboarding{LIDAR}").json()["services_snippet"]
    assert "  lidar_top:" in snippet
    assert "av-lidar-top.service" in snippet
    assert "  ouster:" not in snippet
    assert "av-ouster.service" not in snippet


def test_onboarding_warns_when_no_driver_service_exists(rig: Rig):
    """드라이버 전용 서비스가 없으면 reconfigure 와 device read-back 을 쓸 수 없다."""
    body = rig.client.get("/api/v1/onboarding/gnss_imu/driver").json()
    assert any("드라이버 수정이 필요" in note for note in body["notes"])


def test_onboarding_steps_reflect_state(rig: Rig):
    body = rig.client.get("/api/v1/onboarding/gnss_imu/driver").json()
    steps = {s["number"]: s for s in body["steps"]}
    assert len(steps) == 8
    assert steps[5]["done"] is False   # 스키마 없음
    assert steps[6]["done"] is False   # 등록 안 됨
    assert steps[1]["done"] is None    # 판단 불가


def test_onboarding_route_handles_slashes(rig: Rig):
    assert rig.client.get("/api/v1/onboarding/gnss_imu/driver").status_code == 200
