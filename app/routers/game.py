from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, StrictInt
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.config import settings
from app.db import get_session
from app.models import Response, Survey
from app.security import has_done_cookie, lookup_limiter, rate_limit, set_done_cookie, submit_limiter
from app.services.persona import determine_persona, persona_of
from app.services.questions import FUN_COUNT, SIGNAL_COUNT, load_questions
from app.templating import templates

router = APIRouter(prefix="/g")

SessionDep = Annotated[Session, Depends(get_session)]

GAME_LENGTH = SIGNAL_COUNT + FUN_COUNT


class AnswerIn(BaseModel):
    question_id: str
    # 일반 int는 true를 1로 받아들이므로 StrictInt를 쓴다.
    choice: Annotated[StrictInt, Field(ge=0, le=1)]


class SubmitIn(BaseModel):
    answers: list[AnswerIn] = Field(max_length=GAME_LENGTH)


def load_survey_by_slug(session: Session, slug: str) -> Survey:
    survey = session.exec(select(Survey).where(Survey.slug == slug)).first()
    if survey is None:
        raise HTTPException(status_code=404)
    if survey.is_expired:
        raise HTTPException(status_code=410)
    return survey


def load_response(session: Session, survey: Survey) -> Response | None:
    return session.exec(select(Response).where(Response.survey_id == survey.id)).first()


def public_questions(survey: Survey) -> list[dict]:
    """응답자 브라우저로는 문구만 보낸다. 태그가 보이면 조사 의도가 드러난다."""
    questions = load_questions()
    return [
        {
            "id": question_id,
            "text": questions[question_id]["text"],
            "choices": [
                {"label": choice["label"], "emoji": choice["emoji"]}
                for choice in questions[question_id]["choices"]
            ],
        }
        for question_id in survey.question_ids
    ]


@router.get("/{slug}", response_class=HTMLResponse, dependencies=[Depends(rate_limit(lookup_limiter))])
def game_page(request: Request, slug: str, session: SessionDep) -> HTMLResponse:
    survey = load_survey_by_slug(session, slug)
    context = {"survey": survey, "base_url": settings.base_url, "noindex": True}
    if load_response(session, survey) is not None:
        context["has_own_result"] = has_done_cookie(request, slug)
        return templates.TemplateResponse(request, "game_done.html", context)
    context["questions"] = public_questions(survey)
    return templates.TemplateResponse(request, "game.html", context)


@router.post("/{slug}/submit", dependencies=[Depends(rate_limit(submit_limiter))])
def submit_answers(slug: str, body: SubmitIn, session: SessionDep) -> JSONResponse:
    survey = load_survey_by_slug(session, slug)
    submitted = {answer.question_id: answer.choice for answer in body.answers}
    if len(body.answers) != len(survey.question_ids) or set(submitted) != set(survey.question_ids):
        raise HTTPException(status_code=422)

    answers = [
        {"question_id": question_id, "choice": submitted[question_id]} for question_id in survey.question_ids
    ]
    session.add(Response(survey_id=survey.id, answers=answers, persona_type=determine_persona(answers)))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409) from None

    response = JSONResponse({"next": f"/g/{slug}/result"})
    set_done_cookie(response, slug)
    return response


@router.get("/{slug}/result", response_model=None, dependencies=[Depends(rate_limit(lookup_limiter))])
def game_result(request: Request, slug: str, session: SessionDep) -> HTMLResponse | RedirectResponse:
    survey = load_survey_by_slug(session, slug)
    stored = load_response(session, survey)
    if stored is None or not has_done_cookie(request, slug):
        return RedirectResponse(f"/g/{slug}", status_code=303)
    return templates.TemplateResponse(
        request,
        "game_result.html",
        {"survey": survey, "persona": persona_of(stored.persona_type, stored.answers), "noindex": True},
    )
