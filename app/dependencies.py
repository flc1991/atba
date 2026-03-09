from typing import Generator

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.utils.flash import get_flash_messages


def get_db() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, db: Session = Depends(get_db)):
    """Return the logged-in User ORM object, or None if not authenticated."""
    from app.models.user import User  # local import avoids circular deps at startup

    user_id = request.session.get("user_id")
    if user_id:
        return db.get(User, user_id)
    return None


def require_login(current_user=Depends(get_current_user)):
    """Raise 401 if not logged in."""
    if current_user is None:
        raise HTTPException(status_code=401, detail="Login required")
    return current_user


def require_admin(current_user=Depends(get_current_user)):
    """Raise 403 if not an admin."""
    if current_user is None or current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


def base_context(request: Request, current_user=Depends(get_current_user)) -> dict:
    """Common template context injected into every route that needs it."""
    return {
        "request": request,
        "current_user": current_user,
        "flash_messages": get_flash_messages(request),
    }
