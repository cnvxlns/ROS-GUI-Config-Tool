"""드라이버가 배포하는 parameter file 에서 문서를 뽑아낸다 (명세 §12.2).

왜 필요한가. 상당수 오픈소스 드라이버는 declare_parameter 를 호출하면서
ParameterDescriptor 를 채우지 않는다. 그러면 자동 수집(§5.1-3)으로는 이름과 타입만
얻고 설명·범위·허용 값은 비어 있다. 반면 그 드라이버가 패키지 config/ 에 넣어
배포하는 parameter file 의 **주석**에는 그 정보가 들어 있는 경우가 많다.

예: ouster-ros 의 config/driver_params.yaml

    # lidar_mode[optional]: resolution and rate; possible values: { 512x10,
    # 512x20, 1024x10, 1024x20, 2048x10, 4096x5 }. Leave empty to remain on
    # current the lidar mode.
    lidar_mode: ''

이 모듈은 그 주석을 파싱해 설명과 허용 값 목록을 돌려준다. 어디까지나 보조
정보다. 드라이버가 문서를 틀리게 적었을 수 있으므로 스키마에 넣기 전에 사람이
확인해야 한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .models import ParamType

# "    name: value" — ros__parameters 블록 안의 파라미터 한 줄
_PARAM_LINE = re.compile(r"^(?P<indent>\s+)(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?P<value>.*)$")
_COMMENT_LINE = re.compile(r"^\s*#\s?(?P<text>.*)$")
_BLANK = re.compile(r"^\s*$")

# "name[optional]: 설명" 또는 "name[required]: 설명".
# 드라이버가 "azimuth window start[optional]" 처럼 밑줄 대신 공백을 쓰기도 해서
# 공백도 허용하고 비교할 때 정규화한다.
_LEAD = re.compile(
    r"^(?P<name>[A-Za-z_][A-Za-z0-9_ ]*?)\s*\[(?P<req>\w+)\]\s*:\s*(?P<rest>.*)$"
)
# 허용 값 목록을 여는 말. 드라이버마다 표현이 다르다.
_ENUM_INTRO = r"(?:possible values|choose from|valid values|one of)"
# "possible values: { a, b, c }" / "choose from: {x, y}"
_BRACED = re.compile(_ENUM_INTRO + r"\s*:?\s*\{(?P<body>[^}]*)\}", re.IGNORECASE)
_INTRO_ONLY = re.compile(_ENUM_INTRO + r"\s*:?\s*$", re.IGNORECASE)
# "- VALUE" 또는 "- VALUE: 설명" / "- VALUE (주석)"
_BULLET = re.compile(r"^-\s*(?P<value>[A-Za-z0-9_.\-]+)\s*(?P<note>[:(].*)?$")


@dataclass
class ParamDoc:
    """parameter file 주석에서 뽑아낸 파라미터 문서."""

    name: str
    default: Any = None
    description: str = ""
    enum: list[str] | None = None
    required: bool = False
    raw_comment: str = field(default="", repr=False)

    def inferred_type(self) -> int:
        """기본값에서 타입을 추론한다. 런타임 수집이 가능하면 그쪽이 정확하다."""
        return infer_type(self.default)


def infer_type(value: Any) -> int:
    if isinstance(value, bool):
        return ParamType.BOOL
    if isinstance(value, int):
        return ParamType.INTEGER
    if isinstance(value, float):
        return ParamType.DOUBLE
    if isinstance(value, str):
        return ParamType.STRING
    if isinstance(value, (list, tuple)):
        if not value:
            return ParamType.STRING_ARRAY
        first = value[0]
        if isinstance(first, bool):
            return ParamType.BOOL_ARRAY
        if isinstance(first, int):
            return ParamType.INTEGER_ARRAY
        if isinstance(first, float):
            return ParamType.DOUBLE_ARRAY
        return ParamType.STRING_ARRAY
    return ParamType.NOT_SET


def _parse_literal(text: str) -> Any:
    """YAML 스칼라를 파이썬 값으로. 인라인 주석은 떼어낸다."""
    text = text.strip()
    # 값 뒤의 "# ..." 주석 제거 (따옴표 안의 # 는 건드리지 않는다)
    if text and text[0] not in "\"'":
        text = text.split("#", 1)[0].strip()
    if text in ("", "~", "null"):
        return "" if text == "" else None
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    low = text.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_parse_literal(p) for p in inner.split(",")]
    return text


def _extract_enum(lines: list[str]) -> tuple[list[str] | None, list[str]]:
    """주석 줄에서 허용 값 목록과, 그것을 뺀 설명 줄을 돌려준다.

    두 가지 표기를 지원한다.

      possible values: { a, b, c }     <- 중괄호형. 여러 줄에 걸칠 수 있다
      choose from: {x, y}

      possible values:                 <- 불릿형
      - A
      - B: 설명

    불릿형에서 여는 말이 "possible\nvalues:" 처럼 줄바꿈될 수 있으므로, 불릿을
    만나기 전까지의 줄을 이어 붙여서 판단한다.
    """
    # 중괄호형: 줄을 모두 이어 붙여 찾는다
    joined = " ".join(lines)
    braced = _BRACED.search(joined)
    if braced:
        values = [v.strip() for v in braced.group("body").split(",") if v.strip()]
        return (values or None), [_BRACED.sub("", joined)]

    # 불릿형: 첫 불릿 위치를 찾고, 그 앞 줄들을 이어 붙여 여는 말이 있는지 본다
    first_bullet = next(
        (i for i, line in enumerate(lines) if _BULLET.match(line.strip())), None
    )
    if first_bullet is None:
        return None, lines

    prefix = " ".join(lines[:first_bullet]).strip()
    if not _INTRO_ONLY.search(prefix):
        return None, lines

    values = []
    tail: list[str] = []
    for line in lines[first_bullet:]:
        bullet = _BULLET.match(line.strip())
        if bullet is not None:
            values.append(bullet.group("value"))
        elif values:
            # 불릿이 끝난 뒤의 줄은 설명으로 되돌린다
            tail.append(line)

    description = _INTRO_ONLY.sub("", prefix)
    return (values or None), [description, *tail]


def _clean_description(text: str) -> str:
    """허용 값 목록을 떼어낸 자리에 남는 구두점을 다듬는다.

    "resolution and rate; . Leave empty" -> "resolution and rate. Leave empty"
    """
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[;,]\s*(?=[.;,])", "", text)   # 연달아 붙은 구두점
    text = re.sub(r"\s+([.;,])", r"\1", text)      # 구두점 앞 공백
    text = re.sub(r"\(\s*\)", "", text)            # 빈 괄호
    return re.sub(r"\s+", " ", text).strip(" .;,")


def parse_params_file(text: str) -> dict[str, ParamDoc]:
    """드라이버의 parameter file 을 주석까지 읽어 파라미터 문서를 만든다.

    주석 블록은 바로 아래 파라미터에 붙는다. 빈 줄이 나오면 블록이 끊긴다.
    """
    docs: dict[str, ParamDoc] = {}
    buffer: list[str] = []
    seen_params_key = False

    for line in text.splitlines():
        if "ros__parameters" in line:
            seen_params_key = True
            buffer = []
            continue

        comment = _COMMENT_LINE.match(line)
        if comment:
            buffer.append(comment.group("text").rstrip())
            continue

        if _BLANK.match(line):
            buffer = []
            continue

        match = _PARAM_LINE.match(line)
        if not match or not seen_params_key:
            buffer = []
            continue

        name = match.group("name")
        default = _parse_literal(match.group("value"))

        enum, body = _extract_enum(buffer)
        required = False
        description_parts: list[str] = []
        for index, chunk in enumerate(body):
            lead = _LEAD.match(chunk) if index == 0 else None
            if lead:
                required = lead.group("req").lower() == "required"
                if lead.group("name").strip().replace(" ", "_") != name:
                    # 주석이 다른 파라미터를 가리키면 붙이지 않는다
                    description_parts = []
                    break
                description_parts.append(lead.group("rest"))
            else:
                description_parts.append(chunk)

        description = _clean_description(" ".join(description_parts))
        docs[name] = ParamDoc(
            name=name,
            default=default,
            description=description,
            enum=enum,
            required=required,
            raw_comment="\n".join(buffer),
        )
        buffer = []

    return docs
