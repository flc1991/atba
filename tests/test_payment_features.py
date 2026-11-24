"""Checks, payment history, admin handling, emails and form errors (plan section B)."""
import asyncio
import io
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from fastapi_mail import FastMail
from openpyxl import load_workbook

from app.config import settings
from app.models.payment import Payment
from app.models.registration import Registration, RegistrationDog
from app.models.trial import TrialEntry, TrialEntrySelection
from app.utils import email_helpers
from app.utils.payment_records import parse_dollars
from tests.test_payment_security import (  # noqa: F401
    KINDS, add, app, client, db, login, make_entry, make_event, make_reg, make_user, paypal,
    record, urls,
)


@pytest.fixture(autouse=True)
def sent(monkeypatch, db):  # noqa: F811
    """Capture emails; use the test database; switch checks on."""
    box = []

    async def deliver(subject, recipient, html, plain):
        box.append({"subject": subject, "to": recipient, "html": html, "plain": plain})

    class Session:
        def __call__(self):
            return self

        def __getattr__(self, name):
            return (lambda: None) if name == "close" else getattr(db, name)

    monkeypatch.setattr(email_helpers, "_deliver", deliver)
    monkeypatch.setattr(email_helpers, "SessionLocal", Session())
    monkeypatch.setattr(settings, "MAIL_FROM", "club@example.com")
    monkeypatch.setattr(settings, "CHECK_PAYEE_NAME", "Pat Payee")
    monkeypatch.setattr(settings, "CHECK_MAILING_ADDRESS", "9 Post Rd")
    monkeypatch.setattr(settings, "BASE_URL", "https://club.example")
    return box


def csrf(client):  # noqa: F811
    """A token for this session, from any page that sets one."""
    db_ = client.app.dependency_overrides
    from app.dependencies import get_db
    ev = make_event(db_[get_db](), kind="smart_dog_day")
    page = client.get(f"/events/{ev.id}/register/smart-dog-day")
    return re.search(r'name="csrf_token" value="([^"]+)"', page.text)[1]


def promise_check(client, rec, kind):  # noqa: F811
    return client.post(f"/payments/check/{kind}/{rec.id}",
                       data={"csrf_token": csrf(client), "t": rec.access_token})


def payments(db):  # noqa: F811
    """All payments except the placeholder order that urls() adds."""
    return db.query(Payment).filter(
        Payment.public_id.is_(None) | Payment.public_id.notlike("UNKNOWN%"))


def check_of(db, rec):  # noqa: F811
    kind = "registration" if isinstance(rec, Registration) else "entry"
    return payments(db).filter_by(kind=kind, record_id=rec.id).one()


def arrive(client, payment, **kw):  # noqa: F811
    data = {"csrf_token": csrf(client), "amount": f"{payment.total_cents / 100:.2f}",
            "reference": "123", "received_on": "2026-11-20", **kw}
    return client.post(f"/admin/checks/{payment.id}/arrived", data=data)


# ---- choosing a check -------------------------------------------------------

@KINDS
def test_check_entry_is_incomplete_with_instructions(client, db, sent, kind):  # noqa: F811
    rec = record(db, kind)
    page = client.get(urls(rec, kind, rec.access_token)[0]).text
    assert "I'm sending a check" in page and "Pat Payee" in page and "Must arrive by" in page
    r = promise_check(client, rec, kind)
    db.refresh(rec)
    assert rec.is_paid is False and check_of(db, rec).status == "awaiting_check"
    assert check_of(db, rec).processing_fee_cents == 0
    text = client.get(r.headers["location"]).text
    assert "awaiting your check" in text and "9 Post Rd" in text and "incomplete" in text
    assert len(sent) == 1 and "awaiting check" in sent[0]["subject"]
    assert "Pat Payee" in sent[0]["plain"] and rec.access_token in sent[0]["plain"]
    assert "payment confirmed" not in sent[0]["html"] and rec.verification_sent is False
    promise_check(client, rec, kind)  # idempotent
    assert payments(db).count() == 1 and len(sent) == 1


@KINDS
def test_check_needs_access_and_csrf(client, db, kind):  # noqa: F811
    rec = record(db, kind)
    url = f"/payments/check/{kind}/{rec.id}"
    assert client.post(url, data={"csrf_token": csrf(client), "t": "bad"}).status_code == 404
    assert client.post(url, data={"t": rec.access_token}).status_code == 403
    assert payments(db).count() == 0


def test_check_hidden_until_payee_configured(client, db, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "CHECK_MAILING_ADDRESS", "")
    rec = record(db, "entry")
    assert "sending a check" not in client.get(urls(rec, "entry", rec.access_token)[0]).text
    promise_check(client, rec, "entry")
    assert payments(db).count() == 0


def test_check_refused_after_entries_close(client, db):  # noqa: F811
    rec = make_entry(db, close_in_days=-1)
    promise_check(client, rec, "entry")
    assert payments(db).count() == 0


def test_near_deadline_warns(client, db):  # noqa: F811
    rec = make_entry(db, close_in_days=2)
    assert "deliver it by hand" in client.get(urls(rec, "entry", rec.access_token)[0]).text
    rec = make_entry(db, close_in_days=20)
    assert "deliver it by hand" not in client.get(urls(rec, "entry", rec.access_token)[0]).text


def test_overdue_check_keeps_price_and_blocks_paypal(client, db):  # noqa: F811
    ev = make_event(db, pre_entry_close_dt=datetime.now(UTC) + timedelta(days=1))
    rec = make_reg(db, ev, pricing_tier="pre_member", fee_cents=1000)
    promise_check(client, rec, "registration")
    ev.pre_entry_close_dt = datetime.now(UTC) - timedelta(days=1)
    db.commit()
    r = client.get(urls(rec, "registration", rec.access_token)[0])
    assert r.status_code == 303
    text = client.get(r.headers["location"]).text
    db.refresh(rec)
    assert rec.fee_cents == 1000 and "deadline has passed" in text and "PayPal instead" not in text


def test_my_entries_shows_awaiting_check(app, client, db):  # noqa: F811
    user = make_user(db, "o@x.com")
    for kind in ("entry", "registration"):
        promise_check(client, record(db, kind, user_id=user.id), kind)
    login(app, user)
    text = client.get("/auth/entries").text
    assert text.count("Awaiting check") == 2 and "Pay Now" not in text


# ---- check arrives ----------------------------------------------------------

@KINDS
@pytest.mark.parametrize("role", ["admin", "secretary"])
def test_check_arrived(app, client, db, sent, kind, role):  # noqa: F811
    rec = record(db, kind)
    promise_check(client, rec, kind)
    user = make_user(db, "s@x.com", role="admin" if role == "admin" else "user")
    user.is_trial_secretary = role == "secretary"
    user.is_active = True
    db.commit()
    login(app, user)
    assert "Check arrived" in client.get("/admin/checks").text
    payment = check_of(db, rec)
    r = arrive(client, payment, back="event")
    assert r.status_code == 303
    assert (r.headers["location"] == "/admin/checks") is (role == "secretary")
    db.refresh(rec)
    db.refresh(payment)
    assert rec.is_paid and payment.status == "completed" and payment.reference == "123"
    assert payment.received_cents == payment.total_cents and "s@x.com" in payment.recorded_by
    assert str(payment.received_on) == "2026-11-20"
    assert "Confirmed" in sent[-1]["subject"] and rec.verification_sent and len(sent) == 2
    arrive(client, payment)
    assert len(sent) == 2


def test_ordinary_or_inactive_users_cannot_handle_checks(app, client, db):  # noqa: F811
    rec = record(db, "entry")
    promise_check(client, rec, "entry")
    plain, inactive = make_user(db, "p@x.com"), make_user(db, "i@x.com")
    inactive.is_trial_secretary, inactive.is_active = True, False
    db.commit()
    for user in (None, plain, inactive):
        login(app, user)
        assert client.get("/admin/checks").status_code == 403
        assert arrive(client, check_of(db, rec)).status_code == 403
    db.refresh(rec)
    assert rec.is_paid is False


def test_secretary_gets_no_other_admin_access(app, client, db):  # noqa: F811
    user = make_user(db, "s@x.com")
    user.is_trial_secretary = True
    db.commit()
    login(app, user)
    assert client.get("/admin/checks").status_code == 200
    ev = make_event(db, kind="trial")
    for url in ("/admin/", "/admin/users", f"/admin/events/{ev.id}/trial-entries"):
        assert client.get(url).status_code == 403


@pytest.mark.parametrize("bad", [dict(amount="abc"), dict(amount="-1"), dict(amount="NaN"),
                                 dict(reference=""), dict(received_on="nope")])
def test_bad_check_details_change_nothing(app, client, db, bad):  # noqa: F811
    rec = record(db, "entry")
    promise_check(client, rec, "entry")
    login(app, make_user(db, "a@x.com", role="admin"))
    arrive(client, check_of(db, rec), **bad)
    db.refresh(rec)
    assert rec.is_paid is False and check_of(db, rec).status == "awaiting_check"


# ---- admin lists, counts, export -------------------------------------------

def test_awaiting_check_excluded_from_call_numbers_and_export(app, client, db):  # noqa: F811
    waiting = make_entry(db, dog_name="WaitDog", handler_name="=Evil")
    te_id = db.query(TrialEntrySelection).filter_by(trial_entry_id=waiting.id).one().trial_event_id
    normal = add(db, TrialEntry(
        event_id=waiting.event_id, governing_body="AKC", handler_name="N", handler_email="n@x.com",
        dog_name="NormalDog", total_fee_cents=6000, address_line1="1", city="X", postal_code="1"))
    add(db, TrialEntrySelection(trial_entry_id=normal.id, trial_event_id=te_id, akc_trial_pref="event_1"))
    promise_check(client, waiting, "entry")
    login(app, make_user(db, "a@x.com", role="admin"))
    base = f"/admin/events/{waiting.event_id}"
    calls = client.get(f"{base}/call-numbers").text
    assert "NormalDog" in calls and "WaitDog" not in calls
    listing = client.get(f"{base}/trial-entries").text
    assert "(1 entry)" in listing and "Awaiting check" in listing and "WaitDog" in listing
    assert "1 entries" in client.get("/admin/").text
    wb = load_workbook(io.BytesIO(client.get(f"{base}/export-trial.xlsx").content))
    sheets = {ws.title: [[c.value for c in row] for row in ws.iter_rows()] for ws in wb.worksheets}
    for title, rows in sheets.items():
        assert any("WaitDog" in row for row in rows) is (title == "Awaiting Check")
    assert ["AKC", "JHD", "test", 1] in sheets["Event Counts"]
    assert all(c.data_type != "f" for ws in wb.worksheets for row in ws.iter_rows() for c in row)


def test_registrations_page_lists_checks_separately(app, client, db):  # noqa: F811
    ev = make_event(db)
    waiting, normal = make_reg(db, ev, name="Waity"), make_reg(db, ev, name="Normy")
    promise_check(client, waiting, "registration")
    login(app, make_user(db, "a@x.com", role="admin"))
    text = client.get(f"/admin/events/{ev.id}/registrations").text
    assert "Awaiting check" in text and "Waity" in text and "Normy" in text
    assert f"/admin/payments/registration/{normal.id}/paid" in text
    assert f"/admin/payments/registration/{waiting.id}/paid" not in text
    assert "1 registrations" in client.get("/admin/").text


# ---- marking paid and unpaid, history, deletion ----------------------------

@KINDS
def test_mark_paid_and_unpaid_keeps_history(app, client, db, sent, kind):  # noqa: F811
    rec = record(db, kind)
    login(app, make_user(db, "a@x.com", role="admin"))
    token = csrf(client)
    url = f"/admin/payments/{kind}/{rec.id}"
    client.post(f"{url}/paid", data={"csrf_token": token, "method": "paypal", "amount": "20", "reference": "TX1"})
    db.refresh(rec)
    assert rec.is_paid and rec.payment_reference == "TX1" and len(sent) == 1
    assert "$20.00" in sent[0]["plain"]
    client.post(f"{url}/paid", data={"csrf_token": token, "method": "cash", "amount": "1"})
    assert payments(db).count() == 1
    client.post(f"{url}/unpaid", data={"csrf_token": token})
    db.refresh(rec)
    payment = check_of(db, rec)
    assert rec.is_paid is False and payment.status == "void" and "a@x.com" in payment.note
    if kind == "entry":
        assert "Void" in client.get(f"/admin/trial-entries/{rec.id}").text
    else:
        assert "Void" in client.get(f"/admin/events/{rec.event_id}/registrations").text


def test_mark_paid_rejects_bad_method_and_non_admin(app, client, db):  # noqa: F811
    rec = record(db, "entry")
    url = f"/admin/payments/entry/{rec.id}/paid"
    data = {"csrf_token": csrf(client), "method": "free", "amount": "1"}
    login(app, make_user(db, "u@x.com"))
    assert client.post(url, data=data).status_code == 403
    login(app, make_user(db, "a@x.com", role="admin"))
    client.post(url, data=data)
    db.refresh(rec)
    assert rec.is_paid is False


def test_paypal_replaces_awaiting_check(app, client, db):  # noqa: F811
    rec = record(db, "entry")
    promise_check(client, rec, "entry")
    login(app, make_user(db, "a@x.com", role="admin"))
    client.post(f"/admin/payments/entry/{rec.id}/paid",
                data={"csrf_token": csrf(client), "method": "paypal", "amount": "60"})
    assert [p.status for p in db.query(Payment).order_by(Payment.id)] == ["void", "completed"]


@KINDS
def test_deleted_record_leaves_nothing_for_a_reused_id(app, client, db, kind):  # noqa: F811
    rec = record(db, kind)
    if kind == "registration":
        add(db, RegistrationDog(registration_id=rec.id, dog_name="D", event_1="Duck A"))
    promise_check(client, rec, kind)
    login(app, make_user(db, "a@x.com", role="admin"))
    page = "registrations" if kind == "registration" else "trial-entries"
    client.post(f"/admin/{page}/{rec.id}/delete", data={"csrf_token": csrf(client)})
    assert db.query(Registration).count() + db.query(TrialEntry).count() == 0
    assert db.query(TrialEntrySelection).count() + db.query(RegistrationDog).count() == 0
    payment = db.query(Payment).one()
    assert payment.record_id is None and payment.status == "void" and "Deleted" in payment.note
    assert "Check arrived" not in client.get("/admin/checks").text


def test_manual_registration_records_payment(app, client, db):  # noqa: F811
    ev = make_event(db)
    login(app, make_user(db, "a@x.com", role="admin"))
    data = {"csrf_token": csrf(client), "event_id": ev.id, "name": "M", "dog_name": "D",
            "fee_dollars": "35", "is_paid": "1", "payment_reference": "Cash"}
    client.post("/admin/registrations/new", data=data)
    reg = db.query(Registration).one()
    assert reg.is_paid and reg.payment_reference == "Cash" and check_of(db, reg).received_cents == 3500
    client.post("/admin/registrations/new", data={**data, "fee_dollars": "abc"})
    assert db.query(Registration).count() == 1


def test_manual_trial_entry_records_payment(app, client, db):  # noqa: F811
    entry = make_entry(db)
    sel = db.query(TrialEntrySelection).one()
    login(app, make_user(db, "a@x.com", role="admin"))
    client.post(f"/admin/events/{entry.event_id}/trial-entries/new-akc", data={
        "csrf_token": csrf(client), "handler_name": "M", "dog_name": "D", "is_paid": "1",
        f"enter_te_e1_{sel.trial_event_id}": "1"})
    manual = db.query(TrialEntry).filter_by(is_manual_entry=True).one()
    assert manual.is_paid and check_of(db, manual).received_cents == 6000


def test_editing_an_entry_does_not_change_paid_state(app, client, db):  # noqa: F811
    entry = make_entry(db, is_paid=True)
    login(app, make_user(db, "a@x.com", role="admin"))
    client.post(f"/admin/trial-entries/{entry.id}/edit", data={
        "csrf_token": csrf(client), "handler_name": "New", "handler_email": "h@x.com", "dog_name": "D"})
    db.refresh(entry)
    assert entry.handler_name == "New" and entry.is_paid


def test_parse_dollars():
    assert parse_dollars("$1,234.50") == 123450 and parse_dollars("0") == 0
    for bad in ("", "x", "-1", "inf", "NaN"):
        with pytest.raises(ValueError):
            parse_dollars(bad)


# ---- emails -----------------------------------------------------------------

def test_test_class_entry_gets_an_email(db, sent):  # noqa: F811
    entry = make_entry(db, is_paid=True)
    assert asyncio.run(email_helpers.send_trial_entry_verification(entry.id))
    assert "JHD — Test" in sent[0]["plain"] and "payment confirmed" in sent[0]["plain"]
    db.refresh(entry)
    assert entry.verification_sent


@KINDS
def test_unpaid_email_does_not_claim_payment(db, sent, kind):  # noqa: F811
    rec = record(db, kind)
    send = (email_helpers.send_registration_verification if kind == "registration"
            else email_helpers.send_trial_entry_verification)
    assert asyncio.run(send(rec.id))
    for part in ("html", "plain", "subject"):
        assert "confirmed" not in sent[0][part].lower().replace("not confirmed", "")
        assert "Paid:" not in sent[0][part]
    db.refresh(rec)
    assert rec.verification_sent is False


def test_failed_delivery_is_not_marked_sent(db, monkeypatch):  # noqa: F811
    async def boom(*a):
        raise OSError("smtp down")
    monkeypatch.setattr(email_helpers, "_deliver", boom)
    entry = make_entry(db, is_paid=True)
    assert asyncio.run(email_helpers.send_trial_entry_verification(entry.id)) is False
    db.refresh(entry)
    assert entry.verification_sent is False


def test_plain_text_is_not_html_escaped(db, sent):  # noqa: F811
    entry = make_entry(db, is_paid=True, dog_name="Rock & Roll's <b>")
    asyncio.run(email_helpers.send_trial_entry_verification(entry.id))
    assert "Rock & Roll's <b>" in sent[0]["plain"] and "<b>" not in sent[0]["html"]


def test_send_all_only_emails_paid(app, client, db, sent):  # noqa: F811
    ev = make_event(db)
    make_reg(db, ev, is_paid=True, email="paid@x.com")
    make_reg(db, ev, email="unpaid@x.com")
    make_reg(db, ev, is_paid=True, verification_sent=True, email="done@x.com")
    login(app, make_user(db, "a@x.com", role="admin"))
    client.post(f"/admin/events/{ev.id}/send-all-verifications", data={"csrf_token": csrf(client)})
    assert [m["to"] for m in sent] == ["paid@x.com"]


def test_blank_mail_from_is_reported_not_fatal(app, client, db, sent, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "MAIL_FROM", "")
    rec = record(db, "entry")
    login(app, make_user(db, "a@x.com", role="admin"))
    client.post(f"/admin/trial-entries/{rec.id}/send-verification", data={"csrf_token": csrf(client)})
    assert sent == [] and "not configured" in client.get(f"/admin/trial-entries/{rec.id}").text


def test_email_module_imports_without_mail_settings():
    import os
    env = {**os.environ, "MAIL_FROM": "", "MAIL_USERNAME": "", "MAIL_PASSWORD": ""}
    subprocess.run([sys.executable, "-c", "import app.utils.email_helpers"], env=env, check=True)


def test_real_message_has_html_and_plain_parts(monkeypatch):
    captured = []

    async def capture(self, message, template_name=None):
        captured.append(message)
    monkeypatch.undo()
    monkeypatch.setattr(settings, "MAIL_FROM", "club@example.com")
    monkeypatch.setattr(FastMail, "send_message", capture)
    asyncio.run(email_helpers._deliver("S", "to@example.com", "<p>h</p>", "plain"))
    assert captured[0].body == "<p>h</p>" and captured[0].alternative_body == "plain"


def test_free_registration_records_payment_and_emails(client, db, sent):  # noqa: F811
    rec = make_reg(db, make_event(db, fee_late_cents=0), fee_cents=0)
    client.get(urls(rec, "registration", rec.access_token)[0])
    assert check_of(db, rec).method == "free" and len(sent) == 1


# ---- form errors ------------------------------------------------------------

def test_trial_form_errors_show_immediately_and_do_not_leak(client, db):  # noqa: F811
    entry = make_entry(db)
    r = client.post(f"/events/{entry.event_id}/trial/enter/akc", data={"csrf_token": csrf(client)})
    assert r.status_code == 422 and "Name is required." in r.text
    assert "Name is required." not in client.get("/about").text
