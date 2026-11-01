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


class WorkerLifecycleTests(unittest.IsolatedAsyncioTestCase):
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
