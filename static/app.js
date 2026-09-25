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

function renderList(kind, items) {
  list.innerHTML = ''
  if (!items.length) {
    showListEmpty('没有符合条件的条目。')
    return
  }
  items.forEach((entry) => {
    const item = document.createElement('li')
    const button = document.createElement('button')
    button.className = 'list-item'
    const title = entry.title || '(无标题)'
    const extra = kind === 'pulls' ? `${entry.head} → ${entry.base}` : (entry.labels || []).join(', ')
    button.textContent = `#${entry.number} ${title}${extra ? ` · ${extra}` : ''}`
    button.addEventListener('click', () => selectEntry(kind, entry))
    item.append(button)
    list.append(item)
  })
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

// 每份列表各自持有页码与分页状态，互不干扰。
// `seq` 是逐请求自增的竞态令牌：响应回来时若与当前值不符，说明这是过期结果，直接丢弃。
// `loading` 是防重复请求闸门，与 `seq` 职责不同，两者都要保留。
const listStates = {
  pulls: { endpoint: '/api/pulls', kind: 'pulls', label: 'Pull Request 列表', page: 1, hasMore: false, nextPage: null, loading: false, seq: 0 },
  issues: { endpoint: '/api/issues', kind: 'issues', label: 'Issue 列表', page: 1, hasMore: false, nextPage: null, loading: false, seq: 0 },
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

// 切换列表、切换筛选条件或切换仓库时调用：页码归 1 并立刻清空旧结果，避免旧数据混入新查询。
function resetList(target, message) {
  target.page = 1
  target.hasMore = false
  target.nextPage = null
  target.loading = false
  target.seq += 1
  listTitle.textContent = '尚未读取'
  showListEmpty(message)
  renderPager(null)
}

function resetAllLists() {
  Object.values(listStates).forEach((target) => resetList(target, '仓库已变更，请重新读取列表。'))
  activeKind = null
}

async function loadList(kind, page) {
  const target = listStates[kind]
  activeKind = kind
  listTitle.textContent = `${target.label} · ${currentQuery().state}`
  const token = ++target.seq
  target.loading = true
  renderPager(target)
  setStatus(`正在请求${target.label}（第 ${page || target.page} 页）…`)
  try {
    const payload = await requestJson(buildListUrl(target, page))
    if (token !== target.seq) return // 过期响应：期间已有更新的请求发出，丢弃本次结果
    target.page = payload.page || 1
    target.hasMore = payload.has_more === true
    target.nextPage = payload.next_page ?? null
    target.loading = false
    renderList(kind, payload[kind] || [])
    showResult(payload)
    renderPager(target)
    const scope = target.hasMore ? '还有下一页' : '已到最后一页'
    setStatus(`${target.label}第 ${target.page} 页共 ${payload.count} 条（state=${payload.state}，${scope}）`, 'ok')
  } catch (error) {
    if (token !== target.seq) return
    target.loading = false
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

// 切换状态筛选或仓库后，页码归 1 并清空旧列表，防止旧结果混入新查询。
stateSelect.addEventListener('change', resetAllLists)
ownerInput.addEventListener('input', resetAllLists)
repoInput.addEventListener('input', resetAllLists)

document.querySelector('#pulls-button').addEventListener('click', () => {
  resetList(listStates.pulls, '正在加载…')
  loadList('pulls', 1)
})

document.querySelector('#issues-button').addEventListener('click', () => {
  resetList(listStates.issues, '正在加载…')
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
