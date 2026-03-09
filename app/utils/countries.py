"""Build a sorted country list for <select> dropdowns, with common countries at top."""
import pycountry

_PRIORITY = ["US", "CA", "MX", "GB", "AU", "DE", "FR", "NL", "NZ", "BE"]


def get_country_choices() -> list[tuple[str, str]]:
    """Return [(alpha_2, name), ...] with priority countries first, then alphabetical."""
    all_countries = {c.alpha_2: c.name for c in pycountry.countries}
    priority = [(code, all_countries[code]) for code in _PRIORITY if code in all_countries]
    rest = sorted(
        [(code, name) for code, name in all_countries.items() if code not in _PRIORITY],
        key=lambda x: x[1],
    )
    return priority + [("---", "---")] + rest
