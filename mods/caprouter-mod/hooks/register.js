// CapRouter host takeover for Claude Code.
// It reads the same ~/.caprouter/state.json as the tray and CLI.
// Requires Claude Code >= 2.1.287; run `claude plugin validate .` before use.

const TASK_VERBS = [
  'research', 'search', 'write', 'generate', 'analyze', 'check', 'verify',
  'organize', 'convert', 'design', 'implement', 'develop', 'fix', 'deploy',
  'test', 'report', 'plan', 'compare', 'extract', 'review', 'translate',
  'continue', 'poster', 'slides', 'table', 'code', 'document', 'summarize',
  'evaluate', 'audit', 'refactor', 'debug',
]

const CONTEXT =
  'CapRouter is enabled. For multi-step or local-capability tasks, use the caprouter skill: ' +
  'write a plan to .caprouter/plans/, run `caprouter plan check`, then `caprouter run`. ' +
  'Do not call WebSearch/WebFetch/native subagents directly. If CapRouter is not applicable, proceed normally.'

function shouldInject(text) {
  const value = String(text || '').trim()
  if (value.length < 12) return false
  if (value.startsWith('/')) return false
  const lowered = value.toLowerCase()
  if (lowered.includes('caprouter') || lowered.includes('do not use')) return false
  return TASK_VERBS.some((verb) => lowered.includes(verb))
}

async function stateEnabled($) {
  try {
    const home = (await $.env.get('USERPROFILE')) || (await $.env.get('HOME')) || ''
    let path = (await $.env.get('CAPROUTER_STATE')) || ''
    if (!path && home) path = home.replace(/\\/g, '/') + '/.caprouter/state.json'
    if (!path) return true
    const exists = await $.fs.exists(path)
    if (!exists) return true
    const raw = await $.fs.read(path)
    const text = typeof raw === 'string' ? raw : raw && raw.content
    if (!text) return true
    const data = JSON.parse(text)
    return data.enabled !== false
  } catch {
    return true
  }
}

export function register(on) {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'caprouter',
      description: 'Show CapRouter status (enabled/disabled)',
    })
    return next(e)
  })

  on('command.run', { command: 'caprouter' }, async ($) => {
    const enabled = await stateEnabled($)
    return { text: enabled ? 'CapRouter: enabled' : 'CapRouter: disabled' }
  })

  on('prompt.submit', async ($, e, next) => {
    if (!(await stateEnabled($))) return next(e)
    if (!shouldInject(e.text)) return next(e)
    const already = Array.isArray(e.context) && e.context.some((item) => String(item).includes('CapRouter is enabled'))
    if (already) return next(e)
    return next({ ...e, context: [...(e.context ?? []), CONTEXT] })
  })
}
