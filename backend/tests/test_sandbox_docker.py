"""Integration tests against a real Docker daemon. Skipped when Docker isn't reachable."""
import pytest

from app.services.sandbox import DockerSandbox, grade

docker = pytest.importorskip("docker")
try:
    docker.from_env().ping()
except Exception:  # noqa: BLE001
    pytest.skip("Docker daemon not available", allow_module_level=True)

pytestmark = pytest.mark.asyncio


async def test_python_correct_and_wrong():
    raw = await DockerSandbox().execute("python", "import sys\nprint(sys.stdin.read().strip()[::-1])", ["abc", "hey"])
    g = grade(raw, ["cba", "nope"], [False, True], ["abc", "hey"])
    assert g["verdict"] == "wrong_answer" and g["passed"] == 1


async def test_infinite_loop_times_out():
    raw = await DockerSandbox().execute("python", "while True: pass", [""])
    assert raw[0].status == "timeout"


async def test_network_and_filesystem_blocked():
    code = (
        "import socket, os\n"
        "try:\n    socket.create_connection(('1.1.1.1', 80), timeout=1); print('NET')\nexcept OSError: print('nonet')\n"
        "try:\n    open('/etc/pwned', 'w'); print('FS')\nexcept OSError: print('rofs')\n"
        "print(os.getuid())\n"
    )
    raw = await DockerSandbox().execute("python", code, [""])
    assert raw[0].stdout.split() == ["nonet", "rofs", "65534"]


async def test_javascript():
    code = "const s=require('fs').readFileSync(0,'utf8').trim();console.log(s.split('').reverse().join(''))"
    raw = await DockerSandbox().execute("javascript", code, ["abc"])
    assert raw[0].status == "ok" and raw[0].stdout.strip() == "cba"
