"""PayPal Orders v2: creating, capturing, webhooks and processing fees (plan section D)."""
import json

import httpx
import pytest

from app.config import settings
from app.models.payment import Payment, PaypalEvent
from app.models.registration import RegistrationDog
from app.models.trial import TrialEntry, TrialEntrySelection, TrialEvent
from app.utils import processing_fee
from app.utils.processing_fee import processing_fee_cents, record_processing_fee_cents, run_prices
from tests.test_payment_features import check_of, csrf, promise_check, sent  # noqa: F401
from tests.test_payment_security import (  # noqa: F401
    KINDS, add, app, client, db, login, make_entry, make_event, make_reg, make_user, paypal,
    record, urls,
)

HEADERS = {f"paypal-{h}": "x" for h in
           ("auth-algo", "cert-url", "transmission-id", "transmission-sig", "transmission-time")}


@pytest.fixture(autouse=True)
def webhook_id(monkeypatch):
    monkeypatch.setattr(settings, "PAYPAL_WEBHOOK_ID", "WH1")


def total_for(db, rec):  # noqa: F811
    cents = rec.total_fee_cents if isinstance(rec, TrialEntry) else rec.fee_cents
    return cents + record_processing_fee_cents(db, rec)


def start(client, db, rec, kind, **kw):  # noqa: F811
    data = {"csrf_token": csrf(client), "t": rec.access_token, "total": total_for(db, rec), **kw}
    return client.post(f"/payments/paypal/{kind}/{rec.id}", data=data)


def begin(client, db, rec, kind):  # noqa: F811
    assert start(client, db, rec, kind).headers["location"].startswith("https://www.sandbox.paypal.com/")
    return db.query(Payment).filter_by(method="paypal").order_by(Payment.id.desc()).first()


def come_back(client, rec, payment):  # noqa: F811
    r = client.get(f"/payments/return?t={rec.access_token}&token={payment.paypal_order_id}&PayerID=P")
    return client.get(r.headers["location"]).text if r.status_code == 303 else r


def hook(client, kind, resource, event_id="EV1"):  # noqa: F811
    body = {"id": event_id, "event_type": kind, "resource": resource}
    return client.post("/payments/webhook", headers=HEADERS, content=json.dumps(body))


# ---- processing fee ---------------------------------------------------------

@pytest.mark.parametrize("run,fee", [(0, 0), (1000, 40), (2500, 100), (3000, 120), (3500, 140),
                                     (6000, 240), (12, 0), (13, 1), (1012, 40), (1013, 41)])
def test_fee_per_run(run, fee):
    assert processing_fee_cents(run) == fee


def test_fee_tier_overrides(monkeypatch):
    monkeypatch.setattr(processing_fee, "FEE_TIERS", [(5000, 6000, 250)])
    assert [processing_fee_cents(c) for c in (4999, 5000, 6000, 6001)] == [200, 250, 250, 240]


def test_fee_is_per_run_not_on_total(db):  # noqa: F811
    reg = make_reg(db, make_event(db), fee_cents=3 * 1013)
    add(db, RegistrationDog(registration_id=reg.id, dog_name="D", event_1="a", event_2="b", event_3="c"))
    assert record_processing_fee_cents(db, reg) == 3 * 41  # on the total it would be 122


def test_fee_mixed_trial_prices_and_edited_total(db):  # noqa: F811
    entry = make_entry(db, total_fee_cents=9500)
    first = db.query(TrialEvent).one()
    te = add(db, TrialEvent(trial_id=first.trial_id, name="B", fee_cents=3500, is_test_class=True, sort_order=2))
    add(db, TrialEntrySelection(trial_entry_id=entry.id, trial_event_id=te.id, akc_trial_pref="event_1"))
    assert sorted(run_prices(db, entry)) == [3500, 6000]
    assert record_processing_fee_cents(db, entry) == 240 + 140
    entry.total_fee_cents = 9001  # no longer matches the selections
    assert sum(run_prices(db, entry)) == 9001 and len(run_prices(db, entry)) == 2


# ---- creating the order -----------------------------------------------------

@KINDS
def test_order_has_server_side_amount_and_reference(client, db, paypal, kind):  # noqa: F811
    rec = record(db, kind)
    page = client.get(urls(rec, kind, rec.access_token)[0]).text
    assert "Processing fee" in page and f"${total_for(db, rec) / 100:.2f}" in page
    payment = begin(client, db, rec, kind)
    _, _, headers, body = next(c for c in paypal.calls if c[1] == "/v2/checkout/orders")
    body = json.loads(body)
    unit = body["purchase_units"][0]
    assert body["intent"] == "CAPTURE" and unit["custom_id"] == payment.public_id
    assert unit["amount"] == {"currency_code": "USD", "value": f"{total_for(db, rec) / 100:.2f}"}
    ctx = body["payment_source"]["paypal"]["experience_context"]
    assert ctx["return_url"] == f"https://club.example/payments/return?t={rec.access_token}"
    assert ctx["cancel_url"].startswith("https://club.example/payments/cancel?")
    assert ctx["user_action"] == "PAY_NOW" and headers["paypal-request-id"]
    assert payment.status == "created" and payment.processing_fee_cents > 0 and not rec.is_paid


def test_start_needs_access_csrf_and_the_shown_total(client, db, paypal):  # noqa: F811
    rec = record(db, "entry")
    assert start(client, db, rec, "entry", t="bad").status_code == 404
    assert start(client, db, rec, "entry", csrf_token="bad").status_code == 403
    assert "checkout" in start(client, db, rec, "entry", total=1).headers["location"]
    assert paypal.orders == {} and db.query(Payment).count() == 0


def test_closed_trial_cannot_start(client, db, paypal):  # noqa: F811
    rec = make_entry(db, close_in_days=-1)
    start(client, db, rec, "entry")
    assert paypal.orders == {}


@pytest.mark.parametrize("failure", [500, 401, httpx.ConnectTimeout("x")])
def test_paypal_down_when_starting(client, db, paypal, failure):  # noqa: F811
    paypal.fail["/v2/checkout/orders"] = failure
    rec = record(db, "entry")
    r = start(client, db, rec, "entry")
    assert "have not been charged" in client.get(r.headers["location"]).text
    assert db.query(Payment).one().status == "void"


def test_hidden_without_real_credentials(client, db, monkeypatch, paypal):  # noqa: F811
    monkeypatch.setattr(settings, "PAYPAL_CLIENT_ID", "your-paypal-client-id")
    rec = record(db, "entry")
    assert "Continue to PayPal" not in client.get(urls(rec, "entry", rec.access_token)[0]).text
    start(client, db, rec, "entry")
    assert paypal.orders == {}


def test_non_paypal_approval_link_is_refused(client, db, paypal, monkeypatch):  # noqa: F811
    real = paypal.handle

    def evil(request):
        resp = real(request)
        if request.url.path == "/v2/checkout/orders":
            return httpx.Response(200, json={"id": "X", "links": [
                {"rel": "payer-action", "href": "https://evilpaypal.com/"}]})
        return resp
    monkeypatch.setattr(paypal, "handle", evil)
    rec = record(db, "entry")
    assert "/payments/checkout/" in start(client, db, rec, "entry").headers["location"]


# ---- returning --------------------------------------------------------------

@KINDS
def test_pay_and_return(client, db, paypal, sent, kind):  # noqa: F811
    rec = record(db, kind)
    payment = begin(client, db, rec, kind)
    paypal.approve(payment.paypal_order_id)
    text = come_back(client, rec, payment)
    db.refresh(rec)
    db.refresh(payment)
    assert rec.is_paid and payment.status == "completed" and payment.received_cents == payment.total_cents
    assert rec.payment_reference == "PayPal: CAP1"
    assert "Payment received" in text and f"${payment.total_cents / 100:.2f}" in text
    assert len(sent) == 1 and f"${payment.total_cents / 100:.2f}" in sent[0]["plain"]
    for _ in range(2):
        come_back(client, rec, payment)
    hook(client, "PAYMENT.CAPTURE.COMPLETED", paypal.captures["CAP1"])
    hook(client, "CHECKOUT.ORDER.APPROVED", {"id": payment.paypal_order_id}, "EV2")
    assert paypal.capture_count == 1 and len(sent) == 1 and payment.needs_review is False


def test_forged_returns_do_nothing(client, db, paypal, sent):  # noqa: F811
    rec, other = record(db, "entry"), record(db, "entry")
    payment = begin(client, db, rec, "entry")
    assert come_back(client, rec, Payment(paypal_order_id="NOPE")).status_code == 404
    assert come_back(client, rec, Payment(paypal_order_id="")).status_code == 404
    paypal.approve(payment.paypal_order_id)
    assert come_back(client, other, payment).status_code == 404  # someone else's token
    for r in (rec, other):
        db.refresh(r)
        assert not r.is_paid
    assert paypal.capture_count == 0 and sent == []


def test_return_without_approval(client, db, paypal):  # noqa: F811
    rec = record(db, "entry")
    text = come_back(client, rec, begin(client, db, rec, "entry"))
    db.refresh(rec)
    assert not rec.is_paid and paypal.capture_count == 0
    assert "not been charged" in text and "Pay Now" in text


def test_cancel_then_pay_again(client, db, paypal):  # noqa: F811
    rec = record(db, "entry")
    first = begin(client, db, rec, "entry")
    r = client.get(f"/payments/cancel?t={rec.access_token}&token={first.paypal_order_id}")
    assert "cancelled" in client.get(r.headers["location"]).text
    second = begin(client, db, rec, "entry")
    paypal.approve(second.paypal_order_id)
    come_back(client, rec, second)
    db.refresh(rec)
    assert rec.is_paid


def tamper(paypal, payment, what):
    unit = paypal.orders[payment.paypal_order_id]["purchase_units"][0]
    if what == "amount":
        unit["amount"] = {"currency_code": "USD", "value": "0.01"}
    elif what == "currency":
        unit["amount"] = {**unit["amount"], "currency_code": "JPY"}
    else:
        unit["custom_id"] = "other"


@pytest.mark.parametrize("what", ["amount", "currency", "reference"])
def test_mismatched_order_is_never_captured(client, db, paypal, what):  # noqa: F811
    rec = record(db, "entry")
    payment = begin(client, db, rec, "entry")
    tamper(paypal, payment, what)
    paypal.approve(payment.paypal_order_id)
    come_back(client, rec, payment)
    db.refresh(rec)
    assert not rec.is_paid and paypal.capture_count == 0


@pytest.mark.parametrize("what", ["amount", "currency", "reference"])
def test_mismatched_capture_is_flagged_not_paid(app, client, db, paypal, what):  # noqa: F811
    rec = record(db, "entry")
    payment = begin(client, db, rec, "entry")
    tamper(paypal, payment, what)
    paypal.approve(payment.paypal_order_id)
    paypal.capture(paypal.orders[payment.paypal_order_id])  # charged outside our checks
    come_back(client, rec, payment)
    db.refresh(rec)
    db.refresh(payment)
    assert not rec.is_paid and payment.status == "review" and payment.needs_review
    start(client, db, rec, "entry")  # must not be able to pay twice
    assert len(paypal.orders) == 1
    login(app, make_user(db, "a@x.com", role="admin"))
    assert "Payments needing review" in client.get("/admin/").text
    client.post(f"/admin/payment-reviews/{payment.id}/done", data={"csrf_token": csrf(client)})
    assert "Payments needing review" not in client.get("/admin/").text


def test_two_orders_only_charge_once(client, db, paypal):  # noqa: F811
    rec = record(db, "entry")
    a, b = begin(client, db, rec, "entry"), begin(client, db, rec, "entry")
    for p in (a, b):
        paypal.approve(p.paypal_order_id)
        come_back(client, rec, p)
    db.refresh(b)
    assert paypal.capture_count == 1 and b.status == "void"


def test_other_order_in_flight_blocks_capture(client, db, paypal):  # noqa: F811
    rec = record(db, "entry")
    a, b = begin(client, db, rec, "entry"), begin(client, db, rec, "entry")
    a.status = "capturing"
    db.commit()
    paypal.approve(b.paypal_order_id)
    assert "confirming your payment" in come_back(client, rec, b)
    assert paypal.capture_count == 0


def test_price_change_after_order_is_not_charged(client, db, paypal):  # noqa: F811
    rec = record(db, "entry")
    payment = begin(client, db, rec, "entry")
    rec.total_fee_cents = 7000
    db.commit()
    paypal.approve(payment.paypal_order_id)
    come_back(client, rec, payment)
    assert paypal.capture_count == 0


def test_declined_then_retry(client, db, paypal):  # noqa: F811
    rec = record(db, "entry")
    payment = begin(client, db, rec, "entry")
    paypal.approve(payment.paypal_order_id)
    paypal.capture_status = "REFUSED"
    text = come_back(client, rec, payment)
    assert "declined" in text and "Pay Now" in text
    paypal.capture_status = "COMPLETED"
    again = begin(client, db, rec, "entry")
    paypal.approve(again.paypal_order_id)
    come_back(client, rec, again)
    db.refresh(rec)
    assert rec.is_paid


def test_lost_capture_response_is_recovered(client, db, paypal, sent):  # noqa: F811
    rec = record(db, "entry")
    payment = begin(client, db, rec, "entry")
    paypal.approve(payment.paypal_order_id)
    paypal.after_capture_fail = True
    text = come_back(client, rec, payment)
    assert "pay again" in text and "Pay Now" not in text
    start(client, db, rec, "entry")  # resolves the first payment instead of starting another
    db.refresh(rec)
    assert rec.is_paid and paypal.capture_count == 1 and len(paypal.orders) == 1 and len(sent) == 1


def test_interrupted_before_charge_can_be_finished(client, db, paypal):  # noqa: F811
    rec = record(db, "entry")
    payment = begin(client, db, rec, "entry")
    paypal.approve(payment.paypal_order_id)
    paypal.fail["/capture"] = 503
    come_back(client, rec, payment)
    db.refresh(payment)
    assert payment.status == "capturing"
    del paypal.fail["/capture"]
    client.get(urls(rec, "entry", rec.access_token)[0])
    db.refresh(rec)
    assert rec.is_paid and paypal.capture_count == 1


def test_interrupted_then_paid_otherwise_is_not_charged(app, client, db, paypal):  # noqa: F811
    rec = record(db, "entry")
    payment = begin(client, db, rec, "entry")
    paypal.approve(payment.paypal_order_id)
    paypal.fail["/capture"] = 503
    come_back(client, rec, payment)
    del paypal.fail["/capture"]
    rec.is_paid = True
    db.commit()
    hook(client, "CHECKOUT.ORDER.APPROVED", {"id": payment.paypal_order_id})
    assert paypal.capture_count == 0


def test_expired_order_allows_new_one(client, db, paypal):  # noqa: F811
    rec = record(db, "entry")
    payment = begin(client, db, rec, "entry")
    del paypal.orders[payment.paypal_order_id]
    come_back(client, rec, payment)
    db.refresh(payment)
    assert payment.status == "void"


def test_paypal_voids_promised_check(client, db, paypal):  # noqa: F811
    rec = record(db, "entry")
    promise_check(client, rec, "entry")
    payment = begin(client, db, rec, "entry")
    assert db.query(Payment).filter_by(method="check").one().status == "awaiting_check"
    paypal.approve(payment.paypal_order_id)
    come_back(client, rec, payment)
    assert db.query(Payment).filter_by(method="check").one().status == "void"


def test_malformed_paypal_reply(client, db, paypal, monkeypatch):  # noqa: F811
    real = paypal.handle
    rec = record(db, "entry")
    payment = begin(client, db, rec, "entry")
    monkeypatch.setattr(paypal, "handle", lambda r: httpx.Response(200, text="<html>")
                        if "/v2/" in r.url.path else real(r))
    assert "pay again" in come_back(client, rec, payment)


# ---- pending ----------------------------------------------------------------

@KINDS
def test_pending_then_completed(app, client, db, paypal, sent, kind):  # noqa: F811
    user = make_user(db, "o@x.com")
    rec = record(db, kind, user_id=user.id)
    payment = begin(client, db, rec, kind)
    paypal.approve(payment.paypal_order_id)
    paypal.capture_status = "PENDING"
    text = come_back(client, rec, payment)
    db.refresh(rec)
    assert not rec.is_paid and "Pay Now" not in text and sent == []
    start(client, db, rec, kind)
    promise_check(client, rec, kind)
    assert len(paypal.orders) == 1 and db.query(Payment).count() == 1
    login(app, user)
    assert "Payment processing" in client.get("/auth/entries").text
    login(app, None)
    paypal.captures["CAP1"]["status"] = "COMPLETED"
    assert hook(client, "PAYMENT.CAPTURE.COMPLETED", paypal.captures["CAP1"]).status_code == 200
    db.refresh(rec)
    assert rec.is_paid and len(sent) == 1


@pytest.mark.parametrize("event", ["PAYMENT.CAPTURE.DENIED", "PAYMENT.CAPTURE.DECLINED"])
def test_pending_then_denied(client, db, paypal, event):  # noqa: F811
    rec = record(db, "entry")
    payment = begin(client, db, rec, "entry")
    paypal.approve(payment.paypal_order_id)
    paypal.capture_status = "PENDING"
    come_back(client, rec, payment)
    paypal.captures["CAP1"]["status"] = "DECLINED"
    hook(client, event, paypal.captures["CAP1"])
    db.refresh(rec)
    db.refresh(payment)
    assert not rec.is_paid and payment.status == "denied"
    paypal.capture_status = "COMPLETED"
    again = begin(client, db, rec, "entry")
    paypal.approve(again.paypal_order_id)
    come_back(client, rec, again)
    db.refresh(rec)
    assert rec.is_paid


# ---- webhook ----------------------------------------------------------------

@KINDS
def test_webhook_captures_when_buyer_never_returns(client, db, paypal, sent, kind):  # noqa: F811
    rec = record(db, kind)
    payment = begin(client, db, rec, kind)
    paypal.approve(payment.paypal_order_id)
    for _ in range(2):
        assert hook(client, "CHECKOUT.ORDER.APPROVED", {"id": payment.paypal_order_id}).status_code == 200
    db.refresh(rec)
    assert rec.is_paid and paypal.capture_count == 1 and len(sent) == 1
    assert db.query(PaypalEvent).count() == 1
    assert "Payment received" in come_back(client, rec, payment)


def test_webhook_signature_is_required(client, db, paypal, monkeypatch):  # noqa: F811
    rec = record(db, "entry")
    payment = begin(client, db, rec, "entry")
    paypal.approve(payment.paypal_order_id)
    args = (client, "CHECKOUT.ORDER.APPROVED", {"id": payment.paypal_order_id})
    paypal.signature_ok = False
    assert hook(*args).status_code == 400
    paypal.signature_ok = True
    monkeypatch.setitem(HEADERS, "paypal-transmission-sig", "")
    assert hook(*args).status_code == 400
    monkeypatch.setitem(HEADERS, "paypal-transmission-sig", "x")
    monkeypatch.setattr(settings, "PAYPAL_WEBHOOK_ID", "")
    assert hook(*args).status_code == 503
    db.refresh(rec)
    assert not rec.is_paid and paypal.capture_count == 0


def test_webhook_body_is_verified_byte_for_byte(client, paypal):  # noqa: F811
    raw = '{ "id":"E9",  "event_type":"X", "resource":{"n":1.50,"u":"é"} }'
    assert client.post("/payments/webhook", headers=HEADERS, content=raw.encode()).status_code == 200
    sent_body = next(c[3] for c in paypal.calls if c[1].endswith("verify-webhook-signature"))
    assert f'"webhook_event": {raw}}}' in sent_body and json.loads(sent_body)["webhook_id"] == "WH1"


@pytest.mark.parametrize("raw,code", [("nope", 400), ("[]", 400), (b"\xff", 400),
                                      ('{}, "webhook_id": "evil"', 400), ("x" * 300_000, 413)],
                         ids=["text", "list", "binary", "injection", "huge"])
def test_bad_webhook_bodies(client, paypal, raw, code):  # noqa: F811
    assert client.post("/payments/webhook", headers=HEADERS, content=raw).status_code == code
    assert paypal.calls == []


def test_webhook_asks_for_retry_when_paypal_is_down(client, db, paypal):  # noqa: F811
    rec = record(db, "entry")
    payment = begin(client, db, rec, "entry")
    paypal.approve(payment.paypal_order_id)
    paypal.fail["/capture"] = 503
    event = (client, "CHECKOUT.ORDER.APPROVED", {"id": payment.paypal_order_id})
    assert hook(*event).status_code == 503 and db.query(PaypalEvent).count() == 0
    del paypal.fail["/capture"]
    assert hook(*event).status_code == 200
    db.refresh(rec)
    assert rec.is_paid


def test_unrelated_webhook_is_accepted_and_ignored(client, db, paypal):  # noqa: F811
    assert hook(client, "PAYMENT.CAPTURE.COMPLETED", {"id": "OLD", "custom_id": "shop"}).status_code == 200
    assert hook(client, "BILLING.SOMETHING", {}, "EV2").status_code == 200


def test_event_contents_are_not_trusted(client, db, paypal):  # noqa: F811
    """Even a genuine event only prompts us to ask PayPal for the real state."""
    rec = record(db, "entry")
    payment = begin(client, db, rec, "entry")
    paypal.captures["CAPX"] = {"id": "CAPX", "status": "DECLINED", "custom_id": payment.public_id}
    hook(client, "PAYMENT.CAPTURE.COMPLETED", {
        "id": "CAPX", "status": "COMPLETED", "custom_id": payment.public_id})
    db.refresh(rec)
    assert not rec.is_paid


def paid(client, db, paypal, kind="entry"):  # noqa: F811
    rec = record(db, kind)
    payment = begin(client, db, rec, kind)
    paypal.approve(payment.paypal_order_id)
    come_back(client, rec, payment)
    return rec, payment


@pytest.mark.parametrize("status,expected", [("REFUNDED", "refunded"), ("PARTIALLY_REFUNDED", "completed")])
def test_refund_is_flagged_for_admin(app, client, db, paypal, status, expected):  # noqa: F811
    rec, payment = paid(client, db, paypal)
    paypal.captures["CAP1"]["status"] = status
    refund = {"id": "REF1", "links": [{"rel": "up", "href": "https://api.paypal.com/v2/payments/captures/CAP1"}]}
    hook(client, "PAYMENT.CAPTURE.REFUNDED", refund)
    db.refresh(rec)
    db.refresh(payment)
    assert rec.is_paid and payment.status == expected and payment.needs_review
    hook(client, "PAYMENT.CAPTURE.COMPLETED", {"id": "CAP1"}, "LATE")  # out of order
    db.refresh(payment)
    assert payment.status == expected
    login(app, make_user(db, "a@x.com", role="admin"))
    assert "refunded in PayPal" in client.get("/admin/").text


def test_reversal_is_flagged(client, db, paypal):  # noqa: F811
    _, payment = paid(client, db, paypal)
    hook(client, "PAYMENT.CAPTURE.REVERSED", {"id": "CAP1"})
    db.refresh(payment)
    assert payment.needs_review and "reversed" in payment.note


def test_late_event_does_not_undo_mark_unpaid(app, client, db, paypal):  # noqa: F811
    rec, payment = paid(client, db, paypal)
    login(app, make_user(db, "a@x.com", role="admin"))
    client.post(f"/admin/payments/entry/{rec.id}/unpaid", data={"csrf_token": csrf(client)})
    hook(client, "PAYMENT.CAPTURE.COMPLETED", {"id": "CAP1"})
    come_back(client, rec, payment)
    db.refresh(rec)
    assert not rec.is_paid


def test_double_payment_is_flagged(client, db, paypal, sent):  # noqa: F811
    rec = record(db, "entry")
    payment = begin(client, db, rec, "entry")
    paypal.approve(payment.paypal_order_id)
    paypal.capture_status = "PENDING"
    come_back(client, rec, payment)
    rec.is_paid = True  # e.g. an admin took cash meanwhile
    db.commit()
    paypal.captures["CAP1"]["status"] = "COMPLETED"
    hook(client, "PAYMENT.CAPTURE.COMPLETED", paypal.captures["CAP1"])
    db.refresh(payment)
    assert payment.needs_review and "double" in payment.note and sent == []


def test_deleted_entry_is_not_charged_and_late_money_is_flagged(app, client, db, paypal):  # noqa: F811
    a, b = record(db, "entry"), record(db, "entry")
    unpaid, pending = begin(client, db, a, "entry"), begin(client, db, b, "entry")
    paypal.approve(pending.paypal_order_id)
    paypal.capture_status = "PENDING"
    come_back(client, b, pending)
    login(app, make_user(db, "a@x.com", role="admin"))
    for rec in (a, b):
        client.post(f"/admin/trial-entries/{rec.id}/delete", data={"csrf_token": csrf(client)})
    paypal.approve(unpaid.paypal_order_id)
    hook(client, "CHECKOUT.ORDER.APPROVED", {"id": unpaid.paypal_order_id})
    assert paypal.capture_count == 1  # only the earlier pending one
    paypal.captures["CAP1"]["status"] = "COMPLETED"
    hook(client, "PAYMENT.CAPTURE.COMPLETED", paypal.captures["CAP1"], "EV2")
    db.refresh(pending)
    assert pending.needs_review and "deleted" in pending.note
    assert "Deleted entry" in client.get("/admin/").text


def test_old_endpoints_are_gone(client):  # noqa: F811
    for path in ("create-order", "capture-order"):
        assert client.post(f"/payments/{path}", json={}).status_code in (404, 405)
