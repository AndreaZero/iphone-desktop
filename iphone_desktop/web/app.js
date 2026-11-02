"use strict";
const token = document.querySelector('meta[name="app-token"]').content;
const $ = id => document.getElementById(id);
let transport = "usb", snapshot = null, viewerUrl = null, toastTimer, pollTimer, submitting = false, stopping = false;
let cameraSource = null, cameraQrUrl = null, cameraQrKey = null, cameraWasConnected = false, cameraPlayer = null;
const busyStates = new Set(["scanning", "connecting", "preparing"]);
const elapsed = seconds => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
const cameraControls = new DesktopCameraControls($("camera-controls-content"), $("camera-controls-status"), async command => {
  const response = await fetch("/api/camera/control", {method: "POST", signal: AbortSignal.timeout(14000),
    headers: {"X-App-Token": token, "Content-Type": "application/json"}, body: JSON.stringify(command)});
  if (!response.ok) throw new Error((await response.text()).slice(0, 240) || "Could not apply the camera setting.");
  return (await response.json()).camera_state;
});

function notify(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 6500);
}

async function api(path, options = {}) {
  const response = await fetch(path, {...options, signal: AbortSignal.timeout(path === "/api/action" ? 35000 : 8000), headers: {"X-App-Token": token, "Content-Type": "application/json", ...options.headers}});
  if (!response.ok) throw new Error(response.status === 409 ? "An operation is already in progress." : "Operation failed. Try again or check the log.");
  return response.json();
}

async function action(name) {
  if (submitting && name !== "disconnect" && name !== "quit") return;
  const pending = !["disconnect", "quit"].includes(name);
  submitting = pending;
  stopping = name === "disconnect";
  if (snapshot) render(snapshot);
  try {
    await api("/api/action", {method: "POST", body: JSON.stringify({action: name, device: $("device").value || null, transport, wifi_host: name === "wifi" ? $("wifi-ip").value.trim() || null : null})});
    if (name === "quit") {
      clearInterval(pollTimer);
      $("loading").hidden = true;
      $("empty-message").textContent = "Application stopped. You can close this window.";
      $("empty").hidden = false;
      $("viewer").removeAttribute("src");
      $("viewer").hidden = true;
      cameraPlayer?.stop();
      cameraControls.reset();
      $("camera-panel").hidden = true;
      window.close();
      return;
    }
  } catch (error) { notify(error.name === "TimeoutError" ? "The local service is taking longer than expected. Check its status before trying again." : error.message); }
  finally {
    submitting = false;
    stopping = false;
    if (name !== "quit") {
      await update();
      if (snapshot) render(snapshot);
    }
  }
}

function render(data) {
  snapshot = data;
  const connected = data.state === "connected";
  const camera = !!data.camera?.active;
  const active = connected || camera;
  $("connection-fields").hidden = camera;
  $("workspace-title").textContent = camera ? "Your camera workspace." : "Connect your iPhone.";
  $("workspace-intro").textContent = camera ? "Fine-tune your live feed from this PC." : "Screen control or a camera for your calls.";
  $("setup").hidden = camera;
  const busy = submitting || stopping || data.busy || busyStates.has(data.state);
  const matching = data.devices.filter(d => d.transport === transport);
  const oldValue = $("device").value;
  const optionsKey = JSON.stringify(matching.map(d => [d.id, d.name, d.transport]));
  if ($("device").dataset.optionsKey !== optionsKey) {
    $("device").replaceChildren();
    if (!matching.length) $("device").add(new Option(transport === "usb" ? "No USB iPhone found" : "No Wi-Fi iPhone found", ""));
    matching.forEach(d => $("device").add(new Option(d.name, d.id)));
    if (matching.some(d => d.id === oldValue)) $("device").value = oldValue;
    $("device").dataset.optionsKey = optionsKey;
  }
  const selected = matching.find(d => d.id === $("device").value);
  $("device-meta").textContent = selected ? `${selected.model || "iPhone"}${selected.ios ? " · iOS " + selected.ios : ""} · ${selected.paired ? "trusted" : "trust required"}` : "Connect and unlock your iPhone, then refresh.";
  $("device").disabled = busy || active;
  $("connect").disabled = !selected || busy || active;
  $("connect").hidden = active;
  $("scan").disabled = busy || active;
  $("disconnect").hidden = !busy && !active;
  $("disconnect").textContent = stopping ? "Stopping…" : busy ? "Cancel" : camera ? "Stop webcam mode" : "Disconnect";
  $("start-camera").disabled = busy || camera;
  $("start-camera").hidden = camera;
  $("disconnect").disabled = stopping || (submitting && !data.busy);
  $("cancel-loading").hidden = !busy;
  $("cancel-loading").disabled = stopping || (submitting && !data.busy);
  $("cancel-loading").textContent = stopping ? "Stopping…" : "Cancel";
  for (const name of ["prepare", "reveal", "setup-wifi"]) $(name).disabled = !selected || transport !== "usb" || busy || active;
  $("setup-wifi").hidden = transport !== "usb";
  $("wifi-config").hidden = transport !== "usb";
  $("wifi-ip").disabled = busy || active;
  if ($("wifi-ip").dataset.device !== (selected?.id || "")) {
    $("wifi-ip").value = selected?.wifi_host || "";
    $("wifi-ip").dataset.device = selected?.id || "";
  }
  $("usb").disabled = busy || active;
  $("wifi").disabled = busy || active;
  const starting = submitting && !data.busy;
  const message = stopping ? "Stopping the current operation and closing the device connection…" : starting ? "Starting your request…" : data.message;
  $("status-card").className = `status-card ${busy && !busyStates.has(data.state) ? "scanning" : data.state}`;
  $("status-title").textContent = stopping ? "Stopping" : starting ? "Starting" : data.problem ? data.problem.title : ({idle: "Ready to start", scanning: "Finding your iPhone", connecting: "Connecting", preparing: "Preparing", connected: "Session active", camera: data.camera.connected ? "Camera live" : "Connect your camera", error: "Check the connection"}[data.state] || "iPhone Desktop");
  $("status-text").textContent = busy ? message : data.problem ? data.problem.detail : camera && data.camera.connected ? "Keep Safari open. Camera adjustments also update the OBS video." : message;
  $("preview-dot").classList.toggle("live", connected || data.camera?.connected);
  $("preview-title").textContent = camera ? "iPhone webcam" : connected && selected ? selected.name : busy ? "Getting your iPhone ready" : "Your screen, right here.";
  $("footer-status").textContent = camera ? "Camera mode · local video for OBS and Discord" : connected ? "Local session · click and drag on the screen" : busy ? `${data.state === "scanning" ? "Finding devices" : "Preparing connection"} · ${elapsed(data.progress?.elapsed_seconds || 0)} elapsed` : "Waiting for a device";
  $("version").textContent = `v${data.version} · preview`;
  $("fullscreen").hidden = !active;
  $("empty").hidden = active || busy;
  $("loading").hidden = !busy || connected;
  $("loading").setAttribute("aria-busy", String(busy));
  $("loading-title").textContent = stopping ? "Stopping the connection" : data.state === "scanning" ? "Finding your iPhone" : "Preparing your connection";
  $("loading-message").textContent = message;
  $("loading-stage").textContent = data.progress?.total > 1 ? `STEP ${data.progress.step} OF ${data.progress.total}` : data.state === "scanning" ? `${transport.toUpperCase()} DISCOVERY` : "GETTING READY";
  $("loading-elapsed").textContent = data.progress ? `${elapsed(data.progress.elapsed_seconds)} elapsed · ${data.progress.elapsed_seconds >= 15 ? "Still working. You can cancel at any time." : "Keep your iPhone unlocked."}` : "Just a moment…";
  $("connection-steps").hidden = data.progress?.total !== 3;
  [...$("connection-steps").children].forEach((item, index) => {
    item.classList.toggle("complete", index + 1 < data.progress?.step);
    item.classList.toggle("current", index + 1 === data.progress?.step);
  });
  $("viewer").hidden = !connected;
  if (connected && data.viewer !== viewerUrl) { viewerUrl = data.viewer; $("viewer").src = viewerUrl; $("setup").open = false; }
  if (!connected && viewerUrl) { viewerUrl = null; $("viewer").removeAttribute("src"); }
  renderCamera(data.camera);
  if ($("log-dialog").open) renderLog();
}

async function loadCameraQr(index, key) {
  try {
    const response = await fetch(`/api/camera/qr?index=${index}`, {headers: {"X-App-Token": token}, signal: AbortSignal.timeout(8000)});
    if (!response.ok) throw new Error("QR code unavailable. Copy the camera link instead.");
    const blob = await response.blob();
    if (cameraQrKey !== key) return;
    if (cameraQrUrl) URL.revokeObjectURL(cameraQrUrl);
    cameraQrUrl = URL.createObjectURL(blob);
    $("camera-qr").src = cameraQrUrl;
  } catch (error) { if (cameraQrKey === key) { cameraQrKey = null; notify(error.message); } }
}

function cameraAddressChanged() {
  const camera = snapshot?.camera;
  if (!camera?.active) return;
  const index = Number($("camera-address").value) || 0;
  const link = camera.sender_urls[index];
  $("camera-link").value = link;
  if (cameraQrKey !== link) { cameraQrKey = link; loadCameraQr(index, link); }
}

function renderCamera(camera) {
  $("camera-panel").hidden = !camera?.active;
  $("camera-tools").hidden = !camera?.active;
  $("camera-controls-panel").hidden = !camera?.active;
  if (!camera?.active) {
    cameraPlayer?.stop();
    cameraPlayer = null;
    cameraSource = null;
    cameraWasConnected = false;
    cameraControls.render(null, false);
    cameraQrKey = null;
    if (cameraQrUrl) { URL.revokeObjectURL(cameraQrUrl); cameraQrUrl = null; $("camera-qr").removeAttribute("src"); }
    return;
  }
  const source = new URL(camera.source_path, location.origin).href + "?clean=1";
  if (cameraSource !== source) {
    cameraSource = source;
    cameraControls.reset();
    cameraPlayer?.stop();
    cameraPlayer = new CameraPreview($("camera-view"));
    cameraPlayer.connect(new URL("frames", source).href);
    $("camera-source").value = source;
    $("open-camera-source").href = source;
    $("camera-pairing").open = true;
    $("setup").open = false;
  }
  const key = JSON.stringify(camera.sender_urls);
  if ($("camera-address").dataset.optionsKey !== key) {
    $("camera-address").replaceChildren(...camera.sender_urls.map((url, i) => new Option(new URL(url).hostname + (i === 0 ? " · recommended" : " · alternate adapter"), i)));
    $("camera-address").dataset.optionsKey = key;
  }
  cameraAddressChanged();
  $("camera-fingerprint").textContent = camera.certificate_sha256?.match(/.{1,2}/g)?.join(":") || "";
  $("camera-status").textContent = camera.connected ? "● Camera live" : "Waiting for camera · open the link on your iPhone";
  $("camera-status").classList.toggle("live", camera.connected);
  $("camera-stage").hidden = !camera.connected;
  $("camera-waiting").hidden = !!camera.connected;
  cameraControls.render(camera.camera_state, !!camera.connected);
  if (camera.connected && !cameraWasConnected) $("camera-pairing").open = false;
  if (!camera.connected && cameraWasConnected) $("camera-pairing").open = true;
  cameraWasConnected = camera.connected;
}

async function copyField(id) {
  try { await navigator.clipboard.writeText($(id).value); notify("Link copied."); }
  catch { $(id).focus(); $(id).select(); notify("Press Ctrl+C to copy the selected link."); }
}

function renderLog() {
  if (!snapshot) return;
  $("log-content").textContent = [...snapshot.logs.map(l => `${l.time}  ${l.text}`), ...snapshot.engine_log].join("\n") || "No operations logged.";
}

let polling = false, failures = 0;
async function update() {
  if (polling) return;
  polling = true;
  try { render(await api("/api/state")); failures = 0; }
  catch (error) {
    if (++failures === 3) {
      $("status-card").className = "status-card error";
      $("status-title").textContent = "Application unavailable";
      $("status-text").textContent = "Restart iPhone Desktop from the application folder.";
      $("loading").hidden = true;
      if (!viewerUrl) { $("empty").hidden = false; $("empty-message").textContent = "The local service is unavailable. Restart iPhone Desktop from the application folder."; }
    }
  }
  finally { polling = false; }
}

for (const mode of ["usb", "wifi"]) $(mode).onclick = () => {
  transport = mode;
  $("usb").classList.toggle("selected", mode === "usb");
  $("wifi").classList.toggle("selected", mode === "wifi");
  $("usb").setAttribute("aria-pressed", String(mode === "usb"));
  $("wifi").setAttribute("aria-pressed", String(mode === "wifi"));
  $("transport-hint").textContent = mode === "usb" ? "Start with a data-capable USB cable." : "For initial setup, use Set up Wi-Fi with USB connected. Then unplug and refresh. The PC can stay on Ethernet; keep the iPhone unlocked with Wi-Fi enabled.";
  $("setup-wifi").hidden = mode !== "usb";
  if (snapshot) render(snapshot);
  if (!snapshot?.busy && snapshot?.state !== "connected") action("scan");
};
$("device").onchange = () => { if (snapshot) render(snapshot); };
$("scan").onclick = () => action("scan");
$("connect").onclick = () => action("connect");
$("start-camera").onclick = () => action("camera");
$("camera-address").onchange = cameraAddressChanged;
$("copy-camera-link").onclick = () => copyField("camera-link");
$("copy-camera-source").onclick = () => copyField("camera-source");
$("disconnect").onclick = () => action("disconnect");
$("cancel-loading").onclick = () => action("disconnect");
$("prepare").onclick = () => action("prepare");
$("reveal").onclick = () => action("reveal");
$("setup-wifi").onclick = () => action("wifi");
$("quit").onclick = () => action("quit");
$("show-log").onclick = () => { renderLog(); $("log-dialog").showModal(); };
$("close-log").onclick = () => $("log-dialog").close();
$("fullscreen").onclick = () => $(snapshot?.camera?.active ? "camera-stage" : "viewer").requestFullscreen().catch(() => notify("Could not enter full screen."));
update().then(() => {
  if (snapshot && !snapshot.busy && !busyStates.has(snapshot.state) && snapshot.state !== "connected" && !snapshot.camera?.active) action("scan");
});
pollTimer = setInterval(update, 750);
