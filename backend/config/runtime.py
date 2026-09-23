from django.conf import settings


def execution_enabled():
    # Operators enable this only after connecting and validating the actual shared agent.
    return bool(settings.REDORDA_RUN_EXECUTION_ENABLED and settings.REDORDA_ENVIRONMENT_FACTORY)
