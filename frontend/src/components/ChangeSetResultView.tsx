/** 적용 진행과 결과 (명세 §8.1 적용 진행). */

import {
  STAGE_LABEL,
  STATUS_LABEL,
  formatValue,
  type ChangeSetResult,
} from '../api/types'

const STAGES = ['validate', 'plan', 'save', 'apply', 'verify'] as const

export function ChangeSetResultView({
  result,
  liveStage,
}: {
  result: ChangeSetResult | null
  liveStage: string | null
}) {
  if (!result && !liveStage) return null

  const status = result?.status
  const tone =
    status === 'succeeded' || status === 'dry_run'
      ? 'panel'
      : status === 'rollback_failed'
        ? 'panel panel--error'
        : 'panel panel--warn'

  const currentStage = result?.stage ?? liveStage ?? 'validate'
  const reached = STAGES.indexOf(currentStage as (typeof STAGES)[number])

  return (
    <div className={tone}>
      <h4>
        {status ? STATUS_LABEL[status] : `진행 중 — ${STAGE_LABEL[currentStage] ?? currentStage}`}
      </h4>

      <ol className="stages">
        {STAGES.map((stage, index) => (
          <li
            key={stage}
            className={
              reached < 0
                ? ''
                : index < reached
                  ? 'stage stage--done'
                  : index === reached
                    ? 'stage stage--current'
                    : 'stage'
            }
          >
            {STAGE_LABEL[stage]}
          </li>
        ))}
      </ol>

      {result?.message && <p>{result.message}</p>}

      {result && result.issues.length > 0 && (
        <>
          <h5>검증 실패 {result.issues.length}건</h5>
          <ul className="small">
            {result.issues.map((issue, index) => (
              <li key={index}>
                <code>{issue.param ?? issue.node}</code> — {issue.message}
                <span className="muted"> ({issue.code})</span>
              </li>
            ))}
          </ul>
        </>
      )}

      {result && result.results.length > 0 && (
        <>
          <h5>반영 확인</h5>
          <table className="diff">
            <thead>
              <tr>
                <th>대상</th>
                <th>요청</th>
                <th>실제</th>
                <th>출처</th>
                <th>결과</th>
              </tr>
            </thead>
            <tbody>
              {result.results.map((entry, index) => (
                <tr key={index}>
                  <td>
                    <code className="small">{entry.node}</code>
                    <br />
                    <strong>{entry.param}</strong>
                  </td>
                  <td className="mono">{formatValue(entry.requested)}</td>
                  <td className="mono">{formatValue(entry.actual)}</td>
                  <td className="small muted">{entry.readback_source ?? '—'}</td>
                  <td>
                    {entry.success ? (
                      <span className="badge badge--info">통과</span>
                    ) : (
                      <span className="badge badge--danger" title={entry.reason ?? ''}>
                        실패
                      </span>
                    )}
                    {entry.reason && <p className="small error">{entry.reason}</p>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {result?.commit && (
        <p className="small muted">
          commit <code>{result.commit.slice(0, 10)}</code>
          {result.rollback_commit && (
            <>
              {' → revert '}
              <code>{result.rollback_commit.slice(0, 10)}</code>
            </>
          )}
        </p>
      )}
    </div>
  )
}
