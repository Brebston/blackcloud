import shutil

import pytest
from django.conf import settings
from rest_framework.test import APIClient

from apps.accounts.models import User


@pytest.fixture(autouse=True)
def _clean_object_store():
    yield
    shutil.rmtree(settings.LOCAL_OBJECT_STORE_DIR, ignore_errors=True)
    from apps.storage.objectstore import get_store

    get_store.cache_clear()


@pytest.fixture(autouse=True)
def _clear_cache():
    from django.core.cache import cache

    cache.clear()
    yield


@pytest.fixture
def password():
    return "Very-Strong-Passw0rd!"


@pytest.fixture
def make_user(db, password):
    def factory(username="alice", **extra):
        return User.objects.create_user(username, f"{username}@example.org", password, **extra)

    return factory


@pytest.fixture
def alice(make_user):
    return make_user("alice")


@pytest.fixture
def bob(make_user):
    return make_user("bob")


@pytest.fixture
def client_for():
    def factory(user, mfa=False):
        """mfa=True — справжня сесія, що пройшла другий фактор (потрібно для адмін-API)."""
        c = APIClient()
        if mfa:
            c.force_login(user)
            session = c.session
            session["mfa"] = True
            session.save()
        else:
            c.force_authenticate(user=user)
        return c

    return factory


@pytest.fixture(autouse=True)
def _immediate_on_commit(monkeypatch):
    """У тестах кожен тест обгорнутий транзакцією, тож on_commit виконуємо одразу."""
    from django.db import transaction

    monkeypatch.setattr(transaction, "on_commit", lambda fn, using=None, robust=False: fn())
