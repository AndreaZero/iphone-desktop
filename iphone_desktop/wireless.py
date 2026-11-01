"""Bonjour discovery and a LAN-only userspace tunnel for the pinned backend.

pymobiledevice3 11.49.0's UserspaceRsdTunnel starts by asking usbmux for a
device. On Windows, Apple can omit network devices from that list even when
their paired lockdown endpoint is reachable through Bonjour.
"""
import asyncio
import ipaddress
import json
import logging
import socket
from contextlib import AsyncExitStack

from iphone_desktop.app import RUNTIME
from pymobiledevice3.bonjour import build_query, parse_mdns_message, QTYPE_PTR, QTYPE_SRV
from pymobiledevice3.remote import tunnel_service, userspace_tunnel
from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService

WIFI_CONFIG = RUNTIME / "wifi-devices.json"


def validate_wifi_host(host):
    try:
        address = ipaddress.ip_address(host)
    except (ValueError, TypeError):
        raise ValueError("Enter a valid local IPv4 address for your iPhone.") from None
    if address.version != 4 or not address.is_private or address.is_loopback or address.is_unspecified or address.is_multicast:
        raise ValueError("Enter the iPhone IPv4 address on your local network.")
    return str(address)


def saved_wifi_devices():
    try:
        entries = json.loads(WIFI_CONFIG.read_text(encoding="utf-8"))
        return {device: entry for device, entry in entries.items() if isinstance(entry, dict) and validate_wifi_host(entry.get("host"))}
    except (OSError, ValueError, AttributeError):
        return {}


def remember_wifi(device, host):
    entries = saved_wifi_devices()
    entries[device] = {"host": validate_wifi_host(host)}
    WIFI_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    temporary = WIFI_CONFIG.with_suffix(".tmp")
    temporary.write_text(json.dumps(entries), encoding="utf-8")
    temporary.replace(WIFI_CONFIG)


def wifi_metadata(phone, host=None):
    return {"id": phone.udid, "transport": "wifi", "name": phone.all_values.get("DeviceName", "iPhone"), "ios": phone.product_version, "model": phone.product_type, "paired": True, "wifi_host": host}


async def discover_wifi():
    result = []
    # The backend stack is a singleton: probe tunnels sequentially.
    for device, entry in saved_wifi_devices().items():
        try:
            async with asyncio.timeout(8):
                async with WifiRsdTunnel(serial=device) as rsd:
                    result.append(wifi_metadata(rsd, entry["host"]))
        except Exception as exc:
            logging.debug("Saved Wi-Fi tunnel unavailable: %s", type(exc).__name__)
    seen = {item["id"] for item in result}
    providers = await tunnel_service.get_remote_pairing_tunnel_services(bonjour_timeout=4, excluded_identifiers=seen)
    try:
        for provider in providers:
            if provider.remote_identifier in seen:
                continue
            try:
                async with asyncio.timeout(8):
                    async with WifiRsdTunnel(serial=provider.remote_identifier, provider=provider) as rsd:
                        result.append(wifi_metadata(rsd, provider.hostname))
                        seen.add(rsd.udid)
            except Exception as exc:
                logging.debug("Bonjour tunnel unavailable: %s", type(exc).__name__)
    finally:
        for provider in providers:
            await provider.close()
    return result


async def remote_pairing_port(host, timeout=3):
    """Ask the known phone directly, so multicast routing is not required."""
    service = "_remotepairing._tcp.local."
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", 0))
        sock.setblocking(False)
        await loop.sock_sendto(sock, build_query(service, QTYPE_PTR, unicast=True), (host, 5353))
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            data, sender = await asyncio.wait_for(loop.sock_recvfrom(sock, 65535), deadline - loop.time())
            if sender[0] != host:
                continue
            for record in parse_mdns_message(data):
                if record.get("type") == QTYPE_SRV and record.get("name", "").endswith("." + service):
                    port = record.get("port", 0)
                    if 0 < port < 65536:
                        return port
    except asyncio.TimeoutError:
        pass
    finally:
        sock.close()
    return None


async def wifi_tunnel_provider(device):
    # iOS 27 can accept lockdown over Wi-Fi but close CoreDeviceProxy's
    # tunnel handshake. Use the authenticated RemotePairing transport.
    entry = saved_wifi_devices().get(device)
    if entry:
        host = entry["host"]
        port = await remote_pairing_port(host)
        if port:
            return await tunnel_service.create_core_device_tunnel_service_using_remotepairing(device, host, port, autopair=False)
    providers = await tunnel_service.get_remote_pairing_tunnel_services(udid=device, bonjour_timeout=4)
    if providers:
        for extra in providers[1:]:
            await extra.close()
        return providers[0]
    raise RuntimeError("WirelessTunnelNotFound: the iPhone responds but does not publish a Wi-Fi tunnel service. Run Set up Wi-Fi again with USB connected.")


class WifiRsdTunnel(userspace_tunnel.UserspaceRsdTunnel):
    """Use the inherited lifecycle, with a Bonjour/TCP provider instead of USB.

    This adapter mirrors the setup in the pinned 11.49.0 backend. Its stack
    globals are also used by the upstream video receiver to bind RTP sockets.
    Each app worker owns one tunnel and no system network adapter is created.
    """

    def __init__(self, serial, provider=None):
        super().__init__(serial=serial, autopair=False)
        self._supplied_provider = provider

    async def _aopen_locked(self):
        if userspace_tunnel._active_tunnel is not None:
            raise RuntimeError("A userspace tunnel is already active in this process")
        stack = AsyncExitStack()
        tunnel_service.USE_USERSPACE_TUNNEL = True
        try:
            provider = self._supplied_provider or await wifi_tunnel_provider(self.serial)
            self._supplied_provider = None
            stack.push_async_callback(provider.close)
            tunnel = await stack.enter_async_context(provider.start_tcp_tunnel())
            self.tun = tunnel.client.tun
            self.tun.set_peer(tunnel.address)
            dial = await stack.enter_async_context(userspace_tunnel.UserspaceDialPlane(self.tun, tunnel.address))
            rsd = RemoteServiceDiscoveryService((tunnel.address, tunnel.port), open_connection=dial.dial, auxiliary_metadata=tunnel.auxiliary_metadata)
            stack.push_async_callback(rsd.close)
            await rsd.connect()
            if rsd.udid != self.serial:
                raise RuntimeError("WirelessIdentityMismatch: the tunnel does not belong to the selected iPhone.")
        except BaseException:
            try:
                await stack.aclose()
            finally:
                tunnel_service.USE_USERSPACE_TUNNEL = False
                self.tun = None
            raise
        self._exit_stack = stack
        self.rsd = rsd
        userspace_tunnel._active_tunnel = self
        userspace_tunnel.USERSPACE_ACTIVE = True
        self._transport_watcher = asyncio.create_task(self._watch_transport_closed(tunnel.client), name="wifi-tunnel-transport-watcher")
        return rsd
