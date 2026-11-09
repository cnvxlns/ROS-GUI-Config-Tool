/**
 * 문서의 mermaid 블록이 실제로 파싱·렌더링되는지 확인한다.
 *
 * 문법이 깨진 다이어그램은 GitHub 이나 뷰어에서 조용히 빈 칸으로 나온다.
 * 보기 전에는 모르므로 여기서 잡는다. 시스템에 설치된 Chrome 으로 실제
 * mermaid 를 돌린다.
 *
 *   npm run check:docs
 *   node scripts/check-mermaid.mjs ../docs/architecture.md
 */
import { readFileSync } from 'node:fs'
import { createServer } from 'node:http'
import puppeteer from 'puppeteer-core'

const files = process.argv.slice(2)
if (files.length === 0) {
  console.error('사용법: node scripts/check-mermaid.mjs <문서.md> [...]')
  process.exit(2)
}
const bundle = readFileSync('node_modules/mermaid/dist/mermaid.min.js', 'utf8')
const page = `<!doctype html><html><body><script>${bundle}<\/script></body></html>`
const server = createServer((_q, r) => {
  r.writeHead(200, { 'Content-Type': 'text/html' })
  r.end(page)
}).listen(0)
const port = server.address().port

const browser = await puppeteer.launch({
  executablePath: '/opt/google/chrome/chrome', headless: true,
  args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
})
let failed = 0
try {
  const tab = await browser.newPage()
  await tab.goto(`http://127.0.0.1:${port}/`, { waitUntil: 'networkidle0' })
  await tab.evaluate(() => window.mermaid.initialize({ startOnLoad: false }))

  for (const file of files) {
  const md = readFileSync(file, 'utf8')
  const blocks = [...md.matchAll(/```mermaid\n([\s\S]*?)```/g)].map((m) => m[1])
  console.log(`${file}: mermaid 블록 ${blocks.length}개`)
  for (const [i, src] of blocks.entries()) {
    const result = await tab.evaluate(async (text, id) => {
      try {
        await window.mermaid.parse(text)
        const { svg } = await window.mermaid.render('d' + id, text)
        return { ok: true, nodes: (svg.match(/<g class="node/g) || []).length }
      } catch (e) {
        return { ok: false, error: String(e.message || e).split('\n').slice(0, 4).join(' | ') }
      }
    }, src, i)
    const title = src.trim().split('\n')[0]
    if (result.ok) {
      console.log(`  OK   #${i + 1} ${title}  (노드 ${result.nodes}개)`)
    } else {
      failed++
      console.log(`  FAIL #${i + 1} ${title}`)
      console.log(`       ${result.error}`)
    }
  }
  }
} finally {
  await browser.close()
  server.close()
}
process.exit(failed ? 1 : 0)
