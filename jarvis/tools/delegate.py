from jarvis.tools import Ctx, tool


@tool("Hand a sub-task to a sub-agent and get its answer back. Agents: 'thinker' (strong "
      "reasoning, planning and analysis; no tools) and 'private' (runs locally, can search and "
      "open the user's files; use it for anything about their own files).",
      agent="string: thinker or private", task="string: the complete sub-task, self-contained")
async def delegate(ctx, agent, task):
    from jarvis.brain import agent_loop  # brain imports tools; import late to avoid a cycle
    a = ctx.cfg.agents.get(agent)
    if a is None:
        raise ValueError(f"no agent called {agent}; available: {', '.join(ctx.cfg.agents)}")
    await ctx.emit({"type": "agent", "agent": agent})
    child = Ctx(ctx.cfg, ctx.conn, ctx.confirm, ctx.emit, agent=agent, allowed=list(a.tools))
    text = await agent_loop(child, [{"role": "system", "content": a.prompt},
                                    {"role": "user", "content": task}],
                            a.route, a.tools, fallback=a.fallback)
    return text or f"The {agent} agent returned nothing.", None
