/** 노드 목록 화면 (명세 §8.1). */

import { Link } from '@tanstack/react-router'
import { useNodes } from '../api/client'
import type { NodeDTO } from '../api/types'

export function NodeList() {
  const { data, isLoading, error } = useNodes()

  if (isLoading) return <p className="muted">노드를 수집하는 중…</p>
  if (error) return <p className="error">노드 목록 조회 실패: {String(error)}</p>
  if (!data) return null

  const bySubsystem = new Map<string, NodeDTO[]>()
  for (const node of data.nodes) {
    const key = node.subsystem ?? '미등록 노드'
    const list = bySubsystem.get(key) ?? []
    list.push(node)
    bySubsystem.set(key, list)
  }

  return (
    <div className="stack">
      {data.schema_errors.length > 0 && (
        <div className="panel panel--error">
          <h3>스키마 로드 실패 {data.schema_errors.length}건</h3>
          <p className="muted">
            해당 드라이버는 원본 모드로 떨어진다. 스키마를 고치기 전까지 적용 방식과
            등급을 신뢰할 수 없다.
          </p>
          <ul>
            {data.schema_errors.map((err) => (
              <li key={err.file}>
                <code>{err.file}</code> — {err.error}
              </li>
            ))}
          </ul>
        </div>
      )}

      {[...bySubsystem.entries()].map(([subsystem, nodes]) => (
        <section
          key={subsystem}
          className={`panel${nodes.some((n) => !n.registered) ? ' panel--warn' : ''}`}
        >
          <h2>{subsystem}</h2>
          {nodes.some((n) => !n.registered) && (
            <p className="muted">
              services.yaml 에 등록되지 않았다. 노드를 열면 등록 방법과 설정 파일
              초안을 볼 수 있다.
            </p>
          )}
          {nodes[0]?.unit && (
            <p className="muted">
              unit <code>{nodes[0].unit}</code> — 재시작 시 이 unit 의 노드{' '}
              {nodes.length}개가 함께 중단된다
            </p>
          )}
          <div className="node-grid">
            {nodes.map((node) => (
              <NodeCard key={node.name} node={node} />
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

function NodeCard({ node }: { node: NodeDTO }) {
  return (
    <Link to="/nodes/$node" params={{ node: node.name.replace(/^\//, '') }} className="node-card">
      <div className="node-card__head">
        <code>{node.name}</code>
        <span className={`dot ${node.alive ? 'dot--ok' : 'dot--off'}`} title={node.alive ? '실행 중' : '정지'} />
      </div>

      {!node.registered && (
        <p className="node-card__badge">
          미등록 — services.yaml 에 없어 읽기 전용이다
        </p>
      )}

      <dl className="node-card__meta">
        <div>
          <dt>표시</dt>
          <dd>
            {node.display_mode === 'mapped' ? (
              <>매핑 ({node.schema_driver})</>
            ) : (
              <span className="warn">원본</span>
            )}
          </dd>
        </div>
        <div>
          <dt>파라미터</dt>
          <dd>{node.param_count}</dd>
        </div>
        <div>
          <dt>drift</dt>
          <dd className={node.drift_count > 0 ? 'warn' : undefined}>{node.drift_count}</dd>
        </div>
      </dl>

      {node.launch_rule_violation && (
        <p className="node-card__alert">
          launch 규칙 위반 — 인라인 parameters 가 있어 Config App 이 해소할 수 없다
        </p>
      )}
      {node.unreachable_reason && <p className="node-card__alert">{node.unreachable_reason}</p>}
      {node.downstream.length > 0 && (
        <p className="muted small">다운스트림 {node.downstream.length}개</p>
      )}
    </Link>
  )
}
