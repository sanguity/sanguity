"""Text recognition for scanned documents (#1129; ocr.py). The routes:

  GET    /api/settings/install/ocr                      admin: General → Text recognition (ocr.status)
  PUT    /api/settings/install/ocr/languages/{name}     admin, after the fresh check: import a language file, the
                                                        request body (nginx lets it be up to 65 MB here alone)
  POST   /api/settings/install/ocr/download             admin, after the fresh check, {code}: download one from
                                                        tessdata_fast, while ocr.downloads is on
  DELETE /api/settings/install/ocr/languages/{code}     admin, after the fresh check: remove one added
  GET    /api/profiles/{pid}/document-text/{doc}        viewers and up: a document's text and state, for its drawer
                                                        (its text logged as opening the document is)
  POST   /api/profiles/{pid}/documents/search           viewers and up, {q}: the records whose documents' text has q
                                                        (in the body, so what's searched is in no address)

The languages in use and the download switch are the install settings ocr.languages and ocr.downloads, changed as
any install setting is (PUT /api/settings/install/{key}). Each language added or removed is in the install's
activity log ("ocr.language"), and each download in Outside connections."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from . import activity, crypto, db as dbm, ocr, settings
from .main import _client_ip, _opened, admin, fresh, get_db, need, user

router = APIRouter()


@router.get("/api/settings/install/ocr")
def ocr_status(s=Depends(admin), db=Depends(get_db)):
    return ocr.status(db)


def _added(db, s, request, entry, how):
    activity.log(db, "ocr.language", s, detail={"code": entry["code"], "name": entry["name"], "how": how}, ip=_client_ip(request))
    return {"added": entry, **ocr.status(db)}


@router.put("/api/settings/install/ocr/languages/{name}")
async def ocr_import(name: str, request: Request, s=Depends(admin), db=Depends(get_db)):
    """An admin's own .traineddata file: its name a language code, at most ocr.MAX_LANG_BYTES, checked, then tried."""
    try:
        ocr.code_of(name)
        if int(request.headers.get("content-length") or 0) > ocr.MAX_LANG_BYTES:
            raise ocr.LanguageError(f"A language file can be at most {ocr.MAX_LANG_BYTES >> 20} MB.")
    except ValueError as e:   # LanguageError, or a content-length that isn't a number
        raise HTTPException(400, str(e) if isinstance(e, ocr.LanguageError) else "Send the file as the request body.")
    await run_in_threadpool(fresh, request, s, "add a text recognition language")
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > ocr.MAX_LANG_BYTES:
            raise HTTPException(400, f"A language file can be at most {ocr.MAX_LANG_BYTES >> 20} MB.")
    try:
        entry = await run_in_threadpool(ocr.import_language, name, bytes(data))
    except ocr.LanguageError as e:
        raise HTTPException(400, str(e))
    return await run_in_threadpool(_added, db, s, request, entry, "imported")


class Code(BaseModel):
    code: str


@router.post("/api/settings/install/ocr/download")
def ocr_download(body: Code, request: Request, s=Depends(admin), db=Depends(get_db)):
    fresh(request, s, "download a text recognition language")
    try:
        entry = ocr.download(db, body.code, s, _client_ip(request))
    except ocr.LanguageError as e:
        raise HTTPException(409 if "turn it on" in str(e) else 400, str(e))
    return _added(db, s, request, entry, "downloaded")


@router.delete("/api/settings/install/ocr/languages/{code}")
def ocr_remove(code: str, request: Request, s=Depends(admin), db=Depends(get_db)):
    fresh(request, s, "remove a text recognition language")
    name = next((x["name"] for x in ocr.languages() if x["code"] == code and not x["built_in"]), None)
    if name is None or not ocr.remove_language(code):
        raise HTTPException(404, "There's no such language file here.")
    with dbm.transaction(db):
        wanted = settings.stored(db, "install").get("ocr.languages")
        if wanted and code in wanted:
            settings.put(db, "ocr.languages", [c for c in wanted if c != code], by=s["id"])
        activity.log(db, "ocr.language", s, detail={"code": code, "name": name, "how": "removed"}, ip=_client_ip(request))
    return ocr.status(db)


@router.get("/api/profiles/{pid}/document-text/{doc}")
def document_text(pid: int, doc: str, request: Request, s=Depends(user), db=Depends(get_db)):
    """A document's text, logged as opening the document is (once a session, by its record's title): it says what
    the document does."""
    need(db, s, pid, "viewer")
    got = ocr.text_of(db, pid, doc[:64])
    if got is None:
        raise HTTPException(404, "Document not found.")
    if got["state"] == "found":
        r = db.execute("SELECT path FROM documents WHERE id=? AND profile_id=?", (doc[:64], pid)).fetchone()
        _opened(db, s, pid, doc, {"path": crypto.dec(r["path"])}, request)
    return got


class Query(BaseModel):
    q: str = ""


@router.post("/api/profiles/{pid}/documents/search")
def documents_search(pid: int, body: Query, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "viewer")
    return {"events": ocr.search(db, pid, body.q)}
