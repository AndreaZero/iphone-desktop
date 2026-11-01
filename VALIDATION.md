# Validation

Development checks recorded through November 1, 2026. Hardware results describe tested configurations, not a guarantee for every device, network, or future iOS release. Personal device names, identifiers, and network addresses are omitted.

## Automated coverage

- API token, Host, and Origin checks; rejection of undiscovered devices.
- Cancellation, worker timeout cleanup, and connection failures.
- RSD developer image preparation and already-mounted images.
- Missing display/HID services, HTTP 500 despite working viewer HTML, delayed video startup, and failure cleanup.
- Wi-Fi discovery without usbmux network devices, deduplication, saved-IP RemotePairing, identity checks, and tunnel cleanup.

Run `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` for the current suite.

## USB and packaged application

- An iPhone 17 Pro on iOS 27.2 was detected over trusted USB.
- Revealing Developer Mode succeeded; enabling it and confirming restart remained manual phone actions.
- Cryptex1 preparation over RSD succeeded, including already-mounted images.
- The codec endpoint returned HTTP 200 with `hev1.1.6.L150.B0` and decoder configuration; the stream endpoint returned video bytes.
- The developer confirmed the app worked after the preparation fix. Reconnection and individual input actions were not recorded as separate controlled tests.
- The portable executable passed its backend self-test without external Python after Wintun and dependency metadata were included.
- Packaged startup, HTTP responses, USB discovery, icon resources, favicon, and orderly shutdown were checked.
- Automated visual inspection could not complete because the UI tool could not verify the Chrome application window's URL.

## Wi-Fi

- Developer RemotePairing was created over trusted USB.
- A cable-free session succeeded with zero USB devices present: connected state, codec HTTP 200, and received video bytes. The PC used Ethernet and the phone used Wi-Fi on the same subnet.
- Direct Bonjour queries found the dynamic service port when multicast and usbmux network discovery were unavailable.
- The packaged 0.2.0 app connected over Wi-Fi and received video. An initial cold scan was empty; a refresh succeeded.
- Wireless visual output and input were not independently confirmed in the recorded validation.

## Additional compatibility

- An iPhone 14 Pro on iOS 26.7.1 exposed display/HID service names after preparation, but media support reported no supported features. The iOS 27 streaming requirement is retained.
- A DVT screenshot succeeded on that older version; this does not establish HEVC streaming or input support. Screenshot mode is not implemented.
- After upgrading that device to iOS 27.2, packaged workers prepared services and opened a RemotePairing Wi-Fi session with codec and video responses. USB remained attached; cable-free behavior and manual input on that second device were not separately confirmed.

Treat control as verified only after a PC input produces the expected action on the phone.
