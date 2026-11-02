import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from iphone_desktop.app import Controller, create_app
from iphone_desktop.errors import explain_error
from iphone_desktop.bridge import prepare_services, require_stream_services


class ErrorTests(unittest.TestCase):
    def test_actionable_device_failures(self):
        cases = {"DeveloperModeIsNotEnabledError": "developer_mode", "PasswordRequiredError": "locked", "PairingDialogResponsePendingError": "trust", "NoMuxDeviceFoundError": "no_device", "ConnectionFailedToUsbmuxdError": "apple_driver", "NotMountedError": "developer_image", "InvalidServiceError": "unsupported_service", "TimeoutError": "timeout"}
        for error, expected in cases.items():
            with self.subTest(error=error):
                self.assertEqual(explain_error(error).code, expected)


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.controller = Controller()
        self.server = TestServer(create_app(self.controller, 1, "test-token"))
        self.client = TestClient(self.server)
        await self.client.start_server()
        # The security boundary must match the actual socket the test created.
        self.port = self.server.port
        self.headers = {"Host": "127.0.0.1:1", "X-App-Token": "test-token"}

    async def asyncTearDown(self):
        await self.controller.shutdown()
        await self.client.close()

    async def test_local_api_requires_token_and_correct_origin(self):
        response = await self.client.get("/api/state", headers={"Host": "127.0.0.1:1"})
        self.assertEqual(response.status, 403)
        response = await self.client.get("/api/state", headers={**self.headers, "Origin": "https://other.example"})
        self.assertEqual(response.status, 403)
        response = await self.client.get("/api/state", headers={**self.headers, "Host": "rebound.example:1"})
        self.assertEqual(response.status, 403)
        response = await self.client.get("/api/state", headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertIsNone((await response.json())["viewer"])

    async def test_cannot_control_undiscovered_device(self):
        response = await self.client.post("/api/action", headers=self.headers, json={"action": "connect", "device": "unknown", "transport": "usb"})
        self.assertEqual(response.status, 400)
        self.assertIsNone(self.controller.operation)

    async def test_disconnect_cancels_pending_operation(self):
        cancelled = asyncio.Event()
        async def slow_operation():
            try:
                await asyncio.sleep(100)
            finally:
                cancelled.set()
        self.controller.operation = asyncio.create_task(slow_operation())
        await asyncio.sleep(0)
        response = await self.client.post("/api/action", headers=self.headers, json={"action": "disconnect"})
        self.assertEqual(response.status, 200)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(self.controller.state, "idle")

    async def test_failed_connection_does_not_publish_viewer(self):
        async def fail(*args, **kwargs):
            raise RuntimeError("Developer mode is not enabled")
        with patch.object(self.controller, "run_worker", side_effect=fail):
            await self.controller.perform("connect", "phone", "usb")
        self.assertEqual(self.controller.state, "error")
        self.assertEqual(self.controller.problem["code"], "developer_mode")
        self.assertIsNone(self.controller.snapshot()["viewer"])

    async def test_pending_discovery_reports_progress_and_remains_cancellable(self):
        started = asyncio.Event()
        async def discover(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()
        with patch.object(self.controller, "run_worker", side_effect=discover) as worker:
            response = await self.client.post("/api/action", headers=self.headers, json={"action": "scan", "transport": "wifi"})
            self.assertEqual(response.status, 200)
            await asyncio.wait_for(started.wait(), 1)
            response = await self.client.get("/api/state", headers=self.headers)
            data = await response.json()
            self.assertEqual(data["state"], "scanning")
            self.assertTrue(data["busy"])
            self.assertIn("local network", data["message"])
            self.assertGreaterEqual(data["progress"]["elapsed_seconds"], 0)
            worker.assert_awaited_once_with("discover", transport="wifi", timeout=40)
            response = await self.client.post("/api/action", headers=self.headers, json={"action": "disconnect"})
            self.assertEqual(response.status, 200)
        self.assertIsNone(self.controller.snapshot()["progress"])
        self.assertEqual(self.controller.state, "idle")

    async def test_scanning_one_transport_keeps_other_known_devices(self):
        self.controller.devices = [{"id": "usb-phone", "transport": "usb"}, {"id": "old-wifi", "transport": "wifi"}]
        with patch.object(self.controller, "run_worker", AsyncMock(return_value={"devices": [{"id": "wifi-phone", "transport": "wifi"}]})):
            await self.controller.perform("scan", transport="wifi")
        self.assertEqual([d["id"] for d in self.controller.devices], ["usb-phone", "wifi-phone"])
        self.assertEqual(self.controller.state, "idle")
        self.assertIsNone(self.controller.snapshot()["progress"])

    async def test_camera_action_needs_no_discovered_phone_and_reports_source(self):
        async def start_camera():
            self.controller.camera.active = True
            self.controller.camera.view_token = "camera-source-token"
            self.controller.camera.sender_urls = ["http://192.168.1.10:12345/sender-token/"]
            self.controller.camera.fingerprint = "test-certificate-sha256"
        with patch.object(self.controller.camera, "start", AsyncMock(side_effect=start_camera)) as start:
            response = await self.client.post("/api/action", headers=self.headers, json={"action": "camera"})
            self.assertEqual(response.status, 200)
            await self.controller.operation
            start.assert_awaited_once_with()
        response = await self.client.get("/api/state", headers=self.headers)
        data = await response.json()
        self.assertEqual(data["state"], "camera")
        self.assertFalse(data["busy"])
        self.assertEqual(data["devices"], [])
        self.assertIsNone(data["viewer"])
        self.assertEqual(data["camera"], {"active": True, "connected": False,
            "sender_urls": ["http://192.168.1.10:12345/sender-token/"],
            "source_path": "/camera/camera-source-token/view",
            "certificate_sha256": "test-certificate-sha256", "camera_state": None})

    async def test_camera_waits_for_screen_shutdown_before_starting_relay(self):
        stop_started, release_stop = asyncio.Event(), asyncio.Event()
        order = []
        self.controller.engine = object()
        async def stop_screen():
            order.append("stopping-screen")
            stop_started.set()
            await release_stop.wait()
            self.controller.engine = None
            order.append("screen-stopped")
        async def start_camera():
            order.append("camera-started")
            self.controller.camera.active = True
        with patch.object(self.controller, "stop_session", AsyncMock(side_effect=stop_screen)), patch.object(self.controller.camera, "start", AsyncMock(side_effect=start_camera)) as start:
            try:
                response = await self.client.post("/api/action", headers=self.headers, json={"action": "camera"})
                self.assertEqual(response.status, 200)
                await asyncio.wait_for(stop_started.wait(), 1)
                start.assert_not_awaited()
                response = await self.client.get("/api/state", headers=self.headers)
                self.assertTrue((await response.json())["busy"])
            finally:
                release_stop.set()
                if self.controller.operation:
                    await self.controller.operation
        self.assertEqual(order, ["stopping-screen", "screen-stopped", "camera-started"])
        self.assertEqual(self.controller.state, "camera")

    async def test_camera_mode_blocks_discovery_and_screen_connections(self):
        self.controller.camera.active = True
        self.controller.devices = [{"id": "phone", "transport": "usb"}]
        for action in ("scan", "connect"):
            with self.subTest(action=action):
                response = await self.client.post("/api/action", headers=self.headers,
                    json={"action": action, "device": "phone", "transport": "usb"})
                self.assertEqual(response.status, 409)
        self.assertIsNone(self.controller.operation)
        self.assertTrue(self.controller.camera.active)

    async def test_disconnect_stops_camera_and_revokes_source_routes(self):
        self.controller.state = "camera"
        self.controller.camera.active = True
        self.controller.camera.view_token = "revoked-source-token"
        self.controller.camera.sender_token = "revoked-sender-token"
        self.controller.camera.sender_urls = ["http://192.168.1.10:12345/revoked-sender-token/"]
        source = "/camera/revoked-source-token/view"
        response = await self.client.get(source, headers={"Host": "127.0.0.1:1"})
        self.assertEqual(response.status, 200)
        response = await self.client.post("/api/action", headers=self.headers, json={"action": "disconnect"})
        self.assertEqual(response.status, 200)
        self.assertEqual(self.controller.state, "idle")
        self.assertFalse(self.controller.camera.active)
        self.assertIsNone(self.controller.camera.sender_token)
        self.assertIsNone(self.controller.camera.view_token)
        self.assertEqual(self.controller.camera.sender_urls, [])
        self.assertIsNone(self.controller.snapshot()["camera"]["source_path"])
        response = await self.client.get(source, headers={"Host": "127.0.0.1:1"})
        self.assertEqual(response.status, 403)

    async def test_camera_control_endpoint_authenticates_and_reports_errors(self):
        response = await self.client.post("/api/camera/control", headers={"Host": "127.0.0.1:1"}, json={"name": "zoom", "value": 2})
        self.assertEqual(response.status, 403)
        response = await self.client.post("/api/camera/control", headers=self.headers, json={"name": "zoom", "value": 2})
        self.assertEqual(response.status, 409)
        self.controller.camera.active = True
        with patch.object(self.controller.camera, "control", AsyncMock(return_value={"controls": {}})) as control:
            response = await self.client.post("/api/camera/control", headers=self.headers, json={"name": "zoom", "value": 2})
            self.assertEqual(response.status, 200)
            self.assertEqual((await response.json())["camera_state"], {"controls": {}})
            control.assert_awaited_once_with({"name": "zoom", "value": 2})
        for exception, status in ((ValueError("Zoom is not supported"), 400), (RuntimeError("Keep Safari open"), 409)):
            with patch.object(self.controller.camera, "control", AsyncMock(side_effect=exception)):
                response = await self.client.post("/api/camera/control", headers=self.headers, json={"name": "zoom", "value": 2})
                self.assertEqual(response.status, status)
                self.assertEqual(await response.text(), str(exception))

    async def test_camera_source_requires_its_secret_and_qr_requires_app_token(self):
        self.controller.camera.active = True
        self.controller.camera.view_token = "source-secret"
        self.controller.camera.sender_urls = ["http://192.168.1.10:12345/sender-secret/"]
        response = await self.client.get("/camera/wrong-secret/view", headers={"Host": "127.0.0.1:1"})
        self.assertEqual(response.status, 403)
        response = await self.client.get("/api/camera/qr", headers={"Host": "127.0.0.1:1"})
        self.assertEqual(response.status, 403)
        response = await self.client.get("/api/camera/qr", headers=self.headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "image/svg+xml")
        self.assertIn(b"<svg", await response.read())
        for index in ("-1", "1", "invalid"):
            response = await self.client.get("/api/camera/qr", params={"index": index}, headers=self.headers)
            self.assertEqual(response.status, 400)


class WorkerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_stop_requests_cleanup_before_terminating_worker(self):
        controller = Controller()
        with tempfile.TemporaryDirectory() as folder:
            controller.engine_result = Path(folder) / "stream.json"
            class Engine:
                returncode = None
                terminate = unittest.mock.Mock()
                async def wait(self):
                    if controller.engine_result.with_suffix(".stop").exists():
                        self.returncode = 0
                    return self.returncode
            engine = Engine()
            controller.engine = engine
            controller.children.add(engine)
            await controller.stop_session()
            self.assertTrue(controller.engine_result.with_suffix(".stop").exists())
        engine.terminate.assert_not_called()
        self.assertIsNone(controller.engine)
        self.assertFalse(controller.children)

    async def test_timed_out_worker_is_killed_and_reaped(self):
        class HungWorker:
            returncode = None
            killed = False
            async def wait(self):
                if self.killed:
                    return 1
                await asyncio.sleep(100)
            def kill(self):
                self.killed = True
                self.returncode = 1
        worker = HungWorker()
        controller = Controller()
        controller.children.add(worker)
        with tempfile.TemporaryDirectory() as folder:
            async def spawn(*args, **kwargs):
                return worker, Path(folder) / "result.json", Path(folder) / "worker.log"
            with patch.object(controller, "spawn", side_effect=spawn):
                with self.assertRaises(TimeoutError):
                    await controller.run_worker("discover", timeout=.02)
        self.assertTrue(worker.killed)
        self.assertEqual(controller.children, set())


class StreamStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_ios27_preparation_uses_rsd_and_accepts_existing_image(self):
        from pymobiledevice3.exceptions import AlreadyMountedError
        phone = SimpleNamespace(product_version="27.2", udid="test-phone")
        rsd = object()
        tunnel = AsyncMock()
        tunnel.__aenter__.return_value = rsd
        with patch("pymobiledevice3.remote.userspace_tunnel.UserspaceRsdTunnel", return_value=tunnel) as factory, patch("pymobiledevice3.services.mobile_image_mounter.auto_mount", new_callable=AsyncMock) as mount:
            await prepare_services(phone)
            factory.assert_called_once_with(serial="test-phone")
            mount.assert_awaited_once_with(rsd)
            mount.side_effect = AlreadyMountedError()
            await prepare_services(phone)
        self.assertEqual(tunnel.__aexit__.await_count, 2)

    async def test_missing_device_services_fail_before_viewer_starts(self):
        with self.assertRaisesRegex(RuntimeError, "ScreenServicesUnavailable") as failure:
            require_stream_services(SimpleNamespace(peer_info={"Services": {}}))
        self.assertEqual(explain_error(str(failure.exception)).code, "screen_services")

    async def check_video_endpoint(self, responses):
        calls = []
        async def codec(request):
            calls.append(request.path)
            value = responses[min(len(calls) - 1, len(responses) - 1)]
            return web.json_response(value) if isinstance(value, dict) else web.Response(status=value)
        async def index(request):
            return web.Response(text="viewer")
        app = web.Application()
        app.router.add_get("/", index)
        app.router.add_get("/codec", codec)
        server = TestServer(app)
        await server.start_server()
        self.addAsyncCleanup(server.close)
        controller = Controller()
        controller.engine_port = server.port
        controller.engine = SimpleNamespace(returncode=None)
        controller.engine_result = Path(tempfile.gettempdir()) / "nonexistent-iphone-result.json"
        return controller, calls

    async def test_http500_is_failure_even_with_working_html(self):
        controller, calls = await self.check_video_endpoint([500])
        with self.assertRaisesRegex(RuntimeError, "HTTP 500"):
            await controller.wait_for_video(timeout=2)
        self.assertEqual(calls, ["/codec"])
        self.assertIsNone(controller.snapshot()["viewer"])

    async def test_waits_through_cold_start_and_incomplete_codec(self):
        controller, calls = await self.check_video_endpoint([503, {"codec": "hev1"}, {"codec": "hev1", "description": "configuration"}])
        await controller.wait_for_video(timeout=3)
        self.assertEqual(len(calls), 3)

    async def test_failed_video_start_cleans_up_engine_after_preparation(self):
        controller = Controller()
        class Engine:
            returncode = None
        engine = Engine()
        async def wait():
            return engine.returncode
        def terminate():
            engine.returncode = 1
        engine.wait, engine.terminate = wait, terminate
        with tempfile.TemporaryDirectory() as folder:
            result, log = Path(folder) / "result.json", Path(folder) / "worker.log"
            worker = AsyncMock(side_effect=[{"name": "iPhone", "ios": "27.2", "developer_mode": True}, {"message": "prepared"}])
            with patch.object(controller, "run_worker", worker), patch.object(controller, "spawn", AsyncMock(return_value=(engine, result, log))), patch.object(controller, "wait_for_video", AsyncMock(side_effect=RuntimeError("Video startup failed: HTTP 500"))):
                await controller.perform("connect", "phone", "usb")
        self.assertEqual(worker.await_args_list[1].args, ("prepare", "phone", "usb"))
        self.assertEqual(engine.returncode, 1)
        self.assertEqual(controller.state, "error")
        self.assertIsNone(controller.engine)
        self.assertIsNone(controller.snapshot()["viewer"])


if __name__ == "__main__":
    unittest.main()
