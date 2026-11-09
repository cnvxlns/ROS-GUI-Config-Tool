/**
 * 변경 검토와 적용 (명세 §8.1, §8.2, §6.1).
 *
 * 편집은 즉시 적용되지 않는다. 변경이 여기에 쌓이고 한 번에 적용한다.
 */

import { useState } from 'react'
import { affectedUnits, globalChanges, useChangeSet } from '../api/changeset'
import { useNodes, useSafety, useSubmitChangeSet } from '../api/client'
import { formatValue, type ChangeSetResult } from '../api/types'
import { ChangeSetResultView } from './ChangeSetResultView'

export function ChangeSetTray() {
  const changes = useChangeSet((s) => s.changes)
  const comment = useChangeSet((s) => s.comment)
  const liveStage = useChangeSet((s) => s.liveStage)
  const setComment = useChangeSet((s) => s.setComment)
  const removeChange = useChangeSet((s) => s.removeChange)
  const clear = useChangeSet((s) => s.clear)
  const { data: safety } = useSafety()
  const { data: snapshot } = useNodes()
  const submit = useSubmitChangeSet()
  const [result, setResult] = useState<ChangeSetResult | null>(null)

  if (changes.length === 0 && result === null) return null

  const units = affectedUnits(changes)
  // 재시작 영향 범위는 unit 단위다. 사용자가 직접 바꾼 노드만 보여주면 실제
  // 중단 범위를 과소평가하게 된다 (§6.1, §8.1).
  const impactedNodes = (snapshot?.nodes ?? []).filter(
    (node) => node.unit !== null && units.includes(node.unit),
  )
  const globals = globalChanges(changes)
  const applyBlocked = safety ? !safety.apply_allowed : false
  const busy = submit.isPending

  const send = (dryRun: boolean) => {
    setResult(null)
    submit.mutate(
      {
        dry_run: dryRun,
        comment,
        changes: changes.map((c) => ({ node: c.node, param: c.param, value: c.value })),
      },
      {
        onSuccess: (data) => {
          setResult(data)
          // 적용이 끝났으면 보류 목록을 비운다. 거부와 검증만 수행한 경우는 남긴다.
          if (data.status === 'succeeded') clear()
        },
        onError: (error) =>
          setResult({
            id: 'local',
            status: 'rejected',
            stage: 'validate',
            issues: [],
            plan: null,
            results: [],
            commit: null,
            rollback_commit: null,
            message: `요청 실패: ${String(error.message)}`,
          }),
      },
    )
  }

  return (
    <aside className="tray">
      <header className="tray__head">
        <h3>변경 {changes.length}건</h3>
        <button type="button" onClick={() => { clear(); setResult(null) }} disabled={busy}>
          전체 취소
        </button>
      </header>

      {changes.length > 0 && (
        <table className="diff">
          <thead>
            <tr>
              <th>노드 / 파라미터</th>
              <th>이전</th>
              <th>이후</th>
              <th>적용</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {changes.map((change) => (
              <tr key={`${change.node}:${change.param}`}>
                <td>
                  <code className="small">{change.node}</code>
                  <br />
                  <strong>{change.param}</strong>
                </td>
                <td className="mono">{formatValue(change.before)}</td>
                <td className="mono strong">{formatValue(change.value)}</td>
                <td>
                  <span
                    className={`badge badge--${change.apply === 'restart' ? 'danger' : 'info'}`}
                  >
                    {change.apply}
                  </span>
                </td>
                <td>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => removeChange(change.node, change.param)}
                  >
                    ×
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {units.length > 0 && (
        <div className="panel panel--warn">
          <h4>재시작 대상 unit {units.length}개</h4>
          <ul>
            {units.map((unit) => (
              <li key={unit}>
                <code>{unit}</code>
              </li>
            ))}
          </ul>
          <p>
            아래 노드 {impactedNodes.length}개가 함께 중단된다. 직접 바꾼 노드만이
            아니라 같은 unit 의 모든 노드가 내려간다.
          </p>
          <ul className="small">
            {impactedNodes.map((node) => (
              <li key={node.name}>
                <code>{node.name}</code>
              </li>
            ))}
          </ul>
        </div>
      )}

      {globals.length > 0 && (
        <div className="panel panel--error">
          <h4>전역 설정 변경 {globals.length}건</h4>
          <ul className="small">
            {globals.map((change) => (
              <li key={`${change.node}:${change.param}`}>
                <strong>{change.param}</strong> → <code>{change.globalRef}</code>
              </li>
            ))}
          </ul>
          <p>
            전역 값은 모든 unit 의 parameter file 에 포함되므로 <strong>센서 스택
            전체가 일괄 재시작된다.</strong> 전역 값은 노드 간 일관성이 전제이므로
            순차 재시작으로 중간 상태를 만들지 않는다.
          </p>
        </div>
      )}

      {changes.length > 0 && (
        <>
          <label className="tray__comment">
            코멘트 (감사 로그와 git commit 메시지에 들어간다)
            <input
              type="text"
              value={comment}
              placeholder="변경 이유"
              disabled={busy}
              onChange={(e) => setComment(e.target.value)}
            />
          </label>

          <div className="tray__actions">
            <button type="button" onClick={() => send(true)} disabled={busy}>
              검증만 (dry run)
            </button>
            <button
              type="button"
              className="apply-button"
              onClick={() => send(false)}
              disabled={busy || applyBlocked}
              title={applyBlocked ? (safety?.reason ?? '적용이 잠겨 있다') : undefined}
            >
              {busy ? '적용 중…' : '적용'}
            </button>
          </div>
          {applyBlocked && <p className="error small">{safety?.reason}</p>}
        </>
      )}

      <ChangeSetResultView result={result} liveStage={busy ? liveStage : null} />
    </aside>
  )
}
