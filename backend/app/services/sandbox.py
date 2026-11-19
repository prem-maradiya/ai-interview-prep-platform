"""Isolated code execution in throwaway Docker containers.

Each run gets a fresh container with:
  - no network, read-only root filesystem, a small tmpfs for /tmp
  - memory / CPU / PID limits, all capabilities dropped, no-new-privileges
  - non-root user (nobody), optional gVisor runtime (SANDBOX_RUNTIME=runsc)
  - a per-test timeout inside the container plus a hard wall-clock timeout outside it

Code and test *inputs* are passed in through an environment variable (base64 JSON), so no
bind mounts are needed and it works when the API itself runs inside Docker. Expected outputs
never enter the container: grading happens here, on the host side.
"""
import asyncio
import base64
import json
from dataclasses import dataclass

import anyio

from app.core.config import get_settings

OUTPUT_LIMIT = 64 * 1024
RESULT_MARKER = "@@RESULT@@"

PY_HARNESS = r"""
import base64, json, os, subprocess, sys, time
p = json.loads(base64.b64decode(os.environ.pop("PAYLOAD")))
with open("/tmp/sol.py", "w") as f:
    f.write(p["code"])
out, LIM = [], p["limit"]
for t in p["tests"]:
    s = time.perf_counter()
    try:
        r = subprocess.run([sys.executable, "/tmp/sol.py"], input=t.encode(), capture_output=True,
                           timeout=p["timeout"], env={"PYTHONDONTWRITEBYTECODE": "1"})
        st, o, e = ("ok" if r.returncode == 0 else "runtime_error"), r.stdout, r.stderr
    except subprocess.TimeoutExpired as x:
        st, o, e = "timeout", (x.stdout or b""), b""
    out.append({"status": st, "stdout": o[:LIM].decode(errors="replace"),
                "stderr": e[-LIM:].decode(errors="replace"), "time_ms": int((time.perf_counter() - s) * 1000)})
print("@@RESULT@@" + json.dumps(out))
"""

JS_HARNESS = r"""
const fs = require('fs'), cp = require('child_process');
const p = JSON.parse(Buffer.from(process.env.PAYLOAD, 'base64').toString());
delete process.env.PAYLOAD;
fs.writeFileSync('/tmp/sol.js', p.code);
const out = [];
for (const t of p.tests) {
  const s = Date.now();
  const r = cp.spawnSync(process.execPath, ['/tmp/sol.js'], {input: t, timeout: p.timeout * 1000, maxBuffer: 4 * 1024 * 1024, env: {}});
  let st = 'ok';
  if ((r.error && r.error.code === 'ETIMEDOUT') || r.signal === 'SIGTERM') st = 'timeout';
  else if (r.error || r.status !== 0) st = 'runtime_error';
  out.push({status: st, stdout: (r.stdout || '').toString().slice(0, p.limit),
            stderr: (r.stderr || '').toString().slice(-p.limit), time_ms: Date.now() - s});
}
console.log('@@RESULT@@' + JSON.stringify(out));
"""

LANGUAGES = {
    "python": {"image": "python:3.12-alpine", "cmd": ["python3", "-c", PY_HARNESS]},
    "javascript": {"image": "node:22-alpine", "cmd": ["node", "-e", JS_HARNESS]},
}


class SandboxError(RuntimeError):
    pass


@dataclass
class RawResult:
    status: str  # ok | runtime_error | timeout
    stdout: str
    stderr: str
    time_ms: int


class Sandbox:
    async def execute(self, language: str, code: str, inputs: list[str]) -> list[RawResult]:
        raise NotImplementedError


class DockerSandbox(Sandbox):
    def __init__(self) -> None:
        self.settings = get_settings()
        self._sem = asyncio.Semaphore(self.settings.sandbox_max_concurrency)
        self._client = None

    def _docker(self):
        if self._client is None:
            import docker

            self._client = docker.from_env()
        return self._client

    def _run_blocking(self, language: str, code: str, inputs: list[str]) -> list[RawResult]:
        import docker.errors
        import requests.exceptions

        s = self.settings
        spec = LANGUAGES[language]
        payload = base64.b64encode(json.dumps({
            "code": code, "tests": inputs, "timeout": s.sandbox_test_timeout_s, "limit": OUTPUT_LIMIT,
        }).encode()).decode()
        try:
            client = self._docker()
            container = client.containers.run(
                spec["image"], spec["cmd"], environment={"PAYLOAD": payload}, detach=True,
                network_mode="none", mem_limit=s.sandbox_memory, memswap_limit=s.sandbox_memory,
                nano_cpus=int(s.sandbox_cpus * 1e9), pids_limit=s.sandbox_pids, read_only=True,
                tmpfs={"/tmp": "rw,size=16m,mode=1777"}, user="65534:65534", cap_drop=["ALL"],
                security_opt=["no-new-privileges"], runtime=s.sandbox_runtime, working_dir="/tmp",
            )
        except docker.errors.DockerException as e:
            raise SandboxError(f"Sandbox unavailable: {e}") from e
        try:
            try:
                container.wait(timeout=s.sandbox_total_timeout_s)
            except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
                container.kill()
                return [RawResult("timeout", "", "Wall-clock limit exceeded", int(s.sandbox_total_timeout_s * 1000))
                        for _ in inputs]
            logs = container.logs(stdout=True, stderr=True).decode(errors="replace")
        finally:
            try:
                container.remove(force=True)
            except docker.errors.DockerException:
                pass
        idx = logs.rfind(RESULT_MARKER)
        if idx == -1:
            # Harness itself died (e.g. OOM-killed) - report as runtime error on every test.
            return [RawResult("runtime_error", "", logs[-2000:] or "Process killed (memory limit?)", 0) for _ in inputs]
        return [RawResult(**r) for r in json.loads(logs[idx + len(RESULT_MARKER):].strip().splitlines()[0])]

    async def execute(self, language: str, code: str, inputs: list[str]) -> list[RawResult]:
        if language not in LANGUAGES:
            raise SandboxError(f"Unsupported language: {language}")
        async with self._sem:
            return await anyio.to_thread.run_sync(self._run_blocking, language, code, inputs)


def _normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def grade(raw: list[RawResult], expected: list[str | None], hidden: list[bool], inputs: list[str]) -> dict:
    """Compare outputs with expected values and compute a verdict. Hidden test details are withheld."""
    results, passed, verdict = [], 0, "accepted"
    for i, (r, exp, hid, inp) in enumerate(zip(raw, expected, hidden, inputs)):
        if r.status == "timeout":
            status = "time_limit_exceeded"
        elif r.status != "ok":
            status = "runtime_error"
        elif exp is None:
            status = "ran"
        else:
            status = "passed" if _normalize(r.stdout) == _normalize(exp) else "wrong_answer"
        ok = status in ("passed", "ran")
        passed += ok
        if not ok and verdict == "accepted":
            verdict = status
        results.append({
            "index": i, "status": status, "passed": ok, "hidden": hid, "time_ms": r.time_ms,
            "input": None if hid else inp, "expected": None if hid else exp,
            "stdout": None if hid else r.stdout[:4000], "stderr": None if hid else r.stderr[-4000:],
        })
    return {
        "verdict": verdict, "passed": passed, "total": len(raw),
        "runtime_ms": max((r.time_ms for r in raw), default=0), "results": results,
    }


_sandbox: Sandbox | None = None


def get_sandbox() -> Sandbox:
    global _sandbox
    if _sandbox is None:
        _sandbox = DockerSandbox()
    return _sandbox
