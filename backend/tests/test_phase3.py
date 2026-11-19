"""Phase 3: assignments, mentor comments and review, integrity signals, CSV exports."""
import csv
import io
from datetime import datetime, timedelta, timezone

import pytest

from app.services.csv_export import safe_cell
from app.services.integrity import normalize

REVERSE_OK = "import sys\nprint(sys.stdin.read().strip()[::-1])"
TWO_SUM_A = (
    "import sys\nlines = sys.stdin.read().split('\\n')\nnums = list(map(int, lines[0].split()))\ntarget = int(lines[1])\n"
    "seen = {}\nfor i, x in enumerate(nums):\n    if target - x in seen:\n        print(seen[target - x], i)\n        break\n"
    "    seen[x] = i\n"
)
# Same solution with every variable renamed and a comment added: should still be flagged as similar.
TWO_SUM_B = (
    "import sys\nrows = sys.stdin.read().split('\\n')\narr = list(map(int, rows[0].split()))\ngoal = int(rows[1])\n"
    "# store indices\nd = {}\nfor idx, val in enumerate(arr):\n    if goal - val in d:\n        print(d[goal - val], idx)\n"
    "        break\n    d[val] = idx\n"
)


def H(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def setup_org(client, reg, slug="acme", names=("Alice", "Bob")):
    owner = await reg(client, slug, f"owner@{slug}.io")
    cands = []
    for i, n in enumerate(names):
        inv = (await client.post("/api/org/invites", json={"role": "candidate"}, headers=owner)).json()
        t = (await client.post("/api/auth/accept-invite", json={
            "code": inv["code"], "full_name": n, "email": f"cand{i}@{slug}.io", "password": "password123"})).json()
        h = H(t["access_token"])
        me = (await client.get("/api/auth/me", headers=h)).json()
        cands.append({"h": h, "id": me["id"], "name": n})
    problems = {p["title"]: p["id"] for p in (await client.get("/api/problems", headers=owner)).json()}
    return owner, cands, problems


def due(days: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


async def finish_interview(client, h, kind="behavioral"):
    iv = (await client.post("/api/interviews", json={"kind": kind}, headers=h)).json()
    await client.post(f"/api/interviews/{iv['id']}/messages", json={"content": "An answer."}, headers=h)
    await client.post(f"/api/interviews/{iv['id']}/end", headers=h)
    return iv["id"]


# ---------- assignments ----------
@pytest.mark.asyncio
async def test_assignment_progress_lifecycle(client, reg):
    owner, (alice, bob), probs = await setup_org(client, reg)
    # Work done BEFORE the assignment exists doesn't count.
    await client.post(f"/api/problems/{probs['Reverse a String']}/submit", json={"language": "python", "code": REVERSE_OK}, headers=alice["h"])
    a = (await client.post("/api/assignments", headers=owner, json={
        "title": "Week 1", "due_at": due(7), "problem_ids": [probs["Reverse a String"]],
        "interview_kind": "behavioral", "interview_count": 1})).json()
    assert [p["title"] for p in a["problems"]] == ["Reverse a String"]

    mine = (await client.get("/api/assignments", headers=alice["h"])).json()
    assert mine[0]["my_progress"]["status"] == "not_started" and mine[0]["my_progress"]["total"] == 2

    await client.post(f"/api/problems/{probs['Reverse a String']}/submit", json={"language": "python", "code": REVERSE_OK}, headers=alice["h"])
    prog = (await client.get(f"/api/assignments/{a['id']}", headers=alice["h"])).json()["my_progress"]
    assert prog["status"] == "in_progress" and prog["done"] == 1 and "progress" not in prog
    await finish_interview(client, alice["h"], "technical")  # wrong kind: doesn't count
    await finish_interview(client, alice["h"], "behavioral")
    prog = (await client.get(f"/api/assignments/{a['id']}", headers=alice["h"])).json()["my_progress"]
    assert prog["status"] == "completed" and prog["completed_at"]

    # Mentor view: matrix for every candidate, plus counts in the list.
    detail = (await client.get(f"/api/assignments/{a['id']}", headers=owner)).json()
    assert {p["full_name"]: p["status"] for p in detail["progress"]} == {"Alice": "completed", "Bob": "not_started"}
    listing = (await client.get("/api/assignments", headers=owner)).json()
    assert listing[0]["assignee_count"] == 2 and listing[0]["completed_count"] == 1


@pytest.mark.asyncio
async def test_assignment_targeting_validation_and_overdue(client, reg):
    owner, (alice, bob), probs = await setup_org(client, reg)
    only_bob = (await client.post("/api/assignments", headers=owner, json={
        "title": "Bob only", "due_at": due(-1), "problem_ids": [probs["FizzBuzz"]],
        "assign_all": False, "assignee_ids": [bob["id"]]})).json()
    assert (await client.get(f"/api/assignments/{only_bob['id']}", headers=alice["h"])).status_code == 404
    assert (await client.get("/api/assignments", headers=alice["h"])).json() == []
    assert (await client.get(f"/api/assignments/{only_bob['id']}", headers=bob["h"])).json()["my_progress"]["status"] == "overdue"
    # Candidates can't create; bad inputs are rejected.
    assert (await client.post("/api/assignments", headers=alice["h"], json={"title": "Mine", "due_at": due(1), "interview_count": 1})).status_code == 403
    assert (await client.post("/api/assignments", headers=owner, json={"title": "Empty", "due_at": due(1)})).status_code == 400
    assert (await client.post("/api/assignments", headers=owner, json={"title": "Bad", "due_at": due(1), "problem_ids": ["nope"]})).status_code == 400
    # Another org's member can't be assigned, and another org can't see this assignment.
    other_owner, (carol,), _ = await setup_org(client, reg, "beta", ("Carol",))
    bad = await client.post("/api/assignments", headers=owner, json={
        "title": "Cross-org", "due_at": due(1), "interview_count": 1, "assign_all": False, "assignee_ids": [carol["id"]]})
    assert bad.status_code == 400
    assert (await client.get(f"/api/assignments/{only_bob['id']}", headers=other_owner)).status_code == 404


# ---------- comments & mentor review ----------
@pytest.mark.asyncio
async def test_mentor_comments_on_interview(client, reg):
    owner, (alice, bob), _ = await setup_org(client, reg)
    iv_id = await finish_interview(client, alice["h"])
    # Mentors can read the candidate's interview; other candidates can't.
    assert (await client.get(f"/api/interviews/{iv_id}", headers=owner)).status_code == 200
    assert (await client.get(f"/api/interviews/{iv_id}", headers=bob["h"])).status_code == 404
    # ...but can't act on it.
    assert (await client.post(f"/api/interviews/{iv_id}/messages", json={"content": "hi"}, headers=owner)).status_code == 404

    target = {"target_type": "interview", "target_id": iv_id}
    c = (await client.post("/api/comments", headers=owner, json={**target, "body": "Good structure; quantify impact."})).json()
    assert c["author_role"] == "owner"
    await client.post("/api/comments", headers=alice["h"], json={**target, "body": "Thanks, will do!"})
    thread = (await client.get("/api/comments", params=target, headers=alice["h"])).json()
    assert [t["body"] for t in thread] == ["Good structure; quantify impact.", "Thanks, will do!"]
    assert (await client.get("/api/comments", params=target, headers=bob["h"])).status_code == 404
    assert (await client.post("/api/comments", headers=bob["h"], json={**target, "body": "x"})).status_code == 404
    # Only the author (or an admin) can delete a comment.
    assert (await client.delete(f"/api/comments/{c['id']}", headers=alice["h"])).status_code == 403
    assert (await client.delete(f"/api/comments/{c['id']}", headers=owner)).status_code == 204
    # Other tenants can't see the thread.
    other_owner = await reg(client, "beta", "owner@beta.io")
    assert (await client.get("/api/comments", params=target, headers=other_owner)).status_code == 404


# ---------- integrity signals ----------
def test_normalize_ignores_renames_and_comments():
    assert normalize(TWO_SUM_A, "python") == normalize(TWO_SUM_B, "python")
    assert normalize("let a = 1 // x", "javascript") == normalize("let b = 1 /* y */", "javascript")


@pytest.mark.asyncio
async def test_integrity_signals_visible_to_mentors_only(client, reg):
    owner, (alice, bob), probs = await setup_org(client, reg)
    two = probs["Two Sum"]
    clean = (await client.post(f"/api/problems/{two}/submit", headers=alice["h"], json={
        "language": "python", "code": TWO_SUM_A,
        "telemetry": {"paste_chars": 0, "tab_switches": 1, "time_spent_s": 600}})).json()
    assert clean["verdict"] == "accepted" and "integrity_flags" not in clean
    copied = (await client.post(f"/api/problems/{two}/submit", headers=bob["h"], json={
        "language": "python", "code": TWO_SUM_B,
        "telemetry": {"paste_chars": len(TWO_SUM_B), "paste_events": 1, "tab_switches": 7, "time_spent_s": 20}})).json()
    assert copied["verdict"] == "accepted"

    # The candidate never sees signals; the mentor does.
    assert "integrity_flags" not in (await client.get(f"/api/submissions/{copied['id']}", headers=bob["h"])).json()
    staff = (await client.get(f"/api/submissions/{copied['id']}", headers=owner)).json()
    types = {f["type"] for f in staff["integrity_flags"]}
    assert types == {"mostly_pasted", "tab_switching", "similar_code"}  # Two Sum is "easy": no very_fast flag
    assert "Alice" in next(f["detail"] for f in staff["integrity_flags"] if f["type"] == "similar_code")

    report = (await client.get("/api/integrity", headers=owner)).json()
    assert [r["full_name"] for r in report] == ["Bob"] and report[0]["flags"][0]["severity"] == "high"
    assert (await client.get("/api/integrity", headers=alice["h"])).status_code == 403
    activity = (await client.get(f"/api/org/members/{bob['id']}/activity", headers=owner)).json()
    assert activity["submissions"][0]["problem_title"] == "Two Sum" and activity["submissions"][0]["integrity_flags"]
    assert "code" not in activity["submissions"][0]


@pytest.mark.asyncio
async def test_fast_solve_flag_on_medium_problem(client, reg):
    owner, (alice, _), probs = await setup_org(client, reg)
    code = ("s=input()\nst=[];m={')':'(',']':'[','}':'{'}\nok=True\nfor ch in s:\n    if ch in m:\n"
            "        if not st or st.pop()!=m[ch]: ok=False; break\n    else: st.append(ch)\nprint('true' if ok and not st else 'false')")
    sub = (await client.post(f"/api/problems/{probs['Valid Parentheses']}/submit", headers=alice["h"], json={
        "language": "python", "code": code, "telemetry": {"time_spent_s": 12}})).json()
    flags = (await client.get(f"/api/submissions/{sub['id']}", headers=owner)).json()["integrity_flags"]
    assert [f["type"] for f in flags] == ["very_fast"]


# ---------- CSV exports ----------
def test_safe_cell_blocks_formula_injection():
    assert safe_cell('=HYPERLINK("http://evil","x")').startswith("'=")
    assert safe_cell("+1") == "'+1" and safe_cell("@SUM(A1)") == "'@SUM(A1)" and safe_cell("-x") == "'-x"
    assert safe_cell(-5) == "-5" and safe_cell(None) == "" and safe_cell("Alice") == "Alice"


@pytest.mark.asyncio
async def test_csv_exports(client, reg):
    owner, (alice, bob), probs = await setup_org(client, reg, names=("=cmd|' /C calc'!A0", "Bob"))
    a = (await client.post("/api/assignments", headers=owner, json={
        "title": "Week 1", "due_at": due(3), "problem_ids": [probs["Reverse a String"]], "interview_count": 1,
        "interview_kind": "behavioral"})).json()
    await client.post(f"/api/problems/{probs['Reverse a String']}/submit", json={"language": "python", "code": REVERSE_OK}, headers=bob["h"])

    r = await client.get(f"/api/assignments/{a['id']}/export.csv", headers=owner)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text.lstrip("\ufeff"))))
    assert rows[0] == ["Name", "Email", "Status", "Done", "Total", "Completed at", "Reverse a String", "behavioral interviews"]
    by_name = {row[0]: row for row in rows[1:]}
    assert by_name["Bob"][2:5] == ["in_progress", "1", "2"] and by_name["Bob"][6:] == ["yes", "0/1"]
    assert "'=cmd|' /C calc'!A0" in by_name  # neutralised, not executable

    cohort = await client.get("/api/analytics/org/export.csv", headers=owner)
    assert cohort.status_code == 200 and "Problems solved" in cohort.text
    assert (await client.get("/api/analytics/org/export.csv", headers=alice["h"])).status_code == 403
