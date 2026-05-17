"""Unit tests for registration_windows.py — AKC/AHBA cutoff and pricing tier logic."""
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.utils.registration_windows import (
    compute_akc_close,
    compute_ahba_close,
    get_trial_status,
    get_pricing_tier,
    resolve_pricing_tier,
)

EASTERN = ZoneInfo("America/New_York")


class TestAkcClose:
    def test_basic_offset(self):
        """10 days before April 17 is April 7."""
        close = compute_akc_close(date(2026, 4, 17))
        close_et = close.astimezone(EASTERN)
        assert close_et.date() == date(2026, 4, 7)
        assert close_et.hour == 20
        assert close_et.minute == 0

    def test_stored_as_utc(self):
        close = compute_akc_close(date(2026, 4, 17))
        assert close.tzinfo == UTC or close.utcoffset().total_seconds() == 0

    def test_edt_offset(self):
        """April is EDT (UTC-4), so 8pm ET = midnight UTC next day."""
        close = compute_akc_close(date(2026, 4, 17))
        # April 7 8pm EDT = April 8 00:00 UTC
        assert close == datetime(2026, 4, 8, 0, 0, 0, tzinfo=UTC)

    def test_est_offset(self):
        """January is EST (UTC-5), so 8pm ET = 1am UTC next day."""
        close = compute_akc_close(date(2026, 1, 20))
        # January 10 8pm EST = January 11 01:00 UTC
        assert close == datetime(2026, 1, 11, 1, 0, 0, tzinfo=UTC)

    def test_crosses_month_boundary(self):
        """Start date May 1 → close April 21."""
        close = compute_akc_close(date(2026, 5, 1))
        close_et = close.astimezone(EASTERN)
        assert close_et.date() == date(2026, 4, 21)


class TestAhbaClose:
    def test_basic(self):
        """AHBA closes on trial date at 11:59:59 pm ET."""
        close = compute_ahba_close(date(2026, 4, 19))
        close_et = close.astimezone(EASTERN)
        assert close_et.date() == date(2026, 4, 19)
        assert close_et.hour == 23
        assert close_et.minute == 59
        assert close_et.second == 59

    def test_edt_utc(self):
        """April 19 11:59:59 EDT = April 20 03:59:59 UTC."""
        close = compute_ahba_close(date(2026, 4, 19))
        assert close == datetime(2026, 4, 20, 3, 59, 59, tzinfo=UTC)


class TestGetTrialStatus:
    def test_none_is_not_yet_open(self):
        assert get_trial_status(None) == "not_yet_open"

    def test_future_is_open(self):
        future = datetime.now(UTC) + timedelta(hours=1)
        assert get_trial_status(future) == "open"

    def test_past_is_closed(self):
        past = datetime.now(UTC) - timedelta(seconds=1)
        assert get_trial_status(past) == "closed"


class TestPricingTier:
    def test_no_cutoff_is_late(self):
        assert get_pricing_tier(None) == "late"

    def test_before_cutoff_is_pre(self):
        future = datetime.now(UTC) + timedelta(hours=1)
        assert get_pricing_tier(future) == "pre"

    def test_after_cutoff_is_late(self):
        past = datetime.now(UTC) - timedelta(seconds=1)
        assert get_pricing_tier(past) == "late"


class TestResolvePricingTier:
    def test_pre_member(self):
        future = datetime.now(UTC) + timedelta(hours=1)
        assert resolve_pricing_tier(future, True) == "pre_member"

    def test_pre_general(self):
        future = datetime.now(UTC) + timedelta(hours=1)
        assert resolve_pricing_tier(future, False) == "pre_general"

    def test_late_ignores_membership(self):
        past = datetime.now(UTC) - timedelta(seconds=1)
        assert resolve_pricing_tier(past, True) == "late"
        assert resolve_pricing_tier(past, False) == "late"

    def test_none_cutoff_always_late(self):
        assert resolve_pricing_tier(None, True) == "late"
        assert resolve_pricing_tier(None, False) == "late"
