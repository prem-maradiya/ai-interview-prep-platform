import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import get_settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


# Used to keep login timing constant when the account doesn't exist (avoids user enumeration).
DUMMY_HASH = hash_password("timing-equalizer-not-a-real-password")


def _encode(payload: dict, expires: timedelta) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    body = {**payload, "iat": now, "exp": now + expires}
    return jwt.encode(body, s.jwt_secret, algorithm=s.jwt_algorithm)


def create_access_token(user_id: str, tenant_id: str, role: str) -> str:
    claims = {"sub": user_id, "tid": tenant_id, "role": role, "type": "access"}
    return _encode(claims, timedelta(minutes=get_settings().access_token_minutes))


def create_refresh_token(user_id: str, tenant_id: str) -> tuple[str, str, datetime]:
    """Returns (token, jti, expires_at). The jti is stored server-side so the token can be revoked."""
    jti = str(uuid.uuid4())
    ttl = timedelta(days=get_settings().refresh_token_days)
    token = _encode({"sub": user_id, "tid": tenant_id, "type": "refresh", "jti": jti}, ttl)
    return token, jti, datetime.now(timezone.utc) + ttl


def decode_token(token: str, expected_type: str) -> dict:
    s = get_settings()
    payload = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("wrong token type")
    return payload
