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

Warm pool: starting a container costs ~0.5-1 s, so a few idle containers per language are kept
ready (SANDBOX_WARM_POOL). A run executes the harness inside one with `docker exec`; the container
has exactly the same limits as a cold one, serves that single run, and is then destroyed - so no
state ever carries over between submissions. Idle containers exit on their own after an hour, so
a crashed worker can't leave them behind for long.
"""
import asyncio
import base64
import hashlib
import json
import logging
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import anyio

from app.core.config import get_settings

log = logging.getLogger("prepforge.sandbox")

OUTPUT_LIMIT = 64 * 1024
RESULT_MARKER = "@@RESULT@@"
IMAGES_DIR = Path(__file__).resolve().parent.parent / "sandbox_images"
HARNESS_CMD = ["python3", "-S", "/opt/runner/harness.py"]
IDLE_TTL_S = 3600
# PID 1 of an idle container: sleeps until the TTL, then exits (and the container is auto-removed).
IDLE_CMD = ["python3", "-S", "-c", f"import time; time.sleep({IDLE_TTL_S})"]
LABEL = "prepforge.sandbox"
TIMED_OUT = "\x00timeout"  # sentinel output: the whole container hit its hard time cap


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
    for name in (f"{language}.Dockerfile", "harness.py", "testdata.py"):
        digest.update((IMAGES_DIR / name).read_bytes().replace(b"\r\n", b"\n"))
    return f"prepforge-sandbox-{language}:{digest.hexdigest()[:12]}"


class Sandbox:
    async def execute(self, language: str, code: str, inputs: list[str],
                      extra_files: dict[str, str] | None = None, args: list[str] | None = None) -> list[RawResult]:
        """Run `code` once per input. `extra_files` are written next to it (e.g. a generated driver);
        `args` (parameter types) marks function-style inputs, converted inside the sandbox."""
        raise NotImplementedError


def _remove(container) -> None:
    import docker.errors

    try:
        container.remove(force=True)
    except docker.errors.DockerException:
        pass  # already gone


def _remove_later(container) -> None:
    threading.Thread(target=_remove, args=(container,), daemon=True).start()


class WarmPool:
    """Pre-started idle containers per language. Thread-safe; each container is handed out once.

    Idle containers are labelled with this host's name. A restarted worker removes the ones its
    previous run left behind; anything else (another host's pool, a crash) expires within the TTL.
    """

    def __init__(self, sandbox: "DockerSandbox", size: int) -> None:
        self.sandbox = sandbox
        self.size = size
        self.owner = socket.gethostname()
        self._idle: dict[str, deque] = {}
        self._starting: dict[str, int] = {}
        self._threads: set[threading.Thread] = set()
        self._lock = threading.Lock()
        self._closed = False
        self._swept = False
        self.hits = 0
        self.misses = 0

    def _sweep_leftovers(self) -> None:
        """Remove idle containers that an earlier process on this host left behind."""
        import docker.errors

        try:
            old = self.sandbox._docker().containers.list(
                all=True, filters={"label": [f"{LABEL}=warm", f"prepforge.owner={self.owner}"]})
        except docker.errors.DockerException:
            return
        for c in old:
            _remove(c)
        if old:
            log.info("removed %d leftover warm container(s)", len(old))

    def take(self, language: str, tag: str):
        """An idle container for `language`, or None (the caller then starts one cold). Refills in the background."""
        container = None
        with self._lock:
            q = self._idle.setdefault(language, deque())
            while q:
                c, image, born = q.popleft()
                if image == tag and time.monotonic() - born < IDLE_TTL_S - 120:
                    container = c
                    break
                _remove_later(c)  # outdated image, or about to expire
            if container is None:
                self.misses += 1
            else:
                self.hits += 1
        self._fill_in_background(language, tag)
        return container

    def _fill_in_background(self, language: str, tag: str) -> None:
        t = threading.Thread(target=self.fill, args=(language, tag), daemon=True)
        with self._lock:
            if self._closed:
                return
            self._threads = {x for x in self._threads if x.is_alive()} | {t}
        t.start()

    def fill(self, language: str, tag: str) -> None:
        import docker.errors

        with self._lock:
            sweep, self._swept = not self._swept, True
        if sweep:
            self._sweep_leftovers()
        while True:
            with self._lock:
                q = self._idle.setdefault(language, deque())
                if self._closed or len(q) + self._starting.get(language, 0) >= self.size:
                    return
                self._starting[language] = self._starting.get(language, 0) + 1
            try:
                opts = self.sandbox.container_options(language)
                opts["labels"] = {**opts["labels"], LABEL: "warm", "prepforge.owner": self.owner}
                c = self.sandbox._docker().containers.run(tag, IDLE_CMD, detach=True, auto_remove=True, **opts)
            except docker.errors.DockerException as e:
                log.warning("could not start a warm %s container: %s", language, e)
                return
            finally:
                with self._lock:
                    self._starting[language] -= 1
            with self._lock:
                if not self._closed:
                    q.append((c, tag, time.monotonic()))
                    continue
            _remove(c)  # closed while it was starting
            return

    def idle_count(self, language: str) -> int:
        with self._lock:
            return len(self._idle.get(language, ()))

    def close(self, timeout: float = 15.0) -> None:
        """Remove every idle container, including ones still starting (so nothing outlives the process)."""
        with self._lock:
            self._closed = True
            threads = list(self._threads)
        end = time.monotonic() + timeout
        for t in threads:
            t.join(max(0.0, end - time.monotonic()))
        with self._lock:
            idle = [c for q in self._idle.values() for c, _, _ in q]
            self._idle.clear()
        for c in idle:
            _remove(c)


class DockerSandbox(Sandbox):
    def __init__(self, warm_pool: int | None = None) -> None:
        self.settings = get_settings()
        self._sem = asyncio.Semaphore(self.settings.sandbox_max_concurrency)
        self._client = None
        self._build_lock = threading.Lock()
        self._ready: set[str] = set()
        size = self.settings.sandbox_warm_pool if warm_pool is None else warm_pool
        self.pool = WarmPool(self, size) if size > 0 else None

    def warm_up(self, languages: list[str] | None = None) -> None:
        """Fill the warm pool now (otherwise each language's pool fills after its first run)."""
        if self.pool:
            for lang in languages or enabled_languages():
                self.pool.fill(lang, self.ensure_image(lang))

    def close(self) -> None:
        if self.pool:
            self.pool.close()

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

    def container_options(self, language: str) -> dict:
        """Isolation and resource limits - identical for cold and warm containers."""
        s = self.settings
        spec = LANGUAGES[language]
        return dict(
            network_mode="none", mem_limit=spec.memory, memswap_limit=spec.memory,
            nano_cpus=int(s.sandbox_cpus * 1e9), pids_limit=spec.pids, read_only=True,
            tmpfs={"/tmp": f"rw,exec,size={spec.tmpfs_mb}m,mode=1777"}, user="65534:65534", cap_drop=["ALL"],
            security_opt=["no-new-privileges"], runtime=s.sandbox_runtime, working_dir="/tmp",
            labels={LABEL: "run", "prepforge.language": language},
        )

    def _run_blocking(self, language: str, code: str, inputs: list[str],
                      extra_files: dict[str, str] | None = None, args: list[str] | None = None) -> list[RawResult]:
        tag = self.ensure_image(language)
        limits = self._limits(LANGUAGES[language], len(inputs))
        payload = base64.b64encode(json.dumps({
            "lang": language, "code": code, "tests": inputs, "output_limit": OUTPUT_LIMIT,
            "extra_files": extra_files or {}, "args": args,
            **{k: v for k, v in limits.items() if k != "container_timeout"},
        }).encode()).decode()
        container = self.pool.take(language, tag) if self.pool else None
        if container is not None:
            logs = self._exec_warm(container, payload, limits["container_timeout"])
            if logs is not None:
                return self._parse(logs, inputs)
            # That idle container had died (expired, Docker restarted...): fall back to a cold start.
        return self._parse(self._run_cold(tag, language, payload, limits["container_timeout"]), inputs)

    def _exec_warm(self, container, payload: str, timeout: float) -> str | None:
        """Run the harness in a pre-started container. None if the container turned out to be unusable."""
        import docker.errors

        fired = threading.Event()

        def hard_cap() -> None:  # same whole-run cap as the cold path
            fired.set()
            _remove(container)

        timer = threading.Timer(timeout, hard_cap)
        timer.start()
        try:
            result = container.exec_run(HARNESS_CMD, environment={"PAYLOAD": payload}, workdir="/tmp")
            logs = (result.output or b"").decode(errors="replace")
        except docker.errors.DockerException:
            logs = None
        finally:
            timer.cancel()
            _remove_later(container)  # one run per container, always
        return TIMED_OUT if fired.is_set() else logs

    def _run_cold(self, tag: str, language: str, payload: str, timeout: float) -> str:
        import docker.errors
        import requests.exceptions

        try:
            container = self._docker().containers.run(
                tag, HARNESS_CMD, environment={"PAYLOAD": payload}, detach=True, **self.container_options(language))
        except docker.errors.DockerException as e:
            raise SandboxError(f"Sandbox unavailable: {e}") from e
        try:
            try:
                container.wait(timeout=timeout)
            except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
                container.kill()
                return TIMED_OUT
            return container.logs(stdout=True, stderr=True).decode(errors="replace")
        finally:
            _remove_later(container)

    @staticmethod
    def _parse(logs: str, inputs: list[str]) -> list[RawResult]:
        if logs == TIMED_OUT:
            return [RawResult("timeout", "", "Overall time limit exceeded", 0) for _ in inputs]
        idx = logs.rfind(RESULT_MARKER)
        if idx == -1:
            # The harness itself died (e.g. the whole container was OOM-killed).
            return [RawResult("memory", "", logs[-2000:] or "Process killed (memory limit?)", 0) for _ in inputs]
        return [RawResult(**r) for r in json.loads(logs[idx + len(RESULT_MARKER):].strip().splitlines()[0])]

    async def execute(self, language: str, code: str, inputs: list[str],
                      extra_files: dict[str, str] | None = None, args: list[str] | None = None) -> list[RawResult]:
        if language not in enabled_languages():
            raise SandboxError(f"Unsupported language: {language}")
        async with self._sem:
            return await anyio.to_thread.run_sync(self._run_blocking, language, code, inputs, extra_files, args)


def _normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


STATUS_VERDICT = {"timeout": "time_limit_exceeded", "memory": "memory_limit_exceeded",
                  "compile_error": "compile_error", "runtime_error": "runtime_error",
                  "invalid_input": "invalid_input"}


def grade(raw: list[RawResult], expected: list[str | None], hidden: list[bool], inputs: list[str],
          signature=None) -> dict:
    """Compare outputs with expected values and compute a verdict. Hidden test details are withheld.

    `signature` (app.services.functions.Signature) marks a function-style problem: the returned
    JSON is compared instead of stdout. Generated (large) inputs are complexity checks: if those are
    the only failures and they ran out of time, the verdict is "too_slow" - correct, but not fast enough.
    """
    from app.services import functions

    results, passed, verdict = [], 0, "accepted"
    large_timeouts, other_failures = 0, 0
    for i, (r, exp, hid, inp) in enumerate(zip(raw, expected, hidden, inputs)):
        large = functions.testdata.is_generated(inp)
        printed, shown = r.stdout, r.stdout
        returned = None
        if signature is not None:
            printed, returned = functions.split_output(r.stdout)
            shown = returned if returned is not None else ""
        if r.status != "ok":
            status = STATUS_VERDICT.get(r.status, "runtime_error")
        elif signature is not None and returned is None:
            status = "runtime_error"  # e.g. the program exited before the function returned
        elif exp is None:
            status = "ran"
        elif signature is not None:
            status = "passed" if functions.results_match(signature, returned, exp) else "wrong_answer"
        else:
            status = "passed" if _normalize(r.stdout) == _normalize(exp) else "wrong_answer"
        ok = status in ("passed", "ran")
        passed += ok
        if not ok:
            if large and status == "time_limit_exceeded":
                large_timeouts += 1
            else:
                other_failures += 1
                if verdict == "accepted":
                    verdict = status
        if large:
            shown_input = functions.describe_generated(inp)
        elif signature is not None:
            shown_input = functions.format_args(signature, inp)
        else:
            shown_input = inp
        # Compiler output is about the candidate's own code, so it's shown even for hidden tests.
        show_stderr = not hid or status in ("compile_error", "invalid_input")
        entry = {
            "index": i, "status": status, "passed": ok, "hidden": hid, "time_ms": r.time_ms, "large": large,
            "input": None if hid and not large else shown_input, "expected": None if hid else exp,
            "stdout": None if hid else shown[:4000], "stderr": r.stderr[-4000:] if show_stderr else None,
        }
        if signature is not None and not hid and printed.strip():
            entry["printed"] = printed[:4000]
        results.append(entry)
    if large_timeouts and not other_failures:
        verdict = "too_slow"
    elif large_timeouts and verdict == "accepted":
        verdict = "time_limit_exceeded"
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

    async def execute(self, language: str, code: str, inputs: list[str],
                      extra_files: dict[str, str] | None = None, args: list[str] | None = None) -> list[RawResult]:
        if language not in enabled_languages():
            raise SandboxError(f"Unsupported language: {language}")
        s = self.settings
        # Generous: queue wait + compile + every test at its wall limit.
        timeout = s.sandbox_compile_wall_s + len(inputs) * max(s.sandbox_wall_limit_s, s.sandbox_cpu_limit_s * 6) + 60
        try:
            pool = await self._get_pool()
            job = await pool.enqueue_job("run_code", language, code, inputs, extra_files, args)
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
    for t in DockerSandbox(warm_pool=0).ensure_images(sys.argv[1:] or None):
        print("ready:", t)
