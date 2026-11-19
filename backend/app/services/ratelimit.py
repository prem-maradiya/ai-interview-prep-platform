"""Fixed-window rate limiting. Redis-backed when REDIS_URL is set (shared across API replicas),
otherwise an in-process dict (fine for a single dev process and for tests)."""
import time

from fastapi import HTTPException, Request, status

from app.core.config import get_settings


class RateLimiter:
    async def hit(self, key: str, limit: int, window_s: int) -> int | None:
        """Count one request. Returns seconds to wait if the limit is exceeded, else None."""
        raise NotImplementedError


class MemoryRateLimiter(RateLimiter):
    def __init__(self) -> None:
        self._windows: dict[str, tuple[float, int]] = {}

    async def hit(self, key: str, limit: int, window_s: int) -> int | None:
        now = time.monotonic()
        start, count = self._windows.get(key, (now, 0))
        if now - start >= window_s:
            start, count = now, 0
        count += 1
        self._windows[key] = (start, count)
        return max(1, int(window_s - (now - start))) if count > limit else None

    def reset(self) -> None:
        self._windows.clear()


class RedisRateLimiter(RateLimiter):
    def __init__(self, url: str) -> None:
        import redis.asyncio as redis

        self._redis = redis.from_url(url)

    async def hit(self, key: str, limit: int, window_s: int) -> int | None:
        k = f"rl:{key}"
        async with self._redis.pipeline(transaction=True) as pipe:
            count, _, ttl = await pipe.incr(k).expire(k, window_s, nx=True).ttl(k).execute()
        return max(1, ttl) if count > limit else None


_limiter: RateLimiter | None = None


def get_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        url = get_settings().redis_url
        _limiter = RedisRateLimiter(url) if url else MemoryRateLimiter()
    return _limiter


def client_ip(request: Request) -> str:
    # Uvicorn runs with --proxy-headers behind nginx, which overwrites X-Forwarded-For with the real peer.
    return request.client.host if request.client else "unknown"


async def enforce(request: Request, name: str, rule: tuple[int, int], *parts: str) -> None:
    limit, window = rule
    key = ":".join([name, client_ip(request), *[p.lower() for p in parts]])
    retry_after = await get_limiter().hit(key, limit, window)
    if retry_after is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts, please try again later",
            headers={"Retry-After": str(retry_after)},
        )
