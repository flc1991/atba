"""Rules and labels for one class selection on a trial entry."""
from sqlalchemy.orm import Session

from app.models.trial import Trial, TrialEntry, TrialEntrySelection, TrialEvent, TrialEventClass

DAY_CHOICES = {"AKC": ("friday", "saturday"), "AHBA": ("saturday", "sunday")}

# "both" only exists on old rows; new ones use a row per side.
_SIDES = {"event_1": {1}, "event_2": {2}, "both": {1, 2}}


def has_second_trial(trial: Trial | None) -> bool:
    if trial is None:
        return False
    number = trial.akc_event_number_2 if trial.governing_body == "AKC" else trial.ahba_event_2_judge
    return bool(number)


def trial_label(trial: Trial | None, pref: str | None) -> str:
    """'Event 1 (#2026226905)' for AKC, 'Event 1 (judge)' for AHBA, '' if not set."""
    if pref == "both":
        return "Event 1 and Event 2"
    if pref not in ("event_1", "event_2"):
        return ""
    side = pref[-1]
    detail = ""
    if trial is not None and trial.governing_body == "AKC":
        number = trial.akc_event_number if side == "1" else trial.akc_event_number_2
        detail = f" (#{number})" if number else ""
    elif trial is not None and trial.governing_body == "AHBA":
        judge = trial.ahba_event_1_judge if side == "1" else trial.ahba_event_2_judge
        detail = f" ({judge})" if judge else ""
    return f"Event {side}{detail}"


def entry_trial(db: Session, entry: TrialEntry) -> Trial | None:
    return db.query(Trial).filter_by(
        event_id=entry.event_id, governing_body=entry.governing_body
    ).first()


def event_classes(db: Session, te: TrialEvent) -> list[TrialEventClass]:
    return (
        db.query(TrialEventClass).filter_by(trial_event_id=te.id)
        .order_by(TrialEventClass.sort_order).all()
    )


def day_options(body: str, te: TrialEvent, current: str | None = None) -> list[str]:
    """Days that may be chosen; '' is no preference. The row's current value stays allowed."""
    choices = DAY_CHOICES.get(body, ())
    if te.available_days in (None, "", "either"):
        options = ["", *choices]
    elif te.available_days in choices:
        options = [te.available_days]
    else:
        options = [""]
    if current is not None and current not in options:
        options.append(current)
    return options


def pref_options(trial: Trial | None, current: str | None = None) -> list[str]:
    options = ["event_1", "event_2"] if has_second_trial(trial) else ["event_1"]
    if current and current not in options:
        options.append(current)
    return options


def parse_selection(
    db: Session, entry: TrialEntry, te: TrialEvent, form, sel: TrialEntrySelection | None = None
) -> dict:
    """Validated column values for adding a selection, or changing `sel`.

    Raises ValueError with a message for the admin.
    """
    trial = db.get(Trial, te.trial_id)
    if trial is None or trial.event_id != entry.event_id or trial.governing_body != entry.governing_body:
        raise ValueError("That event is not part of this entry's trial.")

    class_id = None
    if not te.is_test_class:
        raw = (form.get("trial_event_class_id") or "").strip()
        if raw not in {str(c.id) for c in event_classes(db, te)}:
            raise ValueError(f"Choose a class for {te.name}.")
        class_id = int(raw)

    pref = (form.get("akc_trial_pref") or "").strip()
    if pref not in pref_options(trial, sel.akc_trial_pref if sel else None):
        raise ValueError("Choose a valid trial (Event 1 or Event 2).")

    day = (form.get("day_preference") or "").strip()
    if day not in day_options(
        entry.governing_body, te, (sel.day_preference or "") if sel else None
    ):
        raise ValueError(f"That day is not available for {te.name}.")

    others = db.query(TrialEntrySelection).filter_by(trial_entry_id=entry.id, trial_event_id=te.id)
    for other in others:
        if sel is not None and other.id == sel.id:
            continue
        if _SIDES.get(pref, {1}) & _SIDES.get(other.akc_trial_pref, {1}):
            raise ValueError(f"This dog is already entered in {te.name} for that trial.")

    return {
        "trial_event_class_id": class_id,
        "akc_trial_pref": pref,
        "day_preference": day or None,
    }
