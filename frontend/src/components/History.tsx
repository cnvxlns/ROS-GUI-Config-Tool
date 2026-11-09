/** 변경 이력 (명세 §8.1 이력). */

import { useHistory } from '../api/client'

export function History() {
  const { data, isLoading, error } = useHistory()

  if (isLoading) return <p className="muted">이력을 읽는 중…</p>
  if (error) return <p className="error">{String(error)}</p>
  if (!data) return null

  return (
    <div className="stack">
      {!data.clean && (
        <div className="panel panel--warn">
          <h3>커밋되지 않은 변경 {data.dirty_files.length}건</h3>
          <p>
            Config App 밖에서 parameter file 이 수정됐다. 적용 전에 확인이 필요하다.
          </p>
          <ul className="small">
            {data.dirty_files.map((file) => (
              <li key={file}>
                <code>{file}</code>
              </li>
            ))}
          </ul>
        </div>
      )}

      <section className="panel">
        <h2>변경 이력</h2>
        <p className="muted small">
          롤백과 복원도 새 commit 으로 쌓이므로 아무 기록도 사라지지 않는다.
        </p>
        <table className="diff">
          <thead>
            <tr>
              <th>시각</th>
              <th>사용자</th>
              <th>내용</th>
              <th>commit</th>
            </tr>
          </thead>
          <tbody>
            {data.commits.map((commit) => (
              <tr key={commit.sha}>
                <td className="small">{new Date(commit.when).toLocaleString('ko-KR')}</td>
                <td className="small">{commit.author}</td>
                <td>{commit.subject}</td>
                <td>
                  <code className="small">{commit.sha.slice(0, 10)}</code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {data.commits.length === 0 && (
          <p className="muted">아직 기록이 없다. params/ 가 git 저장소인지 확인하라.</p>
        )}
      </section>
    </div>
  )
}
