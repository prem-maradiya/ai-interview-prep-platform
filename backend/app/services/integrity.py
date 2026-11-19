"""Integrity signals for submissions: hints for mentors to look closer, never automatic verdicts.

Signals:
  - mostly_pasted : most of the submitted code arrived via paste
  - tab_switching : the candidate left the tab many times while solving
  - very_fast     : a non-easy problem solved in under a minute
  - similar_code  : near-identical (after normalisation) to another member's accepted submission
"""
import builtins
import io
import keyword
import re
import tokenize
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Submission, User

PASTE_RATIO = 0.6
MIN_CODE_FOR_PASTE = 120
TAB_SWITCHES = 5
FAST_SECONDS = 60
SIMILARITY = 0.9
MIN_NORMALIZED_LEN = 80  # tiny solutions all look alike; don't compare them

_JS_KEYWORDS = set(
    "break case catch class const continue debugger default delete do else export extends finally for function if "
    "import in instanceof let new return super switch this throw try typeof var void while with yield async await of "
    "true false null undefined console log require".split()
)
_WORD = re.compile(r"[A-Za-z_$][\w$]*")
_PY_BUILTINS = set(dir(builtins))


def normalize(code: str, language: str) -> str:
    """Strip comments/whitespace and rename identifiers, so renaming variables doesn't hide copying."""
    if language == "python":
        try:
            out = []
            for tok in tokenize.generate_tokens(io.StringIO(code).readline):
                if tok.type in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
                    continue
                if tok.type == tokenize.NAME and not keyword.iskeyword(tok.string) and tok.string not in _PY_BUILTINS:
                    out.append("v")
                elif tok.type == tokenize.STRING:
                    out.append("s")
                else:
                    out.append(tok.string)
            return " ".join(out)
        except (tokenize.TokenError, IndentationError, SyntaxError):
            pass  # fall through to the generic normaliser
    code = re.sub(r"//[^\n]*|/\*.*?\*/|#[^\n]*", " ", code, flags=re.S)
    code = re.sub(r"(['\"`]).*?\1", "s", code)
    code = _WORD.sub(lambda m: m.group(0) if m.group(0) in _JS_KEYWORDS or keyword.iskeyword(m.group(0)) else "v", code)
    return " ".join(code.split())


def telemetry_flags(code: str, telemetry: dict | None, verdict: str, difficulty: str) -> list[dict]:
    t = telemetry or {}
    flags = []
    pasted = int(t.get("paste_chars") or 0)
    if len(code) >= MIN_CODE_FOR_PASTE and pasted >= PASTE_RATIO * len(code):
        flags.append({"type": "mostly_pasted", "severity": "medium",
                      "detail": f"{min(100, round(pasted / len(code) * 100))}% of the code was pasted in"})
    switches = int(t.get("tab_switches") or 0)
    if switches >= TAB_SWITCHES:
        flags.append({"type": "tab_switching", "severity": "low", "detail": f"Left the tab {switches} times while solving"})
    spent = t.get("time_spent_s")
    if verdict == "accepted" and difficulty != "easy" and spent is not None and 0 <= float(spent) < FAST_SECONDS:
        flags.append({"type": "very_fast", "severity": "low",
                      "detail": f"Solved a {difficulty} problem {int(float(spent))}s after opening it"})
    return flags


async def similarity_flag(db: AsyncSession, sub: Submission) -> dict | None:
    """Compare an accepted submission with other members' accepted submissions of the same problem."""
    mine = normalize(sub.code, sub.language)
    if sub.verdict != "accepted" or len(mine) < MIN_NORMALIZED_LEN:
        return None
    rows = (await db.execute(
        select(Submission.code, Submission.language, User.full_name)
        .join(User, User.id == Submission.user_id)
        .where(Submission.tenant_id == sub.tenant_id, Submission.problem_id == sub.problem_id,
               Submission.user_id != sub.user_id, Submission.verdict == "accepted",
               Submission.language == sub.language)
        .order_by(Submission.created_at.desc()).limit(200)
    )).all()
    best, best_name = 0.0, None
    for code, language, name in rows:
        other = normalize(code, language)
        if len(other) < MIN_NORMALIZED_LEN:
            continue
        ratio = SequenceMatcher(None, mine, other, autojunk=False).ratio()
        if ratio > best:
            best, best_name = ratio, name
    if best >= SIMILARITY:
        return {"type": "similar_code", "severity": "high",
                "detail": f"{round(best * 100)}% similar to {best_name}'s accepted submission (after renaming variables)"}
    return None
