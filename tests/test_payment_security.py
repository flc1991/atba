"""Payment access, pricing and status tests (plan items A1-A6). No network calls."""
import re
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import object_session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models.dog  # noqa: F401
import app.models.event_pdf  # noqa: F401
import app.models.member  # noqa: F401
import app.models.payment  # noqa: F401
from app import create_app
from app.config import settings
from app.database import Base
from app.dependencies import get_current_user, get_db
from app.models.event import Event
from app.models.payment import Payment
from app.models.registration import Registration, RegistrationDog
from app.models.trial import Trial, TrialEntry, TrialEntrySelection, TrialEvent
from app.models.user import User
from app.utils import paypal_helpers
from tests.fake_paypal import FakePayPal

ADDR = dict(address_line1="1 St", city="X", postal_code="1", country="US")
KINDS = pytest.mark.parametrize("kind", ["registration", "entry"])
ROUTES = pytest.mark.parametrize(
    "kind,route", [("fun_run", "fun-run"), ("smart_dog_day", "smart-dog-day")]
)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def paypal(monkeypatch):
    fake = FakePayPal()
    monkeypatch.setattr(paypal_helpers, "_client", fake.client)
    return fake


@pytest.fixture
def app(db, monkeypatch, paypal):
    monkeypatch.setattr(settings, "PAYPAL_CLIENT_ID", "id")
    monkeypatch.setattr(settings, "PAYPAL_CLIENT_SECRET", "secret")
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    return app


@pytest.fixture
def client(app):
    return TestClient(app, base_url="https://testserver", follow_redirects=False)


def login(app, user):
    app.dependency_overrides[get_current_user] = lambda: user


def add(db, obj):
    db.add(obj)
    db.commit()
    return obj


def make_event(db, kind="fun_run", days=30, **kw):
    day = date.today() + timedelta(days=days)
    return add(db, Event(**{**dict(
        title="E", event_type=kind, start_date=day, end_date=day, is_published=True,
        fee_pre_member_cents=1000, fee_pre_general_cents=2500, fee_late_cents=3500,
    ), **kw}))


def make_reg(db, ev, **kw):
    return add(db, Registration(**{**dict(
        event_id=ev.id, name="T", email="t@example.com", dog_name="D",
        pricing_tier="late", fee_cents=3500,
    ), **ADDR, **kw}))


def make_entry(db, close_in_days=5, **kw):
    ev = make_event(db, kind="trial")
    trial = add(db, Trial(
        event_id=ev.id, governing_body="AKC",
        reg_close_dt=datetime.now(UTC) + timedelta(days=close_in_days),
    ))
    te = add(db, TrialEvent(
        trial_id=trial.id, name="JHD", fee_cents=6000, is_test_class=True, sort_order=1,
    ))
    entry = add(db, TrialEntry(**{**dict(
        event_id=ev.id, governing_body="AKC", handler_name="H",
        handler_email="h@example.com", dog_name="D", total_fee_cents=6000,
    ), **ADDR, **kw}))
    add(db, TrialEntrySelection(
        trial_entry_id=entry.id, trial_event_id=te.id, akc_trial_pref="event_1",
    ))
    return entry


def make_user(db, email, role="user"):
    return add(db, User(name="U", email=email, password_hash="x", role=role))


def record(db, kind, **kw):
    return make_reg(db, make_event(db), **kw) if kind == "registration" else make_entry(db, **kw)


def urls(rec, kind, token=""):
    """[checkout, return, cancel, confirmation]. The order is one PayPal doesn't know."""
    order = f"UNKNOWN-{kind}-{rec.id}"
    session = object_session(rec)
    if not session.query(Payment).filter_by(paypal_order_id=order).first():
        add(session, Payment(kind=kind, record_id=rec.id, method="paypal", status="created",
                             entry_cents=1, total_cents=1, paypal_order_id=order, public_id=order))
    page = (f"register/confirmation?registration_id={rec.id}" if kind == "registration"
            else f"trial/confirmation?entry_id={rec.id}")
    return [
        f"/payments/checkout/{kind}/{rec.id}?t={token}",
        f"/payments/return?token={order}&t={token}",
        f"/payments/cancel?token={order}&t={token}",
        f"/events/{rec.event_id}/{page}&t={token}",
    ]


def submit(client, db, ev, route):
    """POST a registration using a CSRF token taken from another, open event's form."""
    donor = make_event(db, kind=ev.event_type)
    page = client.get(f"/events/{donor.id}/register/{route}")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text)[1]
    return client.post(f"/events/{ev.id}/register/{route}", data={
        "csrf_token": token, "name": "Z", "email": "z@example.com",
        "dog_name": "D", "dog_0_name": "D", "dog_0_event_1": "Duck A",
    })


@KINDS
def test_return_never_marks_paid(client, db, kind):  # A1, A6
    first, second = record(db, kind), record(db, kind)
    for rec in (first, second, first):
        r = client.get(urls(rec, kind, rec.access_token)[1] + "&tx=SAME")
        assert r.status_code == 303
        page = client.get(r.headers["location"])
        assert page.status_code == 200 and "payment pending" in page.text
        db.refresh(rec)
        assert rec.is_paid is False and rec.paypal_order_id is None


@KINDS
@pytest.mark.parametrize("token", ["", "wrong", "%C3%A9"])
def test_strangers_get_404(client, db, kind, token):  # A4
    rec = record(db, kind)
    for url in urls(rec, kind, token):
        assert client.get(url).status_code == 404


@KINDS
def test_blank_stored_token_grants_nothing(client, db, kind):  # A4
    rec = record(db, kind)
    rec.access_token = None
    db.commit()
    for url in urls(rec, kind):
        assert client.get(url).status_code == 404


@KINDS
def test_owner_admin_and_token_allowed_other_user_not(app, client, db, kind):  # A4
    owner, other = make_user(db, "o@x.com"), make_user(db, "n@x.com")
    admin = make_user(db, "a@x.com", role="admin")
    rec = record(db, kind, user_id=owner.id)
    for user, ok in ((owner, True), (admin, True), (other, False)):
        login(app, user)
        for url in urls(rec, kind):
            assert (client.get(url).status_code in (200, 303)) is ok
    login(app, None)
    for url in urls(rec, kind, rec.access_token):
        assert client.get(url).status_code in (200, 303)


@KINDS
def test_confirmation_matches_paid_state(client, db, kind):  # A5
    rec = record(db, kind)
    url = urls(rec, kind, rec.access_token)[3]
    text = client.get(url).text
    assert "payment pending" in text and "Amount due" in text and "Pay Now" in text
    assert "Amount paid" not in text and "Total paid" not in text
    assert "registered!" not in text and "confirmation email" not in text
    rec.is_paid = True
    db.commit()
    text = client.get(url).text
    assert "Amount due" not in text and "Pay Now" not in text
    assert "Amount paid" in text or "Total paid" in text


@ROUTES
@pytest.mark.parametrize("kw", [
    dict(fee_pre_member_cents=None, fee_pre_general_cents=None, fee_late_cents=None),  # A2
    dict(days=-2),  # A3
])
def test_unopened_or_past_event_refused(client, db, kind, route, kw):
    ev = make_event(db, kind=kind, **kw)
    assert client.get(f"/events/{ev.id}/register/{route}").status_code == 303
    assert submit(client, db, ev, route).headers["location"] == f"/events/{ev.id}"
    assert db.query(Registration).count() == 0
    assert f"/register/{route}" not in client.get(f"/events/{ev.id}").text


@ROUTES
def test_open_event_goes_to_checkout(client, db, kind, route):
    ev = make_event(db, kind=kind)
    assert f"/register/{route}" in client.get(f"/events/{ev.id}").text
    page = client.get(submit(client, db, ev, route).headers["location"])
    assert page.status_code == 200
    assert "$35.00" in page.text and "$36.40" in page.text  # entry, with 4% fee


@ROUTES
def test_explicit_zero_fee_is_free(client, db, kind, route):  # A2
    ev = make_event(db, kind=kind, fee_late_cents=0)
    client.get(submit(client, db, ev, route).headers["location"])
    reg = db.query(Registration).one()
    assert reg.fee_cents == 0 and reg.is_paid


def test_zero_pre_entry_fee_does_not_fall_back_to_late(client, db):
    ev = make_event(db, kind="smart_dog_day", fee_pre_general_cents=0,
                    pre_entry_close_dt=datetime.now(UTC) + timedelta(days=5))
    submit(client, db, ev, "smart-dog-day")
    assert db.query(Registration).one().fee_cents == 0


def test_checkout_refuses_closed_registration(client, db):  # A3
    rec = make_reg(db, make_event(db, days=-2))
    r = client.get(urls(rec, "registration", rec.access_token)[0])
    assert r.status_code == 303 and "confirmation" in r.headers["location"]
    assert "Pay Now" not in client.get(r.headers["location"]).text


def test_checkout_refuses_zero_record_when_fee_blank(client, db):  # A2
    rec = make_reg(db, make_event(db, fee_late_cents=None), fee_cents=0)
    client.get(urls(rec, "registration", rec.access_token)[0])
    db.refresh(rec)
    assert rec.is_paid is False


def test_checkout_refuses_closed_trial_but_allows_manual(client, db):  # A3
    closed = make_entry(db, close_in_days=-1)
    assert client.get(urls(closed, "entry", closed.access_token)[0]).status_code == 303
    manual = make_entry(db, close_in_days=-1, is_manual_entry=True)
    assert client.get(urls(manual, "entry", manual.access_token)[0]).status_code == 200


def test_zero_manual_entry_is_not_auto_paid(client, db):  # A2
    rec = make_entry(db, is_manual_entry=True, total_fee_cents=0)
    client.get(urls(rec, "entry", rec.access_token)[0])
    db.refresh(rec)
    assert rec.is_paid is False


def test_stale_pre_entry_price_moves_to_late(client, db):  # A3
    ev = make_event(db, pre_entry_close_dt=datetime.now(UTC) - timedelta(days=1))
    rec = make_reg(db, ev, pricing_tier="pre_member", fee_cents=2000)
    add(db, RegistrationDog(
        registration_id=rec.id, dog_name="D", event_1="Duck A", event_2="Duck B"))
    page = client.get(urls(rec, "registration", rec.access_token)[0])
    assert "$70.00" in page.text and "$72.80" in page.text
    assert "late rate now applies" in page.text and "form.submit()" not in page.text
