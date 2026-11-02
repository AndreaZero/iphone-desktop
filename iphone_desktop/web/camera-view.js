(function () {
  "use strict";

  class CameraPreview {
    constructor(canvas, waitingElement = null) {
      this.canvas = canvas;
      this.context = canvas.getContext("2d", {alpha: false});
      this.waitingElement = waitingElement;
      this.session = null;
      this.socket = null;
      this.reconnectTimer = null;
      this.staleTimer = null;
      this.clean = false;
    }

    clearFrame(message = "Waiting for the iPhone camera…") {
      if (this.session) {
        this.session.lastFrame = 0;
        this.session.pending = null;
      }
      this.context.clearRect(0, 0, this.canvas.width, this.canvas.height);
      this.canvas.hidden = true;
      if (this.waitingElement) {
        this.waitingElement.textContent = message;
        this.waitingElement.hidden = this.clean;
      }
    }

    async decodeFrames(session) {
      if (session.decoding) return;
      session.decoding = true;
      try {
        while (session.pending && this.session === session) {
          const blob = session.pending;
          session.pending = null;
          const bitmap = await createImageBitmap(blob);
          try {
            if (this.session !== session || !session.lastFrame || performance.now() - session.lastFrame > 3000) continue;
            if (this.canvas.width !== bitmap.width || this.canvas.height !== bitmap.height) {
              this.canvas.width = bitmap.width;
              this.canvas.height = bitmap.height;
            }
            this.context.drawImage(bitmap, 0, 0);
            this.canvas.hidden = false;
            if (this.waitingElement) this.waitingElement.hidden = true;
          } finally { bitmap.close(); }
        }
      } catch (_) { /* Skip damaged frames; the next valid frame replaces them. */ }
      finally {
        session.decoding = false;
        if (session.pending && this.session === session) this.decodeFrames(session);
      }
    }

    connect(framesUrl) {
      this.stop();
      const address = new URL(framesUrl, location.href);
      if (address.protocol === "http:") address.protocol = "ws:";
      if (address.protocol === "https:") address.protocol = "wss:";
      const session = {url: address.href, pending: null, decoding: false, lastFrame: 0};
      this.session = session;
      this.clearFrame();
      const ws = new WebSocket(address);
      ws.binaryType = "blob";
      this.socket = ws;
      ws.onmessage = event => {
        if (this.session !== session) return;
        if (typeof event.data === "string") {
          if (event.data === "waiting") this.clearFrame();
          return;
        }
        session.lastFrame = performance.now();
        // Keep one newest frame while the preceding image is being decoded.
        session.pending = event.data;
        this.decodeFrames(session);
      };
      ws.onclose = () => {
        if (this.session !== session) return;
        this.clearFrame("Camera connection ended. Waiting to reconnect…");
        clearTimeout(this.reconnectTimer);
        this.reconnectTimer = setTimeout(() => {
          if (this.session === session) this.connect(session.url);
        }, 1500);
      };
      ws.onerror = () => ws.close();
      this.staleTimer = setInterval(() => {
        if (this.session === session && session.lastFrame && performance.now() - session.lastFrame > 3000) this.clearFrame();
      }, 1000);
      return this;
    }

    stop() {
      this.session = null;
      clearTimeout(this.reconnectTimer);
      clearInterval(this.staleTimer);
      this.reconnectTimer = this.staleTimer = null;
      if (this.socket) {
        this.socket.onclose = this.socket.onerror = this.socket.onmessage = null;
        this.socket.close();
        this.socket = null;
      }
      this.clearFrame();
    }
  }

  window.CameraPreview = CameraPreview;

  // The same renderer powers the main app canvas and the standalone OBS page.
  const canvas = document.getElementById("feed");
  if (canvas) {
    const options = new URLSearchParams(location.search);
    canvas.classList.toggle("cover", options.get("fit") === "cover");
    const preview = new CameraPreview(canvas, document.getElementById("waiting"));
    preview.clean = options.get("clean") === "1";
    const frames = new URL("frames", location.href);
    preview.connect(frames);
    window.addEventListener("pagehide", () => preview.stop());
    window.addEventListener("pageshow", event => { if (event.persisted) preview.connect(frames); });
  }
})();
