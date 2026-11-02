# iPhone Desktop

<img src="iphone_desktop/web/app-icon.png" alt="iPhone Desktop icon" width="96">

View and control your iPhone from a Windows PC over USB or Wi-Fi. A local desktop interface for iOS developer services powered by [pymobiledevice3](https://github.com/doronz88/pymobiledevice3).

**Version 0.4.0 · Preview · Windows 10/11 x64 · iOS 27 or later for screen control**

## Features

- Live screen viewing and mouse/keyboard input through the upstream WebCodecs viewer.
- USB and wireless RemotePairing connections on your local network.
- Guided device trust, Developer Mode, and developer service setup.
- Automatic developer image preparation, local diagnostics, and session cleanup.
- Separate iPhone webcam mode with a local Safari camera sender, live PC preview, and an OBS Browser source for OBS Virtual Camera / Discord.
- Faster USB-only startup discovery, visible connection stages, elapsed time, and cancellation.
- Live camera controls from the PC or iPhone: available cameras/lenses, zoom, supported exposure/focus/color controls, torch, and reset.
- Compact fixed workspace with a centered video/screen preview and independently scrolling settings sidebar.
- English interface. No Bluetooth transport, jailbreak, signed iPhone app, subscription, or application cloud relay.

## Requirements

- Windows 10/11 x64 and Python 3.13 for running from source or building.
- Chrome or Edge with WebCodecs and HEVC decoding available.
- **iOS 27 or later** for the streaming protocol in the pinned backend. Older versions are rejected before the viewer opens.
- Apple Devices or iTunes with Apple Mobile Device support for USB.
- A data-capable USB cable for initial trust and wireless setup.
- An unlocked, trusted iPhone with Developer Mode enabled.
- Internet access on the PC for the first developer image download.

## Download for Windows

Download **iPhone-Desktop-0.4.0-Windows-x64.zip** from the [0.4.0 release](https://github.com/AndreaZero/iphone-desktop/releases/tag/v0.4.0), extract the entire archive to a writable folder, and open **iPhone Desktop.exe** inside **iPhone Desktop**. Keep all files together. Python and application dependencies are included; no separate Python installation is needed. The portable build is unsigned. Use the attached `SHA256SUMS.txt` to verify the download.

## Run from source

```powershell
git clone https://github.com/andreazero/iphone-desktop.git
cd iphone-desktop
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

The app opens a Chrome or Edge application window. Prebuilt Windows packages are available under [Releases](https://github.com/AndreaZero/iphone-desktop/releases).

## USB setup

1. Connect the data cable, unlock the iPhone, and accept **Trust This Computer** on the phone.
2. Enable **Settings → Privacy & Security → Developer Mode**. Restart and confirm on the phone. If the setting is missing, click **Setting missing?** to reveal it; the app does not enable it or restart the phone.
3. Select the device and click **Connect iPhone**. Developer services are prepared automatically, or separately with **Prepare services**. The first download may take several minutes. On iOS 27, preparation uses an RSD tunnel and a Cryptex1 developer image.
4. Try a harmless action, such as returning to the Home screen, to confirm input. Click and drag in the viewer to send touch input; follow the viewer's keyboard instructions.

## Wi-Fi setup

1. Complete USB trust and Developer Mode setup first.
2. With USB connected, click **Set up Wi-Fi while USB is connected**. This enables network connections and creates developer RemotePairing through trusted USB.
3. If discovery cannot find the phone, enter its local IPv4 address before setup: **Settings → Wi-Fi → ⓘ → IP Address**.
4. Disconnect USB, keep the phone unlocked, select **Wi-Fi**, refresh, and connect.

An Ethernet-connected PC and a Wi-Fi-connected phone can communicate on the same local network. Guest networks and client isolation may prevent connections. A saved IP is queried directly through Bonjour when multicast discovery is unavailable. If it changes, update it through USB setup. A cold scan may return no devices; refresh and try again.

## Build a portable Windows app

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
powershell -File scripts\build.ps1 -SkipDependencyInstall
```

Open `dist\iPhone Desktop\iPhone Desktop.exe`. Keep the entire distribution folder together; the executable cannot be moved on its own. `Start.cmd` launches the built app or source environment.

The build runs tests and collects dependency license notices. `requirements-lock.txt` records the resolved development/build environment; install it instead of the requirements files to reproduce those exact versions. The build preserves an existing distribution's `.runtime` folder. Remove personal runtime data from any package you share.

Windows and browser icons are in `iphone_desktop/web`. Install Pillow and run `scripts/generate_icons.py` to regenerate them.

## iPhone camera, OBS, and Discord

iOS blocks camera/microphone access during developer screen mirroring. Opening Camera while controlling the screen can therefore show a black preview; changing the desktop video decoder cannot restore that camera feed. [Apple documents this conflict](https://developer.apple.com/documentation/xcode/interacting-with-your-app-in-device-hub#Handle-camera-and-microphone-access-conflicts-on-physical-devices).

Use **Use iPhone as webcam** instead. This stops screen mirroring cooperatively and opens a separate camera connection. It does not need USB pairing or Developer Mode; the phone captures video in Safari on the same local network as the PC.

1. Click **Use iPhone as webcam** and scan the QR code with your iPhone. Open the setup link in Safari. If it cannot connect, choose the PC's correct network adapter in the app; allow the app on your private network if Windows asks. Guest networks and client isolation may block the connection.
2. On first use, download the local camera certificate from the setup page. Compare its SHA-256 fingerprint with the one shown on the PC. Install **iPhone Desktop Local Camera** under **Settings → General → VPN & Device Management** (or **Profile Downloaded**), then enable full trust under **Settings → General → About → Certificate Trust Settings**. [Apple's trust instructions](https://support.apple.com/en-us/102390). The certificate is generated on your PC; it is not installed automatically.
3. Return to Safari and tap **Open camera → Start camera**, then allow camera access. Select front or rear camera. Keep Safari visible and the phone unlocked; switching apps or locking stops capture.
4. In OBS, add a **Browser** source and paste **Copy OBS URL** from the app. Use 1280 × 720; the feed preserves the phone's aspect ratio. The clean source contains only video and clears to black when disconnected. [OBS Browser source guide](https://obsproject.com/kb/browser-source).
5. To use the feed in Discord, click **Start Virtual Camera** in OBS, then choose **OBS Virtual Camera** under **User Settings → Voice & Video → Camera** in Discord. [OBS Virtual Camera guide](https://obsproject.com/kb/virtual-camera-guide).

Camera mode sends video only (up to 1280 pixels on the longest side, targeting 20 fps); choose your PC microphone separately. Frame rate and latency depend on the phone and local network. Camera mode is experimental until verified on your physical iPhone. It uses JPEG frames over an authenticated local TLS WebSocket; it does not install a webcam driver or register its own Windows camera device. OBS supplies the virtual camera.

Keep iPhone Desktop open during capture. **Stop webcam mode** closes the camera listeners, clears the last frame, and revokes the session links. The setup and OBS URLs change each time camera mode starts. The local certificate is reused across restarts; remove its profile in iPhone settings when no longer needed. Do not share `.runtime/camera-tls`, which contains private keys. Deleting it creates a new certificate and requires repeating trust setup.

### Camera controls

After starting the camera in Safari, **Camera controls** appears in the PC sidebar. The phone has the same settings. Reload Safari after updating the desktop app to load the new control interface.

- **Lens & framing:** select any camera/lens actually listed by Safari, use the zoom slider or quick zoom buttons. Lens names come from the browser; the app does not assume that every iPhone exposes separate 0.5×/telephoto devices. Camera switches keep the video connection open while changing the capture track.
- **Zoom:** the camera's native zoom is used when exposed and readable. Otherwise **Digital zoom** crops the source image from 1× to 4× before sending it; both the phone preview and OBS receive that framing. Digital zoom reduces image detail.
- **Exposure:** exposure mode, EV compensation, shutter duration, and ISO appear only if the browser reports adjustable capabilities and current values. Choose Manual to unlock manual shutter/ISO controls where supported. Shutter values are displayed as seconds or fractions of a second.
- **Focus and color:** focus mode/distance, white balance/temperature, and image controls appear when supported. Manual focus or white balance modes unlock their corresponding manual controls. **Light** provides torch control where available.
- **Reset camera settings:** restore the values captured when the current camera opened and return digital framing to 1×. Confirmed values are synchronized to the PC and phone; rejected settings show an error and retain the camera's reported values.

Safari may expose only a subset of these controls, and many iPhones do not expose manual sensor exposure through the browser. Missing controls are omitted; they are not simulated sensor adjustments. The app keeps pixel processing out of the capture loop. OBS can apply separate color correction to the received video when needed. No change to the OBS source is needed for an ordinary camera adjustment.

## Development

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

With Node.js installed, run the camera capability and phone runtime regressions using `node --test tests/camera-settings.test.cjs tests/camera-phone.test.cjs`. Node.js is not required by the app or portable executable.

The aiohttp server manages discovery, prerequisites, sessions, cancellation, and errors. Each device operation runs in an isolated worker. USB/RSD/HID protocols and the WebCodecs viewer come from pymobiledevice3 11.49.0, with upstream attribution preserved.

Application APIs, screen streaming, and camera consumers bind to `127.0.0.1`. App APIs check the session token, Host, and Origin; the upstream viewer applies its own origin checks. The viewer opens only after actual video codec configuration is received. Opting into webcam mode additionally starts LAN HTTP setup/certificate and HTTPS camera-upload listeners with separate random session secrets, pinned Host/Origin checks, one sender, eight local viewers, bounded frames, and stale-frame clearing. Those listeners close when webcam mode stops; they do not expose the device-control APIs to the network.

See [CONTRIBUTING.md](CONTRIBUTING.md) and [VALIDATION.md](VALIDATION.md) for contribution guidance and validation limits.

## Limitations and local data

- iOS developer protocols may change. Detection or a successful HTML response alone does not prove video or input support.
- Audio is unavailable in this Windows version; the upstream audio decoder requires macOS.
- Developer preparation may download Apple components and contact personalization services through pymobiledevice3. Screen frames and input use the PC and local device connection without an application relay.
- State, logs, and saved wireless addresses are in `.runtime` beside the app. They are not uploaded automatically and may contain device names and identifiers. Review before sharing.
- **Disconnect** stops the engine. **Quit** stops the application. Closing only the window stops the process after approximately two minutes without a heartbeat.

## License

Distributed under the [GNU General Public License v3.0](LICENSE). See [THIRD_PARTY.md](THIRD_PARTY.md) for component attribution and build notices.

An independent project, unaffiliated with Apple, Microsoft, or Google.
