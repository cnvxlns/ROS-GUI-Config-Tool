/**
 * 신규 센서 온보딩 안내 (명세 §12.2).
 *
 * 자동 수집으로 얻은 정보로 설정 파일 초안을 만들어 보여준다. 계층, 적용 방식,
 * 등급은 ROS 가 알려줄 수 없으므로 TODO 로 남고 사람이 채워야 한다.
 */

import { useMemo, useState } from 'react'
import { useOnboarding, useOnboardingDraft } from '../api/client'
import type { NodeDTO, OnboardingChoices, OnboardingDTO } from '../api/types'
import { OnboardingForm } from './OnboardingForm'

export function OnboardingPanel({ node }: { node: NodeDTO }) {
  // 완전히 등록된 노드에는 띄우지 않는다
  const needed = !node.registered || node.schema_driver === null
  const [open, setOpen] = useState(!node.registered)
  const [choices, setChoices] = useState<OnboardingChoices>({})

  // 아무것도 고르지 않았으면 GET 을 쓴다. 빈 POST 와 결과가 같지만, 폼을 열지
  // 않은 사용자가 요청을 한 번 더 보낼 이유가 없다.
  const answered = useMemo(() => hasAnswer(choices), [choices])
  const base = useOnboarding(node.name, needed && open && !answered)
  const filled = useOnboardingDraft(node.name, choices, needed && open && answered)

  // 첫 선택을 하는 순간 GET 에서 POST 로 넘어간다. 그때 새 질의에는 아직 결과가
  // 없으므로 data 가 잠깐 undefined 가 되는데, 그대로 두면 폼이 통째로 사라졌다가
  // 다시 그려진다 — 입력 포커스가 날아가서 한 글자밖에 못 친다. 그래서 직전
  // 초안을 계속 보여준다.
  const data = filled.data ?? base.data
  const isLoading = data === undefined && (filled.isLoading || base.isLoading)
  const error = answered ? filled.error : base.error

  if (!needed) return null

  return (
    <section className={`panel ${node.registered ? 'panel--warn' : 'panel--error'}`}>
      <div className="onboarding__head">
        <h3>
          {node.registered
            ? '스키마가 없는 노드다'
            : 'services.yaml 에 등록되지 않은 노드다'}
        </h3>
        <button type="button" onClick={() => setOpen((v) => !v)}>
          {open ? '접기' : '등록 방법 보기'}
        </button>
      </div>

      <p>
        {node.registered ? (
          <>
            모든 파라미터가 <code>restart</code> / <code>engineer</code> 로 간주되고,
            장치 계층 반영 여부를 검증할 수 없다. 스키마를 추가하면 적용 방식과 등급이
            정확해진다.
          </>
        ) : (
          <>
            변경을 저장할 parameter file 이 없어 <strong>이 노드는 읽기 전용이다.</strong>{' '}
            아래 초안으로 등록하면 편집할 수 있다.
          </>
        )}
      </p>

      {open && isLoading && <p className="muted">초안을 만드는 중…</p>}
      {open && error && <p className="error">초안 생성 실패: {String(error)}</p>}
      {open && data && (
        <OnboardingBody data={data} choices={choices} onChange={setChoices} />
      )}
    </section>
  )
}

/** 사용자가 한 자리라도 골랐는지. 고르기 전에는 POST 할 이유가 없다. */
function hasAnswer(choices: OnboardingChoices): boolean {
  return Object.values(choices).some((value) => {
    if (value === null || value === undefined) return false
    if (typeof value === 'object') return true // {} 도 "확인했다"는 답이다
    return value !== ''
  })
}

function OnboardingBody({
  data,
  choices,
  onChange,
}: {
  data: OnboardingDTO
  choices: OnboardingChoices
  onChange: (next: OnboardingChoices) => void
}) {
  return (
    <div className="stack">
      {data.notes.length > 0 && (
        <ul className="onboarding__notes">
          {data.notes.map((note, index) => (
            <li key={index}>{note}</li>
          ))}
        </ul>
      )}

      <div>
        <h4>절차 (명세 §12.2)</h4>
        <ol className="onboarding__steps">
          {data.steps.map((step) => (
            <li key={step.number} className={`step step--${stateOf(step.done)}`}>
              <span className="step__mark" aria-hidden="true">
                {step.done === true ? '✓' : step.done === false ? '○' : '·'}
              </span>
              <div>
                <strong>{step.title}</strong>
                <p className="small muted">{step.detail}</p>
              </div>
            </li>
          ))}
        </ol>
        <p className="small muted">
          · 표시는 Config App 이 판단할 수 없는 단계다. 사람이 확인해야 한다.
        </p>
      </div>

      <OnboardingForm
        topics={data.topics}
        editableParams={data.editable_params}
        discoveredServices={data.schema_draft.includes('required_services:\n')}
        choices={choices}
        onChange={onChange}
      />

      {data.todo_count > 0 ? (
        <p className="draft__warning">
          채우지 않은 자리가 {data.todo_count}개 남았다. 이대로 붙여 넣으면 스키마
          로드가 거부되거나, 적용 방식이 틀린 채로 "적용됨"이 표시된다.
        </p>
      ) : (
        <p className="small muted">
          채워야 할 자리를 모두 채웠다. 아래 초안을 그대로 쓸 수 있다.
        </p>
      )}

      <Draft
        title="1. services.yaml 에 등록"
        filename="services.yaml"
        hint={`subsystems: 아래에 붙여 넣는다`}
        content={data.services_snippet}
      />
      <Draft
        title="2. parameter file 생성"
        filename={data.param_file.split('/').pop() ?? 'params.yaml'}
        hint={`config/${data.param_file} 로 저장한다`}
        content={data.param_file_draft}
      />
      <Draft
        title="3. 스키마 작성"
        filename={data.schema_file.split('/').pop() ?? 'schema.yaml'}
        hint={`config/${data.schema_file} 로 저장하고 TODO 를 채운다`}
        content={data.schema_draft}
        warning="layer / apply / tier 는 ROS 가 알려줄 수 없다. 드라이버 문서와 장치 명세를 보고 채워야 한다."
      />

      <p className="small muted">
        초안을 반영한 뒤 노드 목록에서 새로고침하면 매핑 모드로 바뀐다. systemd 유닛과
        launch 파일은 별도로 만들어야 한다.
      </p>
    </div>
  )
}

function Draft({
  title,
  filename,
  hint,
  content,
  warning,
}: {
  title: string
  filename: string
  hint: string
  content: string
  warning?: string
}) {
  const [copied, setCopied] = useState(false)
  // 안내 주석에도 TODO 라는 낱말이 들어가므로, 머리말을 뺀 본문만 센다.
  // 서버의 count_todo 와 같은 규칙이다 (onboarding.py 의 _with_header 참고).
  const todoCount = content
    .split('\n')
    .filter((line) => !line.trimStart().startsWith('#'))
    .join('\n')
    .split('TODO').length - 1

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(content)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      // 클립보드 권한이 없으면 사용자가 직접 선택해 복사하면 된다
      setCopied(false)
    }
  }

  const download = () => {
    const url = URL.createObjectURL(new Blob([content], { type: 'text/yaml' }))
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    link.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="draft">
      <div className="draft__head">
        <div>
          <strong>{title}</strong>
          <p className="small muted">{hint}</p>
        </div>
        <div className="draft__actions">
          {todoCount > 0 && (
            <span className="badge badge--warn" title="사람이 채워야 하는 자리">
              TODO {todoCount}
            </span>
          )}
          <button type="button" onClick={copy}>
            {copied ? '복사됨' : '복사'}
          </button>
          <button type="button" onClick={download}>
            내려받기
          </button>
        </div>
      </div>
      {warning && <p className="draft__warning">{warning}</p>}
      <pre className="draft__code">
        <code>{content}</code>
      </pre>
    </div>
  )
}

function stateOf(done: boolean | null): string {
  if (done === true) return 'done'
  if (done === false) return 'todo'
  return 'unknown'
}
