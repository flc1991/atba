from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.dependencies import base_context, get_db
from app.models.event import Event

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def home(request: Request, ctx: dict = Depends(base_context), db: Session = Depends(get_db)):
    today = date.today()
    upcoming = (
        db.query(Event)
        .filter(Event.is_published.is_(True), Event.is_deleted.is_(False), Event.end_date >= today)
        .order_by(Event.start_date)
        .limit(5)
        .all()
    )
    ctx["upcoming_events"] = upcoming
    return templates.TemplateResponse(request, "main/home.html", ctx)


@router.get("/about", response_class=HTMLResponse)
def about(request: Request, ctx: dict = Depends(base_context)):
    return templates.TemplateResponse(request, "main/atba_info.html", ctx)
