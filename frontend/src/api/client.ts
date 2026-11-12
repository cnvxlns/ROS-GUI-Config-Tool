/** 백엔드 호출과 TanStack Query 훅 (명세 §7.3). */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import type {
  ChangeSetResult,
  DiscoverySnapshotDTO,
  HistoryDTO,
  NodeParamsDTO,
  OnboardingChoices,
  OnboardingDTO,
  ParamValue,
  SafetyStateDTO,
  StreamMessage,
} from './types'

const BASE = '/api/v1'

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = (await response.json()) as { detail?: string }
      if (body.detail) detail = body.detail
    } catch {
      // 본문이 JSON 이 아니면 statusText 를 쓴다
    }
    throw new Error(`${response.status} ${detail}`)
  }
  return (await response.json()) as T
}

export function useNodes() {
  return useQuery({
    queryKey: ['nodes'],
    queryFn: () => getJson<DiscoverySnapshotDTO>('/nodes'),
    refetchInterval: 10_000,
  })
}

export function useNodeParams(node: string | null) {
  return useQuery({
    queryKey: ['params', node],
    // 노드 이름이 경로 마지막에 온다. 슬래시를 포함하므로 인코딩하지 않는다 (§7.3).
    queryFn: () => getJson<NodeParamsDTO>(`/params${node}`),
    enabled: node !== null,
  })
}

/**
 * 신규 센서 등록에 필요한 초안과 절차 (§12.2).
 *
 * 등록되지 않았거나 스키마가 없는 노드에서만 의미가 있으므로 호출자가
 * enabled 로 제어한다.
 */
export function useOnboarding(node: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ['onboarding', node],
    queryFn: () => getJson<OnboardingDTO>(`/onboarding${node}`),
    enabled: node !== null && enabled,
    staleTime: 30_000,
  })
}

/**
 * 사람이 정한 값을 박아 넣은 초안 (§12.2).
 *
 * 아무것도 저장하지 않는다. 생성 로직은 서버 한 곳에만 둔다 — 화면에서 문자열을
 * 치환하면 YAML 들여쓰기와 따옴표 규칙이 두 곳으로 갈라진다.
 */
export function useOnboardingDraft(
  node: string | null,
  choices: OnboardingChoices,
  enabled: boolean,
) {
  return useQuery({
    queryKey: ['onboarding-draft', node, choices],
    queryFn: () =>
      postJson<OnboardingDTO>(`/onboarding${node}`, choices),
    enabled: node !== null && enabled,
    // 입력할 때마다 이전 초안을 지우면 화면이 깜빡인다
    placeholderData: (previous) => previous,
  })
}

export function useHistory(limit = 50) {
  return useQuery({
    queryKey: ['history', limit],
    queryFn: () => getJson<HistoryDTO>(`/history?limit=${limit}`),
  })
}

export interface SubmitPayload {
  dry_run: boolean
  comment: string
  changes: { node: string; param: string; value: ParamValue }[]
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body),
  })
  const text = await response.text()
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const parsed = JSON.parse(text) as { detail?: string }
      if (parsed.detail) detail = parsed.detail
    } catch {
      // JSON 이 아니면 상태 줄을 쓴다
    }
    throw new Error(detail)
  }
  return JSON.parse(text) as T
}

/**
 * change set 제출 (§6.1).
 *
 * 성공 여부와 무관하게 노드와 파라미터 캐시를 무효화한다. 적용이 실패해 롤백됐더라도
 * 런타임 상태가 달라졌을 수 있기 때문이다.
 */
export function useSubmitChangeSet() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: SubmitPayload) =>
      postJson<ChangeSetResult>('/changesets', payload),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ['nodes'] })
      queryClient.invalidateQueries({ queryKey: ['params'] })
      queryClient.invalidateQueries({ queryKey: ['history'] })
    },
  })
}

export function useSafety() {
  return useQuery({
    queryKey: ['safety'],
    queryFn: () => getJson<SafetyStateDTO>('/safety'),
    refetchInterval: 5_000,
  })
}

/**
 * /api/v1/stream 구독 (§7.3 WebSocket 규약).
 *
 * 이 채널이 라이브 상태 채널을 겸하므로 지수 백오프로 재연결하고, 재연결 시
 * 호출자가 상태를 재동기화해야 한다. 끊긴 동안의 이벤트는 재전송되지 않는다.
 */
export function useStream(onMessage: (msg: StreamMessage) => void) {
  const [connected, setConnected] = useState(false)
  const handlerRef = useRef(onMessage)
  handlerRef.current = onMessage

  useEffect(() => {
    let socket: WebSocket | null = null
    let timer: number | undefined
    let attempt = 0
    let closed = false

    const connect = () => {
      if (closed) return
      const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
      socket = new WebSocket(`${scheme}://${window.location.host}${BASE}/stream`)

      socket.onopen = () => {
        attempt = 0
        setConnected(true)
      }
      socket.onmessage = (event) => {
        try {
          handlerRef.current(JSON.parse(event.data as string) as StreamMessage)
        } catch {
          // 알 수 없는 메시지는 버린다
        }
      }
      socket.onclose = () => {
        setConnected(false)
        if (closed) return
        // 지수 백오프, 최대 15초
        const delay = Math.min(15_000, 500 * 2 ** attempt)
        attempt += 1
        timer = window.setTimeout(connect, delay)
      }
      socket.onerror = () => socket?.close()
    }

    connect()
    return () => {
      closed = true
      if (timer) window.clearTimeout(timer)
      socket?.close()
    }
  }, [])

  return { connected }
}
