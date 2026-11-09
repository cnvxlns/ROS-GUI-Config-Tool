/**
 * 파라미터 한 줄 (명세 §8.1 파라미터 편집, §8.2 UX 규칙).
 *
 * 필드 하나가 저장값, 런타임값, drift 경고, 적용 방식, 등급, 범위, 설명을 함께
 * 담는다. 이것이 폼 자동 생성 라이브러리를 쓰지 않는 이유 중 하나다 (§8.3).
 */

import { useChangeSet } from '../api/changeset'
import {
  APPLY_LABEL,
  APPLY_SEVERITY,
  TYPE_LABEL,
  formatValue,
  isEditable,
  type NodeDTO,
  type ParamDTO,
} from '../api/types'
import { ValueEditor } from './fields/ValueEditor'

interface Props {
  node: NodeDTO
  param: ParamDTO
  /**
   * 노드 전체에 걸린 잠금 사유(주행 중, 미등록 등). null 이면 노드 단위 잠금은 없다.
   *
   * 이 사유는 필드마다 반복하지 않는다. 같은 문장이 수십 번 나오면 읽지 않게 되고,
   * 정작 파라미터별 사유(read_only, 등급)가 묻힌다. 화면 위 안내가 한 번 알려주고,
   * 여기서는 입력을 비활성화하고 title 로만 남긴다.
   */
  lockReason: string | null
}

export function ParamField({ node, param, lockReason }: Props) {
  const pending = useChangeSet((s) => s.find(node.name, param.name))
  const setChange = useChangeSet((s) => s.setChange)
  const removeChange = useChangeSet((s) => s.removeChange)

  const editable = isEditable(param) && lockReason === null
  const current = pending ? pending.value : param.runtime_value
  const severity = APPLY_SEVERITY[param.apply]

  // 파라미터 자체의 사유. 노드 전체 잠금과 구분해 이것만 본문에 적는다.
  const paramLockReason = param.read_only
    ? '드라이버가 read_only 로 선언한 값이다. 표시만 한다.'
    : !isEditable(param)
      ? '이 등급은 편집할 수 없다.'
      : null

  return (
    <div
      className={`param-row${pending ? ' param-row--pending' : ''}`}
      title={!editable && paramLockReason === null ? (lockReason ?? undefined) : undefined}
    >
      <div className="param-head">
        <div className="param-name">
          <strong>{param.label ?? param.name}</strong>
          {param.label && <code className="param-key">{param.name}</code>}
        </div>

        <div className="param-badges">
          <span className="badge badge--type">{TYPE_LABEL[param.type] ?? '?'}</span>
          {param.layer && <span className="badge badge--layer">{param.layer}</span>}
          <span className={`badge badge--${severity}`} title={applyHint(param.apply)}>
            {APPLY_LABEL[param.apply]}
          </span>
          <span className="badge badge--tier">{param.tier}</span>
          {!param.has_schema && (
            <span className="badge badge--warn" title="스키마가 없어 원본 모드로 표시된다">
              원본
            </span>
          )}
          {param.global_ref && (
            <span
              className="badge badge--danger"
              title={`전역 설정 ${param.global_ref} 를 참조한다. 변경하면 센서 스택 전체가 재시작된다.`}
            >
              전역
            </span>
          )}
        </div>
      </div>

      {param.description && <p className="param-desc">{param.description}</p>}

      <div className="param-body">
        <ValueEditor
          param={param}
          value={current}
          disabled={!editable}
          onChange={(value) =>
            setChange({
              node: node.name,
              param: param.name,
              before: param.runtime_value,
              value,
              apply: param.apply,
              unit: node.unit,
              globalRef: param.global_ref,
            })
          }
        />

        <dl className="param-values">
          <div>
            <dt>런타임</dt>
            <dd>{formatValue(param.runtime_value)}</dd>
          </div>
          <div>
            <dt>저장</dt>
            <dd>
              {formatValue(param.saved_value)}
              {param.saved_source && <small> ({param.saved_source})</small>}
            </dd>
          </div>
        </dl>
      </div>

      {param.drift && (
        <p className="param-drift">
          저장값과 런타임값이 다르다. 저장값은 parameter file 머지 결과다.
          {param.type === 0 &&
            ' 노드가 이 파라미터를 선언하지 않아 YAML 값이 적용되지 않고 있다.'}
        </p>
      )}

      {paramLockReason && <p className="param-locked">{paramLockReason}</p>}

      {pending && (
        <p className="param-pending">
          변경 대기: {formatValue(pending.before)} → {formatValue(pending.value)}
          <button type="button" onClick={() => removeChange(node.name, param.name)}>
            취소
          </button>
        </p>
      )}
    </div>
  )
}

function applyHint(apply: ParamDTO['apply']): string {
  switch (apply) {
    case 'runtime':
      return '실행 중 즉시 반영된다'
    case 'reconfigure':
      return '노드는 유지하고 장치를 재설정한다'
    case 'restart':
      return '적용 시 이 노드가 속한 unit 전체가 재시작된다'
  }
}
