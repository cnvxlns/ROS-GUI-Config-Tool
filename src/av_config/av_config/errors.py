"""ROS 의존이 없는 예외 정의.

적용·검증 단계의 예외를 여기 두면 오케스트레이터(changesets.py)가 rclpy 를
import 하지 않고도 동작한다. 트랜잭션 로직을 ROS 없이 테스트할 수 있어야
하기 때문이다.
"""

from __future__ import annotations


class ApplyError(Exception):
    """적용 단계가 실패했다. 호출자는 롤백해야 한다 (명세 §6.2)."""


class VerifyError(Exception):
    """read-back 을 수행할 수 없었다 (명세 §6.1-6)."""


class ChangeSetBusy(Exception):
    """이미 실행 중인 change set 이 있다 (명세 §6.3).

    두 번째 요청은 대기열이 아니라 거부한다.
    """
