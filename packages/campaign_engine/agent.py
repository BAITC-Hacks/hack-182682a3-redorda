from campaign_engine.candidates import load_history
from campaign_engine.contracts import AgentEnvironment
from campaign_engine.engine_types import EngineFailure
from campaign_engine.runner import run_campaigns


class Agent:
    """Deterministic adapter for the shared runner."""

    def act(self, env: AgentEnvironment) -> list[dict]:
        result = run_campaigns(env, history=load_history())
        if result.status != "completed":
            raise EngineFailure(result.stop_reason)
        return result.campaigns
