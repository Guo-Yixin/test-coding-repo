const ownerInput = document.querySelector('#owner')
const repoInput = document.querySelector('#repo')
const issueNumberInput = document.querySelector('#issue-number')
const statusText = document.querySelector('#status')
const result = document.querySelector('#result')
const tokenState = document.querySelector('#token-state')
const list = document.querySelector('#list')

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

async function loadList(endpoint, kind, label) {
  const owner = ownerInput.value.trim()
  const repo = repoInput.value.trim()
  setStatus(`正在请求${label}…`)
  try {
    const payload = await requestJson(`${endpoint}?owner=${encodeURIComponent(owner)}&repo=${encodeURIComponent(repo)}&state=open`)
    const items = payload[kind] || []
    renderList(kind, items)
    showResult(payload)
    setStatus(`${label}共 ${payload.count} 条（state=${payload.state}）`, 'ok')
  } catch (error) {
    showListEmpty('读取失败。')
    showResult({ error: error.message })
    setStatus(error.message, 'error')
  }
}

document.querySelector('#pulls-button').addEventListener('click', () => loadList('/api/pulls', 'pulls', 'Pull Request 列表'))

document.querySelector('#issues-button').addEventListener('click', () => loadList('/api/issues', 'issues', 'Issue 列表'))

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
