# Third-party components

iPhone Desktop 0.4.0 is distributed with its source under the GNU General Public License v3.0. See [LICENSE](LICENSE).

The iOS transport, developer services, HID handling, and WebCodecs viewer are provided by **pymobiledevice3 11.49.0**, © doronz88 and contributors, GPL-3.0: <https://github.com/doronz88/pymobiledevice3>. These components are not presented as original project code. The official Python distribution is on [PyPI](https://pypi.org/project/pymobiledevice3/11.49.0/).

The server uses aiohttp. The desktop window uses Chrome or Edge installed on the PC. Portable executables are built with PyInstaller and include Python and dependencies recorded in `requirements-lock.txt`. `scripts/bundle_notices.py` collects installed dependency license texts into the distribution's `licenses` directory.

Camera pairing QR codes use **qrcode 8.2** (BSD-3-Clause); local camera certificates use cryptography, already included through the transport backend. No camera frames or pairing links are sent to a third-party QR service. The clean output can be added to OBS Studio, which supplies the optional virtual camera; OBS is installed separately.

Apple developer components downloaded during preparation are not included in the application distribution and retain their respective terms. No affiliation with Apple, Microsoft, or Google.

Build instructions: [README.md](README.md), `requirements-lock.txt`, and `scripts/build.ps1`. iOS protocol support depends on upstream compatibility with the installed iOS version.
