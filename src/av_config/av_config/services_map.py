"""services.yaml 로드 (명세 §11.2).

Config App 이 어떤 노드를 어떤 unit 으로 재시작하고 어떤 YAML 을 고쳐야 하는지
아는 유일한 근거다.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ruamel.yaml import YAML

from .models import ServicesFile, Subsystem
from .store import normalize_node_name

_log = logging.getLogger(__name__)
_yaml = YAML(typ="safe")


class ServicesMap:
    def __init__(self, subsystems: dict[str, Subsystem]) -> None:
        self.subsystems = subsystems
        # 노드 -> 서브시스템 이름. 노드가 두 unit 에 중복 등록되면 설정 오류다.
        self._by_node: dict[str, str] = {}
        for name, sub in subsystems.items():
            for node in sub.nodes:
                key = normalize_node_name(node)
                if key in self._by_node:
                    _log.error(
                        "노드 %s 가 서브시스템 %s 와 %s 에 중복 등록됨. "
                        "재시작 단위가 모호해진다.",
                        key,
                        self._by_node[key],
                        name,
                    )
                self._by_node[key] = name

    @classmethod
    def load(cls, path: Path) -> ServicesMap:
        with path.open("r", encoding="utf-8") as fh:
            raw = _yaml.load(fh)
        parsed = ServicesFile.model_validate(raw)
        return cls(parsed.subsystems)

    def subsystem_of(self, node_name: str) -> tuple[str, Subsystem] | None:
        name = self._by_node.get(normalize_node_name(node_name))
        if name is None:
            return None
        return name, self.subsystems[name]

    def param_files_of(self, node_name: str) -> list[str]:
        found = self.subsystem_of(node_name)
        return list(found[1].param_files) if found else []

    def unit_of(self, node_name: str) -> str | None:
        found = self.subsystem_of(node_name)
        return found[1].unit if found else None

    def nodes_in_unit(self, unit: str) -> list[str]:
        """unit 에 속한 모든 노드. 재시작 영향 범위 계산에 쓴다 (§6.1, §8.1)."""
        out: list[str] = []
        for sub in self.subsystems.values():
            if sub.unit == unit:
                out.extend(normalize_node_name(n) for n in sub.nodes)
        return out

    def all_nodes(self) -> list[str]:
        return sorted(self._by_node)
