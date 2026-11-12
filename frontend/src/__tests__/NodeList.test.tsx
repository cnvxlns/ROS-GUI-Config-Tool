/** 노드 목록 화면 (명세 §8.1). */

import type React from 'react'
import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { NodeList } from '../components/NodeList'
import { makeNode } from './factories'
import { renderWithQuery, stubFetch } from './renderWithQuery'

// Link 는 라우터 컨텍스트를 요구한다. 이 테스트가 보는 것은 목록 구성이지
// 라우팅이 아니므로 앵커로 대체한다.
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, ...rest }: { children: React.ReactNode }) => (
    <a {...rest}>{children}</a>
  ),
}))

function snapshot(nodes: ReturnType<typeof makeNode>[]) {
  return { collected_at: 0, nodes, schema_errors: [] }
}

const driver = makeNode({ name: '/ouster/os_driver' })
const remote = makeNode({
  name: '/rviz',
  registered: false,
  subsystem: null,
  unit: null,
  param_files: [],
  schema_driver: null,
  display_mode: 'raw',
})
const tfListener = makeNode({
  name: '/transform_listener_impl_5b043ac74e90',
  registered: false,
  internal: true,
  subsystem: null,
  unit: null,
  param_files: [],
  schema_driver: null,
  display_mode: 'raw',
})
const daemon = makeNode({
  name: '/_ros2cli_daemon_0_dcb3b08e',
  registered: false,
  internal: true,
  subsystem: null,
  unit: null,
  param_files: [],
  schema_driver: null,
  display_mode: 'raw',
})

afterEach(() => vi.unstubAllGlobals())

describe('NodeList 의 내부 노드 필터', () => {
  it('ROS 내부 노드는 기본으로 접혀 있다', async () => {
    vi.stubGlobal(
      'fetch',
      stubFetch({ '/nodes': snapshot([driver, remote, tfListener, daemon]) }),
    )
    renderWithQuery(<NodeList />)

    expect(await screen.findByText('/ouster/os_driver')).toBeTruthy()
    expect(screen.getByText('/rviz')).toBeTruthy()
    // 목록을 가리던 것들은 접혀 있다
    expect(screen.queryByText('/transform_listener_impl_5b043ac74e90')).toBeNull()
    expect(screen.queryByText('/_ros2cli_daemon_0_dcb3b08e')).toBeNull()
  })

  it('감추지 않고 개수를 밝힌 뒤 펼칠 수 있게 한다', async () => {
    vi.stubGlobal(
      'fetch',
      stubFetch({ '/nodes': snapshot([driver, tfListener, daemon]) }),
    )
    renderWithQuery(<NodeList />)

    // 안 보이면 드라이버가 안 떴는지 필터에 걸렸는지 구분할 수 없다
    expect(await screen.findByText('ROS 내부 노드 2개')).toBeTruthy()

    await userEvent.click(screen.getByRole('button', { name: '펼치기' }))
    expect(screen.getByText('/transform_listener_impl_5b043ac74e90')).toBeTruthy()
    expect(screen.getByText('/_ros2cli_daemon_0_dcb3b08e')).toBeTruthy()
  })

  it('내부 노드가 없으면 섹션도 없다', async () => {
    vi.stubGlobal('fetch', stubFetch({ '/nodes': snapshot([driver, remote]) }))
    renderWithQuery(<NodeList />)

    expect(await screen.findByText('/ouster/os_driver')).toBeTruthy()
    expect(screen.queryByText(/ROS 내부 노드/)).toBeNull()
  })

  it('내부 노드는 미등록 노드 묶음에 섞이지 않는다', async () => {
    vi.stubGlobal(
      'fetch',
      stubFetch({ '/nodes': snapshot([driver, remote, tfListener]) }),
    )
    renderWithQuery(<NodeList />)

    await screen.findByText('미등록 노드')
    const unregistered = screen.getByText('미등록 노드').closest('section')!
    expect(unregistered.textContent).toContain('/rviz')
    expect(unregistered.textContent).not.toContain('transform_listener_impl')
  })
})
