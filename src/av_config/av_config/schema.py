"""파라미터 스키마 로드와 매칭 (명세 §4.4, §5.1-6).

스키마 파일은 로드 시 메타스키마로 검증한다. 잘못 작성한 스키마가 적용 시점에
터지는 것을 막기 위함이다. 특히 layer: device 인데 readback_via 가 없으면 거부한다.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError
from ruamel.yaml import YAML, YAMLError

from .models import DriverSchema, ParamSchema, SchemaLoadError
from .store import normalize_node_name

_log = logging.getLogger(__name__)
_yaml = YAML(typ="safe")


@dataclass
class SchemaRegistry:
    """로드된 드라이버 스키마 모음."""

    schemas: list[DriverSchema] = field(default_factory=list)
    errors: list[SchemaLoadError] = field(default_factory=list)
    _compiled: dict[str, re.Pattern[str]] = field(default_factory=dict, repr=False)

    @classmethod
    def load_dir(cls, directory: Path) -> SchemaRegistry:
        reg = cls()
        if not directory.is_dir():
            _log.warning("스키마 디렉터리 없음: %s", directory)
            return reg

        for path in sorted(directory.glob("*.yaml")):
            if path.name.startswith("_"):
                continue
            try:
                with path.open("r", encoding="utf-8") as fh:
                    raw = _yaml.load(fh)
                parsed = DriverSchema.model_validate(raw)
            except (YAMLError, ValidationError, TypeError) as exc:
                # 메타스키마 검증 실패. 해당 스키마를 로드하지 않고 사유를 남긴다.
                # 그 드라이버는 원본 모드로 떨어진다(§5.2).
                reg.errors.append(SchemaLoadError(file=path.name, error=str(exc)))
                _log.error("스키마 로드 실패 %s: %s", path.name, exc)
                continue

            try:
                reg._compiled[parsed.driver] = re.compile(parsed.match.node_name_regex)
            except re.error as exc:
                reg.errors.append(
                    SchemaLoadError(file=path.name, error=f"node_name_regex 오류: {exc}")
                )
                _log.error("스키마 %s 의 node_name_regex 오류: %s", path.name, exc)
                continue

            reg.schemas.append(parsed)
            _log.info(
                "스키마 로드: %s (%d개 파라미터)", parsed.driver, len(parsed.params)
            )
        return reg

    def match(
        self,
        node_name: str,
        available_services: set[str] | None = None,
        hardware_id: str | None = None,
    ) -> DriverSchema | None:
        """노드에 맞는 스키마를 찾는다 (§5.1-6).

        노드 이름, 제공 서비스, /diagnostics 의 hardware_id 로 매칭한다.
        available_services 가 None 이면 서비스 조건을 건너뛴다(오프라인 편집).
        """
        node = normalize_node_name(node_name)
        for schema in self.schemas:
            pattern = self._compiled.get(schema.driver)
            if pattern is None or not pattern.match(node):
                continue

            required = set(schema.match.required_services)
            if (
                required
                and available_services is not None
                and not required.issubset(available_services)
            ):
                continue

            if (
                schema.match.hardware_id_regex
                and hardware_id is not None
                and not re.match(schema.match.hardware_id_regex, hardware_id)
            ):
                continue

            return schema
        return None

    def param_schema(self, schema: DriverSchema | None, param: str) -> ParamSchema | None:
        if schema is None:
            return None
        return schema.params.get(param)
