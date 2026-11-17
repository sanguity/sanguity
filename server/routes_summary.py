"""The visit summary (#48; summary.py builds it, pdfdoc.py writes the PDF).

  GET  /api/profiles/{pid}/summary               what the page offers: upcoming appointments, past visits, labs,
                                                 the key labs, the sections and your questions draft
  PUT  /api/profiles/{pid}/summary/questions     {"text"}: keep your own questions draft (empty removes it)
  POST /api/profiles/{pid}/summary/preview       {appointment, since, sections, labs, questions}: the summary as
                                                 JSON, with how many pages it takes and what didn't fit
  POST /api/profiles/{pid}/summary/pdf           the same, as a PDF download; keeps the questions as the draft too

Everyone a profile is shared with may make one, viewers included: it's the records they can already read, put on
paper, and the questions are a draft for the visit, not part of the records: each person's own (#219). Each PDF made is logged on the
profile's activity log ("summary": the sections, dates and counts, never the questions or any result). The PDF is
made in memory and streamed: nothing is written to disk, and it isn't cached (no-store, as every API answer).
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import activity, cycle_tracking, db as dbm, profiles as prof, summary
from .main import _client_ip, get_db, need, user

router = APIRouter()
# menstrual cycle records (#183): offered only to owners and editors while tracking is on, and never ticked by default
summary.register_section(*cycle_tracking.SUMMARY_SECTION, cycle_tracking.summary_rows)


class Draft(BaseModel):
    text: str = Field("", max_length=summary.MAX_QUESTION_TEXT)


class Options(BaseModel):
    appointment: Optional[int] = None
    since: Optional[str] = Field(None, max_length=10)
    sections: List[str] = Field(default_factory=list, max_length=20)
    labs: List[str] = Field(default_factory=list, max_length=summary.MAX_PICKED)
    questions: str = Field("", max_length=summary.MAX_QUESTION_TEXT)


def _name(db, pid):
    r = db.execute("SELECT name FROM profiles WHERE id=?", (pid,)).fetchone()
    return r["name"] if r else ""


def _build(db, s, pid, body):
    if "cycle" in body.sections and (need(db, s, pid, "viewer") == "viewer" or not cycle_tracking.is_on(db, pid)):
        raise HTTPException(403, "The cycle records aren't available to you here.")   # (#183)
    prof.sync_build(db, pid)
    try:
        return summary.build(db, pid, s["id"], _name(db, pid), body.model_dump())
    except summary.SummaryError as e:
        raise HTTPException(400, str(e)) from None


@router.get("/api/profiles/{pid}/summary")
def summary_options(pid: int, s=Depends(user), db=Depends(get_db)):
    role = need(db, s, pid, "viewer")
    prof.sync_build(db, pid)
    opts = summary.options(db, pid, s["id"])
    cycle = role != "viewer" and cycle_tracking.is_on(db, pid)   # (#183) ticked each time: "ask"
    opts["sections"] = [{**x, "ask": True} if x["id"] == "cycle" else x for x in opts["sections"] if x["id"] != "cycle" or cycle]
    return {"profile": {"id": pid, "name": _name(db, pid)}, "role": role, **opts}


@router.put("/api/profiles/{pid}/summary/questions")
def keep_questions(pid: int, body: Draft, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "viewer")
    try:
        with dbm.transaction(db):
            text = summary.save_questions(db, pid, s["id"], body.text)
    except summary.SummaryError as e:
        raise HTTPException(400, str(e)) from None
    return {"questions": text}


@router.post("/api/profiles/{pid}/summary/preview")
def preview(pid: int, body: Options, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "viewer")
    out = _build(db, s, pid, body)
    _, fit = summary.render(out)
    return {**out, **fit}


@router.post("/api/profiles/{pid}/summary/pdf")
def make_pdf(pid: int, body: Options, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "viewer")
    out = _build(db, s, pid, body)
    pdf, fit = summary.render(out)
    with dbm.transaction(db):
        summary.save_questions(db, pid, s["id"], body.questions)
        activity.log(db, "summary", s, pid, {"sections": out["sections"], "since": out["since"]["date"],
                                             "for": (out["for"] or {}).get("date"), "pages": fit["pages"],
                                             "questions": len(out.get("questions") or [])}, _client_ip(request))
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{summary.filename(out["profile"], out["prepared"])}"',
        "Cache-Control": "no-store", "Pragma": "no-cache", "Content-Length": str(len(pdf))})
