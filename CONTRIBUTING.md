# Contributing

Bug reports, compatibility results, documentation fixes, and pull requests are welcome.

Use Windows and Python 3.13. Follow [README.md](README.md), then run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Keep changes focused, explain the resulting behavior, and include checks run. Add or update tests when changing connection, API, or tunnel behavior. Keep documentation and user-facing text in English.

For bug reports, include app and Windows versions, iPhone model, iOS version, transport, reproduction steps, expected behavior, and actual behavior. Note Developer Mode and trust status. Distinguish discovery, video output, and input control in hardware reports.

Review logs before attaching them. Remove device names, identifiers, addresses, tokens, and pairing information. Do not upload `.runtime`, pairing records, or personal developer images.

Device transport and the viewer come from the pinned pymobiledevice3 backend. Preserve attribution and explain compatibility when using upstream internals. Verify tunnel cleanup.

Contributions are distributed under this repository's GPL-3.0 license.
