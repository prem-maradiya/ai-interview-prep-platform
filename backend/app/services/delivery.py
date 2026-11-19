"""Speaking-delivery metrics for voice answers. Deterministic (no LLM), so it's free and testable."""
import re
from collections import Counter

FILLERS = ["um", "uh", "erm", "hmm", "you know", "basically", "i mean", "sort of", "kind of", "literally"]
_FILLER_RE = re.compile(r"\b(" + "|".join(re.escape(f) for f in sorted(FILLERS, key=len, reverse=True)) + r")\b")


def delivery_metrics(voice_answers: list[str]) -> dict | None:
    if not voice_answers:
        return None
    word_counts = [len(a.split()) for a in voice_answers]
    total_words = sum(word_counts) or 1
    fillers = Counter(m.group(1) for a in voice_answers for m in _FILLER_RE.finditer(a.lower()))
    filler_total = sum(fillers.values())
    per_100 = round(filler_total / total_words * 100, 1)
    avg = round(total_words / len(voice_answers))
    notes = []
    if per_100 > 4:
        notes.append(f"You used {filler_total} filler words ({per_100} per 100 words). Pause silently instead of filling gaps.")
    if avg < 40:
        notes.append("Spoken answers were short on average - add context, your actions and the result.")
    elif avg > 220:
        notes.append("Spoken answers ran long - lead with the headline, then give detail if asked.")
    if not notes:
        notes.append("Good pacing and few filler words - keep it up.")
    return {
        "voice_answers": len(voice_answers),
        "avg_words_per_answer": avg,
        "filler_words": filler_total,
        "fillers_per_100_words": per_100,
        "top_fillers": [w for w, _ in fillers.most_common(3)],
        "notes": notes,
    }
