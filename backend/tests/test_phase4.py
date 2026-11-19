"""Phase 4: notifications, badges/XP, problem lists, company tags, more languages, seed upgrades."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db.models import Problem
from app.db.session import SessionLocal
from app.seed import seed_global_problems
from app.services.achievements import level_for
from tests.test_phase3 import REVERSE_OK, finish_interview, setup_org

pytestmark = pytest.mark.asyncio


def due(hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


async def notes(client, h):
    return (await client.get("/api/notifications", headers=h)).json()


# ---------- notifications ----------
async def test_assignment_and_comment_notifications(client, reg):
    owner, (alice, bob), probs = await setup_org(client, reg)
    a = (await client.post("/api/assignments", headers=owner, json={
        "title": "Week 2", "due_at": due(72), "problem_ids": [probs["Two Sum"]]})).json()
    n = await notes(client, alice["h"])
    assert n["unread_count"] == 1 and n["items"][0]["kind"] == "assignment"
    assert n["items"][0]["link"] == f"/assignments/{a['id']}" and "Week 2" in n["items"][0]["title"]
    assert (await notes(client, owner))["unread_count"] == 0  # the author isn't notified

    iv_id = await finish_interview(client, alice["h"])
    target = {"target_type": "interview", "target_id": iv_id}
    await client.post("/api/comments", headers=owner, json={**target, "body": "Nice STAR structure."})
    items = (await notes(client, alice["h"]))["items"]
    assert items[0]["kind"] == "comment" and items[0]["body"] == "Nice STAR structure." and items[0]["link"] == f"/interviews/{iv_id}"
    # The candidate's reply notifies the mentor who commented, not the candidate.
    await client.post("/api/comments", headers=alice["h"], json={**target, "body": "Thank you!"})
    mentor_items = (await notes(client, owner))["items"]
    assert mentor_items[0]["kind"] == "comment" and "replied" in mentor_items[0]["title"]
    assert (await notes(client, bob["h"]))["unread_count"] == 1  # only the assignment

    # Deleting an assignment removes its notifications (no dead links).
    tmp = (await client.post("/api/assignments", headers=owner, json={
        "title": "Temporary", "due_at": due(10), "problem_ids": [probs["FizzBuzz"]]})).json()
    assert any(i["link"] == f"/assignments/{tmp['id']}" for i in (await notes(client, bob["h"]))["items"])
    await client.delete(f"/api/assignments/{tmp['id']}", headers=owner)
    assert not any(i["link"] == f"/assignments/{tmp['id']}" for i in (await notes(client, bob["h"]))["items"])

    first = (await notes(client, alice["h"]))["items"][0]
    assert (await client.post(f"/api/notifications/{first['id']}/read", headers=alice["h"])).status_code == 204
    assert (await client.post(f"/api/notifications/{first['id']}/read", headers=bob["h"])).status_code == 404
    await client.post("/api/notifications/read-all", headers=alice["h"])
    assert (await notes(client, alice["h"]))["unread_count"] == 0


async def test_due_soon_reminder_once_and_not_when_done(client, reg):
    owner, (alice, bob), probs = await setup_org(client, reg)
    await client.post("/api/assignments", headers=owner, json={
        "title": "Due tomorrow", "due_at": due(12), "problem_ids": [probs["Reverse a String"]]})
    await client.post(f"/api/problems/{probs['Reverse a String']}/submit", headers=bob["h"],
                      json={"language": "python", "code": REVERSE_OK})  # Bob finished it
    for _ in range(2):  # checking twice must not duplicate the reminder
        alice_kinds = [i["kind"] for i in (await notes(client, alice["h"]))["items"]]
    assert alice_kinds.count("due_soon") == 1
    assert "due_soon" not in [i["kind"] for i in (await notes(client, bob["h"]))["items"]]


# ---------- badges & XP ----------
async def test_levels():
    assert level_for(0) == {"level": 1, "xp": 0, "current_level_xp": 0, "next_level_xp": 50}
    assert level_for(149)["level"] == 2 and level_for(150)["level"] == 3
    assert level_for(10_000)["next_level_xp"] is None


async def test_badges_and_xp(client, reg):
    owner, (alice, _), probs = await setup_org(client, reg)
    start = (await client.get("/api/achievements", headers=alice["h"])).json()
    assert start["xp"] == 0 and start["level"] == 1 and not any(b["earned"] for b in start["badges"])

    await client.post(f"/api/problems/{probs['Reverse a String']}/submit", headers=alice["h"],
                      json={"language": "python", "code": REVERSE_OK})
    # Re-solving the same problem doesn't add XP.
    await client.post(f"/api/problems/{probs['Reverse a String']}/submit", headers=alice["h"],
                      json={"language": "python", "code": REVERSE_OK})
    ach = (await client.get("/api/achievements", headers=alice["h"])).json()
    earned = {b["key"] for b in ach["badges"] if b["earned"]}
    assert ach["xp"] == 10 and earned == {"first_solve"} and ach["streak_days"] == 1
    badge_notes = [i for i in (await notes(client, alice["h"]))["items"] if i["kind"] == "badge"]
    assert len(badge_notes) == 1 and "First blood" in badge_notes[0]["title"]

    await finish_interview(client, alice["h"])
    ach = (await client.get("/api/achievements", headers=alice["h"])).json()
    assert "first_interview" in {b["key"] for b in ach["badges"] if b["earned"]} and ach["xp"] > 30
    # The test sandbox runs Python for any language label, which lets us exercise the polyglot rule.
    for lang in ("javascript", "java"):
        await client.post(f"/api/problems/{probs['Reverse a String']}/submit", headers=alice["h"],
                          json={"language": lang, "code": REVERSE_OK})
    ach = (await client.get("/api/achievements", headers=alice["h"])).json()
    assert "polyglot" in {b["key"] for b in ach["badges"] if b["earned"]}

    board = (await client.get("/api/analytics/org", headers=owner)).json()["leaderboard"]
    assert board[0]["full_name"] == "Alice" and board[0]["xp"] == ach["xp"] and board[0]["level"] >= 1


# ---------- problem lists & company tags ----------
async def test_global_and_org_lists(client, reg):
    owner, (alice, _), probs = await setup_org(client, reg)
    lists = (await client.get("/api/lists", headers=alice["h"])).json()
    assert {l["slug"] for l in lists} == {"interview-warmup", "arrays-hashing", "big-tech-frequent"}
    warm = next(l for l in lists if l["slug"] == "interview-warmup")
    assert warm["total"] == 6 and warm["solved"] == 0

    await client.post(f"/api/problems/{probs['Reverse a String']}/submit", headers=alice["h"],
                      json={"language": "python", "code": REVERSE_OK})
    detail = (await client.get(f"/api/lists/{warm['id']}", headers=alice["h"])).json()
    assert detail["solved"] == 1 and next(p for p in detail["problems"] if p["title"] == "Reverse a String")["solved"]

    mine = (await client.post("/api/lists", headers=owner, json={
        "title": "Week 3 drills", "problem_ids": [probs["Two Sum"], probs["Binary Search"]]})).json()
    assert mine["total"] == 2 and not mine["is_global"]
    assert (await client.post("/api/lists", headers=alice["h"], json={"title": "Mine", "problem_ids": [probs["Two Sum"]]})).status_code == 403
    other_owner = await reg(client, "beta", "owner@beta.io")
    assert (await client.get(f"/api/lists/{mine['id']}", headers=other_owner)).status_code == 404
    assert (await client.delete(f"/api/lists/{warm['id']}", headers=owner)).status_code == 404  # global: read-only
    assert (await client.delete(f"/api/lists/{mine['id']}", headers=owner)).status_code == 204


async def test_company_filter_and_new_language_starters(client, reg):
    h = await reg(client)
    google = {p["title"] for p in (await client.get("/api/problems", params={"company": "google"}, headers=h)).json()}
    assert {"Two Sum", "Trapping Rain Water", "Edit Distance"} <= google and "FizzBuzz" not in google
    two = next(p for p in (await client.get("/api/problems", headers=h)).json() if p["title"] == "Two Sum")
    detail = (await client.get(f"/api/problems/{two['id']}", headers=h)).json()
    assert set(detail["starter_code"]) == {"python", "javascript", "java", "cpp", "go"}
    assert "Amazon" in detail["companies"]
    bad = await client.post(f"/api/problems/{two['id']}/run", headers=h, json={"language": "cobol", "code": "x"})
    assert bad.status_code == 422


async def test_seed_upgrade_backfills_existing_databases(client, reg):
    """An install seeded by an older version gets new problems, lists and starter code on startup."""
    async with SessionLocal() as db:
        p = await db.scalar(select(Problem).where(Problem.title == "Two Sum"))
        p.starter_code = {"python": "print()"}
        p.companies = []
        await db.delete(await db.scalar(select(Problem).where(Problem.title == "Edit Distance")))
        await db.commit()
    async with SessionLocal() as db:
        await seed_global_problems(db)
        await seed_global_problems(db)  # idempotent
    async with SessionLocal() as db:
        p = await db.scalar(select(Problem).where(Problem.title == "Two Sum"))
        assert p.starter_code["python"] == "print()"  # existing code kept
        assert {"java", "cpp", "go"} <= set(p.starter_code) and p.companies
        assert len((await db.scalars(select(Problem).where(Problem.title == "Edit Distance"))).all()) == 1
