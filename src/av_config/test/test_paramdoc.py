"""드라이버 parameter file 주석 파서 테스트 (명세 §12.2).

픽스처는 ouster-ros 의 config/driver_params.yaml 에서 발췌한 실물이다.
가짜로 만든 주석으로 테스트하면 실제 드라이버에서 깨지는 것을 못 잡는다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src" / "av_config"))

from av_config.models import ParamType  # noqa: E402
from av_config.paramdoc import infer_type, parse_params_file  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "ouster_driver_params.yaml"


@pytest.fixture(scope="module")
def docs() -> dict:
    return parse_params_file(FIXTURE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 기본


def test_parses_every_parameter(docs):
    assert len(docs) == 13
    assert "sensor_hostname" in docs
    assert "lidar_mode" in docs


def test_defaults_are_parsed_with_types(docs):
    assert docs["sensor_hostname"].default == ""
    assert docs["lidar_port"].default == 0
    assert docs["ptp_utc_tai_offset"].default == -37.0
    assert docs["pub_static_tf"].default is True
    assert docs["point_type"].default == "original"


def test_required_marker_is_read(docs):
    """주석의 [required] / [optional] 표기를 읽는다."""
    assert docs["sensor_hostname"].required is True
    assert docs["lidar_mode"].required is False


# ---------------------------------------------------------------- enum 추출


def test_braced_enum_on_one_line(docs):
    """possible values: { a, b, c } 형태."""
    assert docs["lidar_mode"].enum == [
        "512x10", "512x20", "1024x10", "1024x20", "2048x10", "4096x5",
    ]


def test_bulleted_enum_with_wrapped_intro(docs):
    """여는 말이 "possible\\nvalues:" 로 줄바꿈된 경우도 잡아야 한다.

    ouster-ros 의 timestamp_mode 가 실제로 이 형태다.
    """
    assert docs["timestamp_mode"].enum == [
        "TIME_FROM_INTERNAL_OSC",
        "TIME_FROM_SYNC_PULSE_IN",
        "TIME_FROM_PTP_1588",
        "TIME_FROM_ROS_TIME",
    ]


def test_bulleted_enum_strips_inline_notes(docs):
    """- LEGACY: not recommended 에서 값만 남긴다."""
    enum = docs["udp_profile_lidar"].enum
    assert enum is not None
    assert enum[0] == "LEGACY"
    assert "RNG15_RFL8_NIR8_DUAL" in enum
    assert not any(":" in v or "(" in v for v in enum)


def test_choose_from_is_also_recognised(docs):
    """ouster 의 point_type 은 "choose from: {...}" 로 적혀 있다."""
    assert docs["point_type"].enum == [
        "original", "native", "xyz", "xyzi", "o_xyzi",
        "xyzir", "xyzrgb", "xyzrgba", "color_point",
    ]


def test_numeric_enum_values_stay_strings(docs):
    """YAML 에 쓸 때 따옴표가 필요하므로 문자열로 둔다."""
    assert docs["columns_per_packet"].enum == ["1", "2", "4", "8", "16", "32", "64"]


def test_params_without_enum_have_none(docs):
    assert docs["sensor_hostname"].enum is None
    assert docs["min_range"].enum is None


# ---------------------------------------------------------------- 설명


def test_description_drops_the_enum_text(docs):
    """허용 값 목록은 enum 으로 빠지고 설명에는 남지 않는다."""
    description = docs["lidar_mode"].description
    assert "resolution and rate" in description
    assert "512x10" not in description
    assert "possible values" not in description.lower()


def test_description_has_no_punctuation_residue(docs):
    """목록을 떼어낸 자리에 "; ." 같은 찌꺼기가 남으면 안 된다."""
    for doc in docs.values():
        assert "; ." not in doc.description
        assert ";;" not in doc.description
        assert not doc.description.startswith((".", ";", ","))


def test_name_prefix_with_spaces_is_also_stripped(docs):
    """드라이버가 "azimuth window start[optional]" 처럼 밑줄 대신 공백을 쓰기도 한다."""
    description = docs["azimuth_window_start"].description
    assert description == "values range [0, 360000] millidegrees"
    assert "[optional]" not in description


def test_no_parameter_keeps_its_marker_in_the_description(docs):
    for doc in docs.values():
        assert "[optional]" not in doc.description
        assert "[required]" not in doc.description


def test_description_strips_the_name_prefix(docs):
    """"sensor_hostname[required]: " 부분은 설명에서 뺀다."""
    assert not docs["sensor_hostname"].description.startswith("sensor_hostname")
    assert "hostname or IP address" in docs["sensor_hostname"].description


# ---------------------------------------------------------------- 타입 추론


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, ParamType.BOOL),
        (3, ParamType.INTEGER),
        (1.5, ParamType.DOUBLE),
        ("x", ParamType.STRING),
        ([1, 2], ParamType.INTEGER_ARRAY),
        ([1.0], ParamType.DOUBLE_ARRAY),
        ([True], ParamType.BOOL_ARRAY),
        (["a"], ParamType.STRING_ARRAY),
        ([], ParamType.STRING_ARRAY),
    ],
)
def test_infer_type(value, expected):
    assert infer_type(value) == expected


def test_bool_is_not_mistaken_for_int():
    """bool 은 int 의 하위 타입이다. 먼저 걸러야 한다."""
    assert infer_type(True) == ParamType.BOOL
    assert infer_type(1) == ParamType.INTEGER


# ---------------------------------------------------------------- 견고성


def test_empty_file_yields_nothing():
    assert parse_params_file("") == {}


def test_file_without_ros_parameters_block_yields_nothing():
    assert parse_params_file("some_key: 1\nother: 2\n") == {}


def test_comment_block_is_broken_by_blank_line():
    """빈 줄로 끊긴 주석은 아래 파라미터에 붙지 않는다."""
    text = (
        "node:\n  ros__parameters:\n"
        "    # 다른 설명\n\n"
        "    value: 1\n"
    )
    assert parse_params_file(text)["value"].description == ""


def test_comment_naming_another_param_is_not_attached():
    """주석이 다른 파라미터를 가리키면 붙이지 않는다."""
    text = (
        "node:\n  ros__parameters:\n"
        "    # other_param[optional]: 다른 것 설명\n"
        "    value: 1\n"
    )
    assert parse_params_file(text)["value"].description == ""


def test_inline_comment_after_value_is_stripped():
    text = "node:\n  ros__parameters:\n    port: 7502  # 기본 포트\n"
    assert parse_params_file(text)["port"].default == 7502
