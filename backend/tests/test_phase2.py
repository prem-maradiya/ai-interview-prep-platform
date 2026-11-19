"""Phase 2: resume/JD documents + fit report, tailored interviews, in-interview code, voice delivery
metrics, spaced repetition and the daily study plan."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, update

from app.db.models import ReviewItem
from app.db.session import SessionLocal
from app.services.delivery import delivery_metrics
from app.services.documents import ExtractionError, extract_text
from app.services.practice import schedule, streak_from_dates

RESUME = "Jane Doe - Software Engineer. Built REST APIs in Python and FastAPI, deployed with Docker on AWS. Some React."
JD = "We need a backend engineer with Python, Kubernetes and PostgreSQL experience. AWS is a plus."


def make_pdf(text: str) -> bytes:
    """Minimal valid one-page PDF with a text line (correct xref offsets)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


async def _doc(client, h, kind, text, title="doc"):
    r = await client.post("/api/documents", json={"kind": kind, "title": title, "text": text}, headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _second_member(client, owner_headers):
    inv = (await client.post("/api/org/invites", json={"role": "candidate"}, headers=owner_headers)).json()
    t = (await client.post("/api/auth/accept-invite", json={
        "code": inv["code"], "full_name": "Other", "email": "other@acme.io", "password": "password123"})).json()
    return {"Authorization": f"Bearer {t['access_token']}"}


# ---------- documents ----------
def test_extract_text_pdf_and_errors():
    assert "Kubernetes engineer resume" in extract_text("cv.pdf", make_pdf("Kubernetes engineer resume with Python skills"))
    with pytest.raises(ExtractionError, match="PDF, .txt"):
        extract_text("cv.docx", b"PK\x03\x04 not supported")
    with pytest.raises(ExtractionError, match="No readable text"):
        extract_text("cv.txt", b"   ")


@pytest.mark.asyncio
async def test_documents_upload_list_privacy_delete(client, reg):
    h = await reg(client)
    pdf = make_pdf("Senior Python developer with Docker and AWS experience")
    up = await client.post("/api/documents/upload", headers=h, data={"kind": "resume"},
                           files={"file": ("cv.pdf", pdf, "application/pdf")})
    assert up.status_code == 201 and "Python developer" in up.json()["text"] and up.json()["title"] == "cv.pdf"
    bad = await client.post("/api/documents/upload", headers=h, data={"kind": "resume"},
                            files={"file": ("cv.docx", b"PK\x03\x04", "application/octet-stream")})
    assert bad.status_code == 422
    jd_id = await _doc(client, h, "job_description", JD, "Backend role")
    listing = (await client.get("/api/documents", headers=h)).json()
    assert {d["kind"] for d in listing} == {"resume", "job_description"} and "text" not in listing[0]
    # Another member of the same org can't read or delete my documents.
    other = await _second_member(client, h)
    assert (await client.get(f"/api/documents/{jd_id}", headers=other)).status_code == 404
    assert (await client.delete(f"/api/documents/{jd_id}", headers=other)).status_code == 404
    assert (await client.get("/api/documents", headers=other)).json() == []
    assert (await client.delete(f"/api/documents/{jd_id}", headers=h)).status_code == 204
    assert len((await client.get("/api/documents", headers=h)).json()) == 1


@pytest.mark.asyncio
async def test_fit_report(client, reg):
    h = await reg(client)
    rid, jid = await _doc(client, h, "resume", RESUME), await _doc(client, h, "job_description", JD)
    rep = (await client.post("/api/documents/fit", json={"resume_id": rid, "job_description_id": jid}, headers=h)).json()
    assert rep["matched_skills"] == ["python", "aws"] and rep["missing_skills"] == ["postgresql", "kubernetes"]
    assert rep["match_score"] == 50 and rep["likely_questions"] and rep["resume_suggestions"]
    # Wrong kinds are rejected (a resume passed as the job description).
    swapped = await client.post("/api/documents/fit", json={"resume_id": rid, "job_description_id": rid}, headers=h)
    assert swapped.status_code == 404


# ---------- tailored interviews ----------
@pytest.mark.asyncio
async def test_interview_tailored_to_resume_and_jd(client, reg):
    h = await reg(client)
    rid, jid = await _doc(client, h, "resume", RESUME), await _doc(client, h, "job_description", JD)
    iv = (await client.post("/api/interviews", json={"kind": "behavioral", "resume_id": rid, "job_description_id": jid},
                            headers=h)).json()
    assert iv["has_resume"] and iv["has_job_description"]
    assert "python on your resume" in iv["messages"][0]["content"]
    second = (await client.post(f"/api/interviews/{iv['id']}/messages", json={"content": "I built APIs."}, headers=h)).json()
    assert "This role needs postgresql" in second[1]["content"]
    # Someone else's resume can't be used.
    other = await _second_member(client, h)
    assert (await client.post("/api/interviews", json={"kind": "behavioral", "resume_id": rid}, headers=other)).status_code == 404


# ---------- code inside technical interviews ----------
@pytest.mark.asyncio
async def test_interview_code_editor_snapshot_and_run(client, reg):
    h = await reg(client)
    iv = (await client.post("/api/interviews", json={"kind": "technical"}, headers=h)).json()
    code = "def two_sum(nums, t):\n    seen = {}\n    for i, x in enumerate(nums):\n        if t - x in seen: return [seen[t - x], i]\n        seen[x] = i"
    msgs = (await client.post(f"/api/interviews/{iv['id']}/messages", headers=h, json={
        "content": "Here is my hash map approach.", "code": code, "code_language": "python"})).json()
    assert msgs[0]["code"] == code and "python code (5 lines)" in msgs[1]["content"]
    # Sending the same code again doesn't attach a duplicate snapshot.
    again = (await client.post(f"/api/interviews/{iv['id']}/messages", headers=h, json={
        "content": "It is O(n).", "code": code, "code_language": "python"})).json()
    assert again[0]["code"] is None
    state = (await client.get(f"/api/interviews/{iv['id']}", headers=h)).json()
    assert state["code"] == code and state["code_language"] == "python"
    run = (await client.post(f"/api/interviews/{iv['id']}/run", headers=h, json={
        "language": "python", "code": "print(sum(map(int, input().split())))", "stdin": "2 3"})).json()
    assert run["verdict"] == "accepted" and run["results"][0]["stdout"].strip() == "5"


# ---------- voice delivery metrics ----------
def test_delivery_metrics():
    assert delivery_metrics([]) is None
    m = delivery_metrics(["Um so basically I, uh, built it", "You know, um, it worked"])
    assert m["filler_words"] == 5 and m["top_fillers"][0] == "um" and m["voice_answers"] == 2
    assert any("filler" in n for n in m["notes"])


@pytest.mark.asyncio
async def test_voice_answers_get_delivery_feedback(client, reg):
    h = await reg(client)
    iv = (await client.post("/api/interviews", json={"kind": "behavioral"}, headers=h)).json()
    await client.post(f"/api/interviews/{iv['id']}/messages", headers=h,
                      json={"content": "Um, so basically, uh, I led the migration.", "input_mode": "voice"})
    await client.post(f"/api/interviews/{iv['id']}/messages", headers=h, json={"content": "Typed answer.", "input_mode": "text"})
    done = (await client.post(f"/api/interviews/{iv['id']}/end", headers=h)).json()
    assert done["scorecard"]["delivery"]["voice_answers"] == 1 and done["scorecard"]["delivery"]["filler_words"] == 3
    # Text-only interviews get no delivery section.
    iv2 = (await client.post("/api/interviews", json={"kind": "behavioral"}, headers=h)).json()
    await client.post(f"/api/interviews/{iv2['id']}/messages", headers=h, json={"content": "Typed."})
    assert "delivery" not in (await client.post(f"/api/interviews/{iv2['id']}/end", headers=h)).json()["scorecard"]


# ---------- spaced repetition ----------
def test_schedule_progression_and_lapse():
    now = datetime.now(timezone.utc)
    item = ReviewItem(ease=2.5, interval_days=0, reps=0, lapses=0, due_at=now)
    intervals = []
    for _ in range(3):
        schedule(item, True, item.due_at)  # solve exactly when due
        intervals.append(item.interval_days)
    assert intervals == [1, 3, 8.1]  # 3 * 2.7
    due_before = item.due_at
    schedule(item, True, now)  # early re-solve of a known problem: no change
    assert item.due_at == due_before and item.reps == 3
    schedule(item, False, item.due_at)
    assert item.reps == 0 and item.lapses == 1 and item.interval_days == 1 and item.ease == pytest.approx(2.6)


def test_streak():
    today = datetime(2026, 11, 19).date()
    d = lambda n: today - timedelta(days=n)  # noqa: E731
    assert streak_from_dates({d(0), d(1), d(2), d(4)}, today) == 3
    assert streak_from_dates({d(1), d(2)}, today) == 2  # not broken until today ends
    assert streak_from_dates({d(2)}, today) == 0


@pytest.mark.asyncio
async def test_study_plan(client, reg):
    h = await reg(client)
    probs = {p["title"]: p for p in (await client.get("/api/problems", headers=h)).json()}
    plan = (await client.get("/api/practice/plan", headers=h)).json()
    assert plan["streak_days"] == 0 and plan["due_reviews"] == [] and len(plan["new_problems"]) == 3
    assert plan["suggested_interview"]["kind"] == "behavioral"

    # Fail Two Sum (arrays, hash-map) and solve Reverse a String.
    two = probs["Two Sum"]["id"]
    await client.post(f"/api/problems/{two}/submit", json={"language": "python", "code": "print(0)"}, headers=h)
    rev = probs["Reverse a String"]["id"]
    await client.post(f"/api/problems/{rev}/submit", json={"language": "python",
                      "code": "import sys\nprint(sys.stdin.read().strip()[::-1])"}, headers=h)
    plan = (await client.get("/api/practice/plan", headers=h)).json()
    assert plan["streak_days"] == 1 and plan["practiced_today"] and plan["learned_count"] == 1
    assert plan["due_count"] == 0 and plan["next_review_at"]  # failed problem comes back tomorrow
    # The failed problem's tags (arrays, hash-map) rank matching problems first, explained in the reason.
    first = plan["new_problems"][0]
    assert "struggled" in first["reason"] and ("arrays" in first["reason"] or "hash-map" in first["reason"])
    assert first["title"] not in ("Two Sum", "Reverse a String")

    # Problems solved before review tracking existed (no ReviewItem) are not offered as "new".
    async with SessionLocal() as s:
        await s.execute(delete(ReviewItem).where(ReviewItem.problem_id == rev))
        await s.commit()
    titles = [p["title"] for p in (await client.get("/api/practice/plan", headers=h)).json()["new_problems"]]
    assert "Reverse a String" not in titles
    await client.post(f"/api/problems/{rev}/submit", json={"language": "python",
                      "code": "import sys\nprint(sys.stdin.read().strip()[::-1])"}, headers=h)

    # Time passes: make the reviews due.
    async with SessionLocal() as s:
        await s.execute(update(ReviewItem).values(due_at=datetime.now(timezone.utc) - timedelta(days=2)))
        await s.commit()
    plan = (await client.get("/api/practice/plan", headers=h)).json()
    assert plan["due_count"] == 2 and {r["title"] for r in plan["due_reviews"]} == {"Two Sum", "Reverse a String"}
    assert any("Not solved yet" in r["reason"] for r in plan["due_reviews"])

    # A completed interview drives the interview suggestion toward its weakest dimension.
    iv = (await client.post("/api/interviews", json={"kind": "system_design"}, headers=h)).json()
    await client.post(f"/api/interviews/{iv['id']}/messages", json={"content": "short"}, headers=h)
    await client.post(f"/api/interviews/{iv['id']}/end", headers=h)
    sug = (await client.get("/api/practice/plan", headers=h)).json()["suggested_interview"]
    assert sug["kind"] == "system_design" and sug["focus"] and "weakest" in sug["reason"]
