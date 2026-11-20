"""Email (notifications, invites, password reset, digest, preferences) and PostgreSQL row-level security.

The RLS tests need PostgreSQL: they run when TEST_DATABASE_URL is set (CI does) and skip on SQLite.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from app.db.models import Notification, Problem, Submission, Tenant, TestCase, User
from app.db.session import SessionLocal, configure_rls, engine, system_session, tenant_session
from app.services import email
from app.services.email_jobs import send_digests
from app.services.notifications import generate_all_due_soon
from tests.test_phase3 import REVERSE_OK, due, setup_org

pg_only = pytest.mark.skipif(engine.dialect.name != "postgresql", reason="row-level security needs PostgreSQL")


async def outbox() -> list[email.Email]:
    await email.wait_for_outgoing()
    return list(email.sent_messages)


def H(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------- notification emails & preferences ----------
async def test_assignment_email_respects_preferences(client, reg):
    owner, (alice, bob), probs = await setup_org(client, reg)
    prefs = (await client.get("/api/notifications/email-preferences", headers=bob["h"])).json()
    assert prefs["email"] == "cand1@acme.io" and prefs["delivery_enabled"]
    assert {o["kind"]: o["enabled"] for o in prefs["options"]} == {
        "assignment": True, "due_soon": True, "comment": True, "badge": False, "digest": True}
    r = await client.put("/api/notifications/email-preferences", headers=bob["h"], json={"assignment": False})
    assert {o["kind"]: o["enabled"] for o in r.json()["options"]}["assignment"] is False

    await client.post("/api/assignments", headers=owner, json={
        "title": "Graphs week", "due_at": due(5), "problem_ids": [probs["Two Sum"]]})
    sent = await outbox()
    assert [m.to for m in sent] == ["cand0@acme.io"]  # Bob opted out; the author isn't emailed
    m = sent[0]
    assert m.subject == "New assignment: Graphs week" and "/assignments/" in m.text
    assert "List-Unsubscribe" in m.headers and "Graphs week" in m.html
    assert "<script" not in m.html


async def test_email_html_is_escaped(client, reg):
    owner, (alice, _), probs = await setup_org(client, reg)
    await client.post("/api/assignments", headers=owner, json={
        "title": "<img src=x onerror=alert(1)>", "due_at": due(5), "problem_ids": [probs["Two Sum"]]})
    m = (await outbox())[0]
    assert "<img src=x" not in m.html and "&lt;img src=x" in m.html


async def test_badges_are_not_emailed_by_default(client, reg):
    _, (alice, _), probs = await setup_org(client, reg)
    await client.post(f"/api/problems/{probs['Reverse a String']}/submit", headers=alice["h"],
                      json={"language": "python", "code": REVERSE_OK})
    kinds = [i["kind"] for i in (await client.get("/api/notifications", headers=alice["h"])).json()["items"]]
    assert "badge" in kinds  # in-app yes...
    assert not [m for m in await outbox() if "Badge" in m.subject]  # ...email no


async def test_unsubscribe_link_turns_everything_off(client, reg):
    owner, (alice, _), probs = await setup_org(client, reg)
    await client.post("/api/assignments", headers=owner, json={
        "title": "W1", "due_at": due(5), "problem_ids": [probs["Two Sum"]]})
    m = (await outbox())[0]
    token = m.headers["List-Unsubscribe"].split("token=")[1].rstrip(">")
    assert (await client.post("/api/notifications/unsubscribe", json={"token": "nope"})).status_code == 400
    r = await client.post(f"/api/notifications/unsubscribe?token={token}")  # RFC 8058 one-click form
    assert r.status_code == 200 and r.json()["email"] == "cand0@acme.io"
    prefs = (await client.get("/api/notifications/email-preferences", headers=alice["h"])).json()
    assert not any(o["enabled"] for o in prefs["options"])


async def test_rolled_back_transactions_send_nothing():
    async with system_session() as db:
        email.queue_email(db, email.render(to="x@y.io", subject="s", heading="h", paragraphs=["p"]))
        await db.rollback()
        await db.commit()
    assert await outbox() == []


# ---------- invites ----------
async def test_invite_with_email_is_emailed(client, reg):
    owner = await reg(client, "acme", "owner@acme.io")
    inv = (await client.post("/api/org/invites", headers=owner, json={"email": "new@acme.io", "role": "mentor"})).json()
    await client.post("/api/org/invites", headers=owner, json={"role": "candidate"})  # no email: nothing sent
    sent = await outbox()
    assert len(sent) == 1 and sent[0].to == "new@acme.io"
    assert f"/?invite={inv['code']}" in sent[0].text and "mentor" in sent[0].text


# ---------- password reset ----------
async def test_password_reset_flow(client, reg):
    await reg(client, "acme", "owner@acme.io")
    login = {"org_slug": "acme", "email": "owner@acme.io"}
    old_refresh = (await client.post("/api/auth/login", json={**login, "password": "password123"})).json()["refresh_token"]

    r = await client.post("/api/auth/password-reset/request", json={"org_slug": "acme", "email": "ghost@acme.io"})
    assert r.status_code == 202 and await outbox() == []  # same answer, no email, for unknown accounts
    r = await client.post("/api/auth/password-reset/request", json=login)
    assert r.status_code == 202
    (m,) = await outbox()
    token = m.text.split("reset-password?token=")[1].split()[0]

    bad = await client.post("/api/auth/password-reset/confirm", json={"token": token[:-3] + "abc", "password": "newpass123"})
    assert bad.status_code == 400
    assert (await client.post("/api/auth/password-reset/confirm", json={"token": token, "password": "newpass123"})).status_code == 204
    # Single use: the link dies once the password changes.
    assert (await client.post("/api/auth/password-reset/confirm", json={"token": token, "password": "another123"})).status_code == 400
    assert (await client.post("/api/auth/login", json={**login, "password": "password123"})).status_code == 401
    assert (await client.post("/api/auth/login", json={**login, "password": "newpass123"})).status_code == 200
    # Existing sessions were signed out.
    assert (await client.post("/api/auth/refresh", json={"refresh_token": old_refresh})).status_code == 401


async def test_password_reset_is_rate_limited(client, reg):
    await reg(client, "acme", "owner@acme.io")
    codes = [(await client.post("/api/auth/password-reset/request",
                                json={"org_slug": "acme", "email": "owner@acme.io"})).status_code for _ in range(6)]
    assert codes[:5] == [202] * 5 and codes[5] == 429


# ---------- background jobs ----------
async def test_due_soon_job_emails_each_assignee_once(client, reg):
    owner, (alice, bob), probs = await setup_org(client, reg)
    await client.post("/api/assignments", headers=owner, json={
        "title": "Due tomorrow", "due_at": due(0.5), "problem_ids": [probs["Reverse a String"]]})
    await client.post(f"/api/problems/{probs['Reverse a String']}/submit", headers=bob["h"],
                      json={"language": "python", "code": REVERSE_OK})
    await outbox()
    email.sent_messages.clear()
    for _ in range(2):  # a second run (or a second API replica) must not send it again
        async with system_session() as db:
            await generate_all_due_soon(db)
            await db.commit()
    sent = await outbox()
    assert [m.to for m in sent] == ["cand0@acme.io"]  # Bob already finished
    assert sent[0].subject == "Due within 24 hours: Due tomorrow"
    # Opening the app afterwards doesn't create a second reminder either.
    kinds = [i["kind"] for i in (await client.get("/api/notifications", headers=alice["h"])).json()["items"]]
    assert kinds.count("due_soon") == 1


async def test_weekly_digest_once_per_week_and_skips_empty_weeks(client, reg):
    owner, (alice, bob), probs = await setup_org(client, reg)
    await client.post(f"/api/problems/{probs['Reverse a String']}/submit", headers=alice["h"],
                      json={"language": "python", "code": REVERSE_OK})
    await outbox()
    email.sent_messages.clear()
    async with system_session() as db:
        too_new = await send_digests(db)
        assert too_new == 0  # nobody has been here a week yet
        await db.execute(update(User).values(created_at=datetime.now(timezone.utc) - timedelta(days=8)))
        await db.commit()
        assert await send_digests(db) == 1
        assert await send_digests(db) == 0  # already sent this week
    sent = await outbox()
    assert [m.to for m in sent] == ["cand0@acme.io"]  # Bob and the owner had an empty week: no email
    assert "Problems solved: 1" in sent[0].text and "streak: 1 day" in sent[0].text


# ---------- row-level security ----------
async def _two_orgs(client, reg):
    a_owner, (alice, _), probs = await setup_org(client, reg, "aaa", ("Alice", "Ann"))
    b_owner, (bert, _), _ = await setup_org(client, reg, "bbb", ("Bert", "Bea"))
    for h in (alice["h"], bert["h"]):
        await client.post(f"/api/problems/{probs['Reverse a String']}/submit", headers=h,
                          json={"language": "python", "code": REVERSE_OK})
    private = (await client.post("/api/problems", headers=b_owner, json={
        "title": "B secret", "description": "d", "difficulty": "easy", "tags": [],
        "tests": [{"input": "1", "expected_output": "1", "is_hidden": True}]})).json()
    async with system_session() as db:
        ids = {t.slug: t.id for t in (await db.scalars(select(Tenant))).all()}
    return ids, private


@pg_only
async def test_rls_is_enforced():
    assert (await configure_rls()).startswith("enforced")


@pg_only
async def test_rls_hides_other_tenants_even_without_filters(client, reg):
    ids, private = await _two_orgs(client, reg)
    async with tenant_session(ids["aaa"]) as db:
        # Deliberately unfiltered queries: the database itself scopes them.
        assert {u.email.split("@")[1] for u in (await db.scalars(select(User))).all()} == {"aaa.io"}
        assert [t.slug for t in (await db.scalars(select(Tenant))).all()] == ["aaa"]
        assert {s.tenant_id for s in (await db.scalars(select(Submission))).all()} == {ids["aaa"]}
        titles = {p.title for p in (await db.scalars(select(Problem))).all()}
        assert "Two Sum" in titles and "B secret" not in titles  # global problems yes, B's private one no
        assert await db.get(Problem, private["id"]) is None
        hidden = (await db.scalars(select(TestCase).where(TestCase.problem_id == private["id"]))).all()
        assert hidden == []  # child rows follow their parent
    async with system_session() as db:
        assert len((await db.scalars(select(Tenant))).all()) == 2


@pg_only
async def test_rls_without_context_sees_nothing(client, reg):
    await _two_orgs(client, reg)
    async with SessionLocal() as db:  # neither tenant nor system: fails closed
        assert (await db.scalars(select(User))).all() == []


@pg_only
async def test_rls_blocks_cross_tenant_writes(client, reg):
    ids, private = await _two_orgs(client, reg)
    async with tenant_session(ids["aaa"]) as db:
        a_user = (await db.scalars(select(User))).first()
        db.add(Notification(tenant_id=ids["bbb"], user_id=a_user.id, kind="x", title="planted"))
        with pytest.raises(DBAPIError, match="row-level security"):
            await db.flush()
    async with tenant_session(ids["aaa"]) as db:
        # Shared rows are read-only, and other tenants' rows are invisible to UPDATE/DELETE.
        changed = await db.execute(update(Problem).where(Problem.tenant_id.is_(None)).values(title="hacked"))
        assert changed.rowcount == 0
        changed = await db.execute(update(Problem).where(Problem.id == private["id"]).values(title="hacked"))
        assert changed.rowcount == 0
        await db.commit()
    async with system_session() as db:
        assert (await db.scalar(text("SELECT count(*) FROM problems WHERE title = 'hacked'"))) == 0


@pg_only
async def test_rls_context_does_not_leak_between_pooled_connections(client, reg):
    ids, _ = await _two_orgs(client, reg)
    for _ in range(3):
        async with tenant_session(ids["aaa"]) as db:
            assert (await db.scalars(select(User))).all()
        async with SessionLocal() as db:
            assert (await db.scalars(select(User))).all() == []
            assert await db.scalar(text("SELECT current_user")) == "prepforge_app"
