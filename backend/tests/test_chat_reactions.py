import pytest

from apps.chat.emoji import is_single_emoji


@pytest.mark.parametrize("value", ["👍", "❤️", "😂", "👨‍👩‍👧", "👍🏽", "🇺🇦", "🏴󠁧󠁢󠁳󠁣󠁴󠁿", "⭐", "✅", "🫠"])
def test_valid_emoji(value):
    assert is_single_emoji(value)


@pytest.mark.parametrize("value", ["", "a", "ok", "👍 ", "<b>", "1", "😀" * 11, None, 5, "‍", "👍\n"])
def test_invalid_emoji(value):
    assert not is_single_emoji(value)


def _conv(client_for, alice, bob):
    ca = client_for(alice)
    conv = ca.post("/api/chat/conversations/", {"usernames": ["bob"]}, format="json").json()
    msg = ca.post(f"/api/chat/conversations/{conv['id']}/messages/", {"body": "привіт"}, format="json").json()
    return ca, client_for(bob), conv, msg


@pytest.mark.django_db
def test_reaction_toggle_and_listing(alice, bob, client_for):
    ca, cb, conv, msg = _conv(client_for, alice, bob)
    url = f"/api/chat/messages/{msg['id']}/reactions/"
    assert cb.post(url, {"emoji": "👍"}, format="json").status_code == 200
    r = ca.post(url, {"emoji": "👍"}, format="json").json()
    assert r["reactions"] == [{"emoji": "👍", "count": 2, "users": ["bob", "alice"]}]
    # повторне натискання знімає реакцію
    r = cb.post(url, {"emoji": "👍"}, format="json").json()
    assert r["reactions"] == [{"emoji": "👍", "count": 1, "users": ["alice"]}]
    listed = ca.get(f"/api/chat/conversations/{conv['id']}/messages/").json()["results"]
    assert listed[0]["reactions"][0]["count"] == 1
    assert cb.post(url, {"emoji": "<script>"}, format="json").status_code == 400


@pytest.mark.django_db
def test_reaction_requires_membership(alice, bob, make_user, client_for):
    _, _, _, msg = _conv(client_for, alice, bob)
    eve = make_user("eve")
    r = client_for(eve).post(f"/api/chat/messages/{msg['id']}/reactions/", {"emoji": "👍"}, format="json")
    assert r.status_code == 404


@pytest.mark.django_db
def test_reaction_limits(alice, bob, client_for):
    ca, _, _, msg = _conv(client_for, alice, bob)
    url = f"/api/chat/messages/{msg['id']}/reactions/"
    emojis = ["😀", "😃", "😄", "😁", "😆", "😅", "🤣", "😂", "🙂", "🙃", "😉"]
    codes = [ca.post(url, {"emoji": e}, format="json").status_code for e in emojis]
    assert codes[:10] == [200] * 10 and codes[10] == 400


@pytest.mark.django_db
def test_mail_service_aliases(make_user, settings):
    from django.core.management import call_command

    from apps.mail.models import Alias

    make_user("yurii")
    call_command("mail_service_aliases", "--to", "yurii")
    a = Alias.objects.get(source=f"postmaster@{settings.MAIL_DOMAIN}")
    assert a.destination == f"yurii@{settings.MAIL_DOMAIN}"
