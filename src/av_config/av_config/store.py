"""parameter file 읽기와 저장값 머지 (명세 §5.1, §11.2).

쓰기(atomic write + git)는 M2 에서 추가한다. 이 모듈은 M1 조회 범위다.

핵심은 "저장값"의 정의다. 저장값은 단일 파일의 값이 아니라
services.yaml 의 param_files 를 등록 순서대로 머지한 결과다 (§5.1).
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

_log = logging.getLogger(__name__)

# ruamel 을 round-trip 모드로 쓴다. 주석과 키 순서를 보존해야 하기 때문이다(§7.1).
_yaml = YAML(typ="rt")
_yaml.preserve_quotes = True

ROS_PARAMS_KEY = "ros__parameters"


@dataclass(frozen=True)
class SavedEntry:
    """머지 결과의 한 항목. source 는 최종 값이 온 파일."""

    value: Any
    source: str


def normalize_node_name(name: str) -> str:
    """정규화된 노드 이름(선행 슬래시 포함)을 돌려준다."""
    name = name.strip()
    return name if name.startswith("/") else "/" + name


def _plain(value: Any) -> Any:
    """ruamel 스칼라를 JSON 직렬화 가능한 순수 파이썬 값으로 바꾼다."""
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return str(value)
    return value


def flatten_params(node: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """중첩 맵을 점 표기 파라미터 이름으로 펼친다.

    ros__parameters:
      device:
        udp_dest: "1.2.3.4"
    -> {"device.udp_dest": "1.2.3.4"}

    리스트는 배열 파라미터 값이므로 펼치지 않는다.
    """
    out: dict[str, Any] = {}
    for key, value in node.items():
        name = f"{prefix}{key}"
        if isinstance(value, Mapping):
            out.update(flatten_params(value, name + "."))
        else:
            out[name] = _plain(value)
    return out


def key_specificity(yaml_key: str, node_name: str) -> int | None:
    """YAML 노드 키가 node_name 에 맞으면 구체성 점수를, 아니면 None 을 준다.

    같은 파일 안에서는 구체성이 낮은 키(예: /**)를 먼저 적용하고 높은 키로 덮는다.
    ROS 2 의 와일드카드 해석과 같은 순서다.
    """
    key = normalize_node_name(str(yaml_key))
    node = normalize_node_name(node_name)

    if key == "/**":
        return 0
    if key.endswith("/**"):
        prefix = key[:-3] or "/"
        if node == prefix or node.startswith(prefix.rstrip("/") + "/"):
            return len([s for s in prefix.split("/") if s])
        return None
    if key.endswith("/*"):
        prefix = key[:-2] or "/"
        if node.startswith(prefix.rstrip("/") + "/"):
            rest = node[len(prefix.rstrip("/")) :]
            if rest.count("/") == 1:
                return len([s for s in prefix.split("/") if s])
        return None
    return 1000 if key == node else None


class ParamStore:
    """parameter file 저장소. root 는 /opt/av_config 에 해당한다."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._cache: dict[str, tuple[float, Any]] = {}

    def path_of(self, rel: str) -> Path:
        return self.root / rel

    def load_file(self, rel: str) -> Any:
        """parameter file 을 round-trip 로드한다. mtime 기준으로 캐시한다."""
        path = self.path_of(rel)
        if not path.is_file():
            _log.warning("parameter file 없음: %s", path)
            return None
        mtime = path.stat().st_mtime
        cached = self._cache.get(rel)
        if cached and cached[0] == mtime:
            return cached[1]
        with path.open("r", encoding="utf-8") as fh:
            doc = _yaml.load(fh)
        self._cache[rel] = (mtime, doc)
        return doc

    def saved_values(self, node_name: str, param_files: list[str]) -> dict[str, SavedEntry]:
        """노드의 저장값을 계산한다 (§5.1 저장값의 정의).

        해석 순서:
          1. param_files 를 나열 순서대로 적용한다. 뒤 파일이 앞을 덮는다.
          2. 한 파일 안에서는 구체성이 낮은 노드 키부터 적용한다.

        그래서 param_files 마지막에 오는 global.yaml 이 노드별 값을 덮는다.
        이는 의도된 순서다 (§11.2).
        """
        merged: dict[str, SavedEntry] = {}
        for rel in param_files:
            doc = self.load_file(rel)
            if not isinstance(doc, Mapping):
                continue

            matched: list[tuple[int, dict[str, Any]]] = []
            for yaml_key, body in doc.items():
                score = key_specificity(str(yaml_key), node_name)
                if score is None or not isinstance(body, Mapping):
                    continue
                params = body.get(ROS_PARAMS_KEY)
                if not isinstance(params, Mapping):
                    continue
                matched.append((score, flatten_params(params)))

            for _, flat in sorted(matched, key=lambda item: item[0]):
                for name, value in flat.items():
                    merged[name] = SavedEntry(value=value, source=rel)

        return merged


def is_drift(saved: SavedEntry | None, runtime: Any, reachable: bool) -> bool:
    """저장값과 런타임값의 불일치 판정 (§5.1-5, §9.2 R4).

    노드에 질의할 수 없으면(reachable=False) 런타임 값이 없다. 비교 대상이 없는
    것은 drift 가 아니라 '알 수 없음'이다. 여기서 drift 로 표시하면 꺼진 노드의
    모든 파라미터가 경고로 떠서 R4 가 노이즈가 된다.

    YAML 에 없는 파라미터도 drift 가 아니다. 노드 기본값을 쓰고 있을 뿐이다.
    """
    if saved is None or not reachable:
        return False
    return not values_equal(saved.value, runtime)


def values_equal(saved: Any, runtime: Any) -> bool:
    """drift 판정용 비교 (§5.1-5, §9.2 R4).

    YAML 의 10 과 런타임의 10.0 을 같은 값으로 본다. 숫자 타입 차이로 drift 가
    오탐되면 R4 가 노이즈가 되기 때문이다.

    bool 은 int 의 하위 타입이지만 ROS 2 파라미터에서는 다른 타입이므로 섞지 않는다.
    """
    if isinstance(saved, bool) or isinstance(runtime, bool):
        return type(saved) is type(runtime) and bool(saved) == bool(runtime)
    if isinstance(saved, (int, float)) and isinstance(runtime, (int, float)):
        return float(saved) == float(runtime)
    if isinstance(saved, (list, tuple)) and isinstance(runtime, (list, tuple)):
        if len(saved) != len(runtime):
            return False
        return all(values_equal(a, b) for a, b in zip(saved, runtime, strict=True))
    return saved == runtime


# --------------------------------------------------------------------------
# 쓰기 (§6.1-4)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class WriteTarget:
    """파라미터 하나를 어느 파일의 어느 노드 키에 쓸지."""

    rel_path: str
    node_key: str
    param: str
    value: Any


class ParamWriteError(Exception):
    """parameter file 저장이 실패했다."""


def _set_dotted(mapping: MutableMapping[str, Any], dotted: str, value: Any) -> None:
    """점 표기 이름으로 값을 넣는다.

    파일에 이미 리터럴 점 키가 있으면 그것을 쓰고, 없으면 중첩 맵을 만든다.
    둘을 섞어 같은 파라미터를 두 번 쓰는 것을 막기 위함이다.
    """
    if dotted in mapping:
        mapping[dotted] = value
        return

    parts = dotted.split(".")
    if len(parts) == 1:
        mapping[dotted] = value
        return

    cursor: MutableMapping[str, Any] = mapping
    for part in parts[:-1]:
        existing = cursor.get(part)
        if not isinstance(existing, MutableMapping):
            if existing is not None:
                raise ParamWriteError(
                    f"{dotted} 를 쓸 수 없다: {part} 가 맵이 아닌 값을 이미 갖고 있다"
                )
            cursor[part] = {}
        cursor = cursor[part]  # type: ignore[assignment]
    cursor[parts[-1]] = value


class ParamWriter:
    """parameter file 에 atomic write 한다.

    쓰기 대상 파일을 고르는 규칙:
      - 그 파라미터의 현재 저장값이 온 파일(saved_source)에 쓴다. 뒤 파일이 앞을
        덮으므로, 이기고 있는 파일에 써야 실제로 반영된다.
      - 어느 파일에도 없으면 param_files 의 첫 파일(노드 전용 파일)에 쓴다.
        뒤 파일이 그 파라미터를 언급하지 않으므로 덮일 일이 없다.
      - 노드 키는 항상 정규화된 전체 노드 이름을 쓴다. /** 아래에 쓰면 다른 노드까지
        바뀌기 때문이다.
    """

    def __init__(self, store: ParamStore) -> None:
        self.store = store

    def plan_targets(
        self,
        node_name: str,
        changes: dict[str, Any],
        param_files: list[str],
    ) -> list[WriteTarget]:
        if not param_files:
            raise ParamWriteError(
                f"{node_name} 에 연결된 parameter file 이 없다. services.yaml 을 확인하라."
            )
        node = normalize_node_name(node_name)
        saved = self.store.saved_values(node, param_files)

        targets: list[WriteTarget] = []
        for param, value in changes.items():
            entry = saved.get(param)
            rel = entry.source if entry is not None else param_files[0]
            if entry is not None and rel != param_files[0]:
                _log.info(
                    "%s 의 %s 는 %s 에서 이기고 있으므로 그 파일에 쓴다",
                    node,
                    param,
                    rel,
                )
            targets.append(
                WriteTarget(rel_path=rel, node_key=node, param=param, value=value)
            )
        return targets

    def apply(self, targets: list[WriteTarget]) -> list[str]:
        """대상 파일들을 수정하고 쓴 파일 목록을 돌려준다.

        파일 단위로 임시 파일에 쓰고 rename 으로 교체한다. 저장 중 크래시로
        설정 파일이 반쯤 잘리면 노드가 아예 뜨지 않기 때문이다.
        """
        by_file: dict[str, list[WriteTarget]] = {}
        for target in targets:
            by_file.setdefault(target.rel_path, []).append(target)

        written: list[str] = []
        for rel, items in by_file.items():
            path = self.store.path_of(rel)
            if not path.is_file():
                raise ParamWriteError(f"{path} 가 없다")

            with path.open("r", encoding="utf-8") as fh:
                doc = _yaml.load(fh)
            if doc is None:
                raise ParamWriteError(f"{path} 가 비어 있다")

            for item in items:
                node_block = doc.get(item.node_key)
                if node_block is None:
                    # 같은 노드를 선행 슬래시 없이 적어 둔 경우를 찾아본다
                    bare = item.node_key.lstrip("/")
                    node_block = doc.get(bare)
                    if node_block is not None:
                        item = WriteTarget(item.rel_path, bare, item.param, item.value)
                if node_block is None:
                    doc[item.node_key] = {ROS_PARAMS_KEY: {}}
                    node_block = doc[item.node_key]
                if ROS_PARAMS_KEY not in node_block:
                    node_block[ROS_PARAMS_KEY] = {}
                _set_dotted(node_block[ROS_PARAMS_KEY], item.param, item.value)

            self._atomic_write(path, doc)
            self.store._cache.pop(rel, None)
            written.append(rel)
        return sorted(written)

    def _atomic_write(self, path: Path, doc: Any) -> None:
        tmp = path.with_name(path.name + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                _yaml.dump(doc, fh)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            raise ParamWriteError(f"{path} 저장 실패: {exc}") from exc

    def file_hashes(self, param_files: list[str]) -> dict[str, str]:
        """저장 전 외부 수정 감지용 해시 (§6.3)."""
        out: dict[str, str] = {}
        for rel in param_files:
            path = self.store.path_of(rel)
            if path.is_file():
                out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        return out
