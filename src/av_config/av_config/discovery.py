"""노드 탐색, 파라미터 수집, drift 판정 (명세 §5.1).

표준 파라미터 서비스로 자동 수집하므로 처음 보는 드라이버도 원본 모드로 표시할 수
있고, 스키마가 매칭되면 의미 정보를 덧붙인 매핑 모드로 보여준다 (§5.2).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from rcl_interfaces.srv import DescribeParameters, GetParameters, ListParameters
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node

from .models import (
    DiscoverySnapshotDTO,
    NodeDTO,
    NodeParamsDTO,
    ParamDTO,
)
from .paramdto import build_param_dto, undeclared_param_dto
from .rosutil import (
    RosCallTimeout,
    RosServiceUnavailable,
    call_service,
    param_value_to_python,
)
from .schema import SchemaRegistry
from .services_map import ServicesMap
from .store import ParamStore, normalize_node_name

_log = logging.getLogger(__name__)

# 한 번에 describe/get 할 파라미터 수. 노드가 많은 파라미터를 가질 때
# 서비스 응답이 DDS 메시지 크기 한계를 넘지 않도록 나눈다.
_BATCH = 50

# ListParameters.Request.DEPTH_RECURSIVE
_DEPTH_RECURSIVE = 0


@dataclass
class _NodeClients:
    """노드 하나에 대한 파라미터 서비스 클라이언트 묶음."""

    list_params: object
    describe: object
    get: object


class Discovery:
    """파라미터 수집기.

    rclpy 클라이언트를 노드별로 캐시한다. 노드 목록은 바뀌므로 지연 생성한다.
    """

    def __init__(
        self,
        node: Node,
        services: ServicesMap,
        schemas: SchemaRegistry,
        store: ParamStore,
        timeout: float = 3.0,
    ) -> None:
        self._node = node
        self._services = services
        self._schemas = schemas
        self._store = store
        self._timeout = timeout
        self._group = ReentrantCallbackGroup()
        self._clients: dict[str, _NodeClients] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 그래프 조회
    # ------------------------------------------------------------------

    def live_nodes(self) -> list[str]:
        """DDS discovery 로 얻은 실행 중 노드 목록 (§5.1-1)."""
        out: list[str] = []
        for name, namespace in self._node.get_node_names_and_namespaces():
            ns = namespace.rstrip("/")
            out.append(f"{ns}/{name}")
        return sorted(out)

    def _split(self, fq_name: str) -> tuple[str, str]:
        fq = normalize_node_name(fq_name)
        namespace, _, name = fq.rpartition("/")
        return name, namespace or "/"

    def services_of(self, fq_name: str) -> set[str]:
        name, namespace = self._split(fq_name)
        try:
            found = self._node.get_service_names_and_types_by_node(name, namespace)
        except Exception as exc:  # 노드가 방금 사라진 경우
            _log.debug("%s 의 서비스 목록 조회 실패: %s", fq_name, exc)
            return set()
        return {srv for srv, _ in found}

    def published_topics(self, fq_name: str) -> list[str]:
        """노드가 발행하는 토픽. 온보딩 초안의 topics 후보로 쓴다 (§12.2-7)."""
        name, namespace = self._split(fq_name)
        try:
            published = self._node.get_publisher_names_and_types_by_node(name, namespace)
        except Exception as exc:
            _log.debug("%s 의 발행 토픽 조회 실패: %s", fq_name, exc)
            return []
        # /parameter_events, /rosout 은 모든 노드가 발행하므로 제외한다
        return sorted(
            topic
            for topic, _types in published
            if topic not in ("/parameter_events", "/rosout")
        )

    def downstream_of(self, fq_name: str) -> list[str]:
        """노드가 발행하는 토픽의 구독자를 모아 다운스트림 목록을 만든다 (§5.1-7).

        손으로 유지하는 affects_extra 대신 그래프에서 계산한다. 스키마 유지보수를
        줄이는 것이 §1.1 목표이기 때문이다.
        """
        myself = normalize_node_name(fq_name)
        result: set[str] = set()

        for topic in self.published_topics(fq_name):
            try:
                endpoints = self._node.get_subscriptions_info_by_topic(topic)
            except Exception as exc:
                _log.debug("%s 의 구독자 조회 실패: %s", topic, exc)
                continue
            for info in endpoints:
                ns = info.node_namespace.rstrip("/")
                other = f"{ns}/{info.node_name}"
                if other != myself:
                    result.add(other)
        return sorted(result)

    # ------------------------------------------------------------------
    # 파라미터 수집
    # ------------------------------------------------------------------

    async def _clients_for(self, fq_name: str) -> _NodeClients:
        async with self._lock:
            cached = self._clients.get(fq_name)
            if cached is not None:
                return cached
            base = normalize_node_name(fq_name)
            clients = _NodeClients(
                list_params=self._node.create_client(
                    ListParameters, f"{base}/list_parameters", callback_group=self._group
                ),
                describe=self._node.create_client(
                    DescribeParameters,
                    f"{base}/describe_parameters",
                    callback_group=self._group,
                ),
                get=self._node.create_client(
                    GetParameters, f"{base}/get_parameters", callback_group=self._group
                ),
            )
            self._clients[fq_name] = clients
            return clients

    async def collect_params(self, fq_name: str) -> NodeParamsDTO:
        """노드 하나의 파라미터를 수집한다 (§5.1-2~7).

        노드가 꺼져 있거나 start_parameter_services=false 면 parameter file 과
        스키마만으로 오프라인 목록을 돌려준다 (§5.3).
        """
        fq_name = normalize_node_name(fq_name)
        alive = fq_name in set(self.live_nodes())
        param_files = self._services.param_files_of(fq_name)
        saved = self._store.saved_values(fq_name, param_files)

        available_services = self.services_of(fq_name) if alive else None
        schema = self._schemas.match(fq_name, available_services)

        node_dto = self._build_node_dto(fq_name, alive, schema)

        if not alive:
            node_dto.unreachable_reason = "노드가 실행 중이 아니다"
            params = self._offline_params(saved, schema)
            node_dto.param_count = len(params)
            node_dto.drift_count = 0
            return NodeParamsDTO(node=node_dto, params=params)

        try:
            names = await self._list(fq_name)
        except (RosCallTimeout, RosServiceUnavailable) as exc:
            # §5.3: start_parameter_services=false 인 노드는 수집 불가로 표시한다.
            node_dto.unreachable_reason = (
                f"파라미터 서비스에 접근할 수 없다 ({exc}). "
                "start_parameter_services=false 일 수 있다."
            )
            params = self._offline_params(saved, schema)
            node_dto.param_count = len(params)
            return NodeParamsDTO(node=node_dto, params=params)

        descriptors = await self._describe(fq_name, names)
        values = await self._get(fq_name, names)

        params: list[ParamDTO] = []
        for name in names:
            desc = descriptors.get(name)
            runtime = values.get(name)
            entry = saved.get(name)
            dto = self._build_param_dto(name, desc, runtime, entry, schema)
            params.append(dto)

        # YAML 에만 있고 노드가 선언하지 않은 값. 설정은 했지만 효력이 없는 상태다.
        declared = set(names)
        for name, entry in sorted(saved.items()):
            if name in declared:
                continue
            params.append(undeclared_param_dto(name, entry))

        node_dto.param_count = len(params)
        node_dto.drift_count = sum(1 for p in params if p.drift)
        node_dto.downstream = self.downstream_of(fq_name)
        return NodeParamsDTO(node=node_dto, params=params)

    async def _list(self, fq_name: str) -> list[str]:
        clients = await self._clients_for(fq_name)
        request = ListParameters.Request()
        request.prefixes = []
        request.depth = _DEPTH_RECURSIVE
        response = await call_service(clients.list_params, request, self._timeout)
        return list(response.result.names)

    async def _describe(self, fq_name: str, names: list[str]) -> dict[str, object]:
        clients = await self._clients_for(fq_name)
        out: dict[str, object] = {}
        for chunk in _chunks(names, _BATCH):
            request = DescribeParameters.Request()
            request.names = chunk
            try:
                response = await call_service(clients.describe, request, self._timeout)
            except (RosCallTimeout, RosServiceUnavailable) as exc:
                _log.warning("%s describe_parameters 실패: %s", fq_name, exc)
                continue
            for descriptor in response.descriptors:
                out[descriptor.name] = descriptor
        return out

    async def _get(self, fq_name: str, names: list[str]) -> dict[str, object]:
        clients = await self._clients_for(fq_name)
        out: dict[str, object] = {}
        for chunk in _chunks(names, _BATCH):
            request = GetParameters.Request()
            request.names = chunk
            try:
                response = await call_service(clients.get, request, self._timeout)
            except (RosCallTimeout, RosServiceUnavailable) as exc:
                _log.warning("%s get_parameters 실패: %s", fq_name, exc)
                continue
            if len(response.values) != len(chunk):
                _log.warning(
                    "%s get_parameters 가 요청 %d개에 값 %d개를 돌려줬다. "
                    "받은 만큼만 표시한다.",
                    fq_name,
                    len(chunk),
                    len(response.values),
                )
            for name, value in zip(chunk, response.values, strict=False):
                out[name] = param_value_to_python(value)
        return out

    # ------------------------------------------------------------------
    # DTO 조립
    # ------------------------------------------------------------------

    def _build_node_dto(self, fq_name: str, alive: bool, schema: object | None) -> NodeDTO:
        found = self._services.subsystem_of(fq_name)
        subsystem_name = found[0] if found else None
        subsystem = found[1] if found else None
        return NodeDTO(
            name=fq_name,
            alive=alive,
            registered=subsystem is not None,
            subsystem=subsystem_name,
            unit=subsystem.unit if subsystem else None,
            param_files=list(subsystem.param_files) if subsystem else [],
            display_mode="mapped" if schema is not None else "raw",
            schema_driver=getattr(schema, "driver", None),
            # TODO(M5): launch 파일을 정적 검사해 인라인 parameters 사용을 찾는다
            # (§12.1-8). services.yaml 은 launch 명령 문자열만 가지고 있어 파일
            # 경로 해석이 필요하다. 그때까지는 판정하지 않는다.
            launch_rule_violation=False,
        )

    def _build_param_dto(
        self,
        name: str,
        descriptor: object | None,
        runtime_value: object,
        saved: object | None,
        schema: object | None,
        reachable: bool = True,
    ) -> ParamDTO:
        return build_param_dto(
            name=name,
            descriptor=descriptor,
            runtime_value=runtime_value,
            saved=saved,  # type: ignore[arg-type]
            param_schema=self._schemas.param_schema(schema, name),  # type: ignore[arg-type]
            reachable=reachable,
        )

    def _offline_params(self, saved: dict, schema: object | None) -> list[ParamDTO]:
        """노드에 질의할 수 없을 때 parameter file 과 스키마만으로 목록을 만든다.

        런타임 값을 모르므로 drift 는 판정하지 않는다 (reachable=False).
        """
        return [
            self._build_param_dto(name, None, None, entry, schema, reachable=False)
            for name, entry in sorted(saved.items())
        ]

    # ------------------------------------------------------------------
    # 스냅샷
    # ------------------------------------------------------------------

    async def snapshot(self) -> DiscoverySnapshotDTO:
        """노드 목록 화면용 요약 (§8.1 노드 목록).

        services.yaml 에 등록된 노드와 실행 중인 노드의 합집합을 돌려준다.
        등록됐지만 꺼진 노드도 보여야 오프라인 편집이 가능하다 (§5.3).
        """
        registered = set(self._services.all_nodes())
        live = set(self.live_nodes())
        targets = sorted(registered | live)

        results = await asyncio.gather(
            *(self.collect_params(n) for n in targets), return_exceptions=True
        )

        nodes: list[NodeDTO] = []
        for target, result in zip(targets, results, strict=True):
            if isinstance(result, BaseException):
                _log.error("%s 수집 실패: %s", target, result)
                nodes.append(
                    NodeDTO(
                        name=target,
                        alive=target in live,
                        unreachable_reason=f"수집 중 오류: {result}",
                    )
                )
            else:
                nodes.append(result.node)

        return DiscoverySnapshotDTO(
            collected_at=time.time(),
            nodes=nodes,
            schema_errors=self._schemas.errors,
        )


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]

