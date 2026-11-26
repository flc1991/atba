"""Admin editing of a trial entry's own details (not its classes, fee or payment state)."""
from datetime import date

from app.models.trial import TrialEntry
from app.utils.countries import get_country_choices
from app.utils.registry import resolve_ahba_number_type

_SHARED = (
    "handler_name", "handler_email", "handler_phone",
    "address_line1", "address_line2", "city", "state_province", "postal_code", "country",
    "dog_name", "dog_call_name", "dog_breed", "dog_registration_number", "dog_sex", "dog_dob",
    "dog_sire", "dog_dam", "dog_breeder", "dog_place_of_birth", "payment_reference",
)
_BY_BODY = {
    "AKC": ("akc_number_type", "akc_foreign_country", "akc_owner_names", "akc_owner_address",
            "akc_handler_name", "akc_handler_address"),
    "AHBA": ("ahba_agent_name", "ahba_agent_phone", "ahba_agent_email"),
}
_CHOICES = {
    "dog_sex": ("", "M", "F"),
    "akc_number_type": ("", "AKC", "PAL_ILP", "Foreign"),
}
_LABELS = {
    "dog_sex": "sex", "akc_number_type": "registration type",
    "handler_name": "handler name", "dog_name": "dog name",
}


def form_values(entry: TrialEntry) -> dict:
    """Current values as the edit form shows them."""
    values = {c.name: getattr(entry, c.name) for c in entry.__table__.columns}
    values["dog_dob"] = entry.dog_dob.isoformat() if entry.dog_dob else ""
    kind = entry.ahba_number_type or ""
    if kind and kind not in ("AKC", "ABCA", "ISDS"):
        values["ahba_number_type"], values["ahba_number_type_other"] = "Other", kind
    return values


def apply_entry_form(entry: TrialEntry, form) -> list[str]:
    """Apply submitted fields. Returns errors, in which case nothing was changed."""
    errors: list[str] = []
    changes: dict = {}
    for name in _SHARED + _BY_BODY.get(entry.governing_body, ()):
        if name not in form:
            continue
        value = form[name].strip()
        column = TrialEntry.__table__.columns[name]
        if name in ("handler_name", "dog_name") and not value:
            errors.append(f"Enter the {_LABELS[name]}.")
        elif name in _CHOICES and value not in (*_CHOICES[name], getattr(entry, name)):
            errors.append(f"Choose a valid {_LABELS[name]}.")
        elif name == "country" and value not in (
            entry.country, *(code for code, _ in get_country_choices() if code != "---")
        ):
            errors.append("Choose a valid country.")
        elif name == "dog_dob":
            try:
                changes[name] = date.fromisoformat(value) if value else None
            except ValueError:
                errors.append("Enter a valid date of birth.")
        else:
            changes[name] = value if value or not column.nullable else None
    if entry.governing_body == "AHBA" and "ahba_number_type" in form:
        changes["ahba_number_type"] = resolve_ahba_number_type(form)
    # Unticked checkboxes aren't submitted, so a hidden marker says the box was on the form.
    if entry.governing_body == "AKC" and "akc_separate_entries_shown" in form:
        changes["akc_separate_entries"] = "akc_separate_entries" in form
    if not errors:
        for name, value in changes.items():
            setattr(entry, name, value)
    return errors
