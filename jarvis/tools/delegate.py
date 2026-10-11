from jarvis.tools import Ctx, tool


@tool("Hand a sub-task to a sub-agent and get its answer back. Agents: 'thinker' (strong "
      "reasoning, planning and analysis; no tools), 'private' (runs locally, can search, read "
      "and open the user's files) and 'researcher' (searches the web and reads pages, returns "
      "a cited answer).",
      readonly=True,   # the agent's own tool calls are checked (and confirmed) one by one
      agent="string: thinker, private or researcher",
      task="string: the complete sub-task, self-contained")
async def delegate(ctx, agent, task):
    from jarvis.brain import agent_loop  # brain imports tools; import late to avoid a cycle
    a = ctx.cfg.agents.get(agent)
    if a is None:
        raise ValueError(f"no agent called {agent}; available: {', '.join(ctx.cfg.agents)}")
    await ctx.emit({"type": "agent", "agent": agent})
    child = Ctx(ctx.cfg, ctx.conn, ctx.confirm, ctx.emit, agent=agent, allowed=list(a.tools),
                tainted=ctx.tainted, private=ctx.private, remote=ctx.remote)
    text = await agent_loop(child, [{"role": "system", "content": a.prompt},
                                    {"role": "user", "content": task}],
                            a.route, a.tools, fallback=a.fallback)
    # What the agent read shapes its answer: carry its privacy and taint back to this turn.
    ctx.private |= child.private or (not a.fallback and ctx.cfg.content_private)
    ctx.tainted |= child.tainted
    return text or f"The {agent} agent returned nothing.", None
