from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlmodel import Session, select

from app.config import settings
from app.db import get_session
from app.models import BUDGET_MAX, BUDGET_MIN, OCCASIONS, RECIPIENT_NAME_MAX_LENGTH, RELATIONS, Response, Survey
from app.security import (
    ai_comment_limiter,
    create_limiter,
    csrf_nonce,
    csrf_token_for,
    is_valid_csrf,
    lookup_limiter,
    rate_limit,
    set_csrf_cookie,
)
from app.services import ai_comment
from app.services.persona import persona_of
from app.services.questions import pick_question_ids
from app.services.recommend import build_report
from app.templating import templates

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]
FormField = Annotated[str, Form()]

DEFAULT_FORM_VALUES = {
    "recipient_name": "",
    "relation": "",
    "occasion": "",
    "budget_min": "30000",
    "budget_max": "70000",
}


def share_url(survey: Survey) -> str:
    return f"{settings.base_url}/g/{survey.slug}"


def result_url(survey: Survey) -> str:
    return f"{settings.base_url}/r/{survey.admin_token}"


def load_survey_by_token(session: Session, admin_token: str) -> Survey:
    survey = session.exec(select(Survey).where(Survey.admin_token == admin_token)).first()
    if survey is None:
        raise HTTPException(status_code=404)
    if survey.is_expired:
        raise HTTPException(status_code=410)
    return survey


def validate_survey_form(values: dict[str, str]) -> tuple[dict | None, str | None]:
    name = " ".join(values["recipient_name"].split()).removesuffix("님").strip()
    if not name:
        return None, "받는 사람 별명을 입력해 주세요."
    if len(name) > RECIPIENT_NAME_MAX_LENGTH:
        return None, f"별명은 {RECIPIENT_NAME_MAX_LENGTH}자까지 입력할 수 있어요."
    if values["relation"] not in RELATIONS:
        return None, "관계를 골라 주세요."
    if values["occasion"] not in OCCASIONS:
        return None, "상황을 골라 주세요."
    try:
        budget_min, budget_max = int(values["budget_min"]), int(values["budget_max"])
    except ValueError:
        return None, "예산 범위를 다시 골라 주세요."
    if not BUDGET_MIN <= budget_min < budget_max <= BUDGET_MAX:
        return None, "예산 범위를 다시 골라 주세요."
    return {
        "recipient_name": name,
        "relation": values["relation"],
        "occasion": values["occasion"],
        "budget_min": budget_min,
        "budget_max": budget_max,
    }, None


def render_form(
    request: Request, values: dict[str, str], error: str | None = None, status_code: int = 200
) -> HTMLResponse:
    nonce = csrf_nonce(request)
    response = templates.TemplateResponse(
        request,
        "new.html",
        {
            "values": values,
            "error": error,
            "csrf_token": csrf_token_for(nonce),
            "relations": RELATIONS,
            "occasions": OCCASIONS,
            "budget_floor": BUDGET_MIN,
            "budget_ceiling": BUDGET_MAX,
            "name_max_length": RECIPIENT_NAME_MAX_LENGTH,
        },
        status_code=status_code,
    )
    set_csrf_cookie(response, nonce)
    return response


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html")


@router.get("/new", response_class=HTMLResponse)
def new_survey_form(request: Request) -> HTMLResponse:
    return render_form(request, DEFAULT_FORM_VALUES)


@router.post("/new", response_model=None, dependencies=[Depends(rate_limit(create_limiter))])
def create_survey(
    request: Request,
    session: SessionDep,
    csrf_token: FormField = "",
    recipient_name: FormField = "",
    relation: FormField = "",
    occasion: FormField = "",
    budget_min: FormField = "",
    budget_max: FormField = "",
) -> HTMLResponse | RedirectResponse:
    values = {
        "recipient_name": recipient_name,
        "relation": relation,
        "occasion": occasion,
        "budget_min": budget_min,
        "budget_max": budget_max,
    }
    if not is_valid_csrf(request, csrf_token):
        return render_form(request, values, "페이지가 오래되었어요. 다시 한 번 눌러 주세요.", status_code=403)

    data, error = validate_survey_form(values)
    if error:
        return render_form(request, values, error, status_code=422)

    survey = Survey(**data, question_ids=pick_question_ids())
    session.add(survey)
    session.commit()
    return RedirectResponse(f"/r/{survey.admin_token}/links", status_code=303)


@router.get("/r/{admin_token}/links", response_class=HTMLResponse, dependencies=[Depends(rate_limit(lookup_limiter))])
def survey_links(request: Request, admin_token: str, session: SessionDep) -> HTMLResponse:
    survey = load_survey_by_token(session, admin_token)
    return templates.TemplateResponse(
        request,
        "links.html",
        {"survey": survey, "share_url": share_url(survey), "result_url": result_url(survey), "noindex": True},
    )


@router.get("/r/{admin_token}", response_class=HTMLResponse, dependencies=[Depends(rate_limit(lookup_limiter))])
def survey_result(request: Request, admin_token: str, session: SessionDep) -> HTMLResponse:
    survey = load_survey_by_token(session, admin_token)
    stored = session.exec(select(Response).where(Response.survey_id == survey.id)).first()
    if stored is None:
        return templates.TemplateResponse(
            request,
            "pending.html",
            {"survey": survey, "share_url": share_url(survey), "noindex": True},
        )
    report = build_report(stored.answers, survey.budget_min, survey.budget_max)
    ai_enabled = ai_comment.is_enabled()
    ai_picks = ai_comment.load_cached_picks(session, survey.id, report) if ai_enabled else None
    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "survey": survey,
            "persona": persona_of(stored.persona_type, stored.answers),
            "report": report,
            "ai_picks": ai_picks,
            # 응답을 기다리느라 페이지가 멈추지 않도록, 없으면 페이지를 연 뒤에 따로 요청한다.
            "ai_pending": ai_enabled and ai_picks is None,
            "noindex": True,
        },
    )


@router.post(
    "/r/{admin_token}/ai-comment", response_model=None, dependencies=[Depends(rate_limit(ai_comment_limiter))]
)
def survey_ai_comment(request: Request, admin_token: str, session: SessionDep) -> HTMLResponse | PlainTextResponse:
    """코멘트 조각을 돌려준다. 보여줄 코멘트가 없으면 204로 답하고 화면은 그 섹션을 숨긴다."""
    survey = load_survey_by_token(session, admin_token)
    stored = session.exec(select(Response).where(Response.survey_id == survey.id)).first()
    if stored is None:
        return PlainTextResponse(status_code=204)

    picks = ai_comment.get_or_create_picks(
        session,
        survey_id=survey.id,
        recipient_name=survey.recipient_name,
        persona=persona_of(stored.persona_type, stored.answers),
        report=build_report(stored.answers, survey.budget_min, survey.budget_max),
        answers=stored.answers,
    )
    if picks is None:
        return PlainTextResponse(status_code=204)
    return templates.TemplateResponse(request, "_ai_comment.html", {"picks": picks})
