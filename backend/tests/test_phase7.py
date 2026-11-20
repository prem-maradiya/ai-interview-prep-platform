"""Function-style problems, complexity checks, audit log, mentor cohort digest and peer mock interviews."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.db.models import User
from app.db.session import system_session
from app.main import app
from app.services import email
from app.services.email_jobs import send_digests
from tests.test_phase3 import REVERSE_OK, due, setup_org


def H(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def outbox() -> list[email.Email]:
    await email.wait_for_outgoing()
    return list(email.sent_messages)


# ---------- function-style problems ----------
SIG = {"function": "pairSum", "params": [{"name": "nums", "type": "int[]"}, {"name": "target", "type": "int"}],
       "returns": "int[]"}
PAIR_SUM = ("def pairSum(nums, target):\n    seen = {}\n    for i, x in enumerate(nums):\n"
            "        if target - x in seen:\n            return [seen[target - x], i]\n        seen[x] = i\n")


async def test_function_problem_authoring_and_judging(client, reg):
    owner, (alice, _), _ = await setup_org(client, reg)
    base = {"title": "Pair sum", "description": "Find two indices.", "signature": SIG}
    bad = await client.post("/api/problems", headers=owner, json={**base, "tests": [{"input": "[1,2]", "expected_output": "[0,1]"}]})
    assert bad.status_code == 422 and "Test 1" in bad.json()["detail"] and "2 line" in bad.json()["detail"]
    bad = await client.post("/api/problems", headers=owner, json={**base, "tests": [{"input": "[1,2]\n3", "expected_output": "nope"}]})
    assert bad.status_code == 422 and "JSON" in bad.json()["detail"]
    bad = await client.post("/api/problems", headers=owner, json={
        **base, "signature": {**SIG, "function": "class"}, "tests": [{"input": "[1,2]\n3", "expected_output": "[0,1]"}]})
    assert bad.status_code == 422

    p = (await client.post("/api/problems", headers=owner, json={**base, "tests": [
        {"input": "[2,7,11,15]\n9", "expected_output": "[0,1]"},
        {"input": "[3,2,4]\n6", "expected_output": "[1,2]", "is_hidden": True}]})).json()
    assert p["signature"]["function"] == "pairSum"
    assert set(p["starter_code"]) == {"python", "javascript", "java", "cpp", "go"}
    assert "def pairSum(nums: list[int], target: int) -> list[int]:" in p["starter_code"]["python"]
    assert "vector<int> pairSum(vector<int>& nums, int target)" in p["starter_code"]["cpp"]
    listed = next(x for x in (await client.get("/api/problems", headers=alice["h"])).json() if x["id"] == p["id"])
    assert listed["style"] == "function"

    url = f"/api/problems/{p['id']}"
    sub = (await client.post(f"{url}/submit", headers=alice["h"], json={"language": "python", "code": PAIR_SUM})).json()
    assert sub["verdict"] == "accepted", sub["results"]
    run = (await client.post(f"{url}/run", headers=alice["h"], json={
        "language": "python", "code": "def pairSum(nums, target):\n    print('checking', len(nums))\n    return [1, 0]\n"})).json()
    first = run["results"][0]
    assert run["verdict"] == "wrong_answer" and first["stdout"] == "[1,0]" and first["printed"] == "checking 4"
    assert first["input"] == "nums = [2,7,11,15], target = 9"
    custom = (await client.post(f"{url}/run", headers=alice["h"], json={
        "language": "python", "code": PAIR_SUM, "custom_input": "[5,5]\n10"})).json()
    assert custom["verdict"] == "accepted" and custom["results"][0]["status"] == "ran"
    assert custom["results"][0]["stdout"] == "[0,1]"
    invalid = (await client.post(f"{url}/run", headers=alice["h"], json={
        "language": "python", "code": PAIR_SUM, "custom_input": "[5,5]\n\"ten\""})).json()
    assert invalid["verdict"] == "invalid_input" and "expected an integer" in invalid["results"][0]["stderr"]
    returned_nothing = (await client.post(f"{url}/run", headers=alice["h"], json={
        "language": "python", "code": "def pairSum(nums, target):\n    import sys; sys.exit(0)\n"})).json()
    assert returned_nothing["verdict"] == "runtime_error"


async def test_unordered_and_float_comparison(client, reg):
    h = await reg(client)
    probs = {p["title"]: p["id"] for p in (await client.get("/api/problems", headers=h)).json()}
    top_k = ("from collections import Counter\ndef topKFrequent(nums, k):\n"
             "    return sorted(x for x, _ in Counter(nums).most_common(k))")  # any order is fine
    sub = (await client.post(f"/api/problems/{probs['Top K Frequent Elements']}/submit", headers=h,
                             json={"language": "python", "code": top_k})).json()
    assert sub["verdict"] == "accepted", sub["results"]
    median_int = ("def findMedianSortedArrays(nums1, nums2):\n    a = sorted(nums1 + nums2); n = len(a)\n"
                  "    return a[n // 2] if n % 2 else (a[n // 2 - 1] + a[n // 2]) / 2 + 1e-9")  # tiny float error
    sub = (await client.post(f"/api/problems/{probs['Median of Two Sorted Arrays']}/submit", headers=h,
                             json={"language": "python", "code": median_int})).json()
    assert sub["verdict"] == "accepted", sub["results"]
    wrong_type = "def isPalindrome(s):\n    return 1\n"  # 1 is not true
    sub = (await client.post(f"/api/problems/{probs['Valid Palindrome']}/submit", headers=h,
                             json={"language": "python", "code": wrong_type})).json()
    assert sub["verdict"] == "wrong_answer"


async def test_quadratic_solution_is_too_slow_on_seeded_stress_test(client, reg):
    h = await reg(client)
    pid = next(p["id"] for p in (await client.get("/api/problems", headers=h)).json() if p["title"] == "Maximum Subarray")
    quadratic = ("a=list(map(int,input().split()))\nbest=a[0]\nfor i in range(len(a)):\n    s=0\n"
                 "    for j in range(i,len(a)):\n        s+=a[j]\n        best=max(best,s)\nprint(best)")
    sub = (await client.post(f"/api/problems/{pid}/submit", headers=h, json={"language": "python", "code": quadratic})).json()
    assert sub["verdict"] == "too_slow"
    large = [r for r in sub["results"] if r["large"]]
    assert large and large[0]["status"] == "time_limit_exceeded" and "200,000 integers" in large[0]["input"]
    assert all(r["passed"] for r in sub["results"] if not r["large"])


# ---------- audit log ----------
async def test_audit_log_records_admin_and_security_actions(client, reg):
    owner, (alice, bob), probs = await setup_org(client, reg)
    other = await reg(client, "other", "owner@other.io")
    await client.patch(f"/api/org/members/{bob['id']}", headers=owner, json={"role": "mentor"})
    await client.patch(f"/api/org/members/{bob['id']}", headers=owner, json={"is_active": False})
    await client.patch(f"/api/org/members/{bob['id']}", headers=owner, json={"is_active": False})  # no-op: no event
    p = (await client.post("/api/problems", headers=owner, json={
        "title": "Temp", "description": "d", "tests": [{"input": "1", "expected_output": "1"}]})).json()
    await client.delete(f"/api/problems/{p['id']}", headers=owner)
    await client.post("/api/assignments", headers=owner, json={"title": "W1", "due_at": due(3), "problem_ids": [probs["Two Sum"]]})
    await client.post("/api/auth/password", headers=alice["h"], json={"current_password": "password123", "new_password": "newpass123"})

    log = (await client.get("/api/org/audit", headers=owner)).json()
    actions = [e["action"] for e in log["items"]]
    for a in ("member.role_changed", "member.deactivated", "invite.created", "invite.accepted", "problem.created",
              "problem.deleted", "assignment.created", "auth.password_changed"):
        assert a in actions, a
    assert actions.count("member.deactivated") == 1
    role = next(e for e in log["items"] if e["action"] == "member.role_changed")
    assert role["details"] == {"from": "candidate", "to": "mentor"} and "cand1@acme.io" in role["target_label"]
    assert role["actor_name"] == "Owner" and log["actions"]["member.role_changed"]
    only = (await client.get("/api/org/audit?action=problem.deleted", headers=owner)).json()["items"]
    assert [e["target_label"] for e in only] == ["Temp"]

    assert (await client.get("/api/org/audit", headers=alice["h"])).status_code == 403
    assert (await client.get("/api/org/audit", headers=other)).json()["items"] == []  # other org's log is empty
    csv_text = (await client.get("/api/org/audit.csv", headers=owner)).text
    assert csv_text.startswith("time (UTC),actor,actor email,action,target") and "Changed a member's role" in csv_text


# ---------- mentor cohort digest ----------
async def test_mentor_digest_summarises_the_cohort(client, reg):
    owner, (alice, bob), probs = await setup_org(client, reg)
    await client.post("/api/assignments", headers=owner, json={
        "title": "Strings week", "due_at": due(3), "problem_ids": [probs["Reverse a String"]]})
    await client.post(f"/api/problems/{probs['Reverse a String']}/submit", headers=alice["h"],
                      json={"language": "python", "code": REVERSE_OK})
    await outbox()
    email.sent_messages.clear()
    async with system_session() as db:
        await db.execute(update(User).values(created_at=datetime.now(timezone.utc) - timedelta(days=8)))
        await db.commit()
        await send_digests(db)
    sent = {m.to: m for m in await outbox()}
    owner_mail = sent["owner@acme.io"].text  # the owner did no practice, but still gets the cohort summary
    assert "1 of 2 candidates practised" in owner_mail and "solving 1 problem" in owner_mail
    assert 'Assignment "Strings week" is due' in owner_mail and "1 of 2 completed" in owner_mail
    assert "No practice in the last 7 days: Bob" in owner_mail
    assert "candidates practised" not in sent["cand0@acme.io"].text  # candidates only get their own week


# ---------- peer interviews: REST ----------
async def test_peer_interview_lifecycle(client, reg):
    owner, (alice, bob), _ = await setup_org(client, reg)
    other = await reg(client, "other", "owner@other.io")
    room = (await client.post("/api/peer", headers=alice["h"], json={"invite_user_id": bob["id"], "difficulty": "easy"})).json()
    url = f"/api/peer/{room['id']}"
    bob_notes = (await client.get("/api/notifications", headers=bob["h"])).json()["items"]
    assert bob_notes[0]["kind"] == "peer" and bob_notes[0]["link"] == f"/peer/{room['id']}"
    assert [r["id"] for r in (await client.get("/api/peer", headers=bob["h"])).json()["open"]] == [room["id"]]
    assert (await client.get(url, headers=other)).status_code == 404  # other organization
    assert (await client.post(f"{url}/join", headers=alice["h"])).status_code == 400  # own room

    state = (await client.post(f"{url}/join", headers=bob["h"])).json()
    assert state["status"] == "active" and state["my_role"] == "interviewer"  # Alice solves first
    assert state["candidate_id"] == alice["id"] and state["problem"]["difficulty"] == "easy"
    assert (await client.post(f"{url}/join", headers=owner)).status_code == 409

    code = REVERSE_OK if state["problem"]["title"] == "Reverse a String" else "print('x')"
    assert (await client.post(f"{url}/run", headers=bob["h"], json={"language": "python", "code": code})).status_code == 403
    run = await client.post(f"{url}/run", headers=alice["h"], json={"language": "python", "code": code})
    assert run.status_code == 200 and run.json()["total"] >= 1

    rating = {"round": 1, "problem_solving": 4, "communication": 5, "code_quality": 3, "hire": "yes", "comments": "Clear thinking."}
    assert (await client.post(f"{url}/feedback", headers=bob["h"], json=rating)).status_code == 409  # round 1 still running
    swapped = (await client.post(f"{url}/swap", headers=alice["h"])).json()
    assert swapped["round"] == 2 and swapped["candidate_id"] == bob["id"] and swapped["my_role"] == "interviewer"
    assert (await client.post(f"{url}/swap", headers=alice["h"])).status_code == 409  # only once
    assert (await client.post(f"{url}/feedback", headers=alice["h"], json=rating)).status_code == 403  # Bob interviewed round 1
    assert (await client.post(f"{url}/feedback", headers=bob["h"], json=rating)).status_code == 201
    assert (await client.post(f"{url}/feedback", headers=bob["h"], json=rating)).status_code == 409
    assert (await client.post(f"{url}/feedback", headers=alice["h"], json={**rating, "round": 2})).status_code == 409
    ended = (await client.post(f"{url}/end", headers=bob["h"])).json()
    assert ended["status"] == "completed"
    assert (await client.post(f"{url}/feedback", headers=alice["h"], json={**rating, "round": 2, "hire": "strong_yes"})).status_code == 201

    mine = (await client.get("/api/peer", headers=alice["h"])).json()["mine"][0]
    assert mine["partner"]["name"] == "Bob" and [f["hire"] for f in mine["my_feedback"]] == ["yes"]
    alice_view = (await client.get(url, headers=alice["h"])).json()
    assert {f["round"] for f in alice_view["feedback"]} == {1, 2} and alice_view["feedback_given"] == [2]
    mentor_view = (await client.get(f"/api/peer?user_id={bob['id']}", headers=owner)).json()["mine"]
    assert mentor_view[0]["my_feedback"][0]["hire"] == "strong_yes"
    assert (await client.get(f"/api/peer?user_id={bob['id']}", headers=alice["h"])).status_code == 403


async def test_peer_room_open_to_org_and_cancel(client, reg):
    owner, (alice, bob), _ = await setup_org(client, reg)
    room = (await client.post("/api/peer", headers=alice["h"], json={"host_first": False})).json()
    assert room["id"] in [r["id"] for r in (await client.get("/api/peer", headers=owner)).json()["open"]]
    assert (await client.post(f"/api/peer/{room['id']}/cancel", headers=bob["h"])).status_code == 409
    assert (await client.post(f"/api/peer/{room['id']}/cancel", headers=alice["h"])).json()["status"] == "cancelled"
    assert (await client.get("/api/peer", headers=bob["h"])).json()["open"] == []
    assert (await client.post(f"/api/peer/{room['id']}/join", headers=bob["h"])).status_code == 404  # gone


# ---------- peer interviews: live room over WebSocket ----------
class Socket:
    """Minimal in-process ASGI WebSocket client (same event loop as the app and its database)."""

    def __init__(self, path: str) -> None:
        self.path, self.to_app, self.from_app = path, asyncio.Queue(), asyncio.Queue()

    async def connect(self) -> "Socket":
        scope = {"type": "websocket", "asgi": {"version": "3.0"}, "path": self.path, "raw_path": self.path.encode(),
                 "query_string": b"", "headers": [(b"host", b"test")], "scheme": "ws", "subprotocols": [],
                 "client": ("127.0.0.1", 5000), "server": ("test", 80), "root_path": ""}
        await self.to_app.put({"type": "websocket.connect"})
        self.task = asyncio.create_task(app(scope, self.to_app.get, self.from_app.put))
        accepted = await asyncio.wait_for(self.from_app.get(), 5)
        assert accepted["type"] == "websocket.accept"
        return self

    async def send(self, message: dict) -> None:
        await self.to_app.put({"type": "websocket.receive", "text": json.dumps(message)})

    async def recv(self, want: str | None = None, timeout: float = 5) -> dict:
        while True:
            event = await asyncio.wait_for(self.from_app.get(), timeout)
            if event["type"] == "websocket.close":
                return {"type": "closed", "code": event.get("code")}
            message = json.loads(event["text"])
            if want is None or message["type"] == want:
                return message

    async def nothing(self, kind: str, wait: float = 0.3) -> bool:
        try:
            while True:
                if (await self.recv(timeout=wait))["type"] == kind:
                    return False
        except asyncio.TimeoutError:
            return True

    async def close(self) -> None:
        await self.to_app.put({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(self.task, 5)


async def _token(client, slug: str, email_: str) -> str:
    r = await client.post("/api/auth/login", json={"org_slug": slug, "email": email_, "password": "password123"})
    return r.json()["access_token"]


async def test_peer_room_live_code_chat_and_presence(client, reg):
    owner, (alice, bob), _ = await setup_org(client, reg)
    room = (await client.post("/api/peer", headers=alice["h"], json={"invite_user_id": bob["id"]})).json()
    await client.post(f"/api/peer/{room['id']}/join", headers=bob["h"])
    path = f"/api/peer/{room['id']}/ws"

    bad = await Socket(path).connect()
    await bad.send({"type": "auth", "token": "not-a-token"})
    assert (await bad.recv())["code"] == 4401
    outsider_token = (await client.post("/api/auth/register-org", json={
        "org_name": "Zed", "org_slug": "zed", "full_name": "Zed", "email": "z@zed.io", "password": "password123"})).json()["access_token"]
    outsider = await Socket(path).connect()
    await outsider.send({"type": "auth", "token": outsider_token})
    assert (await outsider.recv())["code"] == 4403

    a = await Socket(path).connect()
    await a.send({"type": "auth", "token": await _token(client, "acme", "cand0@acme.io")})
    assert (await a.recv("state"))["state"]["my_role"] == "candidate"
    b = await Socket(path).connect()
    await b.send({"type": "auth", "token": await _token(client, "acme", "cand1@acme.io")})
    assert (await b.recv("state"))["state"]["my_role"] == "interviewer"
    assert set((await a.recv("presence"))["online"]) == {alice["id"]}
    assert set((await a.recv("presence"))["online"]) == {alice["id"], bob["id"]}

    await a.send({"type": "code", "language": "python", "code": "print('hi')"})
    got = await b.recv("code")
    assert got["code"] == "print('hi')" and got["from"] == alice["id"]
    assert (await a.recv("code"))["from"] == alice["id"]  # the sender gets the echo too (the UI ignores its own)
    await b.send({"type": "code", "language": "python", "code": "hijack"})  # the interviewer can't edit
    assert await a.nothing("code")

    await b.send({"type": "chat", "text": "Walk me through your approach?"})
    assert (await a.recv("chat"))["message"]["text"] == "Walk me through your approach?"
    await b.send({"type": "notes", "text": "Good start, missed edge case"})  # private to the interviewer
    assert await a.nothing("notes")
    await a.send({"type": "ping"})
    assert (await a.recv("pong"))["type"] == "pong"

    # Everything was saved: a fresh page load shows the code, chat and (for the interviewer) notes.
    a_view = (await client.get(f"/api/peer/{room['id']}", headers=alice["h"])).json()
    b_view = (await client.get(f"/api/peer/{room['id']}", headers=bob["h"])).json()
    assert a_view["code"]["code"] == "print('hi')" and a_view["notes"] is None
    assert b_view["notes"] == "Good start, missed edge case"
    assert [m["text"] for m in a_view["messages"]] == ["Walk me through your approach?"]

    # REST changes reach the room too.
    await client.post(f"/api/peer/{room['id']}/swap", headers=bob["h"])
    assert (await a.recv("changed"))["round"] == 2
    await b.close()
    assert set((await a.recv("presence"))["online"]) == {alice["id"]}
    await a.close()
