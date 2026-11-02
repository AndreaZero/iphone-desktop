# Validation

Development checks recorded through November 2, 2026. Hardware results describe tested configurations, not a guarantee for every device, network, or future iOS release. Personal device names, identifiers, and network addresses are omitted.

## Automated coverage

- API token, Host, and Origin checks; rejection of undiscovered devices.
- Cancellation, worker timeout cleanup, and connection failures.
- RSD developer image preparation and already-mounted images.
- Missing display/HID services, HTTP 500 despite working viewer HTML, delayed video startup, and failure cleanup.
- Wi-Fi discovery without usbmux network devices, deduplication, saved-IP RemotePairing, identity checks, and tunnel cleanup.
- Transport-specific discovery: USB scans do not wait for Bonjour; scan/progress APIs stay responsive and cancellable.
- Cooperative stream shutdown requests coroutine cancellation so upstream cleanup can release iOS remote observation and camera access; cleanup/error propagation is covered with simulated services.
- Camera mode transition ordering, device-independent startup, source/QR authorization, disconnect, and link revocation.
- Camera sender/viewer WebSocket relay with JPEG acknowledgement, exclusive publisher, frame size limit, stale-frame clearing, token/Host/Origin checks, listener shutdown, and persistent CA signatures/SAN/EKU.

Run `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` for the current suite.

## Camera controls and compact workspace (0.4.0)

- All 60 Python tests and 13 Node camera tests passed. Coverage includes duplex commands during JPEG streaming, capability validation, bounded request timeouts, stale responses, disconnect/replacement, manual mode constraints, ignored-setting detection, lens switching without replacing the upload socket, digital crop output, and a reset interrupted by Stop/Start.
- Chrome's desktop sidebar successfully sent zoom 2×, Manual exposure, and ISO 150 to a simulated phone and displayed confirmed values while synthetic video continued. These are control-path tests, not evidence that a physical iPhone exposes those particular settings.
- At 1250 × 800 and 1024 × 640, the document height matched the viewport and only the sidebar overflowed vertically. The separate phone page also fit a 390 × 844 viewport with a centered preview and its own settings column.
- The pinned screen viewer retains its decoder/input and attribution, with a compact fixed layout and initially collapsed utility trays. Compatibility/idempotency checks pass. The new screen layout has not been independently inspected during a physical mirroring session.
- Native camera settings available on the user's specific iPhone still require hardware confirmation. Safari-reported capabilities and readable values control which settings appear; no unreported sensor exposure controls are invented.
- The portable 0.4.0 executable passed QR/shared-settings asset loading, verified local TLS, JPEG streaming, a confirmed zoom command through the desktop API, disconnect/stale clearing, revocation, shutdown, and the skinned backend self-test.

Run `node --test tests/camera-settings.test.cjs tests/camera-phone.test.cjs` for the phone capability/runtime tests. Node.js is a development check only.

## Camera mode (0.3.0)

All 46 automated tests passed on November 2. The local relay and certificate lifecycle are covered with real aiohttp sockets and simulated JPEG uploads. Chrome displayed the webcam setup QR and both the main app preview and clean OBS source rendered a 1280 × 720 synthetic video uploaded over a verified local TLS connection. Each 45-second synthetic run acknowledged 721 frames; this is a desktop test sender, not a measurement of iPhone performance. Disconnect cleared the video.

The portable 0.3.0 executable also passed QR generation, local certificate creation, verified TLS sender access, live JPEG relay, disconnect/stale clearing, link revocation, orderly shutdown, and the frozen iPhone backend self-test.

The user subsequently confirmed that the iPhone camera feed worked over Wi-Fi and as an OBS Browser source, describing latency as practically imperceptible. This is user-reported hardware validation, not an instrumented latency/frame-rate measurement. Individual front/rear lens behavior and Discord video calls were not recorded as separate controlled checks. No native Windows webcam driver is installed; Discord uses OBS Virtual Camera.

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
