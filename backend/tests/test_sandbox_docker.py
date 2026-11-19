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
    return sb


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
