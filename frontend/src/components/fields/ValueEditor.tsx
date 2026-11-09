/**
 * ROS 2 파라미터 타입별 값 편집기 (명세 §8.3).
 *
 * ParameterType 은 9종으로 닫혀 있으므로 여기서 전부 분기한다. JSON Schema 폼
 * 생성기를 쓰지 않는 이유가 이것이다 — 처리할 타입이 유한하고 평면적이다.
 */

import { useState } from 'react'
import { ParamType, type ParamDTO, type ParamValue } from '../../api/types'

interface Props {
  param: ParamDTO
  value: ParamValue
  disabled: boolean
  onChange: (value: ParamValue) => void
}

export function ValueEditor({ param, value, disabled, onChange }: Props) {
  switch (param.type) {
    case ParamType.BOOL:
      return <BoolField value={value} disabled={disabled} onChange={onChange} />
    case ParamType.INTEGER:
      return (
        <NumberField
          param={param}
          value={value}
          disabled={disabled}
          onChange={onChange}
          integer
        />
      )
    case ParamType.DOUBLE:
      return (
        <NumberField
          param={param}
          value={value}
          disabled={disabled}
          onChange={onChange}
          integer={false}
        />
      )
    case ParamType.STRING:
      return <StringField param={param} value={value} disabled={disabled} onChange={onChange} />
    case ParamType.BOOL_ARRAY:
      return <BoolArrayField value={value} disabled={disabled} onChange={onChange} />
    case ParamType.INTEGER_ARRAY:
      return <NumberArrayField value={value} disabled={disabled} onChange={onChange} integer />
    case ParamType.DOUBLE_ARRAY:
      return (
        <NumberArrayField value={value} disabled={disabled} onChange={onChange} integer={false} />
      )
    case ParamType.STRING_ARRAY:
      return <StringArrayField value={value} disabled={disabled} onChange={onChange} />
    case ParamType.BYTE_ARRAY:
      return <ByteArrayField value={value} />
    default:
      return (
        <span className="field-note">
          노드가 이 파라미터를 선언하지 않았다. 편집할 수 없다.
        </span>
      )
  }
}

// --------------------------------------------------------------------------

function BoolField({
  value,
  disabled,
  onChange,
}: Pick<Props, 'value' | 'disabled' | 'onChange'>) {
  return (
    <label className="bool-field">
      <input
        type="checkbox"
        checked={value === true}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>{value === true ? 'true' : 'false'}</span>
    </label>
  )
}

function NumberField({
  param,
  value,
  disabled,
  onChange,
  integer,
}: Props & { integer: boolean }) {
  const range = integer ? param.integer_range : param.floating_point_range
  const [text, setText] = useState(value === null ? '' : String(value))
  const [error, setError] = useState<string | null>(null)

  const commit = (raw: string) => {
    setText(raw)
    if (raw.trim() === '') {
      setError('값이 비어 있다')
      return
    }
    const parsed = integer ? Number.parseInt(raw, 10) : Number.parseFloat(raw)
    if (Number.isNaN(parsed)) {
      setError(integer ? '정수가 아니다' : '숫자가 아니다')
      return
    }
    if (range && (parsed < range.from_value || parsed > range.to_value)) {
      setError(`허용 범위 ${range.from_value} ~ ${range.to_value}`)
      return
    }
    setError(null)
    onChange(parsed)
  }

  return (
    <div className="number-field">
      <input
        type="number"
        value={text}
        disabled={disabled}
        step={range?.step || (integer ? 1 : 'any')}
        min={range?.from_value}
        max={range?.to_value}
        onChange={(e) => commit(e.target.value)}
      />
      {range && (
        <input
          type="range"
          value={Number(text) || range.from_value}
          disabled={disabled}
          min={range.from_value}
          max={range.to_value}
          step={range.step || (integer ? 1 : (range.to_value - range.from_value) / 100)}
          onChange={(e) => commit(e.target.value)}
        />
      )}
      {error && <span className="field-error">{error}</span>}
    </div>
  )
}

function StringField({ param, value, disabled, onChange }: Props) {
  // enum 이 있으면 자유 입력을 막는다. 허용 값은 센서 모델과 펌웨어에 따라
  // 다르므로 스키마가 선언한 목록만 고를 수 있게 한다 (§4.4).
  if (param.enum_values && param.enum_values.length > 0) {
    return (
      <select
        value={value === null ? '' : String(value)}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      >
        {value === null && <option value="">— 선택 —</option>}
        {param.enum_values.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    )
  }
  return (
    <input
      type="text"
      value={value === null ? '' : String(value)}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}

function BoolArrayField({
  value,
  disabled,
  onChange,
}: Pick<Props, 'value' | 'disabled' | 'onChange'>) {
  const items = Array.isArray(value) ? (value as boolean[]) : []
  const update = (index: number, next: boolean) => {
    const copy = [...items]
    copy[index] = next
    onChange(copy)
  }
  return (
    <div className="array-field">
      {items.length === 0 && <span className="field-note">빈 배열</span>}
      {items.map((item, index) => (
        <label key={index} className="bool-field">
          <input
            type="checkbox"
            checked={item === true}
            disabled={disabled}
            onChange={(e) => update(index, e.target.checked)}
          />
          <span>[{index}]</span>
        </label>
      ))}
      <button type="button" disabled={disabled} onClick={() => onChange([...items, false])}>
        + 추가
      </button>
      {items.length > 0 && (
        <button type="button" disabled={disabled} onClick={() => onChange(items.slice(0, -1))}>
          − 마지막 삭제
        </button>
      )}
    </div>
  )
}

function NumberArrayField({
  value,
  disabled,
  onChange,
  integer,
}: Pick<Props, 'value' | 'disabled' | 'onChange'> & { integer: boolean }) {
  const initial = Array.isArray(value) ? value.join(', ') : ''
  const [text, setText] = useState(initial)
  const [error, setError] = useState<string | null>(null)

  const commit = (raw: string) => {
    setText(raw)
    const trimmed = raw.trim()
    if (trimmed === '') {
      setError(null)
      onChange([])
      return
    }
    const parts = trimmed.split(',').map((s) => s.trim())
    const parsed: number[] = []
    for (const part of parts) {
      const num = integer ? Number.parseInt(part, 10) : Number.parseFloat(part)
      if (Number.isNaN(num) || (integer && !/^-?\d+$/.test(part))) {
        setError(`"${part}" 는 ${integer ? '정수' : '숫자'}가 아니다`)
        return
      }
      parsed.push(num)
    }
    setError(null)
    onChange(parsed)
  }

  return (
    <div className="number-field">
      <input
        type="text"
        value={text}
        disabled={disabled}
        placeholder="쉼표로 구분"
        onChange={(e) => commit(e.target.value)}
      />
      {error && <span className="field-error">{error}</span>}
    </div>
  )
}

function StringArrayField({
  value,
  disabled,
  onChange,
}: Pick<Props, 'value' | 'disabled' | 'onChange'>) {
  const initial = Array.isArray(value) ? (value as string[]).join('\n') : ''
  const [text, setText] = useState(initial)
  return (
    <textarea
      rows={Math.min(8, Math.max(2, text.split('\n').length))}
      value={text}
      disabled={disabled}
      placeholder="한 줄에 하나"
      onChange={(e) => {
        setText(e.target.value)
        const lines = e.target.value.split('\n').filter((line) => line.trim() !== '')
        onChange(lines)
      }}
    />
  )
}

function ByteArrayField({ value }: Pick<Props, 'value'>) {
  const items = Array.isArray(value) ? (value as number[]) : []
  // byte_array 를 UI 에서 손으로 고치는 것은 의미가 없다. 표시만 한다.
  const preview = items
    .slice(0, 16)
    .map((b) => b.toString(16).padStart(2, '0'))
    .join(' ')
  return (
    <span className="field-note">
      {items.length}바이트{items.length > 0 && <code> {preview}{items.length > 16 ? ' …' : ''}</code>}
      {' '}(읽기 전용)
    </span>
  )
}
