"""REST / WebSocket 엔드포인트와 정적 파일 서빙 (명세 §7.3).

프론트엔드 빌드 산출물을 Config App 이 직접 서빙하므로 별도 웹 서버를 두지 않는다
(§8). 따라서 프로세스는 하나이고 CORS 문제도 없다.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .changesets import ChangeSetRunner
from .errors import ChangeSetBusy
from .gitparams import GitError, GitParams
from .models import (
    ChangeSetRequest,
    ChangeSetResult,
    DiscoverySnapshotDTO,
    NodeParamsDTO,
    OnboardingChoicesDTO,
    OnboardingDTO,
    OnboardingStepDTO,
    Role,
    SafetyMode,
    SafetyRules,
)
from .onboarding import (
    OnboardingChoices,
    ParamChoice,
    build_steps,
    count_todo,
    driver_name,
    editable_params,
    param_file_draft,
    schema_draft,
    services_snippet,
    subsystem_name,
    unit_name,
)
from .store import normalize_node_name
from .validator import SafetyGate

if TYPE_CHECKING:  # rclpy 를 끌어오지 않는다. 라우터는 덕 타이핑으로 쓴다.
    from .discovery import Discovery

_log = logging.getLogger(__name__)

_STREAM_PERIOD_SEC = 2.0


class StreamHub:
    """/api/v1/stream 연결 묶음.

    끊긴 동안의 이벤트는 재전송하지 않는다. 클라이언트가 재연결 시 상태를
    재동기화한다 (§7.3).
    """

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def add(self, socket: WebSocket) -> None:
        async with self._lock:
            self._clients.add(socket)

    async def remove(self, socket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(socket)

    async def broadcast(self, message: dict[str, Any]) -> None:
        async with self._lock:
            targets = list(self._clients)
        for socket in targets:
            try:
                await socket.send_json(message)
            except Exception:
                await self.remove(socket)


def _decode_header(value: str | None) -> str | None:
    """HTTP 헤더 값을 UTF-8 로 복구한다.

    ASGI 서버는 헤더를 latin-1 로 디코딩한다(RFC 9110). 그래서 한글 사용자
    이름이 들어오면 글자가 깨져 감사 로그(§10.3)가 손상된다. latin-1 로 되돌린
    뒤 UTF-8 로 다시 읽어 복구한다.
    """
    if value is None:
        return None
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def _identity(
    role_header: str | None, user_header: str | None
) -> tuple[Role, str]:
    """요청자의 역할과 이름.

    인증은 M3 에서 붙인다 (§13.1). 그때까지는 헤더로 받고, 없으면 engineer 로
    간주하되 경고를 남긴다. 운영 배포 전에 반드시 교체해야 한다.
    """
    user = _decode_header(user_header) or "anonymous"
    if role_header is None:
        _log.warning(
            "X-AV-Role 헤더가 없어 engineer 로 간주한다. 인증은 M3 에서 붙는다."
        )
        return Role.ENGINEER, user
    try:
        return Role(role_header), user
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"알 수 없는 역할: {role_header}"
        ) from None


def build_router(
    discovery: Discovery,
    safety: SafetyRules,
    gate: SafetyGate,
    runner: ChangeSetRunner,
    git: GitParams,
    hub: StreamHub,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    # ------------------------------------------------------------------ 조회

    @router.get("/nodes", response_model=DiscoverySnapshotDTO)
    async def list_nodes() -> DiscoverySnapshotDTO:
        """노드 목록, 매칭된 스키마, unit 매핑, 상태 (§8.1 노드 목록)."""
        return await discovery.snapshot()

    # 주의: 초안의 /nodes/{node}/params 는 라우팅할 수 없다. 노드 이름이 슬래시를
    # 포함하므로 path 변환기가 뒤의 /params 까지 먹는다. 노드 이름을 마지막에 둔다.
    @router.get("/params/{node:path}", response_model=NodeParamsDTO)
    async def node_params(node: str) -> NodeParamsDTO:
        """파라미터 목록 (런타임값, 저장값, drift, 스키마 정보)."""
        fq = normalize_node_name(node)
        result = await discovery.collect_params(fq)
        if result.node.unreachable_reason and not result.params:
            raise HTTPException(status_code=404, detail=f"{fq}: {result.node.unreachable_reason}")
        return result

    @router.get("/safety")
    async def safety_state() -> dict[str, Any]:
        """현재 적용 가능 여부와 사유 (§10.1)."""
        locked = gate.blocks_any()
        return {
            "mode": safety.mode.value,
            "apply_allowed": locked is None,
            "reason": locked
            or (
                "safety.mode=open (개발 모드). 적용마다 경고를 남긴다."
                if safety.mode is SafetyMode.OPEN
                else "차량이 정차 상태다."
            ),
            "vehicle_state_topic": safety.vehicle_state.topic or None,
            "vehicle_state_known": gate.state_known,
            "busy_changeset": runner.busy,
        }

    @router.get("/onboarding/{node:path}", response_model=OnboardingDTO)
    async def onboarding(node: str) -> OnboardingDTO:
        """신규 센서 등록에 필요한 초안과 절차 (§12.2).

        자동 수집 결과로 services.yaml 조각, parameter file, 스키마 초안을 만든다.
        계층·적용 방식·등급은 ROS 가 알려줄 수 없으므로 TODO 로 남는다.
        """
        return await _onboarding(node, OnboardingChoicesDTO())

    @router.post("/onboarding/{node:path}", response_model=OnboardingDTO)
    async def onboarding_draft(
        node: str, choices: OnboardingChoicesDTO
    ) -> OnboardingDTO:
        """사람이 정한 값을 박아 넣은 초안 (§12.2).

        아무것도 저장하지 않는다. services.yaml 과 스키마는 코드 리뷰를 거쳐
        리포에 들어가야 하므로, 이 엔드포인트는 붙여 넣을 본문만 돌려준다.
        """
        return await _onboarding(node, choices)

    async def _onboarding(node: str, choices: OnboardingChoicesDTO) -> OnboardingDTO:
        fq = normalize_node_name(node)
        collected = await discovery.collect_params(fq)
        dto = collected.node
        sub = subsystem_name(fq)

        topics = discovery.published_topics(fq)
        services = sorted(discovery.services_of(fq))
        # 표준 파라미터 서비스는 모든 노드가 가지므로 매칭 조건으로 쓸모가 없다
        services = [
            srv
            for srv in services
            if not srv.endswith(
                (
                    "/list_parameters",
                    "/get_parameters",
                    "/set_parameters",
                    "/describe_parameters",
                    "/get_parameter_types",
                    "/set_parameters_atomically",
                    "/get_type_description",
                )
            )
        ]

        notes: list[str] = []
        if not dto.alive:
            notes.append(
                "노드가 실행 중이 아니다. 초안은 parameter file 에 남아 있는 값만 "
                "반영한다. 장치를 연결하고 노드를 띄운 뒤 다시 받는 편이 정확하다."
            )
        if dto.registered and dto.schema_driver is None:
            notes.append(
                "services.yaml 에는 등록됐지만 스키마가 없다. 모든 파라미터가 "
                "restart / engineer 로 간주되고, device 계층 검증을 할 수 없다."
            )
        if not dto.registered:
            notes.append(
                "services.yaml 에 없어 변경을 저장할 곳이 없다. 등록 전까지 "
                "이 노드는 읽기 전용이다."
            )
        if not services:
            notes.append(
                "표준 파라미터 서비스 외에 드라이버 전용 서비스가 보이지 않는다. "
                "reconfigure 적용과 device 계층 read-back 을 쓸 수 없으므로 "
                "드라이버 수정이 필요할 수 있다 (§12.1-4, §12.1-5)."
            )

        steps = build_steps(
            registered=dto.registered,
            has_schema=dto.schema_driver is not None,
            alive=dto.alive,
            has_param_file=bool(dto.param_files),
        )

        picked = OnboardingChoices(
            launch=choices.launch,
            driver=choices.driver,
            topic_hz=None if choices.topic_hz is None else dict(choices.topic_hz),
            required_services=choices.required_services,
            layer=choices.layer.value if choices.layer else None,
            apply=choices.apply.value if choices.apply else None,
            tier=choices.tier.value if choices.tier else None,
            readback_service=choices.readback_service,
            params={
                name: ParamChoice(
                    label=item.label,
                    layer=item.layer.value if item.layer else None,
                    apply=item.apply.value if item.apply else None,
                    tier=item.tier.value if item.tier else None,
                    readback_service=item.readback_service,
                    readback_json_path=item.readback_json_path,
                )
                for name, item in choices.params.items()
            },
        )

        snippet = services_snippet(
            fq,
            topics,
            existing_subsystem=dto.subsystem,
            existing_unit=dto.unit,
            existing_param_files=dto.param_files or None,
            choices=picked,
        )
        params_draft = param_file_draft(fq, collected.params)
        schema = schema_draft(fq, collected.params, services, choices=picked)

        return OnboardingDTO(
            node=fq,
            registered=dto.registered,
            has_schema=dto.schema_driver is not None,
            alive=dto.alive,
            subsystem=dto.subsystem or sub,
            unit=dto.unit or unit_name(sub),
            param_file=f"params/{sub}.yaml",
            schema_file=f"schemas/{driver_name(fq)}.yaml",
            services_snippet=snippet,
            param_file_draft=params_draft,
            schema_draft=schema,
            topics=topics,
            editable_params=editable_params(collected.params),
            todo_count=count_todo(snippet, params_draft, schema),
            steps=[OnboardingStepDTO(**vars(step)) for step in steps],
            notes=notes,
        )

    # ------------------------------------------------------------------ 적용

    @router.post("/changesets", response_model=ChangeSetResult)
    async def submit_changeset(
        request: ChangeSetRequest,
        x_av_role: str | None = Header(default=None),
        x_av_user: str | None = Header(default=None),
    ) -> ChangeSetResult:
        """change set 검증만(dry_run=true) 또는 적용 (§6.1)."""
        role, author = _identity(x_av_role, x_av_user)
        try:
            return await runner.submit(request, role, author, progress=hub.broadcast)
        except ChangeSetBusy as exc:
            # 두 번째 요청은 대기열이 아니라 거부한다 (§6.3)
            raise HTTPException(
                status_code=409,
                detail=f"이미 실행 중인 change set 이 있다: {exc}",
            ) from exc

    @router.get("/changesets/{changeset_id}", response_model=ChangeSetResult)
    async def changeset_status(changeset_id: str) -> ChangeSetResult:
        result = runner.history.get(changeset_id)
        if result is None:
            raise HTTPException(status_code=404, detail="알 수 없는 change set 이다")
        return result

    # ------------------------------------------------------------------ 이력

    @router.get("/history")
    async def history(limit: int = 50) -> dict[str, Any]:
        """변경 이력 (git log 기반, §7.3)."""
        if not git.is_repo():
            raise HTTPException(
                status_code=503,
                detail=f"{git.repo} 가 git 저장소가 아니다. M0 에서 git init 이 필요하다.",
            )
        return {
            "clean": git.is_clean(),
            "dirty_files": git.dirty_files(),
            "commits": [c.__dict__ for c in git.log(limit)],
        }

    @router.get("/history/{commit}/diff")
    async def history_diff(commit: str) -> dict[str, str]:
        try:
            return {"diff": git.show_diff(commit)}
        except GitError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/history/{commit}/restore")
    async def restore(
        commit: str,
        x_av_role: str | None = Header(default=None),
        x_av_user: str | None = Header(default=None),
    ) -> dict[str, str]:
        """특정 시점으로 복원한다. 새 commit 으로 쌓이므로 이력이 사라지지 않는다."""
        role, author = _identity(x_av_role, x_av_user)
        if role not in (Role.ENGINEER, Role.ADMIN):
            raise HTTPException(status_code=403, detail="롤백은 engineer 이상이다")
        try:
            sha = git.restore(commit, author, f"{commit[:8]} 시점으로 복원")
        except GitError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "commit": sha,
            "note": (
                "파일만 되돌렸다. 노드에 반영하려면 해당 unit 을 재시작하는 "
                "change set 을 적용하라."
            ),
        }

    # ------------------------------------------------------------------ 스트림

    @router.websocket("/stream")
    async def stream(websocket: WebSocket) -> None:
        """적용 진행, 노드 상태, 점검 결과 실시간 푸시 (§7.3).

        모든 메시지에 type 디스크리미네이터를 둔다.
        """
        await websocket.accept()
        await hub.add(websocket)
        try:
            while True:
                snapshot = await discovery.snapshot()
                for node in snapshot.nodes:
                    await websocket.send_json(
                        {
                            "type": "node_state",
                            "node": node.name,
                            "alive": node.alive,
                            "unit": node.unit,
                            "drift_count": node.drift_count,
                            "unreachable_reason": node.unreachable_reason,
                        }
                    )
                await asyncio.sleep(_STREAM_PERIOD_SEC)
        except WebSocketDisconnect:
            _log.debug("WebSocket 연결 종료")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.warning("WebSocket 오류: %s", exc)
        finally:
            await hub.remove(websocket)

    return router


def build_app(
    discovery: Discovery,
    safety: SafetyRules,
    gate: SafetyGate,
    runner: ChangeSetRunner,
    git: GitParams,
    static_dir: Path,
) -> FastAPI:
    app = FastAPI(
        title="AV Web Configure App",
        version="0.2.0",
        description="ROS 2 파라미터 설정 도구 (M1 조회 + M2 적용)",
    )
    hub = StreamHub()
    app.include_router(build_router(discovery, safety, gate, runner, git, hub))

    if static_dir.is_dir():
        _mount_spa(app, static_dir)
        _log.info("정적 파일 서빙: %s", static_dir)
    else:
        _log.warning("정적 파일 디렉터리 없음: %s. frontend 를 빌드하라.", static_dir)

        @app.get("/")
        async def placeholder() -> dict[str, str]:
            return {"status": "ok", "note": "프론트엔드 미빌드. /docs 에서 API 확인."}

    return app


def _mount_spa(app: FastAPI, static_dir: Path) -> None:
    """SPA 를 서빙한다.

    StaticFiles 만 쓰면 /history 같은 클라이언트 라우트를 직접 요청할 때 404 가
    난다. 브라우저 새로고침, 북마크, URL 공유가 모두 깨지므로 라우트처럼 보이는
    경로는 index.html 로 넘긴다.
    """
    root = static_dir.resolve()
    index = root / "index.html"

    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        # API 경로는 라우터가 먼저 받는다. 여기 오면 없는 엔드포인트다.
        if full_path.startswith(("api/", "docs", "openapi.json", "redoc")):
            raise HTTPException(status_code=404, detail="없는 엔드포인트다")

        if full_path:
            candidate = (root / full_path).resolve()
            # 경로 탈출 방지: 반드시 static 디렉터리 안이어야 한다
            if candidate.is_file() and (candidate == root or root in candidate.parents):
                return FileResponse(candidate)
            # 확장자가 있는데 파일이 없으면 진짜 404 다. index.html 을 주면
            # 잘못된 Content-Type 으로 응답하게 된다.
            if Path(full_path).suffix:
                raise HTTPException(status_code=404, detail=f"{full_path} 가 없다")

        return FileResponse(index)
