// Compatibility boundary for pinned SDK 0.1.5rc1: its JSON-RPC server always
// calls agents.create. Use the public native resume API for an existing root
// session. Persistence errors propagate; never replace a corrupt/locked log.
exports.name = 'netops-durable-session'
exports.inject = ['agents', 'sessionPersistence']
exports.apply = (ctx) => {
  const create = ctx.agents.create.bind(ctx.agents)
  ctx.agents.create = async (options) => {
    if (options.sessionId !== process.env.NETOPS_DSH_SESSION_ID) return create(options)
    const existing = await ctx.sessionPersistence.stat(options.sessionId)
    if (!existing) return create(options)
    return ctx.agents.resume({
      resumeSessionId: options.sessionId,
      agentOptions: options.agentOptions,
    })
  }
}
