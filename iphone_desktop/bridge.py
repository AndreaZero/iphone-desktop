"""Isolated device worker. No Bluetooth, WDA, signed phone app or cloud relay."""
import argparse
import asyncio
import contextlib
import json
import logging
import sys
from pathlib import Path


def session_tunnel(device, transport="usb"):
    if transport == "wifi":
        from iphone_desktop.wireless import WifiRsdTunnel
        return WifiRsdTunnel(serial=device)
    from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
    return UserspaceRsdTunnel(serial=device)


async def prepare_services(phone, transport="usb"):
    from packaging.version import Version
    from pymobiledevice3.exceptions import AlreadyMountedError
    from pymobiledevice3.services.mobile_image_mounter import CRYPTEX_IMAGE_MIN_VERSION, auto_mount

    async def mount(provider):
        try:
            await auto_mount(provider)
        except AlreadyMountedError:
            logging.info("The developer image is already mounted.")

    # iOS 27 installs the DDI as a cryptex over RSD. The CLI retries on
    # RSDRequiredError, but library calls do not create that tunnel for us.
    if Version(phone.product_version) >= CRYPTEX_IMAGE_MIN_VERSION:
        async with session_tunnel(phone.udid, transport) as rsd:
            await mount(rsd)
    else:
        await mount(phone)


def require_stream_services(rsd):
    required = ("com.apple.coredevice.displayservice", "com.apple.coredevice.hid.universalhidservice")
    services = (rsd.peer_info or {}).get("Services", {})
    missing = [name for name in required if name not in services]
    if missing:
        raise RuntimeError("ScreenServicesUnavailable: " + ", ".join(missing))


async def discover(transport=None) -> list[dict]:
    from pymobiledevice3.usbmux import list_devices
    from pymobiledevice3.lockdown import create_using_usbmux

    devices = await asyncio.wait_for(list_devices(), 10)
    result = []
    for device in devices:
        if transport and ("usb" if device.is_usb else "wifi") != transport:
            continue
        item = {"id": device.serial, "transport": "usb" if device.is_usb else "wifi", "name": "iPhone", "paired": False}
        try:
            async with asyncio.timeout(6):
                async with await create_using_usbmux(serial=device.serial, connection_type=device.connection_type, autopair=False) as phone:
                    item.update(name=phone.all_values.get("DeviceName", "iPhone"), ios=phone.product_version, model=phone.product_type, paired=phone.paired)
        except Exception as exc:
            item["warning"] = f"{type(exc).__name__}: {exc}"
        result.append(item)
    from iphone_desktop.wireless import discover_wifi, saved_wifi_devices
    saved = saved_wifi_devices()
    for item in result:
        item["wifi_host"] = saved.get(item["id"], {}).get("host")
    if transport == "usb":
        return result
    try:
        async with asyncio.timeout(15):
            wireless = await discover_wifi()
        known = {(item["id"], item["transport"]) for item in result}
        result.extend(item for item in wireless if (item["id"], item["transport"]) not in known)
    except Exception as exc:
        logging.warning("Bonjour Wi-Fi discovery failed: %s: %s", type(exc).__name__, exc)
    return result


async def serve_stream(server, result_path, rsd=None):
    """Cancel cooperatively so iOS releases remote observation and its camera.

    TerminateProcess on Windows skips the upstream server's cleanup. A local
    sentinel lets the controller request cancellation inside the worker loop.
    """
    stop_file = Path(result_path).with_suffix(".stop")
    serving = asyncio.create_task(server.serve())
    try:
        while not serving.done():
            if stop_file.exists():
                serving.cancel()
                break
            await asyncio.sleep(0.2)
        with contextlib.suppress(asyncio.CancelledError):
            await serving
    finally:
        try:
            if not serving.done():
                serving.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await serving
        finally:
            try:
                if rsd is not None:
                    # Upstream starts audio/video before entering its server
                    # cleanup block. Cancelling that preflight can leave iOS
                    # remotely observed even though serve() has already ended.
                    try:
                        from pymobiledevice3.remote.core_device.display_service import DisplayService
                        await asyncio.wait_for(DisplayService.stop_all_streams(rsd), timeout=6)
                    except Exception:
                        logging.exception("Could not release the device media session")
            finally:
                try:
                    stop_file.unlink(missing_ok=True)
                except OSError:
                    logging.exception("Could not remove the stream stop request")


async def execute(args) -> dict:
    if args.action == "selftest":
        from importlib.metadata import version
        from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
        from pymobiledevice3.remote.core_device.screen_stream import ScreenStreamServer, VIEWER_HTML
        from pymobiledevice3.remote.core_device.display_service import VIDEO_STREAM_MIN_VERSION
        from pymobiledevice3.services.mobile_image_mounter import auto_mount
        from iphone_desktop.viewer import prepare_viewer
        prepare_viewer()
        return {"backend": version("pymobiledevice3"), "minimum_ios": str(VIDEO_STREAM_MIN_VERSION), "viewer_bytes": len(VIEWER_HTML), "runtime": "ready"}
    if args.action == "discover":
        return {"devices": await discover(args.transport)}

    if args.transport == "wifi":
        from iphone_desktop.wireless import wifi_metadata
        from pymobiledevice3.exceptions import AlreadyMountedError
        from pymobiledevice3.services.mobile_image_mounter import auto_mount
        async with session_tunnel(args.device, "wifi") as rsd:
            metadata = wifi_metadata(rsd)
            metadata["developer_mode"] = await rsd.get_developer_mode_status()
            if args.action == "info":
                return metadata
            if not metadata["developer_mode"]:
                raise RuntimeError("Developer mode is not enabled")
            if args.action == "prepare":
                try:
                    await auto_mount(rsd)
                except AlreadyMountedError:
                    logging.info("The developer image is already mounted.")
                return {**metadata, "message": "Developer services ready over Wi-Fi."}
            if args.action == "stream":
                from pymobiledevice3.remote.core_device.screen_stream import ScreenStreamServer
                from iphone_desktop.viewer import prepare_viewer
                prepare_viewer()
                require_stream_services(rsd)
                await serve_stream(ScreenStreamServer(rsd, bind="127.0.0.1", http_port=args.port, audio_default_on=False), args.result, rsd)
                return metadata
            raise ValueError("This operation requires a USB cable.")

    from pymobiledevice3.lockdown import create_using_usbmux

    phone = await create_using_usbmux(serial=args.device, connection_type="USB", autopair=True, pair_timeout=25)
    async with phone:
        metadata = {"id": phone.udid, "name": phone.all_values.get("DeviceName", "iPhone"), "ios": phone.product_version, "model": phone.product_type, "paired": phone.paired}
        if args.action == "reveal":
            from pymobiledevice3.services.amfi import AmfiService
            await AmfiService(phone).reveal_developer_mode_option_in_ui()
            return {**metadata, "message": "Find Developer Mode in Settings → Privacy & Security. Enable it and confirm the restart on your iPhone."}
        if args.action == "wifi":
            await phone.set_enable_wifi_connections(True)
            if not await phone.get_enable_wifi_connections():
                raise RuntimeError("WirelessSetupFailed: the phone did not enable network connections.")
            if args.wifi_host:
                from iphone_desktop.wireless import remember_wifi
                remember_wifi(phone.udid, args.wifi_host)
            from pymobiledevice3.remote.tunnel_service import RemotePairingLockdownService
            from pymobiledevice3.exceptions import RemotePairingCompletedError
            service = await RemotePairingLockdownService.create(phone)
            try:
                try:
                    await service.connect(autopair=True)
                except RemotePairingCompletedError:
                    logging.info("Wi-Fi developer pairing created through trusted USB.")
            finally:
                await service.close()
            return {**metadata, "message": "Wi-Fi enabled. Unplug USB, keep the iPhone unlocked, select Wi-Fi, and refresh. The PC and iPhone must be able to communicate on the same local network."}
        developer_mode = await phone.get_developer_mode_status()
        metadata["developer_mode"] = developer_mode
        if args.action == "info":
            return metadata
        if not developer_mode:
            raise RuntimeError("Developer mode is not enabled")
        if args.action == "prepare":
            await prepare_services(phone, args.transport)
            return {**metadata, "message": "Developer services prepared. Click Connect iPhone."}

    # Keep the userspace network stack isolated in this process: stopping the
    # session cannot leave a system-wide tunnel or change the PC's adapters.
    if args.action == "stream":
        from pymobiledevice3.remote.core_device.screen_stream import ScreenStreamServer
        from iphone_desktop.viewer import prepare_viewer
        prepare_viewer()
        async with session_tunnel(args.device, args.transport) as rsd:
            require_stream_services(rsd)
            server = ScreenStreamServer(rsd, bind="127.0.0.1", http_port=args.port, audio_default_on=False)
            await serve_stream(server, args.result, rsd)
    return metadata


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("selftest", "discover", "info", "prepare", "reveal", "wifi", "stream"))
    parser.add_argument("--device")
    parser.add_argument("--transport", choices=("usb", "wifi"), default="usb")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--wifi-host")
    parser.add_argument("--result", required=True)
    parser.add_argument("--log", required=True)
    args = parser.parse_args(argv)
    # Windowed PyInstaller executables set the standard streams to None.
    # A real file works in source and frozen workers, including exceptions.
    log_file = open(args.log, "a", encoding="utf-8", buffering=1)
    sys.stdout = log_file
    sys.stderr = log_file
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=log_file, force=True)
    try:
        value = asyncio.run(execute(args))
        result = {"ok": True, **value}
    except BaseException as exc:
        logging.exception("Device operation failed")
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    Path(args.result).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    log_file.flush()
    if not result["ok"]:
        raise SystemExit(1)
