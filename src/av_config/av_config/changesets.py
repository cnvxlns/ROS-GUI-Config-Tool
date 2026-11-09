"""change set 실행 (명세 §6.1 전체, §6.2, §6.3).

적용 버튼 하나가 검증 -> 저장 -> 적용 -> 반영 확인 -> (실패 시) 롤백을 하나의
트랜잭션처럼 수행한다.

검증에서 실패하면 아무것도 바뀌지 않고, 적용 후 반영 확인에서 실패하면 revert
commit 을 쌓아 직전 상태로 되돌린다.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from .errors import ApplyError, ChangeSetBusy, VerifyError
from .gitparams import GitError, GitParams
from .models import (
    ChangeSetRequest,
    ChangeSetResult,
    ChangeSetStatus,
    ExecutionPlan,
    IssueCode,
    Layer,
    ParamDTO,
    ParamResult,
    Role,
    ServiceRef,
    StepKind,
    ValidationIssue,
)
from .planner import build_plan
from .schema import SchemaRegistry
from .services_map import ServicesMap
from .store import ParamStore, ParamWriteError, ParamWriter, normalize_node_name
from .validator import SafetyGate, validate

if TYPE_CHECKING:  # rclpy 를 끌어오지 않는다. 오케스트레이터는 덕 타이핑으로 쓴다.
    from .applier import Applier
    from .discovery import Discovery
    from .verifier import Verifier

_log = logging.getLogger(__name__)

ProgressFn = Callable[[dict[str, Any]], Awaitable[None]]

# 재시작 후 노드가 다시 **쓸 수 있는 상태**로 돌아올 때까지 기다리는 시간.
# ouster_ros 는 노드를 만든 뒤 센서 클라이언트 초기화에 20초 넘게 쓰고, 그 다음에야
# 서비스와 토픽이 뜬다(2026-11-09 실측: 재시작 -> 서비스 등장까지 약 25초).
# 되읽기가 그 전에 돌면 정상 적용이 롤백된다. 센서가 느릴 때를 감안해 여유를 둔다.
_NODE_RETURN_TIMEOUT = 90.0

# 재시작 뒤 반영 확인이 수렴할 때까지 기다리는 시간 (§6.1-6).
#
# 그래프 정보는 재시작 직후에 믿을 수 없다. 사라진 노드의 이름과 서비스가 DDS
# discovery 에 한동안 남아 있어서, 그것만 보고 "준비됐다"고 판단하면 되읽기가
# "5초 안에 응답 없음" 으로 실패한다 (2026-11-09 실측, 정상 적용이 롤백됐다).
# 믿을 수 있는 신호는 **실제 호출이 성공하는 것** 하나뿐이므로, 일시적 실패는
# 이 시간 동안 다시 시도한다.
#
# 다시 시도하는 조건은 둘로 한정한다:
#   - VerifyError (서비스를 못 찾거나 응답이 없다)  -> 아직 안 올라온 상태
#   - 토픽 표본이 0건                               -> 발행이 아직 시작되지 않음
# 값이 돌아왔는데 요청값과 다르면 다시 시도하지 않는다. 재시작 후의 노드는 새
# 인스턴스이므로 그 답은 최종값이다.
_VERIFY_SETTLE_TIMEOUT = 60.0


class ChangeSetRunner:
    def __init__(
        self,
        discovery: Discovery,
        services: ServicesMap,
        schemas: SchemaRegistry,
        store: ParamStore,
        git: GitParams,
        applier: Applier,
        verifier: Verifier,
        gate: SafetyGate,
    ) -> None:
        self._discovery = discovery
        self._services = services
        self._schemas = schemas
        self._store = store
        self._writer = ParamWriter(store)
        self._git = git
        self._applier = applier
        self._verifier = verifier
        self._gate = gate

        self._running: str | None = None
        self._lock = asyncio.Lock()
        self.history: dict[str, ChangeSetResult] = {}

    # ------------------------------------------------------------------

    @property
    def busy(self) -> str | None:
        return self._running

    async def submit(
        self,
        request: ChangeSetRequest,
        role: Role,
        author: str,
        progress: ProgressFn | None = None,
    ) -> ChangeSetResult:
        changeset_id = uuid.uuid4().hex[:12]

        async with self._lock:
            if self._running is not None and not request.dry_run:
                raise ChangeSetBusy(self._running)
            if not request.dry_run:
                self._running = changeset_id

        try:
            result = await self._run(changeset_id, request, role, author, progress)
        finally:
            async with self._lock:
                if self._running == changeset_id:
                    self._running = None

        self.history[changeset_id] = result
        return result

    # ------------------------------------------------------------------

    async def _run(
        self,
        changeset_id: str,
        request: ChangeSetRequest,
        role: Role,
        author: str,
        progress: ProgressFn | None,
    ) -> ChangeSetResult:
        result = ChangeSetResult(id=changeset_id, status=ChangeSetStatus.RUNNING)

        async def emit(stage: str, detail: Any = None) -> None:
            result.stage = stage
            if progress is not None:
                await progress(
                    {
                        "type": "changeset_progress",
                        "id": changeset_id,
                        "stage": stage,
                        "detail": detail,
                    }
                )

        # --- 2. 검증 -------------------------------------------------
        await emit("validate")
        nodes = sorted({normalize_node_name(c.node) for c in request.changes})
        params_by_node: dict[str, dict[str, ParamDTO]] = {}
        nodes_by_name = {}
        checks_by_node: dict[str, dict[str, list[str]]] = {}
        readback_refs: dict[tuple[str, str], ServiceRef | None] = {}

        for node in nodes:
            collected = await self._discovery.collect_params(node)
            params_by_node[node] = {p.name: p for p in collected.params}
            nodes_by_name[node] = collected.node
            schema = self._schemas.match(node, None)
            checks: dict[str, list[str]] = {}
            for name in params_by_node[node]:
                ps = self._schemas.param_schema(schema, name)
                if ps is not None:
                    checks[name] = list(ps.checks)
                    readback_refs[(node, name)] = ps.readback_via
            checks_by_node[node] = checks

        issues = validate(
            list(request.changes), params_by_node, checks_by_node, role, self._gate
        )

        # --- 저장 전 외부 수정 감지 (§6.3) ---------------------------
        issues.extend(self._check_hashes(request, nodes))

        if issues:
            result.status = ChangeSetStatus.REJECTED
            result.issues = issues
            result.message = "검증에서 거부되었다. 아무것도 바뀌지 않았다."
            await emit("rejected", {"issues": len(issues)})
            return result

        # --- 3. 계획 -------------------------------------------------
        await emit("plan")
        try:
            targets = self._plan_write_targets(request, nodes)
        except ParamWriteError as exc:
            result.status = ChangeSetStatus.REJECTED
            result.issues = [
                ValidationIssue(
                    node=nodes[0], code=IssueCode.NO_PARAM_FILE, message=str(exc)
                )
            ]
            return result

        # 공유 파일(global.yaml)을 고치는 경우, 그 파일을 쓰는 다른 unit 의 노드도
        # 수집해야 재시작 범위를 정확히 좁힐 수 있다. 수집하지 않으면 planner 가
        # "선언 여부 불명"으로 보고 보수적으로 전부 재시작한다.
        await self._collect_file_sharers(targets, params_by_node, nodes_by_name)

        plan = build_plan(
            list(request.changes), params_by_node, nodes_by_name, self._services, targets
        )
        result.plan = plan

        if request.dry_run:
            result.status = ChangeSetStatus.DRY_RUN
            result.message = "검증과 계획만 수행했다."
            await emit("dry_run")
            return result

        # --- 4. 저장 -------------------------------------------------
        await emit("save", {"files": plan.files_to_write})
        baseline = self._git.head()
        try:
            written = self._writer.apply(targets)
        except ParamWriteError as exc:
            result.status = ChangeSetStatus.REJECTED
            result.message = f"저장 실패. 아무것도 바뀌지 않았다: {exc}"
            return result

        commit_sha: str | None = None
        try:
            commit_sha = self._git.commit(
                self._to_repo_relative(written),
                _commit_message(request, author, changeset_id),
                author,
            )
            result.commit = commit_sha
        except GitError as exc:
            # commit 에 실패하면 되돌릴 기준점이 없다. 롤백 불가 상태로 적용을
            # 진행하는 것은 위험하므로, 파일 쓰기를 취소하고 거부한다.
            _log.error("git commit 실패: %s", exc)
            restored = self._restore_working_tree()
            result.status = ChangeSetStatus.REJECTED
            result.message = (
                f"git commit 실패로 적용을 중단했다: {exc}. "
                + (
                    "파일을 직전 상태로 되돌렸다."
                    if restored
                    else "파일을 되돌리지 못했다. 수동 확인이 필요하다."
                )
            )
            await emit("rejected", {"reason": "git_commit_failed"})
            return result

        # --- 5. 적용 + 6. 반영 확인 ----------------------------------
        requested = {
            (normalize_node_name(c.node), c.param): c.value for c in request.changes
        }
        try:
            await self._execute(plan, requested, params_by_node, emit)
            await emit("verify")
            results = await self._verify(
                request, plan, params_by_node, readback_refs, emit
            )
            result.results = results
            failures = [r for r in results if not r.success]
        except (ApplyError, VerifyError) as exc:
            result.results = [
                ParamResult(
                    node=normalize_node_name(c.node),
                    param=c.param,
                    requested=c.value,
                    success=False,
                    reason=str(exc),
                )
                for c in request.changes
            ]
            failures = result.results

        if not failures:
            result.status = ChangeSetStatus.SUCCEEDED
            result.message = "적용과 반영 확인을 모두 통과했다."
            await emit("done")
            return result

        # --- 7. 롤백 -------------------------------------------------
        await emit("rollback", {"failed": len(failures)})
        result.status = ChangeSetStatus.ROLLED_BACK
        try:
            if commit_sha is not None and commit_sha != baseline:
                result.rollback_commit = self._git.revert(commit_sha, author)
            else:
                _log.warning("되돌릴 commit 이 없다. 파일 상태를 그대로 둔다.")
            # 되돌린 파일의 값으로 같은 계획을 다시 적용해 런타임 상태를 맞춘다.
            # YAML 이 주인이므로(원칙 1) 적용할 값은 되돌린 파일에서 다시 읽는다.
            reverted = self._reverted_values(request)
            await self._execute(plan, reverted, params_by_node, emit)
            result.message = (
                "반영 확인에 실패해 직전 상태로 되돌렸다. "
                f"실패 {len(failures)}건의 사유를 확인하라."
            )
        except (GitError, ApplyError) as exc:
            # 롤백도 실패했다. 위험 상태다 (§6.2).
            result.status = ChangeSetStatus.ROLLBACK_FAILED
            result.message = (
                f"롤백도 실패했다. 수동 조치가 필요하다: {exc} "
                f"(기준 commit {baseline})"
            )
            _log.critical("롤백 실패: %s", exc)
        await emit("rolled_back")
        return result

    # ------------------------------------------------------------------

    def _check_hashes(
        self, request: ChangeSetRequest, nodes: list[str]
    ) -> list[ValidationIssue]:
        """YAML 이 Config App 밖에서 수정됐는지 확인한다 (§6.3)."""
        if not request.expected_hashes:
            return []
        files: set[str] = set()
        for node in nodes:
            files.update(self._services.param_files_of(node))
        current = self._writer.file_hashes(sorted(files))
        issues: list[ValidationIssue] = []
        for rel, expected in request.expected_hashes.items():
            actual = current.get(rel)
            if actual is not None and actual != expected:
                issues.append(
                    ValidationIssue(
                        node=nodes[0],
                        code=IssueCode.FILE_CHANGED,
                        message=(
                            f"{rel} 이 조회 이후 외부에서 수정됐다. "
                            "다시 조회한 뒤 적용하라."
                        ),
                    )
                )
        return issues

    def _reverted_values(
        self, request: ChangeSetRequest
    ) -> dict[tuple[str, str], Any]:
        """revert 후 parameter file 에 남은 값. 롤백 재적용에 쓴다."""
        out: dict[tuple[str, str], Any] = {}
        by_node: dict[str, list[str]] = {}
        for change in request.changes:
            by_node.setdefault(normalize_node_name(change.node), []).append(change.param)
        for node, names in by_node.items():
            # 캐시를 비워 revert 된 파일을 다시 읽는다
            for rel in self._services.param_files_of(node):
                self._store._cache.pop(rel, None)
            saved = self._store.saved_values(node, self._services.param_files_of(node))
            for name in names:
                entry = saved.get(name)
                if entry is None:
                    _log.warning(
                        "%s 의 %s 가 되돌린 파일에 없다. 재적용에서 건너뛴다.", node, name
                    )
                    continue
                out[(node, name)] = entry.value
        return out

    async def _collect_file_sharers(
        self,
        targets: list[Any],
        params_by_node: dict[str, dict[str, ParamDTO]],
        nodes_by_name: dict[str, Any],
    ) -> None:
        """쓰려는 파일을 공유하는 다른 unit 의 노드도 수집한다 (§6.1 전역 변경 처리).

        이 정보가 없으면 그 파라미터를 쓰지 않는 unit 까지 재시작하게 된다.
        수집에 실패한 노드는 그대로 두어 planner 가 보수적으로 판단하게 한다.
        """
        files = {t.rel_path for t in targets}
        shared = [
            rel
            for rel in files
            if sum(1 for sub in self._services.subsystems.values() if rel in sub.param_files)
            > 1
        ]
        if not shared:
            return

        extra: set[str] = set()
        for sub in self._services.subsystems.values():
            if not any(rel in sub.param_files for rel in shared):
                continue
            for node in sub.nodes:
                fq = normalize_node_name(node)
                if fq not in params_by_node:
                    extra.add(fq)

        for fq in sorted(extra):
            try:
                collected = await self._discovery.collect_params(fq)
            except Exception as exc:
                _log.warning("%s 수집 실패. 재시작 범위를 보수적으로 잡는다: %s", fq, exc)
                continue
            params_by_node[fq] = {p.name: p for p in collected.params}
            nodes_by_name[fq] = collected.node

    def _to_repo_relative(self, written: list[str]) -> list[str]:
        """store 기준 경로를 git 저장소 기준 경로로 바꾼다.

        parameter file 은 services.yaml 에 "params/lidar_top.yaml" 처럼 설정 루트
        기준으로 적히지만, git 저장소는 params/ 에 있다 (§11.1). 두 기준을 섞으면
        git add 가 실패한다.
        """
        out: list[str] = []
        for rel in written:
            absolute = self._store.path_of(rel).resolve()
            try:
                out.append(str(absolute.relative_to(self._git.repo.resolve())))
            except ValueError:
                _log.error(
                    "%s 는 git 저장소(%s) 밖에 있어 이력에 남길 수 없다",
                    absolute,
                    self._git.repo,
                )
        if not out:
            raise GitError(
                "변경된 parameter file 이 모두 git 저장소 밖에 있다. "
                "services.yaml 의 param_files 와 §11.1 디렉터리 구조를 확인하라."
            )
        return out

    def _restore_working_tree(self) -> bool:
        """커밋되지 않은 파일 변경을 되돌린다. commit 실패 시 사용한다."""
        try:
            self._git._run("checkout", "--", ".")
            for rel in list(self._store._cache):
                self._store._cache.pop(rel, None)
            return True
        except GitError as exc:
            _log.error("작업 트리 복원 실패: %s", exc)
            return False

    def _plan_write_targets(self, request: ChangeSetRequest, nodes: list[str]):
        targets = []
        for node in nodes:
            changes = {
                c.param: c.value
                for c in request.changes
                if normalize_node_name(c.node) == node
            }
            targets.extend(
                self._writer.plan_targets(
                    node, changes, self._services.param_files_of(node)
                )
            )
        return targets

    async def _execute(
        self,
        plan: ExecutionPlan,
        values: dict[tuple[str, str], Any],
        params_by_node: dict[str, dict[str, ParamDTO]],
        emit: Callable[..., Awaitable[None]],
    ) -> None:
        """계획 순서대로 적용한다 (§6.1-5).

        values 는 적용할 값이다. 정방향 적용에서는 요청값이고, 롤백 재적용에서는
        되돌린 YAML 의 값이다. 요청값을 재사용하면 YAML 은 되돌아갔는데 장치에는
        새 값이 남는 상태가 된다.
        """
        for step in plan.steps:
            await emit("apply", {"kind": step.kind.value, "target": step.node or step.units})

            if step.kind is StepKind.SET_PARAMETERS and step.node:
                payload = {}
                for name in step.params:
                    if (step.node, name) not in values:
                        continue
                    param = params_by_node[step.node][name]
                    payload[name] = (int(param.type), values[(step.node, name)])
                if not payload:
                    continue
                rejected = await self._applier.set_parameters(step.node, payload)
                bad = {k: v for k, v in rejected.items() if v is not None}
                if bad:
                    raise ApplyError(
                        "드라이버가 거부했다: "
                        + "; ".join(f"{k}: {v}" for k, v in bad.items())
                    )

            elif step.kind is StepKind.RECONFIGURE and step.node:
                schema = self._schemas.match(step.node, None)
                for name in step.params:
                    if (step.node, name) not in values:
                        continue
                    ps = self._schemas.param_schema(schema, name)
                    if ps is None or ps.apply_via is None:
                        raise ApplyError(
                            f"{name} 의 apply_via 가 없어 장치를 재설정할 수 없다"
                        )
                    await self._applier.call_driver_service(
                        ps.apply_via, values[(step.node, name)]
                    )

            elif step.kind is StepKind.RESTART_UNITS:
                await self._applier.restart_units(step.units)
                await self._wait_for_nodes(step.units)

    async def _wait_for_nodes(self, units: list[str]) -> None:
        """재시작한 unit 의 노드가 다시 **쓸 수 있는 상태**로 올 때까지 기다린다.

        노드 이름만 보면 안 된다. 드라이버는 노드를 먼저 만들고, 장치에 접속한
        뒤에야 서비스와 토픽을 띄운다. 이름이 보이는 시점과 되읽기가 가능한 시점
        사이가 ouster_ros 에서는 20초가 넘는다.

        이름만 확인하고 넘어가면 §6.1-6 의 되읽기가 "서비스를 찾을 수 없다"로
        실패하고, 반영이 제대로 됐는데도 롤백된다. 2026-11-09 에 실제로 그렇게
        됐다 (status=rollback_failed, 사유 "/ouster/get_config 를 그래프에서 찾을
        수 없다").

        준비 완료의 기준은 스키마가 이미 갖고 있다. required_services 가 보이면
        매칭되고, 안 보이면 매칭되지 않는다(§5.3). 그 게이트를 그대로 쓴다.
        """
        expected = set()
        for unit in units:
            expected.update(self._services.nodes_in_unit(unit))
        if not expected:
            return
        deadline = asyncio.get_running_loop().time() + _NODE_RETURN_TIMEOUT
        while True:
            live = set(self._discovery.live_nodes())
            missing = expected - live
            not_ready = {
                node for node in expected - missing if not self._node_ready(node)
            }
            if not missing and not not_ready:
                return
            if asyncio.get_running_loop().time() >= deadline:
                detail = []
                if missing:
                    detail.append(f"돌아오지 않은 노드: {', '.join(sorted(missing))}")
                if not_ready:
                    detail.append(
                        "서비스가 아직 뜨지 않은 노드: " + ", ".join(sorted(not_ready))
                    )
                raise ApplyError("재시작 후 " + "; ".join(detail))
            await asyncio.sleep(0.5)

    def _node_ready(self, node: str) -> bool:
        """스키마가 요구하는 서비스가 그래프에 보이는지.

        스키마가 없는 노드는 판정할 근거가 없으므로 준비된 것으로 본다.
        """
        if self._schemas.match(node, available_services=None) is None:
            return True
        return self._schemas.match(node, self._discovery.services_of(node)) is not None

    async def _verify(
        self,
        request: ChangeSetRequest,
        plan: ExecutionPlan,
        params_by_node: dict[str, dict[str, ParamDTO]],
        readback_refs: dict[tuple[str, str], ServiceRef | None],
        emit: Callable[..., Awaitable[None]],
    ) -> list[ParamResult]:
        """계층별 read-back 과 데이터 흐름으로 반영을 확인한다 (§6.1-6)."""
        results: list[ParamResult] = []

        deadline = asyncio.get_running_loop().time() + _VERIFY_SETTLE_TIMEOUT

        for change in request.changes:
            node = normalize_node_name(change.node)
            param = params_by_node[node][change.param]
            entry = ParamResult(node=node, param=change.param, requested=change.value)
            while True:
                try:
                    read = await self._verifier.read_back(
                        node, param, readback_refs.get((node, change.param))
                    )
                except VerifyError as exc:
                    entry.success = False
                    entry.reason = str(exc)
                    if asyncio.get_running_loop().time() < deadline:
                        await asyncio.sleep(1.0)
                        continue  # 아직 안 올라왔을 수 있다
                    break
                entry.actual = read.value
                entry.readback_source = read.source
                entry.success = self._verifier.matches(change.value, read.value)
                # 재시도 중에 담아둔 일시적 실패 사유를 지운다. 남겨 두면 성공한
                # 항목에 "응답 없음" 이 붙어 화면이 거짓말을 한다.
                entry.reason = None
                if not entry.success:
                    entry.reason = (
                        "read-back 값이 요청값과 다르다. "
                        + (
                            "장치가 값을 거부했거나 보정했다."
                            if param.layer is Layer.DEVICE
                            else "노드가 값을 반영하지 않았다."
                        )
                    )
                break  # 값이 돌아왔으면 그게 결론이다
            results.append(entry)

        # 데이터 흐름 확인. 값이 맞아도 데이터가 끊기면 반영 실패로 본다.
        await emit("verify_dataflow")
        for unit in plan.restart_units:
            for subsystem in self._services.subsystems.values():
                if subsystem.unit != unit:
                    continue
                for topic in subsystem.topics:
                    entry = None
                    while True:
                        try:
                            sample = await self._verifier.measure_hz(topic.name)
                        except VerifyError as exc:
                            entry = ParamResult(
                                node=unit,
                                param=topic.name,
                                success=False,
                                reason=f"토픽 확인 실패: {exc}",
                            )
                        else:
                            ok = sample.count > 0
                            if ok and topic.expect_hz:
                                ok = sample.hz >= topic.expect_hz * 0.5
                            entry = ParamResult(
                                node=unit,
                                param=topic.name,
                                requested=topic.expect_hz,
                                actual=round(sample.hz, 2),
                                success=ok,
                                reason=None
                                if ok
                                else f"{sample.hz:.2f} Hz 로 기대에 미달",
                                readback_source="topic",
                            )
                        if entry.success:
                            break
                        if asyncio.get_running_loop().time() >= deadline:
                            break
                        await asyncio.sleep(1.0)  # 발행이 아직 시작되지 않았을 수 있다
                    results.append(entry)
        return results


def _commit_message(request: ChangeSetRequest, author: str, changeset_id: str) -> str:
    """감사 로그를 commit 메시지에 담는다 (§10.3)."""
    lines = [request.comment.strip() or "설정 변경", ""]
    lines.append(f"changeset: {changeset_id}")
    lines.append(f"user: {author}")
    for change in request.changes:
        lines.append(f"  {normalize_node_name(change.node)} {change.param} = {change.value!r}")
    return "\n".join(lines)
