"""store / schema / services_map 단위 테스트.

이 모듈들은 rclpy 를 import 하지 않으므로 ROS 2 환경 없이 돌 수 있다.
discovery / api / main 테스트는 컨테이너 안에서 돌려야 한다.

    pytest src/av_config/test/test_store_and_schema.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src" / "av_config"))

from av_config.models import ApplyMethod, DriverSchema, Layer  # noqa: E402
from av_config.schema import SchemaRegistry  # noqa: E402
from av_config.services_map import ServicesMap  # noqa: E402
from av_config.store import (  # noqa: E402
    ParamStore,
    flatten_params,
    key_specificity,
    normalize_node_name,
)

CONFIG = REPO / "config"
NODE = "/ouster/os_driver"


# ---------------------------------------------------------------- services.yaml


def test_services_map_resolves_unit_and_param_files():
    smap = ServicesMap.load(CONFIG / "services.yaml")
    assert smap.unit_of(NODE) == "av-lidar-top.service"
    # 순서가 저장값을 결정한다 (§5.1). global.yaml 이 마지막이어야 한다.
    assert smap.param_files_of(NODE) == [
        "params/lidar_top.yaml",
        "params/global.yaml",
    ]


def test_nodes_in_unit_lists_every_node_of_the_unit():
    """재시작 영향 범위는 unit 단위로 계산한다 (§6.1, §8.1)."""
    smap = ServicesMap.load(CONFIG / "services.yaml")
    assert smap.nodes_in_unit("av-lidar-top.service") == [NODE]


# ---------------------------------------------------------------- 키 매칭


@pytest.mark.parametrize(
    ("key", "node", "expected"),
    [
        ("/**", "/ouster/os_driver", 0),
        ("/ouster/**", "/ouster/os_driver", 1),
        ("/ouster/os_driver", "/ouster/os_driver", 1000),
        ("ouster/os_driver", "/ouster/os_driver", 1000),  # 선행 슬래시 없이도 매칭
        ("/camera/**", "/ouster/os_driver", None),
        ("/ouster/other", "/ouster/os_driver", None),
    ],
)
def test_key_specificity(key, node, expected):
    assert key_specificity(key, node) == expected


def test_specificity_orders_wildcard_before_exact():
    """같은 파일 안에서는 /** 를 먼저 적용하고 구체적인 키로 덮는다."""
    assert key_specificity("/**", NODE) < key_specificity(NODE, NODE)


def test_normalize_node_name():
    assert normalize_node_name("ouster/os_driver") == "/ouster/os_driver"
    assert normalize_node_name("/ouster/os_driver") == "/ouster/os_driver"


def test_flatten_nested_params_to_dotted_names():
    flat = flatten_params({"a": 1, "device": {"udp_dest": "1.2.3.4", "deep": {"x": 2}}})
    assert flat == {"a": 1, "device.udp_dest": "1.2.3.4", "device.deep.x": 2}


def test_flatten_keeps_arrays_as_values():
    """리스트는 배열 파라미터 값이므로 펼치지 않는다."""
    assert flatten_params({"ids": [1, 2, 3]}) == {"ids": [1, 2, 3]}


# ---------------------------------------------------------------- 저장값 머지


def test_saved_values_merges_param_files_in_order():
    """저장값은 단일 파일의 값이 아니라 param_files 머지 결과다 (§5.1)."""
    store = ParamStore(CONFIG)
    saved = store.saved_values(NODE, ["params/lidar_top.yaml", "params/global.yaml"])

    # lidar_top.yaml 에만 있는 값
    assert saved["lidar_mode"].value == "1024x10"
    assert saved["lidar_mode"].source == "params/lidar_top.yaml"

    # global.yaml 의 /** 에서 온 값
    assert saved["use_sim_time"].value is False
    assert saved["use_sim_time"].source == "params/global.yaml"

    # global.yaml 이 뒤에 오므로 전역 값이 이긴다 (§11.2, 의도된 순서)
    assert saved["sensor_frame"].value == "lidar_top_link"
    assert saved["sensor_frame"].source == "params/global.yaml"


def test_later_param_file_wins():
    """순서를 뒤집으면 승자가 바뀐다. 순서가 저장값을 결정한다는 뜻이다."""
    store = ParamStore(CONFIG)
    forward = store.saved_values(NODE, ["params/lidar_top.yaml", "params/global.yaml"])
    reversed_ = store.saved_values(NODE, ["params/global.yaml", "params/lidar_top.yaml"])
    assert forward["sensor_frame"].source == "params/global.yaml"
    # 뒤집으면 lidar_top.yaml 에 sensor_frame 이 없으므로 global 값이 남는다
    assert reversed_["sensor_frame"].value == "lidar_top_link"
    # 반대로 lidar_top 에만 있는 값의 출처는 바뀌지 않는다
    assert reversed_["lidar_mode"].source == "params/lidar_top.yaml"


def test_missing_param_file_is_skipped_not_fatal():
    store = ParamStore(CONFIG)
    saved = store.saved_values(NODE, ["params/does_not_exist.yaml", "params/lidar_top.yaml"])
    assert saved["lidar_mode"].value == "1024x10"


# ---------------------------------------------------------------- 스키마


OUSTER_SERVICES = {"/ouster/get_metadata", "/ouster/get_config"}


def test_schema_registry_loads_ouster():
    """배포되는 ouster 스키마가 실제 드라이버 사실과 맞는지 확인한다.

    사실 출처: https://github.com/ouster-lidar/ouster-ros 태그 jazzy-0.13.15.
    센서 FW 2.1.2 와 통신할 수 있는 마지막 릴리스라 그 버전에 고정했다
    (docs/driver-version-pin.md). 숫자가 깨지면 드라이버 버전이 바뀐 것이다.
    """
    reg = SchemaRegistry.load_dir(CONFIG / "schemas")
    assert reg.errors == []
    matched = reg.match(NODE, available_services=OUSTER_SERVICES)
    assert matched is not None
    assert matched.driver == "ouster_ros"
    assert len(matched.params) == 33

    # lidar_mode 는 센서 펌웨어에 기록되므로 device 계층이다
    assert matched.params["lidar_mode"].layer is Layer.DEVICE
    assert matched.params["lidar_mode"].enum_values == [
        "512x10", "512x20", "1024x10", "1024x20", "2048x10", "4096x5",
    ]


def test_ouster_has_no_runtime_params():
    """드라이버에 add_on_set_parameters_callback 이 없다.

    따라서 실행 중 바꿀 수 있는 파라미터가 하나도 없고, 전부 restart 다.
    이 단언이 깨지면 드라이버가 업그레이드된 것이므로 스키마를 다시 봐야 한다.
    """
    reg = SchemaRegistry.load_dir(CONFIG / "schemas")
    schema = reg.match(NODE, available_services=OUSTER_SERVICES)
    assert schema is not None
    assert all(p.apply is ApplyMethod.RESTART for p in schema.params.values())


def test_every_ouster_device_param_can_be_read_back():
    """device 계층은 장치에서 되읽을 수 있어야 한다 (§4.4).

    메타스키마가 이미 강제하지만, 몇 개가 device 로 분류됐는지도 함께 본다.
    jazzy-0.13.15 는 센서 설정 중 8개만 ROS 파라미터로 노출하고, 그중 장치가
    되읽을 수 있는 것은 7개다 (udp_profile_lidar 는 FW 2.1 의 get_config 에
    없다). 나머지 19개는
    드라이버가 선언하지 않아 Config App 이 다룰 수 없다
    (docs/driver-version-pin.md).
    """
    reg = SchemaRegistry.load_dir(CONFIG / "schemas")
    schema = reg.match(NODE, available_services=OUSTER_SERVICES)
    assert schema is not None
    device = [p for p in schema.params.values() if p.layer is Layer.DEVICE]
    assert len(device) == 7
    assert all(p.readback_via is not None for p in device)
    assert all(p.readback_via.service == "/ouster/get_config" for p in device)


def test_ouster_schema_requires_its_services():
    """표준 파라미터 서비스만 가진 노드에는 매칭되면 안 된다."""
    reg = SchemaRegistry.load_dir(CONFIG / "schemas")
    assert reg.match(NODE, available_services=set()) is None
    assert reg.match(NODE, available_services={"/ouster/get_metadata"}) is None


def test_schema_match_skips_service_check_when_offline():
    """오프라인 편집에서는 서비스 조건을 건너뛴다 (§5.3)."""
    reg = SchemaRegistry.load_dir(CONFIG / "schemas")
    assert reg.match(NODE, available_services=None) is not None


def test_schema_does_not_match_other_nodes():
    reg = SchemaRegistry.load_dir(CONFIG / "schemas")
    assert reg.match("/camera_front/camera", available_services=None) is None


def test_device_layer_requires_readback_via():
    """§4.4: layer: device 는 readback_via 필수. 누락 시 로드 단계에서 거부한다."""
    bad = {
        "driver": "bad_driver",
        "match": {"node_name_regex": "^/bad$"},
        "params": {
            "some_device_param": {
                "layer": "device",
                "apply": "restart",
                "tier": "engineer",
            }
        },
    }
    with pytest.raises(ValidationError, match="readback_via"):
        DriverSchema.model_validate(bad)


def test_reconfigure_requires_apply_via():
    bad = {
        "driver": "bad_driver",
        "match": {"node_name_regex": "^/bad$"},
        "params": {
            "p": {
                "layer": "driver",
                "apply": "reconfigure",
                "tier": "engineer",
            }
        },
    }
    with pytest.raises(ValidationError, match="apply_via"):
        DriverSchema.model_validate(bad)


def test_driver_layer_does_not_require_readback_via():
    ok = {
        "driver": "ok_driver",
        "match": {"node_name_regex": "^/ok$"},
        "params": {"p": {"layer": "driver", "apply": "restart", "tier": "engineer"}},
    }
    parsed = DriverSchema.model_validate(ok)
    assert parsed.params["p"].readback_via is None


def test_unknown_schema_field_is_rejected():
    """오타를 조용히 무시하면 설정이 적용되지 않는 이유를 찾기 어렵다."""
    bad = {
        "driver": "d",
        "match": {"node_name_regex": "^/x$"},
        "params": {"p": {"layer": "driver", "apply": "restart", "tier": "engineer", "tyer": "x"}},
    }
    with pytest.raises(ValidationError):
        DriverSchema.model_validate(bad)


def test_broken_schema_file_is_reported_not_raised():
    """스키마 하나가 깨져도 앱은 뜨고, 그 드라이버만 원본 모드로 떨어진다 (§5.2)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "broken.yaml").write_text(
            "driver: broken\nmatch: {node_name_regex: '^/b$'}\n"
            "params: {p: {layer: device, apply: restart, tier: engineer}}\n",
            encoding="utf-8",
        )
        reg = SchemaRegistry.load_dir(d)
        assert reg.schemas == []
        assert len(reg.errors) == 1
        assert "readback_via" in reg.errors[0].error


# ---------------------------------------------------------------- drift 비교

from av_config.store import values_equal  # noqa: E402


@pytest.mark.parametrize(
    ("saved", "runtime"),
    [
        (10, 10.0),          # YAML 정수 vs 런타임 double — drift 아님
        (10.0, 10),
        (0.1, 0.1),
        ("abc", "abc"),
        (True, True),
        (False, False),
        ([1, 2], [1.0, 2.0]),
        ([], []),
    ],
)
def test_values_equal_true(saved, runtime):
    assert values_equal(saved, runtime)


@pytest.mark.parametrize(
    ("saved", "runtime"),
    [
        (10, 11),
        ("abc", "abd"),
        (True, False),
        (1, True),           # bool 과 int 는 섞지 않는다
        (True, 1),
        (0, False),
        ([1, 2], [1, 2, 3]),
        ([1, 2], [2, 1]),
        ("1024x10", None),   # 런타임이 NOT_SET
        (None, "1024x10"),
    ],
)
def test_values_equal_false(saved, runtime):
    assert not values_equal(saved, runtime)


# ---------------------------------------------------------------- drift 판정

from av_config.store import SavedEntry, is_drift  # noqa: E402


def test_drift_when_saved_differs_from_runtime():
    entry = SavedEntry(value="1024x10", source="params/lidar_top.yaml")
    assert is_drift(entry, "512x10", reachable=True) is True


def test_no_drift_when_values_match():
    entry = SavedEntry(value="1024x10", source="params/lidar_top.yaml")
    assert is_drift(entry, "1024x10", reachable=True) is False


def test_no_drift_when_node_is_unreachable():
    """런타임 값이 없으면 비교 대상이 없다. drift 가 아니라 '알 수 없음'이다.

    여기서 drift 로 표시하면 꺼진 노드의 모든 파라미터가 경고로 떠서 R4 가
    노이즈가 된다 (§9.2).
    """
    entry = SavedEntry(value="1024x10", source="params/lidar_top.yaml")
    assert is_drift(entry, None, reachable=False) is False


def test_no_drift_when_param_is_not_in_any_file():
    """YAML 에 없으면 노드 기본값을 쓰는 것이므로 drift 가 아니다."""
    assert is_drift(None, "whatever", reachable=True) is False


def test_numeric_type_difference_is_not_drift():
    entry = SavedEntry(value=10, source="params/lidar_top.yaml")
    assert is_drift(entry, 10.0, reachable=True) is False
