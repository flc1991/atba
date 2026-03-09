"""
Registration window logic for AKC and AHBA trials, and pre-entry pricing.

All datetimes are stored and compared in UTC.
Cutoff times are expressed in America/New_York (handles EDT/EST via zoneinfo).
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# AKC cutoff: 10 days before first day of event at 8:00 PM ET
# ---------------------------------------------------------------------------

def compute_akc_close(akc_trial_start_date: date) -> datetime:
    close_date = akc_trial_start_date - timedelta(days=10)
    return datetime(
        close_date.year, close_date.month, close_date.day,
        20, 0, 0,
        tzinfo=EASTERN,
    ).astimezone(UTC)


# ---------------------------------------------------------------------------
# AHBA cutoff: trial date at 11:59:59 PM ET
# ---------------------------------------------------------------------------

def compute_ahba_close(ahba_trial_date: date) -> datetime:
    return datetime(
        ahba_trial_date.year, ahba_trial_date.month, ahba_trial_date.day,
        23, 59, 59,
        tzinfo=EASTERN,
    ).astimezone(UTC)


# ---------------------------------------------------------------------------
# Trial open/closed status
# ---------------------------------------------------------------------------

def get_trial_status(reg_close_dt: datetime | None) -> str:
    """Return 'open' or 'closed' based on the current UTC time."""
    if reg_close_dt is None:
        return "open"
    return "open" if datetime.now(UTC) < reg_close_dt else "closed"


# ---------------------------------------------------------------------------
# Fun Run / Smart Dog Day pricing tier
# ---------------------------------------------------------------------------

def get_pricing_tier(pre_entry_close_dt: datetime | None) -> str:
    """
    Return the applicable pricing tier name:
      - 'pre_member' / 'pre_general'  — before pre_entry_close_dt (caller decides which)
      - 'late'                          — after pre_entry_close_dt or if no cutoff set
    Caller should check membership separately and choose pre_member vs pre_general.
    This function only determines whether we are still in the pre-entry window.
    """
    if pre_entry_close_dt is None:
        return "late"
    return "pre" if datetime.now(UTC) < pre_entry_close_dt else "late"


def resolve_pricing_tier(pre_entry_close_dt: datetime | None, is_current_member: bool) -> str:
    """
    Return the exact pricing tier string for a registrant.
    Member discount only applies during pre-entry window.
    """
    window = get_pricing_tier(pre_entry_close_dt)
    if window == "late":
        return "late"
    return "pre_member" if is_current_member else "pre_general"
