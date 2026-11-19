"""Phase 1: token rotation/revocation, rate limits, SSE streaming, sandbox queue worker, migrations."""
import json
import os
import tempfile

import pytest

from app.core.config import Settings, get_settings

LOGIN = {"org_slug": "acme", "email": "owner@acme.io", "password": "password123"}


async def _login(client):
    r = await client.post("/api/auth/login", json=LOGIN)
    assert r.status_code == 200, r.text
    return r.json()


# ---------- refresh token rotation & revocation ----------
@pytest.mark.asyncio
async def test_refresh_rotates_and_reuse_revokes_family(client, reg):
    await reg(client)
    t1 = await _login(client)
    r = await client.post("/api/auth/refresh", json={"refresh_token": t1["refresh_token"]})
    assert r.status_code == 200
    t2 = r.json()
    assert t2["refresh_token"] != t1["refresh_token"]
    # Replaying the old token = theft signal: rejected, and the new token dies with it.
    replay = await client.post("/api/auth/refresh", json={"refresh_token": t1["refresh_token"]})
    assert replay.status_code == 401 and "reuse" in replay.json()["detail"]
    assert (await client.post("/api/auth/refresh", json={"refresh_token": t2["refresh_token"]})).status_code == 401
    # A separate login session (different family) is unaffected.
    t3 = await _login(client)
    assert (await client.post("/api/auth/refresh", json={"refresh_token": t3["refresh_token"]})).status_code == 200


@pytest.mark.asyncio
async def test_logout_revokes_refresh_token(client, reg):
    await reg(client)
    t = await _login(client)
    assert (await client.post("/api/auth/logout", json={"refresh_token": t["refresh_token"]})).status_code == 204
    assert (await client.post("/api/auth/refresh", json={"refresh_token": t["refresh_token"]})).status_code == 401
    # Garbage tokens don't error.
    assert (await client.post("/api/auth/logout", json={"refresh_token": "nope"})).status_code == 204


@pytest.mark.asyncio
async def test_deactivating_member_revokes_their_sessions(client, reg):
    owner = await reg(client)
    inv = (await client.post("/api/org/invites", json={"role": "candidate"}, headers=owner)).json()
    cand = (await client.post("/api/auth/accept-invite", json={
        "code": inv["code"], "full_name": "C", "email": "c@acme.io", "password": "password123"})).json()
    me = (await client.get("/api/auth/me", headers={"Authorization": f"Bearer {cand['access_token']}"})).json()
    assert (await client.patch(f"/api/org/members/{me['id']}", json={"is_active": False}, headers=owner)).status_code == 200
    assert (await client.post("/api/auth/refresh", json={"refresh_token": cand["refresh_token"]})).status_code == 401
    assert (await client.get("/api/auth/me", headers={"Authorization": f"Bearer {cand['access_token']}"})).status_code == 401


# ---------- rate limiting ----------
@pytest.mark.asyncio
async def test_login_rate_limited(client, reg, monkeypatch):
    await reg(client)
    monkeypatch.setattr(get_settings(), "rl_login", (3, 300))
    bad = {**LOGIN, "password": "wrong-password"}
    codes = [(await client.post("/api/auth/login", json=bad)).status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]
    blocked = await client.post("/api/auth/login", json=LOGIN)
    assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) > 0
    # Limits are per account: another account from the same IP is not blocked.
    assert (await client.post("/api/auth/login", json={**LOGIN, "email": "other@acme.io"})).status_code == 401


# ---------- SSE streaming ----------
def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        events.append((lines["event"], json.loads(lines["data"])))
    return events


@pytest.mark.asyncio
async def test_interview_reply_streams_over_sse(client, reg):
    h = await reg(client)
    iv = (await client.post("/api/interviews", json={"kind": "behavioral"}, headers=h)).json()
    r = await client.post(f"/api/interviews/{iv['id']}/messages/stream", json={"content": "My answer"}, headers=h)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(r.text)
    kinds = [e for e, _ in events]
    assert kinds[0] == "candidate" and kinds[-1] == "done" and kinds.count("delta") > 3
    streamed = "".join(d["text"] for e, d in events if e == "delta").strip()
    done = events[-1][1]
    assert done["content"] == streamed and done["sender"] == "interviewer" and done["seq"] == 2
    # Both messages were persisted.
    saved = (await client.get(f"/api/interviews/{iv['id']}", headers=h)).json()["messages"]
    assert [m["sender"] for m in saved] == ["interviewer", "candidate", "interviewer"]


@pytest.mark.asyncio
async def test_stream_rejected_for_completed_interview(client, reg):
    h = await reg(client)
    iv = (await client.post("/api/interviews", json={"kind": "behavioral"}, headers=h)).json()
    await client.post(f"/api/interviews/{iv['id']}/messages", json={"content": "answer"}, headers=h)
    await client.post(f"/api/interviews/{iv['id']}/end", headers=h)
    r = await client.post(f"/api/interviews/{iv['id']}/messages/stream", json={"content": "more"}, headers=h)
    assert r.status_code == 409


# ---------- sandbox queue worker ----------
@pytest.mark.asyncio
async def test_worker_job_serializes_results():
    from tests.conftest import LocalPythonSandbox

    from app.worker import run_code

    out = await run_code({"sandbox": LocalPythonSandbox()}, "python", "print(input()[::-1])", ["abc"])
    assert out[0]["status"] == "ok" and out[0]["stdout"].strip() == "cba" and set(out[0]) == {"status", "stdout", "stderr", "time_ms"}


@pytest.mark.asyncio
async def test_queue_sandbox_reports_unavailable_redis():
    from app.services.sandbox import QueueSandbox, SandboxError

    sb = QueueSandbox("redis://127.0.0.1:1")  # nothing listening
    with pytest.raises(SandboxError):
        await sb.execute("python", "print(1)", [""])
    with pytest.raises(SandboxError, match="Unsupported"):
        await sb.execute("cobol", "", [""])


# ---------- config & migrations ----------
def test_production_refuses_insecure_config():
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        Settings(environment="production").validate_for_production()
    Settings(environment="production", jwt_secret="x" * 48, sandbox_backend="queue",
             redis_url="redis://r:6379").validate_for_production()


def test_migrations_match_models():
    """Fresh DB -> `alembic upgrade head` -> schema has no drift from the ORM models."""
    from alembic import command
    from alembic.config import Config

    here = os.path.dirname(os.path.dirname(__file__))
    db = os.path.join(tempfile.mkdtemp(), "mig.db")
    cfg = Config(os.path.join(here, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(here, "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{db}")
    command.upgrade(cfg, "head")
    command.check(cfg)  # raises if models changed without a migration
    command.downgrade(cfg, "base")
