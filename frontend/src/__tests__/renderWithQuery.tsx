import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactElement } from 'react'

export function renderWithQuery(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

/** fetch 를 경로별 응답 맵으로 대체한다. */
export function stubFetch(
  routes: Record<string, unknown>,
  onPost?: (path: string, body: unknown) => unknown,
) {
  return async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const path = url.replace('/api/v1', '')
    if (init?.method === 'POST') {
      const body = init.body ? JSON.parse(String(init.body)) : null
      const payload = onPost ? onPost(path, body) : {}
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
    const match = Object.keys(routes).find((key) => path.startsWith(key))
    if (match === undefined) {
      return new Response('not found', { status: 404 })
    }
    return new Response(JSON.stringify(routes[match]), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }
}
