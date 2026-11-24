"""config/ 를 템플릿으로만 쓰는지 확인한다.

설정 루트가 다른 git 저장소 안에 있으면 Config App 이 그 저장소의 추적 파일을
고쳐 쓰고 이력까지 그 저장소에 쌓을 수 있다. ROS 2 가 필요 없다.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / "src" / "av_config"))

from av_config.gitparams import GitParams, enclosing_worktree  # noqa: E402
from av_config.services_map import ServicesMap  # noqa: E402

from tools.init_config_root import create_config_root  # noqa: E402

TEMPLATE = PROJECT / "config"


def git(path: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", *args], text=True
    ).strip()


def test_config_root_is_a_copy_with_its_own_history(tmp_path):
    root = create_config_root(tmp_path / "av_config", TEMPLATE)

    assert sorted(path.name for path in root.iterdir()) == ["params", "rules", "schemas", "services.yaml"]
    assert ServicesMap.load(root / "services.yaml").unit_of("/ouster/os_driver")
    params = GitParams(root / "params")
    assert params.is_repo()
    assert params.is_clean()
    assert [commit.subject for commit in params.log(10)] == ["initial: 기준 설정"]


def test_existing_config_root_is_not_overwritten(tmp_path):
    root = create_config_root(tmp_path / "av_config", TEMPLATE)
    with pytest.raises(FileExistsError):
        create_config_root(root, TEMPLATE)


def test_config_root_inside_a_repository_is_refused(tmp_path):
    git(tmp_path, "init", "-q")
    assert enclosing_worktree(tmp_path / "config") == tmp_path.resolve()
    with pytest.raises(ValueError):
        create_config_root(tmp_path / "config", TEMPLATE)


def test_config_root_outside_repositories_is_accepted(tmp_path):
    assert enclosing_worktree(tmp_path / "av_config") is None


def test_params_inside_another_repository_is_not_its_own_repo(tmp_path):
    params = tmp_path / "config" / "params"
    params.mkdir(parents=True)
    git(tmp_path, "init", "-q")
    assert not GitParams(params).is_repo()
    git(params, "init", "-q")
    assert GitParams(params).is_repo()
