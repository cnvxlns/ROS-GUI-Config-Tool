"""parameter file 쓰기와 git 이력 테스트.

ROS 2 가 필요 없으므로 호스트에서 돌 수 있다.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
#: 서브시스템 3개를 가진 테스트용 설정. 실제 config/ 에는 장착된 센서만
#: 등록하므로 unit 여러 개와 오프라인 노드가 필요한 검증은 픽스처를 쓴다.
FIXTURE_CONFIG = Path(__file__).resolve().parent / "fixtures" / "config"
sys.path.insert(0, str(REPO / "src" / "av_config"))

from av_config.gitparams import GitParams  # noqa: E402
from av_config.store import ParamStore, ParamWriter  # noqa: E402

NODE = "/ouster/os_driver"
FILES = ["params/lidar_top.yaml", "params/global.yaml"]


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """리포의 config/ 를 복사해 격리된 작업 공간을 만든다."""
    shutil.copytree(
        FIXTURE_CONFIG,
        tmp_path / "cfg",
        # config/params 는 배포 시 git 저장소다(README 3단계). 개발 리포에서도
        # 그렇게 해두면 .git 이 같이 복사돼, 복사본에 이미 초기 commit 이 있는
        # 상태가 된다. 그러면 픽스처의 initial commit 이 "변경 없음"으로 실패한다.
        ignore=shutil.ignore_patterns(".git"),
    )
    return tmp_path / "cfg"


@pytest.fixture
def git_repo(workspace: Path) -> GitParams:
    params = workspace / "params"
    subprocess.run(["git", "-C", str(params), "init", "-q"], check=True)
    subprocess.run(
        ["git", "-C", str(params), "-c", "user.name=t", "-c", "user.email=t@t",
         "add", "--all"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(params), "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-q", "-m", "initial"],
        check=True,
    )
    return GitParams(params)


# ---------------------------------------------------------------- 쓰기 대상 선택


def test_writes_to_the_file_that_currently_wins(workspace: Path):
    """뒤 파일이 앞을 덮으므로 이기고 있는 파일에 써야 실제로 반영된다."""
    writer = ParamWriter(ParamStore(workspace))
    # sensor_frame 은 global.yaml 에서 이기고 있다
    targets = writer.plan_targets(NODE, {"sensor_frame": "new_link"}, FILES)
    assert len(targets) == 1
    assert targets[0].rel_path == "params/global.yaml"


def test_writes_node_specific_file_when_param_is_new(workspace: Path):
    writer = ParamWriter(ParamStore(workspace))
    targets = writer.plan_targets(NODE, {"brand_new_param": 1}, FILES)
    assert targets[0].rel_path == "params/lidar_top.yaml"


def test_writes_to_the_owning_file_for_node_local_param(workspace: Path):
    writer = ParamWriter(ParamStore(workspace))
    targets = writer.plan_targets(NODE, {"lidar_mode": "2048x10"}, FILES)
    assert targets[0].rel_path == "params/lidar_top.yaml"


def test_plan_targets_rejects_node_without_param_files(workspace: Path):
    writer = ParamWriter(ParamStore(workspace))
    with pytest.raises(Exception, match="parameter file"):
        writer.plan_targets("/unknown/node", {"x": 1}, [])


# ---------------------------------------------------------------- 쓰기 동작


def test_write_updates_value_and_reads_back(workspace: Path):
    store = ParamStore(workspace)
    writer = ParamWriter(store)
    writer.apply(writer.plan_targets(NODE, {"lidar_mode": "2048x10"}, FILES))

    saved = ParamStore(workspace).saved_values(NODE, FILES)
    assert saved["lidar_mode"].value == "2048x10"


def test_write_preserves_comments_and_key_order(workspace: Path):
    """사람이 직접 읽는 파일이므로 주석과 순서를 보존해야 한다 (§7.1).

    PyYAML 을 쓰면 이 테스트가 깨진다. 그래서 ruamel 을 round-trip 모드로 쓴다.
    """
    path = workspace / "params" / "lidar_top.yaml"
    before = path.read_text(encoding="utf-8")
    assert "# lidar_top 서브시스템 parameter file" in before
    keys_before = [
        line.split(":")[0].strip()
        for line in before.splitlines()
        if line.startswith("    ") and ":" in line
    ]

    store = ParamStore(workspace)
    writer = ParamWriter(store)
    writer.apply(writer.plan_targets(NODE, {"lidar_mode": "2048x10"}, FILES))

    after = path.read_text(encoding="utf-8")
    assert "# lidar_top 서브시스템 parameter file" in after
    assert "https://github.com/ouster-lidar/ouster-ros" in after
    assert "udp_dest 가 호스트 NIC 주소와 다르면" in after
    keys_after = [
        line.split(":")[0].strip()
        for line in after.splitlines()
        if line.startswith("    ") and ":" in line
    ]
    assert keys_before == keys_after


def test_write_uses_exact_node_key_not_wildcard(workspace: Path):
    """/** 아래에 쓰면 다른 노드까지 바뀐다. 항상 정확한 노드 키에 쓴다."""
    store = ParamStore(workspace)
    writer = ParamWriter(store)
    # use_sim_time 은 global.yaml 의 /** 에서 온다
    writer.apply(writer.plan_targets(NODE, {"use_sim_time": True}, FILES))

    text = (workspace / "params" / "global.yaml").read_text(encoding="utf-8")
    # /** 블록의 값은 그대로여야 한다
    wildcard_block = text.split("/**:")[1].split("/ouster")[0]
    assert "use_sim_time: false" in wildcard_block
    # 노드 전용 키에 새 값이 들어갔다
    saved = ParamStore(workspace).saved_values(NODE, FILES)
    assert saved["use_sim_time"].value is True
    # 다른 노드는 영향을 받지 않는다
    other = ParamStore(workspace).saved_values("/camera_front/camera", FILES)
    assert other["use_sim_time"].value is False


def test_write_creates_nested_map_for_dotted_name(workspace: Path):
    store = ParamStore(workspace)
    writer = ParamWriter(store)
    writer.apply(writer.plan_targets(NODE, {"device.udp_port": 7502}, FILES))

    saved = ParamStore(workspace).saved_values(NODE, FILES)
    assert saved["device.udp_port"].value == 7502
    text = (workspace / "params" / "lidar_top.yaml").read_text(encoding="utf-8")
    assert "device:" in text
    assert "udp_port: 7502" in text


def test_write_leaves_no_temp_file(workspace: Path):
    store = ParamStore(workspace)
    writer = ParamWriter(store)
    writer.apply(writer.plan_targets(NODE, {"lidar_mode": "512x20"}, FILES))
    assert list((workspace / "params").glob("*.tmp")) == []


def test_multiple_params_touching_two_files(workspace: Path):
    store = ParamStore(workspace)
    writer = ParamWriter(store)
    written = writer.apply(
        writer.plan_targets(
            NODE, {"lidar_mode": "2048x10", "sensor_frame": "other_link"}, FILES
        )
    )
    assert written == ["params/global.yaml", "params/lidar_top.yaml"]


def test_file_hashes_change_after_write(workspace: Path):
    store = ParamStore(workspace)
    writer = ParamWriter(store)
    before = writer.file_hashes(FILES)
    writer.apply(writer.plan_targets(NODE, {"lidar_mode": "512x20"}, FILES))
    after = writer.file_hashes(FILES)
    assert before["params/lidar_top.yaml"] != after["params/lidar_top.yaml"]
    assert before["params/global.yaml"] == after["params/global.yaml"]


# ---------------------------------------------------------------- git


def test_commit_then_revert_restores_content_and_keeps_history(
    workspace: Path, git_repo: GitParams
):
    """롤백은 reset 이 아니라 revert 다. 실패한 시도도 이력에 남아야 한다 (§6.2)."""
    store = ParamStore(workspace)
    writer = ParamWriter(store)
    writer.apply(writer.plan_targets(NODE, {"lidar_mode": "2048x10"}, FILES))

    sha = git_repo.commit(
        ["lidar_top.yaml"], "라이다 해상도 상향 테스트\n\nuser=engineer1", "engineer1"
    )
    assert ParamStore(workspace).saved_values(NODE, FILES)["lidar_mode"].value == "2048x10"
    assert len(git_repo.log()) == 2

    git_repo.revert(sha, "engineer1")

    # 내용이 돌아왔다
    assert ParamStore(workspace).saved_values(NODE, FILES)["lidar_mode"].value == "1024x10"
    # 그리고 이력은 지워지지 않고 늘어났다 — 실패한 시도가 남는다
    log = git_repo.log()
    assert len(log) == 3
    assert any("2048x10" in c.subject or "라이다" in c.subject for c in log)
    assert log[0].subject.startswith("Revert")


def test_restore_old_commit_adds_new_commit(workspace: Path, git_repo: GitParams):
    """특정 시점 복원도 새 commit 으로 쌓인다 (§7.3)."""
    store = ParamStore(workspace)
    writer = ParamWriter(store)
    baseline = git_repo.head()
    assert baseline is not None

    writer.apply(writer.plan_targets(NODE, {"lidar_mode": "2048x10"}, FILES))
    git_repo.commit(["lidar_top.yaml"], "1차 변경", "engineer1")
    writer.apply(writer.plan_targets(NODE, {"lidar_mode": "512x20"}, FILES))
    git_repo.commit(["lidar_top.yaml"], "2차 변경", "engineer1")
    assert len(git_repo.log()) == 3

    git_repo.restore(baseline, "operator1", "기준 시점으로 복원")

    assert ParamStore(workspace).saved_values(NODE, FILES)["lidar_mode"].value == "1024x10"
    log = git_repo.log()
    assert len(log) == 4  # 아무것도 사라지지 않았다
    assert log[0].subject == "기준 시점으로 복원"
    assert log[0].author == "operator1"


def test_commit_records_author_for_audit_log(workspace: Path, git_repo: GitParams):
    store = ParamStore(workspace)
    writer = ParamWriter(store)
    writer.apply(writer.plan_targets(NODE, {"lidar_mode": "512x20"}, FILES))
    git_repo.commit(["lidar_top.yaml"], "변경", "engineer42")
    assert git_repo.log()[0].author == "engineer42"


def test_commit_without_changes_is_a_noop(workspace: Path, git_repo: GitParams):
    before = git_repo.head()
    sha = git_repo.commit(["lidar_top.yaml"], "변화 없음", "t")
    assert sha == before
    assert len(git_repo.log()) == 1


def test_dirty_detection_sees_external_edits(workspace: Path, git_repo: GitParams):
    """Config App 밖에서 YAML 이 수정된 경우를 감지한다 (§6.3)."""
    assert git_repo.is_clean()
    path = workspace / "params" / "lidar_top.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "\n# 손으로 고침\n", encoding="utf-8")
    assert not git_repo.is_clean()
    assert "lidar_top.yaml" in git_repo.dirty_files()


def test_author_name_is_sanitised(workspace: Path, git_repo: GitParams):
    """git -c 값에 줄바꿈이 섞여 명령이 깨지지 않게 한다."""
    store = ParamStore(workspace)
    writer = ParamWriter(store)
    writer.apply(writer.plan_targets(NODE, {"lidar_mode": "512x20"}, FILES))
    git_repo.commit(["lidar_top.yaml"], "변경", "bad\nname")
    assert "\n" not in git_repo.log()[0].author
