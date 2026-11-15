"""Upload routes (issue #19): the import page sends a profile's files here, one request each.

  GET    /api/uploads/limits                              what an upload may hold
  POST   /api/profiles/{pid}/uploads                      a new upload: {"id", "limits"}
  PUT    /api/profiles/{pid}/uploads/{id}/files?path=...  one file: the request body is the file, streamed to disk
  POST   /api/profiles/{pid}/uploads/{id}/finish          unpack zips, check, and mark it ready: {"files", "bytes"}
  GET    /api/profiles/{pid}/uploads/{id}                 its state and paths
  DELETE /api/profiles/{pid}/uploads/{id}                 discard it

Owners and editors only. The bodies are raw (no multipart parser), so nginx lets only the PUT be large; every
other /api/ request stays under its 64 KB cap. Changes need the X-Sanguity header, as every API write does.
"""
import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from . import activity, config, uploads
from .main import _client_ip, get_db, need, user

router = APIRouter()


def _refused(e):
    return HTTPException(e.status, str(e))


class _Body:
    """The request body as a file-like object for a worker thread: each read() takes the next piece the server
    received, waiting for it on the event loop."""
    def __init__(self, request):
        self._it = request.stream().__aiter__()

    async def _next(self):
        try:
            return await self._it.__anext__()
        except StopAsyncIteration:
            return b""

    def read(self, n=-1):   # n is ignored: a piece is as large as it arrived; b"" at the end
        return anyio.from_thread.run(self._next)


@router.get("/api/uploads/limits")
def upload_limits(s=Depends(user)):
    return uploads.limits()


@router.post("/api/profiles/{pid}/uploads")
def new_upload(pid: int, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    return {"id": uploads.create(db, pid, s["id"]), "limits": uploads.limits()}


@router.put("/api/profiles/{pid}/uploads/{upload_id}/files")
async def upload_file(pid: int, upload_id: int, path: str, request: Request, s=Depends(user), db=Depends(get_db)):
    await run_in_threadpool(need, db, s, pid, "editor")
    try:
        size = int(request.headers.get("content-length") or 0)
    except ValueError:
        size = 0
    if size > config.UPLOAD_MAX_FILE:   # refused before any of it is read
        raise _refused(uploads.too_big(True))
    try:
        return await run_in_threadpool(uploads.add_file, db, pid, upload_id, path, _Body(request))
    except uploads.UploadError as e:
        raise _refused(e) from None


@router.post("/api/profiles/{pid}/uploads/{upload_id}/finish")
def finish_upload(pid: int, upload_id: int, request: Request, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    try:
        done = uploads.finish(db, pid, upload_id)
    except uploads.UploadError as e:
        raise _refused(e) from None
    activity.log(db, "upload", s, pid, {"upload": upload_id, **done}, _client_ip(request))
    return done


@router.get("/api/profiles/{pid}/uploads/{upload_id}")
def upload_info(pid: int, upload_id: int, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    try:
        return uploads.info(db, pid, upload_id)
    except uploads.UploadError as e:
        raise _refused(e) from None


@router.delete("/api/profiles/{pid}/uploads/{upload_id}")
def discard_upload(pid: int, upload_id: int, s=Depends(user), db=Depends(get_db)):
    need(db, s, pid, "editor")
    try:
        uploads.discard(db, pid, upload_id)
    except uploads.UploadError as e:
        raise _refused(e) from None
    return {"ok": True}
