"""Backups on the Admin page (backup_jobs.py, #200). Admins only, with their own second factor once the server has more
than one account (main.admin); every POST and PUT passes main.guard's CSRF check like any other change. Turning the
schedule off, or a change that makes the next backup delete backups the current settings would keep, needs the
fresh check of the most powerful admin actions (main.fresh). Nothing here downloads a backup or restores one into
the live data: restoring stays a command-line step with the app stopped (README, Backup and restore).

  GET  /api/backups            the schedule, the newest backup (when, size, where), late or not, the backups in the
                               folder with their last check, what's running and the latest runs
  POST /api/backups            Back up now: {"id"} of the run (202); 409 while another backup or check runs
  POST /api/backups/check      {"name"}: check that backup by restoring a copy into a temporary folder (202)
  PUT  /api/backups/schedule   {"schedule": "off" | "daily" | "weekly", "keep": 1..365}
  DELETE /api/backups/replaced/{name}   Delete now: what a restore replaced (DATA_DIR/before-restore-<time>/, #595),
                               or the phone and watch data it left to move in by hand (watch-restored-<time>/, #603),
                               by its name only, with the fresh check; otherwise deleted 30 days after the restore
"""
import os
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import activity, backup, backup_jobs as jobs, config, db as dbm
from .main import _client_ip, admin, fresh, get_db

router = APIRouter()


@router.get("/api/backups")
def backups(s=Depends(admin), db=Depends(get_db)):
    return jobs.status(db)


@router.post("/api/backups", status_code=202)
def back_up_now(request: Request, s=Depends(admin), db=Depends(get_db)):
    try:
        return {"id": jobs.submit(db, "backup", s, _client_ip(request))}
    except jobs.Busy as e:
        raise HTTPException(409, str(e))


class CheckIn(BaseModel):
    name: str


@router.post("/api/backups/check", status_code=202)
def check_backup(body: CheckIn, request: Request, s=Depends(admin), db=Depends(get_db)):
    """By name only, one of Sanguity's backups in the backups folder: never a path."""
    if not backup.NAME.match(body.name) or not os.path.isfile(os.path.join(config.BACKUP_DIR, body.name)):
        raise HTTPException(404, "There's no backup of that name in the backups folder.")
    try:
        return {"id": jobs.submit(db, "check", s, _client_ip(request), name=body.name)}
    except jobs.Busy as e:
        raise HTTPException(409, str(e))


@router.delete("/api/backups/replaced/{name}")
def delete_replaced(name: str, request: Request, s=Depends(admin), db=Depends(get_db)):
    """By name only, one of the before-restore-<time>/ or watch-restored-<time>/ folders restore() leaves in DATA_DIR:
    never a path or a link."""
    if not backup._replaced_dir(name):
        raise HTTPException(404, "There's nothing a restore replaced of that name in the data folder.")
    fresh(request, s, "delete what a restore replaced")
    try:
        if not backup.delete_replaced(name):
            raise HTTPException(404, "There's nothing a restore replaced of that name in the data folder.")
    except OSError:   # it couldn't be moved out of the way: nothing of it was deleted
        raise HTTPException(500, "It couldn't be deleted, and is as it was: check that the app may change its data folder.") from None
    activity.log(db, "backup.replaced", s, detail={"name": name}, ip=_client_ip(request))
    return jobs.status(db)


class ScheduleIn(BaseModel):
    schedule: Literal["off", "daily", "weekly"]
    keep: int


@router.put("/api/backups/schedule")
def change_schedule(body: ScheduleIn, request: Request, s=Depends(admin), db=Depends(get_db)):
    if not 1 <= body.keep <= jobs.KEEP_MAX:
        raise HTTPException(400, f"Keep between 1 and {jobs.KEEP_MAX} backups.")
    old, old_keep = jobs.schedule(db)
    if (old, old_keep) == (body.schedule, body.keep):
        return jobs.status(db)
    n = len(jobs.files())
    if body.schedule == "off" and old != "off":
        fresh(request, s, "turn off scheduled backups")
    elif jobs.removes(n, body.schedule, body.keep) > jobs.removes(n, old, old_keep):
        fresh(request, s, "keep fewer backups")
    with dbm.transaction(db):
        jobs.set_schedule(db, body.schedule, body.keep)
        activity.log(db, "backup.schedule", s, detail={"schedule": body.schedule, "keep": body.keep, "from": old},
                     ip=_client_ip(request))
    jobs.wake()
    return jobs.status(db)
