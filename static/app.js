const ownerInput = document.querySelector('#owner')
const repoInput = document.querySelector('#repo')
const stateSelect = document.querySelector('#state')
const issueNumberInput = document.querySelector('#issue-number')
const statusText = document.querySelector('#status')
const result = document.querySelector('#result')
const tokenState = document.querySelector('#token-state')
const list = document.querySelector('#list')
const listTitle = document.querySelector('#list-title')
const pager = document.querySelector('#pager')
const pagerPage = document.querySelector('#pager-page')
const pagePrev = document.querySelector('#page-prev')
const pageNext = document.querySelector('#page-next')
const listSearchInput = document.querySelector('#list-search')
const listNote = document.querySelector('#list-note')

function setStatus(message, kind = '') {
  statusText.textContent = message
  statusText.className = `status ${kind}`
}

function showResult(value) {
  result.textContent = JSON.stringify(value, null, 2)
}

function showListEmpty(message) {
  list.innerHTML = ''
  const item = document.createElement('li')
  item.className = 'list-empty'
  item.textContent = message
  list.append(item)
}

// 关键词过滤只看标题与编号，纯字符串比较，不做排序，因此命中结果保持接口返回的原有顺序。
function matchesQuery(target, entry) {
  if (!target.appliedQuery) return true
  const title = String(entry.title || '').toLowerCase()
  const number = String(entry.number)
  return title.includes(target.appliedQuery) || number.includes(target.appliedQuery)
}

// 只重建 #list 的子节点：#pager 是 #list 的兄弟节点，页码文字与按钮禁用态由 renderPager 独立维护，
// 因此过滤永远不会改动分页状态。
function renderList(target, items, { filtered = false } = {}) {
  list.innerHTML = ''
  if (!items.length) {
    // 区分「这一页本来就没有条目」与「当前关键词在本页没有命中」，避免用户误判搜索范围。
    showListEmpty(
      filtered
        ? `未在当前已加载的 ${target.items.length} 条中找到与“${target.query.trim()}”匹配的条目（仅搜索本页）。`
        : '没有符合条件的条目。',
    )
    return
  }
  items.forEach((entry) => {
    const item = document.createElement('li')
    const button = document.createElement('button')
    button.className = 'list-item'
    const title = entry.title || '(无标题)'
    const extra = target.kind === 'pulls' ? `${entry.head} → ${entry.base}` : (entry.labels || []).join(', ')
    button.textContent = `#${entry.number} ${title}${extra ? ` · ${extra}` : ''}`
    button.addEventListener('click', () => selectEntry(target.kind, entry))
    item.append(button)
    list.append(item)
  })
}

function renderListNote(target, matched) {
  if (!target) {
    listNote.textContent = '先读取列表，再按标题或 #编号过滤当前已加载的一页；不会请求 GitHub，也不跨页搜索。'
    return
  }
  const scope = `${target.label}第 ${target.page} 页共 ${target.items.length} 条`
  if (!target.appliedQuery) {
    listNote.textContent = `${scope}；按标题或 #编号过滤当前已加载的这一页，不会请求 GitHub，也不跨页搜索。`
    return
  }
  listNote.textContent = `当前关键词仅匹配已加载的这一页（${scope}），命中 ${matched} 条；不会请求 GitHub，也不跨页搜索。`
}

// 搜索与加载共用这一条渲染路径，避免出现两处各自渲染导致搜索结果被加载结果覆盖。
function applyFilter(target) {
  if (!target) return
  const matched = target.items.filter((entry) => matchesQuery(target, entry))
  listTitle.textContent = target.appliedQuery
    ? `${target.label} · ${currentQuery().state} · 命中 ${matched.length}/${target.items.length}`
    : `${target.label} · ${currentQuery().state}`
  renderListNote(target, matched.length)
  renderList(target, matched, { filtered: Boolean(target.appliedQuery) })
}

// 输入框的值来自列表状态而不是事件本身，重置两个列表后再统一回写一次，避免互相覆盖。
function syncSearchInput() {
  const target = currentState()
  listSearchInput.value = target ? target.query : ''
  listSearchInput.disabled = !target
  listSearchInput.setAttribute('aria-disabled', String(!target))
}

function selectEntry(kind, entry) {
  showResult(entry)
  issueNumberInput.value = String(entry.number)
  setStatus(`已选择 #${entry.number}，可点击“读取 ${kind === 'pulls' ? 'Pull Request' : 'Issue'} 上下文”继续`, 'ok')
}

function setPill(text, kind = '') {
  tokenState.textContent = text
  tokenState.className = `pill ${kind}`
}

function summarizeDiagnostics(payload) {
  const rate = payload.rate_limit || {}
  const repository = payload.repository || {}
  if (rate.ok === false) {
    setPill(`配额读取失败：${rate.status || '网络错误'}`, 'warn')
    return `自检发现问题：${(payload.problems || []).join('、')}`
  }
  setPill(`配额剩余 ${rate.remaining}/${rate.limit}`, rate.remaining > 0 ? 'ok' : 'warn')
  if (repository.skip) return `自检完成：配额剩余 ${rate.remaining}，未做仓库探测`
  if (repository.ok === false) return `自检发现问题：仓库 ${repository.full_name} 不可达（${repository.status || '网络错误'}）`
  return `自检通过：仓库 ${repository.full_name} 可达，配额剩余 ${rate.remaining}`
}

document.querySelector('#diagnostics-button').addEventListener('click', async () => {
  const owner = ownerInput.value.trim()
  const repo = repoInput.value.trim()
  setStatus('正在执行上游自检…')
  try {
    const query = owner && repo ? `?owner=${encodeURIComponent(owner)}&repo=${encodeURIComponent(repo)}` : ''
    const payload = await requestJson(`/api/diagnostics${query}`)
    // 自检接口失败也返回 200，因此要靠 problems 判断，而不是靠 HTTP 状态码。
    setStatus(summarizeDiagnostics(payload), (payload.problems || []).length ? 'error' : 'ok')
    showResult(payload)
  } catch (error) {
    setPill('自检失败', 'warn')
    showResult({ error: error.message })
    setStatus(error.message, 'error')
  }
})

async function requestJson(url, options) {
  const response = await fetch(url, options)
  const payload = await response.json().catch(() => ({ detail: response.statusText }))
  if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`)
  return payload
}

document.querySelector('#health-button').addEventListener('click', async () => {
  setStatus('正在检查 FastAPI…')
  try {
    const payload = await requestJson('/api/health')
    tokenState.textContent = payload.github_token_configured ? 'Token 已配置' : 'Token 未配置'
    tokenState.className = `pill ${payload.github_token_configured ? 'ok' : 'warn'}`
    showResult(payload)
    setStatus('FastAPI 健康检查通过', 'ok')
  } catch (error) {
    setStatus(error.message, 'error')
  }
})

document.querySelector('#repo-button').addEventListener('click', async () => {
  const owner = ownerInput.value.trim()
  const repo = repoInput.value.trim()
  setStatus('正在请求 GitHub 仓库信息…')
  try {
    const payload = await requestJson(`/api/repository?owner=${encodeURIComponent(owner)}&repo=${encodeURIComponent(repo)}`)
    showResult(payload)
    setStatus(`读取成功：${payload.full_name}`, 'ok')
  } catch (error) {
    showResult({ error: error.message })
    setStatus(error.message, 'error')
  }
})

// 每份列表各自持有页码、分页状态与搜索状态，互不干扰。
// `seq` 是逐请求自增的竞态令牌：响应回来时若与当前值不符，说明这是过期结果，直接丢弃。
// `loading` 是防重复请求闸门，与 `seq` 职责不同，两者都要保留。
// `items` 保存接口原样返回的这一页条目，是关键词过滤的唯一数据源；
// `query` 是用户输入的原始关键词，`appliedQuery` 是用于匹配的规范化关键词（trim + 小写）。
const listStates = {
  pulls: { endpoint: '/api/pulls', kind: 'pulls', label: 'Pull Request 列表', page: 1, hasMore: false, nextPage: null, loading: false, seq: 0, items: [], query: '', appliedQuery: '' },
  issues: { endpoint: '/api/issues', kind: 'issues', label: 'Issue 列表', page: 1, hasMore: false, nextPage: null, loading: false, seq: 0, items: [], query: '', appliedQuery: '' },
}
let activeKind = null

function currentState() {
  return activeKind ? listStates[activeKind] : null
}

function currentQuery() {
  return {
    owner: ownerInput.value.trim(),
    repo: repoInput.value.trim(),
    state: stateSelect.value,
  }
}

// 只在翻到第 2 页起才带上 page，与后端「首屏请求不额外带参数」的约定保持一致。
function buildListUrl(target, page) {
  const { owner, repo, state } = currentQuery()
  const params = [
    `owner=${encodeURIComponent(owner)}`,
    `repo=${encodeURIComponent(repo)}`,
    `state=${encodeURIComponent(state)}`,
  ]
  if (page && page > 1) params.push(`page=${encodeURIComponent(page)}`)
  return `${target.endpoint}?${params.join('&')}`
}

// 「还有下一页」只读接口返回的 has_more，绝不用 count === per_page 之类的条数推断。
function renderPager(target) {
  if (!target) {
    pager.hidden = true
    return
  }
  pager.hidden = false
  pagerPage.textContent = target.loading ? `第 ${target.page} 页 · 加载中…` : `第 ${target.page} 页`
  pagePrev.disabled = target.loading || target.page <= 1
  pageNext.disabled = target.loading || !target.hasMore
}

// 切换列表、切换筛选条件或切换仓库时调用：页码归 1、清空已加载条目与搜索词，并立刻清空旧结果，
// 避免上一份列表被恢复后连旧关键词一起显示出来。
function resetList(target, message) {
  target.page = 1
  target.hasMore = false
  target.nextPage = null
  target.loading = false
  target.seq += 1
  target.items = []
  target.query = ''
  target.appliedQuery = ''
  listTitle.textContent = '尚未读取'
  renderListNote(null, 0)
  showListEmpty(message)
  renderPager(null)
}

function resetAllLists() {
  Object.values(listStates).forEach((target) => resetList(target, '仓库已变更，请重新读取列表。'))
  activeKind = null
  // 两个列表都重置完再统一回写输入框，避免前者刚清空就被后者覆盖。
  syncSearchInput()
}

async function loadList(kind, page) {
  const target = listStates[kind]
  activeKind = kind
  // 翻页保留已输入的关键词：输入框里有什么就是用户此刻的意图，不应被翻页悄悄清掉。
  listTitle.textContent = `${target.label} · ${currentQuery().state}`
  const token = ++target.seq
  target.loading = true
  syncSearchInput()
  renderPager(target)
  setStatus(`正在请求${target.label}（第 ${page || target.page} 页）…`)
  try {
    const payload = await requestJson(buildListUrl(target, page))
    if (token !== target.seq) return // 过期响应：期间已有更新的请求发出，丢弃本次结果
    target.page = payload.page || 1
    target.hasMore = payload.has_more === true
    target.nextPage = payload.next_page ?? null
    target.loading = false
    // 整页覆盖原始条目，再走统一渲染入口应用关键词过滤。
    target.items = payload[kind] || []
    applyFilter(target)
    showResult(payload)
    renderPager(target)
    const scope = target.hasMore ? '还有下一页' : '已到最后一页'
    setStatus(`${target.label}第 ${target.page} 页共 ${payload.count} 条（state=${payload.state}，${scope}）`, 'ok')
  } catch (error) {
    if (token !== target.seq) return
    target.loading = false
    target.items = []
    renderListNote(target, 0)
    showListEmpty('读取失败。')
    showResult({ error: error.message })
    renderPager(null)
    setStatus(error.message, 'error')
  }
}

pagePrev.addEventListener('click', () => {
  const target = currentState()
  if (!target || target.loading || target.page <= 1) return
  loadList(target.kind, target.page - 1)
})

pageNext.addEventListener('click', () => {
  const target = currentState()
  if (!target || target.loading || !target.hasMore) return
  loadList(target.kind, target.page + 1)
})

// 把输入框的值回写到当前列表的搜索状态。这里不发任何请求：过滤只作用于已加载的那一页。
listSearchInput.addEventListener('input', () => {
  const target = currentState()
  if (!target) return
  target.query = listSearchInput.value
  target.appliedQuery = listSearchInput.value.trim().toLowerCase()
  applyFilter(target)
})

// 切换状态筛选或仓库后，页码归 1、清空旧列表与搜索词，防止旧结果和旧关键词混入新查询。
stateSelect.addEventListener('change', resetAllLists)
ownerInput.addEventListener('input', resetAllLists)
repoInput.addEventListener('input', resetAllLists)

document.querySelector('#pulls-button').addEventListener('click', () => {
  resetList(listStates.pulls, '正在加载…')
  // 先重置再立即启用输入框，用户可以在请求返回前就把关键词打好。
  syncSearchInput()
  loadList('pulls', 1)
})

document.querySelector('#issues-button').addEventListener('click', () => {
  resetList(listStates.issues, '正在加载…')
  syncSearchInput()
  loadList('issues', 1)
})

document.querySelector('#issue-button').addEventListener('click', async () => {
  const owner = ownerInput.value.trim()
  const repo = repoInput.value.trim()
  const number = issueNumberInput.value.trim()
  if (!number) {
    setStatus('请填写 Issue 编号', 'error')
    return
  }
  setStatus(`正在请求 Issue #${number} 上下文…`)
  try {
    const payload = await requestJson(`/api/issues/${encodeURIComponent(number)}/context?owner=${encodeURIComponent(owner)}&repo=${encodeURIComponent(repo)}`)
    showResult(payload)
    setStatus(`读取成功：Issue #${payload.issue.number} ${payload.issue.title}`, 'ok')
  } catch (error) {
    showResult({ error: error.message })
    setStatus(error.message, 'error')
  }
})
