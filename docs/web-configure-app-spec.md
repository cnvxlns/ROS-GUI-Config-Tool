# Web Configure App 설계 명세

**v1.1 · 2026년 10월 30일 · @한유**

> 초판(v1.0) 대비 변경 사항은 [14장 개정 이력](#14-개정-이력)에 정리했다.
>
> 구현된 동작을 그림으로 보려면 [architecture.md](architecture.md) 를 참고한다.

## 1. 개요

Web Configure App은 자율주행 IPC(또는 Jetson)에 연결된 모든 센서와 ROS 2 노드의 설정을 하나의 웹 UI에서 조회, 변경, 적용하는 도구다. 설정의 단일 진실 원천(single source of truth)은 각 노드의 ROS 2 parameter file(YAML)이며, 실제 장치 반영은 각 센서의 ROS 2 드라이버 노드가 담당한다.

### 1.1 목적

- 센서마다 다른 설정 방식(벤더 웹 UI, SDK, CLI)을 하나의 화면으로 통일한다.
- 설정 변경 이력을 남기고, 실패 시 이전 상태로 되돌릴 수 있게 한다.
- 신규 센서나 노드를 추가할 때 UI 코드를 거의 고치지 않도록 파라미터를 자동 수집한다.

### 1.2 범위

| 구분 | 포함 | 제외 |
|---|---|---|
| 센서/드라이버 설정 | 드라이버 노드 파라미터 조회·변경·적용 | 센서 벤더 웹 UI(:80 등)를 통한 직접 설정 |
| 시스템 설정 | 점검 항목(UDP 수신, NIC, 방화벽 상태) 표시 | OS 설정 자동 변경 (1차 범위 외, TBD) |
| 운영 | 프로파일, 이력, 롤백, 상태 모니터링 | 실시간 데이터 시각화(점구름, 영상) |
| 장치 관리 | 연결 상태, 진단 표시 | 펌웨어 업데이트 |

### 1.3 핵심 원칙

1. **YAML이 주인이다.** 모든 변경은 parameter file에 먼저 저장되고, 런타임 상태는 그 결과일 뿐이다.
2. **장치에는 드라이버만 쓴다.** Config App은 센서 장치 API를 직접 호출하지 않는다. 드라이버와의 불일치(last writer wins 문제)를 막기 위함이다.
3. **적용했다가 아니라 반영됐다를 확인한다.** 적용 후 반드시 read-back과 데이터 흐름으로 검증한다. 이때 `layer: device` 파라미터는 **노드 파라미터가 아니라 장치에서** 읽어야 한다 (4.4절 `readback_via`).
4. **주행 중에는 바꾸지 않는다.** 안전 게이트를 통과하지 못하면 적용을 거부한다.

### 1.4 용어

| 용어 | 의미 |
|---|---|
| 드라이버 노드 | 호스트에서 실행되며 센서 장치와 ROS 2 사이를 변환하는 노드 (예: ouster-ros `os_driver`) |
| parameter file | 노드에 `--params-file`로 전달되는 YAML |
| Config App 노드 | 본 앱의 백엔드. 웹 서버와 ROS 2 클라이언트를 겸하는 ROS 2 노드 |
| 파라미터 스키마 | 파라미터별 계층, 적용 방식, 노출 등급, read-back 경로, 의존성을 정의한 메타데이터 |
| 적용 방식 | `runtime` / `reconfigure` / `restart` 중 하나 |
| 프로파일 | 여러 노드의 파라미터 묶음. 한 번에 전환 가능한 설정 세트 |
| read-back | 적용 후 노드나 **장치**에서 값을 다시 읽어 실제 반영 여부를 확인하는 것 |
| 저장값 | 해당 노드의 `param_files`를 등록 순서대로 머지한 결과값 (5.1절) |
| drift | 저장값과 런타임값의 불일치 |
| unit | Config App이 재시작 단위로 다루는 systemd 서비스. 노드 여러 개를 포함할 수 있다 |

## 2. 시스템 컨텍스트와 전제

본 명세는 아래 전제를 기준으로 작성했다. TBD 항목은 13장 미결 사항에서 확정한다.

| 항목 | 전제 | 상태 |
|---|---|---|
| 호스트 | NVIDIA Jetson 계열 또는 x86 IPC, ARM64/x86_64 모두 고려 | TBD: 모델 확정 |
| OS | Ubuntu (JetPack 기반 포함) | TBD: 버전 |
| ROS 2 배포판 | Jazzy. 공식 바이너리는 Ubuntu 24.04 대상이므로, Jetson이면 JetPack의 Ubuntu 버전에 따라 소스 빌드나 컨테이너가 필요할 수 있음 | 확정 |
| 센서 | 라이다(Ouster 등), 카메라, GNSS/IMU 등 다수. 각각 ROS 2 드라이버 노드로 구동 | TBD: 센서 목록 |
| 프로세스 관리 | 기존 launch가 없으므로 처음부터 서브시스템별로 분리하고 각각 systemd 서비스로 등록 | 확정 |
| 접속 단말 | 차량 네트워크에 연결한 노트북/태블릿 브라우저 | 확정 (차량 내 디스플레이 미사용) |
| 동시 사용자 | 1~2명 (엔지니어, 운영자) | TBD |
| 차량 상태 신호 | 주행 모드와 속도를 알 수 있는 ROS 2 토픽 존재 | TBD: 토픽명 |

### 2.1 센서 연결 관점의 전제

- 드라이버 노드는 호스트에서 실행되며, 센서 장치 설정은 드라이버가 벤더 인터페이스(예: Ouster HTTP API :80, RealSense librealsense SDK)로 수행한다.
- 측정 데이터 경로(예: Ouster UDP :7502/:7503)는 설정 경로와 분리되어 있으며, Config App은 이 경로를 직접 다루지 않고 수신 상태만 점검한다.
- 드라이버가 없는 장치는 팀 내부 드라이버 작성 규칙(12장)에 따라 래퍼 노드를 새로 만든다.

## 3. 아키텍처

Config App은 호스트에서 실행되는 ROS 2 노드 하나로, 웹 프론트엔드 서빙과 백엔드 API, ROS 2 클라이언트 역할을 함께 맡는다. 브라우저는 HTTP로 Config App에만 접속하고, Config App은 DDS(ROS 2 서비스)와 systemd로 드라이버 노드를 제어한다.

설정은 위에서 아래로만 흐른다. Config App은 YAML을 쓰고 드라이버 노드에 적용을 요청하며, 센서 장치에 쓰는 것은 드라이버 노드뿐이다.

### 3.1 구성 요소

| 구성 요소 | 위치 | 역할 |
|---|---|---|
| 브라우저 (프론트엔드) | 접속 단말 | 설정 화면 표시, 사용자 입력, 결과 표시 |
| Config App 노드 | 호스트 | 정적 파일 서빙, REST/WebSocket API, 검증, YAML 저장, 적용, 검증, 롤백 |
| 파라미터 스키마 저장소 | 호스트 파일 | 드라이버별 매핑, 적용 방식, 노출 등급, read-back 경로, 의존성 |
| Parameter file 저장소 | 호스트 파일 (git 관리) | 노드별 YAML. 단일 진실 원천 |
| systemd 서비스 | 호스트 | 서브시스템별 launch 실행과 재시작 |
| 드라이버 노드 | 호스트 | YAML 로드, 장치 설정, 데이터 수신·발행 |
| 센서 장치 | 차량 | 자체 펌웨어와 설정 인터페이스, 측정 데이터 송신 |

### 3.2 설정 소유권 모델

| 대상 | 소유자 | 쓰는 주체 | 쓰는 시점 |
|---|---|---|---|
| Parameter file (YAML) | Config App | Config App만 | 사용자가 적용할 때 |
| 노드 런타임 파라미터 | 드라이버 노드 | 드라이버 노드 (Config App이 서비스로 요청) | 노드 초기화, `set_parameters`, 전용 서비스 |
| 센서 장치 설정 | 드라이버 노드 | 드라이버 노드만 | 노드 초기화, reconfigure |
| launch 파일 | 개발자 | 개발자 (Config App은 수정 안 함) | 배포 시 |

센서 벤더 웹 UI로 장치 설정을 바꾸는 것은 운영 절차상 금지한다. 드라이버가 다음 초기화 때 YAML 값으로 덮어쓰고, 그 사이에는 드라이버의 metadata와 장치 설정이 어긋날 수 있기 때문이다.

launch 파일은 Config App이 수정하지 않으므로, **launch가 파라미터를 인라인으로 주입하면 Config App이 영원히 되돌릴 수 없는 불일치가 생긴다.** 이를 막기 위해 12.1절에 launch 작성 규칙을 둔다.

## 4. 파라미터 모델

모든 파라미터는 계층, 적용 방식, 노출 등급, 의존성 네 가지 속성을 가진다. 이 속성은 파라미터 스키마 파일에 정의하며, UI와 백엔드가 같은 스키마를 기준으로 동작한다.

### 4.1 계층

| 계층 | 예시 | 저장 위치 | 반영 경로 |
|---|---|---|---|
| 장치 설정 (`device`) | Ouster `lidar_mode`, `udp_dest`, 카메라 노출 | 센서 펌웨어 (드라이버가 씀) | YAML → 드라이버 → 장치 |
| 드라이버 설정 (`driver`) | `sensor_frame`, `point_type`, QoS | 드라이버 노드 메모리 | YAML → 드라이버 |
| 시스템 전역 설정 (`global`) | frame 이름 규칙, extrinsic, 시간 동기화 모드 | 공통 YAML | 여러 노드에 동시 반영 |
| 호스트 설정 (`host`) | NIC IP, 방화벽, `net.core.rmem_max` | OS | 1차 범위에서는 점검만 |

### 4.2 적용 방식

| 적용 방식 | 의미 | 수행 방법 | 예시 |
|---|---|---|---|
| `runtime` | 실행 중 즉시 반영 | `/<node>/set_parameters` | 필터 threshold |
| `reconfigure` | 노드는 유지, 장치 재설정 | 드라이버 전용 서비스 또는 lifecycle 전이 | Ouster `lidar_mode` via `/ouster/set_config` |
| `restart` | 노드 프로세스 재시작 필요 | `systemctl restart <unit>` | 토픽 이름, 장치 경로, QoS |

`reconfigure`는 **드라이버가 해당 서비스를 제공할 때만** 성립한다. 제공하지 않는 드라이버의 파라미터는 `restart`로 분류한다. 신규 센서 온보딩 시 12.2절 3단계에서 `ros2 service list`로 실제 확인한다.

#### 설정 파일형 서비스는 현재 모델로 다룰 수 없다

`apply_via`는 **값 하나를 요청 필드에 담아 보내는** 서비스를 전제한다. 그런데 드라이버에 따라 설정 **파일 경로**를 받아 장치 설정 전체를 교체하는 형태를 쓴다.

ouster-ros 의 `set_config` 가 그렇다. `string config_file` 을 받아 파일을 읽고, 센서 설정 전체로 파싱한 뒤 `reset_sensor(true)` 로 재초기화한다. 파라미터 하나만 바꾸려면 Config App 이 **나머지 device 계층 값을 모두 포함한 센서 설정 파일을 만들어** 경로를 넘겨야 한다.

이를 지원하려면 적용 방식에 `config_file` 모드를 추가해야 한다. 그때까지 해당 파라미터는 `restart`로 분류한다. 센서 설정 자체는 노드가 다시 뜰 때 YAML 에서 반영되므로 결과는 같고, 서브시스템 중단 시간만 길어진다.

`restart`의 실행 단위는 노드가 아니라 **unit**이다. 11.2절의 unit 하나가 여러 노드를 띄우므로, 한 노드의 파라미터 때문에 같은 unit의 모든 노드가 함께 내려간다. 6.1절 계획 단계는 이 단위로 동작한다.

### 4.3 노출 등급

| 등급 | 대상 | 정책 |
|---|---|---|
| `operator` | 프로파일 선택, 일부 threshold | 로그인한 운영자 변경 가능 |
| `engineer` | 장치 모드, extrinsic, 네트워크 관련 | engineer 권한, 주행 중 잠금 |
| `hidden` | 드라이버 내부 튜닝, 변경 금지 값 | UI 미노출 (스키마에는 존재) |
| `read_only` | 드라이버가 `read_only`로 선언한 값 | 표시만 |

### 4.4 파라미터 스키마 포맷

드라이버 종류별로 스키마 파일 하나를 둔다. 자동 수집한 정보(이름, 타입, 범위, 설명)는 스키마에 없어도 표시되고, 스키마는 자동으로 얻을 수 없는 의미 정보만 덧붙인다.

```yaml
# schemas/ouster_ros.yaml
driver: ouster_ros
match:
  node_name_regex: "^/ouster/os_(driver|sensor)$"
  required_services: ["/ouster/get_metadata"]
params:
  lidar_mode:
    label: "해상도 / 회전 속도"
    layer: device
    apply: reconfigure
    apply_via:    { service: "/ouster/set_config" }
    readback_via: { service: "/ouster/get_metadata", json_path: "$.config.lidar_mode" }
    tier: engineer
    enum: ["512x10", "512x20", "1024x10", "1024x20", "2048x10"]
    affects_extra: ["/perception/lidar_segmentation"]
    lock_while_driving: true
  udp_dest:
    label: "데이터 수신 IP"
    layer: device
    apply: restart
    readback_via: { service: "/ouster/get_metadata", json_path: "$.config.udp_dest" }
    tier: engineer
    checks: ["must_match_host_nic_ip"]
  sensor_frame:
    layer: driver
    apply: restart
    tier: engineer
    global_ref: frames.lidar_top
  point_type:
    layer: driver
    apply: restart
    tier: hidden
```

필드 정의:

| 필드 | 필수 | 의미 |
|---|---|---|
| `description` | — | 파라미터 설명. 드라이버가 `ParameterDescriptor`를 채우지 않을 때 **유일한 설명 출처**가 된다. 주석이 아니라 필드여야 UI 가 읽는다 |
| `layer` | O | 4.1절 계층 |
| `apply` | O | 4.2절 적용 방식 |
| `tier` | O | 4.3절 노출 등급 |
| `apply_via` | `apply: reconfigure`일 때 | 장치 재설정에 쓸 드라이버 서비스. `request_field`로 값을 넣을 요청 필드를 지정한다 |
| `readback_via` | **`layer: device`일 때** | 장치에서 실제 값을 읽는 경로. `response_field`와 점 표기 `json_path`로 응답에서 값을 꺼낸다 |
| `enum` / `checks` | — | 허용 값 목록, 추가 검증 규칙 이름 |
| `global_ref` | — | 시스템 전역 설정의 키 |
| `affects_extra` | — | 그래프로 도출되지 않는 다운스트림의 수동 보충 |
| `lock_while_driving` | — | 기본값 `true`. 안전한 항목만 명시적으로 `false` |

#### `readback_via`가 필요한 이유

`layer: device` 파라미터를 `get_parameters`로 읽으면 **드라이버가 장치에서 write-back하지 않는 한 요청값이 그대로 돌아온다.** 센서가 값을 거부했거나 보정했어도 노드 파라미터는 요청값으로 남는다. 즉 노드 파라미터 read-back만으로는 6.2절이 상정한 "장치가 값을 보정했거나 거부" 상황을 감지할 수 없고, 핵심 원칙 3이 device 계층에서 무력화된다.

따라서 `layer: device`인 파라미터는 `readback_via`를 **필수**로 하고, 누락 시 스키마 로드 단계에서 거부한다. `layer: driver`는 `get_parameters`로 충분하다.

#### 그 외 주의

- `enum` 값은 예시이며, 실제 허용 값은 센서 모델과 펌웨어에 따라 다르므로 드라이버나 장치 명세에서 확인한다.
- `global_ref`는 시스템 전역 설정의 키를 가리키며, 전역 값이 바뀌면 참조하는 모든 노드에 함께 반영한다. 반영 비용은 6.1절 전역 변경 처리를 따른다.
- `affects_extra`는 이름 그대로 **보충**이다. 다운스트림 영향은 기본적으로 ROS 그래프에서 계산한다(7.2절 `discovery`). 1.1절의 "신규 센서 추가 시 UI 코드를 거의 고치지 않는다"는 목표에 맞추기 위해, 손으로 유지해야 하는 목록은 그래프에 드러나지 않는 관계(파일 경유, 상시 꺼져 있는 노드)로 한정한다.
- `/ouster/set_config`의 존재 여부는 ouster-ros 버전에 따라 다르다. 13.2절 미결 사항으로 두고 실제 드라이버에서 확인한다. 부재 시 `lidar_mode`는 `apply: restart`로 내려간다.

스키마 파일 자체는 로드 시 메타스키마로 검증한다(7.2절 `schema`). 잘못 작성한 스키마가 적용 시점에 터지는 것을 막기 위함이다.

## 5. 파라미터 수집과 드라이버 매핑

Config App은 ROS 2 노드라면 모두 갖고 있는 표준 파라미터 서비스로 파라미터를 자동 수집하고, 드라이버별 스키마로 의미 정보를 덧붙인다. 그래서 처음 보는 드라이버도 원본 그대로 표시할 수 있고, 아는 드라이버는 정리된 화면으로 보여준다.

### 5.1 자동 수집 절차

1. DDS discovery로 실행 중인 노드 목록을 얻는다 (`get_node_names_and_namespaces`).
2. 각 노드의 `/<node>/list_parameters`를 호출해 이름 목록을 얻는다.
3. `/<node>/describe_parameters`로 타입, 범위, 설명, `read_only`를 얻는다.
4. `/<node>/get_parameters`로 현재 런타임 값을 얻는다.
5. 해당 노드의 **저장값**을 계산해 런타임 값과 비교한다. 다르면 drift로 표시한다.
6. 노드 이름, 제공 서비스, `/diagnostics`의 `hardware_id`로 스키마를 매칭한다.
7. `get_subscriptions_info_by_topic()`으로 각 노드 발행 토픽의 구독자를 조회해 다운스트림 영향 목록을 만든다. 여기에 스키마의 `affects_extra`를 합친다.

수집은 Config App 시작 시와 노드 목록이 바뀔 때 수행하고, 사용자가 화면에서 새로고침할 수 있다.

#### 저장값의 정의와 파라미터 해석 순서

런타임 값은 다음 순서로 결정되며, 뒤가 앞을 덮는다.

1. 노드 코드의 `declare_parameter` 기본값
2. `--params-file`로 전달된 YAML — **나열 순서대로, 뒤가 승리**
3. launch의 `parameters=[...]` 인라인 항목 — 12.1절 규칙으로 **금지**
4. CLI `-p name:=value` — 최우선

따라서 Config App이 말하는 **저장값은 `services.yaml`의 `param_files`를 등록 순서대로 머지한 결과**다. 단일 파일의 값이 아니다. drift 판정과 UI의 "저장값" 표시는 모두 이 머지 결과를 기준으로 한다.

11.2절 예시에서 `global.yaml`이 뒤에 오므로 **전역 값이 노드별 값을 덮는다.** 이는 의도된 순서다. frame 이름이나 extrinsic은 시스템 전체가 같은 값을 써야 하므로 노드별 재정의를 허용하지 않는다. 노드별 재정의가 필요한 파라미터는 애초에 `global.yaml`에 두지 않는다.

### 5.2 표시 모드

| 모드 | 조건 | 표시 내용 |
|---|---|---|
| 매핑 모드 | 스키마가 매칭됨 | label, 그룹, 등급, 적용 방식 안내가 붙은 정리된 폼 |
| 원본 모드 | 스키마 없음 | 수집된 이름, 타입, 범위, 설명 그대로. `engineer` 등급으로 간주, 적용 방식은 `restart`로 간주 |

### 5.3 수집의 한계와 대응

| 한계 | 대응 |
|---|---|
| 노드가 꺼져 있으면 질의 불가 | parameter file과 스키마만으로 오프라인 편집 지원, 적용은 재시작으로 |
| `declare_parameter` 하지 않은 값은 안 보임 | 해당 드라이버를 수정하거나 스키마에 수동 정의 |
| 장치 모델에 따라 파라미터가 동적으로 선언됨 (예: RealSense) | 실제 장치 연결 상태에서 수집한 결과를 스키마 작성 기준으로 사용 |
| 드라이버가 장치 기능 일부만 노출 | 필요 시 드라이버 수정. Config App이 장치 API를 직접 부르는 예외는 두지 않음 (원칙 2) |
| `start_parameter_services=false`인 노드 | 수집 불가 노드로 표시, 스키마로만 관리 |
| **launch 인라인 파라미터로 인한 유령 drift** | Config App은 launch를 수정하지 않으므로(3.2절) 인라인 오버라이드가 있으면 지울 수 없는 drift가 상시 발생한다. 12.1절 규칙으로 금지하고, 위반 노드는 drift 경고 대신 "launch 규칙 위반" 배지로 구분해 표시한다 |

## 6. 설정 적용 흐름

적용 버튼 하나는 검증 → 저장 → 적용 → 반영 확인 → (실패 시) 롤백을 하나의 트랜잭션처럼 수행한다. 사용자는 변경 사항을 모아서 한 번에 적용하며, 적용 단위를 change set이라 부른다.

검증에서 실패하면 아무것도 바뀌지 않고, 적용 후 반영 확인에서 실패하면 직전 상태로 롤백한다.

### 6.1 단계

1. **요청**: 프론트엔드가 change set을 `POST /api/v1/changesets`로 보낸다.
2. **검증**: 타입, 범위, enum, 의존성 규칙, 권한(tier), 안전 게이트(주행 중 여부)를 확인한다. 하나라도 실패하면 아무것도 바꾸지 않고 거부한다.
3. **계획**: 각 파라미터의 적용 방식을 보고 실행 계획을 만든다. 아래 전역 변경 처리와 unit 단위 묶기 규칙을 따른다. 영향 목록을 함께 보여주고 사용자 확인을 받는다.
4. **저장**: 대상 YAML을 임시 파일에 쓰고 `rename`으로 교체한다(atomic write). 변경을 git commit으로 남긴다.
5. **적용**: 계획 순서대로 `runtime`(`set_parameters`) → `reconfigure`(전용 서비스) → `restart`(`systemctl restart <unit>`)를 실행한다. 각 단계에 타임아웃을 둔다.
6. **반영 확인**: 계층별 read-back을 수행하고, 관련 토픽 발행 주기와 `/diagnostics`가 정상 범위로 돌아오는지 확인한다.
7. **완료 또는 롤백**: 모두 통과하면 완료로 기록한다. 실패하면 **revert commit**을 만들어 YAML을 직전 상태로 되돌리고 같은 계획으로 재적용한 뒤 결과를 보고한다.
8. **응답**: 파라미터별 결과(요청값, 실제값, 성공 여부, 실패 사유)를 프론트엔드에 반환하고 WebSocket으로 진행 상황을 스트리밍한다.

#### 계획 단계: unit 단위 묶기

재시작 수단이 `systemctl restart <unit>`이므로 restart 집합은 **unit 단위로 계산한다.**

- change set에 `apply: restart` 파라미터가 하나라도 있으면, 그 파라미터가 속한 노드를 포함하는 **unit 전체**가 재시작 대상이 된다.
- 같은 unit에 속한 다른 노드의 `runtime` / `reconfigure` 변경은 재시작으로 함께 반영되므로 별도 적용을 생략한다.
- 8.1절 변경 검토 화면은 영향 unit에 속한 **모든 노드**와 예상 다운타임을 표시한다. 사용자가 직접 바꾼 노드만 보여주면 실제 중단 범위를 과소평가하게 된다.

#### 계획 단계: 전역 변경 처리

`global.yaml`은 11.2절대로 모든 unit의 `param_files`에 포함된다. 따라서 **전역 값 한 줄 변경은 센서 스택 전체를 재시작 대상으로 만든다.** 이 비용은 구조적으로 수용하되, 다음을 따른다.

- 전역 파라미터 변경 시 영향 unit 목록과 예상 다운타임을 변경 검토 화면에서 강조 표시하고, 10.1절의 추가 확인을 받는다.
- 재시작은 **순차가 아니라 일괄**로 한다. 영향 unit 전체를 중단하고 YAML을 교체한 뒤 전체를 기동한다. 전역 값은 frame 이름이나 extrinsic처럼 노드 간 일관성이 전제인 항목이므로, 순차 재시작은 일부 노드가 구 값, 일부가 신 값인 중간 상태를 만들어 오히려 위험하다. 설정 변경은 10.1절에 따라 정차 중에만 수행되므로 일괄 중단의 비용은 감수할 수 있다.
- 센서 unit을 모두 기동한 뒤 다운스트림(perception 등) unit을 올린다.

#### 반영 확인: 계층별 read-back

| 계층 | read-back 경로 | 비고 |
|---|---|---|
| `device` | 스키마의 `readback_via` (장치 진실 경로) | 노드 파라미터는 근거로 쓰지 않는다 |
| `driver` | `/<node>/get_parameters` | |
| `global` | 참조하는 모든 노드에서 각각 확인 | 하나라도 불일치면 실패 |

`apply: restart` 파라미터는 노드가 YAML을 다시 읽고 올라오므로 노드 파라미터 read-back이 거의 항상 일치한다. 즉 이 경우 read-back은 "노드가 정상 기동했다"만 입증한다. 실제 반영 여부는 `device` 계층의 `readback_via`와 토픽 발행 주기·`/diagnostics`로 판정한다.

### 6.2 실패 처리

| 실패 지점 | 상태 | 처리 |
|---|---|---|
| 검증 실패 | 아무것도 바뀌지 않음 | 사유 표시, 종료 |
| YAML 저장 실패 | 아무것도 바뀌지 않음 | 사유 표시, 종료 |
| `set_parameters` 거부 (`successful=false`) | YAML만 바뀜 | 롤백 |
| 서비스/재시작 타임아웃 | 불명확 | 롤백 후 노드 상태 재수집 |
| read-back 불일치 | 장치가 값을 보정했거나 거부 | 롤백, 실제값 표시 |
| 데이터 미수신 (예: 토픽 0 Hz) | 장치는 바뀌었으나 수신 실패 | 롤백, 점검 규칙(9장) 결과 표시 |
| 롤백도 실패 | 위험 상태 | 경고 배너, 수동 조치 안내, 해당 서브시스템 표시 고정 |

롤백은 **새 revert commit을 쌓는 방식**으로 수행한다. `reset --hard`로 과거를 덮어쓰면 실패한 적용 시도 자체가 이력에서 사라져 10.3절의 감사 로그 요건과 모순된다.

### 6.3 동시성

- change set은 한 번에 하나만 실행한다 (전역 lock). 두 번째 요청은 대기열이 아니라 거부한다.
- 적용 중에는 대상 노드의 편집 화면을 잠근다.
- YAML이 Config App 밖에서 수정된 경우(파일 해시 불일치) 저장 전에 경고하고 사용자가 선택하게 한다.

## 7. 백엔드 명세

백엔드는 ROS 2 노드 하나로 구현한다. 언어는 Python(rclpy + FastAPI)으로 확정했다. C++ 2안은 비교 참고용으로 남긴다.

### 7.1 기술 스택

| 항목 | 1안 (확정) | 2안 (Python 의존성 제거, 참고용) |
|---|---|---|
| ROS 2 클라이언트 | rclpy | rclcpp |
| HTTP/WebSocket | FastAPI + uvicorn (worker 1개) | cpp-httplib 또는 Crow |
| YAML | ruamel.yaml (주석·순서 보존) | yaml-cpp |
| 버전 관리 | git CLI 또는 pygit2 | libgit2 |
| 빌드/배포 | colcon (ament_python) | colcon (ament_cmake) |

#### Python 의존성 설치: venv `--system-site-packages`로 확정

Jazzy가 대상으로 하는 Ubuntu 24.04는 시스템 pip 설치를 막는다(PEP 668). 해결 경로는 **venv 하나뿐이다.**

```bash
python3 -m venv --system-site-packages /opt/av_config/.venv
/opt/av_config/.venv/bin/pip install \
  "fastapi>=0.115" "uvicorn[standard]" "pydantic>=2.9" ruamel.yaml pygit2
```

`--system-site-packages`로 apt 설치된 rclpy를 그대로 쓰고, venv 자체 site-packages가 시스템 패키지를 가리므로 최신 FastAPI와 Pydantic v2가 적용된다. systemd 유닛의 `ExecStart`는 `/opt/av_config/.venv/bin/python`을 직접 가리킨다.

**apt로 `python3-fastapi`를 설치하고 venv 없이 실행하는 경로는 채택하지 않는다.** Ubuntu 24.04(noble)의 패키지 버전이 아래와 같아 Pydantic v1에 고정되기 때문이다.

| 패키지 | noble 버전 | 문제 |
|---|---|---|
| `python3-fastapi` | 0.101.0-3 (universe) | 2023년 릴리스 |
| `python3-uvicorn` | 0.27.1-1 (universe) | — |
| `python3-pydantic` | **1.10.14-1** | v2 API(`model_json_schema()`) 없음 |

#### 그 외 구현 규칙

- rclpy executor는 별도 스레드에서 spin하고, FastAPI 핸들러는 서비스 호출 future를 asyncio로 기다린다.
- uvicorn worker는 1개로 고정하고 `reload=False`로 실행한다. 같은 이름의 노드가 중복 생성되는 것을 막기 위함이다.
- YAML 쓰기는 주석과 키 순서를 보존한다. 사람이 직접 읽는 파일이기 때문이다.
- **Pydantic은 파라미터 모델링에 쓰지 않는다.** 파라미터의 진실 원천은 4.4절 스키마와 런타임 `ParameterDescriptor`이므로, Pydantic 모델을 따로 두면 정의가 3중화된다. Pydantic의 역할은 ① REST 요청/응답 검증, ② `schemas/*.yaml`과 `rules/*.yaml`의 메타스키마 검증으로 한정한다.
- venv에 Pydantic v2를 넣으면 시스템의 1.10이 가려지므로, `python3-pydantic`에 의존하는 ROS 패키지를 같은 프로세스에서 import하지 않는지 확인한다 (rclpy 코어는 해당 없음).

### 7.2 내부 모듈

| 모듈 | 책임 |
|---|---|
| `discovery` | 노드 탐색, 파라미터 수집, 저장값 머지와 drift 판정, 그래프 기반 다운스트림 도출, 스키마 매칭 (5장) |
| `schema` | 스키마 로드, 메타스키마 검증(`layer: device`면 `readback_via` 필수), 검증 규칙, 의존성 그래프 |
| `store` | parameter file 읽기/쓰기, atomic write, git commit/revert, 프로파일 |
| `planner` | change set → 실행 계획. **unit 단위** restart 집합, 전역 변경 fan-out 계산, 적용 순서 |
| `applier` | `set_parameters`, 전용 서비스 호출, systemd 재시작 (D-Bus 또는 `systemctl`) |
| `verifier` | 계층별 read-back 분기(`device`는 `readback_via`), 토픽 주기 측정, diagnostics 확인 |
| `health` | 상시 상태 수집과 점검 규칙 실행 (9장) |
| `safety` | 차량 상태 구독, 적용 허용 여부 판단, `safety.mode` 처리 |
| `auth` | 로그인, 역할(tier) 확인, 감사 로그 |
| `api` | REST/WebSocket 엔드포인트, 정적 파일 서빙 |

### 7.3 REST API (초안)

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/v1/nodes` | 노드 목록, 매칭된 스키마, unit 매핑, 상태 |
| GET | `/api/v1/params/{node}` | 파라미터 목록 (런타임값, 저장값, drift, 스키마 정보). `{node}`는 선행 슬래시를 포함한 전체 이름 |
| POST | `/api/v1/changesets` | change set 검증만 (`dry_run=true`) 또는 적용 |
| GET | `/api/v1/changesets/{id}` | 적용 진행 상태와 결과 |
| GET | `/api/v1/history` | 변경 이력 (git log 기반) |
| GET | `/api/v1/history/{commit}/diff` | 해당 commit 의 diff |
| POST | `/api/v1/history/{commit}/restore` | 특정 시점의 값으로 복원. **revert commit을 새로 쌓는다** |
| GET/POST | `/api/v1/profiles` | 프로파일 목록, 저장 (M5) |
| POST | `/api/v1/profiles/{name}/apply` | 프로파일 적용 (change set으로 변환) (M5) |
| GET | `/api/v1/health` | 점검 규칙 결과 (M4) |
| GET | `/api/v1/onboarding/{node}` | 신규 센서 등록 절차와 설정 파일 초안 (§12.2) |
| GET | `/api/v1/safety` | 현재 적용 가능 여부와 사유 (`safety.mode` 포함) |
| WS | `/api/v1/stream` | 적용 진행, 노드 상태, 점검 결과 실시간 푸시 |

change set 요청 예시:

```json
{
  "dry_run": false,
  "comment": "라이다 해상도 상향 테스트",
  "changes": [
    {"node": "/ouster/os_driver", "param": "lidar_mode", "value": "2048x10"}
  ]
}
```

#### 노드 이름을 경로에 두는 방식

파라미터 조회 경로는 `/api/v1/params/{node}`이며 노드 이름이 경로의 **마지막**에 온다. 초판의 `/api/v1/nodes/{node}/params`는 라우팅이 불가능하다. 노드 이름이 슬래시를 포함하므로 path 변환기가 뒤의 `/params`까지 함께 소비하고, `%2F`로 인코딩해도 ASGI 서버가 경로를 디코딩한 뒤 라우팅하므로 해결되지 않는다.

#### WebSocket 규약

`/api/v1/stream`은 세 종류의 이벤트를 함께 전달하므로 메시지에 `type` 디스크리미네이터를 둔다.

```json
{"type": "changeset_progress", "id": "...", "stage": "verify", "detail": {}}
{"type": "node_state",         "node": "/ouster/os_driver", "alive": true, "drift": []}
{"type": "health_result",      "rule": "R1", "status": "warn", "detail": {}}
```

이 채널이 라이브 상태 채널까지 겸하므로, 클라이언트는 **지수 백오프 재연결**과 **재연결 시 상태 재동기화**(`/api/v1/nodes`, `/api/v1/health` 재조회)를 구현한다. 연결이 끊긴 동안의 이벤트는 재전송하지 않는다.

## 8. 프론트엔드 명세

프론트엔드는 빌드된 정적 파일(SPA)로 Config App이 직접 서빙한다. 별도 웹 서버는 두지 않는다. 폼은 백엔드가 주는 파라미터 디스크립터로 생성한다.

### 8.1 화면 구성

| 화면 | 내용 |
|---|---|
| 대시보드 | 서브시스템별 상태(정상/경고/오류), 노드 alive, 주요 토픽 Hz, 적용 가능 여부 배지 |
| 노드 목록 | 센서·노드별 카드. 매칭된 스키마, 소속 unit, drift 여부, 마지막 변경 시각 |
| 파라미터 편집 | 그룹별 폼. 각 필드에 저장값, 런타임값, 적용 방식 아이콘, 등급, 설명, 범위 |
| 변경 검토 | 보류 중인 change set의 diff, **영향 unit과 그 안의 전체 노드**, 재시작 대상, 예상 다운타임 |
| 적용 진행 | 단계별 진행 표시(검증 → 저장 → 적용 → 확인), 파라미터별 결과 |
| 이력 | 변경 목록(누가, 언제, 무엇을, 코멘트), diff, 이 시점으로 복원 |
| 프로파일 | 프로파일 목록, 현재 설정을 프로파일로 저장, 프로파일 적용 |
| 온보딩 | 미등록 노드나 스키마 없는 노드에서 §12.2 절차와 설정 파일 초안 3종 |
| 점검 | 점검 규칙 결과와 관련 파라미터로 이동하는 링크 |

### 8.2 UX 규칙

- 편집은 즉시 적용되지 않는다. 변경은 change set에 쌓이고, 변경 검토 화면에서 한 번에 적용한다.
- 적용 방식이 `restart`인 필드는 아이콘과 문구로 "적용 시 해당 unit 재시작"을 미리 표시한다. 영향 범위는 노드가 아니라 unit 기준으로 쓴다.
- 전역 파라미터(`global_ref`를 가진 값, `global.yaml` 소속) 변경은 영향 unit 목록과 함께 **센서 스택 전체 재시작** 경고를 강조 표시한다.
- 저장값과 런타임값이 다르면(drift) 필드에 경고를 붙인다. 저장값은 5.1절 머지 결과를 쓴다.
- 주행 중이거나 권한이 없으면 적용 버튼을 비활성화하고 사유를 표시한다.
- 적용 결과는 요청값이 아니라 read-back 값을 기준으로 표시한다. `device` 계층은 장치에서 읽은 값을 표시한다.
- 원본 모드(스키마 없음) 노드는 경고 배지를 달고 engineer에게만 보인다.
- launch 규칙(12.1절 8번)을 위반한 노드는 drift 경고와 구분되는 "launch 규칙 위반" 배지로 표시한다. 이 drift는 Config App이 해소할 수 없기 때문이다.

### 8.3 기술 스택과 선택 근거

| 항목 | 선택 |
|---|---|
| 빌드/프레임워크 | **Vite + React + TypeScript** |
| 서버 상태 | TanStack Query (파라미터 폴링, change set 후 캐시 무효화) |
| 라우팅 | TanStack Router |
| 클라이언트 상태 | Zustand (보류 중 change set) |
| 폼 | **타입별 필드 렌더러 직접 작성** |

#### Next.js를 쓰지 않는 이유

SSR·RSC·SEO가 모두 무용하고(LAN 내부, 사용자 1~2명, 실시간 데이터), 프로덕션에 Node 프로세스가 추가로 필요해 "Config App 노드 하나가 UI와 API를 모두 서빙한다"는 3장 전제와 충돌한다. Vite는 정적 파일만 산출하므로 FastAPI가 그대로 서빙할 수 있다.

#### JSON Schema 폼 자동 생성 라이브러리(RJSF, JSONForms)를 쓰지 않는 이유

1. **ROS 2 파라미터는 평면 구조이고 타입이 8종으로 닫혀 있다.** `ParameterType`은 bool, int64, double, string과 각 배열형뿐이다. 중첩 객체도, `oneOf`/`$ref`도, 재귀 스키마도 없다. 임의 깊이의 트리를 처리하기 위해 존재하는 폼 생성기의 능력이 전부 낭비되고, 대가로 커스터마이즈 비용만 남는다.
2. **8.1절의 필드는 입력 위젯이 아니라 복합 컴포넌트다.** 한 필드가 저장값, 런타임값, drift 경고, 적용 방식 아이콘, 등급, 범위, 설명을 동시에 담는다. RJSF에서 이를 구현하려면 `FieldTemplate`과 위젯을 전부 덮어써야 하고, 그 지점부터 라이브러리는 부채다.
3. **스키마가 런타임에 발견된다.** 5.1절대로 파라미터는 실행 중인 노드에서 `describe_parameters`로 수집한다. 정적 JSON Schema를 빌드 타임에 생성하는 전제가 성립하지 않는다.

따라서 백엔드 파라미터 디스크립터를 받아 `switch (descriptor.type)`로 분기하는 필드 렌더러 8~10개를 직접 작성한다.

#### 제외 항목

| 제외 | 이유 |
|---|---|
| Monaco, CodeMirror | 8.1절에 원본 YAML 편집 화면이 없다. 3.2절 소유권 모델상 YAML은 Config App만 쓰므로 자유 텍스트 편집기는 모델을 깨뜨린다 |
| 텍스트 diff 라이브러리 | 파라미터별 before/after를 구조화된 데이터로 갖고 있으므로 테이블 diff가 더 정확하다 |

## 9. 상태 모니터링과 이상 감지

"무엇을 고쳐야 하는가"는 자동으로 알 수 없지만, 이상 징후를 감지해 관련 파라미터를 짚어주는 것은 규칙 기반으로 자동화한다. 1차 릴리스는 아래 규칙 중 R1~R4를 구현한다.

### 9.1 수집 대상

- 노드 alive 여부 (graph 변화 감지)
- 스키마에 정의한 주요 토픽의 발행 주기(Hz)와 마지막 수신 시각
- `/diagnostics` 상태 (OK/WARN/ERROR/STALE)
- 저장값과 런타임값 drift
- 호스트 NIC IP와 링크 상태, 지정 포트 수신 여부 (읽기 전용 점검)

### 9.2 점검 규칙

| ID | 증상 | 감지 방법 | 짚어줄 원인/파라미터 |
|---|---|---|---|
| R1 | 센서 데이터 미수신 | 토픽 0 Hz가 N초 지속 | 장치 연결, `udp_dest`, 방화벽 |
| R2 | `udp_dest`와 호스트 IP 불일치 | 파라미터 값과 NIC IP 비교 | `udp_dest` |
| R3 | 드라이버 경고/오류 | `/diagnostics` WARN/ERROR | 해당 노드 |
| R4 | 저장값과 런타임값 불일치 | 5.1절 머지 결과와 런타임 값 비교. launch 규칙 위반 노드는 별도 분류 | drift 파라미터 목록 |
| R5 | 발행 주기 저하 | 실측 Hz와 설정 기대값 비교 | 해상도, 프레임레이트, 수신 버퍼 |
| R6 | frame 누락 | TF 트리에 `frame_id` 없음 | `*_frame`, extrinsic |
| R7 | QoS 불일치 | 발행자/구독자 QoS 비교 | QoS 설정 |
| R8 | 센서 간 시간 불일치 | 타임스탬프 차이, PTP 상태 | `timestamp_mode` |
| R9 | 기준 프로파일과 다름 | 현재 YAML과 기준 프로파일 diff | 차이 나는 파라미터 |

규칙의 임계값(N초, 허용 Hz 오차 등)은 규칙 설정 파일로 관리하며, 수치는 센서 확정 후 정한다.

## 10. 안전, 권한, 보안

설정 변경은 차량 거동에 직접 영향을 주므로, 적용은 안전 게이트와 역할 권한을 모두 통과해야 한다.

### 10.1 안전 게이트

- `safety` 모듈이 차량 상태 토픽(TBD)을 구독해 주행 모드와 속도를 확인한다.
- 자율주행 모드이거나 속도가 0이 아니면 `lock_while_driving: true`인 파라미터의 적용을 거부한다. 기본값은 `true`로 두고, 안전한 항목만 스키마에서 `false`로 연다.
- 차량 상태를 알 수 없으면(토픽 미수신) 잠금으로 간주한다 (fail-safe).
- `restart`가 필요한 change set은 대상 서브시스템이 일시 중단된다는 확인을 추가로 받는다. 전역 파라미터 변경은 중단 범위가 센서 스택 전체이므로 확인 문구에 영향 unit 목록을 포함한다.

#### `safety.mode` — 개발 단계 게이트 제어

위 fail-safe 규칙과 차량 상태 토픽 미확정(13.2절) 상태가 겹치면, 안전 게이트를 켜는 순간 **모든 적용이 거부되어 M2에서 만든 기능을 테스트할 수 없다.** 이를 피하기 위해 `rules/safety.yaml`에 모드를 둔다.

| 모드 | 동작 | 용도 |
|---|---|---|
| `open` | 차량 상태 미수신 시 적용을 허용한다. 모든 적용에 경고 로그를 남긴다 | 개발·벤치 테스트 |
| `enforce` | 차량 상태 미수신 시 잠금 (fail-safe) | 차량 탑재 운영 |

프로덕션 배포 시 `enforce`를 강제한다. `enforce`이면서 시작 후 일정 시간 차량 상태 토픽을 받지 못하면 경고 로그를 남기고 `/api/v1/safety`에 사유를 노출한다. 기본값 전환 시점은 13.1절 M3이다.

### 10.2 역할

| 역할 | 할 수 있는 것 |
|---|---|
| `viewer` | 상태, 설정, 이력 조회 |
| `operator` | `operator` 등급 파라미터 변경, 프로파일 적용 |
| `engineer` | `engineer` 등급 파라미터 변경, 원본 모드 노드 편집, 롤백 |
| `admin` | 사용자 관리, 스키마/규칙 파일 교체 |

### 10.3 보안

- **바인딩**: 차량 내부 네트워크 인터페이스에만 바인딩한다. `127.0.0.1`만 열면 호스트 본체에서만 접속 가능하므로, 노트북 접속이 필요하면 해당 NIC에 연다. 외부(인터넷) 노출은 금지한다.
- **인증**: 로그인 필수. 세션 토큰은 HttpOnly 쿠키. 방식(로컬 계정, 공용 PIN 등)은 TBD.
  - M3 까지의 임시 수단으로 역할과 사용자를 `X-AV-Role` / `X-AV-User` 헤더로 받는다. 헤더가 없으면 `engineer` 로 간주하고 경고를 남긴다. **이 상태로는 외부에 노출하면 안 된다.**
- **전송**: 차량 내부망이라도 가능하면 HTTPS(자체 서명 인증서)를 사용한다. TBD.
- **감사 로그**: 모든 change set에 사용자, 시각, 코멘트, 결과를 기록하고 git commit 메시지에 포함한다. 롤백과 복원도 revert commit으로 남기므로 이력에 구멍이 생기지 않는다(6.2절).
- **프로세스 권한**: Config App은 전용 사용자 계정으로 실행하고, parameter file 디렉터리와 지정 systemd 유닛 재시작 권한만 준다.
  - **polkit 규칙을 우선한다.** D-Bus로 `StartUnit`/`RestartUnit`을 호출하면서 유닛 이름을 화이트리스트로 제한하는 방식이 가장 좁다.
  - sudoers를 쓸 경우 유닛 이름을 **와일드카드 없이 정확히** 나열한다. `av-*.service` 같은 패턴은 임의 유닛 재시작 경로가 되어 권한 상승에 쓰일 수 있다.

## 11. 배포, 프로세스 관리, 자원 격리

서브시스템별로 launch를 나누고 각각 systemd 서비스로 등록한다. Config App은 이 서비스 단위로 재시작하며, 자신은 AI 추론과 제어 루프에 영향을 주지 않도록 자원을 제한한다.

### 11.1 디렉터리 구조 (제안)

```
/opt/av_config/
├── .venv/             # 7.1절 Python 의존성 (git 관리 대상 아님)
├── params/            # parameter file (git 저장소)
│   ├── global.yaml    # 시스템 전역 설정 (frame, extrinsic 등)
│   ├── lidar_top.yaml
│   ├── camera_front.yaml
│   └── profiles/
├── schemas/           # 드라이버별 파라미터 스키마
├── rules/             # 점검 규칙 설정, safety.yaml
└── services.yaml      # 노드 ↔ systemd 유닛 ↔ parameter file 매핑
```

git 저장소는 `params/`에 둔다. `.venv/`와 `schemas/`, `rules/`는 배포 산출물이므로 설정 이력과 분리한다.

### 11.2 서비스 매핑

`services.yaml`은 Config App이 어떤 노드를 어떤 유닛으로 재시작하고 어떤 YAML을 고쳐야 하는지 아는 유일한 근거다.

```yaml
lidar_top:
  unit: av-lidar-top.service
  launch: "ros2 launch av_bringup lidar_top.launch.py"
  param_files: [params/lidar_top.yaml, params/global.yaml]
  nodes: [/ouster/os_driver]
camera_front:
  unit: av-camera-front.service
  param_files: [params/camera_front.yaml, params/global.yaml]
  nodes: [/camera_front/camera]
```

읽는 방법:

- `nodes`는 배열이다. **unit 하나가 노드 여러 개를 띄울 수 있고, 재시작은 unit 단위로만 가능하다.** 6.1절 계획 단계는 이 관계를 전제로 restart 집합을 계산한다.
- `param_files`의 **순서가 저장값을 결정한다**(5.1절). 뒤에 오는 파일이 앞을 덮으므로, 위 예시에서는 `global.yaml`이 노드별 값을 덮는다.
- `global.yaml`이 모든 unit에 포함되므로 전역 값 변경은 전체 unit에 영향을 준다. 처리 방식은 6.1절 전역 변경 처리를 따른다.

### 11.3 Config App 자원 제한

```ini
[Service]
User=avconfig
ExecStart=/opt/av_config/.venv/bin/python -m av_config.main
Nice=10
CPUAffinity=0 1        # AI/제어에 할당하지 않은 코어 (TBD)
CPUQuota=20%
MemoryMax=300M
Restart=on-failure
```

- Config App이 죽어도 다른 노드는 영향을 받지 않는다. Config App은 설정 경로에만 있고 데이터 경로에 없기 때문이다.
- **Config App은 자신이 재시작하는 unit에 포함되지 않아야 한다.** `services.yaml`에 등록된 어떤 unit에도 Config App을 넣지 않는다. 자기 자신을 재시작하면 적용 트랜잭션이 중단되고 결과를 보고할 수 없다.
- 같은 DDS 도메인 ID를 사용해야 노드를 볼 수 있다. 도메인과 RMW 설정은 다른 노드와 일치시킨다.

## 12. 드라이버 작성 규칙과 신규 센서 온보딩

직접 만드는 드라이버는 처음부터 Config App과 연동되도록 아래 규칙을 따른다. 오픈소스 드라이버가 규칙을 따르지 않으면 스키마로 보완한다.

### 12.1 드라이버 작성 규칙

1. 모든 설정값은 `declare_parameter`로 선언한다. 코드에 상수로 박지 않는다.
2. 모든 파라미터에 `ParameterDescriptor`의 `description`, 범위(`integer_range`/`floating_point_range`), `read_only`를 채운다.
3. 실행 중 변경을 지원하는 파라미터는 `add_on_set_parameters_callback`에서 처리하고, 거부 시 `SetParametersResult.reason`에 사유를 쓴다. 지원하지 않는 파라미터는 `read_only=true`로 선언한다.
4. 장치 재설정이 필요한 변경은 lifecycle 노드의 `configure` 전이 또는 전용 서비스로 처리한다.
5. **장치에 실제로 적용된 값을 되읽을 수 있는 조회 서비스를 제공한다.** `layer: device` 파라미터는 이 경로가 없으면 반영 여부를 검증할 수 없다(4.4절 `readback_via`).
6. `/diagnostics`에 연결 상태, 수신 주기, 오류를 `hardware_id`와 함께 발행한다.
7. 파라미터 이름 규칙: 소문자 snake_case, 장치 설정은 `device.` 접두사, 프레임은 `*_frame` 접미사 (TBD: 팀 합의).
8. **launch 파일에서 파라미터를 인라인으로 주입하지 않는다.** `parameters=[{...}]` 형태의 딕셔너리를 금지하고, 파라미터는 `services.yaml`에 등록된 `--params-file`로만 전달한다. launch는 Config App이 수정하지 않으므로(3.2절), 인라인 오버라이드는 Config App이 해소할 수 없는 유령 drift를 상시 발생시킨다.
9. 기본 parameter file을 패키지 `config/`에 제공한다.

### 12.2 신규 센서 온보딩 절차

1. **ROS 2 드라이버 확보**: 제조사 문서 → 제조사 GitHub → ROS Index 순으로 확인. 대상 ROS 2 배포판과 ARM64(Jetson) 빌드 가능 여부를 확인한다.
2. 없으면 제조사 SDK 또는 프로토콜 문서로 래퍼 노드를 작성한다 (12.1 규칙 적용).
3. 장치를 연결한 상태에서 `ros2 param list`, `ros2 param describe`로 실제 파라미터를 수집하고, **`ros2 service list`로 reconfigure용 서비스와 조회 서비스의 실제 존재 여부를 확인한다.** 조회 서비스가 없으면 `layer: device` 파라미터를 검증할 수 없으므로 드라이버 수정 대상이 된다.
4. 장치 매뉴얼과 비교해 드라이버로 못 바꾸는 필요한 설정이 있는지 확인한다. 있으면 드라이버를 수정한다.
5. 스키마 파일 작성: 계층, 적용 방식, 등급, `readback_via`, 의존성, `lock_while_driving` 태깅.
6. launch와 parameter file을 만들고 `services.yaml`에 등록, systemd 유닛 생성. 이때 unit에 포함되는 노드 전체를 `nodes`에 적는다.
7. 점검 규칙에 주요 토픽과 기대 주기를 등록한다.
8. 테스트: 각 적용 방식별 적용, read-back(장치 경로 포함), 롤백을 실제 장치로 확인한다.

#### Config App 의 온보딩 보조

드라이버 노드가 떠 있으면 5장의 자동 수집 결과로 위 3·5·6단계의 **초안**을 만들어
화면에 내놓는다. 손으로 `ros2 param describe` 를 돌려 YAML 을 쓰는 공수를 줄이는 것이
목적이다.

| 초안 | 자동으로 채우는 것 | 근거 |
|---|---|---|
| `services.yaml` 조각 | 서브시스템·unit 이름(노드 이름에서 추론), `param_files`, `nodes`, `topics` | 노드 이름, 발행 토픽 목록 |
| `params/<이름>.yaml` | 현재 런타임 값 (`read_only` 는 제외) | `get_parameters` |
| `schemas/<드라이버>.yaml` | 파라미터 이름, 타입, 범위, 설명, `node_name_regex`, `required_services` | `describe_parameters`, 서비스 목록 |

**계층(`layer`), 적용 방식(`apply`), 노출 등급(`tier`) 은 채우지 않는다.** 어떤
파라미터가 장치 펌웨어에 쓰이는지, 실행 중 바꿀 수 있는지는 ROS 가 알려주지 않는다.
드라이버 문서와 장치 명세를 봐야 하므로 그 자리는 `TODO` 로 남기고, 화면이 남은
개수를 센다.

스키마 초안의 기본 계층은 `driver` 다. `device` 로 두면 `readback_via` 가 없어
로드 단계에서 거부되기 때문이다(4.4절). `device` 로 바꿀 때 `readback_via` 를 함께
채우도록 초안 주석이 안내한다.

이미 등록된 노드에는 추론한 이름이 아니라 **현재 `services.yaml` 의 이름**을
보여준다. 추론한 이름을 그대로 붙여 넣으면 같은 노드가 두 서브시스템에 중복
등록되기 때문이다.

5.2절의 원본 모드 노드에도 같은 보조가 적용된다. 등록은 됐지만 스키마가 없는
경우다.

#### 드라이버가 배포하는 parameter file 에서 가져오기

노드를 띄우지 않고 드라이버 패키지의 `config/*.yaml` 만으로도 스키마를 만들 수 있다.
장치가 아직 없거나, 드라이버를 받아만 두고 구성을 미리 잡을 때 쓴다.

이 경로가 중요한 이유가 있다. 상당수 오픈소스 드라이버는 `declare_parameter`를
호출하면서 `ParameterDescriptor`를 채우지 않는다. 그러면 자동 수집으로는 이름과
타입만 얻고 **설명·범위·허용 값이 전부 비어 있다.** 반면 그 드라이버가 배포하는
parameter file 의 **주석**에는 그 정보가 적혀 있다.

실측 예 — ouster-ros (ros2 브랜치, 2026-09-24):

| 항목 | 수 |
|---|---|
| `declare_parameter` 호출 | 99 |
| `ParameterDescriptor` 사용 | **0** |
| `add_on_set_parameters_callback` | **0** |
| `config/driver_params.yaml` 의 파라미터 | 57 |
| 그 주석에서 뽑아낸 허용 값 목록 | 23 |

즉 이 드라이버에서는 **설명과 허용 값을 parameter file 주석에서만 얻을 수 있고**,
실행 중 변경이 불가능하므로 모든 파라미터가 `restart`다.

생성은 두 입력을 합친다.

| 입력 | 채우는 것 |
|---|---|
| 드라이버의 parameter file | 이름, 기본값, 타입, 설명, 허용 값 |
| 오버레이 파일 | 계층, 적용 방식, 등급, `readback_via`, `global_ref` |

오버레이는 ROS 가 알려줄 수 없는 정보만 담는다. 드라이버가 업데이트되면 같은
명령으로 다시 생성하므로, 손으로 고친 내용이 사라지지 않는다. 오버레이는
`schemas/` 가 아니라 별도 디렉터리에 둔다. 스키마 디렉터리에 두면 로더가
스키마로 읽으려다 실패한다.

## 13. 구현 단계와 미결 사항

구현은 조회 → 적용 → 운영 기능 순으로 쌓는다. 각 단계는 앞 단계 없이 동작하지 않는다.

### 13.1 구현 단계

1. **M0 기반 구성**: 서브시스템별 launch, parameter file 디렉터리, `services.yaml`, systemd 유닛을 먼저 만든다. 기존 launch가 없으므로 Config App 전제 구조로 처음부터 설계한다. `params/`에 **`git init`을 수행한다** — 6.1절 저장 단계와 10.3절 감사 로그가 git에 의존하므로 선행 조건이다. `avconfig` 사용자의 `user.name`/`user.email`도 이 단계에서 설정한다.
2. **M1 조회**: 노드 탐색, 파라미터 자동 수집, 원본 모드 화면, parameter file 읽기, 저장값 머지와 drift 표시.
3. **M2 적용**: 스키마 1종(Ouster), change set, 검증, atomic write + git commit, unit 단위 `runtime`/`restart` 적용, 계층별 read-back, revert 기반 롤백. 이 단계에서는 `safety.mode: open`으로 테스트한다.
4. **M3 안전·권한**: 안전 게이트, 차량 상태 토픽 연동, `safety.mode` 기본값을 `enforce`로 전환, 로그인과 역할, 감사 로그.
5. **M4 점검**: 점검 규칙 R1~R4, 대시보드.
6. **M5 확장**: 나머지 센서 스키마, `reconfigure` 적용, 프로파일, 이력 화면, 점검 규칙 R5~R9.

### 13.2 미결 사항

확정:

- [x] ROS 2 배포판: **Jazzy**
- [x] 백엔드 언어: **Python (rclpy + FastAPI)**, 의존성은 venv `--system-site-packages`
- [x] 프론트엔드 프레임워크: **Vite + React + TypeScript**, 폼은 직접 작성 (8.3절)
- [x] 접속 방식: 노트북/태블릿만 (차량 네트워크 NIC에 바인딩)
- [x] launch 구조: 기존 없음, 서브시스템별로 새로 구성
- [x] `global.yaml` 처리: 공유 구조 유지, 전역 변경은 영향 unit 일괄 재시작 (6.1절)

미결:

- [ ] 호스트 하드웨어 모델 (Jetson 모델 또는 IPC 사양)과 OS/JetPack 버전 (Jazzy 설치 방식에 영향)
- [ ] RMW (Fast DDS / Cyclone DDS / Zenoh)
- [ ] 1차 대상 센서 목록과 각 드라이버 (공식/커뮤니티/자체)
- [x] `/ouster/set_config` 서비스 존재 여부 — **존재하나 쓸 수 없다.** `string config_file`(파일 경로)을 받아 센서 설정 전체를 교체하고 reinit 한다. 값 하나를 넘기는 방식이 아니므로 현재 `apply_via` 모델로는 호출할 수 없다. 4.2절 참고
- [x] Ouster 의 장치 값 조회 서비스 — `/ouster/get_config`, `/ouster/get_metadata` 존재. `readback_via` 를 쓸 수 있다
- [ ] `get_config` 응답 JSON 의 키 이름이 ROS 파라미터 이름과 일치하는지 (실제 장치에서 확인 필요)
- [ ] 나머지 드라이버의 장치 값 조회 서비스 존재 여부
- [ ] 인증 방식 (개인 계정 / 공용 PIN)
- [ ] 차량 상태 토픽 이름과 메시지 타입 (안전 게이트용)
- [ ] 호스트 설정(NIC, sysctl) 변경을 범위에 넣을지
- [ ] Config App에 할당할 CPU 코어
- [ ] 파라미터 이름 규칙 팀 합의 (12.1절 7번)
- [ ] 동시 사용자 수와 세션 정책

## 14. 개정 이력

### v1.1 (2026-10-30)

| 항목 | 변경 | 근거 |
|---|---|---|
| §7.1 | apt로 `python3-fastapi` 설치하는 대안 **삭제**. venv `--system-site-packages`를 유일 경로로 확정 | Ubuntu 24.04(noble)의 `python3-pydantic`이 1.10.14이므로 해당 경로는 Pydantic v1에 고정된다 |
| §7.1 | Pydantic 역할을 REST 검증과 스키마 파일 검증으로 한정 | 파라미터 진실 원천은 §4.4 스키마와 런타임 `ParameterDescriptor`다. Pydantic 모델을 두면 정의가 3중화된다 |
| §4.4 | `readback_via` 필드 신설, `layer: device`에 필수화. §6.1에 계층별 read-back 표 추가 | `device` 파라미터는 `get_parameters`로 읽으면 요청값이 그대로 돌아오므로, 장치가 값을 거부·보정한 경우를 감지할 수 없었다. 핵심 원칙 3이 무력화되는 문제 |
| §6.1, §8.1, §11.2 | restart 집합을 **unit 단위**로 계산하도록 수정. 영향 범위 표시도 unit 소속 전체 노드로 변경 | 재시작 수단이 `systemctl restart <unit>`이고 unit 하나가 노드 여러 개를 띄우므로, 노드 단위 묶기는 실제 중단 범위를 과소평가한다 |
| §6.1 | 전역 변경 처리 절 신설. 공유 구조를 유지하되 일괄 재시작으로 규정 | `global.yaml`이 모든 unit에 포함되므로 전역 값 변경은 센서 스택 전체에 영향을 준다. 전역 값은 노드 간 일관성이 전제이므로 순차 재시작은 중간 상태를 만들어 더 위험하다 |
| §5.1 | 저장값 정의와 파라미터 해석 순서 명시 | 같은 키가 여러 `param_files`에 있으면 "저장값"이 단일하게 정의되지 않아 drift 판정이 성립하지 않았다 |
| §12.1 | 규칙 8번 신설 — launch 인라인 `parameters` 금지. §5.3에 유령 drift 항목 추가 | launch는 Config App이 수정하지 않으므로(§3.2) 인라인 오버라이드는 해소 불가능한 drift를 상시 발생시킨다 |
| §10.1 | `safety.mode: open \| enforce` 도입. §13.1에 전환 시점 명시 | fail-safe 잠금 + 차량 상태 토픽 미확정 + M2(적용) → M3(안전) 순서가 겹쳐 M2 기능을 테스트할 수 없었다 |
| §6.1, §6.2, §7.3 | 롤백과 이력 복원을 **revert commit**으로 정의 | `reset --hard`는 실패한 적용 시도를 이력에서 지워 §10.3 감사 로그 요건과 모순된다 |
| §4.4, §5.1, §7.2 | `affects` → `affects_extra`로 격하. 다운스트림은 ROS 그래프에서 계산 | §1.1의 "신규 센서 추가 시 UI 코드를 거의 고치지 않는다" 목표에 맞춰 수동 유지 항목을 줄인다 |
| §8 | 프론트엔드 확정: Vite + React + TS. 폼 자동 생성 라이브러리 미사용. §8.3 선택 근거 신설 | ROS 2 파라미터는 평면 구조이고 타입이 8종으로 닫혀 있으며 스키마가 런타임에 발견되므로, JSON Schema 폼 생성기의 전제가 성립하지 않는다 |
| §7.3 | `/api/v1/stream`에 `type` 디스크리미네이터와 재연결 재동기화 규약 추가 | 한 채널이 3종 이벤트와 라이브 상태를 겸한다 |
| §10.3 | `systemctl` 권한은 polkit 우선, sudoers 사용 시 와일드카드 금지 | `av-*.service` 패턴은 임의 유닛 재시작 경로가 된다 |
| §11.3 | Config App이 자신이 재시작하는 unit에 포함되지 않음을 명시 | 자기 자신을 재시작하면 적용 트랜잭션이 중단되고 결과를 보고할 수 없다 |
| §11.1 | `.venv/` 추가, git 저장소를 `params/`로 한정 | 배포 산출물과 설정 이력을 분리한다 |
| §13.1 | M0에 `git init`과 git 사용자 설정 추가 | §6.1 저장 단계와 §10.3 감사 로그가 git에 의존한다 |
| §4.2, §13.2 | `/ouster/set_config` 존재 여부를 미결 사항으로 등록. `reconfigure`가 드라이버 의존임을 명시 | 서비스가 없으면 `lidar_mode`는 `restart`로 내려가고 §4.2의 세 단계 중 하나가 비게 된다 |
| §12.2 | 드라이버의 parameter file 주석에서 설명·허용 값을 가져오는 임포터와 오버레이 방식 추가 | ouster-ros 는 `ParameterDescriptor`를 전혀 채우지 않아(99회 선언, 0회 기술) 자동 수집만으로는 설명과 허용 값을 얻을 수 없다. 그 정보는 패키지의 `config/driver_params.yaml` 주석에만 있다 |
| §4.4 | `ParamSchema`에 `description` 필드 추가 | 설명을 YAML 주석으로 두면 UI 가 읽을 수 없다. 드라이버가 기술자를 채우지 않을 때 이 필드가 유일한 설명 출처다 |
| §4.2 | 설정 파일형 서비스(`set_config`)를 현재 `apply_via` 모델로 다룰 수 없음을 명시 | ouster-ros 의 `set_config` 는 값이 아니라 설정 파일 경로를 받아 센서 전체를 재설정한다. 실물 확인 결과 |
| §13.2 | `/ouster/set_config` 와 장치 조회 서비스 미결 사항 해소 | https://github.com/ouster-lidar/ouster-ros 소스에서 직접 확인 |
| §12.2, §7.3, §8.1 | 온보딩 보조 신설 — 자동 수집 결과로 `services.yaml` 조각, parameter file, 스키마 초안을 만들어 화면에 제공 | 신규 센서는 등록 전까지 읽기 전용인데 화면이 이유도 방법도 알려주지 않았다. §12.2 의 3·5·6단계를 손으로 하던 공수를 줄인다 |
| §8.2 | 노드 전체 잠금 사유를 필드마다 반복하지 않도록 규정 | 같은 문장이 수십 번 나오면 읽지 않게 되고 파라미터별 사유가 묻힌다 |
| §7.3 | SPA 딥링크 폴백 | `StaticFiles` 만으로는 `/history` 를 직접 요청할 때 404 가 나 새로고침·북마크·URL 공유가 깨졌다 |
| §4.4 | `apply_via`/`readback_via`에 `request_field`, `response_field`, `json_path` 추가 | 서비스 타입은 그래프에서 조회할 수 있지만 어느 필드에 값을 넣고 꺼낼지는 드라이버마다 달라 스키마가 지정해야 한다. 구현 중 발견 |
| §10.3 | M3 까지의 임시 인증 수단(`X-AV-Role`/`X-AV-User` 헤더) 명시 | M2 를 테스트하려면 역할을 전달할 통로가 필요하다. 임시임을 문서에 남겨 운영 노출을 막는다 |
| §7.3 | 파라미터 조회 경로를 `/api/v1/params/{node}`로 변경 | 노드 이름이 슬래시를 포함하므로 `/nodes/{node}/params`는 라우팅할 수 없다. 구현 중 발견 |
| §13.2 | 확정 항목을 `[x]`로 분리, 신규 미결 항목 추가 | 초판은 본문에서 확정으로 서술한 항목도 `[ ]`로 두어 상태가 모호했다 |
