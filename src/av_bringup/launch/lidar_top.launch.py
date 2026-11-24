"""lidar_top 서브시스템 bringup.

services.yaml 의 `launch: "ros2 launch av_bringup lidar_top.launch.py"` 가
이 파일이다.

핵심 규칙 하나만 지킨다. **파라미터를 인라인으로 적지 않는다.** services.yaml 의
param_files 를 같은 순서로 넘기는 일만 한다.

    param_files: [params/lidar_top.yaml, params/global.yaml]

순서가 곧 우선순위다. 뒤 파일이 앞 파일을 덮으므로 global.yaml 이 노드별 값을
덮는다(명세 §5.1). ParamStore 의 머지 순서와 반드시 같아야 하고, 어긋나면 Config
App 이 "저장값"으로 계산한 값과 노드의 실제 값이 달라져 해소할 수 없는 drift 로
보인다.

ouster_ros 의 driver.launch.py 를 include 하지 않는 이유:
  그 launch 는 params_file 을 **하나만** 받는다(0.15.1 의 launch/driver.launch.py
  에서 확인). 두 파일을 순서대로 넘길 수 없고, rviz 도 기본으로 켠다. 그래서
  os_driver 노드 기동과 lifecycle 전이를 여기서 직접 한다. lifecycle 전이 순서는
  driver.launch.py 와 같다(configure -> inactive 되면 activate).

노드 이름과 namespace 는 인자로 열어두지 않는다. services.yaml 의
nodes: ["/ouster/os_driver"] 와 스키마의 node_name_regex ^/ouster/os_driver$ 가
이 이름에 못박혀 있다. 바꾸려면 세 곳을 같이 바꿔야 한다.
"""

import launch
import lifecycle_msgs.msg
from launch.actions import (
    DeclareLaunchArgument,
    EmitEvent,
    LogInfo,
    RegisterEventHandler,
)
from launch.events import matches_action
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import LifecycleNode
from launch_ros.event_handlers import OnStateTransition
from launch_ros.events.lifecycle import ChangeState

#: services.yaml 의 param_files 와 같은 순서여야 한다.
PARAM_FILES = ("params/lidar_top.yaml", "params/global.yaml")

OUSTER_NS = "ouster"
OS_DRIVER_NAME = "os_driver"


def generate_launch_description():
    # 설정 루트. Config App 의 AV_CONFIG_ROOT 와 같은 곳을 가리켜야 한다.
    # 배포 기본값은 /opt/av_config 이다. 리포의 config/ 는 템플릿이므로 개발 중에도
    # tools/init_config_root.py 로 만든 복사본을 config_root:= 로 넘긴다.
    config_root_arg = DeclareLaunchArgument(
        "config_root",
        default_value="/opt/av_config",
        description="services.yaml 과 params/ 가 있는 설정 루트",
    )
    config_root = LaunchConfiguration("config_root")

    parameters = [PathJoinSubstitution([config_root, *rel.split("/")]) for rel in PARAM_FILES]

    os_driver = LifecycleNode(
        package="ouster_ros",
        executable="os_driver",
        name=OS_DRIVER_NAME,
        namespace=OUSTER_NS,
        parameters=parameters,
        output="screen",
    )

    configure_event = EmitEvent(
        event=ChangeState(
            lifecycle_node_matcher=matches_action(os_driver),
            transition_id=lifecycle_msgs.msg.Transition.TRANSITION_CONFIGURE,
        )
    )

    activate_event = RegisterEventHandler(
        OnStateTransition(
            target_lifecycle_node=os_driver,
            goal_state="inactive",
            entities=[
                LogInfo(msg="os_driver activating..."),
                EmitEvent(
                    event=ChangeState(
                        lifecycle_node_matcher=matches_action(os_driver),
                        transition_id=lifecycle_msgs.msg.Transition.TRANSITION_ACTIVATE,
                    )
                ),
            ],
            handle_once=True,
        )
    )

    # configure 가 실패하면 노드는 unconfigured 로 **되돌아간다**. finalized 로
    # 가지 않는다. 2026-11-06 실측:
    #
    #   [os_driver] ERROR: Error connecting to sensor 10.5.5.87 ... 404
    #   [launch_ros] Failed to make transition 'TRANSITION_CONFIGURE'
    #   $ ros2 lifecycle get /ouster/os_driver  ->  unconfigured [1]
    #
    # 이 핸들러가 없으면 프로세스가 살아 있는 채로 아무 일도 하지 않는다. launch
    # 도 종료하지 않으므로 systemd 의 Restart=on-failure 가 영원히 걸리지 않고,
    # 죽은 노드가 그래프에 남아 Config App 에는 "살아 있음"으로 보인다.
    # 그래서 실패한 configure 를 종료 사유로 바꾼다. 재시도는 systemd 가 한다.
    #
    # start_state 를 함께 지정한다. unconfigured 는 정상적인 cleanup 으로도
    # 도달하는 상태여서 goal_state 만 보면 정상 정리까지 종료로 처리한다.
    configure_failed_event = RegisterEventHandler(
        OnStateTransition(
            target_lifecycle_node=os_driver,
            start_state="configuring",
            goal_state="unconfigured",
            entities=[
                LogInfo(msg="configure 실패. 종료한다. 재시도는 systemd 가 한다."),
                EmitEvent(event=launch.events.Shutdown(reason="configure failed")),
            ],
        )
    )

    # 센서와 통신하지 못해 드라이버가 finalized 로 가는 경로도 함께 막는다.
    finalized_event = RegisterEventHandler(
        OnStateTransition(
            target_lifecycle_node=os_driver,
            goal_state="finalized",
            entities=[
                LogInfo(msg="센서와 통신하지 못했다. 종료한다."),
                EmitEvent(event=launch.events.Shutdown(reason="Couldn't communicate with sensor")),
            ],
        )
    )

    return launch.LaunchDescription(
        [
            config_root_arg,
            os_driver,
            configure_event,
            activate_event,
            configure_failed_event,
            finalized_event,
        ]
    )
