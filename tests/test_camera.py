import asyncio
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from aiohttp import WSMsgType, WSServerHandshakeError, web
from aiohttp.test_utils import TestClient, TestServer

from iphone_desktop.camera import CameraRelay, MAX_FRAME, MAX_CONTROL_TEXT, STALE_SECONDS, camera_certificates, sanitize_camera_state


ASSETS = Path(__file__).resolve().parents[1] / "iphone_desktop" / "web"
JPEG_FRAME = b"\xff\xd8test-camera-frame\xff\xd9"


def reported_camera(zoom=1):
    return {"devices": [{"id": "back", "label": "Back camera"}, {"id": "front", "label": "Front camera"}],
            "selectedDevice": "back", "facingMode": "environment", "width": 1280, "height": 720,
            "settings": {"zoom": zoom, "frameRate": 20}, "busy": False,
            "controls": {
                "zoom": {"type": "range", "min": 1, "max": 8, "step": .1, "value": zoom, "kind": "digital", "label": "Digital zoom", "unit": "×"},
                "exposureMode": {"type": "select", "options": ["continuous", "manual"], "value": "continuous", "label": "Exposure mode"},
                "torch": {"type": "toggle", "value": False, "label": "Torch"}}}


class CameraRelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.relay = CameraRelay(self.directory.name, ASSETS)
        self.relay.active = True
        self.relay.sender_token = "test-sender"
        self.relay.view_token = "test-viewer"
        self.relay.addresses = ["192.168.1.10"]
        self.relay.https_port = 443
        self.relay.ca_der = b"test-certificate"
        self.relay.fingerprint = "test-fingerprint"
        self.sender = TestClient(TestServer(self.relay.sender_app(secure=True)))
        await self.sender.start_server()
        view_app = web.Application()
        self.relay.install_view_routes(view_app)
        self.viewer = TestClient(TestServer(view_app))
        await self.viewer.start_server()

    async def asyncTearDown(self):
        await self.relay.stop()
        await self.sender.close()
        await self.viewer.close()
        self.directory.cleanup()

    async def camera_phone(self):
        phone = await self.sender.ws_connect("/test-sender/upload")
        await phone.send_json({"type": "camera-state", "state": reported_camera()})
        for _ in range(100):
            if self.relay.camera_state is not None:
                return phone
            await asyncio.sleep(.001)
        await phone.close()
        self.fail("Phone camera capabilities were not received")

    async def test_camera_controls_round_trip_without_interrupting_video(self):
        phone = await self.camera_phone()
        try:
            adjustment = asyncio.create_task(self.relay.control({"name": "zoom", "value": 2}))
            command = await phone.receive_json(timeout=2)
            self.assertEqual(command["type"], "camera-command")
            self.assertEqual(command["command"], {"name": "zoom", "value": 2})
            self.assertTrue(self.relay.snapshot()["camera_state"]["busy"])
            await phone.send_bytes(JPEG_FRAME)
            self.assertEqual(await phone.receive_str(timeout=2), "ok")
            await phone.send_json({"type": "camera-result", "id": command["id"], "ok": True, "state": reported_camera(2)})
            state = await asyncio.wait_for(adjustment, 2)
            self.assertEqual(state["controls"]["zoom"]["value"], 2)
            self.assertFalse(self.relay.snapshot()["camera_state"]["busy"])
            self.assertEqual(self.relay.frame, JPEG_FRAME)
        finally:
            await phone.close()

    async def test_invalid_or_unreported_adjustments_never_reach_the_phone(self):
        phone = await self.camera_phone()
        try:
            invalid = [None, {"name": "iso", "value": 100}, {"name": "zoom", "value": 99},
                {"name": "zoom", "value": True}, {"name": "zoom", "value": float("nan")},
                {"name": "zoom", "value": 10 ** 1000}, {"name": "zoom", "value": 2, "extra": True},
                {"name": "exposureMode", "value": "unsupported"}, {"name": "torch", "value": "yes"},
                {"name": "deviceId", "value": "unreported-lens"}, {"name": "facingMode", "value": "rear"},
                {"name": "reset", "value": 1}]
            for command in invalid:
                with self.subTest(command=command), self.assertRaises(ValueError):
                    await self.relay.control(command)
            with self.assertRaises(asyncio.TimeoutError):
                await phone.receive(timeout=.03)
            await phone.send_bytes(JPEG_FRAME)
            self.assertEqual(await phone.receive_str(timeout=2), "ok")
        finally:
            await phone.close()

    async def test_busy_and_rejected_controls_do_not_queue_or_stop_video(self):
        phone = await self.camera_phone()
        try:
            adjustment = asyncio.create_task(self.relay.control({"name": "zoom", "value": 2}))
            command = await phone.receive_json(timeout=2)
            with self.assertRaisesRegex(RuntimeError, "adjusting"):
                await self.relay.control({"name": "zoom", "value": 3})
            await phone.send_json({"type": "camera-result", "id": command["id"], "ok": False,
                "error": "This camera rejected the adjustment.", "state": reported_camera()})
            with self.assertRaisesRegex(RuntimeError, "rejected"):
                await asyncio.wait_for(adjustment, 2)
            self.assertIsNone(self.relay.pending_control)
            self.assertEqual(self.relay.camera_state["settings"]["zoom"], 1)
            await phone.send_bytes(JPEG_FRAME)
            self.assertEqual(await phone.receive_str(timeout=2), "ok")
        finally:
            await phone.close()

    async def test_timed_out_reply_cannot_complete_or_overwrite_the_next_adjustment(self):
        phone = await self.camera_phone()
        try:
            with patch("iphone_desktop.camera.CONTROL_TIMEOUT", .03):
                adjustment = asyncio.create_task(self.relay.control({"name": "zoom", "value": 2}))
                old = await phone.receive_json(timeout=2)
                with self.assertRaisesRegex(TimeoutError, "did not confirm"):
                    await adjustment
            self.assertIsNone(self.relay.pending_control)
            next_adjustment = asyncio.create_task(self.relay.control({"name": "zoom", "value": 3}))
            current = await phone.receive_json(timeout=2)
            self.assertNotEqual(old["id"], current["id"])
            await phone.send_json({"type": "camera-result", "id": old["id"], "ok": True, "state": reported_camera(8)})
            await phone.send_bytes(JPEG_FRAME)
            self.assertEqual(await phone.receive_str(timeout=2), "ok")
            self.assertFalse(next_adjustment.done())
            self.assertEqual(self.relay.camera_state["settings"]["zoom"], 1)
            await phone.send_json({"type": "camera-result", "id": current["id"], "ok": True, "state": reported_camera(3)})
            self.assertEqual((await asyncio.wait_for(next_adjustment, 2))["settings"]["zoom"], 3)
        finally:
            await phone.close()

    async def test_disconnected_publisher_rejects_pending_control_and_clears_settings(self):
        phone = await self.camera_phone()
        adjustment = asyncio.create_task(self.relay.control({"name": "zoom", "value": 2}))
        old = await phone.receive_json(timeout=2)
        await phone.close()
        with self.assertRaisesRegex(RuntimeError, "disconnected"):
            await asyncio.wait_for(adjustment, 2)
        self.assertIsNone(self.relay.snapshot()["camera_state"])
        self.assertIsNone(self.relay.pending_control)
        replacement = await self.camera_phone()
        try:
            await replacement.send_json({"type": "camera-result", "id": old["id"], "ok": True, "state": reported_camera(8)})
            await replacement.send_bytes(JPEG_FRAME)
            self.assertEqual(await replacement.receive_str(timeout=2), "ok")
            self.assertEqual(self.relay.camera_state["settings"]["zoom"], 1)
        finally:
            await replacement.close()

    async def test_stopping_rejects_pending_control_immediately(self):
        phone = await self.camera_phone()
        try:
            adjustment = asyncio.create_task(self.relay.control({"name": "torch", "value": True}))
            await phone.receive_json(timeout=2)
            await self.relay.stop()
            with self.assertRaisesRegex(RuntimeError, "disconnected"):
                await asyncio.wait_for(adjustment, 2)
            self.assertIsNone(self.relay.camera_state)
            self.assertIsNone(self.relay.pending_control)
        finally:
            await phone.close()

    async def test_camera_without_reported_controls_keeps_streaming(self):
        phone = await self.sender.ws_connect("/test-sender/upload")
        try:
            with self.assertRaisesRegex(RuntimeError, "Start the camera"):
                await self.relay.control({"name": "zoom", "value": 2})
            await phone.send_bytes(JPEG_FRAME)
            self.assertEqual(await phone.receive_str(timeout=2), "ok")
            self.assertIsNone(self.relay.camera_state)
        finally:
            await phone.close()

    async def test_settings_text_has_a_separate_small_limit(self):
        phone = await self.sender.ws_connect("/test-sender/upload")
        await phone.send_str(" " * (MAX_CONTROL_TEXT + 1))
        self.assertEqual((await phone.receive(timeout=2)).type, WSMsgType.CLOSE)
        self.assertEqual(phone.close_code, 1009)
        self.assertIsNone(self.relay.camera_state)

    async def test_invalid_settings_json_is_rejected(self):
        phone = await self.sender.ws_connect("/test-sender/upload")
        await phone.send_str('{"type":"camera-state","state":{"width":NaN}}')
        self.assertEqual((await phone.receive(timeout=2)).type, WSMsgType.CLOSE)
        self.assertEqual(phone.close_code, 1003)
        self.assertIsNone(self.relay.camera_state)

    async def test_sender_rejects_wrong_secret_host_and_origin(self):
        for path, headers in (
            ("/wrong/send", {}),
            ("/test-sender/send", {"Host": "other.example"}),
            ("/test-sender/send", {"Origin": "https://other.example"}),
        ):
            with self.subTest(path=path, headers=headers):
                response = await self.sender.get(path, headers=headers)
                self.assertEqual(response.status, 403)
        for path, headers in (
            ("/wrong/upload", {}),
            ("/test-sender/upload", {"Origin": "https://other.example"}),
        ):
            with self.subTest(websocket_path=path):
                with self.assertRaises(WSServerHandshakeError) as raised:
                    await self.sender.ws_connect(path, headers=headers)
                self.assertEqual(raised.exception.status, 403)
        self.assertIsNone(self.relay.sender)

    async def test_only_one_phone_can_publish_at_a_time(self):
        connection = await self.sender.ws_connect("/test-sender/upload")
        try:
            with self.assertRaises(WSServerHandshakeError) as raised:
                await self.sender.ws_connect("/test-sender/upload")
            self.assertEqual(raised.exception.status, 409)
            await connection.send_bytes(JPEG_FRAME)
            self.assertEqual((await connection.receive(timeout=2)).data, "ok")
        finally:
            await connection.close()

    async def test_uploaded_frame_reaches_preview_and_disconnect_clears_it(self):
        phone = await self.sender.ws_connect("/test-sender/upload")
        preview = await self.viewer.ws_connect("/camera/test-viewer/frames")
        try:
            await phone.send_bytes(JPEG_FRAME)
            self.assertEqual((await phone.receive(timeout=2)).data, "ok")
            while True:
                message = await preview.receive(timeout=2)
                if message.type == WSMsgType.BINARY:
                    break
                self.assertEqual(message.data, "waiting")
            self.assertEqual(message.data, JPEG_FRAME)
            self.assertTrue(self.relay.snapshot()["connected"])
            await phone.close()
            while True:
                message = await preview.receive(timeout=2)
                if message.type == WSMsgType.TEXT:
                    break
            self.assertEqual(message.data, "waiting")
            self.assertIsNone(self.relay.frame)
            self.assertFalse(self.relay.snapshot()["connected"])
        finally:
            await phone.close()
            await preview.close()

    async def test_non_jpeg_input_is_rejected_without_publishing(self):
        phone = await self.sender.ws_connect("/test-sender/upload")
        await phone.send_bytes(b"not-an-image")
        message = await phone.receive(timeout=2)
        self.assertEqual(message.type, WSMsgType.CLOSE)
        self.assertEqual(phone.close_code, 1003)
        self.assertIsNone(self.relay.frame)
        self.assertFalse(self.relay.snapshot()["connected"])

    async def test_oversized_frame_is_rejected_without_storing_it(self):
        phone = await self.sender.ws_connect("/test-sender/upload")
        await phone.send_bytes(b"\xff\xd8" + b"x" * MAX_FRAME + b"\xff\xd9")
        self.assertEqual((await phone.receive(timeout=2)).type, WSMsgType.CLOSE)
        self.assertEqual(phone.close_code, 1009)
        self.assertIsNone(self.relay.frame)

    async def test_stale_frame_is_not_displayed_as_live_video(self):
        self.relay.sender = object()
        self.relay.frame = JPEG_FRAME
        self.relay.frame_time = time.monotonic() - STALE_SECONDS - 1
        self.assertFalse(self.relay.snapshot()["connected"])
        self.relay.sender = None
        preview = await self.viewer.ws_connect("/camera/test-viewer/frames")
        try:
            message = await preview.receive(timeout=2)
            self.assertEqual(message.type, WSMsgType.TEXT)
            self.assertEqual(message.data, "waiting")
        finally:
            await preview.close()

    async def test_stopping_revokes_sender_and_viewer_links(self):
        await self.relay.stop()
        state = self.relay.snapshot()
        self.assertFalse(state["active"])
        self.assertFalse(state["connected"])
        self.assertEqual(state["sender_urls"], [])
        self.assertIsNone(state["source_path"])
        self.assertIsNone(self.relay.sender_token)
        self.assertIsNone(self.relay.view_token)
        self.assertEqual((await self.sender.get("/test-sender/send")).status, 403)
        self.assertEqual((await self.viewer.get("/camera/test-viewer/view")).status, 403)
        with self.assertRaises(WSServerHandshakeError) as raised:
            await self.viewer.ws_connect("/camera/test-viewer/frames")
        self.assertEqual(raised.exception.status, 403)

    async def test_http_bootstrap_exposes_setup_only(self):
        bootstrap = TestClient(TestServer(self.relay.sender_app(secure=False)))
        await bootstrap.start_server()
        try:
            response = await bootstrap.get("/test-sender/")
            self.assertEqual(response.status, 200)
            self.assertIn("Certificate Trust Settings", await response.text())
            certificate = await bootstrap.get("/test-sender/camera.cer")
            self.assertEqual(await certificate.read(), b"test-certificate")
            self.assertEqual(certificate.headers["Cache-Control"], "no-store")
            self.assertEqual((await bootstrap.get("/test-sender/upload")).status, 403)
            self.assertEqual((await bootstrap.get("/test-sender/send")).status, 403)
        finally:
            await bootstrap.close()


class CameraLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_and_stop_release_listeners_and_rotate_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            relay = CameraRelay(directory, ASSETS)
            try:
                with patch("iphone_desktop.camera.local_addresses", return_value=["192.168.1.10"]):
                    await relay.start()
                    first_token = relay.sender_token
                    self.assertTrue(relay.snapshot()["active"])
                    self.assertEqual(len(relay.runners), 2)
                    self.assertTrue(relay.snapshot()["sender_urls"][0].startswith("http://192.168.1.10:"))
                    await relay.stop()
                    self.assertEqual(relay.runners, [])
                    await relay.start()
                    self.assertNotEqual(relay.sender_token, first_token)
            finally:
                await relay.stop()
            self.assertFalse(relay.active)
            self.assertEqual(relay.runners, [])

    async def test_missing_network_does_not_leave_camera_active(self):
        with tempfile.TemporaryDirectory() as directory:
            relay = CameraRelay(directory, ASSETS)
            with patch("iphone_desktop.camera.local_addresses", return_value=[]):
                with self.assertRaisesRegex(RuntimeError, "CameraNetworkUnavailable"):
                    await relay.start()
            self.assertFalse(relay.active)
            self.assertEqual(relay.runners, [])


class CameraCertificateTests(unittest.TestCase):
    def test_local_ca_persists_and_server_certificate_covers_lan_address(self):
        from cryptography import x509
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import ExtendedKeyUsageOID

        with tempfile.TemporaryDirectory() as directory:
            cert, key, ca_der, fingerprint = camera_certificates(Path(directory), ["192.168.1.10"])
            self.assertTrue(key.is_file())
            leaf = x509.load_pem_x509_certificate(cert.read_bytes())
            ca = x509.load_der_x509_certificate(ca_der)
            ca.public_key().verify(leaf.signature, leaf.tbs_certificate_bytes, ec.ECDSA(leaf.signature_hash_algorithm))
            self.assertFalse(leaf.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
            self.assertTrue(ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca)
            self.assertIn(ExtendedKeyUsageOID.SERVER_AUTH, leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value)
            sans = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            self.assertEqual({str(ip) for ip in sans.get_values_for_type(x509.IPAddress)}, {"127.0.0.1", "192.168.1.10"})
            _, _, next_ca, next_fingerprint = camera_certificates(Path(directory), ["192.168.1.20"])
            self.assertEqual(next_ca, ca_der)
            self.assertEqual(next_fingerprint, fingerprint)


class CameraSettingsTests(unittest.TestCase):
    def test_only_supported_bounded_metadata_reaches_the_desktop(self):
        reported = reported_camera()
        reported["unexpected"] = {"private": "data"}
        reported["settings"]["unexpected"] = "data"
        reported["settings"]["iso"] = float("inf")
        reported["controls"]["zoom"]["unused"] = "data"
        reported["controls"]["iso"] = {"type": "range", "min": 0, "max": 100, "step": 1, "value": float("nan")}
        reported["controls"]["focusDistance"] = {"type": "range", "min": 0, "max": 100, "step": 1, "value": 10 ** 1000}
        reported["controls"]["unknown"] = {"type": "toggle", "value": True}
        reported["devices"][0]["label"] = "a" * 1000
        reported["devices"].append({"id": "back", "label": "Duplicate"})
        state = sanitize_camera_state(reported)
        self.assertEqual(len(state["devices"]), 2)
        self.assertEqual(len(state["devices"][0]["label"]), 96)
        self.assertEqual(set(state["controls"]), {"zoom", "exposureMode", "torch"})
        self.assertNotIn("unexpected", state)
        self.assertNotIn("unexpected", state["settings"])
        self.assertNotIn("iso", state["settings"])
        self.assertNotIn("unused", state["controls"]["zoom"])

    def test_malformed_capabilities_cannot_create_desktop_controls(self):
        reported = reported_camera()
        reported["controls"]["zoom"]["step"] = 0
        reported["controls"]["exposureMode"]["options"] = [{"label": "Invalid"}]
        reported["controls"]["torch"]["value"] = "false"
        reported["width"] = float("inf")
        reported["height"] = 10 ** 1000
        state = sanitize_camera_state(reported)
        self.assertEqual(state["controls"], {})
        self.assertEqual((state["width"], state["height"]), (0, 0))


if __name__ == "__main__":
    unittest.main()
