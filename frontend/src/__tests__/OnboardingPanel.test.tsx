/** 신규 센서 온보딩 안내 (명세 §12.2). */

import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { OnboardingPanel } from '../components/OnboardingPanel'
import { ParamEditor } from '../components/ParamEditor'
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
  topics: ['/gnss/fix'],
  editable_params: ['baudrate', 'serial_port'],
  todo_count: 2,
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
  // ------------------------------------------------ TODO 채우기 폼 (§12.2)

  it('ROS 가 알려줄 수 없는 자리만 폼으로 묻는다', async () => {
    vi.stubGlobal('fetch', stubFetch({ '/onboarding': DRAFT }))
    renderWithQuery(<OnboardingPanel node={unregistered} />)

    await waitFor(() => expect(screen.getByText('채워야 할 자리')).toBeTruthy())
    expect(screen.getByText('launch 명령')).toBeTruthy()
    expect(screen.getByText('드라이버 패키지 이름')).toBeTruthy()
    // 기본값 드롭다운과 파라미터별 예외 표의 머리글에 각각 나온다
    expect(screen.getAllByText('계층').length).toBeGreaterThan(0)
    expect(screen.getAllByText('적용').length).toBeGreaterThan(0)
    expect(screen.getAllByText('등급').length).toBeGreaterThan(0)
  })

  it('기대 주기는 자동으로 채우지 않고 사람에게 묻는다', async () => {
    vi.stubGlobal('fetch', stubFetch({ '/onboarding': DRAFT }))
    renderWithQuery(<OnboardingPanel node={unregistered} />)

    await waitFor(() => expect(screen.getByText('/gnss/fix')).toBeTruthy())
    // 측정값을 기대값으로 굳히면 성능 저하가 영구히 정상으로 기록된다
    expect(screen.getByText(/공칭값. 지금 측정값이 아니다/)).toBeTruthy()
  })

  it('고른 값을 서버로 보내 초안을 다시 만든다', async () => {
    const sent: unknown[] = []
    vi.stubGlobal(
      'fetch',
      stubFetch({ '/onboarding': DRAFT }, (_path, body) => {
        sent.push(body)
        return { ...DRAFT, todo_count: 0, schema_draft: 'driver: ouster_ros\n' }
      }),
    )
    renderWithQuery(<OnboardingPanel node={unregistered} />)
    await waitFor(() => expect(screen.getByText('채워야 할 자리')).toBeTruthy())

    await userEvent.type(screen.getByPlaceholderText('ouster_ros'), 'gnss_imu_driver')

    // 글자마다 한 번씩 보낸다. 마지막 요청이 최종 입력을 담고 있어야 한다.
    await waitFor(() => {
      const last = sent[sent.length - 1] as { driver?: string }
      expect(last?.driver).toBe('gnss_imu_driver')
    })
  })

  it('남은 자리가 있으면 왜 위험한지 알린다', async () => {
    vi.stubGlobal('fetch', stubFetch({ '/onboarding': DRAFT }))
    renderWithQuery(<OnboardingPanel node={unregistered} />)
    await waitFor(() =>
      expect(screen.getByText(/채우지 않은 자리가 2개 남았다/)).toBeTruthy(),
    )
    expect(screen.getByText(/적용 방식이 틀린 채로/)).toBeTruthy()
  })

  it('다 채우면 그대로 쓸 수 있다고 알린다', async () => {
    vi.stubGlobal(
      'fetch',
      stubFetch({ '/onboarding': DRAFT }, () => ({
        ...DRAFT,
        todo_count: 0,
        schema_draft: 'driver: gnss_imu_driver\n',
      })),
    )
    renderWithQuery(<OnboardingPanel node={unregistered} />)
    await waitFor(() => expect(screen.getByText('채워야 할 자리')).toBeTruthy())

    await userEvent.type(screen.getByPlaceholderText('ouster_ros'), 'x')

    await waitFor(() =>
      expect(screen.getByText(/채워야 할 자리를 모두 채웠다/)).toBeTruthy(),
    )
  })

  it('device 계층을 고르면 조회 서비스를 묻는다', async () => {
    vi.stubGlobal('fetch', stubFetch({ '/onboarding': DRAFT }, () => DRAFT))
    renderWithQuery(<OnboardingPanel node={unregistered} />)
    await waitFor(() => expect(screen.getByText('채워야 할 자리')).toBeTruthy())

    // §4.4: device 계층은 readback_via 가 필수다
    expect(screen.queryByPlaceholderText('/ouster/get_config')).toBeNull()
    const layer = screen.getAllByRole('combobox')[0]!
    await userEvent.selectOptions(layer, 'device')

    await waitFor(() =>
      expect(screen.getByPlaceholderText('/ouster/get_config')).toBeTruthy(),
    )
  })

  it('안내 주석의 TODO 는 남은 자리로 세지 않는다', async () => {
    const headerOnly = {
      ...DRAFT,
      todo_count: 0,
      schema_draft: '# 아래 TODO 를 모두 채워야 한다\ndriver: gnss_imu_driver\n',
    }
    vi.stubGlobal('fetch', stubFetch({ '/onboarding': headerOnly }))
    renderWithQuery(<OnboardingPanel node={unregistered} />)

    await waitFor(() => expect(screen.getByText('3. 스키마 작성')).toBeTruthy())
    expect(screen.queryByText(/^TODO \d/)).toBeNull()
  })
})

describe('파라미터 서비스에 닿지 않는 노드 (§5.3)', () => {
  it('503 을 노드가 사라진 것처럼 보여주지 않는다', async () => {
    vi.stubGlobal('fetch', async () =>
      new Response(
        JSON.stringify({
          detail:
            '/transform_listener_impl_5b043ac74e90: 파라미터 서비스에 접근할 수 없다 ' +
            '(/transform_listener_impl_5b043ac74e90/list_parameters).',
        }),
        { status: 503, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    renderWithQuery(<ParamEditor node="/transform_listener_impl_5b043ac74e90" />)

    await waitFor(() =>
      expect(screen.getByText(/파라미터 서비스에 접근할 수 없다/)).toBeTruthy(),
    )
    expect(screen.getByText(/노드가 사라진 것은 아니다/)).toBeTruthy()
    // 상태 코드는 사유 앞에 붙여 보여주지 않는다
    expect(screen.queryByText(/^503 /)).toBeNull()
  })
})
