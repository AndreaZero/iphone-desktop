import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from iphone_desktop.bridge import discover, execute, serve_stream
from iphone_desktop import wireless


class StreamShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_request_waits_for_server_cleanup_and_removes_sentinel(self):
        started = asyncio.Event()
        released = asyncio.Event()

        async def serve():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                # Simulate an asynchronous device-side stop RPC.
                await asyncio.sleep(0)
                released.set()

        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / "worker.json"
            stop = result.with_suffix(".stop")
            task = asyncio.create_task(serve_stream(SimpleNamespace(serve=serve), result))
            try:
                await asyncio.wait_for(started.wait(), 1)
                stop.touch()
                await asyncio.wait_for(task, 2)
                self.assertTrue(released.is_set())
                self.assertFalse(stop.exists())
            finally:
                if not task.done():
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task

    async def test_worker_cancellation_releases_server_resources(self):
        started = asyncio.Event()
        released = asyncio.Event()

        async def serve():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                released.set()

        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / "worker.json"
            task = asyncio.create_task(serve_stream(SimpleNamespace(serve=serve), result))
            await asyncio.wait_for(started.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            self.assertTrue(released.is_set())
            self.assertFalse(result.with_suffix(".stop").exists())

    async def test_server_failure_reaches_worker_error_handler(self):
        async def serve():
            raise RuntimeError("Device connection lost")

        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / "worker.json"
            with self.assertRaisesRegex(RuntimeError, "Device connection lost"):
                await asyncio.wait_for(serve_stream(SimpleNamespace(serve=serve), result), 2)

    async def test_cleanup_failure_is_not_reported_as_success(self):
        started = asyncio.Event()

        async def serve():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                raise RuntimeError("Could not release media session")

        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / "worker.json"
            stop = result.with_suffix(".stop")
            task = asyncio.create_task(serve_stream(SimpleNamespace(serve=serve), result))
            await asyncio.wait_for(started.wait(), 1)
            stop.touch()
            with self.assertRaisesRegex(RuntimeError, "Could not release media session"):
                await asyncio.wait_for(task, 2)
            self.assertFalse(stop.exists())

    async def test_cancelled_preflight_releases_device_without_server_cleanup(self):
        started = asyncio.Event()
        rsd = object()

        async def serve():
            # The upstream preflight can start audio before reaching the
            # server's try/finally. Deliberately provide no cleanup here.
            started.set()
            await asyncio.Event().wait()

        release = AsyncMock()
        with tempfile.TemporaryDirectory() as directory, patch("pymobiledevice3.remote.core_device.display_service.DisplayService.stop_all_streams", release):
            result = Path(directory) / "worker.json"
            stop = result.with_suffix(".stop")
            task = asyncio.create_task(serve_stream(SimpleNamespace(serve=serve), result, rsd))
            await asyncio.wait_for(started.wait(), 1)
            stop.touch()
            await asyncio.wait_for(task, 2)
            release.assert_awaited_once_with(rsd)
            self.assertFalse(stop.exists())

    async def test_release_failure_preserves_stream_error_and_removes_stop_request(self):
        rsd = object()
        stop = None

        async def serve():
            stop.touch()
            raise RuntimeError("Original stream failure")

        release = AsyncMock(side_effect=OSError("Device is unreachable"))
        with tempfile.TemporaryDirectory() as directory, patch("pymobiledevice3.remote.core_device.display_service.DisplayService.stop_all_streams", release):
            result = Path(directory) / "worker.json"
            stop = result.with_suffix(".stop")
            with self.assertLogs(level="ERROR") as logged:
                with self.assertRaisesRegex(RuntimeError, "Original stream failure"):
                    await asyncio.wait_for(serve_stream(SimpleNamespace(serve=serve), result, rsd), 2)
            release.assert_awaited_once_with(rsd)
            self.assertIn("Could not release the device media session", "\n".join(logged.output))
            self.assertFalse(stop.exists())


class DiscoveryTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_usb_scan_skips_bonjour_and_network_device_handshakes(self):
        usb = SimpleNamespace(serial="usb-phone", is_usb=True, connection_type="USB")
        network = SimpleNamespace(serial="wifi-phone", is_usb=False, connection_type="Network")
        phone = AsyncMock()
        phone.__aenter__.return_value = phone
        phone.all_values = {"DeviceName": "Test iPhone"}
        phone.product_version = "27.2"
        phone.product_type = "iPhone18,1"
        phone.paired = True
        create = AsyncMock(return_value=phone)
        bonjour = AsyncMock()
        with patch("pymobiledevice3.usbmux.list_devices", AsyncMock(return_value=[network, usb])), patch("pymobiledevice3.lockdown.create_using_usbmux", create), patch.object(wireless, "discover_wifi", bonjour), patch.object(wireless, "saved_wifi_devices", return_value={}):
            devices = await discover("usb")
        self.assertEqual([device["id"] for device in devices], ["usb-phone"])
        self.assertEqual(devices[0]["transport"], "usb")
        create.assert_awaited_once_with(serial="usb-phone", connection_type="USB", autopair=False)
        bonjour.assert_not_awaited()

    async def test_discovery_worker_uses_requested_transport(self):
        args = SimpleNamespace(action="discover", transport="usb")
        with patch("iphone_desktop.bridge.discover", AsyncMock(return_value=[])) as scan:
            self.assertEqual(await execute(args), {"devices": []})
        scan.assert_awaited_once_with("usb")


if __name__ == "__main__":
    unittest.main()
