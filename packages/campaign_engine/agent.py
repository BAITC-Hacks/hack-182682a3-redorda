from campaign_engine.contracts import AgentEnvironment


class Agent:
    """Developer 2 implements exploration and optimization here, without Django imports."""

    def act(self, env: AgentEnvironment) -> list[dict]:
        raise NotImplementedError(
            "Campaign strategy is not implemented in the scaffold. "
            "See docs/developers/02-agent.md, stage 1."
        )
