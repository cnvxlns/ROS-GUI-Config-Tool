"""설정 적용 (명세 §6.1-5).

계획 순서대로 runtime(set_parameters) -> reconfigure(전용 서비스) ->
restart(systemctl restart) 를 실행한다. 각 단계에 타임아웃을 둔다.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
from typing import Any

from rcl_interfaces.msg import Parameter, ParameterValue
from rcl_interfaces.srv import SetParameters
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node

from .errors import ApplyError
from .models import ParamType, ServiceRef
from .rosutil import RosCallTimeout, RosServiceUnavailable, call_service
from .store import normalize_node_name

_log = logging.getLogger(__name__)

_RESTART_TIMEOUT = 60.0


def _to_parameter_value(ptype: int, value: Any) -> ParameterValue:
    pv = ParameterValue()
    pv.type = int(ptype)
    if ptype == ParamType.BOOL:
        pv.bool_value = bool(value)
    elif ptype == ParamType.INTEGER:
        pv.integer_value = int(value)
    elif ptype == ParamType.DOUBLE:
        pv.double_value = float(value)
    elif ptype == ParamType.STRING:
        pv.string_value = str(value)
    elif ptype == ParamType.BYTE_ARRAY:
        pv.byte_array_value = bytes(int(b) for b in value)
    elif ptype == ParamType.BOOL_ARRAY:
        pv.bool_array_value = [bool(b) for b in value]
    elif ptype == ParamType.INTEGER_ARRAY:
        pv.integer_array_value = [int(b) for b in value]
    elif ptype == ParamType.DOUBLE_ARRAY:
        pv.double_array_value = [float(b) for b in value]
    elif ptype == ParamType.STRING_ARRAY:
        pv.string_array_value = [str(b) for b in value]
    else:
        raise ApplyError(f"적용할 수 없는 파라미터 타입 {ptype}")
    return pv


class Applier:
    def __init__(self, node: Node, timeout: float = 5.0) -> None:
        self._node = node
        self._timeout = timeout
        self._group = ReentrantCallbackGroup()
        self._set_clients: dict[str, Any] = {}
        self._service_clients: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._systemctl_user = os.environ.get("AV_CONFIG_SYSTEMCTL_USER") == "1"

    # ------------------------------------------------------------------
    # runtime
    # ------------------------------------------------------------------

    async def set_parameters(
        self, node_name: str, values: dict[str, tuple[int, Any]]
    ) -> dict[str, str | None]:
        """실행 중 반영. 파라미터별로 None(성공) 또는 거부 사유를 돌려준다.

        드라이버가 거부하면 SetParametersResult.reason 에 사유가 온다 (§12.1-3).
        """
        fq = normalize_node_name(node_name)
        client = await self._set_client(fq)

        request = SetParameters.Request()
        names: list[str] = []
        for name, (ptype, value) in values.items():
            parameter = Parameter()
            parameter.name = name
            parameter.value = _to_parameter_value(ptype, value)
            request.parameters.append(parameter)
            names.append(name)

        try:
            response = await call_service(client, request, self._timeout)
        except (RosCallTimeout, RosServiceUnavailable) as exc:
            raise ApplyError(f"{fq}/set_parameters 호출 실패: {exc}") from exc

        if len(response.results) != len(names):
            # 서비스 계약 위반이다. 어느 파라미터가 성공했는지 알 수 없으므로
            # 성공으로 넘기지 않는다. 호출자가 롤백한다.
            raise ApplyError(
                f"{fq}/set_parameters 가 요청 {len(names)}개에 결과 "
                f"{len(response.results)}개를 돌려줬다. 반영 여부를 판단할 수 없다."
            )
        out: dict[str, str | None] = {}
        for name, result in zip(names, response.results, strict=True):
            out[name] = None if result.successful else (result.reason or "사유 없음")
        return out

    async def _set_client(self, fq: str) -> Any:
        async with self._lock:
            client = self._set_clients.get(fq)
            if client is None:
                client = self._node.create_client(
                    SetParameters, f"{fq}/set_parameters", callback_group=self._group
                )
                self._set_clients[fq] = client
            return client

    # ------------------------------------------------------------------
    # reconfigure
    # ------------------------------------------------------------------

    async def call_driver_service(self, ref: ServiceRef, value: Any) -> Any:
        """드라이버 전용 서비스를 호출한다 (§4.2 reconfigure).

        서비스 타입은 ROS 그래프에서 찾아 동적으로 로드한다. 타입을 찾을 수
        없으면 실패로 처리한다 — 조용히 넘기면 적용했는지 알 수 없게 된다.
        """
        srv_type = self._resolve_service_type(ref.service)
        client = await self._driver_client(ref.service, srv_type)
        request = srv_type.Request()

        if ref.request_field is not None:
            if not hasattr(request, ref.request_field):
                raise ApplyError(
                    f"{ref.service} 요청에 {ref.request_field} 필드가 없다. "
                    "스키마의 apply_via.request_field 를 확인하라."
                )
            setattr(request, ref.request_field, value)

        try:
            return await call_service(client, request, self._timeout)
        except (RosCallTimeout, RosServiceUnavailable) as exc:
            raise ApplyError(f"{ref.service} 호출 실패: {exc}") from exc

    def _resolve_service_type(self, service: str) -> Any:
        from rosidl_runtime_py.utilities import get_service

        for name, types in self._node.get_service_names_and_types():
            if name == service and types:
                return get_service(types[0])
        raise ApplyError(
            f"{service} 를 그래프에서 찾을 수 없다. 드라이버가 이 서비스를 "
            "제공하는지 ros2 service list 로 확인하라."
        )

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
    # restart
    # ------------------------------------------------------------------

    def _systemctl_command(self) -> list[str]:
        if self._systemctl_user:
            return ["systemctl", "--user"]
        prefix = shlex.split(os.environ.get("AV_CONFIG_SYSTEMCTL_PREFIX", ""))
        return [*prefix, "systemctl"]

    async def check_restart_units(self, units: list[str]) -> None:
        """Check the live sensor service before writing parameters."""
        if not self._systemctl_user:
            return
        for unit in units:
            try:
                await self._run_systemctl("is-active", [unit], timeout=5.0)
            except ApplyError as exc:
                raise ApplyError(
                    f"{unit} 이 실행 중이 아닙니다. 기존 센서 터미널을 종료한 뒤 "
                    "같은 워크스페이스에서 ./run_live_1_sensors.sh --managed 로 시작하세요."
                ) from exc

    async def restart_units(self, units: list[str]) -> None:
        """unit 을 일괄 재시작한다 (§6.1 전역 변경 처리).

        순차가 아니라 한 번의 systemctl 호출로 묶는다. 전역 값은 노드 간 일관성이
        전제이므로 중간 상태를 만들지 않는다.

        권한은 polkit 규칙으로 좁히는 것을 권장한다. sudo 를 쓰는 경우 유닛 이름을
        와일드카드 없이 정확히 못박아야 한다 (§10.3).
        """
        if not units:
            return
        _log.info("재시작: %s", ", ".join(units))
        await self._run_systemctl("restart", units, timeout=_RESTART_TIMEOUT)

    async def _run_systemctl(self, operation: str, units: list[str], timeout: float) -> None:
        for unit in units:
            if not _looks_like_unit(unit):
                raise ApplyError(f"unit 이름이 올바르지 않다: {unit!r}")

        command = [*self._systemctl_command(), operation, *units]
        try:
            proc = await asyncio.create_subprocess_exec(
                *command, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
            )
        except OSError as exc:
            raise ApplyError(f"systemctl {operation} 실행 실패: {exc}") from exc
        try:
            _stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError as exc:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            raise ApplyError(f"systemctl {operation} 이 {timeout}초 안에 끝나지 않았다") from exc

        if proc.returncode != 0:
            raise ApplyError(
                f"systemctl {operation} 실패 (exit {proc.returncode}): "
                f"{stderr.decode(errors='replace').strip()}"
            )


def _looks_like_unit(name: str) -> bool:
    """systemd 유닛 이름만 통과시킨다. 셸 메타문자와 경로를 막는다."""
    if not name or len(name) > 128 or not name.endswith(".service"):
        return False
    return all(ch.isalnum() or ch in "-_.@" for ch in name)
