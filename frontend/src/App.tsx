/** 라우팅과 레이아웃. */

import {
  Link,
  Outlet,
  RouterProvider,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { QueryClient, QueryClientProvider, useQueryClient } from '@tanstack/react-query'
import { useSafety, useStream } from './api/client'
import { useChangeSet } from './api/changeset'
import { ChangeSetTray } from './components/ChangeSetTray'
import { History } from './components/History'
import { NodeList } from './components/NodeList'
import { ParamEditor } from './components/ParamEditor'

function Shell() {
  const queryClient = useQueryClient()
  const changeCount = useChangeSet((s) => s.changes.length)
  const setLiveStage = useChangeSet((s) => s.setLiveStage)
  const { data: safety } = useSafety()

  // 재연결 시 상태를 재동기화한다. 끊긴 동안의 이벤트는 재전송되지 않으므로
  // 캐시를 무효화해 다시 조회한다 (§7.3 WebSocket 규약).
  const { connected } = useStream((msg) => {
    if (msg.type === 'node_state') {
      queryClient.invalidateQueries({ queryKey: ['nodes'] })
    } else if (msg.type === 'changeset_progress') {
      setLiveStage(msg.stage)
    }
  })

  return (
    <div className="shell">
      <header className="topbar">
        <nav className="topbar__nav">
          <Link to="/" className="brand">
            AV Config
          </Link>
          <Link to="/history" className="navlink">
            이력
          </Link>
        </nav>
        <div className="topbar__status">
          <span className={`dot ${connected ? 'dot--ok' : 'dot--off'}`} />
          <span className="small">{connected ? '실시간 연결' : '연결 끊김'}</span>
          {safety && (
            <span className={`badge badge--${safety.apply_allowed ? 'info' : 'danger'}`}>
              {safety.mode === 'open' ? '안전 게이트 open' : '안전 게이트 enforce'}
            </span>
          )}
          {changeCount > 0 && <span className="badge badge--warn">변경 {changeCount}건</span>}
        </div>
      </header>

      <main className="content">
        <Outlet />
      </main>

      <ChangeSetTray />
    </div>
  )
}

const rootRoute = createRootRoute({ component: Shell })

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  component: NodeList,
})

const nodeRoute = createRoute({
  getParentRoute: () => rootRoute,
  // 노드 이름은 선행 슬래시를 떼고 넘긴다. 아래에서 다시 붙인다.
  path: '/nodes/$node',
  component: NodeDetail,
})

function NodeDetail() {
  const { node } = nodeRoute.useParams()
  return <ParamEditor node={`/${node}`} />
}

const historyRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/history',
  component: History,
})

const routeTree = rootRoute.addChildren([indexRoute, nodeRoute, historyRoute])
const router = createRouter({ routeTree })

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false },
  },
})

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
}
