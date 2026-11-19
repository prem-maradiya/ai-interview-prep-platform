from collections.abc import Callable
from typing import Annotated, TypeVar

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token
from app.db.models import Role, User
from app.db.session import get_db

bearer = HTTPBearer(auto_error=False)

DB = Annotated[AsyncSession, Depends(get_db)]

T = TypeVar("T")


async def get_current_user(
    db: DB, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
) -> User:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", {"WWW-Authenticate": "Bearer"})
    if creds is None:
        raise unauthorized
    try:
        payload = decode_token(creds.credentials, "access")
    except jwt.PyJWTError:
        raise unauthorized
    user = await db.get(User, payload["sub"])
    # The tenant in the token must still match the user's tenant, and the account must be active.
    if user is None or not user.is_active or user.tenant_id != payload.get("tid"):
        raise unauthorized
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]

ROLE_RANK = {Role.candidate: 0, Role.mentor: 1, Role.admin: 2, Role.owner: 3}


def require_role(minimum: Role) -> Callable:
    async def checker(user: CurrentUser) -> User:
        if ROLE_RANK[Role(user.role)] < ROLE_RANK[minimum]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires {minimum} role or higher")
        return user

    return checker


MentorUser = Annotated[User, Depends(require_role(Role.mentor))]
AdminUser = Annotated[User, Depends(require_role(Role.admin))]


async def get_scoped(db: AsyncSession, model: type[T], obj_id: str, tenant_id: str) -> T:
    """Fetch a tenant-owned row by id; 404 (not 403) when it belongs to another tenant."""
    obj = await db.scalar(select(model).where(model.id == obj_id, model.tenant_id == tenant_id))  # type: ignore[attr-defined]
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{model.__name__} not found")
    return obj
