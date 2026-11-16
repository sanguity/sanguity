"""Outside connections (#59, #97; outside.py): every host this install may contact, and when it last did.

  GET /api/outside   what the Settings page's "Outside connections" section shows: admins the whole install,
                     owners and editors their profiles' entries (viewers see an empty list)
"""
from fastapi import APIRouter, Depends

from . import outside
from .main import get_db, user

router = APIRouter()


@router.get("/api/outside")
def outside_connections(s=Depends(user), db=Depends(get_db)):
    return outside.view(db, s)
