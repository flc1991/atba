"""Admin editing of trial entries, and verification emails per person."""
import io
from datetime import date

import pytest
from openpyxl import load_workbook

from app.models.payment import Payment
from app.models.trial import Trial, TrialEntry, TrialEntrySelection, TrialEvent, TrialEventClass
from app.utils import email_helpers
from tests.test_payment_features import check_of, csrf, promise_check, sent  # noqa: F401
from tests.test_payment_security import (  # noqa: F401
    ADDR, add, app, client, db, login, make_entry, make_user, paypal,
)


@pytest.fixture
def admin(app, db):  # noqa: F811
    user = make_user(db, "admin@x.com", role="admin")
    login(app, user)
    return user


@pytest.fixture
def entry(db):  # noqa: F811
    return make_entry(db)


def trial_of(db, entry):  # noqa: F811
    return db.query(Trial).filter_by(event_id=entry.event_id, governing_body="AKC").one()


def two_trials(db, entry):  # noqa: F811
    trial = trial_of(db, entry)
    trial.akc_event_number, trial.akc_event_number_2 = "111", "222"
    db.commit()
    return trial


def selections(db, entry):  # noqa: F811
    db.expire_all()
    return db.query(TrialEntrySelection).filter_by(trial_entry_id=entry.id).order_by(
        TrialEntrySelection.id).all()


def add_event(db, trial, **kw):  # noqa: F811
    te = add(db, TrialEvent(**{**dict(trial_id=trial.id, name="Duck A", fee_cents=6000, sort_order=2), **kw}))
    cls = add(db, TrialEventClass(trial_event_id=te.id, name="Started", sort_order=1))
    return te, cls


def post(client, url, **data):  # noqa: F811
    return client.post(url, data={"csrf_token": csrf(client), **data})


def add_class(client, entry, te, cls=None, pref="event_1", day=""):  # noqa: F811
    return post(client, f"/admin/trial-entries/{entry.id}/selections/add", trial_event_id=te.id,
                trial_event_class_id=cls.id if cls else "", akc_trial_pref=pref, day_preference=day)


def update(client, sel, pref="event_1", day="", cls=""):  # noqa: F811
    return post(client, f"/admin/trial-entry-selections/{sel.id}/update",
                akc_trial_pref=pref, day_preference=day, trial_event_class_id=cls)


def another(db, entry, **kw):  # noqa: F811
    return add(db, TrialEntry(**{**dict(
        event_id=entry.event_id, governing_body="AKC", handler_name="H",
        handler_email="h@example.com", dog_name="Second", total_fee_cents=6000), **ADDR, **kw}))


def test_update_and_remove_return_to_entry(client, db, admin, entry):  # noqa: F811
    back = f"/admin/trial-entries/{entry.id}#selections"
    sel = selections(db, entry)[0]
    r = update(client, sel, day="friday")
    assert r.headers["location"] == back and selections(db, entry)[0].day_preference == "friday"
    r = post(client, f"/admin/trial-entry-selections/{sel.id}/delete")
    assert r.headers["location"] == back and selections(db, entry) == []


def test_duplicate_side_is_refused(client, db, admin, entry):  # noqa: F811
    trial = two_trials(db, entry)
    te = db.query(TrialEvent).filter_by(trial_id=trial.id).one()
    add_class(client, entry, te, pref="event_2")
    first, second = selections(db, entry)
    assert update(client, first, pref="event_2").status_code == 303
    assert [s.akc_trial_pref for s in selections(db, entry)] == ["event_1", "event_2"]
    assert "already entered" in client.get(f"/admin/trial-entries/{entry.id}").text
    add_class(client, entry, te, pref="event_2")
    assert len(selections(db, entry)) == 2


def test_invalid_side_or_day_is_refused(client, db, admin, entry):  # noqa: F811
    sel = selections(db, entry)[0]
    update(client, sel, pref="event_2")   # the weekend has one trial
    update(client, sel, pref="both")
    update(client, sel, day="sunday")     # AKC is Friday and Saturday
    sel = selections(db, entry)[0]
    assert (sel.akc_trial_pref, sel.day_preference) == ("event_1", None)


def test_add_class_rejects_wrong_event_class_or_day(client, db, admin, entry):  # noqa: F811
    te, cls = add_event(db, trial_of(db, entry), available_days="friday")
    foreign_te, foreign_cls = add_event(db, trial_of(db, make_entry(db)))
    for args in [(foreign_te, foreign_cls), (te, foreign_cls), (te, None)]:
        add_class(client, entry, *args, day="friday")
    add_class(client, entry, te, cls, day="saturday")
    post(client, f"/admin/trial-entries/{entry.id}/selections/add", trial_event_id="abc")
    assert len(selections(db, entry)) == 1
    add_class(client, entry, te, cls, day="friday")
    new = selections(db, entry)[1]
    assert (new.trial_event_id, new.trial_event_class_id, new.day_preference) == (te.id, cls.id, "friday")


def first_te(db, entry):  # noqa: F811
    return db.query(TrialEvent).filter_by(trial_id=trial_of(db, entry).id).order_by(TrialEvent.id).first()


def remove(client, sel):  # noqa: F811
    return post(client, f"/admin/trial-entry-selections/{sel.id}/delete")


def test_unpaid_fee_follows_classes_and_keeps_custom_price(client, db, admin, entry):  # noqa: F811
    entry.total_fee_cents = 5500
    db.commit()
    te, cls = add_event(db, trial_of(db, entry), fee_cents=4000)
    add_class(client, entry, te, cls)
    db.refresh(entry)
    assert entry.total_fee_cents == 9500
    assert "from $55.00 to $95.00" in client.get(f"/admin/trial-entries/{entry.id}").text
    remove(client, selections(db, entry)[1])
    db.refresh(entry)
    assert entry.total_fee_cents == 5500
    remove(client, selections(db, entry)[0])
    db.refresh(entry)
    assert entry.total_fee_cents == 0


def test_removal_never_makes_remaining_classes_free(client, db, admin, entry):  # noqa: F811
    te, cls = add_event(db, trial_of(db, entry), fee_cents=9000)
    add(db, TrialEntrySelection(trial_entry_id=entry.id, trial_event_id=te.id,
                                trial_event_class_id=cls.id, akc_trial_pref="event_1"))
    remove(client, selections(db, entry)[1])
    db.refresh(entry)
    assert entry.total_fee_cents == 6000


def test_waiting_check_amount_follows_fee(client, db, admin, entry):  # noqa: F811
    promise_check(client, entry, "entry")
    te, cls = add_event(db, trial_of(db, entry))
    add_class(client, entry, te, cls)
    check = check_of(db, entry)
    assert (check.entry_cents, check.total_cents, check.status) == (12000, 12000, "awaiting_check")
    assert "tell the entrant" in client.get(f"/admin/trial-entries/{entry.id}").text


def balance_pages(client, entry):  # noqa: F811
    return [client.get(u).text for u in (
        f"/admin/trial-entries/{entry.id}", f"/admin/events/{entry.event_id}/trial-entries",
        f"/events/{entry.event_id}/trial/confirmation?entry_id={entry.id}&t={entry.access_token}",
        "/auth/entries")]


@pytest.mark.parametrize("history", [True, False])
def test_paid_entry_stays_paid_and_shows_balance_then_refund(client, db, admin, sent, history):  # noqa: F811
    entry = make_entry(db, user_id=admin.id, is_paid=True)
    if history:
        add(db, Payment(kind="entry", record_id=entry.id, method="paypal", status="completed",
                        entry_cents=6000, processing_fee_cents=240, total_cents=6240, received_cents=6240))
    assert not any("Balance due" in p or "Refund due" in p for p in balance_pages(client, entry))
    te, cls = add_event(db, trial_of(db, entry))
    add_class(client, entry, te, cls)
    db.refresh(entry)
    assert entry.is_paid and entry.total_fee_cents == 12000
    assert all("Balance due: $60.00" in p for p in balance_pages(client, entry))
    group_email(client, entry)
    for part in ("html", "plain"):
        assert "Balance due: $60.00" in sent[0][part] and "Total due: $60.00" in sent[0][part]
        assert ("$62.40" if history else "Total paid: $60.00") in sent[0][part]
    for sel in selections(db, entry):
        remove(client, sel)
    db.refresh(entry)
    assert entry.is_paid and entry.total_fee_cents == 0
    admin_page, admin_list, confirmation, mine = balance_pages(client, entry)
    assert "Refund due in PayPal: $60.00" in admin_page and "Refund due in PayPal: $60.00" in admin_list
    assert "Refunds are made in PayPal, not on this site" in admin_page
    for page in (confirmation, mine):
        assert "Refund due: $60.00" in page and "PayPal" not in page


def test_export_shows_balance_and_money_actually_collected(client, db, admin):  # noqa: F811
    entry = make_entry(db, is_paid=True)
    te, cls = add_event(db, trial_of(db, entry))
    add_class(client, entry, te, cls)
    r = client.get(f"/admin/events/{entry.event_id}/export-trial.xlsx")
    rows = list(load_workbook(io.BytesIO(r.content))["Payment Summary"].values)
    assert rows[1][3] == 120 and rows[1][6] == 60
    assert next(row for row in rows if row[2] == "AKC total collected")[3] == 60


@pytest.mark.parametrize("status", ["capturing", "pending"])
def test_fee_locked_while_paypal_is_processing(client, db, admin, entry, status):  # noqa: F811
    two_trials(db, entry)
    add(db, Payment(kind="entry", record_id=entry.id, method="paypal", status=status,
                    entry_cents=6000, total_cents=6240, public_id="p", paypal_order_id="o"))
    add_class(client, entry, first_te(db, entry), pref="event_2")
    remove(client, selections(db, entry)[0])
    db.refresh(entry)
    assert entry.total_fee_cents == 6000 and len(selections(db, entry)) == 1
    assert "being processed" in client.get(f"/admin/trial-entries/{entry.id}").text


def test_both_row_is_two_runs(client, db, admin, entry):  # noqa: F811
    two_trials(db, entry)
    sel = selections(db, entry)[0]
    sel.akc_trial_pref, entry.total_fee_cents = "both", 12000
    db.commit()
    update(client, sel, pref="event_1")
    db.refresh(entry)
    assert entry.total_fee_cents == 6000


def test_old_both_row_survives_a_save(client, db, admin, entry):  # noqa: F811
    trial = two_trials(db, entry)
    sel = selections(db, entry)[0]
    sel.akc_trial_pref = "both"
    db.commit()
    page = client.get(f"/admin/trial-entries/{entry.id}").text
    assert '<option value="both" selected>Event 1 and Event 2' in page
    update(client, sel, pref="both", day="saturday")
    assert selections(db, entry)[0].akc_trial_pref == "both"
    add_class(client, entry, db.query(TrialEvent).filter_by(trial_id=trial.id).one(), pref="event_2")
    assert len(selections(db, entry)) == 1


FIELDS = dict(
    handler_name="New H", handler_email="new@x.com", handler_phone="5", address_line1="2 Rd",
    address_line2="Apt", city="Y", state_province="PA", postal_code="2", country="CA",
    dog_name="Rex", dog_call_name="R", dog_breed="GSD", dog_registration_number="N1", dog_sex="F",
    dog_dob="2020-02-03", dog_sire="S", dog_dam="Da", dog_breeder="B", dog_place_of_birth="P",
    payment_reference="Cash",
)
AKC = dict(akc_number_type="Foreign", akc_foreign_country="Canada", akc_owner_names="O",
           akc_owner_address="OA", akc_handler_name="RH", akc_handler_address="RA")
AHBA = dict(ahba_agent_name="Ag", ahba_agent_phone="6", ahba_agent_email="ag@x.com", ahba_number_type="ISDS")


@pytest.mark.parametrize("body,extra", [("AKC", AKC), ("AHBA", AHBA)])
def test_every_field_saves_and_is_shown(client, db, admin, body, extra):  # noqa: F811
    entry = make_entry(db, governing_body=body)
    fields = {**FIELDS, **extra}
    url = f"/admin/trial-entries/{entry.id}"
    assert post(client, f"{url}/edit", akc_separate_entries_shown="1", akc_separate_entries="1",
                **fields).status_code == 303
    db.expire_all()
    for name, value in fields.items():
        assert str(getattr(entry, name)) == value
    assert entry.dog_dob == date(2020, 2, 3) and entry.akc_separate_entries == (body == "AKC")
    page = client.get(url).text
    for name in fields:
        assert f'name="{name}"' in page
    post(client, f"{url}/edit", akc_separate_entries_shown="1", handler_phone="", address_line1="")
    db.expire_all()
    assert (entry.akc_separate_entries, entry.handler_phone, entry.address_line1) == (False, None, "")


def test_custom_ahba_registry_round_trips(client, db, admin):  # noqa: F811
    entry = make_entry(db, governing_body="AHBA")
    post(client, f"/admin/trial-entries/{entry.id}/edit", ahba_number_type="Other", ahba_number_type_other="CKC")
    db.expire_all()
    assert entry.ahba_number_type == "CKC"
    page = client.get(f"/admin/trial-entries/{entry.id}").text
    assert 'value="Other" selected' in page and 'value="CKC"' in page


@pytest.mark.parametrize("bad", [
    {"dog_dob": "nope"}, {"dog_sex": "X"}, {"country": "---"}, {"handler_name": " "},
    {"dog_name": ""}, {"akc_number_type": "zzz"},
])
def test_bad_input_changes_nothing_and_keeps_typing(client, db, admin, entry, bad):  # noqa: F811
    r = post(client, f"/admin/trial-entries/{entry.id}/edit", **{"city": "Typed City", **bad})
    db.expire_all()
    assert r.status_code == 422 and "Typed City" in r.text and entry.city == "X"
    assert "alid" in r.text or "Enter the" in r.text
    assert "Enter the" not in client.get(f"/admin/trial-entries/{entry.id}").text


def test_reminder_only_after_confirmation_sent(client, db, admin, entry):  # noqa: F811
    url = f"/admin/trial-entries/{entry.id}"
    post(client, f"{url}/edit", city="Z")
    assert "already sent" not in client.get(url).text
    entry.verification_sent = True
    db.commit()
    post(client, f"{url}/edit", city="Z")
    assert "already sent" in client.get(url).text


def group_email(client, entry, body="AKC", email="h@example.com"):  # noqa: F811
    return post(client, f"/admin/events/{entry.event_id}/verification-emails", body=body, email=email)


def test_one_akc_email_covers_both_dogs(client, db, admin, sent):  # noqa: F811
    entry = make_entry(db, is_paid=True, dog_name="First")
    two_trials(db, entry)
    selections(db, entry)[0].day_preference = "friday"
    db.commit()
    second = another(db, entry, is_paid=True, handler_email=" H@Example.com ")
    ahba = another(db, entry, is_paid=True, governing_body="AHBA", dog_name="AhbaDog")
    other = another(db, entry, is_paid=True, handler_email="other@x.com", dog_name="Stranger")
    elsewhere = make_entry(db, is_paid=True, dog_name="Elsewhere")
    group_email(client, entry)
    db.expire_all()
    assert len(sent) == 1 and sent[0]["subject"].startswith("AKC Trial Entries Confirmed")
    for part in ("html", "plain"):
        text = sent[0][part]
        assert "First" in text and "Second" in text and "Event 1 (#111), Friday" in text
        assert "$120.00" in text
        assert not any(n in text for n in ("AhbaDog", "Stranger", "Elsewhere"))
    assert [e.verification_sent for e in (entry, second, ahba, other, elsewhere)] == [True, True, False, False, False]
    page = client.get(f"/admin/events/{entry.event_id}/trial-entries").text
    assert "sent for 2 of 2 entries" in page and "sent for 0 of 1 entry" in page


def test_mixed_payment_marks_only_paid(client, db, admin, entry, sent):  # noqa: F811
    paid = another(db, entry, is_paid=True)
    waiting = another(db, entry, dog_name="Waiting")
    te = db.query(TrialEvent).filter_by(trial_id=trial_of(db, entry).id).one()
    add(db, TrialEntrySelection(trial_entry_id=waiting.id, trial_event_id=te.id, akc_trial_pref="event_1"))
    promise_check(client, waiting, "entry")
    sent.clear()
    group_email(client, entry)
    db.expire_all()
    assert "payment outstanding" in sent[0]["subject"]
    for part in ("html", "plain"):
        text = sent[0][part]
        assert "Total due: $120.00" in text and "Total paid: $60.00" in text
        assert "Pat Payee" in text and "not confirmed a payment" in text
    assert [e.verification_sent for e in (entry, paid, waiting)] == [False, True, False]


def test_failed_group_delivery_marks_none(client, db, admin, monkeypatch):  # noqa: F811
    entry = make_entry(db, is_paid=True)

    async def fail(*args):
        raise RuntimeError("down")
    monkeypatch.setattr(email_helpers, "_deliver", fail)
    group_email(client, entry)
    db.expire_all()
    assert entry.verification_sent is False


def test_blank_email_matches_nobody(client, db, admin, sent):  # noqa: F811
    entry = make_entry(db, is_paid=True, handler_email="")
    another(db, entry, handler_email="")
    group_email(client, entry, email="")
    assert sent == []
    assert "verification-emails" not in client.get(f"/admin/events/{entry.event_id}/trial-entries").text


def test_send_all_sends_one_per_person_and_body(client, db, admin, sent):  # noqa: F811
    entry = make_entry(db, is_paid=True)
    another(db, entry, is_paid=True)
    another(db, entry, dog_name="UnpaidDog")
    another(db, entry, is_paid=True, governing_body="AHBA")
    another(db, entry, is_paid=True, verification_sent=True, handler_email="done@x.com")
    post(client, f"/admin/events/{entry.event_id}/send-all-verifications")
    assert sorted(m["subject"][:4].strip() for m in sent) == ["AHBA", "AKC"]
    assert {m["to"] for m in sent} == {"h@example.com"}
    assert not any("UnpaidDog" in m["html"] for m in sent)


def test_admin_and_csrf_required(app, client, db, entry, sent):  # noqa: F811
    sel = selections(db, entry)[0]
    targets = [
        f"/admin/trial-entries/{entry.id}/selections/add", f"/admin/trial-entries/{entry.id}/edit",
        f"/admin/trial-entry-selections/{sel.id}/update", f"/admin/trial-entry-selections/{sel.id}/delete",
        f"/admin/events/{entry.event_id}/verification-emails",
    ]
    data = dict(body="AKC", email="h@example.com", city="Hacked")
    login(app, make_user(db, "u@x.com"))
    assert all(post(client, t, **data).status_code in (302, 303, 403) for t in targets)
    login(app, make_user(db, "a@x.com", role="admin"))
    assert all(client.post(t, data={**data, "csrf_token": "bad"}).status_code in (400, 403) for t in targets)
    db.expire_all()
    assert sent == [] and entry.city == "X" and len(selections(db, entry)) == 1
