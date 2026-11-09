# 동작 구조

구현된 것을 기준으로 그렸다. 설계 의도는 [web-configure-app-spec.md](web-configure-app-spec.md) 를 본다.

## 1. 전체 구성

```mermaid
flowchart TB
    B["브라우저<br/>Vite + React SPA"]

    subgraph HOST["호스트 — IPC / Jetson"]
        subgraph CA["Config App : ROS 2 노드 하나"]
            API["api.py<br/>REST · WebSocket · 정적 파일"]
            DISC["discovery.py<br/>노드·파라미터 수집"]
            CS["changesets.py<br/>적용 트랜잭션"]
            STORE["store.py<br/>YAML 읽기·쓰기·머지"]
            GIT["gitparams.py<br/>이력·롤백"]
            APPL["applier.py<br/>적용"]
            VER["verifier.py<br/>반영 확인"]
        end

        PF[("params/*.yaml<br/>git 저장소")]
        SC[("schemas/*.yaml")]
        SD["systemd"]
        DRV["드라이버 노드<br/>예: /ouster/os_driver"]
    end

    SENSOR["센서 장치"]

    B -->|HTTP · WS| API
    API --> DISC
    API --> CS
    CS --> STORE
    CS --> GIT
    CS --> APPL
    CS --> VER
    STORE <--> PF
    GIT --> PF
    DISC --> SC
    DISC -->|"DDS : list · describe · get_parameters"| DRV
    APPL -->|"set_parameters · 드라이버 서비스"| DRV
    APPL -->|"systemctl restart"| SD
    SD -->|기동| DRV
    VER -->|"get_parameters · get_config"| DRV
    PF -->|"--params-file : 기동 시 1회"| DRV
    DRV -->|"벤더 API"| SENSOR

    classDef done fill:#dcfce7,stroke:#15803d,color:#14532d
    classDef untested fill:#fef3c7,stroke:#b45309,color:#78350f
    class API,CS,STORE,GIT done
    class DISC,APPL,VER untested
```

초록은 호스트에서 테스트까지 끝낸 모듈, 노랑은 rclpy 가 필요해 **아직 실환경에서
돌려보지 못한** 모듈이다.

설정은 위에서 아래로만 흐른다. Config App 은 YAML 을 쓰고 드라이버에 적용을
요청하며, 센서 장치에 쓰는 것은 드라이버뿐이다.

## 2. 적용 트랜잭션

적용 버튼 하나가 검증 → 저장 → 적용 → 확인 → (실패 시) 롤백을 한 번에 수행한다.

```mermaid
flowchart TD
    START(["적용"]) --> V["1 · 검증<br/>타입 · 범위 · enum · 권한 · 안전 게이트<br/>외부 수정 감지"]
    V -->|하나라도 실패| REJ(["거부 — 아무것도 바뀌지 않음"])
    V -->|통과| P["2 · 계획<br/>unit 단위 restart 집합<br/>전역 파일 fan-out"]
    P --> DRY{"dry_run ?"}
    DRY -->|예| PLAN(["계획만 반환"])
    DRY -->|아니오| W["3 · 저장<br/>임시 파일 → rename"]
    W --> C{"4 · git commit"}
    C -->|실패| UNDO(["파일 되돌리고 거부<br/>되돌릴 기준점이 없기 때문"])
    C -->|성공| A["5 · 적용<br/>runtime → reconfigure → restart"]
    A -->|실패| RB["7 · git revert"]
    A -->|성공| VF["6 · 반영 확인<br/>계층별 read-back · 토픽 Hz · diagnostics"]
    VF -->|전부 통과| OK(["완료"])
    VF -->|하나라도 실패| RB
    RB --> RE["되돌린 YAML 값으로 재적용"]
    RE -->|성공| ROLLED(["롤백 완료"])
    RE -->|실패| DANGER(["위험 상태 — 수동 조치 필요"])

    classDef ok fill:#dcfce7,stroke:#15803d,color:#14532d
    classDef bad fill:#fee2e2,stroke:#b91c1c,color:#7f1d1d
    classDef warn fill:#fef3c7,stroke:#b45309,color:#78350f
    class OK,PLAN ok
    class DANGER bad
    class REJ,UNDO,ROLLED warn
```

롤백은 `reset` 이 아니라 **revert commit** 을 쌓는다. 실패한 시도도 이력에 남아야
감사 로그가 성립한다. 재적용에 쓰는 값은 요청값이 아니라 **되돌린 YAML 의 값**이다.
요청값을 다시 쓰면 YAML 은 되돌아갔는데 장치에는 새 값이 남는다.

## 3. 저장값은 어떻게 정해지고 drift 는 무엇인가

```mermaid
flowchart LR
    subgraph ORDER["services.yaml 의 param_files 순서"]
        direction TB
        F1["params/lidar_top.yaml"]
        F2["params/global.yaml"]
    end

    F1 -->|먼저| M["머지<br/>뒤 파일이 앞을 덮는다"]
    F2 -->|나중| M
    M --> SV["저장값"]

    DRV["드라이버 노드"] -->|get_parameters| RV["런타임값"]

    SV --> CMP{"같은가?"}
    RV --> CMP
    CMP -->|다르다| D["drift 경고"]
    CMP -->|같다| N["정상"]
    CMP -.->|"노드가 꺼져 있으면<br/>비교 대상이 없다"| U["판정하지 않음"]
```

저장값은 **단일 파일의 값이 아니다.** `global.yaml` 이 뒤에 오므로 전역 값이
노드별 값을 덮는다. 쓸 때도 같은 규칙을 따른다 — 그 파라미터가 **이기고 있는
파일**에 써야 실제로 반영된다.

## 4. restart 영향 범위는 unit 단위다

```mermaid
flowchart TB
    CH["lidar_mode 변경<br/>apply: restart"] --> U["av-lidar-top.service"]
    U --> N1["/ouster/os_driver"]
    U --> N2["같은 unit 의 다른 노드"]

    GL["sensor_frame 변경<br/>global.yaml 에 기록"] --> SHARE{"그 파일을 쓰는<br/>다른 unit 이 있나?"}
    SHARE -->|"그 파라미터를 선언함"| U2["av-camera-front.service"]
    SHARE -->|"노드가 꺼져 있어 모름"| U3["av-perception.service<br/>보수적으로 포함"]
    SHARE -->|"선언하지 않음"| SKIP["제외"]

    classDef warn fill:#fef3c7,stroke:#b45309,color:#78350f
    class U,U2,U3 warn
```

재시작 수단이 `systemctl restart <unit>` 이고 unit 하나가 노드 여러 개를 띄우므로,
노드 단위로 묶으면 실제 중단 범위를 과소평가한다. 전역 파일을 고치면 그 파일을
쓰는 unit 들로 번지되, 그 파라미터를 **실제로 선언한** 노드가 있는 unit 만 고른다.

## 5. 스키마는 어디서 오는가

```mermaid
flowchart LR
    DP["드라이버 패키지<br/>config/driver_params.yaml"]
    RT["실행 중 노드<br/>describe_parameters"]
    OV["config/overlays/*.yaml<br/>사람이 작성"]

    DP -->|"주석 파싱 · paramdoc.py"| D1["이름 · 기본값 · 타입<br/>설명 · 허용 값"]
    RT -->|"드라이버가 채웠다면"| D2["타입 · 범위 · read_only"]
    OV --> D3["계층 · 적용 방식 · 등급<br/>readback_via · global_ref"]

    D1 --> GEN["import_driver_params.py"]
    D3 --> GEN
    GEN --> SCH["config/schemas/*.yaml<br/>자동 생성물"]

    D2 --> DTO["paramdto.py<br/>DTO 조립"]
    SCH --> DTO
    DTO --> UI["UI : 설명 · 허용 값 · 배지"]

    classDef gap fill:#fee2e2,stroke:#b91c1c,color:#7f1d1d
    class RT gap
```

`describe_parameters` 가 빨간 이유는 **상당수 드라이버가 `ParameterDescriptor` 를
채우지 않기 때문**이다. ouster-ros 는 `declare_parameter` 를 99회 호출하면서
기술자를 0회 쓴다. 그래서 설명과 허용 값은 드라이버가 배포하는 parameter file 의
주석에서만 얻을 수 있다.

## 6. 신규 센서가 장착되면

```mermaid
flowchart TD
    NEW["드라이버 노드 기동"] --> AUTO["자동 수집<br/>이름 · 타입 · 현재값"]
    AUTO --> LIST["노드 목록에 나타남"]
    LIST --> REG{"services.yaml 에<br/>등록돼 있나?"}

    REG -->|아니오| RO["읽기 전용<br/>저장할 parameter file 이 없다"]
    RO --> OB["온보딩 화면<br/>절차 8단계 + 초안 3종"]
    OB --> HUMAN["사람이 TODO 를 채운다<br/>계층 · 적용 방식 · 등급"]
    HUMAN --> REG

    REG -->|예| SCHEMA{"스키마가 있나?"}
    SCHEMA -->|아니오| RAW["원본 모드<br/>전부 restart · engineer 로 간주<br/>device 계층 검증 불가"]
    RAW --> OB
    SCHEMA -->|예| MAPPED["매핑 모드<br/>설명 · 허용 값 · 적용 방식 표시"]

    classDef warn fill:#fef3c7,stroke:#b45309,color:#78350f
    classDef ok fill:#dcfce7,stroke:#15803d,color:#14532d
    class RO,RAW warn
    class MAPPED ok
```

조회는 등록 없이도 자동이다. **변경만 등록이 필요하다.** 어느 systemd 유닛이 그
노드를 띄웠는지는 ROS 그래프로 알 수 없기 때문이다.

## 7. ROS 의존을 분리한 이유

```mermaid
flowchart TB
    subgraph NOROS["rclpy 불필요 — 호스트에서 248개 테스트"]
        direction LR
        M1["models · store · schema"]
        M2["validator · planner"]
        M3["gitparams · services_map"]
        M4["onboarding · paramdoc · paramdto"]
        M5["changesets · api"]
    end

    subgraph ROS["rclpy 필요 — 컨테이너에서만"]
        direction LR
        R1["discovery"]
        R2["applier · verifier"]
        R3["rosutil · main"]
    end

    M5 -.->|"TYPE_CHECKING<br/>덕 타이핑으로 호출"| R1
    M5 -.-> R2

    classDef ok fill:#dcfce7,stroke:#15803d,color:#14532d
    classDef untested fill:#fef3c7,stroke:#b45309,color:#78350f
    class M1,M2,M3,M4,M5 ok
    class R1,R2,R3 untested
```

오케스트레이터(`changesets.py`)와 라우터(`api.py`)가 ROS 의존 모듈을
`TYPE_CHECKING` 으로만 참조한다. 덕분에 **트랜잭션 로직과 REST 표면 전체를
컨테이너 없이 테스트**할 수 있다. 이 분리가 없었다면 롤백 경로를 한 번도 검증하지
못했을 것이다.
