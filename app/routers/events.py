from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import settings
from app.dependencies import base_context, get_db
from app.models.event import Event
from app.models.event_pdf import EventPdf
from app.models.trial import Trial, TrialEvent, TrialEventClass
from app.utils.registration_windows import get_trial_status

router = APIRouter(tags=["events"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def events_list(request: Request, ctx: dict = Depends(base_context), db: Session = Depends(get_db)):
    today = date.today()
    events = (
        db.query(Event)
        .filter(Event.is_published.is_(True), Event.is_deleted.is_(False), Event.end_date >= today)
        .order_by(Event.start_date)
        .all()
    )
    ctx["events"] = events
    return templates.TemplateResponse(request, "events/list.html", ctx)


@router.get("/{event_id}", response_class=HTMLResponse)
def event_detail(
    event_id: int,
    request: Request,
    ctx: dict = Depends(base_context),
    db: Session = Depends(get_db),
):
    event = db.query(Event).filter_by(id=event_id, is_deleted=False).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    ctx["event"] = event

    if event.event_type == "trial":
        trials = (
            db.query(Trial).filter_by(event_id=event_id).order_by(Trial.governing_body).all()
        )
        trial_statuses = {t.id: get_trial_status(t.reg_close_dt) for t in trials}
        any_trial_open = any(s == "open" for s in trial_statuses.values())

        trial_events_map: dict[int, list[TrialEvent]] = {}
        te_classes_map: dict[int, list[TrialEventClass]] = {}
        for trial in trials:
            tes = db.query(TrialEvent).filter_by(trial_id=trial.id).order_by(TrialEvent.sort_order).all()
            trial_events_map[trial.id] = tes
            for te in tes:
                te_classes_map[te.id] = (
                    db.query(TrialEventClass)
                    .filter_by(trial_event_id=te.id)
                    .order_by(TrialEventClass.sort_order)
                    .all()
                )

        pdfs = db.query(EventPdf).filter_by(event_id=event_id).all()
        pdfs_by_body: dict[str, list[EventPdf]] = {}
        for pdf in pdfs:
            pdfs_by_body.setdefault(pdf.governing_body, []).append(pdf)

        ctx.update({
            "trials": trials,
            "trial_statuses": trial_statuses,
            "any_trial_open": any_trial_open,
            "trial_events_map": trial_events_map,
            "te_classes_map": te_classes_map,
            "pdfs_by_body": pdfs_by_body,
        })

    return templates.TemplateResponse(request, "events/detail.html", ctx)


@router.get("/{event_id}/docs/{governing_body}/{doc_type}.pdf")
def serve_pdf(
    event_id: int,
    governing_body: str,
    doc_type: str,
    db: Session = Depends(get_db),
):
    pdf = (
        db.query(EventPdf)
        .filter_by(event_id=event_id, governing_body=governing_body.upper(), document_type=doc_type)
        .first()
    )
    if not pdf:
        raise HTTPException(status_code=404, detail="Document not found")

    import os
    path = os.path.join("app", "static", "pdfs", pdf.filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found on disk")

    return FileResponse(path, media_type="application/pdf", filename=pdf.filename)
