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


def _encode(payload: dict, expires: timedelta) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    body = {**payload, "iat": now, "exp": now + expires}
    return jwt.encode(body, s.jwt_secret, algorithm=s.jwt_algorithm)


def create_token_pair(user_id: str, tenant_id: str, role: str) -> dict:
    s = get_settings()
    claims = {"sub": user_id, "tid": tenant_id, "role": role}
    return {
        "access_token": _encode({**claims, "type": "access"}, timedelta(minutes=s.access_token_minutes)),
        "refresh_token": _encode({**claims, "type": "refresh"}, timedelta(days=s.refresh_token_days)),
        "token_type": "bearer",
    }


def decode_token(token: str, expected_type: str) -> dict:
    s = get_settings()
    payload = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError("wrong token type")
    return payload
