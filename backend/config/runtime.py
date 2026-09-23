from django.conf import settings


def execution_enabled():
    from apps.campaigns.services.public_environment import LOCAL_FACTORY, local_simulation_available

    return bool(settings.REDORDA_RUN_EXECUTION_ENABLED and settings.REDORDA_ENVIRONMENT_FACTORY
                and (settings.REDORDA_ENVIRONMENT_FACTORY != LOCAL_FACTORY
                     or local_simulation_available()))


def environment_description():
    from apps.campaigns.services.public_environment import LOCAL_FACTORY

    if settings.REDORDA_ENVIRONMENT_FACTORY == LOCAL_FACTORY:
        return {"mode": "local_simulation", "label": (
            "Локальный симулятор организаторов. Пилоты и прогноз используют учебную модель; "
            "это не результат судейства."
        )}
    return {"mode": "external", "label": "Внешняя среда расчёта"}
