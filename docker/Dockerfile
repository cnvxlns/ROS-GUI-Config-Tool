# 개발 호스트 전용 이미지.
#
# 이 호스트는 Ubuntu 26.04 라서 ROS 2 jazzy 바이너리가 없다(jazzy = noble/24.04).
# 그래서 드라이버와 Config App 이 둘 다 컨테이너에서 돈다. 실차 IPC/Jetson 은
# 24.04 네이티브이므로 이 이미지는 배포 산출물이 아니다.
#
# 빌드:
#   docker build -t ros2-jazzy-ouster:0.13.15 docker/
#
# 쓰는 곳: deploy/dev-host/ 의 유닛과 README.
FROM osrf/ros:jazzy-desktop

ENV DEBIAN_FRONTEND=noninteractive

# apt 의 ros-jazzy-ouster-ros(0.15.1)를 쓰지 않는다. 그 버전이 들고 있는 SDK 는
# FW 2.2 미만의 HTTP API 구현을 지우고 나가서 이 센서(FW 2.1.2)와 통신하지 못한다.
# 2026-11-06 실측:
#   Error connecting to sensor 10.5.5.87, details: CurlClient::execute_request
#   failed for url: [10.5.5.87/api/v1/sensor/metadata/sensor_info] code: [404]
# 자세한 경위는 docs/driver-version-pin.md 를 보라.
#
# systemd / dbus 는 데몬을 띄우기 위한 것이 아니다. Config App 의
# applier 가 호스트의 systemd 를 systemctl 로 재시작하려면 클라이언트가 필요하다
# (deploy/dev-host/README.md 의 D-Bus 브리지). 드라이버 컨테이너에는 쓰이지 않는다.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-venv \
        git \
        systemd \
        dbus \
    && rm -rf /var/lib/apt/lists/*

# ouster_ros 를 소스에서 빌드한다. 태그를 고정하는 이유:
#   jazzy-0.13.15 (2025-10-24) 가 레거시 FW 구현을 들고 있는 마지막 jazzy 릴리스다.
#   그 안에 포함된 ouster-sdk 의 SensorHttp::create 가 fw.minor == 1 분기로
#   SensorHttpImp_2_1 을 돌려준다. 다음 릴리스(0.14.0, 2026-02-26)가 쓰는 SDK 는
#   0.16.0 에서 그 분기를 지운 뒤라 쓸 수 없다.
# 올리기 전에 반드시 확인할 것: 센서 FW 가 2.2 이상으로 올라갔는지.
ARG OUSTER_ROS_TAG=jazzy-0.13.15
RUN set -eux; \
    mkdir -p /opt/ouster_ws/src; \
    git clone --depth 1 --branch "${OUSTER_ROS_TAG}" \
        https://github.com/ouster-lidar/ouster-ros.git /opt/ouster_ws/src/ouster-ros; \
    apt-get update; \
    rosdep update --rosdistro jazzy; \
    rosdep install -y --from-paths /opt/ouster_ws/src --ignore-src --rosdistro jazzy; \
    bash -lc 'source /opt/ros/jazzy/setup.bash \
        && cd /opt/ouster_ws \
        && colcon build --cmake-args -DCMAKE_BUILD_TYPE=Release'; \
    rm -rf /opt/ouster_ws/build /opt/ouster_ws/log /var/lib/apt/lists/*; \
    bash -lc 'source /opt/ouster_ws/install/setup.bash && ros2 pkg prefix ouster_ros'

# Config App 의 파이썬 의존성.
# apt 의 python3-fastapi 는 Pydantic v1 에 묶이므로 쓰지 않는다(명세 §7.1).
# --system-site-packages 로 rclpy 를 그대로 쓴다.
#
# 배포 환경은 venv 를 /opt/av_config/.venv 에 둔다(deploy/av-config.service).
# 여기서는 /opt/av_venv 다. 설정 루트를 /opt/av_config 로 마운트하면 그 안의
# venv 가 덮여서 가려지기 때문이다 — 실제로 한 번 당했다:
#   bash: /opt/av_config/.venv/bin/python: No such file or directory
# 배포 환경은 마운트가 아니라 실제 디렉터리라서 충돌이 없다.
RUN python3 -m venv --system-site-packages /opt/av_venv \
    && /opt/av_venv/bin/pip install --no-cache-dir \
        "fastapi>=0.115" \
        "uvicorn[standard]" \
        "pydantic>=2.9" \
        ruamel.yaml

# 컨테이너는 root 로 돌고 마운트된 설정은 호스트 uid 1000 소유다. git 은 소유자가
# 다른 저장소를 거부하므로(dubious ownership) GitParams 의 모든 호출이 실패한다.
# 배포 환경은 avconfig 사용자가 그 디렉터리를 소유해서 해당 문제가 없다.
RUN git config --system --add safe.directory /opt/av_config/params

# /root 가 700 이면 `docker run -u 1000` 으로는 그 아래 마운트에 들어갈 수 없다.
# Config App 을 uid 1000 으로 돌려야 하는 이유가 있다 — 호스트의 systemd user
# bus 는 소유자 uid 만 인증하기 때문이다(deploy/dev-host/av-systemctl-user).
# 개발 이미지라 /root 를 열어 둔다.
RUN chmod 755 /root

# 설정 루트는 tools/init_config_root.py 로 만든 호스트의 ~/av_config 를
# /opt/av_config 로 마운트한다. 리포의 config/ 는 템플릿이다. 이미지에 넣지 않는다.
WORKDIR /root/ros2_ws
