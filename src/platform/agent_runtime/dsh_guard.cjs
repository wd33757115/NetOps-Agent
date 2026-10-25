// Thin NetOps boundary around official DSH tools; no tool or Agent loop implementation.
const fs = require('node:fs')
const path = require('node:path')

exports.name = 'netops-tool-boundary'
exports.inject = ['tools']
exports.apply = (ctx) => {
  const root = fs.realpathSync(process.cwd())
  const allowed = new Set(JSON.parse(process.env.NETOPS_DSH_TOOLS))
  const inside = (value) => {
    if (typeof value !== 'string' || value.includes('\0')) return false
    try {
      const target = fs.realpathSync(path.resolve(root, value))
      const relative = path.relative(root, target)
      return relative === '' || (!relative.startsWith('..' + path.sep) && relative !== '..' && !path.isAbsolute(relative))
    } catch { return false }
  }
  ctx.tools.guard(exec => {
    if (!allowed.has(exec.name)) return 'Tool is not available in this NetOps run'
    if (['read', 'glob', 'grep'].includes(exec.name)) {
      const args = exec.arguments || {}
      if (!inside(exec.name === 'read' ? args.file_path : (args.path || '.'))) return 'Path is outside the authorized document workspace'
    }
  })
  const call = async (route, body, signal) => {
    const response = await fetch(process.env.NETOPS_DSH_CALLBACK + route, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + process.env.NETOPS_DSH_RUN_TOKEN },
      body: JSON.stringify(body),
      signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]),
    })
    if (!response.ok) throw new Error('NetOps run authorization or budget unavailable')
    return response.json()
  }
  ctx.on('tools/pre-execute', async (exec, next) => {
    await call('/before', { name: exec.name, call_id: exec.callId, arguments: exec.arguments }, exec.signal)
    return next()
  })
  ctx.on('tools/execute', async (exec, next) => {
    const result = await next()
    const value = result.value
    let summary = null
    if (!result.isError && exec.name === 'read' && Array.isArray(value?.lines)) {
      summary = { lines: value.lines.length, truncated: !!value.truncated }
    } else if (!result.isError && exec.name === 'grep' && Array.isArray(value?.matches)) {
      summary = { count: value.matches.length, truncated: value.matches.length > 40 }
    } else if (!result.isError && exec.name === 'glob' && Array.isArray(value?.paths)) {
      summary = { count: value.paths.length, truncated: value.paths.length > 60 }
    }
    await call('/after', {
      name: exec.name, call_id: exec.callId, arguments: exec.arguments,
      ok: !result.isError, value: exec.name === 'read' && !result.isError ? result.value : null,
      output: exec.name === 'mcp__netops__submit_answer' ? null : JSON.stringify(result.value ?? result).slice(0, 64000),
      summary,
    }, exec.signal)
    return result
  })
}
