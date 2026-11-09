/** 파라미터 편집 화면 (명세 §8.1, §8.2). */

import { useNodeParams, useSafety } from '../api/client'
import { OnboardingPanel } from './OnboardingPanel'
import { ParamField } from './ParamField'
import type { ParamDTO } from '../api/types'

export function ParamEditor({ node }: { node: string }) {
  const { data, isLoading, error } = useNodeParams(node)
  const { data: safety } = useSafety()

  if (isLoading) return <p className="muted">파라미터를 수집하는 중…</p>
  if (error) return <p className="error">{String(error)}</p>
  if (!data) return null

  // 주행 중이거나 권한이 없으면 적용 버튼을 비활성화하고 사유를 표시한다 (§8.2).
  // 등록되지 않은 노드는 저장할 parameter file 이 없어 애초에 편집할 수 없다.
  const lockReason = !data.node.registered
    ? 'services.yaml 에 등록되지 않아 변경을 저장할 곳이 없다.'
    : safety && !safety.apply_allowed
      ? safety.reason
      : null

  // hidden 등급은 UI 에 노출하지 않는다 (§4.3).
  const visible = data.params.filter((p) => p.tier !== 'hidden')
  const groups = groupByPrefix(visible)

  return (
    <div className="stack">
      <section className="panel">
        <h2>
          <code>{data.node.name}</code>
        </h2>
        <dl className="node-card__meta">
          <div>
            <dt>unit</dt>
            <dd>{data.node.unit ?? '—'}</dd>
          </div>
          <div>
            <dt>parameter file</dt>
            <dd>
              {data.node.param_files.length === 0
                ? '—'
                : data.node.param_files.join(' → ')}
            </dd>
          </div>
          <div>
            <dt>drift</dt>
            <dd className={data.node.drift_count > 0 ? 'warn' : undefined}>
              {data.node.drift_count}
            </dd>
          </div>
        </dl>
        {data.node.param_files.length > 1 && (
          <p className="muted small">
            저장값은 위 파일을 왼쪽에서 오른쪽 순서로 머지한 결과다. 뒤 파일이 앞을
            덮는다.
          </p>
        )}
        {data.node.display_mode === 'raw' && (
          <p className="warn">
            스키마가 없어 원본 모드로 표시한다. 적용 방식은 모두 restart 로 간주한다.
          </p>
        )}
        {lockReason && (
          <p className="error">
            <strong>편집 잠김</strong> — {lockReason} 아래 필드는 모두 읽기 전용이다.
          </p>
        )}
      </section>

      <OnboardingPanel node={data.node} />

      {[...groups.entries()].map(([group, params]) => (
        <section key={group} className="panel">
          <h3>{group}</h3>
          {params.map((param) => (
            <ParamField
              key={param.name}
              node={data.node}
              param={param}
              lockReason={lockReason}
            />
          ))}
        </section>
      ))}
    </div>
  )
}

/** 점 표기 이름의 첫 조각으로 묶는다. device.* 가 한 그룹이 된다. */
function groupByPrefix(params: ParamDTO[]): Map<string, ParamDTO[]> {
  const groups = new Map<string, ParamDTO[]>()
  for (const param of params) {
    const dot = param.name.indexOf('.')
    const key = dot > 0 ? param.name.slice(0, dot) : '일반'
    const list = groups.get(key) ?? []
    list.push(param)
    groups.set(key, list)
  }
  return groups
}
