/** change set 집계 로직 (명세 §6.1 계획 단계). */

import { beforeEach, describe, expect, it } from 'vitest'
import { affectedUnits, globalChanges, useChangeSet, type PendingChange } from '../api/changeset'

const change = (over: Partial<PendingChange> = {}): PendingChange => ({
  node: '/ouster/os_driver',
  param: 'p',
  before: 'a',
  value: 'b',
  apply: 'restart',
  unit: 'av-lidar-top.service',
  globalRef: null,
  ...over,
})

beforeEach(() => useChangeSet.getState().clear())

describe('affectedUnits', () => {
  it('restart 변경이 있는 unit 만 모은다', () => {
    expect(
      affectedUnits([
        change({ apply: 'restart', unit: 'a.service' }),
        change({ param: 'q', apply: 'runtime', unit: 'b.service' }),
      ]),
    ).toEqual(['a.service'])
  })

  it('같은 unit 을 중복으로 세지 않는다', () => {
    expect(
      affectedUnits([
        change({ param: 'p', unit: 'a.service' }),
        change({ param: 'q', unit: 'a.service' }),
      ]),
    ).toEqual(['a.service'])
  })

  it('unit 이 없는 노드는 재시작 대상에 넣지 않는다', () => {
    expect(affectedUnits([change({ unit: null })])).toEqual([])
  })
})

describe('globalChanges', () => {
  it('전역 설정을 참조하는 변경만 고른다', () => {
    const result = globalChanges([
      change({ param: 'sensor_frame', globalRef: 'frames.lidar_top' }),
      change({ param: 'lidar_mode', globalRef: null }),
    ])
    expect(result).toHaveLength(1)
    expect(result[0]?.param).toBe('sensor_frame')
  })
})

describe('change set 스토어', () => {
  it('같은 파라미터를 다시 바꾸면 덮어쓴다', () => {
    const store = useChangeSet.getState()
    store.setChange(change({ value: 'b' }))
    store.setChange(change({ value: 'c' }))
    const changes = useChangeSet.getState().changes
    expect(changes).toHaveLength(1)
    expect(changes[0]?.value).toBe('c')
  })

  it('다른 파라미터는 따로 쌓인다', () => {
    const store = useChangeSet.getState()
    store.setChange(change({ param: 'p' }))
    store.setChange(change({ param: 'q' }))
    expect(useChangeSet.getState().changes).toHaveLength(2)
  })

  it('배열 값도 원래 값과 같으면 제거한다', () => {
    useChangeSet.getState().setChange(
      change({ before: [1, 2] as unknown as PendingChange['before'], value: [1, 2] as unknown as PendingChange['value'] }),
    )
    expect(useChangeSet.getState().changes).toHaveLength(0)
  })

  it('removeChange 는 해당 항목만 뺀다', () => {
    const store = useChangeSet.getState()
    store.setChange(change({ param: 'p' }))
    store.setChange(change({ param: 'q' }))
    useChangeSet.getState().removeChange('/ouster/os_driver', 'p')
    const changes = useChangeSet.getState().changes
    expect(changes).toHaveLength(1)
    expect(changes[0]?.param).toBe('q')
  })
})
