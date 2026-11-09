# 센서 기준값 — OS-2-128 / SN 992134000971

2026-11-06 에 HTTP API 로 조회한 값이다. **FW 업그레이드 전 기준값**이므로
업그레이드 후 설정이 초기화되면 여기를 보고 되돌린다.

연결: 호스트 `enp1s0` 10.5.5.1/24 — 센서 10.5.5.87 (MAC `bc:0f:a7:00:2e:81`).

## 식별 정보

`GET /api/v1/sensor/cmd/get_sensor_info`

```json
{
  "prod_line": "OS-2-128",
  "prod_sn": "992134000971",
  "prod_pn": "840-102146-D",
  "base_pn": "830-101845-07",
  "base_sn": "102109000147",
  "proto_rev": "v1.1.1",
  "build_rev": "v2.1.2",
  "build_date": "2021-07-27T22:34:53Z",
  "image_rev": "ousteros-image-prod-aries-v2.1.2+20210727223313.patch-v2.1.2",
  "status": "RUNNING"
}
```

`GET /api/v1/system/firmware`

```json
{"fw": "ousteros-image-prod-aries-v2.1.2+20210727223313.patch-v2.1.2", "commit_pending": false}
```

## 활성 설정

`GET /api/v1/sensor/cmd/get_config_param?args=active`

```json
{
  "udp_ip": "192.168.0.3",
  "udp_dest": "192.168.0.3",
  "udp_port_lidar": 7502,
  "udp_port_imu": 7503,
  "lidar_mode": "1024x10",
  "timestamp_mode": "TIME_FROM_INTERNAL_OSC",
  "operating_mode": "NORMAL",
  "multipurpose_io_mode": "OUTPUT_FROM_ENCODER_ANGLE",
  "azimuth_window": [0, 360000],
  "signal_multiplier": 1,
  "auto_start_flag": 1,
  "phase_lock_enable": false,
  "phase_lock_offset": 0,
  "sync_pulse_in_polarity": "ACTIVE_HIGH",
  "sync_pulse_out_polarity": "ACTIVE_HIGH",
  "sync_pulse_out_frequency": 1,
  "sync_pulse_out_angle": 60,
  "sync_pulse_out_pulse_width": 8,
  "nmea_in_polarity": "ACTIVE_HIGH",
  "nmea_baud_rate": "BAUD_9600",
  "nmea_ignore_valid_char": 0,
  "nmea_leap_seconds": 0
}
```

`udp_dest` 가 192.168.0.3 인 것은 이전 네트워크의 잔재다. 현재 호스트 NIC 는
10.5.5.1 이므로 데이터가 수신되지 않는다. 드라이버가 기동 시 `udp_dest`
파라미터로 센서를 reinit 하므로, `params/lidar_top.yaml` 의 값이 맞으면 해소된다.

## FW 2.1.2 의 제약

- 활성 설정에 `udp_profile_lidar` 키가 **없다**. 프로파일 개념이 없는 FW 이므로
  LEGACY 만 가능하다. `params/lidar_top.yaml` 의
  `udp_profile_lidar: RNG19_RFL8_SIG16_NIR16` 은 FW 업그레이드 후에만 유효하다.
- HTTP API 가 구형이다. `/api/v1/sensor/cmd/get_sensor_info` 는 있지만
  `/api/v1/sensor/metadata/sensor_info` 는 404 다.
- 그래서 **ouster-sdk 1.0.1 로는 연결되지 않는다.** 실측:

  ```
  $ ouster-cli source -x -y 10.5.5.87 config
  Error: CurlClient::execute_request failed for url:
    [10.5.5.87/api/v1/sensor/metadata/sensor_info] with the code: [404]
  ```

  `ros-jazzy-ouster-ros` 0.15.1 **드라이버도 같은 지점에서 실패한다.** 2026-11-06
  에 컨테이너에서 실측했다:

  ```
  [os_driver] Will send UDP data to 10.5.5.1
  [os_driver] Contacting sensor 10.5.5.87 ...
  [os_driver] ERROR: Error connecting to sensor 10.5.5.87, details:
    CurlClient::execute_request failed for url:
    [10.5.5.87/api/v1/sensor/metadata/sensor_info] with the code: [404]
  [launch_ros] Failed to make transition 'TRANSITION_CONFIGURE'
  $ ros2 lifecycle get /ouster/os_driver   ->  unconfigured [1]
  ```

  FW 업그레이드가 선행 조건이다. ouster-cli 의 `firmware` 명령도 같은 404 로
  죽으므로 업그레이드는 센서 내장 웹 대시보드(http://10.5.5.87/ 의
  "Firmware Update", `POST api/v1/system/firmware`)로 해야 한다.

## 받지 못한 것

전원이 내려가서 아래는 조회하지 못했다. **FW 업그레이드 전에 반드시 받아둔다** —
intrinsics 는 공장 캘리브레이션 값이라 되돌릴 수 없다.

```bash
for ep in sensor/cmd/get_beam_intrinsics sensor/cmd/get_imu_intrinsics \
          sensor/cmd/get_lidar_intrinsics sensor/cmd/get_lidar_data_format \
          sensor/cmd/get_calibration_status sensor/cmd/get_config_param?args=staged \
          system/network; do
  echo "== $ep"; curl -s "http://10.5.5.87/api/v1/$ep"; echo
done | tee docs/sensor-baseline-992134000971-full.txt
```
