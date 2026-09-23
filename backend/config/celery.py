import os
import sys

from celery import Celery, current_app
from celery.signals import worker_process_init

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
app = Celery("redorda")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@worker_process_init.connect
def initialize_macos_child(**kwargs):
    # Celery 5.6 / Billiard uses spawn on macOS. Its fast task tracer's
    # process-local registry is empty in a spawned child (unlike Linux fork).
    # Initialize it after Celery registers tasks; retain process time limits.
    if sys.platform == "darwin":
        from celery.app.trace import setup_worker_optimizations

        setup_worker_optimizations(current_app._get_current_object())
