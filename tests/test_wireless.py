import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from iphone_desktop import wireless
from iphone_desktop.bridge import discover, session_tunnel


class WirelessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.saved_patch = patch.object(wireless, "saved_wifi_devices", return_value={})
        self.saved_patch.start()
        self.addCleanup(self.saved_patch.stop)

    async def test_discovery_finds_lan_phone_when_usbmux_lists_nothing(self):
        expected = {"id": "phone", "transport": "wifi", "paired": True}
        with patch("pymobiledevice3.usbmux.list_devices", AsyncMock(return_value=[])), patch.object(wireless, "discover_wifi", AsyncMock(return_value=[expected])):
            self.assertEqual(await discover(), [expected])

    async def test_discovery_does_not_duplicate_apple_network_device(self):
        device = SimpleNamespace(serial="phone", is_usb=False, connection_type="Network")
        phone = AsyncMock()
        phone.__aenter__.return_value = phone
        phone.all_values = {"DeviceName": "iPhone"}
        phone.product_version, phone.product_type, phone.paired = "27.2", "iPhone18,1", True
        with patch("pymobiledevice3.usbmux.list_devices", AsyncMock(return_value=[device])), patch("pymobiledevice3.lockdown.create_using_usbmux", AsyncMock(return_value=phone)), patch.object(wireless, "discover_wifi", AsyncMock(return_value=[{"id": "phone", "transport": "wifi"}])):
            result = await discover()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "phone")

    async def test_known_ip_uses_unicast_and_authenticated_remote_pairing(self):
        provider = object()
        with patch.object(wireless, "saved_wifi_devices", return_value={"phone": {"host": "192.168.0.11"}}), patch.object(wireless, "remote_pairing_port", AsyncMock(return_value=49152)), patch.object(wireless.tunnel_service, "create_core_device_tunnel_service_using_remotepairing", AsyncMock(return_value=provider)) as create:
            self.assertIs(await wireless.wifi_tunnel_provider("phone"), provider)
            create.assert_awaited_once_with("phone", "192.168.0.11", 49152, autopair=False)

    async def test_missing_wifi_device_reports_specific_error(self):
        with patch.object(wireless.tunnel_service, "get_remote_pairing_tunnel_services", AsyncMock(return_value=[])):
            with self.assertRaisesRegex(RuntimeError, "WirelessTunnelNotFound"):
                await wireless.wifi_tunnel_provider("phone")

    async def check_tunnel_lifecycle(self, fail=False, wrong_identity=False):
        client = SimpleNamespace(tun=SimpleNamespace(set_peer=Mock()), wait_closed=asyncio.Event().wait)
        context = AsyncMock()
        context.__aenter__.return_value = SimpleNamespace(client=client, address="fd00::1", port=1234, auxiliary_metadata={})
        provider = SimpleNamespace(close=AsyncMock(), start_tcp_tunnel=Mock(return_value=context))
        dial_context = AsyncMock()
        rsd = SimpleNamespace(udid="other" if wrong_identity else "phone", connect=AsyncMock(side_effect=OSError("lost link") if fail else None), close=AsyncMock())
        with patch.object(wireless, "wifi_tunnel_provider", AsyncMock(return_value=provider)) as create, patch.object(wireless.userspace_tunnel, "UserspaceDialPlane", Mock(return_value=dial_context)), patch.object(wireless, "RemoteServiceDiscoveryService", Mock(return_value=rsd)):
            tunnel = session_tunnel("phone", "wifi")
            self.assertIsInstance(tunnel, wireless.WifiRsdTunnel)
            if fail:
                with self.assertRaises(OSError):
                    await tunnel.aopen()
            elif wrong_identity:
                with self.assertRaisesRegex(RuntimeError, "WirelessIdentityMismatch"):
                    await tunnel.aopen()
            else:
                async with tunnel as connected:
                    self.assertIs(connected, rsd)
                    self.assertTrue(wireless.userspace_tunnel.USERSPACE_ACTIVE)
                    self.assertIs(wireless.userspace_tunnel._active_tunnel, tunnel)
                    create.assert_awaited_once_with("phone")
            self.assertIsNone(wireless.userspace_tunnel._active_tunnel)
            self.assertFalse(wireless.userspace_tunnel.USERSPACE_ACTIVE)
            self.assertFalse(wireless.tunnel_service.USE_USERSPACE_TUNNEL)
        rsd.close.assert_awaited_once()
        provider.close.assert_awaited_once()
        context.__aexit__.assert_awaited_once()

    async def test_lan_tunnel_registers_udp_stack_and_releases_resources(self):
        await self.check_tunnel_lifecycle()

    async def test_failed_lan_handshake_releases_resources_and_resets_stack(self):
        await self.check_tunnel_lifecycle(fail=True)

    async def test_wrong_phone_identity_closes_tunnel(self):
        await self.check_tunnel_lifecycle(wrong_identity=True)

    async def test_unreachable_saved_phone_does_not_hide_other_bonjour_phones(self):
        rsd = SimpleNamespace(udid="phone", all_values={"DeviceName": "iPhone"}, product_version="27.2", product_type="iPhone18,1")
        provider = SimpleNamespace(remote_identifier="phone", hostname="192.168.0.11", close=AsyncMock())
        tunnel = AsyncMock()
        tunnel.__aenter__.return_value = rsd
        with patch.object(wireless.tunnel_service, "get_remote_pairing_tunnel_services", AsyncMock(return_value=[provider])), patch.object(wireless, "WifiRsdTunnel", Mock(return_value=tunnel)):
            devices = await wireless.discover_wifi()
        self.assertEqual(devices[0]["id"], "phone")
        self.assertTrue(devices[0]["paired"])
        provider.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
