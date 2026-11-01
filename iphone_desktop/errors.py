"""Translate transport errors into specific next steps, preserving the original log."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Problem:
    code: str
    title: str
    detail: str


def explain_error(message: str) -> Problem:
    text = message.lower()
    if "wirelessdevicenotfound" in text:
        return Problem("wifi_not_found", "iPhone unavailable over Wi-Fi", "Unlock your iPhone and check Wi-Fi. For initial setup, connect USB and click Set up Wi-Fi, then unplug and refresh. The PC and iPhone must share a local network; guest networks or client isolation may prevent connections.")
    if "wirelesstunnelnotfound" in text:
        return Problem("wifi_tunnel", "Wi-Fi tunnel unavailable", "The iPhone responds on the network but its developer tunnel is unavailable. Connect USB, click Set up Wi-Fi, and retry. Keep the phone unlocked and close USB sessions before testing.")
    if "screenservicesunavailable" in text:
        return Problem("screen_services", "Screen and input services unavailable", "Required iPhone services are missing even after preparation. Keep the phone unlocked and retry over USB. If this persists, check the log: the engine may be incompatible with the services offered by the phone.")
    if "rsdrequirederror" in text:
        return Problem("developer_image", "Tunnel preparation required", "This iOS version requires an RSD tunnel to prepare developer services. Restart the updated app and click Prepare services.")
    if any(x in text for x in ("developermodeisnotenabled", "developer mode is not enabled", "developer mode disabled")):
        return Problem("developer_mode", "Enable Developer Mode", "On iPhone: Settings → Privacy & Security → Developer Mode. Enable it, restart, confirm on the phone, then retry.")
    if any(x in text for x in ("passwordrequired", "device is locked", "deviceislocked", "password protected")):
        return Problem("locked", "Unlock your iPhone", "Unlock the phone with its passcode and keep it unlocked while connecting.")
    if any(x in text for x in ("pairingdialogresponsepending", "notpaired", "userdeniedpairing", "trust this computer", "invalidhostid")):
        return Problem("trust", "Trust This Computer", "Unlock your iPhone, accept Trust This Computer, and enter the passcode on the phone. Then refresh.")
    if any(x in text for x in ("nomuxdevicefound", "no device found", "no usb device", "no devices", "devicenotfound")):
        return Problem("no_device", "No iPhone found", "Connect a USB data cable, unlock your iPhone, and refresh. Wi-Fi requires initial setup over USB.")
    if any(x in text for x in ("connectionrefused", "winerror 10061", "connectionfailedtousbmuxd", "apple mobile device service")):
        return Problem("apple_driver", "Check the Apple service", "USB requires Apple Devices or iTunes and the Apple Mobile Device service. Also check that the cable supports data.")
    if any(x in text for x in ("developerdiskimage", "personalizedimagemount", "missingdeveloperdiskimage", "notmountederror", "image is not mounted", "no developer disk image", "ddi is not mounted")):
        return Problem("developer_image", "Prepare developer services", "Click Prepare services. The compatible developer image is downloaded and mounted on first use. The PC needs Internet access.")
    if any(x in text for x in ("invalidservice", "service is not available", "service not found", "unsupported", "not supported")):
        return Problem("unsupported_service", "iOS service unavailable", "Check Developer Mode and service preparation. If the problem persists, this iOS version may not support the protocol; check the log.")
    if any(x in text for x in ("getaddrinfo", "name resolution", "host sconosciuto", "cannot connect to host")):
        return Problem("network", "Connection failed", "Check the PC network. The first developer service download needs Internet access; Wi-Fi requires the phone and PC to communicate on the local network.")
    if any(x in text for x in ("timeout", "timed out")):
        return Problem("timeout", "The iPhone did not respond", "Check the cable, unlock the phone, and retry. If using Wi-Fi, try USB first.")
    return Problem("connection", "Connection interrupted", "Open the log for details. Check that your iPhone is connected, unlocked, and trusted, then retry.")
