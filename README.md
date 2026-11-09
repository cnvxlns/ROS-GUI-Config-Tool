# AV Web Configure App

자율주행 IPC/Jetson 에 연결된 센서와 ROS 2 노드의 설정을 **웹 UI 하나**에서 조회,
변경, 적용하는 도구다. 센서마다 다른 설정 방식(벤더 웹 UI, SDK, CLI)을 한 화면으로
통일하고, 변경 이력을 git 에 남겨 실패하면 되돌린다.

- 동작 구조 (그림): [`docs/architecture.md`](docs/architecture.md)
- 설계 명세: [`docs/web-configure-app-spec.md`](docs/web-configure-app-spec.md)

핵심 원칙 두 개만 기억하면 구조가 이해된다.

1. **YAML 이 주인이다.** 모든 변경은 parameter file 에 먼저 저장되고, 노드의 런타임
   상태는 그 결과일 뿐이다.
2. **장치에는 드라이버만 쓴다.** Config App 은 센서 API 를 직접 부르지 않는다.
   YAML 을 쓰고 드라이버 노드에 적용을 요청한다.

---

## 1. 구현 현황

| 단계 | 범위 | 상태 |
|---|---|---|
| M0 | 설정 디렉터리, `services.yaml`, 스키마, systemd 유닛 | 구현 완료 |
| M1 | 노드 탐색, 파라미터 자동 수집, 저장값 머지, drift 표시, 조회 UI | 구현 완료 |
| M2 | change set, 검증, atomic write + git, unit 단위 적용, 계층별 read-back, revert 롤백 | 구현 완료 |
| M3 | 안전 게이트(차량 상태 구독), 로그인과 역할, 감사 로그 | 미구현 |
| M4 | 점검 규칙 R1~R4, 대시보드 | 미구현 |
| M5 | 나머지 센서 스키마, 프로파일, 점검 규칙 R5~R9 | 미구현 |

검증 상태는 [9장](#9-검증-상태)에 정리했다. 요약하면 **ROS 2 실환경에서는 아직
실행해보지 못했다.**

---

## 2. 5분 안에 UI 띄워보기 (docker 불필요)

ROS 2 컨테이너 없이도 UI 전체를 눌러볼 수 있다. `tools/mock_backend.py` 가 **실제
FastAPI 앱과 실제 트랜잭션 엔진**을 쓰고, rclpy 가 필요한 세 계층(노드 탐색,
적용, read-back)만 가짜로 끼운다.

### 2.1 준비물

- Node.js 18 이상 (`node -v`)
- Python 3.10 이상 (`python3 -V`)
- git

파이썬 가상환경 도구가 하나 필요하다. **`uv` 를 권한다.** `python3 -m venv` 는
배포판에 따라 `python3-venv` 패키지가 따로 있어야 하고, 그 설치에는 sudo 가 필요하다.

```bash
# uv 가 없으면
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2.2 명령

```bash
cd ~/ros2_ws

# (1) 프론트엔드 빌드 — 산출물은 백엔드가 서빙할 위치로 떨어진다
cd frontend && npm install && npm run build && cd ..

# (2) 개발용 파이썬 환경
uv venv --python 3.12 .venv-dev
uv pip install --python .venv-dev/bin/python \
  "fastapi>=0.115" "uvicorn[standard]" "pydantic>=2.9" ruamel.yaml httpx pytest

# (3) 개발 서버 실행
.venv-dev/bin/python tools/mock_backend.py --port 8080
```

브라우저에서 **http://127.0.0.1:8080/** 을 열면 된다. API 문서는
http://127.0.0.1:8080/docs 에 있다. 종료는 `Ctrl+C`.

신규 센서 온보딩 화면을 보려면 `--new-sensor` 를 붙인다. `services.yaml` 에도
스키마에도 없는 노드가 하나 더 뜬다.

```bash
.venv-dev/bin/python tools/mock_backend.py --port 8080 --new-sensor
```

<details>
<summary>uv 대신 표준 venv 를 쓰고 싶다면</summary>

```bash
# ensurepip 이 없다는 오류가 나면 배포판 패키지를 먼저 설치한다
sudo apt install python3-venv     # 또는 python3.12-venv 등 버전별 패키지

python3 -m venv .venv-dev
.venv-dev/bin/pip install --upgrade pip
.venv-dev/bin/pip install "fastapi>=0.115" "uvicorn[standard]" "pydantic>=2.9" \
    ruamel.yaml httpx pytest
```

Python 3.13 이상에서는 `pydantic-core` 휠이 아직 없을 수 있다. 그 경우 uv 로
3.12 를 쓰는 편이 빠르다.
</details>

### 2.3 무엇을 눌러볼 수 있는가

`config/` 를 임시 디렉터리로 복사해 쓰므로 **리포의 설정 파일은 바뀌지 않는다.**
서버를 끄면 임시 디렉터리도 사라진다. 남기고 싶으면 `--keep` 을 붙인다.

| 화면 | 확인할 것 |
|---|---|
| 노드 목록 | 세 서브시스템. `/perception` 은 일부러 꺼져 있어 오프라인 표시를 볼 수 있다. 데모는 `src/av_config/test/fixtures/config/` 를 쓴다 — 실제 `config/` 가 아니다 |
| 파라미터 편집 | `lidar_mode` 가 drift 상태로 시작한다 (저장 `1024x10` vs 런타임 `512x10`) |
| 타입별 입력 | ROS 2 파라미터 9종 전부 (bool, int, double, string, 배열 4종, byte 배열) |
| 변경 검토 | 재시작 대상 unit 과 **그 unit 의 모든 노드**가 중단 범위로 표시된다 |
| 전역 설정 경고 | `sensor_frame` 을 바꾸면 전역 파일을 건드린다는 경고가 뜬다 |
| 적용 진행 | 검증 → 계획 → 저장 → 적용 → 반영 확인 단계 표시 |
| 이력 | 적용마다 git commit 이 쌓인다. 코멘트와 사용자가 남는다 |
| 딥링크 | `/history` 를 직접 열거나 새로고침해도 뜬다 |
| 온보딩 (`--new-sensor`) | 미등록 노드를 열면 등록 절차와 설정 파일 초안 3종이 나온다 |

적용은 실제로 YAML 을 쓰고 git commit 을 만든다. 적용 후 노드 목록으로 돌아가면
drift 가 해소된 것이 보인다.

### 2.4 터미널에서 API 만 두드려보기

```bash
B=http://127.0.0.1:8080/api/v1

# 노드 목록
curl -s $B/nodes | python3 -m json.tool

# 파라미터 (노드 이름이 경로 마지막에 온다)
curl -s $B/params/ouster/os_driver | python3 -m json.tool

# 검증만 — 아무것도 바뀌지 않는다
curl -s -X POST $B/changesets -H 'Content-Type: application/json' -d '{
  "dry_run": true,
  "changes": [{"node":"/ouster/os_driver","param":"lidar_mode","value":"2048x10"}]
}' | python3 -m json.tool

# 실제 적용
curl -s -X POST $B/changesets -H 'Content-Type: application/json' \
  -H 'X-AV-User: engineer1' -d '{
  "comment": "해상도 상향 테스트",
  "changes": [{"node":"/ouster/os_driver","param":"lidar_mode","value":"2048x10"}]
}' | python3 -m json.tool

# 이력
curl -s $B/history | python3 -m json.tool
```

거부되는 경로도 눌러보면 이해가 빠르다.

```bash
# enum 밖의 값 -> not_in_enum
curl -s -X POST $B/changesets -H 'Content-Type: application/json' -d '{
  "changes": [{"node":"/ouster/os_driver","param":"lidar_mode","value":"9999x99"}]
}' | python3 -m json.tool

# operator 가 engineer 등급을 건드림 -> forbidden_tier
curl -s -X POST $B/changesets -H 'Content-Type: application/json' \
  -H 'X-AV-Role: operator' -d '{
  "changes": [{"node":"/ouster/os_driver","param":"lidar_mode","value":"512x10"}]
}' | python3 -m json.tool
```

> **이 개발 서버는 컨테이너 검증을 대체하지 않는다.** 실제 센서 반영, 실제
> `systemctl restart`, 실제 파라미터 서비스는 확인되지 않는다. 8장을 참고하라.

---

## 3. 디렉터리 구조

```
config/                    # /opt/av_config/ 로 배포될 설정 원본
├── services.yaml           #   노드 <-> systemd 유닛 <-> parameter file 매핑
├── params/                 #   parameter file. 배포 시 여기가 git 저장소다
│   ├── global.yaml         #     시스템 전역 설정 (frame, extrinsic)
│   └── lidar_top.yaml      #     등록된 서브시스템만 둔다
├── schemas/                #   드라이버별 파라미터 스키마 (자동 생성물)
├── overlays/               #   스키마 생성 입력. ROS 가 알려줄 수 없는 정보만 담는다
└── rules/                  #   안전 게이트, 점검 규칙 설정
deploy/
├── av-config.service       # Config App 유닛 (자원 제한 포함)
├── av-lidar-top.service    # lidar_top 드라이버 유닛 — 실차(24.04 네이티브) 기준
└── dev-host/               #   개발 호스트(26.04) 전용 배선. README 참고
docker/Dockerfile          # 개발 호스트용 이미지 (jazzy + ouster_ros 0.13.15 소스 빌드)
docs/
├── architecture.md         # 동작 구조 다이어그램
├── web-configure-app-spec.md
└── sensor-baseline-*.md    # 실장치 기준값과 펌웨어 제약
frontend/                  # Vite + React + TS — 호스트에서 빌드
└── scripts/smoke.mjs       #   실제 Chrome 으로 렌더링 확인
src/
├── av_config/             # ament_python 백엔드 — 컨테이너에서 빌드
│   ├── av_config/          #   모듈
│   └── test/               #   pytest (대부분 rclpy 없이 돈다)
│       └── fixtures/config/ #     서브시스템 3개짜리 테스트·mock 설정
└── av_bringup/            # 서브시스템 launch. 파라미터는 파일로만 넘긴다
    └── launch/
tools/
├── mock_backend.py         # docker 없이 UI 를 띄우는 개발 서버
└── import_driver_params.py # 드라이버 parameter file -> 스키마 초안
ruff.toml                  # 백엔드 린트 규칙
```

프론트엔드 빌드 산출물은 `src/av_config/av_config/static/` 으로 떨어지고 Config App
이 직접 서빙한다. **별도 웹 서버가 없으므로 프로덕션 프로세스는 하나다.**

---

## 4. 신규 센서가 장착되면

센서 드라이버 노드가 뜨면 **조회는 자동**이다. 코드도 설정도 건드릴 필요가 없다.

| 자동 | 근거 |
|---|---|
| 노드 목록에 나타남 | DDS discovery |
| 파라미터 종류가 드라이버 기준으로 수집됨 | `list_parameters` |
| 타입, 범위, 설명, `read_only` | `describe_parameters` |
| 타입 검증과 입력 위젯 선택 | 수집한 타입 그대로 |
| 드라이버를 업그레이드해 파라미터가 늘거나 줄어도 따라감 | 시작 시·그래프 변경 시·새로고침 시 재수집 |

**변경은 등록 후에만 가능하다.** `services.yaml` 에 없으면 저장할 parameter file 이
없어 읽기 전용이 된다. 어느 systemd 유닛이 그 노드를 띄웠는지는 ROS 그래프로 알 수
없기 때문이다 (명세 §11.2).

그래서 미등록 노드를 열면 **등록 절차(명세 §12.2 8단계)와 설정 파일 초안 3종**이
화면에 나온다.

| 초안 | 내용 | 자동으로 채워지는 것 |
|---|---|---|
| `services.yaml` 조각 | 서브시스템, unit, param_files, nodes, topics | 노드 이름에서 추론한 이름, 발행 토픽 목록 |
| `params/<이름>.yaml` | 현재 런타임 값 | 수집한 값 전부 (`read_only` 는 제외) |
| `schemas/<드라이버>.yaml` | 파라미터별 스키마 | 이름, 타입, 범위, 설명, 매칭 정규식, 드라이버 서비스 |

**계층(`layer`), 적용 방식(`apply`), 등급(`tier`) 은 비워 둔다.** ROS 가 알려줄 수
없기 때문이다 — 어떤 파라미터가 장치 펌웨어에 쓰이는지, 실행 중 바꿀 수 있는지는
드라이버 문서와 장치 명세를 봐야 한다. 그 자리에 `TODO` 가 들어가고 화면이 개수를
센다.

초안은 복사하거나 파일로 내려받을 수 있다. 생성된 YAML 이 실제 로더를 통과하는지는
테스트로 확인한다(`test_onboarding.py`) — "붙여 넣으면 된다"가 거짓말이 되지 않도록.

### 드라이버 문서에서 가져오기 (노드 없이)

상당수 드라이버는 `declare_parameter` 를 호출하면서 `ParameterDescriptor` 를 채우지
않는다. 그러면 자동 수집으로는 이름과 타입만 얻고 **설명·범위·허용 값이 비어 있다.**
그 정보는 드라이버가 배포하는 `config/*.yaml` 의 **주석**에 있다.

실측 — [ouster-ros](https://github.com/ouster-lidar/ouster-ros) (ros2 브랜치, 2026-09-24):

| | |
|---|---|
| `declare_parameter` 호출 | 99 |
| `ParameterDescriptor` 사용 | **0** |
| `add_on_set_parameters_callback` | **0** (실행 중 변경 불가 → 전부 restart) |
| `driver_params.yaml` 파라미터 | 57 |
| 주석에서 뽑아낸 허용 값 목록 | 23 |

```bash
python tools/import_driver_params.py \
  ~/ros2_ws/src/ouster-ros/ouster-ros/config/driver_params.yaml \
  --overlay config/overlays/ouster_ros.yaml \
  --out config/schemas/ouster_ros.yaml
```

입력이 둘이다.

| 입력 | 채우는 것 |
|---|---|
| 드라이버의 parameter file | 이름, 기본값, 타입, 설명, 허용 값 |
| `config/overlays/<driver>.yaml` | 계층, 적용 방식, 등급, `readback_via`, `global_ref` |

오버레이는 ROS 가 알려줄 수 없는 것만 담는다. **드라이버가 업데이트되면 같은 명령으로
다시 생성**하므로 손으로 채운 내용이 사라지지 않는다. `config/schemas/` 아래 파일은
자동 생성물이니 직접 고치지 말고 오버레이를 고친다.

---

## 5. 개발 환경

프론트엔드는 **호스트**에서, 백엔드는 **컨테이너**에서 다룬다. 이유가 분명하다.

- `osrf/ros:jazzy-desktop` 에는 Node.js 가 없다
- 호스트에는 rclpy 가 없다
- 호스트 Python 과 컨테이너 Python 버전이 달라 venv 를 공유할 수 없다

그래서 `node_modules` 는 호스트 전용, `.venv` 는 컨테이너 전용이다. 둘 다
`.gitignore` 에 있다.

### 5.1 프론트엔드 (호스트)

```bash
cd frontend
npm install          # 처음 한 번
npm run dev          # http://localhost:5173, /api 는 127.0.0.1:8080 으로 프록시
npm run build        # ../src/av_config/av_config/static/ 으로 출력
npm test             # vitest
npm run typecheck    # tsc 만 돌린다
```

`npm run dev` 는 백엔드가 `127.0.0.1:8080` 에 떠 있다고 가정한다. 2장의 개발 서버나
컨테이너의 Config App 중 아무거나 띄워 두면 된다. 컨테이너를 `--net=host` 로 띄웠다면
포트 매핑 없이 그대로 닿는다.

### 5.2 백엔드 (컨테이너)

컨테이너는 워크스페이스를 마운트해 띄운다. 이미 띄워 뒀다면 이 단계는 넘긴다.

```bash
docker run -it --name ros2-jazzy --net=host --ipc=host \
  -v ~/ros2_ws:/root/ros2_ws osrf/ros:jazzy-desktop bash
```

이미 만들어 둔 컨테이너에 다시 들어갈 때:

```bash
docker start ros2-jazzy
docker exec -it ros2-jazzy bash
```

**이하는 컨테이너 안에서 실행한다.**

```bash
# (1) 의존성. Ubuntu 24.04 는 시스템 pip 설치를 막으므로(PEP 668) venv 를 쓴다.
#     apt 의 python3-fastapi 는 Pydantic v1 에 묶이므로 쓰지 않는다 (명세 §7.1).
mkdir -p /opt/av_config
python3 -m venv --system-site-packages /opt/av_config/.venv
/opt/av_config/.venv/bin/pip install \
  "fastapi>=0.115" "uvicorn[standard]" "pydantic>=2.9" ruamel.yaml

# (2) 빌드
source /opt/ros/jazzy/setup.bash
cd /root/ros2_ws
colcon build --symlink-install
source install/setup.bash

# (3) 설정 루트의 git 저장소 초기화 (이력과 롤백이 여기에 의존한다)
cd /root/ros2_ws/config/params
git init -q
git config user.name avconfig
git config user.email avconfig@localhost
git add --all && git commit -q -m "initial: 기준 설정"
cd /root/ros2_ws

# (4) 실행
AV_CONFIG_ROOT=/root/ros2_ws/config \
AV_CONFIG_HOST=127.0.0.1 \
AV_CONFIG_PORT=8080 \
  /opt/av_config/.venv/bin/python -m av_config.main
```

`--symlink-install` 을 쓰면 파이썬 파일을 고칠 때 다시 빌드하지 않아도 된다.
프로세스만 재시작하면 반영된다.

> **파일 소유권 주의.** 컨테이너가 root 로 돌기 때문에 `colcon build` 산출물이
> 호스트에서 root 소유가 된다. 거슬리면 `docker exec -u $(id -u):$(id -g)` 로
> 빌드하라.

### 5.3 환경 변수

| 변수 | 기본값 | 용도 |
|---|---|---|
| `AV_CONFIG_ROOT` | `/opt/av_config` | 설정 루트. `services.yaml`, `params/`, `schemas/`, `rules/` 가 있는 곳 |
| `AV_CONFIG_HOST` | `127.0.0.1` | 바인딩 주소. **외부 노출 금지** (명세 §10.3) |
| `AV_CONFIG_PORT` | `8080` | 포트 |
| `AV_CONFIG_LOG_LEVEL` | `INFO` | `DEBUG` 로 올리면 서비스 호출까지 보인다 |
| `AV_CONFIG_SYSTEMCTL_PREFIX` | (없음) | `systemctl` 앞에 붙일 명령. polkit 을 쓰면 비워 둔다 |

노트북에서 접속해야 하면 `AV_CONFIG_HOST` 를 차량 내부 NIC 주소로 지정한다.
`0.0.0.0` 은 경고가 뜨고, 인터넷 노출은 금지다.

### 5.4 실센서 붙이기

실제 Ouster 센서를 붙여 `/ouster/os_driver` 까지 띄우는 배선은
[`deploy/dev-host/README.md`](deploy/dev-host/README.md) 에 있다. 이미지 빌드,
워크스페이스 빌드, 드라이버 유닛 설치, Config App 기동이 그 순서로 적혀 있다.

센서 기준값과 펌웨어 제약은
[`docs/sensor-baseline-992134000971.md`](docs/sensor-baseline-992134000971.md) 를
본다. **현재 센서 FW v2.1.2 는 ouster-sdk 1.0.1 과 통신하지 못한다.** 드라이버를
띄우기 전에 펌웨어 업그레이드가 선행 조건이다.

---

## 6. 테스트

### 6.1 백엔드 — 호스트에서 (rclpy 불필요)

248개 테스트가 ROS 2 없이 돈다. 오케스트레이터(`changesets.py`)와 라우터(`api.py`)가
ROS 의존 모듈을 `TYPE_CHECKING` 으로만 참조하기 때문이다.

2장에서 `.venv-dev` 를 만들었다면 그대로 쓴다. 처음부터 한다면:

```bash
cd ~/ros2_ws
uv venv --python 3.12 .venv-dev
uv pip install --python .venv-dev/bin/python \
  "pydantic>=2.9" ruamel.yaml "fastapi>=0.115" httpx pytest

.venv-dev/bin/python -m pytest src/av_config/test/ -q
```

마지막 줄은 `248 passed` 를 내야 한다.

파일별로 돌리고 싶을 때:

```bash
V=.venv-dev/bin/python
$V -m pytest src/av_config/test/test_store_and_schema.py -q   # 저장값 머지, 스키마 검증, drift
$V -m pytest src/av_config/test/test_write_and_git.py -q      # atomic write, 주석 보존, git revert
$V -m pytest src/av_config/test/test_validate_and_plan.py -q  # 검증 규칙, unit 단위 계획
$V -m pytest src/av_config/test/test_changesets.py -q         # 트랜잭션과 롤백
$V -m pytest src/av_config/test/test_api.py -q                # REST 라우팅, SPA 서빙, 상태 코드
$V -m pytest src/av_config/test/test_onboarding.py -q         # 설정 파일 초안 생성
$V -m pytest src/av_config/test/test_paramdoc.py -q           # 드라이버 문서 파싱 (실물 픽스처)

# 하나만, 출력까지 보기
$V -m pytest src/av_config/test/test_changesets.py -k rollback -v
```

**돌지 않는 것**: `discovery.py`, `applier.py`, `verifier.py`, `main.py` 는 rclpy 가
필요해 컨테이너에서 확인해야 한다.

### 6.2 프론트엔드 — 호스트에서

```bash
cd frontend
npm test                                  # 55개 (jsdom 컴포넌트 테스트)
npm test -- ValueEditor                   # 파일 이름으로 좁히기
npm run test:watch                        # 파일 변경 감지
```

### 6.3 실제 브라우저 스모크 테스트

빌드된 번들을 **시스템에 설치된 Chrome** 으로 띄워 확인한다. 브라우저를 따로
내려받지 않는다 (`puppeteer-core` 만 쓴다).

2장의 개발 서버나 컨테이너의 Config App 을 먼저 띄워 둔다.

```bash
cd frontend
npm run smoke                                       # 기본 http://127.0.0.1:8080
node scripts/smoke.mjs http://127.0.0.1:8080        # 주소 지정
node scripts/smoke.mjs --screenshot /tmp/ui.png     # 스크린샷까지
```

문서의 다이어그램도 같은 방식으로 검사한다. 문법이 깨진 mermaid 는 뷰어에서
조용히 빈 칸으로 나오므로 눈으로는 모른다.

```bash
npm run check:docs
```

확인하는 것: 노드 카드 렌더링, 오프라인 노드 표시, 스키마 매핑 표시, 파라미터
타입 9종의 입력 요소, 적용 방식·등급 배지, 값 변경 시 변경 검토 트레이, 딥링크
직접 요청. 콘솔 오류와 실패한 요청이 있으면 종료 코드가 1 이 된다.

Chrome 경로를 못 찾으면 `CHROME_PATH=/경로/chrome npm run smoke` 로 지정한다.

### 6.4 백엔드 — 컨테이너에서

```bash
source /opt/ros/jazzy/setup.bash
source install/setup.bash
colcon test --packages-select av_config
colcon test-result --verbose
```

### 6.5 린트

규칙은 `ruff.toml` 에 있다. 스타일 취향보다 버그에 가까운 것만 고르고, 현재
**경고 0건** 이다.

```bash
uv pip install --python .venv-dev/bin/python ruff
.venv-dev/bin/python -m ruff check .          # All checks passed! 가 나와야 한다
.venv-dev/bin/python -m ruff check . --fix    # 자동 수정
```

---

## 7. 자주 겪는 문제

**`docker: permission denied while trying to connect to the docker API`**
사용자가 `docker` 그룹에 없다. `sudo usermod -aG docker $USER` 후 재로그인하면
`sudo` 없이 쓸 수 있다. 당장은 `sudo docker ...` 로 쓴다.

**`ensurepip is not available` 또는 `python3-venv` 설치 안내**
배포판이 venv 모듈을 따로 패키징한 경우다. `uv` 를 쓰면 sudo 없이 해결된다
(2.1절). 또는 `sudo apt install python3-venv`.

**`Chrome 을 찾을 수 없다` (npm run smoke)**
`CHROME_PATH` 로 경로를 지정한다. 예:
`CHROME_PATH=/opt/google/chrome/chrome npm run smoke`

**`error: externally-managed-environment` (pip)**
Ubuntu 24.04 의 PEP 668 이다. 5.2절처럼 venv 를 쓴다. `--break-system-packages` 는
쓰지 않는다.

**`ModuleNotFoundError: No module named 'rclpy'`**
호스트에서 백엔드를 돌리려 한 것이다. 백엔드는 컨테이너에서만 돈다. UI 만 보려면
2장의 개발 서버를 쓴다.

**`ModuleNotFoundError: No module named 'fastapi'`** (컨테이너)
venv 의 파이썬이 아니라 시스템 파이썬으로 실행한 것이다. `python -m av_config.main`
이 아니라 `/opt/av_config/.venv/bin/python -m av_config.main` 으로 실행한다.

**UI 가 `정적 파일 디렉터리 없음` 경고와 함께 빈 화면**
프론트엔드를 빌드하지 않았다. `cd frontend && npm run build`.

**이력 화면이 비어 있고 적용이 실패한다**
`config/params/` 가 git 저장소가 아니다. 5.2절 (3)단계를 실행한다. 저장소가 없으면
Config App 이 시작할 때 오류 로그를 남긴다.

**노드 목록이 비어 있다**
Config App 과 드라이버 노드의 `ROS_DOMAIN_ID` 와 RMW 설정이 같아야 서로 보인다.
`ros2 node list` 로 먼저 확인한다.

**파라미터가 `원본` 배지로만 나온다**
스키마가 매칭되지 않았다. `config/schemas/*.yaml` 의 `node_name_regex` 와
`required_services` 를 실제 노드 이름 및 `ros2 service list` 결과와 맞춘다.
스키마 로드 실패는 노드 목록 화면 위쪽에 사유가 표시된다.

---

## 8. 남은 검증 (컨테이너 필요)

```bash
# 1. 빌드가 통과하는지
colcon build --packages-select av_config

# 2. 기동과 응답
AV_CONFIG_ROOT=/root/ros2_ws/config /opt/av_config/.venv/bin/python -m av_config.main &
curl -s http://127.0.0.1:8080/api/v1/nodes

# 3. 임의 노드를 상대로 수집이 되는지
ros2 run demo_nodes_cpp talker &
curl -s http://127.0.0.1:8080/api/v1/params/talker

# 4. Ouster 서비스가 실제로 있는지 — 명세 §13.2 미결 사항
ros2 service list | grep ouster
```

4번이 중요하다. `/ouster/set_config` 가 없으면 `lidar_mode` 를 `apply: reconfigure`
로 둘 수 없고 `restart` 로 내려야 한다. 그러면 명세 §4.2 의 세 적용 방식 중 하나가
비게 된다.

---

## 9. 검증 상태

### 실제로 확인된 것

| 항목 | 방법 |
|---|---|
| 저장값 머지 순서 (global 이 노드별 값을 덮는지) | 단위 테스트 |
| 주석·키 순서 보존 round-trip | 단위 테스트 (ruamel) |
| 정확한 노드 키에만 쓰기 (`/**` 오염 방지) | 단위 테스트 |
| `layer: device` 의 `readback_via` 필수 강제 | 단위 테스트 |
| 타입 검증 18케이스, drift 비교 18케이스 | 단위 테스트 |
| unit 단위 재시작 집합, 전역 fan-out 선별 | 단위 테스트 + 개발 서버 |
| git revert/restore 로 이력이 보존되는지 | 단위 테스트 |
| 롤백 4경로 (read-back 불일치, 적용 실패, 0 Hz, 롤백 실패) | 단위 테스트 |
| 동시 change set 409 거부 | 단위 테스트 |
| ROS 2 파라미터 9종 렌더링 | 컴포넌트 테스트 |
| REST 라우팅, 상태 코드, 권한 거부 | TestClient + 개발 서버 curl |
| 적용 전체 흐름 (검증→저장→commit→적용→확인) | 개발 서버에서 end-to-end |
| SPA 딥링크 새로고침, 경로 탈출 차단 | TestClient + curl |
| **실제 Chrome 렌더링** — 노드 카드, 타입 9종 입력, 배지, 변경 검토 트레이 | `npm run smoke` |
| 생성한 설정 초안이 실제 로더를 통과하는지 | 단위 테스트 (ServicesFile / ParamStore / DriverSchema) |
| 드라이버 문서 파싱 (중괄호형·불릿형 enum, 줄바꿈된 여는 말) | ouster-ros 실물 발췌 픽스처로 테스트 |
| ouster 스키마가 드라이버 소스의 사실과 맞는지 | 단위 테스트 (전부 restart, device 27개 readback 보유) |
| 온보딩 화면 렌더링, 절차 표시, 초안 3종 | `npm run smoke --new-sensor` |
| 이 README 의 모든 호스트측 명령 | 그대로 실행해 확인 |

### 확인되지 않은 것

- `colcon build` 통과 여부
- `main.py` 기동, rclpy executor 스레드와 uvicorn 공존
- `discovery` 의 실제 파라미터 서비스 호출
- `applier` 의 `set_parameters`, 드라이버 서비스, `systemctl restart`
- `verifier` 의 장치 read-back, 토픽 Hz 실측, `/diagnostics`
- 실제 센서 반영

---

## 10. 알려진 제약

- **인증이 없다.** 역할과 사용자는 `X-AV-Role` / `X-AV-User` 헤더로 받고, 없으면
  `engineer` 로 간주한다. M3 에서 교체해야 한다. **그때까지 외부에 노출하면 안 된다.**
- **안전 게이트가 차량 상태를 모른다.** `config/rules/safety.yaml` 의 `mode: open` 은
  개발 전용이다. 차량 탑재 전에 `enforce` 로 바꾸고 차량 상태 토픽을 연결해야 한다.
  `enforce` 에서 상태를 모르면 모든 잠긴 파라미터의 적용이 거부된다 (fail-safe).
- **launch 규칙 위반을 감지하지 못한다.** `services.yaml` 이 launch 명령 문자열만
  갖고 있어 정적 검사를 못 한다 (명세 §12.1-8). launch 에 인라인 `parameters=[{...}]`
  를 쓰면 Config App 이 해소할 수 없는 drift 가 생긴다.
- **점검 규칙(명세 9장)이 미구현이다.** `config/rules/health.yaml` 은 설정만 있다.
- **프로파일(명세 §7.3)이 미구현이다.** M5 범위다.
- **설정 파일형 적용 서비스를 다룰 수 없다.** ouster-ros 의 `set_config` 는 값이 아니라
  설정 파일 경로를 받아 센서 전체를 재설정한다. 현재 `apply_via` 모델은 값 하나를
  요청 필드에 담는 형태만 지원하므로, 해당 파라미터는 `restart` 로 분류했다 (명세 §4.2).
- **`config/` 에는 `lidar_top` 하나만 등록돼 있다.** 실물이 붙은 것이 OS-2-128
  하나뿐이다. 장착되지 않은 센서를 등록해두면 UI 에 영구 오프라인 카드로 남고,
  전역 변경이 존재하지 않는 unit 을 재시작 대상에 넣는다. 장치가 붙으면 온보딩
  화면(§12.2)이 `services.yaml` 조각을 만들어 준다.
  unit 여러 개나 오프라인 노드가 필요한 검증은
  `src/av_config/test/fixtures/config/` 의 서브시스템 3개짜리 설정을 쓴다.
