import pytest

pytestmark = pytest.mark.asyncio

REVERSE_OK = "import sys\nprint(sys.stdin.read().strip()[::-1])"


async def test_register_login_refresh_me(client, reg):
    await reg(client)
    r = await client.post("/api/auth/login", json={"org_slug": "acme", "email": "owner@acme.io", "password": "password123"})
    assert r.status_code == 200
    tokens = r.json()
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.json()["role"] == "owner" and me.json()["tenant"]["slug"] == "acme"
    # refresh token can't be used as an access token, but works on /refresh
    bad = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {tokens['refresh_token']}"})
    assert bad.status_code == 401
    assert (await client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})).status_code == 200


async def test_wrong_password_and_duplicate_slug(client, reg):
    await reg(client)
    r = await client.post("/api/auth/login", json={"org_slug": "acme", "email": "owner@acme.io", "password": "nope-nope"})
    assert r.status_code == 401
    r = await client.post("/api/auth/register-org", json={
        "org_name": "Other Org", "org_slug": "acme", "full_name": "X", "email": "x@other.io", "password": "password123"})
    assert r.status_code == 409


async def test_invite_flow_and_roles(client, reg):
    owner = await reg(client)
    inv = (await client.post("/api/org/invites", json={"role": "candidate"}, headers=owner)).json()
    r = await client.post("/api/auth/accept-invite", json={
        "code": inv["code"], "full_name": "Cand", "email": "c@acme.io", "password": "password123"})
    assert r.status_code == 201
    cand = {"Authorization": f"Bearer {r.json()['access_token']}"}
    # invite is single-use
    again = await client.post("/api/auth/accept-invite", json={
        "code": inv["code"], "full_name": "C2", "email": "c2@acme.io", "password": "password123"})
    assert again.status_code == 400
    # candidates can't access admin endpoints or create problems
    assert (await client.get("/api/org/members", headers=cand)).status_code == 403
    assert (await client.post("/api/problems", json={"title": "x", "description": "y", "tests": [{"expected_output": "1"}]},
                              headers=cand)).status_code == 403


async def test_tenant_isolation(client, reg):
    a = await reg(client, "alpha", "a@alpha.io")
    b = await reg(client, "beta", "b@beta.io")
    p = await client.post("/api/problems", headers=a, json={
        "title": "Alpha secret", "description": "d", "tests": [{"input": "1", "expected_output": "1", "is_hidden": True}]})
    pid = p.json()["id"]
    # beta can see global problems but not alpha's
    titles_b = [x["title"] for x in (await client.get("/api/problems", headers=b)).json()]
    assert "Alpha secret" not in titles_b and "Two Sum" in titles_b
    assert (await client.get(f"/api/problems/{pid}", headers=b)).status_code == 404
    assert (await client.delete(f"/api/problems/{pid}", headers=b)).status_code == 404
    # interviews and submissions are isolated too
    iv = (await client.post("/api/interviews", json={"kind": "behavioral"}, headers=a)).json()
    assert (await client.get(f"/api/interviews/{iv['id']}", headers=b)).status_code == 404
    # members list only shows own tenant
    members = (await client.get("/api/org/members", headers=b)).json()
    assert [m["email"] for m in members] == ["b@beta.io"]


async def test_hidden_tests_not_leaked(client, reg):
    h = await reg(client)
    problems = (await client.get("/api/problems", headers=h)).json()
    rev = next(p for p in problems if p["title"] == "Reverse a String")
    detail = (await client.get(f"/api/problems/{rev['id']}", headers=h)).json()
    assert all(not t["is_hidden"] for t in detail["sample_tests"]) and detail["hidden_test_count"] == 2
    sub = (await client.post(f"/api/problems/{rev['id']}/submit", json={"language": "python", "code": REVERSE_OK}, headers=h)).json()
    assert sub["verdict"] == "accepted" and sub["passed"] == sub["total"] == 4
    hidden = [r for r in sub["results"] if r["hidden"]]
    assert hidden and all(r["input"] is None and r["expected"] is None and r["stdout"] is None for r in hidden)


REFERENCE_SOLUTIONS = {
    "Reverse a String": "import sys\nprint(sys.stdin.read().strip()[::-1])",
    "FizzBuzz": "n=int(input())\nprint('\\n'.join('FizzBuzz' if i%15==0 else 'Fizz' if i%3==0 else 'Buzz' if i%5==0 else str(i) for i in range(1,n+1)))",
    "Two Sum": (
        "import sys\nl=sys.stdin.read().split('\\n')\nn=list(map(int,l[0].split()));t=int(l[1]);s={}\n"
        "for i,x in enumerate(n):\n    if t-x in s: print(s[t-x],i); break\n    s[x]=i"
    ),
    "Valid Parentheses": (
        "s=input() if True else ''\nst=[];m={')':'(',']':'[','}':'{'}\nok=True\n"
        "for ch in s:\n    if ch in m:\n        if not st or st.pop()!=m[ch]: ok=False; break\n    else: st.append(ch)\n"
        "print('true' if ok and not st else 'false')"
    ),
    "Longest Substring Without Repeating Characters": (
        "import sys\ns=sys.stdin.read().strip('\\n');b=0;st=0;last={}\n"
        "for i,ch in enumerate(s):\n    if last.get(ch,-1)>=st: st=last[ch]+1\n    last[ch]=i;b=max(b,i-st+1)\nprint(b)"
    ),
}


async def test_seed_problems_accept_reference_solutions(client, reg):
    """Guards against wrong expected outputs in the seeded problem bank."""
    h = await reg(client)
    problems = (await client.get("/api/problems", headers=h)).json()
    assert {p["title"] for p in problems} == set(REFERENCE_SOLUTIONS)
    for p in problems:
        sub = (await client.post(f"/api/problems/{p['id']}/submit",
                                 json={"language": "python", "code": REFERENCE_SOLUTIONS[p["title"]]}, headers=h)).json()
        assert sub["verdict"] == "accepted", (p["title"], sub["results"])


async def test_run_submit_verdicts_and_progress(client, reg):
    h = await reg(client)
    rev = next(p for p in (await client.get("/api/problems", headers=h)).json() if p["title"] == "Reverse a String")
    run = (await client.post(f"/api/problems/{rev['id']}/run", json={"language": "python", "code": "print('nope')"}, headers=h)).json()
    assert run["verdict"] == "wrong_answer"
    custom = (await client.post(f"/api/problems/{rev['id']}/run",
                                json={"language": "python", "code": REVERSE_OK, "custom_input": "xyz"}, headers=h)).json()
    assert custom["results"][0]["stdout"].strip() == "zyx"
    crash = (await client.post(f"/api/problems/{rev['id']}/submit", json={"language": "python", "code": "1/0"}, headers=h)).json()
    assert crash["verdict"] == "runtime_error"
    await client.post(f"/api/problems/{rev['id']}/submit", json={"language": "python", "code": REVERSE_OK}, headers=h)
    listing = (await client.get("/api/problems", headers=h)).json()
    assert next(p for p in listing if p["id"] == rev["id"])["solved"] is True
    me = (await client.get("/api/analytics/me", headers=h)).json()
    assert me["solved_total"] == 1 and me["submissions"] == 2 and me["acceptance_rate"] == 50.0
    # AI review is generated once and cached on the submission
    sub_id = (await client.get("/api/submissions", headers=h)).json()[0]["id"]
    reviewed = (await client.post(f"/api/submissions/{sub_id}/review", headers=h)).json()
    assert "mock review" in reviewed["ai_review"]
    hint = (await client.post(f"/api/problems/{rev['id']}/hint", json={"code": "", "level": 2}, headers=h)).json()
    assert hint["text"]


async def test_interview_lifecycle(client, reg):
    h = await reg(client)
    iv = (await client.post("/api/interviews", json={"kind": "technical", "level": "mid", "focus": "graphs"}, headers=h)).json()
    assert iv["status"] == "active" and iv["messages"][0]["sender"] == "interviewer"
    assert (await client.post(f"/api/interviews/{iv['id']}/end", headers=h)).status_code == 400  # no answers yet
    for _ in range(3):
        msgs = (await client.post(f"/api/interviews/{iv['id']}/messages", json={"content": "I'd use a hash map " * 30}, headers=h)).json()
        assert [m["sender"] for m in msgs] == ["candidate", "interviewer"]
    done = (await client.post(f"/api/interviews/{iv['id']}/end", headers=h)).json()
    assert done["status"] == "completed" and 0 <= done["overall_score"] <= 100
    assert len(done["scorecard"]["dimensions"]) == 5 and len(done["messages"]) == 7
    assert (await client.post(f"/api/interviews/{iv['id']}/messages", json={"content": "more"}, headers=h)).status_code == 409
    me = (await client.get("/api/analytics/me", headers=h)).json()
    assert me["interviews_completed"] == 1 and len(me["focus_areas"]) == 3
    org = (await client.get("/api/analytics/org", headers=h)).json()
    assert org["total_interviews"] == 1


async def test_quota_enforced(client, reg, monkeypatch):
    from datetime import timedelta

    from app.services import quota

    monkeypatch.setitem(quota.PLAN_LIMITS, "free", {"llm": (2, timedelta(days=30)), "sandbox": (1, timedelta(days=1))})
    h = await reg(client)
    rev = next(p for p in (await client.get("/api/problems", headers=h)).json() if p["title"] == "Reverse a String")
    body = {"language": "python", "code": REVERSE_OK}
    assert (await client.post(f"/api/problems/{rev['id']}/run", json=body, headers=h)).status_code == 200
    assert (await client.post(f"/api/problems/{rev['id']}/run", json=body, headers=h)).status_code == 429
    usage = (await client.get("/api/org", headers=h)).json()["usage"]
    assert usage["sandbox"]["used"] == 1 and usage["sandbox"]["limit"] == 1
