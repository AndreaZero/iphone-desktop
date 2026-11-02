"""Local layout overrides for the pinned, attributed upstream screen viewer."""
from pathlib import Path


def prepare_viewer():
    from pymobiledevice3.remote.core_device import screen_stream

    if getattr(screen_stream, "_iphone_desktop_layout", False):
        return
    replacements = (
        (b"const stacked = window.matchMedia('(max-width: 900px)').matches;", b"const stacked = false;"),
        (b"localStorage.getItem('tray-' + side) === 'collapsed'", b"localStorage.getItem('tray-' + side) !== 'open'"),
    )
    script = screen_stream.VIEWER_JS_TEMPLATE
    for original, replacement in replacements:
        if script.count(original) != 1:
            raise RuntimeError("The screen viewer layout has changed. Update the local layout integration for this backend.")
        script = script.replace(original, replacement)
    # Keep the upstream decoder, device input, preferences and attribution.
    screen_stream.VIEWER_JS_TEMPLATE = script
    screen_stream.VIEWER_CSS += b"\n" + (Path(__file__).resolve().parent / "web" / "screen-viewer.css").read_bytes()
    screen_stream._iphone_desktop_layout = True
