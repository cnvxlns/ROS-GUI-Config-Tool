"""Config App 엔트리포인트 (명세 §3, §7.1).

ROS 2 노드 하나가 웹 프론트엔드 서빙과 백엔드 API, ROS 2 클라이언트를 겸한다.

구현 규칙 (§7.1):
  - rclpy executor 는 별도 스레드에서 spin 하고, FastAPI 핸들러는 서비스 호출
    future 를 asyncio 로 기다린다.
  - uvicorn worker 는 1개로 고정하고 reload=False 로 실행한다. 같은 이름의 노드가
    중복 생성되는 것을 막기 위함이다.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from pathlib import Path

import rclpy
import uvicorn
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from ruamel.yaml import YAML

from .api import build_app
from .applier import Applier
from .changesets import ChangeSetRunner
from .discovery import Discovery
from .gitparams import GitParams
from .models import SafetyRules
from .schema import SchemaRegistry
from .services_map import ServicesMap
from .store import ParamStore
from .validator import SafetyGate
from .verifier import Verifier

_log = logging.getLogger(__name__)
_yaml = YAML(typ="safe")

NODE_NAME = "av_config_app"
DEFAULT_ROOT = Path("/opt/av_config")


def _resolve_root() -> Path:
    """설정 루트를 정한다. 개발 중에는 리포의 config/ 를 가리킬 수 있다."""
    env = os.environ.get("AV_CONFIG_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_ROOT


def _load_safety(path: Path) -> SafetyRules:
    """rules/safety.yaml 을 읽는다. 없으면 fail-safe 기본값(enforce)을 쓴다."""
    if not path.is_file():
        _log.warning("%s 없음. safety.mode=enforce 로 둔다 (fail-safe).", path)
        return SafetyRules()
    with path.open("r", encoding="utf-8") as fh:
        raw = _yaml.load(fh) or {}
    rules = SafetyRules.model_validate(raw)
    if rules.mode.value == "open":
        _log.warning(
            "safety.mode=open: 차량 상태를 알 수 없어도 적용을 허용한다. "
            "개발·벤치 전용이며 차량 탑재 시 enforce 로 바꿔야 한다 (명세 §10.1)."
        )
    return rules


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=os.environ.get("AV_CONFIG_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    root = _resolve_root()
    _log.info("설정 루트: %s", root)

    services_path = root / "services.yaml"
    if not services_path.is_file():
        raise SystemExit(
            f"{services_path} 가 없다. Config App 은 이 파일 없이는 어떤 노드를 "
            "어떤 unit 으로 재시작할지 알 수 없다 (명세 §11.2)."
        )

    services = ServicesMap.load(services_path)
    schemas = SchemaRegistry.load_dir(root / "schemas")
    store = ParamStore(root)
    safety = _load_safety(root / "rules" / "safety.yaml")

    for err in schemas.errors:
        _log.error("스키마 %s 를 건너뛴다: %s", err.file, err.error)

    rclpy.init(args=argv)
    node: Node = rclpy.create_node(NODE_NAME)

    # 파라미터 서비스 호출과 spin 이 서로를 막지 않도록 멀티스레드 executor 를 쓴다.
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    spin_thread = threading.Thread(
        target=executor.spin, name="rclpy-executor", daemon=True
    )
    spin_thread.start()
    _log.info("ROS 2 노드 기동: %s (domain=%s)", NODE_NAME, os.environ.get("ROS_DOMAIN_ID", "0"))

    discovery = Discovery(node, services, schemas, store)
    git = GitParams(root / "params")
    if not git.is_repo():
        _log.error(
            "%s 가 git 저장소가 아니다. 적용 이력과 롤백이 동작하지 않는다. "
            "M0 에서 git init 이 필요하다 (명세 §13.1).",
            git.repo,
        )
    elif not git.is_clean():
        _log.warning(
            "parameter file 에 커밋되지 않은 변경이 있다: %s. "
            "Config App 밖에서 수정된 것일 수 있다 (§6.3).",
            ", ".join(git.dirty_files()),
        )

    gate = SafetyGate(safety)
    applier = Applier(node)
    runner = ChangeSetRunner(
        discovery=discovery,
        services=services,
        schemas=schemas,
        store=store,
        git=git,
        applier=applier,
        verifier=Verifier(node),
        gate=gate,
        restart_preflight=applier.check_restart_units,
    )

    static_dir = Path(__file__).parent / "static"
    app = build_app(discovery, safety, gate, runner, git, static_dir)

    host = os.environ.get("AV_CONFIG_HOST", "127.0.0.1")
    port = int(os.environ.get("AV_CONFIG_PORT", "8080"))
    if host == "0.0.0.0":
        _log.warning(
            "0.0.0.0 에 바인딩한다. 명세 §10.3 은 차량 내부 NIC 에만 바인딩하고 "
            "외부 노출을 금지한다."
        )

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        workers=1,       # §7.1: 노드 이름 중복 방지
        reload=False,    # 같은 이유로 reload 를 쓰지 않는다
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)

    def _shutdown(signum: int, _frame: object) -> None:
        _log.info("시그널 %s 수신. 종료한다.", signum)
        server.should_exit = True

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        server.run()
    finally:
        executor.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        _log.info("종료 완료")


if __name__ == "__main__":
    main()
