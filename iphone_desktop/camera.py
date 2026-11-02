"""Opt-in LAN camera sender, separate from iOS remote screen observation.

Only the sender endpoints are exposed to the LAN. Desktop APIs and video
consumers stay on loopback. Session secrets rotate whenever camera mode stops.
"""
import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import math
from pathlib import Path
import secrets
import socket
import ssl
import time

from aiohttp import WSMsgType, web

MAX_FRAME = 512 * 1024
STALE_SECONDS = 3
MAX_CONTROL_TEXT = 16 * 1024
CONTROL_TIMEOUT = 4
CONTROL_NAMES = frozenset(("zoom", "exposureCompensation", "exposureMode", "exposureTime", "iso",
    "focusMode", "focusDistance", "whiteBalanceMode", "colorTemperature", "torch", "brightness",
    "contrast", "saturation", "sharpness"))
SETTING_NAMES = CONTROL_NAMES | {"deviceId", "facingMode", "width", "height", "frameRate", "aspectRatio", "resizeMode"}


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and abs(value) <= 1e12 and math.isfinite(value)


def bounded_text(value, maximum=96):
    return " ".join(value.split())[:maximum] if isinstance(value, str) else ""


def reject_json_constant(value):
    raise ValueError("Non-finite JSON number")


def sanitize_camera_state(value):
    """Keep only bounded camera metadata needed by desktop controls.

    Browser capability objects vary by engine. Ignore unsupported fields rather
    than trusting arbitrary sender objects or inventing unavailable controls.
    """
    if not isinstance(value, dict):
        raise ValueError("Expected camera settings")
    devices, known_devices = [], set()
    for device in value.get("devices", [])[:16] if isinstance(value.get("devices"), list) else []:
        if not isinstance(device, dict):
            continue
        identifier = device.get("id")
        if not isinstance(identifier, str) or not identifier or len(identifier) > 256 or identifier in known_devices:
            continue
        known_devices.add(identifier)
        devices.append({"id": identifier, "label": bounded_text(device.get("label")) or "Camera"})
    controls = {}
    raw_controls = value.get("controls")
    for name, item in raw_controls.items() if isinstance(raw_controls, dict) else ():
        if name not in CONTROL_NAMES or not isinstance(item, dict):
            continue
        kind = item.get("type")
        control = {"type": kind, "kind": "digital" if item.get("kind") == "digital" else "camera",
                   "label": bounded_text(item.get("label"), 64) or name}
        if kind == "range":
            minimum, maximum, step, current = (item.get(key) for key in ("min", "max", "step", "value"))
            if not all(finite_number(number) for number in (minimum, maximum, step, current)):
                continue
            if minimum >= maximum or step <= 0 or not minimum <= current <= maximum:
                continue
            control.update(min=minimum, max=maximum, step=step, value=current)
        elif kind == "select":
            options = item.get("options")
            if not isinstance(options, list):
                continue
            options = list(dict.fromkeys(option for option in options[:16]
                if isinstance(option, str) and 0 < len(option) <= 64))
            if not options or item.get("value") not in options:
                continue
            control.update(options=options, value=item["value"])
        elif kind == "toggle" and isinstance(item.get("value"), bool):
            control["value"] = item["value"]
        else:
            continue
        unit = bounded_text(item.get("unit"), 16)
        if unit:
            control["unit"] = unit
        controls[name] = control
    settings = {}
    raw_settings = value.get("settings")
    for name, setting in raw_settings.items() if isinstance(raw_settings, dict) else ():
        if name not in SETTING_NAMES:
            continue
        if isinstance(setting, bool) or finite_number(setting):
            settings[name] = setting
        elif isinstance(setting, str) and len(setting) <= 256:
            settings[name] = setting
    selected = value.get("selectedDevice")
    return {"devices": devices, "selectedDevice": selected if isinstance(selected, str) and len(selected) <= 256 else "",
            "facingMode": value.get("facingMode") if value.get("facingMode") in ("user", "environment") else "",
            "width": int(value["width"]) if finite_number(value.get("width")) and 0 <= value["width"] <= 16384 else 0,
            "height": int(value["height"]) if finite_number(value.get("height")) and 0 <= value["height"] <= 16384 else 0,
            "settings": settings, "controls": controls, "busy": value.get("busy") is True}


def local_addresses():
    addresses = set()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = ipaddress.ip_address(item[4][0])
            if address.is_private and not address.is_loopback and not address.is_link_local:
                addresses.add(str(address))
    except OSError:
        pass
    # A UDP connect only selects the default route; it sends no network data.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))
            primary = sock.getsockname()[0]
    except OSError:
        primary = None
    return sorted(addresses, key=lambda address: (address != primary, address))


def camera_certificates(directory, addresses):
    """Keep one local CA so the phone's explicit trust survives app restarts."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    directory.mkdir(parents=True, exist_ok=True)
    ca_key_path, ca_cert_path = directory / "ca-key.pem", directory / "ca.pem"
    now = datetime.now(timezone.utc)
    if ca_key_path.exists() and ca_cert_path.exists():
        ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)
        ca = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())
    else:
        ca_key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "iPhone Desktop Local Camera")])
        ca = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
              .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=3650))
              .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
              .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                  key_encipherment=False, data_encipherment=False, key_agreement=False,
                  key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
              .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
              .sign(ca_key, hashes.SHA256()))
        ca_key_path.write_bytes(ca_key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        ca_cert_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "iPhone Desktop Camera")])
    leaf = (x509.CertificateBuilder().subject_name(subject).issuer_name(ca.subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=90))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(a))
                for a in ["127.0.0.1", *addresses]]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    cert_path, key_path = directory / "server.pem", directory / "server-key.pem"
    cert_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM) + ca.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cert_path, key_path, ca.public_bytes(serialization.Encoding.DER), ca.fingerprint(hashes.SHA256()).hex()


class CameraRelay:
    def __init__(self, runtime, assets):
        self.runtime, self.assets = Path(runtime), Path(assets)
        self.active = False
        self.sender_token = self.view_token = None
        self.addresses = []
        self.sender_urls = []
        self.runners = []
        self.sender = None
        self.viewers = set()
        self.frame = None
        self.frame_time = 0
        self.sequence = 0
        self.changed = asyncio.Condition()
        self.fingerprint = None
        self.camera_state = None
        self.pending_control = None

    async def start(self):
        if self.active:
            return
        self.addresses = await asyncio.to_thread(local_addresses)
        if not self.addresses:
            raise RuntimeError("CameraNetworkUnavailable: connect the PC and iPhone to the same local network.")
        cert, key, self.ca_der, self.fingerprint = await asyncio.to_thread(
            camera_certificates, self.runtime / "camera-tls", self.addresses)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(cert, key)
        self.sender_token, self.view_token = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        try:
            secure = web.AppRunner(self.sender_app(secure=True), access_log=None)
            self.runners.append(secure)
            await secure.setup()
            secure_site = web.TCPSite(secure, "0.0.0.0", 0, ssl_context=tls)
            await secure_site.start()
            self.https_port = secure_site._server.sockets[0].getsockname()[1]
            bootstrap = web.AppRunner(self.sender_app(secure=False), access_log=None)
            self.runners.append(bootstrap)
            await bootstrap.setup()
            bootstrap_site = web.TCPSite(bootstrap, "0.0.0.0", 0)
            await bootstrap_site.start()
            self.http_port = bootstrap_site._server.sockets[0].getsockname()[1]
            self.sender_urls = [f"http://{address}:{self.http_port}/{self.sender_token}/" for address in self.addresses]
            self.active = True
        except BaseException:
            await self.stop()
            raise

    async def stop(self):
        self.active = False
        self.sender_token = self.view_token = None
        self.reset_controls()
        sockets = [socket_ for socket_ in (self.sender, *self.viewers)
                   if socket_ is not None and socket_.prepared]
        if sockets:
            await asyncio.gather(*(socket_.close() for socket_ in sockets), return_exceptions=True)
        for runner in reversed(self.runners):
            await runner.cleanup()
        self.runners.clear()
        self.sender_urls = []
        await self.clear_frame()

    async def clear_frame(self):
        async with self.changed:
            self.frame = None
            self.frame_time = 0
            self.sequence += 1
            self.changed.notify_all()

    def snapshot(self):
        camera_state = self.camera_state
        if camera_state is not None and self.pending_control is not None:
            camera_state = {**camera_state, "busy": True}
        return {"active": self.active,
                "connected": self.active and self.sender is not None and time.monotonic() - self.frame_time < STALE_SECONDS,
                "sender_urls": self.sender_urls,
                "source_path": f"/camera/{self.view_token}/view" if self.active else None,
                "certificate_sha256": self.fingerprint if self.active else None,
                "camera_state": camera_state if self.active else None}

    def reset_controls(self):
        self.camera_state = None
        pending, self.pending_control = self.pending_control, None
        if pending is not None and not pending["future"].done():
            pending["future"].set_result({"ok": False, "error": "The iPhone camera disconnected. Start it again in Safari."})

    def validate_control(self, command):
        if not isinstance(command, dict) or set(command) - {"name", "value"}:
            raise ValueError("Choose a supported camera control.")
        name, value = command.get("name"), command.get("value")
        if not isinstance(name, str):
            raise ValueError("Choose a supported camera control.")
        if name == "reset":
            if value is not None:
                raise ValueError("Reset does not accept a value.")
            return {"name": name}
        if name == "deviceId":
            if not isinstance(value, str) or value not in {device["id"] for device in self.camera_state["devices"]}:
                raise ValueError("Choose a camera reported by your iPhone.")
        elif name == "facingMode":
            if value not in ("user", "environment"):
                raise ValueError("Choose the front or back camera.")
        else:
            control = self.camera_state["controls"].get(name)
            if name not in CONTROL_NAMES or control is None:
                raise ValueError("This control is unavailable for the current iPhone camera.")
            if control["type"] == "range":
                if not finite_number(value) or not control["min"] <= value <= control["max"]:
                    raise ValueError("Choose a value inside the camera's supported range.")
            elif control["type"] == "select":
                if not isinstance(value, str) or value not in control["options"]:
                    raise ValueError("Choose a mode supported by the camera.")
            elif control["type"] == "toggle" and not isinstance(value, bool):
                raise ValueError("Choose on or off for this control.")
        return {"name": name, "value": value}

    async def control(self, command):
        sender = self.sender
        if not self.active or sender is None or sender.closed or self.camera_state is None:
            raise RuntimeError("Camera controls are unavailable. Start the camera in Safari first.")
        command = self.validate_control(command)
        if self.pending_control is not None or self.camera_state["busy"]:
            raise RuntimeError("The iPhone is adjusting its camera. Wait a moment and try again.")
        identifier = secrets.token_hex(12)
        future = asyncio.get_running_loop().create_future()
        pending = {"id": identifier, "future": future}
        self.pending_control = pending
        try:
            await asyncio.wait_for(sender.send_json({"type": "camera-command", "id": identifier, "command": command}), 2)
            timeout = 10 if command["name"] in ("deviceId", "facingMode", "reset") else CONTROL_TIMEOUT
            try:
                result = await asyncio.wait_for(future, timeout)
            except asyncio.TimeoutError:
                raise TimeoutError("The iPhone did not confirm the camera adjustment. Check Safari before trying again.") from None
            if not result["ok"]:
                raise RuntimeError(result.get("error") or "The iPhone could not apply this camera setting.")
            if self.sender is not sender or self.camera_state is None:
                raise RuntimeError("The iPhone camera disconnected before confirming its settings.")
            return self.camera_state
        except (ConnectionError, ConnectionResetError) as exc:
            raise RuntimeError("The camera connection ended. Start it again in Safari.") from exc
        finally:
            if self.pending_control is pending:
                self.pending_control = None
            if not future.done():
                future.cancel()

    async def receive_control(self, socket_, text):
        if len(text.encode("utf-8")) > MAX_CONTROL_TEXT:
            await socket_.close(code=1009, message=b"Camera settings message too large")
            return
        try:
            message = json.loads(text, parse_constant=reject_json_constant)
            if not isinstance(message, dict):
                raise ValueError("Expected camera message")
            message_type = message.get("type")
            if message_type == "camera-state":
                self.camera_state = sanitize_camera_state(message.get("state"))
            elif message_type == "camera-result":
                pending = self.pending_control
                if pending is None or message.get("id") != pending["id"] or pending["future"].done():
                    return
                if not isinstance(message.get("ok"), bool):
                    raise ValueError("Expected camera adjustment result")
                if isinstance(message.get("state"), dict):
                    self.camera_state = sanitize_camera_state(message["state"])
                elif message["ok"]:
                    raise ValueError("Expected updated camera settings")
                pending["future"].set_result({"ok": message["ok"], "error": bounded_text(message.get("error"), 240)})
        except (ValueError, TypeError, OverflowError):
            await socket_.close(code=1003, message=b"Invalid camera settings")

    def sender_app(self, secure):
        @web.middleware
        async def authorize(request, handler):
            if not self.sender_token or not secrets.compare_digest(request.match_info.get("token", ""), self.sender_token):
                raise web.HTTPForbidden()
            # Pin requests to the addresses shown at pairing; prevent DNS rebinding.
            if request.url.host not in ["127.0.0.1", *self.addresses]:
                raise web.HTTPForbidden()
            origin = request.headers.get("Origin")
            if origin and origin != f"{request.scheme}://{request.host}":
                raise web.HTTPForbidden()
            response = await handler(request)
            if not isinstance(response, web.WebSocketResponse):
                response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                    "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY"})
            return response

        app = web.Application(middlewares=[authorize], client_max_size=MAX_FRAME)

        async def bootstrap(request):
            address = request.url.host
            prefix = f"/{self.sender_token}"
            target = f"https://{address}:{self.https_port}{prefix}/send"
            return web.Response(text=f'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>iPhone Desktop · Camera setup</title>
<style>body{{color:#edf3f4;background:#10191f;font:16px/1.6 -apple-system,sans-serif;max-width:620px;margin:40px auto;padding:24px}}a{{color:#96ebc9}}li{{margin:16px 0}}.go{{display:inline-block;padding:12px 20px;background:#96ebc9;color:#12392c;border-radius:12px;text-decoration:none}}small{{color:#9db1bb;word-break:break-all}}h1{{line-height:1.15}}</style>
<p>IPHONE DESKTOP / LOCAL CAMERA</p><h1>Your camera,<br>on your PC.</h1>
<p>First setup: Safari needs a trusted HTTPS connection to use your camera. The certificate below belongs to this PC.</p>
<ol><li><a href="{prefix}/camera.cer">Download the local camera certificate</a> and allow the download.</li>
<li>Open <b>Settings → General → VPN &amp; Device Management</b> (or <b>Profile Downloaded</b>). Install <b>iPhone Desktop Local Camera</b>.</li>
<li>Open <b>Settings → General → About → Certificate Trust Settings</b>. Turn on full trust for <b>iPhone Desktop Local Camera</b>.</li>
<li>Return to Safari, tap below, then allow camera access. After first setup you can go straight to the camera.</li></ol>
<p><a class="go" href="{target}">Open camera →</a></p>
<p>Keep Safari open and the iPhone unlocked. Camera mode uses your local network and stops screen mirroring. No microphone audio is sent.</p>
<small>Certificate SHA-256 (compare with the PC): {self.fingerprint}</small>
<p><small>You can remove the certificate in VPN &amp; Device Management when you no longer use this feature.</small></p></html>''', content_type="text/html")

        async def certificate(request):
            return web.Response(body=self.ca_der, content_type="application/x-x509-ca-cert",
                headers={"Content-Disposition": 'attachment; filename="iPhone Desktop Local Camera.cer"'})

        async def asset(request):
            names = {"send": "camera-phone.html", "phone.js": "camera-phone.js", "settings.js": "camera-settings.js", "camera.css": "camera.css"}
            return web.FileResponse(self.assets / names[request.match_info["asset"]])

        app.router.add_get("/{token}/", bootstrap)
        app.router.add_get("/{token}/camera.cer", certificate)
        if secure:
            app.router.add_get("/{token}/{asset:send|phone.js|settings.js|camera.css}", asset)
            app.router.add_get("/{token}/upload", self.upload)
        return app

    async def upload(self, request):
        if self.sender is not None:
            raise web.HTTPConflict(text="A camera is already connected. Stop it before connecting another.")
        socket_ = web.WebSocketResponse(max_msg_size=MAX_FRAME, heartbeat=10, receive_timeout=15, timeout=1)
        # Reserve before awaiting the upgrade so two senders cannot race.
        self.sender = socket_
        self.reset_controls()
        try:
            await socket_.prepare(request)
            async for message in socket_:
                if message.type == WSMsgType.BINARY:
                    frame = message.data
                    if not frame.startswith(b"\xff\xd8") or not frame.endswith(b"\xff\xd9"):
                        await socket_.close(code=1003, message=b"Expected a JPEG frame")
                        break
                    # Bounded latest-frame storage; slow viewers never queue frames.
                    delay = 1 / 30 - (time.monotonic() - self.frame_time)
                    if delay > 0:
                        await asyncio.sleep(delay)
                    async with self.changed:
                        self.frame, self.frame_time = frame, time.monotonic()
                        self.sequence += 1
                        self.changed.notify_all()
                    await socket_.send_str("ok")
                elif message.type == WSMsgType.TEXT:
                    await self.receive_control(socket_, message.data)
                elif message.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        finally:
            if self.sender is socket_:
                self.sender = None
                self.reset_controls()
                await self.clear_frame()
        return socket_

    def install_view_routes(self, app):
        async def view(request):
            if not self.active or not self.view_token or not secrets.compare_digest(request.match_info["token"], self.view_token):
                raise web.HTTPForbidden()
            name = request.match_info["asset"]
            if name == "frames":
                return await self.frames(request)
            files = {"view": "camera-view.html", "view.js": "camera-view.js"}
            return web.FileResponse(self.assets / files[name])
        app.router.add_get("/camera/{token}/{asset:view|view.js|frames}", view)

    async def frames(self, request):
        if len(self.viewers) >= 8:
            raise web.HTTPTooManyRequests(text="Too many camera viewers. Close an unused preview and try again.")
        viewer = web.WebSocketResponse(heartbeat=10, timeout=1)
        # Reserve a slot before the upgrade yields, including pending handshakes.
        self.viewers.add(viewer)
        tasks = []
        try:
            await viewer.prepare(request)

            async def send_frames():
                last = -1
                while self.active and not viewer.closed:
                    async with self.changed:
                        if last == self.sequence:
                            with contextlib.suppress(asyncio.TimeoutError):
                                await asyncio.wait_for(self.changed.wait(), 1)
                        fresh = self.frame is not None and time.monotonic() - self.frame_time < STALE_SECONDS
                        frame, last = self.frame if fresh else None, self.sequence
                    if not self.active or viewer.closed:
                        break
                    if frame is not None:
                        await asyncio.wait_for(viewer.send_bytes(frame), 3)
                    else:
                        await asyncio.wait_for(viewer.send_str("waiting"), 3)

            async def read_messages():
                async for _ in viewer:
                    pass

            tasks = [asyncio.create_task(send_frames()), asyncio.create_task(read_messages())]
            # A failed sender must end the request reader too. Otherwise a slow
            # consumer can leave its socket and viewer slot alive indefinitely.
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (ConnectionError, asyncio.TimeoutError):
            pass
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            try:
                if viewer.prepared:
                    with contextlib.suppress(ConnectionError, asyncio.TimeoutError):
                        await asyncio.wait_for(viewer.close(), 1.5)
            finally:
                self.viewers.discard(viewer)
        return viewer
