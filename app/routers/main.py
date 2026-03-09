from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.dependencies import base_context

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def home(request: Request, ctx: dict = Depends(base_context)):
    return templates.TemplateResponse(request, "main/home.html", ctx)


@router.get("/about", response_class=HTMLResponse)
def about(request: Request, ctx: dict = Depends(base_context)):
    return templates.TemplateResponse(request, "main/atba_info.html", ctx)
