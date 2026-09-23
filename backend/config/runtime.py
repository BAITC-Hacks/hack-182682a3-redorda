import os

from django.conf import settings


def execution_enabled():
    # Operators enable this only after connecting and validating the actual shared agent.
    return bool(settings.REDORDA_RUN_EXECUTION_ENABLED and settings.REDORDA_ENVIRONMENT_FACTORY)


def openai_enabled():
    return execution_enabled() and bool(os.getenv("OPENAI_API_KEY", "").strip())


def strategy_enabled(strategy):
    return openai_enabled() if strategy == "openai" else execution_enabled()
