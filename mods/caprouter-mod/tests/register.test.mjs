import assert from 'node:assert/strict'
import { register } from '../hooks/register.js'

function setup(enabled) {
  const handlers = new Map()
  const on = (event, matcherOrHandler, maybeHandler) => {
    const handler = typeof matcherOrHandler === 'function' ? matcherOrHandler : maybeHandler
    handlers.set(event, handler)
  }
  const state = JSON.stringify({ schema: 'caprouter.state.v1', enabled })
  const commands = []
  const $ = {
    env: { get: async (key) => (key === 'CAPROUTER_STATE' ? '/tmp/caprouter-state.json' : undefined) },
    fs: { exists: async () => true, read: async () => state },
    command: { register: async (command) => commands.push(command) },
  }
  register(on)
  return { handlers, $, commands }
}

async function nextRecorder() {
  const seen = []
  const next = async (event) => {
    seen.push(event)
    return event
  }
  return { next, seen }
}

const enabled = setup(true)
assert.ok(enabled.handlers.has('session.start'))
assert.ok(enabled.handlers.has('command.run'))
assert.ok(enabled.handlers.has('prompt.submit'))

const startNext = await nextRecorder()
await enabled.handlers.get('session.start')(enabled.$, {}, startNext.next)
assert.equal(enabled.commands[0].name, 'caprouter')

const promptNext = await nextRecorder()
await enabled.handlers.get('prompt.submit')(
  enabled.$,
  { text: 'research the western fantasy web-novel market and write a report', context: [] },
  promptNext.next,
)
assert.equal(promptNext.seen.length, 1)
assert.ok(promptNext.seen[0].context.some((item) => item.includes('CapRouter is enabled')))

const shortNext = await nextRecorder()
await enabled.handlers.get('prompt.submit')(enabled.$, { text: 'hi', context: [] }, shortNext.next)
assert.equal(shortNext.seen[0].context.length, 0)

const disabled = setup(false)
const disabledNext = await nextRecorder()
await disabled.handlers.get('prompt.submit')(
  disabled.$,
  { text: 'research the western fantasy web-novel market and write a report', context: [] },
  disabledNext.next,
)
assert.equal(disabledNext.seen[0].context.length, 0)

const status = await enabled.handlers.get('command.run')(enabled.$, {}, async (e) => e)
assert.equal(status.text, 'CapRouter: enabled')

const dedupeNext = await nextRecorder()
await enabled.handlers.get('prompt.submit')(
  enabled.$,
  { text: 'research the western fantasy web-novel market and write a report', context: ['CapRouter is enabled. already present'] },
  dedupeNext.next,
)
assert.equal(dedupeNext.seen[0].context.length, 1)

console.log('mod tests OK')
