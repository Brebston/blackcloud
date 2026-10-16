"""Адресна книга і підказки одержувачів."""

import pytest
from rest_framework.test import APIClient

from apps.mail import contacts, imap
from apps.mail.models import Contact, RecipientStat

URL = "/api/mail/contacts/"


@pytest.mark.django_db
def test_contact_crud_and_isolation(alice, make_user, client_for):
    bob = make_user("bob")
    c = client_for(alice)
    r = c.post(URL, {
        "first_name": " Олена ", "last_name": "Коваль", "company": "Acme",
        "emails": [{"address": "Olena@Acme.com", "label": "робоча"}, {"address": "olena@acme.com"}],
        "phones": [{"number": "+380 (67) 123-45-67", "label": "мобільний"}],
    }, format="json")
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["first_name"] == "Олена" and body["display_name"] == "Олена Коваль"
    assert body["emails"] == [{"address": "olena@acme.com", "label": "робоча"}]  # дублікати прибрано
    cid = body["id"]
    assert [x["id"] for x in c.get(URL, {"q": "кова"}).json()] == [cid]
    assert c.get(URL, {"q": "acme.com"}).json()[0]["id"] == cid
    assert c.get(URL, {"q": "nothing"}).json() == []
    r = c.patch(f"{URL}{cid}/", {"starred": True, "notes": "Колега"}, format="json")
    assert r.status_code == 200 and r.json()["starred"] is True
    # Чужий контакт не видно і не змінити
    other = client_for(bob)
    assert other.get(URL).json() == []
    assert other.get(f"{URL}{cid}/").status_code == 404
    assert other.patch(f"{URL}{cid}/", {"notes": "x"}, format="json").status_code == 404
    assert other.delete(f"{URL}{cid}/").status_code == 404
    assert c.delete(f"{URL}{cid}/").status_code == 204
    assert not Contact.objects.exists()
    assert APIClient().get(URL).status_code in (401, 403)


@pytest.mark.django_db
def test_contact_validation(alice, client_for):
    c = client_for(alice)
    assert c.post(URL, {}, format="json").status_code == 400  # ні імені, ні email
    assert c.post(URL, {"emails": [{"address": "not-an-email"}]}, format="json").status_code == 400
    assert c.post(URL, {"first_name": "A", "phones": [{"number": "call me"}]}, format="json").status_code == 400
    many = [{"address": f"a{i}@x.com"} for i in range(11)]
    assert c.post(URL, {"emails": many}, format="json").status_code == 400
    assert c.post(URL, {"emails": [{"address": "only@email.com"}]}, format="json").status_code == 201


@pytest.mark.django_db
def test_suggest_ranks_like_gmail(alice, make_user, client_for):
    # Користувач BlackCloud (дозволив себе знаходити) і той, хто сховався
    oleh = make_user("oleh")
    hidden = make_user("olga")
    hidden.preferences.discoverable = False
    hidden.preferences.save()
    Contact.objects.create(owner=alice, first_name="Олена", last_name="Коваль", emails=[{"address": "olena@acme.com"}])
    Contact.objects.create(owner=alice, first_name="Petro", emails=[{"address": "petro@olx.ua"}])
    contacts.record_recipients(alice, ['"Ola Nowak" <ola@firma.pl>, ola@firma.pl'])
    contacts.record_recipients(alice, ["ola@firma.pl, someone@example.net"])

    c = client_for(alice)
    res = c.get(f"{URL}suggest/", {"q": "ol"}).json()
    addresses = [r["address"] for r in res]
    assert "ola@firma.pl" in addresses and "olena@acme.com" in addresses
    assert f"oleh@example.com" in addresses
    assert "olga@example.com" not in addresses  # не дозволила себе знаходити
    assert addresses[0] == "ola@firma.pl"  # писали двічі + збіг з початку адреси
    ola = next(r for r in res if r["address"] == "ola@firma.pl")
    assert ola["name"] == "Ola Nowak" and ola["sources"] == ["recent"]
    assert next(r for r in res if r["address"] == "oleh@example.com")["sources"] == ["user"]
    # Збіг усередині (petro@OLx.ua) — нижче за збіги з початку
    assert addresses.index("petro@olx.ua") > addresses.index("olena@acme.com")
    # Ім'я кирилицею
    assert c.get(f"{URL}suggest/", {"q": "кова"}).json()[0]["address"] == "olena@acme.com"
    assert c.get(f"{URL}suggest/", {"q": ""}).json() == []
    # Прибрати з підказок
    assert c.delete(f"{URL}recent/?address=ola@firma.pl").status_code == 204
    assert "ola@firma.pl" not in [r["address"] for r in c.get(f"{URL}suggest/", {"q": "ol"}).json()]
    # Свої контакти/листування чужим не підказуються
    assert "olena@acme.com" not in [r["address"] for r in client_for(oleh).get(f"{URL}suggest/", {"q": "ol"}).json()]


@pytest.mark.django_db
def test_sending_records_recipients(alice, client_for, monkeypatch):
    monkeypatch.setattr(imap, "send_message", lambda *a, **k: {"message_id": "<x@y>"})
    c = client_for(alice)
    r = c.post("/api/mail/send/", {"to": "Bob <bob@ext.com>", "cc": "carol@ext.com", "subject": "Hi", "text": "x"}, format="json")
    assert r.status_code == 201, r.content
    c.post("/api/mail/send/", {"to": "bob@ext.com", "subject": "Hi", "text": "x"}, format="json")
    stats = {s.address: (s.count, s.name) for s in RecipientStat.objects.filter(owner=alice)}
    assert stats == {"bob@ext.com": (2, "Bob"), "carol@ext.com": (1, "")}
    # Невалідні адреси не записуються
    contacts.record_recipients(alice, ["not an address, @@"])
    assert RecipientStat.objects.filter(owner=alice).count() == 2
