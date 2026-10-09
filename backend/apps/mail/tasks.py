from celery import shared_task

from . import confidential


@shared_task
def purge_expired_confidential():
    return confidential.purge_expired()
