"""Isolated code execution in throwaway Docker containers, for Python, JavaScript, Java, C++ and Go.

Each run gets a fresh container from a small per-language image (built from app/sandbox_images):
  - no network, read-only root filesystem, a tmpfs for /tmp
  - memory / CPU / PID limits, all capabilities dropped, no-new-privileges
  - non-root user (nobody), optional gVisor runtime (SANDBOX_RUNTIME=runsc)
  - per-test CPU-time limits enforced by the kernel (RLIMIT_CPU), a generous per-test wall-clock
    limit for blocked programs, and a hard limit on the whole container

The same harness (app/sandbox_images/harness.py) runs in every image: it compiles once, then runs
each test. Times are reported as CPU time, so verdicts don't depend on how busy the host is.

Code and test *inputs* go in through an environment variable (base64 JSON), so no bind mounts are
needed and it works when the worker runs inside Docker. Expected outputs never enter the container:
grading happens here, on the host side.
"""
import asyncio
import base64
import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

import anyio

from app.core.config import get_settings

log = logging.getLogger("prepforge.sandbox")

OUTPUT_LIMIT = 64 * 1024
RESULT_MARKER = "@@RESULT@@"
IMAGES_DIR = Path(__file__).resolve().parent.parent / "sandbox_images"


@dataclass(frozen=True)
class LanguageSpec:
    memory: str
    pids: int
    tmpfs_mb: int
    time_factor: float = 1.0  # CPU-limit multiplier (JVM startup + JIT cost more CPU)
    compiled: bool = False


LANGUAGES: dict[str, LanguageSpec] = {
    "python": LanguageSpec(memory="256m", pids=64, tmpfs_mb=16),
    "javascript": LanguageSpec(memory="256m", pids=64, tmpfs_mb=16),
    "java": LanguageSpec(memory="512m", pids=256, tmpfs_mb=64, time_factor=2.0, compiled=True),
    "cpp": LanguageSpec(memory="256m", pids=64, tmpfs_mb=64, compiled=True),
    "go": LanguageSpec(memory="512m", pids=128, tmpfs_mb=192, compiled=True),
}


class SandboxError(RuntimeError):
    pass


@dataclass
class RawResult:
    status: str  # ok | runtime_error | timeout | memory | compile_error
    stdout: str
    stderr: str
    time_ms: int  # CPU time


def enabled_languages() -> list[str]:
    return [lang for lang in get_settings().sandbox_languages if lang in LANGUAGES]


def image_tag(language: str) -> str:
    """Content-addressed tag: changing the Dockerfile or harness produces a new image automatically."""
    digest = hashlib.sha256()
    for name in (f"{language}.Dockerfile", "harness.py"):
        digest.update((IMAGES_DIR / name).read_bytes().replace(b"\r\n", b"\n"))
    return f"prepforge-sandbox-{language}:{digest.hexdigest()[:12]}"


class Sandbox:
    async def execute(self, language: str, code: str, inputs: list[str]) -> list[RawResult]:
        raise NotImplementedError


class DockerSandbox(Sandbox):
    def __init__(self) -> None:
        self.settings = get_settings()
        self._sem = asyncio.Semaphore(self.settings.sandbox_max_concurrency)
        self._client = None
        self._build_lock = threading.Lock()
        self._ready: set[str] = set()

    def _docker(self):
        if self._client is None:
            import docker

            self._client = docker.from_env()
        return self._client

    def check_runtime(self) -> None:
        """Fail fast if a hardened runtime (e.g. gVisor's runsc) is configured but not installed."""
        runtime = self.settings.sandbox_runtime
        if runtime and runtime not in self._docker().info().get("Runtimes", {}):
            raise SandboxError(f"Docker runtime '{runtime}' is not installed on this host")

    def ensure_image(self, language: str) -> str:
        """Build the language's sandbox image if this exact version isn't present yet."""
        import docker.errors

        tag = image_tag(language)
        if tag in self._ready:
            return tag
        with self._build_lock:
            if tag in self._ready:
                return tag
            client = self._docker()
            try:
                client.images.get(tag)
            except docker.errors.ImageNotFound:
                log.info("Building sandbox image %s (one-time)", tag)
                try:
                    client.images.build(path=str(IMAGES_DIR), dockerfile=f"{language}.Dockerfile", tag=tag,
                                        rm=True, forcerm=True, pull=True)
                except docker.errors.DockerException as e:
                    raise SandboxError(f"Could not build the {language} sandbox image: {e}") from e
            except docker.errors.DockerException as e:
                raise SandboxError(f"Sandbox unavailable: {e}") from e
            self._remove_old_versions(language, keep=tag)
            self._ready.add(tag)
            return tag

    def _remove_old_versions(self, language: str, keep: str) -> None:
        """Delete outdated versions of this language's sandbox image, so upgrades don't pile up on disk."""
        import docker.errors

        repo = keep.split(":")[0]
        for image in self._docker().images.list(name=repo):
            for t in image.tags:
                if t.startswith(repo + ":") and t != keep:
                    try:
                        self._docker().images.remove(t)
                        log.info("Removed old sandbox image %s", t)
                    except docker.errors.DockerException:
                        pass  # still used by a running container; it'll go next time

    def ensure_images(self, languages: list[str] | None = None) -> list[str]:
        return [self.ensure_image(lang) for lang in (languages or enabled_languages())]

    def _limits(self, spec: LanguageSpec, n_tests: int) -> dict:
        s = self.settings
        cpu = s.sandbox_cpu_limit_s * spec.time_factor
        wall = max(s.sandbox_wall_limit_s, cpu * 3)
        compile_wall = s.sandbox_compile_wall_s if spec.compiled else 0
        return {
            "cpu_limit": cpu, "wall_limit": wall,
            "compile_cpu_limit": s.sandbox_compile_cpu_s, "compile_wall_limit": s.sandbox_compile_wall_s,
            # Whole-container cap: compile + every test hitting its wall limit + startup slack.
            "container_timeout": compile_wall + n_tests * wall + 15,
        }

    def _run_blocking(self, language: str, code: str, inputs: list[str]) -> list[RawResult]:
        import docker.errors
        import requests.exceptions

        s = self.settings
        spec = LANGUAGES[language]
        tag = self.ensure_image(language)
        limits = self._limits(spec, len(inputs))
        payload = base64.b64encode(json.dumps({
            "lang": language, "code": code, "tests": inputs, "output_limit": OUTPUT_LIMIT,
            **{k: v for k, v in limits.items() if k != "container_timeout"},
        }).encode()).decode()
        try:
            container = self._docker().containers.run(
                tag, ["python3", "-S", "/opt/runner/harness.py"], environment={"PAYLOAD": payload}, detach=True,
                network_mode="none", mem_limit=spec.memory, memswap_limit=spec.memory,
                nano_cpus=int(s.sandbox_cpus * 1e9), pids_limit=spec.pids, read_only=True,
                tmpfs={"/tmp": f"rw,exec,size={spec.tmpfs_mb}m,mode=1777"}, user="65534:65534", cap_drop=["ALL"],
                security_opt=["no-new-privileges"], runtime=s.sandbox_runtime, working_dir="/tmp",
            )
        except docker.errors.DockerException as e:
            raise SandboxError(f"Sandbox unavailable: {e}") from e
        try:
            try:
                container.wait(timeout=limits["container_timeout"])
            except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
                container.kill()
                return [RawResult("timeout", "", "Overall time limit exceeded", 0) for _ in inputs]
            logs = container.logs(stdout=True, stderr=True).decode(errors="replace")
        finally:
            try:
                container.remove(force=True)
            except docker.errors.DockerException:
                pass
        idx = logs.rfind(RESULT_MARKER)
        if idx == -1:
            # The harness itself died (e.g. the whole container was OOM-killed).
            return [RawResult("memory", "", logs[-2000:] or "Process killed (memory limit?)", 0) for _ in inputs]
        return [RawResult(**r) for r in json.loads(logs[idx + len(RESULT_MARKER):].strip().splitlines()[0])]

    async def execute(self, language: str, code: str, inputs: list[str]) -> list[RawResult]:
        if language not in enabled_languages():
            raise SandboxError(f"Unsupported language: {language}")
        async with self._sem:
            return await anyio.to_thread.run_sync(self._run_blocking, language, code, inputs)


def _normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


STATUS_VERDICT = {"timeout": "time_limit_exceeded", "memory": "memory_limit_exceeded",
                  "compile_error": "compile_error", "runtime_error": "runtime_error"}


def grade(raw: list[RawResult], expected: list[str | None], hidden: list[bool], inputs: list[str]) -> dict:
    """Compare outputs with expected values and compute a verdict. Hidden test details are withheld."""
    results, passed, verdict = [], 0, "accepted"
    for i, (r, exp, hid, inp) in enumerate(zip(raw, expected, hidden, inputs)):
        if r.status != "ok":
            status = STATUS_VERDICT.get(r.status, "runtime_error")
        elif exp is None:
            status = "ran"
        else:
            status = "passed" if _normalize(r.stdout) == _normalize(exp) else "wrong_answer"
        ok = status in ("passed", "ran")
        passed += ok
        if not ok and verdict == "accepted":
            verdict = status
        # Compiler output is about the candidate's own code, so it's shown even for hidden tests.
        show_stderr = not hid or status == "compile_error"
        results.append({
            "index": i, "status": status, "passed": ok, "hidden": hid, "time_ms": r.time_ms,
            "input": None if hid else inp, "expected": None if hid else exp,
            "stdout": None if hid else r.stdout[:4000], "stderr": r.stderr[-4000:] if show_stderr else None,
        })
    return {
        "verdict": verdict, "passed": passed, "total": len(raw),
        "runtime_ms": max((r.time_ms for r in raw), default=0), "results": results,
    }


class QueueSandbox(Sandbox):
    """Sends runs to the arq worker over Redis, so the API process never touches the Docker socket."""

    def __init__(self, redis_url: str) -> None:
        self.redis_url = redis_url
        self.settings = get_settings()
        self._pool = None

    async def _get_pool(self):
        if self._pool is None:
            from arq import create_pool
            from arq.connections import RedisSettings

            self._pool = await create_pool(RedisSettings.from_dsn(self.redis_url))
        return self._pool

    async def execute(self, language: str, code: str, inputs: list[str]) -> list[RawResult]:
        if language not in enabled_languages():
            raise SandboxError(f"Unsupported language: {language}")
        s = self.settings
        # Generous: queue wait + compile + every test at its wall limit.
        timeout = s.sandbox_compile_wall_s + len(inputs) * max(s.sandbox_wall_limit_s, s.sandbox_cpu_limit_s * 6) + 60
        try:
            pool = await self._get_pool()
            job = await pool.enqueue_job("run_code", language, code, inputs)
            data = await job.result(timeout=timeout, poll_delay=0.05)
        except TimeoutError as e:
            raise SandboxError("Code runner is busy, please try again") from e
        except Exception as e:  # noqa: BLE001  (Redis down, worker crashed, job failed)
            raise SandboxError(f"Code runner unavailable: {type(e).__name__}") from e
        return [RawResult(**r) for r in data]


_sandbox: Sandbox | None = None


def get_sandbox() -> Sandbox:
    global _sandbox
    if _sandbox is None:
        s = get_settings()
        if s.sandbox_backend == "queue":
            if not s.redis_url:
                raise SandboxError("SANDBOX_BACKEND=queue requires REDIS_URL")
            _sandbox = QueueSandbox(s.redis_url)
        else:
            _sandbox = DockerSandbox()
    return _sandbox


if __name__ == "__main__":
    # Pre-build sandbox images:  python -m app.services.sandbox [language ...]
    import sys

    logging.basicConfig(level=logging.INFO)
    for t in DockerSandbox().ensure_images(sys.argv[1:] or None):
        print("ready:", t)
