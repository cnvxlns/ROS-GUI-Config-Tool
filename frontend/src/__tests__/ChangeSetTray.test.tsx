/** 변경 검토와 적용 (명세 §8.1, §8.2, §6.1). */

import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ChangeSetTray } from '../components/ChangeSetTray'
import { useChangeSet, type PendingChange } from '../api/changeset'
import type { ChangeSetResult } from '../api/types'
import { makeNode } from './factories'
import { renderWithQuery, stubFetch } from './renderWithQuery'

const change = (over: Partial<PendingChange> = {}): PendingChange => ({
  node: '/ouster/os_driver',
  param: 'lidar_mode',
  before: '1024x10',
  value: '2048x10',
  apply: 'restart',
  unit: 'av-lidar-top.service',
  globalRef: null,
  ...over,
})

const SAFETY_OPEN = {
  mode: 'open',
  apply_allowed: true,
  reason: 'safety.mode=open (개발 모드).',
  vehicle_state_topic: null,
}
const SAFETY_LOCKED = {
  mode: 'enforce',
  apply_allowed: false,
  reason: '차량 상태를 알 수 없다. enforce 모드에서는 잠금으로 간주한다.',
  vehicle_state_topic: null,
}
const NODES = {
  collected_at: 0,
  nodes: [makeNode(), makeNode({ name: '/ouster/imu_publisher' })],
  schema_errors: [],
}

const SUCCESS: ChangeSetResult = {
  id: 'abc123',
  status: 'succeeded',
  stage: 'done',
  issues: [],
  plan: null,
  results: [
    {
      node: '/ouster/os_driver',
      param: 'lidar_mode',
      requested: '2048x10',
      actual: '2048x10',
      success: true,
      reason: null,
      readback_source: '/ouster/get_metadata',
    },
  ],
  commit: 'deadbeefcafe0000',
  rollback_commit: null,
  message: '적용과 반영 확인을 모두 통과했다.',
}

beforeEach(() => useChangeSet.getState().clear())
afterEach(() => vi.unstubAllGlobals())

describe('ChangeSetTray', () => {
  it('변경이 없으면 아무것도 렌더링하지 않는다', () => {
    vi.stubGlobal('fetch', stubFetch({ '/safety': SAFETY_OPEN, '/nodes': NODES }))
    const { container } = renderWithQuery(<ChangeSetTray />)
    expect(container.querySelector('.tray')).toBeNull()
  })

  it('변경 diff 와 재시작 대상 unit 을 보여준다', async () => {
    vi.stubGlobal('fetch', stubFetch({ '/safety': SAFETY_OPEN, '/nodes': NODES }))
    useChangeSet.getState().setChange(change())
    renderWithQuery(<ChangeSetTray />)

    expect(screen.getByText('변경 1건')).toBeTruthy()
    expect(screen.getByText('1024x10')).toBeTruthy()
    expect(screen.getByText('2048x10')).toBeTruthy()
    await waitFor(() =>
      expect(screen.getByText('재시작 대상 unit 1개')).toBeTruthy(),
    )
  })

  it('같은 unit 의 다른 노드까지 중단 범위로 표시한다', async () => {
    vi.stubGlobal('fetch', stubFetch({ '/safety': SAFETY_OPEN, '/nodes': NODES }))
    useChangeSet.getState().setChange(change())
    renderWithQuery(<ChangeSetTray />)
    // NODES 의 두 노드가 같은 unit 이므로 둘 다 나와야 한다
    await waitFor(() => expect(screen.getByText(/노드 2개가 함께 중단된다/)).toBeTruthy())
    expect(screen.getByText('/ouster/imu_publisher')).toBeTruthy()
  })

  it('전역 설정 변경은 센서 스택 전체 재시작을 경고한다', async () => {
    vi.stubGlobal('fetch', stubFetch({ '/safety': SAFETY_OPEN, '/nodes': NODES }))
    useChangeSet
      .getState()
      .setChange(change({ param: 'sensor_frame', globalRef: 'frames.lidar_top' }))
    renderWithQuery(<ChangeSetTray />)
    expect(screen.getByText('전역 설정 변경 1건')).toBeTruthy()
    expect(screen.getByText(/센서 스택/)).toBeTruthy()
  })

  it('안전 게이트가 잠겨 있으면 적용 버튼을 비활성화하고 사유를 보여준다', async () => {
    vi.stubGlobal('fetch', stubFetch({ '/safety': SAFETY_LOCKED, '/nodes': NODES }))
    useChangeSet.getState().setChange(change())
    renderWithQuery(<ChangeSetTray />)

    await waitFor(() => {
      const apply = screen.getByRole('button', { name: '적용' }) as HTMLButtonElement
      expect(apply.disabled).toBe(true)
    })
    expect(screen.getByText(/enforce 모드에서는 잠금으로 간주한다/)).toBeTruthy()
    // 검증만 하는 것은 잠겨도 가능하다
    expect((screen.getByRole('button', { name: /dry run/ }) as HTMLButtonElement).disabled).toBe(
      false,
    )
  })

  it('적용하면 change set 을 그대로 전송한다', async () => {
    const posted: { path: string; body: unknown }[] = []
    vi.stubGlobal(
      'fetch',
      stubFetch({ '/safety': SAFETY_OPEN, '/nodes': NODES }, (path, body) => {
        posted.push({ path, body })
        return SUCCESS
      }),
    )
    useChangeSet.getState().setChange(change())
    useChangeSet.getState().setComment('해상도 상향')
    renderWithQuery(<ChangeSetTray />)

    await waitFor(() =>
      expect((screen.getByRole('button', { name: '적용' }) as HTMLButtonElement).disabled).toBe(
        false,
      ),
    )
    await userEvent.click(screen.getByRole('button', { name: '적용' }))

    await waitFor(() => expect(posted).toHaveLength(1))
    expect(posted[0]?.path).toBe('/changesets')
    expect(posted[0]?.body).toEqual({
      dry_run: false,
      comment: '해상도 상향',
      changes: [{ node: '/ouster/os_driver', param: 'lidar_mode', value: '2048x10' }],
    })
  })

  it('dry run 은 dry_run=true 로 보낸다', async () => {
    const posted: { body: unknown }[] = []
    vi.stubGlobal(
      'fetch',
      stubFetch({ '/safety': SAFETY_OPEN, '/nodes': NODES }, (_path, body) => {
        posted.push({ body })
        return { ...SUCCESS, status: 'dry_run' }
      }),
    )
    useChangeSet.getState().setChange(change())
    renderWithQuery(<ChangeSetTray />)
    await userEvent.click(screen.getByRole('button', { name: /dry run/ }))
    await waitFor(() => expect(posted).toHaveLength(1))
    expect((posted[0]?.body as { dry_run: boolean }).dry_run).toBe(true)
  })

  it('적용이 성공하면 보류 목록을 비우고 결과를 보여준다', async () => {
    vi.stubGlobal(
      'fetch',
      stubFetch({ '/safety': SAFETY_OPEN, '/nodes': NODES }, () => SUCCESS),
    )
    useChangeSet.getState().setChange(change())
    renderWithQuery(<ChangeSetTray />)
    await userEvent.click(screen.getByRole('button', { name: '적용' }))

    await waitFor(() => expect(screen.getByText('적용 완료')).toBeTruthy())
    expect(useChangeSet.getState().changes).toHaveLength(0)
    expect(screen.getByText('/ouster/get_metadata')).toBeTruthy()
  })

  it('거부되면 보류 목록을 유지하고 사유를 보여준다', async () => {
    const rejected: ChangeSetResult = {
      ...SUCCESS,
      status: 'rejected',
      stage: 'rejected',
      results: [],
      commit: null,
      message: '검증에서 거부되었다. 아무것도 바뀌지 않았다.',
      issues: [
        {
          node: '/ouster/os_driver',
          param: 'lidar_mode',
          code: 'not_in_enum',
          message: '허용 값이 아니다',
        },
      ],
    }
    vi.stubGlobal(
      'fetch',
      stubFetch({ '/safety': SAFETY_OPEN, '/nodes': NODES }, () => rejected),
    )
    useChangeSet.getState().setChange(change())
    renderWithQuery(<ChangeSetTray />)
    await userEvent.click(screen.getByRole('button', { name: '적용' }))

    await waitFor(() =>
      expect(screen.getByText('거부됨 — 아무것도 바뀌지 않았다')).toBeTruthy(),
    )
    expect(screen.getByText(/허용 값이 아니다/)).toBeTruthy()
    // 사용자가 고쳐서 다시 낼 수 있어야 하므로 목록은 남긴다
    expect(useChangeSet.getState().changes).toHaveLength(1)
  })

  it('409 같은 요청 실패도 사용자에게 알린다', async () => {
    vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      if (init?.method === 'POST') {
        return new Response(
          JSON.stringify({ detail: '이미 실행 중인 change set 이 있다: abc' }),
          { status: 409, headers: { 'Content-Type': 'application/json' } },
        )
      }
      const payload = path.includes('/safety') ? SAFETY_OPEN : NODES
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    })
    useChangeSet.getState().setChange(change())
    renderWithQuery(<ChangeSetTray />)
    await userEvent.click(screen.getByRole('button', { name: '적용' }))
    await waitFor(() =>
      expect(screen.getByText(/이미 실행 중인 change set/)).toBeTruthy(),
    )
  })
})
