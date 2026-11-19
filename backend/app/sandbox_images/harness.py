"""Runs untrusted code inside a sandbox container. Same script for every language image.

Input: env PAYLOAD = base64(JSON {lang, code, tests: [stdin...], cpu_limit, wall_limit, output_limit}).
Output: one line "@@RESULT@@" + JSON list (one entry per test), or a compile error for all tests.

Time is measured as CPU time (user + sys) of the child, and limited with RLIMIT_CPU by the kernel.
That keeps verdicts fair on any machine: a busy or slow host makes wall-clock time longer, but
barely changes CPU time. A separate, generous wall-clock limit catches programs that block or sleep.
"""
import base64
import json
import math
import os
import resource
import shutil
import signal
import subprocess
import sys

MARKER = "@@RESULT@@"

LANGS = {
    "python": {"file": "sol.py", "compile": None, "run": [sys.executable, "-S", "sol.py"]},
    "javascript": {"file": "sol.js", "compile": None, "run": ["node", "--max-old-space-size=200", "sol.js"]},
    "java": {
        "file": "Main.java",
        "compile": ["javac", "-J-Xmx256m", "-J-XX:+UseSerialGC", "-J-XX:TieredStopAtLevel=1", "Main.java"],
        "run": ["java", "-Xmx256m", "-Xss64m", "-XX:+UseSerialGC", "-XX:TieredStopAtLevel=1", "-cp", ".", "Main"],
    },
    "cpp": {"file": "sol.cpp", "compile": ["g++", "-O2", "-std=c++17", "-pipe", "-o", "sol", "sol.cpp"], "run": ["./sol"]},
    "go": {"file": "main.go", "compile": ["go", "build", "-o", "sol", "main.go"], "run": ["./sol"]},
}

CHILD_ENV = {
    "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    "HOME": "/tmp",
    "LANG": "C.UTF-8",
    "PYTHONDONTWRITEBYTECODE": "1",
    "GOCACHE": "/tmp/gocache",
    "GOPATH": "/tmp/gopath",
    "GOTOOLCHAIN": "local",
    "CGO_ENABLED": "0",
}
PREBUILT_GO_CACHE = "/opt/gocache"  # baked into the Go image (see go.Dockerfile)


def cpu_seconds() -> float:
    r = resource.getrusage(resource.RUSAGE_CHILDREN)
    return r.ru_utime + r.ru_stime


def memory_events() -> tuple[int, int]:
    """(times memory hit the container limit, OOM kills) from cgroup v2; (0, 0) if unavailable.

    Near the limit the kernel reclaims memory before OOM-killing, and that time counts as the
    program's CPU time - so a memory hog can trip the CPU limit first. These counters tell us
    the real cause.
    """
    try:
        with open("/sys/fs/cgroup/memory.events") as f:
            events = dict(line.split() for line in f if line.strip())
        return int(events.get("max", 0)), int(events.get("oom_kill", 0))
    except (OSError, ValueError):
        return 0, 0


def run(cmd, stdin: bytes, cpu_limit: float, wall_limit: float, limit: int):
    """Run one process with a CPU-time rlimit. Returns (status, stdout, stderr, cpu_ms)."""
    hard = max(1, math.ceil(cpu_limit))

    def preexec():
        resource.setrlimit(resource.RLIMIT_CPU, (hard, hard + 1))  # SIGXCPU at soft, SIGKILL at hard
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        os.setsid()  # own process group, so a wall-clock kill takes children too

    before = cpu_seconds()
    mem_before = memory_events()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd="/tmp/work", env=CHILD_ENV, preexec_fn=preexec)
    wall_killed = False
    try:
        out, err = proc.communicate(stdin, timeout=wall_limit)
    except subprocess.TimeoutExpired:
        wall_killed = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = proc.communicate()
    used = cpu_seconds() - before
    rc = proc.returncode
    mem_after = memory_events()
    hit_memory_limit = mem_after[0] > mem_before[0] or mem_after[1] > mem_before[1]
    if rc != 0 and hit_memory_limit:
        status = "memory"  # it reached the memory limit; any timeout was a side effect of reclaim
    elif wall_killed or used > cpu_limit or rc in (-signal.SIGXCPU,):
        status = "timeout"
    elif rc == -signal.SIGKILL:
        status = "memory"  # killed without hitting CPU/wall limits: the container's OOM killer
    elif rc != 0:
        status = "runtime_error"
    else:
        status = "ok"
    return status, out[:limit].decode(errors="replace"), err[-limit:].decode(errors="replace"), int(used * 1000)


def main() -> None:
    p = json.loads(base64.b64decode(os.environ.pop("PAYLOAD")))
    spec = LANGS[p["lang"]]
    limit = p["output_limit"]
    os.makedirs("/tmp/work", exist_ok=True)
    with open(os.path.join("/tmp/work", spec["file"]), "w") as f:
        f.write(p["code"])
    if p["lang"] == "go" and os.path.isdir(PREBUILT_GO_CACHE):
        shutil.copytree(PREBUILT_GO_CACHE, CHILD_ENV["GOCACHE"], dirs_exist_ok=True)

    if spec["compile"]:
        status, _, err, _ = run(spec["compile"], b"", p["compile_cpu_limit"], p["compile_wall_limit"], limit)
        if status != "ok":
            detail = err.strip() or ("Compilation timed out" if status == "timeout" else "Compilation failed")
            print(MARKER + json.dumps([{"status": "compile_error", "stdout": "", "stderr": detail[-4000:], "time_ms": 0}
                                       for _ in p["tests"]]))
            return

    results = []
    for stdin in p["tests"]:
        status, out, err, cpu_ms = run(spec["run"], stdin.encode(), p["cpu_limit"], p["wall_limit"], limit)
        results.append({"status": status, "stdout": out, "stderr": err, "time_ms": cpu_ms})
    print(MARKER + json.dumps(results))


if __name__ == "__main__":
    main()
