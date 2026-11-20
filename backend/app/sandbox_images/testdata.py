"""Test-input helpers shared by the sandbox runner (inside every image) and the server.

1. Generated inputs. A test whose input starts with "@gen " holds a small JSON spec instead of the
   data itself, e.g. 200,000 random integers. It is expanded inside the sandbox, so huge inputs
   never travel through the job queue (or hit the 128 KB limit on one environment variable).

   {"lines": [<part>, ...]}  - one output line per part:
     {"ints": n, "min": a, "max": b, "seed": s,       n random integers in [min, max]
      "mod": m, "residue": r,                          optional: every value = r (mod m)
      "distinct": true,                                optional: no repeated values
      "insert": [[index, value], ...],                 optional: overwrite these positions
      "json": true}                                    optional: "[1,2,3]" instead of "1 2 3"
     {"chars": n, "alphabet": "abc", "seed": s,        n random characters
      "json": true}                                    optional: as a JSON string
     {"value": v}                                      a literal: str as-is, else JSON

2. Function-style problems. Their tests hold one JSON value per line (one per argument). For the
   generated driver programs, which can't easily parse JSON in every language, these are turned
   into a flat stream of whitespace-separated tokens:
     int/long/float -> the number; bool -> 1/0; string -> "s" + hex of its UTF-8 bytes;
     T[] -> length, then each element; T[][] -> rows, then each row as a T[].
"""
import json
import random

GEN_PREFIX = "@gen "
SCALARS = {"int", "long", "float", "bool", "string"}


def is_generated(text: str) -> bool:
    return text.startswith(GEN_PREFIX)


def _ints(part: dict) -> list[int]:
    rng = random.Random(part.get("seed", 0))
    n, lo, hi = int(part["ints"]), int(part.get("min", 0)), int(part.get("max", 10**9))
    mod, res = int(part.get("mod", 1)), int(part.get("residue", 0))
    if part.get("distinct"):  # no repeated values (sampled without replacement)
        values = rng.sample(range(lo, hi + 1), n)
    elif mod > 1:  # draw multiples of mod, then shift to the residue class
        values = [rng.randint((lo - res) // mod + 1, (hi - res) // mod) * mod + res for _ in range(n)]
    else:
        values = [rng.randint(lo, hi) for _ in range(n)]
    for index, value in part.get("insert", []):
        values[index] = value
    return values


def _line(part: dict) -> str:
    if "ints" in part:
        values = _ints(part)
        return json.dumps(values, separators=(",", ":")) if part.get("json") else " ".join(map(str, values))
    if "chars" in part:
        rng = random.Random(part.get("seed", 0))
        text = "".join(rng.choice(part["alphabet"]) for _ in range(int(part["chars"])))
        return json.dumps(text) if part.get("json") else text
    if "value" in part:
        v = part["value"]
        return v if isinstance(v, str) else json.dumps(v, separators=(",", ":"))
    raise ValueError(f"unknown generator part: {sorted(part)}")


def expand(text: str) -> str:
    """The real input for a test: generated inputs are expanded, anything else returned unchanged."""
    if not is_generated(text):
        return text
    spec = json.loads(text[len(GEN_PREFIX):])
    return "\n".join(_line(p) for p in spec["lines"])


# ---------- function-style problems ----------
def _check(value, typ: str):
    if typ.endswith("[]"):
        if not isinstance(value, list):
            raise ValueError(f"expected a list for {typ}")
        return [_check(v, typ[:-2]) for v in value]
    if typ in ("int", "long"):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"expected an integer, got {json.dumps(value)}")
    elif typ == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"expected a number, got {json.dumps(value)}")
    elif typ == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"expected true/false, got {json.dumps(value)}")
    elif typ == "string":
        if not isinstance(value, str):
            raise ValueError(f"expected a string, got {json.dumps(value)}")
    return value


def _tokens(value, typ: str, out: list[str]) -> None:
    if typ.endswith("[]"):
        out.append(str(len(value)))
        for v in value:
            _tokens(v, typ[:-2], out)
    elif typ == "bool":
        out.append("1" if value else "0")
    elif typ == "string":
        out.append("s" + value.encode("utf-8").hex())
    elif typ == "float":
        out.append(repr(float(value)))
    else:
        out.append(str(int(value)))


def parse_args(text: str, param_types: list[str]) -> list:
    """Parse and type-check a function test input: one JSON value per line, one line per parameter."""
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    if len(lines) != len(param_types):
        raise ValueError(f"expected {len(param_types)} line(s), one JSON value per parameter, got {len(lines)}")
    args = []
    for i, (line, typ) in enumerate(zip(lines, param_types)):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            raise ValueError(f"line {i + 1} isn't valid JSON: {line[:60]}")
        args.append(_check(value, typ))
    return args


def to_tokens(text: str, param_types: list[str]) -> str:
    out: list[str] = []
    for value, typ in zip(parse_args(text, param_types), param_types):
        _tokens(value, typ, out)
    return " ".join(out) + "\n"
