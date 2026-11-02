"""Local application server and desktop-window lifecycle."""
import argparse
import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import webbrowser

from aiohttp import ClientSession, ClientTimeout, web

from iphone_desktop import __version__
from iphone_desktop.errors import explain_error
from iphone_desktop.camera import CameraRelay

ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
ASSETS = Path(getattr(sys, "_MEIPASS", ROOT)) / "iphone_desktop" / "web"
RUNTIME = ROOT / ".runtime"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def worker_command(action: str, result: Path, log: Path, device=None, transport="usb", port=0, wifi_host=None) -> list[str]:
    entry = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, str(ROOT / "run.py")]
    command = [*entry, "--bridge", action, "--result", str(result), "--log", str(log), "--transport", transport]
    if device:
        command.extend(["--device", device])
    if port:
        command.extend(["--port", str(port)])
    if wifi_host:
        command.extend(["--wifi-host", wifi_host])
    return command


class Controller:
    def __init__(self):
        self.devices = []
        self.state = "idle"
        self.message = "Connect your iPhone over USB to get started."
        self.problem = None
        self.engine = None
        self.engine_port = None
        self.engine_log = None
        self.operation = None
        self.engine_monitor = None
        self.children = set()
        self.logs = []
        self.last_seen = time.monotonic()
        self.stop_event = asyncio.Event()
        self.lock = asyncio.Lock()
        self.generation = 0
        self.operation_started = None
        self.progress = None
        self.camera = CameraRelay(RUNTIME, ASSETS)

    def loading(self, state, message, step=1, total=1):
        self.state = state
        self.message = message
        self.progress = {"step": step, "total": total}

    def log(self, text):
        self.logs.append({"time": time.strftime("%H:%M:%S"), "text": str(text)})
        self.logs = self.logs[-100:]
        logging.info("%s", text)

    def failure(self, error):
        problem = explain_error(error)
        self.problem = {"code": problem.code, "title": problem.title, "detail": problem.detail}
        self.state = "error"
        self.message = problem.title
        self.log(error)

    async def spawn(self, action, device=None, transport="usb", port=0, wifi_host=None):
        identifier = secrets.token_hex(6)
        result = RUNTIME / f"{identifier}.json"
        log = RUNTIME / f"{identifier}.log"
        command = worker_command(action, result, log, device, transport, port, wifi_host)
        child = await asyncio.create_subprocess_exec(*command, cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.children.add(child)
        return child, result, log

    async def run_worker(self, action, device=None, transport="usb", timeout=40, wifi_host=None):
        child, result, log = await self.spawn(action, device, transport, wifi_host=wifi_host)
        try:
            await asyncio.wait_for(child.wait(), timeout)
            if not result.exists():
                raise RuntimeError(log.read_text(encoding="utf-8", errors="replace")[-4000:] if log.exists() else "Worker stopped without a result")
            data = json.loads(result.read_text(encoding="utf-8"))
            if not data.get("ok"):
                raise RuntimeError(data.get("error", "Device operation failed"))
            return data
        finally:
            if child.returncode is None:
                child.kill()
                await child.wait()
            self.children.discard(child)

    async def scan(self, transport="usb"):
        self.loading("scanning", "Looking for a trusted USB iPhone…" if transport == "usb" else "Looking for your iPhone on the local network. Wi-Fi discovery can take up to 30 seconds…")
        data = await self.run_worker("discover", transport=transport, timeout=40)
        # Keep the other transport's results so switching tabs does not empty
        # a known USB connection while a wireless search is in progress.
        self.devices = [d for d in self.devices if d["transport"] != transport] + data["devices"]
        usb = sum(d["transport"] == "usb" for d in self.devices)
        wifi = sum(d["transport"] == "wifi" for d in self.devices)
        self.log(f"Found {usb} USB and {wifi} Wi-Fi connections (Apple and Bonjour).")
        found = any(d["transport"] == transport for d in self.devices)
        self.message = "Select the phone and click Connect iPhone." if found else ("No USB iPhone found. Connect the cable, unlock the phone, and trust this PC." if transport == "usb" else "No Wi-Fi iPhone found. Keep it unlocked, check the local network, and refresh. Use USB for initial Wi-Fi setup.")
        self.state = "idle"

    async def start_session(self, device, transport):
        await self.camera.stop()
        await self.stop_session()
        self.loading("connecting", "Checking device trust and iPhone services…", 1, 3)
        info = await self.run_worker("info", device, transport)
        self.log(f"{info['name']} · iOS {info['ios']} · Developer Mode: {info['developer_mode']}")
        if not info["developer_mode"]:
            raise RuntimeError("Developer mode is not enabled")
        # The upstream streaming protocol currently requires iOS 27. Do not
        # open a black viewer or advertise unsupported control on older iOS.
        from packaging.version import Version
        if Version(info["ios"]) < Version("27.0"):
            self.state = "error"
            self.message = "This streaming method requires iOS 27 or later."
            self.problem = {"code": "ios_version", "title": "iOS version unsupported by the engine", "detail": f"The phone runs iOS {info['ios']}. This preview cannot stream the screen on that version. No purchase is needed: support requires an engine change or another protocol."}
            return
        self.loading("connecting", "Preparing developer services: the first download may take a few minutes…", 2, 3)
        prepared = await self.run_worker("prepare", device, transport, timeout=600)
        self.log(prepared["message"])
        self.engine_port = free_port()
        self.engine, self.engine_result, self.engine_log = await self.spawn("stream", device, transport, self.engine_port)
        self.log("Starting the local tunnel and screen/touch engine. No Bluetooth.")
        self.loading("connecting", "Starting screen and controls. Waiting for the first video data…", 3, 3)
        generation = self.generation
        await self.wait_for_video()
        self.state = "connected"
        self.message = "Video stream ready. Click the screen to test input."
        self.engine_monitor = asyncio.create_task(self.monitor_engine(generation))

    def engine_error(self):
        detail = self.engine_log.read_text(encoding="utf-8", errors="replace")[-6000:] if self.engine_log and self.engine_log.exists() else "Engine stopped"
        if self.engine_result.exists():
            detail = json.loads(self.engine_result.read_text(encoding="utf-8")).get("error", detail)
        return detail

    async def wait_for_video(self, timeout=65):
        deadline = time.monotonic() + timeout
        async with ClientSession(timeout=ClientTimeout(total=8)) as client:
            while time.monotonic() < deadline:
                if self.engine.returncode is not None:
                    raise RuntimeError(self.engine_error())
                try:
                    # The HTML endpoint succeeds even when the device services
                    # fail. /codec needs actual video parameter sets from iPhone.
                    async with client.get(f"http://127.0.0.1:{self.engine_port}/codec") as response:
                        if response.status == 200:
                            codec = await response.json()
                            if codec.get("codec") and codec.get("description"):
                                return
                        elif response.status != 503:
                            raise RuntimeError(f"Video startup failed: HTTP {response.status}\n{self.engine_error()}")
                except (OSError, asyncio.TimeoutError):
                    pass
                await asyncio.sleep(0.5)
        raise TimeoutError("Timeout while starting the iPhone screen stream\n" + self.engine_error())

    async def monitor_engine(self, generation):
        engine = self.engine
        await engine.wait()
        if generation == self.generation and self.state == "connected":
            detail = self.engine_log.read_text(encoding="utf-8", errors="replace")[-5000:] if self.engine_log and self.engine_log.exists() else "Device disconnected"
            self.engine_port = None
            self.failure(detail)

    async def stop_session(self):
        self.generation += 1
        if self.engine_monitor:
            self.engine_monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.engine_monitor
            self.engine_monitor = None
        if self.engine and self.engine.returncode is None:
            # Windows TerminateProcess skips the worker's finally blocks.
            # Let it stop device streams and close its tunnel before fallback.
            result_path = getattr(self, "engine_result", None)
            if result_path:
                try:
                    result_path.with_suffix(".stop").touch()
                except OSError:
                    logging.exception("Could not request graceful stream shutdown")
                else:
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(self.engine.wait(), 20)
            if self.engine.returncode is None:
                self.engine.terminate()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self.engine.wait(), 4)
            if self.engine.returncode is None:
                self.engine.kill()
                await self.engine.wait()
        if self.engine:
            self.children.discard(self.engine)
        self.engine = None
        self.engine_port = None
        self.state = "idle"
        self.message = "Session ended."

    async def perform(self, action, device=None, transport="usb", wifi_host=None):
        async with self.lock:
            self.operation_started = time.monotonic()
            try:
                self.problem = None
                if action == "camera":
                    self.loading("preparing", "Releasing screen mirroring and starting the local camera connection…")
                    await self.stop_session()
                    self.loading("preparing", "Preparing the secure iPhone camera link…")
                    await self.camera.start()
                    self.state = "camera"
                    self.message = "Scan the camera QR code with your iPhone, then open the link in Safari."
                    self.log("Camera mode enabled on the local network. Screen mirroring stopped.")
                elif action == "scan":
                    await self.scan(transport)
                elif action == "connect":
                    if not device:
                        raise ValueError("No device found")
                    await self.start_session(device, transport)
                else:
                    if not device:
                        raise ValueError("No device found")
                    if self.engine:
                        await self.stop_session()
                    self.loading("preparing", {"prepare": "Preparing services: the first download may take a few minutes…", "reveal": "Revealing the Developer Mode setting…", "wifi": "Enabling Wi-Fi connections over USB…"}[action])
                    data = await self.run_worker(action, device, "usb", timeout=600 if action == "prepare" else 40, wifi_host=wifi_host)
                    self.log(data["message"])
                    self.message = data["message"]
                    self.state = "idle"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self.stop_session()
                self.failure(str(exc) or type(exc).__name__)
            finally:
                self.progress = None
                self.operation_started = None

    async def shutdown(self):
        if self.operation and not self.operation.done():
            self.operation.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.operation
        await self.camera.stop()
        await self.stop_session()
        for child in tuple(self.children):
            if child.returncode is None:
                child.kill()
                await child.wait()
        self.children.clear()

    def snapshot(self):
        engine_lines = []
        if self.engine_log and self.engine_log.exists():
            # Bound the read even after a long session.
            with self.engine_log.open("rb") as source:
                source.seek(max(0, self.engine_log.stat().st_size - 12000))
                engine_lines = source.read().decode("utf-8", "replace").splitlines()[-35:]
        progress = {**self.progress, "elapsed_seconds": max(0, int(time.monotonic() - self.operation_started))} if self.progress and self.operation_started is not None else None
        return {"version": __version__, "state": self.state, "message": self.message, "problem": self.problem, "devices": self.devices, "busy": bool(self.operation and not self.operation.done()), "progress": progress, "viewer": f"http://127.0.0.1:{self.engine_port}/" if self.state == "connected" else None, "camera": self.camera.snapshot(), "logs": self.logs, "engine_log": engine_lines}


def create_app(controller, port, token):
    @web.middleware
    async def local_only(request, handler):
        if request.host != f"127.0.0.1:{port}":
            raise web.HTTPForbidden()
        origin = request.headers.get("Origin")
        if origin and origin != f"http://127.0.0.1:{port}":
            raise web.HTTPForbidden()
        if request.path.startswith("/api/") and not secrets.compare_digest(request.headers.get("X-App-Token", ""), token):
            raise web.HTTPForbidden()
        response = await handler(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    app = web.Application(middlewares=[local_only], client_max_size=4096)

    async def index(request):
        content = (ASSETS / "index.html").read_text(encoding="utf-8").replace("__APP_TOKEN__", token)
        return web.Response(text=content, content_type="text/html")

    async def state(request):
        controller.last_seen = time.monotonic()
        return web.json_response(controller.snapshot())

    async def action(request):
        body = await request.json()
        command = body.get("action")
        if command not in ("scan", "connect", "prepare", "reveal", "wifi", "camera", "disconnect", "quit"):
            raise web.HTTPBadRequest(text="Unknown action")
        if command == "quit":
            controller.stop_event.set()
            return web.json_response({"ok": True})
        if command == "disconnect":
            if controller.operation and not controller.operation.done():
                controller.operation.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await controller.operation
            await controller.camera.stop()
            await controller.stop_session()
            return web.json_response({"ok": True})
        if controller.operation and not controller.operation.done():
            raise web.HTTPConflict(text="Operation in progress")
        if command == "scan" and controller.engine:
            raise web.HTTPConflict(text="Stop the current session before scanning")
        if controller.camera.active and command != "camera":
            raise web.HTTPConflict(text="Stop camera mode before starting another operation")
        device = body.get("device")
        transport = body.get("transport", "usb")
        if transport not in ("usb", "wifi"):
            raise web.HTTPBadRequest()
        if command not in ("scan", "camera") and not any(d["id"] == device and d["transport"] == transport for d in controller.devices):
            raise web.HTTPBadRequest(text="Select a discovered device")
        wifi_host = body.get("wifi_host") if command == "wifi" else None
        if wifi_host:
            from iphone_desktop.wireless import validate_wifi_host
            try:
                wifi_host = validate_wifi_host(wifi_host)
            except ValueError as exc:
                raise web.HTTPBadRequest(text=str(exc))
        controller.operation = asyncio.create_task(controller.perform(command, device, transport, wifi_host=wifi_host))
        return web.json_response({"ok": True})

    app.router.add_get("/", index)
    app.router.add_get("/api/state", state)
    app.router.add_post("/api/action", action)
    app.router.add_static("/assets", ASSETS)
    controller.camera.install_view_routes(app)

    async def camera_qr(request):
        if not controller.camera.active:
            raise web.HTTPConflict(text="Enable camera mode first")
        try:
            index = int(request.query.get("index", "0"))
            if index < 0:
                raise ValueError()
            url = controller.camera.sender_urls[index]
        except (ValueError, IndexError):
            raise web.HTTPBadRequest()
        import io
        import qrcode
        import qrcode.image.svg
        output = io.BytesIO()
        qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage).save(output)
        return web.Response(body=output.getvalue(), content_type="image/svg+xml")

    async def camera_control(request):
        if not controller.camera.active:
            raise web.HTTPConflict(text="Start webcam mode first.")
        try:
            command = await request.json()
            camera_state = await controller.camera.control(command)
        except (ValueError, TypeError) as exc:
            raise web.HTTPBadRequest(text=str(exc))
        except (RuntimeError, asyncio.TimeoutError) as exc:
            raise web.HTTPConflict(text=str(exc) or "The iPhone did not respond. Keep Safari open and retry.")
        return web.json_response({"ok": True, "camera_state": camera_state})

    app.router.add_get("/api/camera/qr", camera_qr)
    app.router.add_post("/api/camera/control", camera_control)
    return app


def launch_window(url):
    browsers = [Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe", Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe", Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Microsoft/Edge/Application/msedge.exe", Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe"]
    for browser in browsers:
        if browser.is_file():
            subprocess.Popen([str(browser), f"--app={url}", "--window-size=1250,880", "--no-first-run"], creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            return
    webbrowser.open(url)


async def serve(args):
    controller = Controller()
    port = args.port or free_port()
    token = secrets.token_urlsafe(32)
    app = create_app(controller, port, token)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    (RUNTIME / "session.json").write_text(json.dumps({"port": port, "token": token}), encoding="utf-8")
    controller.log(f"iPhone Desktop {__version__}. Local interface at 127.0.0.1:{port}.")
    if not args.no_window:
        launch_window(f"http://127.0.0.1:{port}/")
    try:
        while not controller.stop_event.is_set():
            try:
                await asyncio.wait_for(controller.stop_event.wait(), 3)
            except asyncio.TimeoutError:
                # Closing the desktop window ends the session, including USB
                # input workers. Allow for minimised-window timer throttling.
                if not args.no_window and time.monotonic() - controller.last_seen > 120:
                    break
    finally:
        await controller.shutdown()
        await runner.cleanup()


def main():
    parser = argparse.ArgumentParser(description="iPhone Desktop")
    parser.add_argument("--no-window", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    RUNTIME.mkdir(exist_ok=True)
    logging.basicConfig(filename=RUNTIME / "app.log", level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", encoding="utf-8")
    try:
        asyncio.run(serve(args))
    except KeyboardInterrupt:
        pass
    except Exception:
        logging.exception("Application failed")
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, f"Startup failed. See {RUNTIME / 'app.log'}", "iPhone Desktop", 0x10)
