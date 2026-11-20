"""Operations (health, metrics, request IDs, logs), discussions & editorials, contests and whiteboards."""
import json
import logging
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from app.core.observability import JsonFormatter, request_id_var
from app.db.models import Submission
from app.db.session import system_session
from app.services import whiteboard
from tests.test_phase3 import REVERSE_OK, setup_org
from tests.test_phase7 import Socket, _token

WRONG = "print('nope')"


def at(minutes: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


# ---------- operations ----------
async def test_health_readiness_and_request_ids(client):
    assert (await client.get("/api/health")).json()["status"] == "ok"
    r = await client.get("/api/ready")
    assert r.status_code == 200 and r.json()["checks"]["database"] == "ok"
    r = await client.get("/api/health", headers={"X-Request-ID": "trace-123"})
    assert r.headers["x-request-id"] == "trace-123"
    r = await client.get("/api/health", headers={"X-Request-ID": "bad id with spaces & <script>"})
    assert r.headers["x-request-id"] != "bad id with spaces & <script>" and len(r.headers["x-request-id"]) == 16


async def test_metrics_are_recorded_and_protected(client, reg, monkeypatch):
    h = await reg(client)
    pid = next(p["id"] for p in (await client.get("/api/problems", headers=h)).json() if p["title"] == "Reverse a String")
    await client.post(f"/api/problems/{pid}/submit", headers=h, json={"language": "python", "code": REVERSE_OK})
    text = (await client.get("/api/metrics")).text  # the test client counts as a private-network address
    assert 'prepforge_sandbox_runs_total{kind="stdio",language="python",verdict="accepted"}' in text
    assert 'route="/api/problems/{problem_id}/submit"' in text  # templated routes, not raw ids
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "metrics_token", "s3cret")
    assert (await client.get("/api/metrics")).status_code == 404
    assert (await client.get("/api/metrics", headers={"Authorization": "Bearer s3cret"})).status_code == 200


def test_json_logs_carry_the_request_id():
    token = request_id_var.set("abc123")
    try:
        record = logging.LogRecord("prepforge.test", logging.INFO, __file__, 1, "hello %s", ("world",), None)
        line = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert line["message"] == "hello world" and line["request_id"] == "abc123" and line["level"] == "info"


# ---------- discussions & editorials ----------
async def test_discussions_unlock_after_solving_or_revealing(client, reg):
    owner, (alice, bob), probs = await setup_org(client, reg)
    pid = probs["Reverse a String"]
    url = f"/api/problems/{pid}/discussions"
    locked = (await client.get(url, headers=alice["h"])).json()
    assert locked["unlocked"] is False and locked["reason"] == "unsolved" and locked["items"] == []
    assert (await client.post(url, headers=alice["h"], json={"body": "hi"})).status_code == 403

    await client.post(f"/api/problems/{pid}/submit", headers=alice["h"], json={"language": "python", "code": REVERSE_OK})
    post = (await client.post(url, headers=alice["h"], json={"body": "Slicing with `[::-1]` is O(n)."})).json()
    assert post["author"]["name"] == "Alice" and post["can_edit"]

    # Bob reveals instead of solving; replying notifies Alice.
    assert (await client.post(f"/api/problems/{pid}/reveal", headers=bob["h"])).json()["reason"] == "revealed"
    reply = (await client.post(url, headers=bob["h"], json={"body": "Also works with two pointers.", "parent_id": post["id"]})).json()
    nested = (await client.post(url, headers=alice["h"], json={"body": "Nice", "parent_id": reply["id"]})).json()
    assert nested["parent_id"] == post["id"]  # replies stay one level deep
    kinds = [n["kind"] for n in (await client.get("/api/notifications", headers=alice["h"])).json()["items"]]
    assert "discussion" in kinds

    assert (await client.post(f"/api/discussions/{post['id']}/vote", headers=alice["h"], json={"value": 1})).status_code == 400
    assert (await client.post(f"/api/discussions/{post['id']}/vote", headers=bob["h"], json={"value": 1})).json()["score"] == 1
    assert (await client.post(f"/api/discussions/{post['id']}/vote", headers=owner, json={"value": -1})).json()["score"] == 0
    assert (await client.post(f"/api/discussions/{post['id']}/vote", headers=bob["h"], json={"value": 0})).json()["score"] == -1

    assert (await client.patch(f"/api/discussions/{post['id']}", headers=bob["h"], json={"body": "x"})).status_code == 403
    edited = (await client.patch(f"/api/discussions/{post['id']}", headers=alice["h"], json={"body": "Edited"})).json()
    assert edited["edited"] and edited["body"] == "Edited"
    await client.delete(f"/api/discussions/{post['id']}", headers=owner)  # mentors moderate; it has replies
    thread = (await client.get(url, headers=bob["h"])).json()["items"]
    assert thread[0]["deleted"] and thread[0]["body"] == "[deleted]" and len(thread[0]["replies"]) == 2

    # Bob solving after revealing is flagged (as a low-severity hint) for mentors.
    sub = (await client.post(f"/api/problems/{pid}/submit", headers=bob["h"], json={"language": "python", "code": REVERSE_OK})).json()
    staff_view = (await client.get(f"/api/submissions/{sub['id']}", headers=owner)).json()
    assert any(f["type"] == "revealed_first" for f in staff_view["integrity_flags"])

    other = await reg(client, "other", "owner@other.io")  # other organizations never see the thread
    assert (await client.get(url, headers=other)).json()["count"] == 0


async def test_editorial_is_written_by_mentors_and_gated(client, reg):
    owner, (alice, _), probs = await setup_org(client, reg)
    pid = probs["Two Sum"]
    draft = (await client.post(f"/api/problems/{pid}/editorial/draft", headers=owner)).json()["body"]
    assert "## Optimal approach" in draft
    assert (await client.put(f"/api/problems/{pid}/editorial", headers=alice["h"], json={"body": "x"})).status_code == 403
    saved = (await client.put(f"/api/problems/{pid}/editorial", headers=owner, json={"body": "Use a hash map."})).json()
    assert saved["editorial"]["body"] == "Use a hash map." and saved["editorial"]["author"] == "Owner"
    locked = (await client.get(f"/api/problems/{pid}/editorial", headers=alice["h"])).json()
    assert locked["exists"] and locked["editorial"] is None
    await client.post(f"/api/problems/{pid}/reveal", headers=alice["h"])
    assert (await client.get(f"/api/problems/{pid}/editorial", headers=alice["h"])).json()["editorial"]["body"] == "Use a hash map."


# ---------- contests ----------
async def _contest(client, owner, probs, **kw):
    body = {"title": "Friday sprint", "problem_ids": [probs["Reverse a String"], probs["FizzBuzz"]],
            "starts_at": at(-1), "ends_at": at(60), **kw}
    r = await client.post("/api/contests", headers=owner, json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def test_contest_scoring_ranks_and_penalties(client, reg):
    owner, (alice, bob), probs = await setup_org(client, reg)
    bad = await client.post("/api/contests", headers=owner, json={
        "title": "x" * 3, "problem_ids": [probs["FizzBuzz"]], "starts_at": at(10), "ends_at": at(5)})
    assert bad.status_code == 400
    assert (await client.post("/api/contests", headers=alice["h"], json={})).status_code in (403, 422)
    c = await _contest(client, owner, probs)
    assert c["status"] == "running" and [p["label"] for p in c["problems"]] == ["A", "B"]
    assert "contest" in [n["kind"] for n in (await client.get("/api/notifications", headers=alice["h"])).json()["items"]]

    rev, fizz = probs["Reverse a String"], probs["FizzBuzz"]
    fizz_ok = "n=int(input())\nprint('\\n'.join('FizzBuzz' if i%15==0 else 'Fizz' if i%3==0 else 'Buzz' if i%5==0 else str(i) for i in range(1,n+1)))"
    sub = lambda h, pid, code: client.post(f"/api/problems/{pid}/submit", headers=h, json={"language": "python", "code": code})  # noqa: E731
    await sub(alice["h"], rev, WRONG)
    await sub(alice["h"], rev, "this is not python (")  # runtime/syntax error: counts as wrong
    await sub(alice["h"], rev, REVERSE_OK)
    await sub(alice["h"], rev, WRONG)  # after solving: ignored
    await sub(bob["h"], rev, REVERSE_OK)
    await sub(bob["h"], fizz, fizz_ok)
    await sub(owner, fizz, fizz_ok)  # staff are unofficial

    board = (await client.get(f"/api/contests/{c['id']}/scoreboard", headers=alice["h"])).json()
    rows = {r["name"]: r for r in board["rows"]}
    assert list(rows) == ["Bob", "Alice"] and "Owner" not in rows
    assert rows["Bob"]["solved"] == 2 and rows["Bob"]["rank"] == 1
    a = rows["Alice"]["problems"][rev]
    assert a["attempts"] == 2 and a["solved_minute"] is not None
    assert rows["Alice"]["penalty"] == a["solved_minute"] + 40
    assert rows["Bob"]["problems"][rev]["first_solve"] is False and rows["Alice"]["problems"][rev]["first_solve"] is True
    mine = {p["id"]: p["my_status"] for p in (await client.get(f"/api/contests/{c['id']}", headers=bob["h"])).json()["problems"]}
    assert mine == {rev: "solved", fizz: "solved"}


async def test_contest_locks_help_and_freezes_the_scoreboard(client, reg):
    owner, (alice, bob), probs = await setup_org(client, reg)
    c = await _contest(client, owner, probs, starts_at=at(-60), ends_at=at(30), freeze_minutes=45)
    rev = probs["Reverse a String"]
    problem = (await client.get(f"/api/problems/{rev}", headers=alice["h"])).json()
    assert problem["contest"]["id"] == c["id"] and problem["contest"]["locked"]
    assert (await client.post(f"/api/problems/{rev}/hint", headers=alice["h"], json={})).status_code == 403
    assert (await client.post(f"/api/problems/{rev}/reveal", headers=alice["h"])).status_code == 403
    assert (await client.get(f"/api/problems/{rev}/discussions", headers=alice["h"])).json()["reason"] == "contest"
    sub = (await client.post(f"/api/problems/{rev}/submit", headers=alice["h"], json={"language": "python", "code": REVERSE_OK})).json()
    assert (await client.post(f"/api/submissions/{sub['id']}/review", headers=alice["h"])).status_code == 403
    assert (await client.post(f"/api/problems/{rev}/hint", headers=owner, json={})).status_code == 200  # staff aren't locked

    # Frozen: Bob sees Alice's attempt as pending; Alice sees her own result; staff see everything.
    bob_view = (await client.get(f"/api/contests/{c['id']}/scoreboard", headers=bob["h"])).json()
    assert bob_view["frozen"] and bob_view["rows"][0]["solved"] == 0 and bob_view["rows"][0]["problems"][rev]["pending"] == 1
    alice_view = (await client.get(f"/api/contests/{c['id']}/scoreboard", headers=alice["h"])).json()
    assert alice_view["rows"][0]["solved"] == 1
    assert (await client.get(f"/api/contests/{c['id']}/scoreboard", headers=owner)).json()["rows"][0]["solved"] == 1

    # After the end, help unlocks and the scoreboard unfreezes for everyone.
    async with system_session() as db:
        from app.db.models import Contest

        await db.execute(update(Contest).values(ends_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
        await db.commit()
    final = (await client.get(f"/api/contests/{c['id']}/scoreboard", headers=bob["h"])).json()
    assert final["status"] == "ended" and not final["frozen"] and final["rows"][0]["solved"] == 1
    assert (await client.post(f"/api/problems/{rev}/hint", headers=alice["h"], json={})).status_code == 200
    late = (await client.post(f"/api/problems/{rev}/submit", headers=bob["h"], json={"language": "python", "code": REVERSE_OK})).json()
    async with system_session() as db:
        assert (await db.get(Submission, late["id"])).contest_id is None  # practice after the end doesn't count


async def test_upcoming_contest_hides_problems_from_candidates(client, reg):
    owner, (alice, _), probs = await setup_org(client, reg)
    c = await _contest(client, owner, probs, starts_at=at(30), ends_at=at(90))
    assert (await client.get(f"/api/contests/{c['id']}", headers=alice["h"])).json()["problems"] == []
    assert len((await client.get(f"/api/contests/{c['id']}", headers=owner)).json()["problems"]) == 2
    assert (await client.get(f"/api/problems/{probs['FizzBuzz']}", headers=alice["h"])).json()["contest"] is None
    await client.delete(f"/api/contests/{c['id']}", headers=owner)
    assert (await client.get("/api/contests", headers=alice["h"])).json() == []
    actions = [e["action"] for e in (await client.get("/api/org/audit", headers=owner)).json()["items"]]
    assert "contest.created" in actions and "contest.deleted" in actions


# ---------- whiteboard ----------
BOARD = {"elements": [
    {"id": "lb", "type": "box", "x": 0, "y": 0, "label": "Load balancer"},
    {"id": "api", "type": "box", "x": 0, "y": 100, "label": "API servers"},
    {"id": "db", "type": "db", "x": 0, "y": 200, "label": "Postgres"},
    {"id": "a1", "type": "arrow", "from": "lb", "to": "api"},
    {"id": "a2", "type": "arrow", "from": "api", "to": "db", "label": "reads/writes"},
    {"id": "n", "type": "text", "x": 300, "y": 0, "label": "Cache hot keys in Redis"},
]}


def test_whiteboard_validation_ops_and_description():
    text = whiteboard.describe(whiteboard.clean_board(BOARD))
    assert "- Load balancer (component)" in text and "- Postgres (database)" in text
    assert "- API servers -> Postgres: reads/writes" in text and "- Cache hot keys in Redis" in text
    after = whiteboard.apply_ops(BOARD, [{"op": "delete", "id": "api"}])
    assert {e["id"] for e in after["elements"]} == {"lb", "db", "n"}  # arrows to a deleted shape go too
    with pytest.raises(whiteboard.BoardError):
        whiteboard.apply_ops(BOARD, [{"op": "put", "element": {"id": "<bad>", "type": "box"}}])
    with pytest.raises(whiteboard.BoardError):
        whiteboard.clean_board({"elements": [{"id": f"e{i}", "type": "box"} for i in range(301)]})
    huge = whiteboard.clean_element({"id": "x", "type": "box", "x": 1e12, "w": -5, "label": "y" * 999})
    assert huge["x"] == whiteboard.COORD and huge["w"] == 10 and len(huge["label"]) == whiteboard.MAX_LABEL


async def test_ai_design_interview_sees_the_whiteboard(client, reg):
    h = await reg(client)
    iv = (await client.post("/api/interviews", headers=h, json={"kind": "system_design"})).json()
    msgs = (await client.post(f"/api/interviews/{iv['id']}/messages", headers=h,
                              json={"content": "Here's my design.", "board": BOARD})).json()
    assert "Postgres" in msgs[0]["board_text"] and "diagram" in msgs[1]["content"]
    again = (await client.post(f"/api/interviews/{iv['id']}/messages", headers=h,
                               json={"content": "Same design.", "board": BOARD})).json()
    assert again[0]["board_text"] is None  # unchanged boards aren't re-sent
    assert (await client.get(f"/api/interviews/{iv['id']}", headers=h)).json()["board"]["elements"][0]["id"] == "lb"
    bad = await client.post(f"/api/interviews/{iv['id']}/messages", headers=h,
                            json={"content": "x", "board": {"elements": [{"id": "!", "type": "box"}]}})
    assert bad.status_code == 422


async def test_peer_whiteboard_syncs_between_both_people(client, reg):
    owner, (alice, bob), _ = await setup_org(client, reg)
    room = (await client.post("/api/peer", headers=alice["h"], json={"invite_user_id": bob["id"]})).json()
    await client.post(f"/api/peer/{room['id']}/join", headers=bob["h"])
    path = f"/api/peer/{room['id']}/ws"
    a, b = await Socket(path).connect(), await Socket(path).connect()
    await a.send({"type": "auth", "token": await _token(client, "acme", "cand0@acme.io")})
    await b.send({"type": "auth", "token": await _token(client, "acme", "cand1@acme.io")})
    await a.recv("state"), await b.recv("state")
    # The interviewer can draw too (unlike code).
    await b.send({"type": "board", "ops": [{"op": "put", "element": BOARD["elements"][0]}]})
    got = await a.recv("board")
    assert got["ops"][0]["element"]["label"] == "Load balancer" and got["from"] == bob["id"]
    await a.send({"type": "board", "ops": [{"op": "put", "element": {"id": "q", "type": "queue", "label": "Jobs"}}]})
    await b.recv("board")
    await a.send({"type": "board", "ops": [{"op": "explode"}]})
    assert "Whiteboard" in (await a.recv("error"))["detail"]
    state = (await client.get(f"/api/peer/{room['id']}", headers=alice["h"])).json()
    assert {e["id"] for e in state["board"]["elements"]} == {"lb", "q"}
    await a.close()
    await b.close()
