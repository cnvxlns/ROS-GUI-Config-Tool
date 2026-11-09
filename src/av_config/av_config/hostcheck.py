"""호스트 읽기 전용 점검 (명세 §9.1, §4.4 checks).

1차 범위에서 호스트 설정은 변경하지 않고 점검만 한다 (§4.1 host 계층).
"""

from __future__ import annotations

import fcntl
import logging
import socket
import struct
from pathlib import Path

_log = logging.getLogger(__name__)

_SIOCGIFADDR = 0x8915
_NET_DIR = Path("/sys/class/net")


def interface_names() -> list[str]:
    if not _NET_DIR.is_dir():
        return []
    return sorted(p.name for p in _NET_DIR.iterdir() if p.name != "lo")


def ipv4_of(interface: str) -> str | None:
    """인터페이스의 IPv4 주소. 없으면 None."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        packed = fcntl.ioctl(
            sock.fileno(),
            _SIOCGIFADDR,
            struct.pack("256s", interface[:15].encode()),
        )
        return socket.inet_ntoa(packed[20:24])
    except OSError:
        return None
    finally:
        sock.close()


def host_ipv4_addresses() -> set[str]:
    """호스트의 모든 NIC IPv4 주소 (lo 제외)."""
    found: set[str] = set()
    for name in interface_names():
        address = ipv4_of(name)
        if address:
            found.add(address)
    return found


def must_match_host_nic_ip(value: object) -> str | None:
    """udp_dest 가 호스트 NIC IP 와 일치하는지 (§9.2 R2).

    불일치면 센서가 데이터를 아무도 듣지 않는 주소로 보내게 된다.
    통과하면 None, 실패하면 사유 문자열을 돌려준다.
    """
    if not isinstance(value, str) or not value:
        return "IP 주소 문자열이 아니다"
    addresses = host_ipv4_addresses()
    if not addresses:
        # NIC 주소를 읽을 수 없으면 막지 않는다. 점검 실패로 적용을 거부하면
        # 컨테이너나 제한된 환경에서 아무것도 바꿀 수 없게 된다.
        _log.warning("호스트 NIC 주소를 읽을 수 없어 must_match_host_nic_ip 를 건너뛴다")
        return None
    if value in addresses:
        return None
    return (
        f"{value} 는 호스트 NIC 주소가 아니다. 현재 주소: {', '.join(sorted(addresses))}. "
        "센서가 수신자 없는 주소로 데이터를 보내게 된다."
    )


# 스키마의 checks 이름 -> 구현
CHECKS = {
    "must_match_host_nic_ip": must_match_host_nic_ip,
}
