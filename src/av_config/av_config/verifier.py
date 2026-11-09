"""반영 확인 (명세 §6.1-6).

"적용했다"가 아니라 "반영됐다"를 확인한다 (핵심 원칙 3).

계층별로 read-back 경로가 다르다. layer: device 는 노드 파라미터를 되읽으면
요청값이 그대로 돌아오므로 반드시 스키마의 readback_via 로 장치에서 읽는다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus
from rcl_interfaces.srv import GetParameters
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy

from .errors import VerifyError
from .models import Layer, ParamDTO, ServiceRef
from .rosutil import (
    RosCallTimeout,
    RosServiceUnavailable,
    call_service,
    param_value_to_python,
)
from .store import normalize_node_name, values_equal

_log = logging.getLogger(__name__)

# 재시작 직후 발행자가 뜰 때까지 기다리는 시간 (§6.1-6)
_PUBLISHER_WAIT = 20.0


@dataclass
class ReadBack:
    value: Any
    source: str


@dataclass
class HzSample:
    topic: str
    count: int
    duration: float

    @property
    def hz(self) -> float:
        return self.count / self.duration if self.duration > 0 else 0.0


@dataclass
class DiagnosticsCache:
    """최근 /diagnostics 상태. hardware_id 로 노드와 맞춘다 (§12.1-6)."""

    by_name: dict[str, tuple[int, str, float]] = field(default_factory=dict)

    def update(self, msg: DiagnosticArray) -> None:
        now = time.time()
        for status in msg.status:
            key = status.hardware_id or status.name
            self.by_name[key] = (int(status.level), status.message, now)

    def worst(self, keys: list[str], stale_after: float = 10.0) -> tuple[int, str] | None:
        now = time.time()
        worst: tuple[int, str] | None = None
        for key in keys:
            entry = self.by_name.get(key)
            if entry is None:
                continue
            level, message, when = entry
            if now - when > stale_after:
                level, message = DiagnosticStatus.STALE, "STALE"
            if worst is None or level > worst[0]:
                worst = (level, message)
        return worst


class Verifier:
    def __init__(self, node: Node, timeout: float = 5.0) -> None:
        self._node = node
        self._timeout = timeout
        self._group = ReentrantCallbackGroup()
        self._get_clients: dict[str, Any] = {}
        self._service_clients: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self.diagnostics = DiagnosticsCache()
        self._diag_sub = node.create_subscription(
            DiagnosticArray,
            "/diagnostics",
            lambda msg: self.diagnostics.update(msg),
            QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE),
            callback_group=self._group,
        )

    # ------------------------------------------------------------------
    # 계층별 read-back
    # ------------------------------------------------------------------

    async def read_back(
        self,
        node_name: str,
        param: ParamDTO,
        readback_via: ServiceRef | None = None,
    ) -> ReadBack:
        """파라미터 하나를 되읽는다. 계층에 따라 경로가 갈린다.

        readback_via 는 호출자가 스키마에서 꺼내 넘긴다. ParamDTO 는 전송용이라
        스키마 참조를 들고 있지 않다.
        """
        if param.layer is Layer.DEVICE:
            if readback_via is None:
                # 스키마 검증이 이를 막지만, 원본 모드 노드에서는 layer 를 모른다.
                raise VerifyError(
                    f"{param.name} 은 device 계층인데 readback_via 가 없다. "
                    "장치 반영 여부를 확인할 수 없다 (명세 §4.4)."
                )
            return await self._read_from_device(readback_via)
        return await self._read_from_node(node_name, param.name)

    async def _read_from_node(self, node_name: str, param: str) -> ReadBack:
        fq = normalize_node_name(node_name)
        client = await self._get_client(fq)
        request = GetParameters.Request()
        request.names = [param]
        try:
            response = await call_service(client, request, self._timeout)
        except (RosCallTimeout, RosServiceUnavailable) as exc:
            raise VerifyError(f"{fq}/get_parameters 호출 실패: {exc}") from exc
        if not response.values:
            raise VerifyError(f"{fq} 가 {param} 값을 돌려주지 않았다")
        return ReadBack(
            value=param_value_to_python(response.values[0]),
            source=f"{fq}/get_parameters",
        )

    async def _read_from_device(self, ref: ServiceRef) -> ReadBack:
        """장치 진실 경로에서 읽는다. 센서가 값을 거부·보정했으면 여기서 드러난다."""
        from rosidl_runtime_py.utilities import get_service

        srv_type = None
        for name, types in self._node.get_service_names_and_types():
            if name == ref.service and types:
                srv_type = get_service(types[0])
                break
        if srv_type is None:
            raise VerifyError(
                f"{ref.service} 를 그래프에서 찾을 수 없다. 장치 값을 확인할 수 없다."
            )

        client = await self._driver_client(ref.service, srv_type)
        try:
            response = await call_service(client, srv_type.Request(), self._timeout)
        except (RosCallTimeout, RosServiceUnavailable) as exc:
            raise VerifyError(f"{ref.service} 호출 실패: {exc}") from exc

        raw = _pick_response_field(response, ref.response_field)
        value = _walk_json_path(raw, ref.json_path) if ref.json_path else raw
        return ReadBack(value=value, source=ref.service)

    async def _get_client(self, fq: str) -> Any:
        async with self._lock:
            client = self._get_clients.get(fq)
            if client is None:
                client = self._node.create_client(
                    GetParameters, f"{fq}/get_parameters", callback_group=self._group
                )
                self._get_clients[fq] = client
            return client

    async def _driver_client(self, service: str, srv_type: Any) -> Any:
        async with self._lock:
            client = self._service_clients.get(service)
            if client is None:
                client = self._node.create_client(
                    srv_type, service, callback_group=self._group
                )
                self._service_clients[service] = client
            return client

    # ------------------------------------------------------------------
    # 데이터 흐름
    # ------------------------------------------------------------------

    async def measure_hz(self, topic: str, duration: float = 2.0) -> HzSample:
        """토픽 발행 주기를 실측한다 (§6.1-6, §9.2 R1/R5).

        값이 바뀌어도 데이터가 안 오면 반영 실패로 본다.
        """
        from rosidl_runtime_py.utilities import get_message

        # 재시작 직후에는 서비스가 먼저 뜨고 발행자가 조금 늦게 뜬다
        # (lifecycle 드라이버는 on_configure 에서 서비스, on_activate 에서
        # publisher 를 만든다). 한 번 보고 없다고 단정하면 정상 적용이 롤백된다.
        msg_type = None
        deadline = time.monotonic() + _PUBLISHER_WAIT
        while True:
            for name, types in self._node.get_topic_names_and_types():
                if name == topic and types:
                    msg_type = get_message(types[0])
                    break
            if msg_type is not None or time.monotonic() >= deadline:
                break
            await asyncio.sleep(0.5)
        if msg_type is None:
            raise VerifyError(
                f"{topic} 의 타입을 {_PUBLISHER_WAIT:.0f}초 안에 알 수 없었다. "
                "발행자가 없을 수 있다."
            )

        counter = {"n": 0}
        # 센서 데이터는 보통 best effort 다. reliable 로 구독하면 아무것도 못 받는다.
        subscription = self._node.create_subscription(
            msg_type,
            topic,
            lambda _msg: counter.__setitem__("n", counter["n"] + 1),
            QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT),
            callback_group=self._group,
        )
        started = time.monotonic()
        try:
            await asyncio.sleep(duration)
        finally:
            self._node.destroy_subscription(subscription)
        return HzSample(
            topic=topic, count=counter["n"], duration=time.monotonic() - started
        )

    def diagnostics_ok(self, keys: list[str]) -> tuple[bool, str | None]:
        worst = self.diagnostics.worst(keys)
        if worst is None:
            return True, None  # 보고가 없으면 판정하지 않는다
        level, message = worst
        if level == DiagnosticStatus.OK:
            return True, None
        name = {
            DiagnosticStatus.WARN: "WARN",
            DiagnosticStatus.ERROR: "ERROR",
            DiagnosticStatus.STALE: "STALE",
        }.get(level, str(level))
        return False, f"/diagnostics {name}: {message}"

    @staticmethod
    def matches(expected: Any, actual: Any) -> bool:
        return values_equal(expected, actual)


# --------------------------------------------------------------------------


def _pick_response_field(response: Any, field_name: str | None) -> Any:
    if field_name is not None:
        if not hasattr(response, field_name):
            raise VerifyError(
                f"응답에 {field_name} 필드가 없다. 스키마의 readback_via.response_field "
                "를 확인하라."
            )
        return getattr(response, field_name)
    fields = list(response.get_fields_and_field_types())
    if not fields:
        raise VerifyError("응답에 읽을 필드가 없다")
    return getattr(response, fields[0])


def _walk_json_path(raw: Any, path: str) -> Any:
    """"$.config.lidar_mode" 같은 점 표기로 값을 꺼낸다.

    배열 인덱스와 필터는 지원하지 않는다. 지원 범위를 넘는 경로는 오류로 알린다.
    """
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode(errors="replace")
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VerifyError(f"응답이 JSON 이 아니다: {exc}") from exc
    else:
        data = raw

    cleaned = path.lstrip("$").lstrip(".")
    if "[" in cleaned:
        raise VerifyError(
            f"지원하지 않는 json_path 다: {path}. 점 표기만 쓸 수 있다."
        )
    cursor = data
    for part in cleaned.split("."):
        if not part:
            continue
        if not isinstance(cursor, dict) or part not in cursor:
            raise VerifyError(f"json_path {path} 의 {part} 를 찾을 수 없다")
        cursor = cursor[part]
    return cursor
