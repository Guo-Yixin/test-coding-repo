const ownerInput = document.querySelector('#owner')
const repoInput = document.querySelector('#repo')
const statusText = document.querySelector('#status')
const result = document.querySelector('#result')
const tokenState = document.querySelector('#token-state')

function setStatus(message, kind = '') {
  statusText.textContent = message
  statusText.className = `status ${kind}`
}

function showResult(value) {
  result.textContent = JSON.stringify(value, null, 2)
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
