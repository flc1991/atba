"""Helpers for dog registration fields."""


def resolve_ahba_number_type(form) -> str | None:
    """Read ahba_number_type from form; if 'Other', use the custom text box."""
    raw = (form.get("ahba_number_type") or "").strip()
    if raw == "Other":
        return (form.get("ahba_number_type_other") or "").strip() or None
    return raw or None
