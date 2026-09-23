import os

from django.conf import settings

PARTICIPANT_FACTORY = "apps.campaigns.services.participant_environment.create_environment"


def execution_enabled():
    from apps.campaigns.services.public_environment import LOCAL_FACTORY, local_simulation_available

    return bool(settings.REDORDA_RUN_EXECUTION_ENABLED and settings.REDORDA_ENVIRONMENT_FACTORY
                and (settings.REDORDA_ENVIRONMENT_FACTORY != LOCAL_FACTORY
                     or local_simulation_available()))


def openai_enabled():
    return execution_enabled() and bool(os.getenv("OPENAI_API_KEY", "").strip())


def strategy_enabled(strategy):
    return openai_enabled() if strategy == "openai" else execution_enabled()


def environment_description():
    from apps.campaigns.services.public_environment import LOCAL_FACTORY

    if settings.REDORDA_ENVIRONMENT_FACTORY in {LOCAL_FACTORY, PARTICIPANT_FACTORY}:
        return {"mode": "local_simulation", "label": (
            "Локальный симулятор организаторов. Пилоты и прогноз используют учебную модель; "
            "это не результат судейства."
        )}
    return {"mode": "external", "label": "Внешняя среда расчёта"}


def dataset_execution_blocker(dataset):
    from apps.campaigns.services.public_environment import LOCAL_FACTORY

    if (settings.REDORDA_ENVIRONMENT_FACTORY in {LOCAL_FACTORY, PARTICIPANT_FACTORY}
            and dataset.summary.get("format") == "raw_csv"):
        return ("В этом наборе нет профиля абонентов (customer_profile.csv). "
                "Для расчёта нужен полный пакет участников; четыре CSV доступны для просмотра.")
    return None
