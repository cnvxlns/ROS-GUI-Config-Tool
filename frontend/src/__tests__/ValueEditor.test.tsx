/**
 * ROS 2 파라미터 타입 9종이 각각 맞는 입력 요소로 렌더링되는지 확인한다.
 *
 * 폼 자동 생성 라이브러리를 쓰지 않기로 한 근거가 "타입이 9종으로 닫혀 있다"는
 * 것이므로, 9종 전부가 실제로 처리되는지가 그 결정의 전제다.
 */

import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ValueEditor } from '../components/fields/ValueEditor'
import { ParamType } from '../api/types'
import { makeParam } from './factories'

const noop = vi.fn()

describe('ValueEditor — 타입별 렌더링', () => {
  it('BOOL 은 체크박스', () => {
    render(
      <ValueEditor
        param={makeParam({ type: ParamType.BOOL })}
        value={true}
        disabled={false}
        onChange={noop}
      />,
    )
    const box = screen.getByRole('checkbox') as HTMLInputElement
    expect(box.checked).toBe(true)
    expect(screen.getByText('true')).toBeTruthy()
  })

  it('INTEGER 는 숫자 입력', () => {
    render(
      <ValueEditor
        param={makeParam({ type: ParamType.INTEGER })}
        value={42}
        disabled={false}
        onChange={noop}
      />,
    )
    const input = screen.getByRole('spinbutton') as HTMLInputElement
    expect(input.value).toBe('42')
  })

  it('INTEGER 에 범위가 있으면 슬라이더도 함께 낸다', () => {
    render(
      <ValueEditor
        param={makeParam({
          type: ParamType.INTEGER,
          integer_range: { from_value: 1, to_value: 4096, step: 1 },
        })}
        value={2048}
        disabled={false}
        onChange={noop}
      />,
    )
    const slider = screen.getByRole('slider') as HTMLInputElement
    expect(slider.min).toBe('1')
    expect(slider.max).toBe('4096')
  })

  it('DOUBLE 은 범위를 floating_point_range 에서 읽는다', () => {
    render(
      <ValueEditor
        param={makeParam({
          type: ParamType.DOUBLE,
          floating_point_range: { from_value: 0, to_value: 5, step: 0.1 },
        })}
        value={0.3}
        disabled={false}
        onChange={noop}
      />,
    )
    const slider = screen.getByRole('slider') as HTMLInputElement
    expect(slider.max).toBe('5')
  })

  it('STRING 은 텍스트 입력', () => {
    render(
      <ValueEditor
        param={makeParam({ type: ParamType.STRING })}
        value="laser"
        disabled={false}
        onChange={noop}
      />,
    )
    expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe('laser')
  })

  it('STRING 에 enum 이 있으면 자유 입력 대신 select', () => {
    render(
      <ValueEditor
        param={makeParam({
          type: ParamType.STRING,
          enum_values: ['512x10', '1024x10', '2048x10'],
        })}
        value="1024x10"
        disabled={false}
        onChange={noop}
      />,
    )
    const select = screen.getByRole('combobox') as HTMLSelectElement
    expect(select.value).toBe('1024x10')
    expect(screen.getAllByRole('option')).toHaveLength(3)
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it('BOOL_ARRAY 는 요소마다 체크박스', () => {
    render(
      <ValueEditor
        param={makeParam({ type: ParamType.BOOL_ARRAY })}
        value={[true, true, false]}
        disabled={false}
        onChange={noop}
      />,
    )
    const boxes = screen.getAllByRole('checkbox') as HTMLInputElement[]
    expect(boxes).toHaveLength(3)
    expect(boxes.map((b) => b.checked)).toEqual([true, true, false])
  })

  it('INTEGER_ARRAY 는 쉼표 구분 텍스트', () => {
    render(
      <ValueEditor
        param={makeParam({ type: ParamType.INTEGER_ARRAY })}
        value={[0, 360000]}
        disabled={false}
        onChange={noop}
      />,
    )
    expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe('0, 360000')
  })

  it('DOUBLE_ARRAY 도 쉼표 구분 텍스트', () => {
    render(
      <ValueEditor
        param={makeParam({ type: ParamType.DOUBLE_ARRAY })}
        value={[1, 0, 0.5]}
        disabled={false}
        onChange={noop}
      />,
    )
    expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe('1, 0, 0.5')
  })

  it('STRING_ARRAY 는 줄 단위 textarea', () => {
    render(
      <ValueEditor
        param={makeParam({ type: ParamType.STRING_ARRAY })}
        value={['points', 'imu']}
        disabled={false}
        onChange={noop}
      />,
    )
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('points\nimu')
  })

  it('BYTE_ARRAY 는 편집 불가 표시만 한다', () => {
    render(
      <ValueEditor
        param={makeParam({ type: ParamType.BYTE_ARRAY })}
        value={[222, 173, 190, 239]}
        disabled={false}
        onChange={noop}
      />,
    )
    expect(screen.getByText(/4바이트/)).toBeTruthy()
    expect(screen.getByText(/de ad be ef/)).toBeTruthy()
    expect(screen.queryByRole('textbox')).toBeNull()
  })

  it('NOT_SET 은 선언되지 않았다고 알린다', () => {
    render(
      <ValueEditor
        param={makeParam({ type: ParamType.NOT_SET })}
        value={null}
        disabled={false}
        onChange={noop}
      />,
    )
    expect(screen.getByText(/선언하지 않았다/)).toBeTruthy()
  })
})

describe('ValueEditor — 검증과 비활성화', () => {
  it('범위를 벗어난 값은 onChange 를 부르지 않고 오류를 낸다', async () => {
    const onChange = vi.fn()
    const { default: userEvent } = await import('@testing-library/user-event')
    render(
      <ValueEditor
        param={makeParam({
          type: ParamType.INTEGER,
          integer_range: { from_value: 1, to_value: 10, step: 1 },
        })}
        value={5}
        disabled={false}
        onChange={onChange}
      />,
    )
    const input = screen.getByRole('spinbutton')
    await userEvent.clear(input)
    await userEvent.type(input, '99')
    expect(screen.getByText(/허용 범위 1 ~ 10/)).toBeTruthy()
    // 99 는 범위 밖이라 전달되지 않는다
    expect(onChange).not.toHaveBeenCalledWith(99)
  })

  it('정수 배열에 소수를 넣으면 거부한다', async () => {
    const onChange = vi.fn()
    const { default: userEvent } = await import('@testing-library/user-event')
    render(
      <ValueEditor
        param={makeParam({ type: ParamType.INTEGER_ARRAY })}
        value={[1, 2]}
        disabled={false}
        onChange={onChange}
      />,
    )
    const input = screen.getByRole('textbox')
    await userEvent.clear(input)
    await userEvent.type(input, '1, 2.5')
    expect(screen.getByText(/정수가 아니다/)).toBeTruthy()
  })

  it('disabled 면 입력이 잠긴다', () => {
    render(
      <ValueEditor
        param={makeParam({ type: ParamType.STRING })}
        value="x"
        disabled={true}
        onChange={noop}
      />,
    )
    expect((screen.getByRole('textbox') as HTMLInputElement).disabled).toBe(true)
  })
})
