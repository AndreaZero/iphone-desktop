import unittest
from unittest.mock import patch

from iphone_desktop.viewer import prepare_viewer


class ViewerLayoutTests(unittest.TestCase):
    def test_pinned_viewer_gets_one_layout_skin_and_retains_upstream_controls(self):
        from pymobiledevice3.remote.core_device import screen_stream
        with patch.object(screen_stream, "_iphone_desktop_layout", False, create=True), \
                patch.object(screen_stream, "VIEWER_CSS", screen_stream.VIEWER_CSS), \
                patch.object(screen_stream, "VIEWER_JS_TEMPLATE", screen_stream.VIEWER_JS_TEMPLATE):
            prepare_viewer()
            css, script = screen_stream.VIEWER_CSS, screen_stream.VIEWER_JS_TEMPLATE
            prepare_viewer()
            self.assertEqual(screen_stream.VIEWER_CSS, css)
            self.assertEqual(screen_stream.VIEWER_JS_TEMPLATE, script)
            self.assertIn(b"height:100dvh", css)
            self.assertIn(b"new VideoDecoder", script)
            self.assertIn(b"function fitCanvasToViewport", script)
            self.assertIn(b"pymobiledevice3", screen_stream.VIEWER_HTML)

    def test_incompatible_backend_fails_without_partially_modifying_viewer(self):
        from pymobiledevice3.remote.core_device import screen_stream
        with patch.object(screen_stream, "_iphone_desktop_layout", False, create=True), \
                patch.object(screen_stream, "VIEWER_JS_TEMPLATE", b"changed upstream viewer"):
            css = screen_stream.VIEWER_CSS
            with self.assertRaisesRegex(RuntimeError, "layout has changed"):
                prepare_viewer()
            self.assertEqual(screen_stream.VIEWER_CSS, css)
            self.assertEqual(screen_stream.VIEWER_JS_TEMPLATE, b"changed upstream viewer")
