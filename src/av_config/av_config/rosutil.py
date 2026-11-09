"""rclpy 와 asyncio 를 잇는 헬퍼 (명세 §7.1).

rclpy executor 는 별도 스레드에서 spin 하고, FastAPI 핸들러는 서비스 호출 future 를
asyncio 로 기다린다. rclpy 의 Future 는 asyncio 와 호환되지 않으므로 여기서 변환한다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from rcl_interfaces.msg import ParameterType as RosParameterType
from rcl_interfaces.msg import ParameterValue

_log = logging.getLogger(__name__)


class RosCallTimeout(Exception):
    """서비스 호출이 제한 시간 안에 끝나지 않았다 (§6.1-5 타임아웃)."""


class RosServiceUnavailable(Exception):
    """서비스가 준비되지 않았다. 노드가 꺼져 있거나 서비스를 제공하지 않는다."""


async def await_ros_future(ros_future: Any, timeout: float) -> Any:
    """rclpy Future 를 asyncio 로 기다린다.

    완료 콜백은 executor 스레드에서 호출되므로 call_soon_threadsafe 로 넘긴다.
    """
    loop = asyncio.get_running_loop()
    bridged: asyncio.Future[Any] = loop.create_future()

    def _on_done(fut: Any) -> None:
        if bridged.done():
            return
        try:
            result = fut.result()
        except Exception as exc:
            loop.call_soon_threadsafe(_set_exception, bridged, exc)
            return
        loop.call_soon_threadsafe(_set_result, bridged, result)

    def _set_result(fut: asyncio.Future[Any], value: Any) -> None:
        if not fut.done():
            fut.set_result(value)

    def _set_exception(fut: asyncio.Future[Any], exc: BaseException) -> None:
        if not fut.done():
            fut.set_exception(exc)

    ros_future.add_done_callback(_on_done)
    try:
        return await asyncio.wait_for(bridged, timeout)
    except TimeoutError as exc:
        ros_future.cancel()
        raise RosCallTimeout(f"{timeout}초 안에 응답 없음") from exc


async def wait_service_ready(client: Any, timeout: float) -> None:
    """서비스가 준비될 때까지 기다린다. 이벤트 루프를 막지 않는다."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not client.service_is_ready():
        if asyncio.get_running_loop().time() >= deadline:
            raise RosServiceUnavailable(client.srv_name)
        await asyncio.sleep(0.02)


async def call_service(client: Any, request: Any, timeout: float) -> Any:
    """서비스를 호출하고 응답을 기다린다. 준비 대기와 호출에 같은 예산을 나눠 쓴다."""
    await wait_service_ready(client, timeout / 2)
    return await await_ros_future(client.call_async(request), timeout)


# --------------------------------------------------------------------------
# ParameterValue 변환
# --------------------------------------------------------------------------

_VALUE_FIELD: dict[int, str] = {
    RosParameterType.PARAMETER_BOOL: "bool_value",
    RosParameterType.PARAMETER_INTEGER: "integer_value",
    RosParameterType.PARAMETER_DOUBLE: "double_value",
    RosParameterType.PARAMETER_STRING: "string_value",
    RosParameterType.PARAMETER_BYTE_ARRAY: "byte_array_value",
    RosParameterType.PARAMETER_BOOL_ARRAY: "bool_array_value",
    RosParameterType.PARAMETER_INTEGER_ARRAY: "integer_array_value",
    RosParameterType.PARAMETER_DOUBLE_ARRAY: "double_array_value",
    RosParameterType.PARAMETER_STRING_ARRAY: "string_array_value",
}


def param_value_to_python(value: ParameterValue) -> Any:
    """ParameterValue 를 JSON 직렬화 가능한 값으로 바꾼다.

    rclpy 는 배열 파라미터를 array.array 나 numpy 배열로 주므로 list 로 변환한다.
    byte_array 는 bytes 이므로 정수 리스트로 바꾼다.
    """
    field_name = _VALUE_FIELD.get(value.type)
    if field_name is None:
        return None  # PARAMETER_NOT_SET

    raw = getattr(value, field_name)
    if value.type == RosParameterType.PARAMETER_BYTE_ARRAY:
        return [int(b) for b in raw]
    if value.type in (
        RosParameterType.PARAMETER_BOOL_ARRAY,
        RosParameterType.PARAMETER_INTEGER_ARRAY,
        RosParameterType.PARAMETER_DOUBLE_ARRAY,
        RosParameterType.PARAMETER_STRING_ARRAY,
    ):
        return list(raw)
    return raw
