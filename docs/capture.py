"""Regenerate the README screenshots from a live stack.

    ./stack.sh up && uv run --with playwright docs/capture.py chain
    ./stack.sh down && ./stack.sh up multi-allocator \
        && uv run --with playwright docs/capture.py multi-allocator

Every image comes from a real network - nothing is edited. Kept in the repo
because a version bump makes every image stale (agent versions are on screen),
and rebuilding the harness each time costs more than the capture.

Three things learned the hard way, and encoded below:

- Traffic is drawn from the *second* poll onward. Until there is a previous
  sample the per-agent delta is unknown, so a shot taken within one poll of
  loading looks idle on a busy network. ``settle()`` waits past it.
- Load is one sequential loop. Parallel loops contend on the leaves, and a
  hero image reading "54 failures" misrepresents the tool.
- The TUI log view is filtered to INFO. With no portal software behind the
  bridge, every completed job produces notification retries that drown the
  cross-agent timeline the view exists to show.

The graph shots run on the host against ./run.sh (which this starts); the TUI
has to run inside the agent network, like tui.sh, so ``tui`` is the in-container
half and is started by ``chain`` itself.
"""

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PORT = int(os.getenv("PORT", "8900"))
BASE = f"http://localhost:{PORT}"
POLL_S = 5  # index.html's refresh interval
WIDTH = 1600
SCALE = 2


# --- the graph server ------------------------------------------------------


def api(path, payload=None, timeout=120):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def start_server():
    env = dict(os.environ, SIGNALBOX_CONFIG="none", SIGNALBOX_READONLY="")
    proc = subprocess.Popen(
        [str(ROOT / "run.sh")],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            if api("/api/topology", timeout=10).get("ok"):
                return proc
        except Exception:
            pass
        time.sleep(2)
    proc.terminate()
    raise SystemExit("graph server did not come up on " + BASE)


def stop_server(proc):
    subprocess.run(["docker", "stop", "signalbox"], capture_output=True)
    proc.wait(timeout=30)


def instances():
    """Routes of the agents that execute - our own only; a peer's has none."""
    nodes = api("/api/topology")["nodes"]
    return sorted(n["route"] for n in nodes if n["type"] == "instance" and n["route"])


class Load(threading.Thread):
    """One sequential loop of ordinary work, so every hop carries traffic."""

    def __init__(self, targets):
        super().__init__(daemon=True)
        self.targets = targets
        self.stop = threading.Event()
        self.failures = []

    def run(self):
        while not self.stop.is_set():
            for target in self.targets:
                project = f"demo{uuid.uuid4().hex[:6]}.waldur"
                for command in (
                    f"add_project {project}",
                    f"get_project_mapping {project}",
                    f"add_user u.{project}",
                    f"get_usage_report {project} this_month",
                ):
                    if self.stop.is_set():
                        return
                    out = api("/api/run", {"command": f"{target} {command}", "timeout_ms": 90000})
                    if not out.get("ok"):
                        self.failures.append(out)


# --- the browser -----------------------------------------------------------


async def settle(page, polls=3):
    """Past the second poll, so the edges have a delta to draw.

    And half a poll clear of the next one: a traffic dot whose animation has
    not begun yet sits at the canvas origin for a moment after each refresh,
    and a wait of whole polls lands exactly on that moment.
    """
    await page.wait_for_selector(".react-flow__node")
    await page.wait_for_timeout(int((polls + 0.5) * POLL_S * 1000))


async def click_edge(page, source, target):
    """Click the middle of the drawn path; a bounding-box centre can miss it."""
    point = await page.evaluate(
        """([source, target]) => {
            const edges = [...document.querySelectorAll('.react-flow__edge')];
            // ids are "<source key>-><target key>", keys ending in the agent
            // path - so compare last segments: "clusters" contains "cluster".
            const last = (key) => key.split(':').pop().split('.').pop();
            const edge = edges.find((e) => {
              const id = (e.getAttribute('data-testid') || '').replace('rf__edge-', '');
              const [from, to] = id.split('->');
              return to !== undefined && last(from) === source && last(to) === target;
            });
            if (!edge) return null;
            const path = edge.querySelector('path');
            const p = path.getPointAtLength(path.getTotalLength() / 2);
            const m = path.getScreenCTM();
            return { x: p.x * m.a + p.y * m.c + m.e, y: p.x * m.b + p.y * m.d + m.f };
        }""",
        [source, target],
    )
    if point is None:
        raise SystemExit(f"no edge {source} -> {target} on the canvas")
    await page.mouse.click(point["x"], point["y"])


async def run_in_console(page, command):
    lines = "#console .out > div"
    before = await page.locator(lines).count()
    box = page.locator("#console .row input")
    await box.fill(command)
    await box.press("Enter")
    # A line lands in the history only once the job is answered.
    await page.wait_for_function(
        f"() => document.querySelectorAll('{lines}').length > {before}",
        timeout=120_000,
    )


async def graph_chain(out):
    from playwright.async_api import async_playwright

    [target] = instances()[:1]
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel="chrome", headless=True)

        page = await browser.new_page(
            viewport={"width": WIDTH, "height": 560}, device_scale_factor=SCALE
        )
        await page.goto(BASE)
        await settle(page)
        await page.screenshot(path=out / "flow.png")
        await page.close()

        # A page of its own, so the waits count from its load and the shot
        # stays half a poll clear of a refresh.
        page = await browser.new_page(
            viewport={"width": WIDTH, "height": 760}, device_scale_factor=SCALE
        )
        await page.goto(BASE)
        await settle(page, polls=2)
        await click_edge(page, "clusters", "cluster")
        await page.wait_for_selector("#side.on")
        await page.wait_for_timeout(POLL_S * 1000)
        await page.screenshot(path=out / "edge.png")
        await page.close()

        # The console after the walkthrough's verification step and one honest
        # refusal: what a "no", a "yes" and a failed instruction look like now.
        page = await browser.new_page(
            viewport={"width": WIDTH, "height": 900}, device_scale_factor=SCALE
        )
        await page.goto(BASE)
        await settle(page, polls=1)
        project = f"sbdemo{uuid.uuid4().hex[:4]}.waldur"
        for command in (
            f"{target} add_project {project}",
            f"{target} is_user_added alice.{project}",
            f"{target} add_user alice.{project}",
            f"{target} is_user_added alice.{project}",
            f"{target} get_project_mapping neverexisted.waldur",
        ):
            await run_in_console(page, command)
        await page.locator("#console").screenshot(path=out / "console.png")
        await browser.close()


async def graph_multi(out):
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel="chrome", headless=True)
        page = await browser.new_page(
            viewport={"width": WIDTH, "height": 720}, device_scale_factor=SCALE
        )
        await page.goto(BASE)
        await settle(page)
        await page.screenshot(path=out / "multi-allocator.png")
        await browser.close()


# --- the terminal UI (runs inside the agent network) ------------------------


async def tui(out):
    sys.path.insert(0, str(ROOT))
    from textual.widgets import DataTable, Input

    import tui as tui_module

    app = tui_module.OpenPortalTUI()
    async with app.run_test(size=(150, 45)) as pilot:
        for _ in range(60):
            await pilot.pause(0.5)
            if app.nodes:
                break
        # On the instance agent rather than the bridge, so the detail pane
        # shows real jobs and a real log rather than an idle router's zeros.
        index = next(i for i, n in enumerate(app.nodes) if n["type"] == "instance")
        app.query_one("#agents", DataTable).move_cursor(row=index)
        await pilot.pause(6)
        app.save_screenshot(filename="tui-agents.svg", path=str(out))

        await pilot.press("2")
        level = app.query_one("#level", Input)
        level.value = "INFO"
        level.focus()
        await pilot.press("enter")
        await pilot.pause(8)
        app.save_screenshot(filename="tui-logs.svg", path=str(out))


def tui_in_container(out):
    """Same network and invite discovery as tui.sh, but headless."""
    env = dict(os.environ, SIGNALBOX_CONFIG="none")
    probe = subprocess.run(
        [
            "bash",
            "-c",
            ". ./detect.sh >/dev/null && resolve_target >/dev/null "
            '&& echo "$NETWORK $INVITE_VOLUME $OPENPORTAL_VERSION"',
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    network, volume, version = probe.stdout.split()
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            network,
            "-e",
            "SIGNALBOX_CONFIG=none",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "-v",
            f"{volume}:/inv:ro",
            "-v",
            f"{ROOT}:/app:ro",
            "-v",
            f"{out}:/out",
            "-w",
            "/app",
            "python:3.12-slim",
            "sh",
            "-c",
            "pip install --quiet --disable-pip-version-check --root-user-action=ignore "
            f"openportal=={version} textual && python docs/capture.py tui /out",
        ],
        check=True,
    )


# --- entry points ------------------------------------------------------------


def with_load(capture):
    server = start_server()
    load = Load(instances())
    load.start()
    try:
        time.sleep(POLL_S * 2)
        capture()
    finally:
        load.stop.set()
        load.join(timeout=120)
        stop_server(server)
    if load.failures:
        print(f"WARNING: {len(load.failures)} load job(s) failed; first: {load.failures[0]}")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "tui":
        asyncio.run(tui(Path(sys.argv[2])))
    elif mode == "chain":

        def capture():
            asyncio.run(graph_chain(HERE))
            tui_in_container(HERE)

        with_load(capture)
    elif mode == "multi-allocator":
        with_load(lambda: asyncio.run(graph_multi(HERE)))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
