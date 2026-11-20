"""Real-time rooms for peer interviews: fan messages out to every WebSocket in a room.

With REDIS_URL set, messages go through Redis pub/sub, so the two people in a room can be connected
to different API replicas; presence (who's online) is kept in a Redis hash. Without Redis, rooms
live in this process (fine for one API instance and for tests).
"""
import asyncio
import contextlib
import json
import logging
from collections import defaultdict

from app.core.config import get_settings

log = logging.getLogger("prepforge.rooms")
CHANNEL = "prepforge:peer:"


class Connection:
    """One WebSocket. `send` never raises: a dead socket is just skipped (its handler cleans up)."""

    def __init__(self, websocket, user_id: str) -> None:
        self.websocket = websocket
        self.user_id = user_id

    async def send(self, message: dict) -> None:
        with contextlib.suppress(Exception):
            await self.websocket.send_text(json.dumps(message, default=str))


class LocalHub:
    def __init__(self) -> None:
        self.rooms: dict[str, set[Connection]] = defaultdict(set)
        self._online: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    async def join(self, room: str, conn: Connection) -> None:
        self.rooms[room].add(conn)
        await self._presence_add(room, conn.user_id, 1)
        await self.publish(room, {"type": "presence", "online": await self.online(room)})

    async def leave(self, room: str, conn: Connection) -> None:
        self.rooms[room].discard(conn)
        if not self.rooms[room]:
            self.rooms.pop(room, None)
        await self._presence_add(room, conn.user_id, -1)
        await self.publish(room, {"type": "presence", "online": await self.online(room)})

    async def publish(self, room: str, message: dict) -> None:
        await self._deliver(room, message)

    async def _deliver(self, room: str, message: dict) -> None:
        conns = list(self.rooms.get(room, ()))
        if conns:
            await asyncio.gather(*(c.send(message) for c in conns))

    async def _presence_add(self, room: str, user_id: str, delta: int) -> None:
        counts = self._online[room]
        counts[user_id] += delta
        if counts[user_id] <= 0:
            counts.pop(user_id, None)
        if not counts:
            self._online.pop(room, None)

    async def online(self, room: str) -> list[str]:
        return sorted(self._online.get(room, {}))


class RedisHub(LocalHub):
    def __init__(self, url: str) -> None:
        super().__init__()
        import redis.asyncio as redis

        self.redis = redis.from_url(url, decode_responses=True)
        self._listener: asyncio.Task | None = None

    async def _ensure_listener(self) -> None:
        if self._listener is None or self._listener.done():
            pubsub = self.redis.pubsub()
            await pubsub.psubscribe(CHANNEL + "*")
            self._listener = asyncio.create_task(self._listen(pubsub))

    async def _listen(self, pubsub) -> None:
        while True:
            try:
                async for item in pubsub.listen():
                    if item.get("type") == "pmessage":
                        room = item["channel"][len(CHANNEL):]
                        if room in self.rooms:
                            await self._deliver(room, json.loads(item["data"]))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001  (Redis restarted: resubscribe and carry on)
                log.warning("room listener lost Redis; reconnecting", exc_info=True)
                await asyncio.sleep(1)
                with contextlib.suppress(Exception):
                    await pubsub.psubscribe(CHANNEL + "*")

    async def join(self, room: str, conn: Connection) -> None:
        await self._ensure_listener()
        await super().join(room, conn)

    async def publish(self, room: str, message: dict) -> None:
        try:
            await self.redis.publish(CHANNEL + room, json.dumps(message, default=str))
        except Exception:  # noqa: BLE001  (Redis down: at least reach people on this instance)
            log.warning("could not publish to Redis; delivering locally", exc_info=True)
            await self._deliver(room, message)

    async def _presence_add(self, room: str, user_id: str, delta: int) -> None:
        key = f"{CHANNEL}online:{room}"
        try:
            n = await self.redis.hincrby(key, user_id, delta)
            if n <= 0:
                await self.redis.hdel(key, user_id)
            await self.redis.expire(key, 6 * 3600)  # self-cleaning if an instance dies mid-session
        except Exception:  # noqa: BLE001
            await super()._presence_add(room, user_id, delta)

    async def online(self, room: str) -> list[str]:
        try:
            return sorted(await self.redis.hkeys(f"{CHANNEL}online:{room}"))
        except Exception:  # noqa: BLE001
            return await super().online(room)


_hub: LocalHub | None = None


def get_hub() -> LocalHub:
    global _hub
    if _hub is None:
        url = get_settings().redis_url
        _hub = RedisHub(url) if url else LocalHub()
    return _hub
