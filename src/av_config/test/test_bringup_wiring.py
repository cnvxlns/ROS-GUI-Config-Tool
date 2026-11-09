"""services.yaml 과 av_bringup / systemd 유닛의 이름이 어긋나지 않는지 본다.

세 곳이 같은 사실을 중복으로 적고 있어서 조용히 어긋날 수 있다.

  services.yaml   unit, launch, nodes, param_files
  av_bringup      launch 파일 이름, PARAM_FILES, 노드 이름과 namespace
  deploy/         유닛 파일 이름

가장 위험한 것은 param_files 순서다. 어긋나면 Config App 이 계산한 "저장값"과
노드의 실제 값이 달라지고, 해소할 수 없는 drift 로 보인다(명세 §5.1).

launch 파일을 import 하지 않고 ast 로 읽는다. import 하면 launch / launch_ros 가
필요해져서 이 테스트가 호스트에서 돌지 못한다.
"""

from __future__ import annotations

import ast
import contextlib
import re
from pathlib import Path

from ruamel.yaml import YAML

REPO = Path(__file__).resolve().parents[3]
CONFIG = REPO / "config"
LAUNCH_DIR = REPO / "src" / "av_bringup" / "launch"
DEPLOY = REPO / "deploy"

_yaml = YAML(typ="safe")


def _services() -> dict:
    with (CONFIG / "services.yaml").open(encoding="utf-8") as fh:
        return _yaml.load(fh)["subsystems"]


def _launch_constants(path: Path) -> dict[str, object]:
    """launch 파일의 모듈 수준 상수를 ast 로 읽는다."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                with contextlib.suppress(ValueError):
                    found[target.id] = ast.literal_eval(node.value)
    return found


def test_lidar_top_launch_파일이_있다():
    sub = _services()["lidar_top"]
    # "ros2 launch <pkg> <file>" 에서 패키지와 파일 이름을 뽑는다.
    match = re.search(r"ros2 launch (\S+) (\S+)", sub["launch"])
    assert match, f"launch 명령 형식이 예상과 다르다: {sub['launch']!r}"
    package, launch_file = match.groups()
    assert package == "av_bringup"
    assert (LAUNCH_DIR / launch_file).is_file(), (
        f"services.yaml 이 {package}/{launch_file} 을 가리키는데 파일이 없다"
    )


def test_param_files_순서가_launch_와_같다():
    sub = _services()["lidar_top"]
    constants = _launch_constants(LAUNCH_DIR / "lidar_top.launch.py")
    assert list(constants["PARAM_FILES"]) == list(sub["param_files"]), (
        "순서가 곧 우선순위다. services.yaml 과 launch 가 다르면 drift 가 된다."
    )


def test_노드_이름이_services_와_스키마와_같다():
    sub = _services()["lidar_top"]
    constants = _launch_constants(LAUNCH_DIR / "lidar_top.launch.py")
    launched = f"/{constants['OUSTER_NS']}/{constants['OS_DRIVER_NAME']}"
    assert sub["nodes"] == [launched]

    with (CONFIG / "schemas" / "ouster_ros.yaml").open(encoding="utf-8") as fh:
        schema = _yaml.load(fh)
    assert re.match(schema["match"]["node_name_regex"], launched), (
        "스키마의 node_name_regex 가 이 노드 이름과 맞지 않으면 파라미터가 "
        "스키마 없이 raw 로만 보인다."
    )


def test_유닛_파일이_있다():
    for name, sub in _services().items():
        unit = sub["unit"]
        native = DEPLOY / unit
        dev_host = DEPLOY / "dev-host" / unit
        if name != "lidar_top":
            # camera_front / perception 의 유닛은 아직 범위가 아니다.
            continue
        assert native.is_file(), f"{unit} (배포용) 이 없다"
        assert dev_host.is_file(), f"{unit} (개발 호스트용) 이 없다"
