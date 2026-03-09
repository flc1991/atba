"""Session-based flash message helpers."""
from fastapi import Request


def flash(request: Request, message: str, category: str = "info") -> None:
    """Queue a flash message to be displayed on the next page load."""
    messages = request.session.setdefault("_flash", [])
    messages.append([category, message])


def get_flash_messages(request: Request) -> list[list[str]]:
    """Pop and return all queued flash messages."""
    return request.session.pop("_flash", [])
