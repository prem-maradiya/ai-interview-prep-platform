from fastapi import APIRouter

from app.api.deps import DB, CurrentUser
from app.services.practice import build_plan

router = APIRouter(prefix="/practice", tags=["practice"])


@router.get("/plan")
async def todays_plan(user: CurrentUser, db: DB):
    """Today's study plan: due spaced-repetition reviews, new problems targeting weak tags,
    a suggested interview for the weakest rubric area, and the current streak."""
    return await build_plan(db, user)
