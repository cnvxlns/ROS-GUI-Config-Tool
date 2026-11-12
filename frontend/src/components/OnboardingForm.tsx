/**
 * 초안의 TODO 자리를 채우는 폼 (명세 §12.2).
 *
 * ROS 가 알려줄 수 없는 것만 묻는다. 계층·적용 방식·등급은 드라이버 문서를 봐야
 * 알 수 있고, 기대 주기는 지금 측정값이 아니라 **공칭값**이라 사람이 넣어야 한다.
 *
 * 자유 입력이 아니라 드롭다운으로 받는 이유는, 복사·붙여 넣기로는 TODO 를 지우지
 * 않고 넘어가는 것을 막을 방법이 없기 때문이다. 고르지 않으면 초안에 TODO 가 그대로
 * 남고 뱃지가 0 이 되지 않는다.
 */

import {
  APPLY_METHODS,
  LAYERS,
  TIERS,
  type ApplyMethod,
  type Layer,
  type OnboardingChoices,
  type Tier,
} from '../api/types'

export function OnboardingForm({
  topics,
  editableParams,
  discoveredServices,
  choices,
  onChange,
}: {
  topics: string[]
  editableParams: string[]
  /** 자동 감지된 드라이버 전용 서비스. 비어 있으면 사람이 확인해야 한다. */
  discoveredServices: boolean
  choices: OnboardingChoices
  onChange: (next: OnboardingChoices) => void
}) {
  const patch = (part: Partial<OnboardingChoices>) =>
    onChange({ ...choices, ...part })

  const patchParam = (name: string, part: Record<string, unknown>) =>
    onChange({
      ...choices,
      params: {
        ...choices.params,
        [name]: { ...choices.params?.[name], ...part },
      },
    })

  const isDevice = choices.layer === 'device'

  return (
    <div className="onboarding__form stack">
      <h4>채워야 할 자리</h4>
      <p className="small muted">
        ROS 가 알려줄 수 없는 것만 묻는다. 고른 값은 아래 초안에 바로 반영된다.
        저장하지는 않는다 — 복사하거나 내려받아 리포에 커밋한다.
      </p>

      <fieldset className="onboarding__fieldset">
        <legend>서브시스템 (services.yaml)</legend>

        <label className="field">
          <span>launch 명령</span>
          <input
            type="text"
            value={choices.launch ?? ''}
            placeholder="ros2 launch av_bringup lidar_top.launch.py"
            onChange={(event) =>
              patch({ launch: event.target.value || null })
            }
          />
        </label>

        {topics.length > 0 ? (
          <div className="field">
            <span>
              기대 주기 <span className="small muted">(공칭값. 지금 측정값이 아니다)</span>
            </span>
            {topics.map((topic) => (
              <label key={topic} className="field field--inline">
                <code>{topic}</code>
                <input
                  type="number"
                  min={0}
                  step="0.1"
                  value={choices.topic_hz?.[topic] ?? ''}
                  placeholder="0.0"
                  onChange={(event) => {
                    const next = { ...(choices.topic_hz ?? {}) }
                    if (event.target.value === '') delete next[topic]
                    else next[topic] = Number(event.target.value)
                    patch({ topic_hz: next })
                  }}
                />
                <span className="small muted">Hz</span>
              </label>
            ))}
          </div>
        ) : (
          <label className="field field--inline">
            <input
              type="checkbox"
              checked={choices.topic_hz !== null && choices.topic_hz !== undefined}
              onChange={(event) =>
                patch({ topic_hz: event.target.checked ? {} : null })
              }
            />
            <span>
              감시할 토픽이 없다 <span className="small muted">(확인했다)</span>
            </span>
          </label>
        )}
      </fieldset>

      <fieldset className="onboarding__fieldset">
        <legend>스키마</legend>

        <label className="field">
          <span>드라이버 패키지 이름</span>
          <input
            type="text"
            value={choices.driver ?? ''}
            placeholder="ouster_ros"
            onChange={(event) => patch({ driver: event.target.value || null })}
          />
        </label>

        {!discoveredServices && (
          <label className="field field--inline">
            <input
              type="checkbox"
              checked={choices.required_services !== null && choices.required_services !== undefined}
              onChange={(event) =>
                patch({ required_services: event.target.checked ? [] : null })
              }
            />
            <span>
              드라이버 전용 서비스가 없다{' '}
              <span className="small muted">(ros2 service list 로 확인했다)</span>
            </span>
          </label>
        )}

        <div className="field">
          <span>
            모든 파라미터의 기본값{' '}
            <span className="small muted">예외는 아래에서 덮는다</span>
          </span>
          <div className="onboarding__defaults">
            <Pick
              label="계층"
              options={LAYERS}
              value={choices.layer ?? ''}
              onPick={(value) => patch({ layer: (value || null) as Layer | null })}
            />
            <Pick
              label="적용"
              options={APPLY_METHODS}
              value={choices.apply ?? ''}
              onPick={(value) =>
                patch({ apply: (value || null) as ApplyMethod | null })
              }
            />
            <Pick
              label="등급"
              options={TIERS}
              value={choices.tier ?? ''}
              onPick={(value) =>
                patch({ tier: (value || null) as Tier | null })
              }
            />
          </div>
        </div>

        {isDevice && (
          <label className="field">
            <span>
              조회 서비스 <span className="small muted">device 계층은 필수다 (§4.4)</span>
            </span>
            <input
              type="text"
              value={choices.readback_service ?? ''}
              placeholder="/ouster/get_config"
              onChange={(event) =>
                patch({ readback_service: event.target.value || null })
              }
            />
            <span className="small muted">
              장치가 값을 거부하거나 보정한 경우를 감지하려면 노드 파라미터가 아니라
              장치에서 되읽어야 한다.
            </span>
          </label>
        )}
      </fieldset>

      {editableParams.length > 0 && (
        <details className="onboarding__overrides">
          <summary>
            파라미터별 예외 ({editableParams.length}개 중{' '}
            {Object.keys(choices.params ?? {}).length}개 지정)
          </summary>
          <table className="onboarding__table">
            <thead>
              <tr>
                <th>파라미터</th>
                <th>라벨</th>
                <th>계층</th>
                <th>적용</th>
                <th>등급</th>
                {isDevice && <th>json_path</th>}
              </tr>
            </thead>
            <tbody>
              {editableParams.map((name) => {
                const pick = choices.params?.[name] ?? {}
                return (
                  <tr key={name}>
                    <td>
                      <code>{name}</code>
                    </td>
                    <td>
                      <input
                        type="text"
                        value={pick.label ?? ''}
                        placeholder="이름 그대로"
                        onChange={(event) =>
                          patchParam(name, { label: event.target.value || null })
                        }
                      />
                    </td>
                    <td>
                      <Pick
                        options={LAYERS}
                        value={pick.layer ?? ''}
                        placeholder="기본값"
                        onPick={(value) => patchParam(name, { layer: value || null })}
                      />
                    </td>
                    <td>
                      <Pick
                        options={APPLY_METHODS}
                        value={pick.apply ?? ''}
                        placeholder="기본값"
                        onPick={(value) => patchParam(name, { apply: value || null })}
                      />
                    </td>
                    <td>
                      <Pick
                        options={TIERS}
                        value={pick.tier ?? ''}
                        placeholder="기본값"
                        onPick={(value) => patchParam(name, { tier: value || null })}
                      />
                    </td>
                    {isDevice && (
                      <td>
                        <input
                          type="text"
                          value={pick.readback_json_path ?? ''}
                          placeholder={`$.${name}`}
                          onChange={(event) =>
                            patchParam(name, {
                              readback_json_path: event.target.value || null,
                            })
                          }
                        />
                      </td>
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </details>
      )}
    </div>
  )
}

function Pick({
  label,
  options,
  value,
  placeholder = '고르지 않음',
  onPick,
}: {
  label?: string
  options: readonly string[]
  value: string
  placeholder?: string
  onPick: (value: string) => void
}) {
  const select = (
    <select value={value} onChange={(event) => onPick(event.target.value)}>
      <option value="">{placeholder}</option>
      {options.map((option) => (
        <option key={option} value={option}>
          {option}
        </option>
      ))}
    </select>
  )
  if (!label) return select
  return (
    <label className="field field--inline">
      <span>{label}</span>
      {select}
    </label>
  )
}
