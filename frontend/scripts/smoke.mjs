/**
 * 빌드된 프로덕션 번들을 실제 Chrome 으로 렌더링해 확인한다.
 *
 * jsdom 은 type="module" 스크립트를 실행하지 못해 Vite 빌드를 띄울 수 없다.
 * 그래서 시스템에 설치된 Chrome 을 headless 로 띄운다. 브라우저를 따로
 * 내려받지 않으므로 puppeteer-core 만 쓴다.
 *
 *   node scripts/smoke.mjs [base-url] [--screenshot out.png]
 *
 * 백엔드(또는 tools/mock_backend.py)가 base-url 에서 떠 있어야 한다.
 */

import { existsSync } from 'node:fs'
import puppeteer from 'puppeteer-core'

const args = process.argv.slice(2)
const base = args.find((a) => a.startsWith('http')) ?? 'http://127.0.0.1:8080'
const shotIndex = args.indexOf('--screenshot')
const shotPath = shotIndex >= 0 ? args[shotIndex + 1] : null

const CANDIDATES = [
  process.env.CHROME_PATH,
  '/opt/google/chrome/chrome',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
].filter(Boolean)

const executablePath = CANDIDATES.find((p) => existsSync(p))
if (!executablePath) {
  console.error('Chrome 을 찾을 수 없다. CHROME_PATH 를 지정하라.')
  process.exit(2)
}

const browser = await puppeteer.launch({
  executablePath,
  headless: true,
  args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
})

const problems = []
try {
  const page = await browser.newPage()
  await page.setViewport({ width: 1280, height: 900 })
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`))
  page.on('console', (m) => {
    if (m.type() === 'error') problems.push(`console.error: ${m.text()}`)
  })
  page.on('requestfailed', (r) => {
    // WebSocket 은 테스트 환경에서 끊길 수 있으므로 무시한다
    if (!r.url().includes('/stream')) {
      problems.push(`requestfailed: ${r.url()} ${r.failure()?.errorText ?? ''}`)
    }
  })

  const response = await page.goto(base, { waitUntil: 'networkidle2', timeout: 20000 })
  console.log(`HTTP ${response?.status()} ${base}`)

  // 노드 카드가 뜰 때까지 기다린다
  await page.waitForSelector('.node-card', { timeout: 15000 })

  const summary = await page.evaluate(() => ({
    title: document.title,
    brand: document.querySelector('.brand')?.textContent?.trim(),
    cards: [...document.querySelectorAll('.node-card')].map((el) => ({
      name: el.querySelector('code')?.textContent,
      alive: el.querySelector('.dot--ok') !== null,
      text: el.textContent.replace(/\s+/g, ' ').trim(),
    })),
    sections: [...document.querySelectorAll('.panel > h2')].map((h) => h.textContent.trim()),
  }))

  console.log(`\ntitle  : ${summary.title}`)
  console.log(`브랜드 : ${summary.brand}`)
  console.log(`섹션   : ${summary.sections.join(', ')}`)
  console.log('\n노드 카드:')
  for (const c of summary.cards) {
    console.log(`  ${c.alive ? '●' : '○'} ${c.name}`)
    console.log(`     ${c.text.slice(0, 110)}`)
  }

  const expect = (label, condition) => {
    console.log(`  ${condition ? 'OK  ' : 'FAIL'} ${label}`)
    if (!condition) problems.push(`검증 실패: ${label}`)
  }
  console.log('\n확인:')
  expect('타이틀이 AV Config', summary.title === 'AV Config')
  // --new-sensor 를 쓰면 미등록 노드가 하나 더 붙는다
  expect('노드 카드가 3개 이상', summary.cards.length >= 3)
  expect('lidar 노드가 살아 있음', summary.cards.some((c) => c.name?.includes('os_driver') && c.alive))
  expect('perception 노드가 꺼져 있음', summary.cards.some((c) => c.name?.includes('perception') && !c.alive))
  expect('매핑 모드 표시', summary.cards.some((c) => c.text.includes('ouster_ros')))
  expect('섹션 수가 카드 수와 맞는다', summary.sections.length >= 3)

  // 파라미터 편집 화면으로 이동. 스키마가 매칭된 라이다 노드를 골라야
  // enum/슬라이더/배열 같은 타입별 렌더링을 전부 확인할 수 있다.
  await page.evaluate(() => {
    const cards = [...document.querySelectorAll('.node-card')]
    const target = cards.find((c) => c.textContent.includes('os_driver'))
    if (!target) throw new Error('os_driver 카드를 찾을 수 없다')
    target.click()
  })
  await page.waitForSelector('.param-row', { timeout: 15000 })
  const editor = await page.evaluate(() => ({
    rows: document.querySelectorAll('.param-row').length,
    drift: document.querySelectorAll('.param-drift').length,
    inputs: {
      text: document.querySelectorAll('input[type=text]').length,
      number: document.querySelectorAll('input[type=number]').length,
      range: document.querySelectorAll('input[type=range]').length,
      checkbox: document.querySelectorAll('input[type=checkbox]').length,
      select: document.querySelectorAll('select').length,
      textarea: document.querySelectorAll('textarea').length,
    },
    badges: [...new Set([...document.querySelectorAll('.badge')].map((b) => b.textContent.trim()))],
  }))
  console.log('\n파라미터 편집 화면:')
  console.log(`  필드 ${editor.rows}개, drift 경고 ${editor.drift}개`)
  console.log(`  입력 요소 ${JSON.stringify(editor.inputs)}`)
  console.log(`  배지 ${editor.badges.join(' / ')}`)
  expect('파라미터 필드가 렌더링됨', editor.rows > 10)
  expect('enum 은 select 로 렌더링', editor.inputs.select >= 1)
  expect('범위가 있는 숫자는 슬라이더', editor.inputs.range >= 1)
  expect('숫자 입력이 있음', editor.inputs.number >= 1)
  expect('bool 은 체크박스', editor.inputs.checkbox >= 1)
  expect('배열 입력이 있음', editor.inputs.textarea + editor.inputs.text > 0)
  expect('적용 방식 배지 표시', editor.badges.some((b) => b.includes('재시작')))
  expect('등급 배지 표시', editor.badges.includes('engineer'))

  // 값을 바꿔 change set 트레이가 뜨는지
  await page.evaluate(() => {
    const select = document.querySelector('select')
    const option = [...select.options].find((o) => o.value !== select.value)
    select.value = option.value
    select.dispatchEvent(new Event('change', { bubbles: true }))
  })
  await new Promise((r) => setTimeout(r, 500))
  const tray = await page.evaluate(() => {
    const el = document.querySelector('.tray')
    return el ? el.textContent.replace(/\s+/g, ' ').trim().slice(0, 200) : null
  })
  console.log('\n변경 검토 트레이:')
  console.log(`  ${tray ?? '(나타나지 않음)'}`)
  expect('값을 바꾸면 변경 검토가 뜬다', tray !== null && tray.includes('변경'))

  // 미등록 노드가 있으면 온보딩 화면을 확인한다 (--new-sensor 로 띄운 경우)
  await page.goto(base, { waitUntil: 'networkidle2' })
  await page.waitForSelector('.node-card')
  const hasUnregistered = await page.evaluate(() => {
    const card = [...document.querySelectorAll('.node-card')].find((c) =>
      c.querySelector('.node-card__badge'),
    )
    if (card) card.click()
    return Boolean(card)
  })

  if (hasUnregistered) {
    await page.waitForSelector('.draft', { timeout: 15000 })
    const ob = await page.evaluate(() => ({
      heading: document.querySelector('.onboarding__head h3')?.textContent?.trim(),
      drafts: [...document.querySelectorAll('.draft__head strong')].map((e) =>
        e.textContent.trim(),
      ),
      steps: document.querySelectorAll('.onboarding__steps li').length,
      todoBadges: [...document.querySelectorAll('.badge--warn')]
        .map((b) => b.textContent.trim())
        .filter((t) => t.startsWith('TODO')),
      locked: document.body.textContent.includes('등록되지 않아 변경을 저장할 곳이 없다'),
      snippetHasNodeName: [...document.querySelectorAll('.draft__code')].some((c) =>
        c.textContent.includes('nodes:'),
      ),
    }))
    console.log('\n온보딩 화면:')
    console.log(`  제목     : ${ob.heading}`)
    console.log(`  초안     : ${ob.drafts.join(' / ')}`)
    console.log(`  절차     : ${ob.steps}단계`)
    console.log(`  TODO 배지: ${ob.todoBadges.join(', ') || '없음'}`)
    expect('미등록 안내가 뜬다', ob.heading?.includes('등록되지 않은'))
    expect('초안 3종이 보인다', ob.drafts.length === 3)
    expect('절차 8단계가 보인다', ob.steps === 8)
    expect('편집이 잠긴 이유를 설명한다', ob.locked)
    expect('services.yaml 조각이 렌더링됨', ob.snippetHasNodeName)
    expect('TODO 개수를 센다', ob.todoBadges.length > 0)
  } else {
    console.log('\n온보딩 화면: 미등록 노드가 없어 건너뜀 (--new-sensor 로 확인 가능)')
  }

  // SPA 딥링크: 직접 요청해도 떠야 한다 (새로고침, 북마크, URL 공유)
  const deepUrl = page.url()
  const reloaded = await page.goto(deepUrl, { waitUntil: 'networkidle2' })
  const stillRenders = await page
    .waitForSelector('.param-row', { timeout: 10000 })
    .then(() => true)
    .catch(() => false)
  console.log('\n딥링크 새로고침:')
  console.log(`  ${deepUrl} -> HTTP ${reloaded?.status()}`)
  expect('딥링크를 직접 요청해도 렌더링된다', reloaded?.status() === 200 && stillRenders)

  if (shotPath) {
    await page.screenshot({ path: shotPath, fullPage: true })
    console.log(`\n스크린샷 저장: ${shotPath}`)
  }
} finally {
  await browser.close()
}

if (problems.length) {
  console.log('\n문제:')
  for (const p of problems) console.log('  -', p)
  process.exit(1)
}
console.log('\n전부 통과')
