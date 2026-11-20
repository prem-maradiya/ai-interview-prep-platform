"""Integration tests against a real Docker daemon, for every language. Skipped when Docker isn't reachable.

The first run builds the sandbox images (a few minutes); after that they're cached by content hash.
"""
import pytest

from app.services.sandbox import DockerSandbox, grade

docker = pytest.importorskip("docker")
try:
    docker.from_env().ping()
except Exception:  # noqa: BLE001
    pytest.skip("Docker daemon not available", allow_module_level=True)

pytestmark = pytest.mark.asyncio

REVERSE = {
    "python": "import sys\nprint(sys.stdin.read().strip()[::-1])",
    "javascript": "const s=require('fs').readFileSync(0,'utf8').trim();console.log(s.split('').reverse().join(''))",
    "java": (
        "import java.io.*;\npublic class Main { public static void main(String[] a) throws IOException {\n"
        "  String s = new String(System.in.readAllBytes()).trim();\n"
        "  System.out.println(new StringBuilder(s).reverse()); } }"
    ),
    "cpp": (
        "#include <bits/stdc++.h>\nusing namespace std;\nint main(){ string s; getline(cin, s);\n"
        "  reverse(s.begin(), s.end()); cout << s << endl; }"
    ),
    "go": (
        "package main\nimport (\"bufio\";\"fmt\";\"os\";\"strings\")\nfunc main(){\n"
        "  r := bufio.NewReader(os.Stdin); s, _ := r.ReadString('\\n'); s = strings.TrimSpace(s)\n"
        "  b := []rune(s); for i, j := 0, len(b)-1; i < j; i, j = i+1, j-1 { b[i], b[j] = b[j], b[i] }\n"
        "  fmt.Println(string(b)) }"
    ),
}
BROKEN = {
    "java": "public class Main { public static void main(String[] a) { int x = } }",
    "cpp": "int main() { return undefined_thing; }",
    "go": "package main\nfunc main() { x := }",
}
BUSY_LOOP = {  # CPU-bound forever: must hit the CPU-time limit
    "python": "while True: pass",
    "javascript": "for(;;){}",
    "java": "public class Main { public static void main(String[] a) { long i = 0; while (true) { i++; } } }",
    "cpp": "int main() { volatile unsigned long i = 0; for(;;) { i++; } }",
    "go": "package main\nfunc main() { i := 0; for { i++ } }",
}


@pytest.fixture(scope="module")
def sandbox():
    sb = DockerSandbox()
    sb.ensure_images()
    yield sb
    sb.close()


@pytest.mark.parametrize("lang", list(REVERSE))
async def test_correct_and_wrong_answer(sandbox, lang):
    raw = await sandbox.execute(lang, REVERSE[lang], ["abc", "hey"])
    g = grade(raw, ["cba", "nope"], [False, True], ["abc", "hey"])
    assert [r["status"] for r in g["results"]] == ["passed", "wrong_answer"], raw


@pytest.mark.parametrize("lang", list(BROKEN))
async def test_compile_error_reported_with_compiler_output(sandbox, lang):
    raw = await sandbox.execute(lang, BROKEN[lang], ["x", "y"])
    g = grade(raw, ["1", "2"], [False, True], ["x", "y"])
    assert g["verdict"] == "compile_error" and g["passed"] == 0
    assert g["results"][1]["stderr"]  # compiler output shown even for the hidden test


@pytest.mark.parametrize("lang", list(BUSY_LOOP))
async def test_cpu_bound_loop_hits_cpu_time_limit(sandbox, lang):
    raw = await sandbox.execute(lang, BUSY_LOOP[lang], [""])
    assert raw[0].status == "timeout", raw


async def test_sleeping_program_hits_wall_limit_with_little_cpu(sandbox):
    # Sleeping uses no CPU, so only the wall-clock limit can stop it - and its CPU time stays tiny.
    raw = await sandbox.execute("python", "import time\ntime.sleep(60)", [""])
    assert raw[0].status == "timeout" and raw[0].time_ms < 1000


async def test_cpu_time_reported_not_wall_time(sandbox):
    raw = await sandbox.execute("python", "import time\ntime.sleep(1.5)\nprint('done')", [""])
    assert raw[0].status == "ok" and raw[0].time_ms < 500  # slept 1.5s, but used almost no CPU


async def test_memory_limit(sandbox):
    # Allocate and touch 64 MB at a time: the container's OOM killer stops it well before the CPU limit.
    code = ("#include <cstdlib>\n#include <cstring>\n"
            "int main(){ for(;;){ char* p = (char*)malloc(64 << 20); if (!p) return 3; memset(p, 1, 64 << 20); } }")
    raw = await sandbox.execute("cpp", code, [""])
    assert grade(raw, ["x"], [False], [""])["verdict"] == "memory_limit_exceeded", raw


async def test_network_filesystem_and_user_isolation(sandbox):
    code = (
        "import socket, os\n"
        "try:\n    socket.create_connection(('1.1.1.1', 80), timeout=1); print('NET')\nexcept OSError: print('nonet')\n"
        "try:\n    open('/etc/pwned', 'w'); print('FS')\nexcept OSError: print('rofs')\n"
        "print(os.getuid())\n"
    )
    raw = await sandbox.execute("python", code, [""])
    assert raw[0].stdout.split() == ["nonet", "rofs", "65534"]


async def test_fork_bomb_is_contained(sandbox):
    code = "import os\nwhile True:\n    try: os.fork()\n    except OSError: pass"
    raw = await sandbox.execute("python", code, [""])
    assert raw[0].status in ("timeout", "runtime_error", "memory")  # contained by pids + CPU limits


# ---------- warm pool ----------
FRESH_CHECK = (
    "import os\n"
    "print('reused' if os.path.exists('/tmp/marker') else 'fresh')\n"
    "open('/tmp/marker', 'w').write('x')\n"
)


def _wait_for_idle(sb, lang, timeout=20.0):
    import time

    end = time.monotonic() + timeout
    while sb.pool.idle_count(lang) < 1 and time.monotonic() < end:
        time.sleep(0.1)
    assert sb.pool.idle_count(lang) >= 1


async def test_warm_pool_serves_runs_and_never_reuses_a_container():
    sb = DockerSandbox(warm_pool=1)
    try:
        sb.warm_up(["python"])
        outputs = []
        for _ in range(3):
            _wait_for_idle(sb, "python")
            raw = await sb.execute("python", FRESH_CHECK, [""])
            outputs.append(raw[0].stdout.strip())
        assert outputs == ["fresh"] * 3  # files written by one run are never seen by the next
        assert sb.pool.hits == 3 and sb.pool.misses == 0
    finally:
        sb.close()


async def test_warm_pool_keeps_limits_and_falls_back_when_container_died():
    sb = DockerSandbox(warm_pool=1)
    try:
        sb.warm_up(["python"])
        raw = await sb.execute("python", BUSY_LOOP["python"], [""])
        assert raw[0].status == "timeout"  # CPU limit still enforced in a warm container
        _wait_for_idle(sb, "python")
        # Kill the idle container behind the pool's back: the run must still succeed (cold start).
        with sb.pool._lock:
            sb.pool._idle["python"][0][0].remove(force=True)
        raw = await sb.execute("python", REVERSE["python"], ["abc"])
        assert raw[0].status == "ok" and raw[0].stdout.strip() == "cba"
    finally:
        sb.close()


async def test_closing_the_pool_removes_idle_containers():
    sb = DockerSandbox(warm_pool=2)
    sb.warm_up(["python"])
    ids = [c.id for c, _, _ in sb.pool._idle["python"]]
    assert len(ids) == 2
    sb.close()
    running = {c.id for c in docker.from_env().containers.list(filters={"label": "prepforge.sandbox=warm"})}
    assert not running.intersection(ids)


async def test_close_also_removes_containers_that_were_still_starting():
    sb = DockerSandbox(warm_pool=1)
    sb.warm_up(["python"])
    await sb.execute("python", REVERSE["python"], ["abc"])  # triggers a background refill
    sb.close()  # right away, while the replacement may still be starting
    assert sb.pool.idle_count("python") == 0
    mine = docker.from_env().containers.list(filters={"label": ["prepforge.sandbox=warm", f"prepforge.owner={sb.pool.owner}"]})
    assert mine == []


# ---------- function-style problems: generated drivers in every language ----------
from app.services import functions  # noqa: E402


def _sig(fn, params, returns, compare="exact"):
    return functions.Signature(function=fn, params=[{"name": n, "type": t} for n, t in params], returns=returns,
                               compare=compare)


ECHO_TYPES = ["int[][]", "string[]", "bool[]", "long", "float[][]", "string"]
ECHO_VALUES = {
    "int[][]": ["[[1,-2,3],[],[2147483647]]", "[]"],
    # JSON escapes (quote, backslash, tab, newline) plus non-ASCII text, written raw so Python leaves them alone.
    "string[]": [r'["", "a b", "quote\"back\\slash", "\u00e9t\u00e9 \u2713", "tab\tnew\nline"]', "[]"],
    "bool[]": ["[true,false,true]", "[]"],
    "long": ["9007199254740993", "-5"],
    "float[][]": ["[[0.5,-1.25],[3.0]]", "[[]]"],
    "string": ['"h\u00e9llo, w\u00f6rld"', '""'],
}


def _echo_code(lang: str, t: str) -> str:
    sig = _sig("echo", [("value", t)], t)
    starter = functions.starter_code(sig)[lang]
    body = {"python": "    return value\n", "javascript": "  return value;\n", "java": "        return value;\n",
            "cpp": "        return value;\n", "go": "\treturn value\n"}[lang]
    lines = [ln for ln in starter.splitlines(keepends=True) if "your code here" not in ln and "return " not in ln and ln.strip() != "pass"]
    out, inserted = [], False
    for ln in lines:
        out.append(ln)
        if not inserted and ("def echo" in ln or "function echo" in ln or " echo(" in ln or "func echo" in ln):
            out.append(body)
            inserted = True
    return "".join(out)


@pytest.mark.parametrize("lang", list(REVERSE))
async def test_function_drivers_round_trip_every_type(sandbox, lang):
    """Each type goes in through the driver's reader and comes back out through its JSON writer."""
    if lang == "javascript":
        pytest.skip("JavaScript numbers can't hold 64-bit longs exactly; covered by the other types below")
    for t in ECHO_TYPES:
        sig = _sig("echo", [("value", t)], t)
        code, extra = functions.prepare(sig, lang, _echo_code(lang, t))
        inputs = ECHO_VALUES[t]
        raw = await sandbox.execute(lang, code, inputs, extra, sig.types)
        g = grade(raw, inputs, [False] * len(inputs), inputs, sig)
        assert g["verdict"] == "accepted", (lang, t, code[-400:], raw)


async def test_function_driver_javascript_types(sandbox):
    for t in ["int[][]", "string[]", "bool[]", "float[][]", "string"]:
        sig = _sig("echo", [("value", t)], t)
        code, extra = functions.prepare(sig, "javascript", _echo_code("javascript", t))
        raw = await sandbox.execute("javascript", code, ECHO_VALUES[t], extra, sig.types)
        g = grade(raw, ECHO_VALUES[t], [False, False], ECHO_VALUES[t], sig)
        assert g["verdict"] == "accepted", (t, raw)


TWO_SUM_FN = {
    "python": "def twoSum(nums, target):\n    seen = {}\n    for i, x in enumerate(nums):\n"
              "        if target - x in seen:\n            return [seen[target - x], i]\n        seen[x] = i\n",
    "javascript": "function twoSum(nums, target) {\n  const seen = new Map();\n  for (let i = 0; i < nums.length; i++) {\n"
                  "    if (seen.has(target - nums[i])) return [seen.get(target - nums[i]), i];\n    seen.set(nums[i], i);\n  }\n}\n",
    "java": "import java.util.*;\nclass Solution {\n  public int[] twoSum(int[] nums, int target) {\n"
            "    Map<Integer, Integer> seen = new HashMap<>();\n    for (int i = 0; i < nums.length; i++) {\n"
            "      Integer j = seen.get(target - nums[i]);\n      if (j != null) return new int[]{j, i};\n"
            "      seen.put(nums[i], i);\n    }\n    return new int[0];\n  }\n}\n",
    "cpp": "#include <bits/stdc++.h>\nusing namespace std;\nclass Solution {\npublic:\n"
           "  vector<int> twoSum(vector<int>& nums, int target) {\n    unordered_map<int, int> seen;\n"
           "    for (int i = 0; i < (int) nums.size(); i++) {\n      auto it = seen.find(target - nums[i]);\n"
           "      if (it != seen.end()) return {it->second, i};\n      seen[nums[i]] = i;\n    }\n    return {};\n  }\n};\n",
    "go": "package main\n\nimport \"fmt\"\n\nfunc twoSum(nums []int, target int) []int {\n\tfmt.Println(\"debug:\", len(nums))\n"
          "\tseen := map[int]int{}\n\tfor i, x := range nums {\n\t\tif j, ok := seen[target-x]; ok {\n"
          "\t\t\treturn []int{j, i}\n\t\t}\n\t\tseen[x] = i\n\t}\n\treturn nil\n}\n",
}


@pytest.mark.parametrize("lang", list(TWO_SUM_FN))
async def test_function_problem_with_debug_prints_and_large_input(sandbox, lang):
    sig = _sig("twoSum", [("nums", "int[]"), ("target", "int")], "int[]")
    from app.seed_functions import gen

    big = gen({"ints": 100_000, "min": -10**9, "max": 10**9, "mod": 4, "residue": 0, "seed": 11, "json": True,
               "insert": [[31_337, 493_829], [88_888, -9_378]]}, {"value": 484451})
    inputs = ["[2,7,11,15]\n9", "[3,2,4]\n6", big]
    code, extra = functions.prepare(sig, lang, TWO_SUM_FN[lang])
    raw = await sandbox.execute(lang, code, inputs, extra, sig.types)
    g = grade(raw, ["[0,1]", "[1,2]", "[31337,88888]"], [False, False, True], inputs, sig)
    assert g["verdict"] == "accepted", (lang, raw)
    assert g["results"][0]["stdout"] == "[0,1]"
    if lang == "go":
        assert g["results"][0]["printed"] == "debug: 4"  # the candidate's own prints are kept apart


async def test_quadratic_solution_is_too_slow_on_large_input(sandbox):
    sig = _sig("containsDuplicate", [("nums", "int[]")], "bool")
    from app.seed_functions import gen

    big = gen({"ints": 200_000, "min": -10**9, "max": 10**9, "distinct": True, "seed": 21, "json": True})
    quadratic = ("def containsDuplicate(nums):\n    for i in range(len(nums)):\n        for j in range(i + 1, len(nums)):\n"
                 "            if nums[i] == nums[j]:\n                return True\n    return False\n")
    linear = "def containsDuplicate(nums):\n    return len(set(nums)) != len(nums)\n"
    inputs, expected, hidden = ["[1,2,3,1]", "[1,2,3]", big], ["true", "false", "false"], [False, False, True]
    for code, verdict in ((quadratic, "too_slow"), (linear, "accepted")):
        full, extra = functions.prepare(sig, "python", code)
        g = grade(await sandbox.execute("python", full, inputs, extra, sig.types), expected, hidden, inputs, sig)
        assert g["verdict"] == verdict, g["results"]
    assert g["results"][2]["large"] and "200,000 integers" in g["results"][2]["input"]
