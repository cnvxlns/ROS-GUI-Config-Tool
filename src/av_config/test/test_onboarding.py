"""온보딩 초안 생성 테스트 (명세 §12.2).

생성한 YAML 이 실제로 파싱되고, 기존 로더가 읽을 수 있어야 한다. 그렇지 않으면
"복사해서 붙여 넣으면 된다"는 안내가 거짓말이 된다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from ruamel.yaml import YAML

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src" / "av_config"))

from av_config.models import (  # noqa: E402
    DriverSchema,
    ParamDTO,
    ParamType,
    ServicesFile,
    Tier,
    ValueRange,
)
from av_config.onboarding import (  # noqa: E402
    build_steps,
    driver_name,
    param_file_draft,
    schema_draft,
    services_snippet,
    subsystem_name,
    unit_name,
    yaml_scalar,
)
from av_config.store import ParamStore  # noqa: E402

NODE = "/gnss_imu/driver"
yaml = YAML(typ="safe")


def param(name: str, ptype: int, value: object, **over) -> ParamDTO:
    return ParamDTO(name=name, type=ptype, runtime_value=value, **over)


@pytest.fixture
def params() -> list[ParamDTO]:
    return [
        param("serial_port", ParamType.STRING, "/dev/ttyUSB0"),
        param("baudrate", ParamType.INTEGER, 460800,
              integer_range=ValueRange(from_value=9600, to_value=921600, step=1)),
        param("frame_id", ParamType.STRING, "gnss_link"),
        param("rtk_enabled", ParamType.BOOL, True),
        param("imu_rate_hz", ParamType.DOUBLE, 200.0),
        param("antenna_offset", ParamType.DOUBLE_ARRAY, [0.0, 0.0, 1.2]),
        param("driver_version", ParamType.STRING, "1.2.3", read_only=True,
              tier=Tier.READ_ONLY),
    ]


# ---------------------------------------------------------------- 이름 추론


@pytest.mark.parametrize(
    ("node", "expected"),
    [
        ("/gnss_imu/driver", "gnss_imu"),
        ("/ouster/os_driver", "ouster"),
        ("/talker", "talker"),
        ("/camera_front/camera/node", "camera_front"),
        ("/Weird-Name/x", "weird_name"),
    ],
)
def test_subsystem_name(node, expected):
    assert subsystem_name(node) == expected


def test_unit_name_uses_hyphens():
    assert unit_name("gnss_imu") == "av-gnss-imu.service"


def test_driver_name():
    assert driver_name(NODE) == "gnss_imu_driver"


# ---------------------------------------------------------------- YAML 스칼라


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, "true"),
        (False, "false"),
        (460800, "460800"),
        (200.0, "200.0"),
        ("gnss_link", "gnss_link"),
        ("/dev/ttyUSB0", '"/dev/ttyUSB0"'),   # 슬래시는 따옴표가 안전하다
        ("", '""'),
        ("on", '"on"'),                        # YAML 1.1 에서 bool 로 읽힌다
        ("no", '"no"'),
        ("1024x10", '"1024x10"'),   # 숫자로 시작하면 따옴표를 씌운다
        ("512", '"512"'),            # 문자열이 정수로 읽히면 안 된다
        ([0.0, 1.2], "[0.0, 1.2]"),
        (["a", "b"], "[a, b]"),
        (None, "null"),
    ],
)
def test_yaml_scalar(value, expected):
    assert yaml_scalar(value) == expected


def test_yaml_scalar_round_trips_through_parser():
    """따옴표 판단이 틀리면 값이 바뀐다. 실제로 파싱해 확인한다."""
    for value in [True, False, "on", "no", "yes", "/dev/ttyUSB0", "1024x10", 42, 1.5, ""]:
        parsed = yaml.load(f"v: {yaml_scalar(value)}")["v"]
        assert parsed == value, f"{value!r} -> {yaml_scalar(value)!r} -> {parsed!r}"


# ---------------------------------------------------------------- services.yaml


def test_services_snippet_parses_and_loads(params):
    snippet = services_snippet(NODE, ["/gnss_imu/fix", "/gnss_imu/imu"])
    # 조각이므로 subsystems: 를 붙여야 완전한 문서가 된다
    document = "subsystems:\n" + snippet
    parsed = ServicesFile.model_validate(yaml.load(document))

    sub = parsed.subsystems["gnss_imu"]
    assert sub.unit == "av-gnss-imu.service"
    assert sub.nodes == [NODE]
    assert sub.param_files == ["params/gnss_imu.yaml", "params/global.yaml"]
    assert [t.name for t in sub.topics] == ["/gnss_imu/fix", "/gnss_imu/imu"]


def test_services_snippet_without_topics_still_parses():
    document = "subsystems:\n" + services_snippet(NODE, [])
    parsed = ServicesFile.model_validate(yaml.load(document))
    assert parsed.subsystems["gnss_imu"].topics == []


def test_services_snippet_puts_global_last():
    """순서가 저장값을 결정한다. global.yaml 이 뒤에 와야 한다 (§11.2)."""
    sub = ServicesFile.model_validate(
        yaml.load("subsystems:\n" + services_snippet(NODE, []))
    ).subsystems["gnss_imu"]
    assert sub.param_files[-1] == "params/global.yaml"


# ---------------------------------------------------------------- parameter file


def test_param_file_draft_is_readable_by_the_store(tmp_path: Path, params):
    """초안을 그대로 저장하면 ParamStore 가 읽을 수 있어야 한다."""
    (tmp_path / "params").mkdir()
    (tmp_path / "params" / "gnss_imu.yaml").write_text(
        param_file_draft(NODE, params), encoding="utf-8"
    )
    saved = ParamStore(tmp_path).saved_values(NODE, ["params/gnss_imu.yaml"])

    assert saved["serial_port"].value == "/dev/ttyUSB0"
    assert saved["baudrate"].value == 460800
    assert saved["rtk_enabled"].value is True
    assert saved["imu_rate_hz"].value == 200.0
    assert saved["antenna_offset"].value == [0.0, 0.0, 1.2]


def test_param_file_draft_skips_read_only(params):
    """read_only 는 YAML 에 써도 드라이버가 거부한다."""
    assert "driver_version" not in param_file_draft(NODE, params)


def test_param_file_draft_handles_empty_params():
    text = param_file_draft(NODE, [])
    assert yaml.load(text) is not None
    assert "수집된 값이 없다" in text


# ---------------------------------------------------------------- 스키마


def test_schema_draft_parses_as_yaml(params):
    parsed = yaml.load(schema_draft(NODE, params, ["/gnss_imu/get_config"]))
    assert parsed["driver"] == "gnss_imu_driver"
    assert parsed["match"]["node_name_regex"] == r"^/gnss_imu/driver$"
    assert parsed["match"]["required_services"] == ["/gnss_imu/get_config"]
    assert set(parsed["params"]) == {p.name for p in params}


def test_schema_draft_regex_actually_matches_the_node(params):
    import re

    parsed = yaml.load(schema_draft(NODE, params, []))
    assert re.match(parsed["match"]["node_name_regex"], NODE)
    assert not re.match(parsed["match"]["node_name_regex"], "/gnss_imu/driver2")


def test_schema_draft_validates_against_the_metaschema(params):
    """초안이 DriverSchema 를 통과해야 '채우면 바로 쓸 수 있다'가 참이 된다.

    기본값을 layer: driver 로 두는 이유이기도 하다. device 로 두면
    readback_via 가 없어 로드 단계에서 거부된다 (§4.4).
    """
    parsed = DriverSchema.model_validate(yaml.load(schema_draft(NODE, params, [])))
    assert parsed.params["baudrate"].layer.value == "driver"
    assert parsed.params["baudrate"].apply.value == "restart"


def test_schema_draft_marks_read_only_params(params):
    parsed = DriverSchema.model_validate(yaml.load(schema_draft(NODE, params, [])))
    assert parsed.params["driver_version"].tier is Tier.READ_ONLY


def test_schema_draft_records_collected_facts(params):
    text = schema_draft(NODE, params, [])
    assert "타입 int" in text
    assert "범위 9600.0 ~ 921600.0" in text
    assert "현재값 460800" in text
    assert "드라이버가 read_only 로 선언" in text


@pytest.mark.parametrize("name", ["frame_id", "sensor_frame", "base_frame_id"])
def test_schema_draft_hints_global_ref_for_frame_params(name):
    """ROS 표준 frame_id 와 명세의 *_frame 규칙을 모두 인식해야 한다."""
    text = schema_draft(NODE, [param(name, ParamType.STRING, "x")], [])
    block = text.split(f"  {name}:", 1)[1]
    assert "global_ref" in block


def test_schema_draft_does_not_hint_global_ref_for_other_params():
    text = schema_draft(NODE, [param("baudrate", ParamType.INTEGER, 1)], [])
    assert "global_ref" not in text


def test_schema_draft_warns_about_device_layer(params):
    assert "readback_via 가 필수" in schema_draft(NODE, params, [])


def test_schema_draft_with_no_params_still_parses():
    parsed = yaml.load(schema_draft(NODE, [], []))
    assert parsed["params"] is None or parsed["params"] == {}


# ---------------------------------------------------------------- 절차


def test_steps_reflect_current_state():
    steps = build_steps(registered=False, has_schema=False, alive=True, has_param_file=False)
    by_number = {s.number: s for s in steps}
    assert len(steps) == 8
    assert by_number[3].done is True    # 장치 연결 상태에서 수집됨
    assert by_number[5].done is False   # 스키마 없음
    assert by_number[6].done is False   # 등록 안 됨
    assert by_number[1].done is None    # 판단 불가


def test_steps_mark_done_when_fully_onboarded():
    steps = build_steps(registered=True, has_schema=True, alive=True, has_param_file=True)
    by_number = {s.number: s for s in steps}
    assert by_number[5].done is True
    assert by_number[6].done is True
