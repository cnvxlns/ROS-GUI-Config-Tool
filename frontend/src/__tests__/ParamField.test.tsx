/** 복합 필드가 저장값·런타임값·drift·적용 방식·잠금을 함께 표시하는지 (명세 §8.1, §8.2). */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it } from 'vitest'
import { ParamField } from '../components/ParamField'
import { useChangeSet } from '../api/changeset'
import { ParamType } from '../api/types'
import { makeNode, makeParam } from './factories'

beforeEach(() => useChangeSet.getState().clear())

describe('ParamField 표시', () => {
  it('런타임값과 저장값을 모두 내고, 저장값 출처 파일도 적는다', () => {
    render(
      <ParamField
        node={makeNode()}
        param={makeParam({
          name: 'sensor_frame',
          type: ParamType.STRING,
          runtime_value: 'lidar_top_link',
          saved_value: 'lidar_top_link',
          saved_source: 'params/global.yaml',
        })}
        lockReason={null}
      />,
    )
    expect(screen.getByText('런타임')).toBeTruthy()
    expect(screen.getByText('저장')).toBeTruthy()
    expect(screen.getByText(/params\/global\.yaml/)).toBeTruthy()
  })

  it('drift 가 있으면 경고를 붙인다', () => {
    render(
      <ParamField
        node={makeNode()}
        param={makeParam({
          type: ParamType.STRING,
          runtime_value: '512x10',
          saved_value: '1024x10',
          saved_source: 'params/lidar_top.yaml',
          drift: true,
        })}
        lockReason={null}
      />,
    )
    expect(screen.getByText(/저장값과 런타임값이 다르다/)).toBeTruthy()
  })

  it('노드가 선언하지 않은 값은 YAML 이 적용되지 않는다고 알린다', () => {
    render(
      <ParamField
        node={makeNode()}
        param={makeParam({
          type: ParamType.NOT_SET,
          runtime_value: null,
          saved_value: 'x',
          saved_source: 'params/lidar_top.yaml',
          drift: true,
        })}
        lockReason={null}
      />,
    )
    expect(screen.getByText(/적용되지 않고 있다/)).toBeTruthy()
  })

  it('restart 파라미터는 unit 재시작을 미리 알린다', () => {
    render(
      <ParamField
        node={makeNode()}
        param={makeParam({ type: ParamType.STRING, apply: 'restart' })}
        lockReason={null}
      />,
    )
    expect(screen.getByText('unit 재시작')).toBeTruthy()
  })

  it('전역 참조가 있으면 전역 배지를 단다', () => {
    render(
      <ParamField
        node={makeNode()}
        param={makeParam({
          type: ParamType.STRING,
          has_schema: true,
          global_ref: 'frames.lidar_top',
        })}
        lockReason={null}
      />,
    )
    expect(screen.getByText('전역')).toBeTruthy()
  })

  it('스키마가 없으면 원본 배지를 단다', () => {
    render(
      <ParamField
        node={makeNode()}
        param={makeParam({ type: ParamType.STRING, has_schema: false })}
        lockReason={null}
      />,
    )
    expect(screen.getByText('원본')).toBeTruthy()
  })
})

describe('ParamField 편집 잠금', () => {
  it('노드 전체 잠금이면 입력을 잠그되 사유를 필드마다 반복하지 않는다', () => {
    const { container } = render(
      <ParamField
        node={makeNode()}
        param={makeParam({ type: ParamType.STRING, runtime_value: 'x' })}
        lockReason="주행 중이다"
      />,
    )
    expect((screen.getByRole('textbox') as HTMLInputElement).disabled).toBe(true)
    // 같은 문장이 필드마다 나오면 파라미터별 사유가 묻힌다. 본문에는 쓰지 않는다.
    expect(screen.queryByText('주행 중이다')).toBeNull()
    // 대신 title 로 남겨 어디서든 확인할 수 있게 한다
    expect(container.querySelector('.param-row')?.getAttribute('title')).toBe('주행 중이다')
  })

  it('파라미터별 사유는 본문에 그대로 적는다', () => {
    render(
      <ParamField
        node={makeNode()}
        param={makeParam({ type: ParamType.STRING, tier: 'hidden', runtime_value: 'x' })}
        lockReason={null}
      />,
    )
    expect(screen.getByText('이 등급은 편집할 수 없다.')).toBeTruthy()
  })

  it('노드 전체 잠금 중에도 read_only 사유는 그대로 보인다', () => {
    render(
      <ParamField
        node={makeNode()}
        param={makeParam({ type: ParamType.STRING, read_only: true, runtime_value: 'x' })}
        lockReason="주행 중이다"
      />,
    )
    expect(screen.getByText(/read_only 로 선언한 값/)).toBeTruthy()
    expect(screen.queryByText('주행 중이다')).toBeNull()
  })

  it('read_only 파라미터는 표시만 한다', () => {
    render(
      <ParamField
        node={makeNode()}
        param={makeParam({ type: ParamType.STRING, read_only: true, runtime_value: 'x' })}
        lockReason={null}
      />,
    )
    expect((screen.getByRole('textbox') as HTMLInputElement).disabled).toBe(true)
    expect(screen.getByText(/read_only 로 선언한 값/)).toBeTruthy()
  })
})

describe('ParamField 편집', () => {
  it('값을 바꾸면 change set 에 쌓이고 즉시 적용되지 않는다', async () => {
    render(
      <ParamField
        node={makeNode()}
        param={makeParam({
          name: 'lidar_mode',
          type: ParamType.STRING,
          has_schema: true,
          apply: 'reconfigure',
          enum_values: ['512x10', '1024x10'],
          runtime_value: '512x10',
        })}
        lockReason={null}
      />,
    )
    await userEvent.selectOptions(screen.getByRole('combobox'), '1024x10')

    const changes = useChangeSet.getState().changes
    expect(changes).toHaveLength(1)
    expect(changes[0]).toMatchObject({
      node: '/ouster/os_driver',
      param: 'lidar_mode',
      before: '512x10',
      value: '1024x10',
      apply: 'reconfigure',
      unit: 'av-lidar-top.service',
    })
    expect(screen.getByText(/변경 대기/)).toBeTruthy()
  })

  it('원래 값으로 되돌리면 change set 에서 빠진다', async () => {
    render(
      <ParamField
        node={makeNode()}
        param={makeParam({
          name: 'lidar_mode',
          type: ParamType.STRING,
          has_schema: true,
          enum_values: ['512x10', '1024x10'],
          runtime_value: '512x10',
        })}
        lockReason={null}
      />,
    )
    const select = screen.getByRole('combobox')
    await userEvent.selectOptions(select, '1024x10')
    expect(useChangeSet.getState().changes).toHaveLength(1)
    await userEvent.selectOptions(select, '512x10')
    expect(useChangeSet.getState().changes).toHaveLength(0)
  })
})
