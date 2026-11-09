/** 신규 센서 온보딩 안내 (명세 §12.2). */

import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { OnboardingPanel } from '../components/OnboardingPanel'
import type { OnboardingDTO } from '../api/types'
import { makeNode } from './factories'
import { renderWithQuery, stubFetch } from './renderWithQuery'

const DRAFT: OnboardingDTO = {
  node: '/gnss_imu/driver',
  registered: false,
  has_schema: false,
  alive: true,
  subsystem: 'gnss_imu',
  unit: 'av-gnss-imu.service',
  param_file: 'params/gnss_imu.yaml',
  schema_file: 'schemas/gnss_imu_driver.yaml',
  services_snippet: '  gnss_imu:\n    unit: av-gnss-imu.service\n',
  param_file_draft: '/gnss_imu/driver:\n  ros__parameters:\n    baudrate: 460800\n',
  schema_draft: 'driver: gnss_imu_driver   # TODO\nparams:\n  baudrate:\n    layer: driver   # TODO\n',
  steps: [
    { number: 1, title: 'ROS 2 드라이버 확보', detail: '제조사 문서부터 확인', done: null },
    { number: 3, title: '실제 장치로 파라미터 수집', detail: '장치 연결 상태에서', done: true },
    { number: 5, title: '스키마 파일 작성', detail: 'TODO 를 채운다', done: false },
  ],
  notes: ['services.yaml 에 없어 변경을 저장할 곳이 없다. 등록 전까지 이 노드는 읽기 전용이다.'],
}

const unregistered = makeNode({
  name: '/gnss_imu/driver',
  registered: false,
  subsystem: null,
  unit: null,
  param_files: [],
  schema_driver: null,
  display_mode: 'raw',
})

afterEach(() => vi.unstubAllGlobals())

describe('OnboardingPanel', () => {
  it('완전히 등록된 노드에는 뜨지 않는다', () => {
    vi.stubGlobal('fetch', stubFetch({}))
    const { container } = renderWithQuery(<OnboardingPanel node={makeNode()} />)
    expect(container.firstChild).toBeNull()
  })

  it('미등록 노드는 읽기 전용임을 알린다', async () => {
    vi.stubGlobal('fetch', stubFetch({ '/onboarding': DRAFT }))
    renderWithQuery(<OnboardingPanel node={unregistered} />)
    expect(screen.getByText(/services.yaml 에 등록되지 않은 노드/)).toBeTruthy()
    expect(screen.getByText(/읽기 전용이다/)).toBeTruthy()
  })

  it('스키마만 없는 등록된 노드에는 다른 안내를 보여준다', async () => {
    vi.stubGlobal('fetch', stubFetch({ '/onboarding': DRAFT }))
    const node = makeNode({ registered: true, schema_driver: null, display_mode: 'raw' })
    renderWithQuery(<OnboardingPanel node={node} />)
    expect(screen.getByText('스키마가 없는 노드다')).toBeTruthy()
    expect(screen.getByText(/장치 계층 반영 여부를 검증할 수 없다/)).toBeTruthy()
  })

  it('미등록 노드는 초안이 바로 펼쳐진다', async () => {
    vi.stubGlobal('fetch', stubFetch({ '/onboarding': DRAFT }))
    renderWithQuery(<OnboardingPanel node={unregistered} />)
    await waitFor(() => expect(screen.getByText('1. services.yaml 에 등록')).toBeTruthy())
    expect(screen.getByText('2. parameter file 생성')).toBeTruthy()
    expect(screen.getByText('3. 스키마 작성')).toBeTruthy()
  })

  it('절차의 완료 여부를 표시한다', async () => {
    vi.stubGlobal('fetch', stubFetch({ '/onboarding': DRAFT }))
    const { container } = renderWithQuery(<OnboardingPanel node={unregistered} />)
    await waitFor(() => expect(screen.getByText('스키마 파일 작성')).toBeTruthy())
    expect(container.querySelectorAll('.step--done')).toHaveLength(1)   // 3번
    expect(container.querySelectorAll('.step--todo')).toHaveLength(1)   // 5번
    expect(container.querySelectorAll('.step--unknown')).toHaveLength(1) // 1번
  })

  it('TODO 개수를 세어 보여준다', async () => {
    vi.stubGlobal('fetch', stubFetch({ '/onboarding': DRAFT }))
    renderWithQuery(<OnboardingPanel node={unregistered} />)
    // schema_draft 에 TODO 가 2개 있다
    await waitFor(() => expect(screen.getByText('TODO 2')).toBeTruthy())
  })

  it('스키마 초안에는 사람이 채워야 한다는 경고가 붙는다', async () => {
    vi.stubGlobal('fetch', stubFetch({ '/onboarding': DRAFT }))
    renderWithQuery(<OnboardingPanel node={unregistered} />)
    await waitFor(() =>
      expect(screen.getByText(/layer \/ apply \/ tier 는 ROS 가 알려줄 수 없다/)).toBeTruthy(),
    )
  })

  it('복사 버튼이 초안 내용을 클립보드에 넣는다', async () => {
    const written: string[] = []
    vi.stubGlobal('fetch', stubFetch({ '/onboarding': DRAFT }))
    vi.stubGlobal('navigator', {
      clipboard: { writeText: async (t: string) => void written.push(t) },
    })
    renderWithQuery(<OnboardingPanel node={unregistered} />)
    await waitFor(() => expect(screen.getAllByRole('button', { name: '복사' })).toHaveLength(3))

    await userEvent.click(screen.getAllByRole('button', { name: '복사' })[0]!)
    await waitFor(() => expect(written).toHaveLength(1))
    expect(written[0]).toBe(DRAFT.services_snippet)
  })

  it('알림을 그대로 보여준다', async () => {
    vi.stubGlobal('fetch', stubFetch({ '/onboarding': DRAFT }))
    renderWithQuery(<OnboardingPanel node={unregistered} />)
    await waitFor(() =>
      expect(screen.getByText(/변경을 저장할 곳이 없다/)).toBeTruthy(),
    )
  })

  it('접기 버튼으로 초안을 숨긴다', async () => {
    vi.stubGlobal('fetch', stubFetch({ '/onboarding': DRAFT }))
    renderWithQuery(<OnboardingPanel node={unregistered} />)
    await waitFor(() => expect(screen.getByText('1. services.yaml 에 등록')).toBeTruthy())
    await userEvent.click(screen.getByRole('button', { name: '접기' }))
    expect(screen.queryByText('1. services.yaml 에 등록')).toBeNull()
    expect(screen.getByRole('button', { name: '등록 방법 보기' })).toBeTruthy()
  })
})
