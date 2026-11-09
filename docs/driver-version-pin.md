# ouster_ros 버전을 jazzy-0.13.15 로 고정한 이유

## 한 줄 요약

센서 펌웨어가 v2.1.2 이고, **ouster-sdk 0.16.0 이 FW 2.2 미만의 HTTP API 구현을
삭제했다.** 그 삭제 이전의 SDK 를 들고 있는 마지막 jazzy 릴리스가
`ouster-ros jazzy-0.13.15` 다.

센서를 FW 2.2 이상으로 올리면 이 고정은 필요 없어진다. 그때는 apt 의
`ros-jazzy-ouster-ros` 를 쓰는 편이 낫다 (docker/Dockerfile 의 ARG 참고).

## 근거

센서는 구형 HTTP API 만 갖고 있다 (2026-11-06 실측):

| 경로 | 10.5.5.87 (FW 2.1.2) |
|---|---|
| `/api/v1/sensor/cmd/get_sensor_info` | 200 |
| `/api/v1/sensor/metadata/sensor_info` | **404** |

SDK 의 `SensorHttp::create` 가 펌웨어 버전으로 구현을 고른다. `jazzy-0.13.15` 가
포함한 SDK (`ouster-ros/ouster-sdk/ouster_client/src/sensor_http.cpp`):

```cpp
if (fw.major == 2) {
    switch (fw.minor) {
        case 0: return std::make_unique<SensorTcpImp>(hostname);
        case 1: return std::make_unique<SensorHttpImp_2_1>(hostname, timeout_sec);
        case 2: return std::make_unique<SensorHttpImp_2_2>(hostname, timeout_sec);
    }
}
return std::make_unique<SensorHttpImp>(hostname, timeout_sec);
```

우리 센서는 `fw.minor == 1` 이라 `SensorHttpImp_2_1` 로 간다. 버전 판별에 쓰는
`api/v1/system/firmware` 는 이 펌웨어에도 있다.

`SensorHttpImp_2_1` / `_2_2` 는 ouster-sdk 커밋 `3696c8b` ("Release 0.16.0",
2025-12-23) 에서 삭제됐다. 그래서:

| ouster-sdk | 레거시 FW 구현 |
|---|---|
| ~ v0.15.0 | 있음 |
| v0.16.0 이후 (1.0.x 포함) | 없음 |

| ouster-ros | 릴리스 | 쓸 수 있나 |
|---|---|---|
| `jazzy-0.13.15` | 2025-10-24 | **쓸 수 있다 (고정 대상)** |
| `jazzy-0.14.1/0.14.2` | 2026-03 | 못 쓴다 |
| `jazzy-0.15.1` (apt 바이너리) | 2026-08-22 | 못 쓴다 — 404 로 실패 확인 |

0.13.15 는 SDK 를 서브모듈이 아니라 소스에 직접 포함한다(`ouster-ros/ouster-sdk`
가 tree 객체다). 그래서 태그만 clone 하면 호환되는 SDK 가 같이 따라온다.

## 대가: 관리 가능한 파라미터가 줄어든다

0.13.15 는 ROS 파라미터를 **33개** 선언한다. 0.15.1 은 56개다. 0.13.15 에만 있는
파라미터는 없다 — 완전한 부분집합이다.

`config/overlays/ouster_ros.yaml` 의 `device_params` 27개 중 0.13.15 에 있는 것은
**8개** 다.

| | 파라미터 |
|---|---|
| 있음 (8) | `udp_dest`, `lidar_port`, `imu_port`, `lidar_mode`, `timestamp_mode`, `udp_profile_lidar`, `azimuth_window_start`, `azimuth_window_end` |
| 없음 (19) | `operating_mode`, `signal_multiplier`, `multipurpose_io_mode`, `phase_lock_enable`, `phase_lock_offset`, `return_order`, `columns_per_packet`, `udp_profile_imu`, `gyro_fsr`, `accel_fsr`, `nmea_*` (4), `sync_pulse_*` (5) |

없는 19개는 드라이버가 ROS 파라미터로 노출하지 않을 뿐, 센서에는 존재한다
(`gyro_fsr` / `accel_fsr` 은 FW 3.1+ 라 이 센서에도 없다). 센서의 HTTP API 로는
설정할 수 있지만 Config App 의 관리 대상에서는 빠진다.

`config/params/lidar_top.yaml` 이 쓰는 10개(`sensor_hostname`, `udp_dest`,
`lidar_mode`, `timestamp_mode`, `udp_profile_lidar`, `point_type`, `proc_mask`,
`lidar_port`, `imu_port`, `sensor_frame`)는 **전부 0.13.15 에 있다.**

## 그대로 쓸 수 있는 것

- 노드 이름이 같다: `executable=os_driver`, `namespace=ouster`, `name=os_driver`
  → `/ouster/os_driver`. `services.yaml` 과 스키마의 정규식을 고치지 않아도 된다.
- 서비스가 다 있다: `get_metadata`, `get_config`, `set_config`, `reset`.
  스키마의 `required_services` 게이트가 통과한다.
- parameter file 최상위 키도 같다 (`ouster/os_driver:`).
- `av_bringup/launch/lidar_top.launch.py` 를 수정할 필요가 없다.

## 센서 FW 를 올리게 되면

1. `docker/Dockerfile` 의 `OUSTER_ROS_TAG` 고정을 지우고 apt 패키지로 돌아간다.
2. `tools/import_driver_params.py` 로 스키마를 재생성한다.
3. `config/overlays/ouster_ros.yaml` 의 `device_params` 를 27개로 되돌린다.
