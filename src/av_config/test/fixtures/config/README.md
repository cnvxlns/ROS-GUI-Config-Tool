# 테스트·mock 용 설정 픽스처

서브시스템 3개(`lidar_top`, `camera_front`, `perception`)를 가진 설정이다.
**실제 차량 설정이 아니다.** 실제 설정은 리포 루트의 `config/` 에 있고, 거기에는
장착된 센서만 등록한다.

이 픽스처가 따로 있는 이유: 아래 검증들이 unit 여러 개와 오프라인 노드를 필요로
한다. 실제 설정에 쓰지 않는 센서를 남겨두는 대신 픽스처로 옮겼다.

| 검증 | 필요한 조건 |
|---|---|
| 전역 변경의 unit fan-out 순서 (`test_validate_and_plan.py`) | unit 2개 이상 |
| 꺼진 노드를 재시작 집합에 보수적으로 포함 (`test_changesets.py`) | 오프라인 노드 |
| 노드 간 쓰기 격리 (`test_write_and_git.py`) | 다른 서브시스템의 노드 |
| 스키마가 엉뚱한 노드에 붙지 않음 (`test_store_and_schema.py`) | ouster 가 아닌 노드 |
| 오프라인 편집 화면 (`frontend/scripts/smoke.mjs`) | 꺼진 노드 |
| UI 데모 (`tools/mock_backend.py`) | 서브시스템 3개 |

`params/` 는 git 저장소로 만들지 않는다. 복사해 쓰는 쪽에서 `git init` 한다.
