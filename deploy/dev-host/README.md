# 개발 호스트 배선 (Ubuntu 26.04)

이 디렉터리는 **이 개발 호스트 전용**이다. 배포 산출물은 한 단계 위의
`deploy/av-config.service`, `deploy/av-lidar-top.service` 다.

## 왜 따로 필요한가

호스트가 Ubuntu 26.04 다. ROS 2 jazzy 는 noble(24.04) 바이너리만 있으므로
드라이버도 Config App 도 컨테이너에서 돈다. 그런데 Config App 의 `applier` 는
`systemctl restart <unit>` 을 호출한다 — 컨테이너에는 systemd 가 없다.

실차 IPC/Jetson 은 24.04 네이티브라 이 문제가 없다. 아래 배선은 개발 호스트에서만
필요한 우회다.

```
  호스트 systemd
    ├── av-lidar-top.service ──> docker run av-lidar-top
    │                              └── ros2 launch av_bringup lidar_top.launch.py
    │                                    └── /ouster/os_driver  (LifecycleNode)
    └── (Config App 은 수동 기동)  docker run av-config
                                   └── av_config.main  ──systemctl restart──┐
                                         ^                                   │
                                         └── 호스트 D-Bus 소켓 마운트 ───────┘
```

## 1. 이미지 빌드

```bash
cd ~/ros2_ws
docker build -t ros2-jazzy-ouster:0.13.15 docker/
```

이미지가 `ouster_ros` 를 **소스에서** 빌드한다 (태그 `jazzy-0.13.15`,
`/opt/ouster_ws` 에 설치). apt 바이너리는 센서 FW 2.1.2 와 통신하지 못한다 —
근거는 `docs/driver-version-pin.md`. C++ 빌드라 10분 정도 걸린다.

그래서 드라이버를 쓰는 명령은 `/opt/ouster_ws/install/setup.bash` 를 함께
source 해야 한다.

## 2. 워크스페이스 빌드

```bash
docker run --rm -v ~/ros2_ws:/root/ros2_ws -w /root/ros2_ws \
  ros2-jazzy-ouster:0.13.15 \
  bash -lc 'source /opt/ros/jazzy/setup.bash \
            && colcon build --symlink-install \
            && chown -R 1000:1000 build install log'
```

root 로 빌드하고 마지막에 소유권을 되돌린다. `-u $(id -u):$(id -g)` 로 바꾸면
안 된다 — 이미지의 `/root` 가 700 이어서 uid 1000 이 `/root/ros2_ws` 로 들어갈
수 없다 (`PermissionError: ... /root/ros2_ws/log/.../logger_all.log`).

## 3. 설정 루트의 git 저장소

이력과 롤백이 여기에 의존한다. 아직 커밋이 없으면 Config App 이 경고를 띄운다.

```bash
cp -a ~/ros2_ws/config ~/av_config          # 리포의 원본을 런타임 루트로 복사
cd ~/av_config/params
git init -q && git config user.name avconfig && git config user.email avconfig@localhost
git add --all && git commit -q -m "initial: 기준 설정"
```

**런타임 루트는 리포 밖(`~/av_config`)이다.** 리포의 `config/` 는 배포될 원본이고,
실행 중인 시스템은 사본에서 이력을 쌓는다 — 배포 환경의 `/opt/av_config` 와 같은
위치다. 둘을 같은 디렉터리로 두면 `config/params` 가 리포 안의 중첩 git 저장소가
되고, 상위 리포가 그것을 깨진 하위 모듈 포인터로 취급해 **parameter file 이
커밋에서 빠진다.**

UI 에서 바꾼 값을 리포의 원본에도 반영하려면 파일을 복사해 커밋한다:

```bash
cp ~/av_config/params/*.yaml ~/ros2_ws/config/params/
```

`~/ros2_ws` 자체가 git 저장소지만 `config/params` 는 **별도 저장소**여야 한다.
배포 시 이 디렉터리만 `/opt/av_config/params` 로 가고, GitParams 가 그 경로를
저장소 루트로 본다.

개발 리포에서 이걸 하면 `config/params/.git` 이 생긴다. 테스트와
`tools/mock_backend.py` 는 `config/` 를 임시 디렉터리로 복사해 쓰므로, 복사할 때
`.git` 을 제외한다 — 안 하면 복사본에 이미 초기 commit 이 있어서 픽스처의
initial commit 이 "변경 없음"으로 실패한다.

## 4. 드라이버 유닛 설치

```bash
sudo cp deploy/dev-host/av-lidar-top.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start av-lidar-top
systemctl status av-lidar-top
journalctl -u av-lidar-top -f
```

센서가 꺼져 있으면 드라이버가 finalized 로 가고 launch 가 종료한다. 유닛의
`Restart=on-failure` 가 5초마다 다시 시도한다. 즉 **센서 전원을 올리면 알아서
붙는다.** 켜 두고 싶지 않으면 `systemctl stop av-lidar-top`.

## 5. Config App 기동

`AV_CONFIG_SYSTEMCTL_PREFIX` 에 shim 을 끼운다. applier 는 항상
`<PREFIX> systemctl restart <unit>` 형태로 부르고 PREFIX 는 systemctl **앞에**
붙으므로, `--user` 를 넣을 자리가 없다. shim 이 그 인수를 다시 쓴다
(`deploy/dev-host/av-systemctl-user`).

```bash
docker run -d --name av-config \
  --net=host --ipc=host --pid=host \
  -u 1000:1000 -e HOME=/tmp \
  -e ROS_DOMAIN_ID=0 \
  -e FASTDDS_BUILTIN_TRANSPORTS=UDPv4 \
  -e AV_CONFIG_ROOT=/opt/av_config \
  -e AV_CONFIG_HOST=127.0.0.1 \
  -e AV_CONFIG_PORT=8080 \
  -e AV_CONFIG_SYSTEMCTL_PREFIX=/root/ros2_ws/deploy/dev-host/av-systemctl-user \
  -e XDG_RUNTIME_DIR=/run/user/1000 \
  -e DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
  -v ~/ros2_ws:/root/ros2_ws \
  -v ~/av_config:/opt/av_config \
  -v /run/user/1000/bus:/run/user/1000/bus \
  -v /run/user/1000/systemd:/run/user/1000/systemd \
  ros2-jazzy-ouster:0.13.15 \
  bash -lc 'source /opt/ros/jazzy/setup.bash && source /opt/ouster_ws/install/setup.bash && source /root/ros2_ws/install/setup.bash && exec /opt/av_venv/bin/python -m av_config.main'
```

브라우저에서 http://127.0.0.1:8080/ 를 연다.

플래그가 많은데 하나씩 이유가 있다. 빼면 조용히 깨진다.

| 플래그 | 없으면 |
|---|---|
| `--net=host` | DDS 로 드라이버를 못 보고, 점검 규칙 R2 가 컨테이너 주소를 보고 `udp_dest` 를 거부한다 |
| `--pid=host` | 호스트 systemd 가 호출자를 못 찾아 `systemctl` 이 **Access denied** 로 거부된다 (PID 네임스페이스가 달라 SO_PEERCRED 의 pid 를 해석하지 못한다) |
| `-u 1000:1000` | 호스트의 systemd **user** bus 는 소유자 uid 만 인증한다. root 로는 못 붙는다 |
| `-e HOME=/tmp` | uid 1000 이 쓸 수 있는 HOME 이 없어 rclpy 로그 생성이 실패한다 |
| `-e FASTDDS_BUILTIN_TRANSPORTS=UDPv4` | `/dev/shm` 의 죽은 Fast DDS 세그먼트·포트 뮤텍스 때문에 discovery 가 깨진다 (아래 참고) |
| `/run/user/1000/bus`, `/run/user/1000/systemd` | 컨테이너에서 호스트 user systemd 에 닿을 수 없다 |

### 중지할 때 — PID 1 에 신호를 보내지 마라

`--pid=host` 때문에 **컨테이너 안의 PID 1 은 호스트의 systemd** 다.
`docker exec av-config kill -TERM 1` 은 호스트 init 에 신호를 보낸다. 대신
프로세스를 지목한다:

```bash
kill -TERM "$(pgrep -f av_config.main | head -1)"
```

### /dev/shm 와 Fast DDS

컨테이너를 SIGKILL 로 끝내면 Fast DDS 의 공유메모리 세그먼트와 포트 뮤텍스가
`/dev/shm` 에 남는다. 쌓이면 **discovery 자체가 깨진다** — 드라이버는 정상인데
`ros2 node list` 가 빈 목록을 돌려준다(2026-11-09 실측, 40개가 쌓였고 32개가
root 소유라 지울 수도 없었다). 그래서 이 호스트는 UDP 전송만 쓰고, 유닛은
SIGINT 로 정상 종료시킨다.

## 검증 상태

2026-11-06 에 이 호스트에서 확인한 것:

| 항목 | 결과 |
|---|---|
| `colcon build` (`av_config` + `av_bringup`) | 통과 |
| launch 가 두 parameter file 을 순서대로 적용 | 통과. `sensor_frame` = `lidar_top_link` (global.yaml 이 덮었다), `use_sim_time` = false (`/**` 에서 왔다) |
| parameter file 의 선행 슬래시 노드 키 (`/ouster/os_driver:`) | 통과. `sensor_hostname`, `udp_dest` 가 그대로 들어갔다 |
| 노드 이름 | `/ouster/os_driver` 로 떴다. services.yaml / 스키마와 일치 |
| 드라이버 ↔ 센서 FW 2.1.2 | **실패.** 404 (docs/sensor-baseline-*.md 참고) |
| Config App 기동 (rclpy executor + uvicorn 공존) | 통과 |
| `discovery` 의 실제 파라미터 서비스 호출 | 통과. `/ouster/os_driver` 에서 64개 수집 |
| drift 탐지 | 통과. `lidar_port` 0 vs 7502, `imu_port` 0 vs 7503 을 잡았다 |
| 검증 + 계획 (dry_run) | 통과. restart 대상 `av-lidar-top.service`, 기록 대상 `params/lidar_top.yaml` |
| git 이력 조회 | 통과 (`safe.directory` 설정 후) |
| 스키마 매칭 | **안 붙는다.** 아래 참고 |

### 센서가 없으면 lidar 는 raw 모드로만 보인다

스키마의 `match.required_services` 가 `/ouster/get_config` 와
`/ouster/get_metadata` 를 요구한다. 이 두 서비스는 드라이버의 `on_configure`
에서 만들어지므로, 센서에 연결하지 못하면 **존재하지 않는다.** 실측:

```
$ ros2 service list | grep ^/ouster
/ouster/os_driver/change_state
/ouster/os_driver/describe_parameters
...                      # lifecycle 과 파라미터 서비스만. get_config 없음
```

그래서 노드가 `display_mode: "raw"`, `schema_driver: null` 로 뜬다. 파라미터
64개는 다 보이고 drift 탐지와 검증·계획도 동작하지만, 설명·enum·계층(`layer`)·
등급(`tier`)이 붙지 않고 device 계층 되읽기도 불가능하다. 설계대로 동작하는
것이다(명세 §4.4) — 버그가 아니다.

아직 확인하지 못한 것:

- 컨테이너 안 `systemctl` 이 호스트 유닛을 재시작하는지 (위 D-Bus 브리지)
- 센서 FW 업그레이드 후 드라이버가 실제로 연결되는지
- 실패한 configure 를 종료로 바꾸는 핸들러가 동작하는지 (추가만 하고 아직
  실행해보지 못했다)

### 알아둘 것

- **컨테이너 정지에 sudo 가 필요하다.** 이 호스트의 snap docker 는
  `docker run` 은 허용하지만 `docker stop|kill|rm` 은
  `permission denied` 로 거부한다. `sudo docker rm -f <name>` 을 쓴다.
- `--symlink-install` 의 심볼릭 링크가 `/root/ros2_ws/build/...` 를 가리킨다.
  호스트에서 보면 끊어진 링크다. 항상 같은 마운트 지점(`/root/ros2_ws`)으로
  컨테이너에서 실행해야 한다.
- `-u $(id -u):$(id -g)` 로는 빌드할 수 없다. 이미지의 `/root` 가 700 이라
  그 아래 마운트를 uid 1000 이 들어갈 수 없다. root 로 빌드하고 산출물을
  `chown -R 1000:1000 build install log` 로 되돌린다 (위 2번 명령에 포함).
