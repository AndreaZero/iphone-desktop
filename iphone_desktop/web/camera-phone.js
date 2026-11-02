"use strict";

const $ = id => document.getElementById(id);
const video = $("preview"), preview = $("camera-preview");
const canvas = document.createElement("canvas");
const context = canvas.getContext("2d", {alpha: false});
const MAX_FRAME_BYTES = 512 * 1024, FRAME_INTERVAL = 50;
let generation = 0, stream = null, socket = null, wakeLock = null, running = false;
let ack = null, ackTimer = null, frameTimer = null, resumeFrame = null;
let digitalZoom = 1, switching = false, phoneBusy = false, devices = [], originalSettings = {};
let selectedFacing = "environment", lastCameraState = null, controlQueue = Promise.resolve();
let previewAspect = 4 / 3;
let appliedNative = {};
const captureConstraints = {width: {ideal: 1280}, height: {ideal: 720}, frameRate: {ideal: 20, max: 30}};

function fitPreview() {
  const area = $("preview-area"), availableWidth = area.clientWidth - 24, availableHeight = area.clientHeight - 24;
  const width = Math.max(1, Math.min(availableWidth, availableHeight * previewAspect));
  preview.style.width = `${width}px`;
  preview.style.height = `${width / previewAspect}px`;
}

function status(title, text, kind = "") {
  $("status-title").textContent = title;
  $("status-text").textContent = text;
  $("status").className = `camera-status ${kind}`;
}

function controls(active, starting = false) {
  $("start").hidden = active;
  $("start").disabled = starting;
  $("stop").hidden = !active;
  $("camera").disabled = starting || phoneBusy;
  $("live-label").hidden = !active || starting;
  $("adjustments").hidden = !active || starting;
  $("reset-camera").disabled = starting || phoneBusy;
}

function cameraError(error) {
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    $("certificate-help").hidden = false;
    $("certificate-help").open = true;
    return "Open the secure camera link in Safari after installing and trusting the local certificate. See the steps below.";
  }
  switch (error.name) {
    case "NotAllowedError":
    case "PermissionDeniedError": return "Camera permission was denied. In Safari website settings, allow Camera, then tap Start camera again.";
    case "NotReadableError":
    case "AbortError": return "The camera is unavailable. Disconnect screen mirroring on the PC, close other camera apps, then try again.";
    case "NotFoundError":
    case "OverconstrainedError": return "That camera or setting could not be opened. Select another available camera or value.";
    default: return error.message || "The camera stopped. Check the local connection and tap Start camera to retry.";
  }
}

function stopTracks() {
  if (stream) stream.getTracks().forEach(track => { track.onended = null; track.stop(); });
  stream = null;
  video.pause();
  video.srcObject = null;
}

function stopCamera() {
  generation++;
  running = false;
  switching = phoneBusy = false;
  clearTimeout(frameTimer);
  frameTimer = null;
  if (resumeFrame) { resumeFrame(); resumeFrame = null; }
  clearTimeout(ackTimer);
  if (ack) { const pending = ack; ack = null; pending.reject(new Error("Camera stopped.")); }
  if (socket) { socket.onclose = null; socket.onerror = null; socket.onmessage = null; socket.close(); socket = null; }
  stopTracks();
  if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
  video.hidden = true;
  $("preview-placeholder").hidden = false;
  controls(false);
  lastCameraState = null;
  digitalZoom = 1;
  appliedNative = {};
  controlQueue = Promise.resolve();
}

function currentTrack() { return stream?.getVideoTracks()[0] || null; }

function cameraState() {
  const track = currentTrack();
  let capabilities = {}, settings = {};
  try { capabilities = track?.getCapabilities?.() || {}; } catch (_) { /* Digital framing remains available. */ }
  try { settings = track?.getSettings?.() || {}; } catch (_) { /* Older Safari may omit native settings. */ }
  return CameraSettings.buildState(capabilities, settings, devices, {digitalZoom, busy: phoneBusy, facingMode: selectedFacing});
}

function sendJson(value) {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(value));
}

function publishState() {
  if (!running || !currentTrack()) return null;
  const state = cameraState();
  lastCameraState = state;
  renderAdjustments(state);
  sendJson({type: "camera-state", state});
  return state;
}

function updatePreviewTransform() {
  const front = (currentTrack()?.getSettings?.().facingMode || selectedFacing) === "user";
  video.style.transform = `scale(${front ? -digitalZoom : digitalZoom}, ${digitalZoom})`;
}

async function refreshDevices() {
  try {
    const available = await navigator.mediaDevices.enumerateDevices();
    devices = available.filter(device => device.kind === "videoinput" && device.deviceId).slice(0, 16)
      .map((device, index) => ({id: device.deviceId, label: device.label || `Camera ${index + 1}`}));
  } catch (_) { devices = []; }
}

async function attachCamera(choice, current) {
  const constraints = {...captureConstraints};
  if (choice.deviceId) constraints.deviceId = {exact: choice.deviceId};
  else constraints.facingMode = {ideal: choice.facingMode || selectedFacing};
  const opened = await navigator.mediaDevices.getUserMedia({audio: false, video: constraints});
  if (generation !== current || !running) { opened.getTracks().forEach(track => track.stop()); throw new Error("Camera stopped."); }
  stream = opened;
  currentTrack().onended = () => {
    if (generation === current && !switching) { stopCamera(); status("Camera was interrupted", "Disconnect screen mirroring or close other camera apps, then tap Start camera again.", "error"); }
  };
  video.srcObject = opened;
  video.hidden = false;
  $("preview-placeholder").hidden = true;
  await video.play();
  if (generation !== current || !running) return;
  originalSettings = {...currentTrack().getSettings?.()};
  selectedFacing = originalSettings.facingMode || choice.facingMode || selectedFacing;
  digitalZoom = 1;
  appliedNative = {};
  updatePreviewTransform();
  await refreshDevices();
}

async function switchCamera(choice, current) {
  const previous = currentTrack()?.getSettings?.() || {};
  const previousFacing = selectedFacing;
  switching = true;
  stopTracks();
  try { await attachCamera(choice, current); }
  catch (error) {
    // Safari needs the old track released before opening another lens. Recover
    // the previous lens if a rejected selection would otherwise leave no feed.
    if (generation === current && running) {
      try { await attachCamera(previous.deviceId ? {deviceId: previous.deviceId} : {facingMode: previousFacing}, current); }
      catch (_) { stopCamera(); }
    }
    throw error;
  } finally { switching = false; }
}

async function performControl(command, current) {
  if (!running || !currentTrack() || generation !== current) throw new Error("Start the camera before adjusting it.");
  const name = command?.name;
  if (name === "deviceId") {
    if (typeof command.value !== "string" || !devices.some(device => device.id === command.value)) throw new Error("Select a camera reported by this iPhone.");
    await switchCamera({deviceId: command.value}, current);
  } else if (name === "facingMode") {
    if (!["environment", "user"].includes(command.value)) throw new Error("Select the front or back camera.");
    await switchCamera({facingMode: command.value}, current);
  } else if (name === "reset") {
    const track = currentTrack(), state = cameraState(), values = {};
    for (const [key, control] of Object.entries(state.controls)) {
      if (control.kind !== "digital" && originalSettings[key] !== undefined) values[key] = originalSettings[key];
    }
    if (Object.keys(values).length) await track.applyConstraints({...captureConstraints, ...track.getConstraints?.(), advanced: [values]});
    if (generation !== current || currentTrack() !== track) throw new Error("Camera stopped.");
    appliedNative = {};
    digitalZoom = 1;
    updatePreviewTransform();
  } else {
    const state = cameraState();
    const requested = CameraSettings.constraintFor(state.controls, name, command.value);
    if (!requested.constraints) { digitalZoom = requested.value; updatePreviewTransform(); }
    else {
      const values = {...appliedNative, ...requested.constraints.advanced[0]};
      await currentTrack().applyConstraints({...captureConstraints, ...currentTrack().getConstraints?.(), advanced: [values]});
      // Read settings again: cameras can reject combinations without changing
      // a previously readable value, even if the request itself resolves.
      let actual, matches = false;
      const deadline = performance.now() + 900;
      do {
        await new Promise(resolve => setTimeout(resolve, 60));
        if (generation !== current || !currentTrack()) throw new Error("Camera stopped.");
        actual = currentTrack().getSettings?.()[name];
        matches = typeof requested.value !== "number" ? actual === requested.value : typeof actual === "number" && Math.abs(actual - requested.value) <= Math.max(state.controls[name].step || 0, 0.01);
      } while (!matches && performance.now() < deadline);
      if (!matches) {
        throw new Error("This camera kept a different value. Try another mode or value.");
      }
      appliedNative = values;
    }
  }
}

function queueControl(command) {
  const current = generation;
  const task = controlQueue.catch(() => {}).then(async () => {
    if (generation !== current) throw new Error("Camera stopped.");
    phoneBusy = true;
    controls(true);
    publishState();
    try { await performControl(command, current); }
    finally {
      if (generation === current) { phoneBusy = false; controls(running); publishState(); }
    }
    return lastCameraState;
  });
  controlQueue = task;
  return task;
}

function localControl(command) {
  queueControl(command).then(() => { $("control-message").textContent = "Setting applied · also available from your PC."; })
    .catch(error => { $("control-message").textContent = cameraError(error); });
}

function renderAdjustments(state) {
  const selection = $("camera"), oldValue = selection.value;
  const options = [["facing:environment", "Back camera"], ["facing:user", "Front camera"], ...state.devices.map(device => ["device:" + device.id, device.label])];
  const optionsKey = JSON.stringify(options);
  if (selection.dataset.optionsKey !== optionsKey) {
    selection.replaceChildren(...options.map(([value, text]) => new Option(text, value)));
    selection.dataset.optionsKey = optionsKey;
  }
  const selected = state.selectedDevice ? "device:" + state.selectedDevice : "facing:" + state.facingMode;
  selection.value = options.some(([value]) => value === selected) ? selected : oldValue;
  $("capture-info").textContent = `${state.width || video.videoWidth} × ${state.height || video.videoHeight} · ${state.controls.zoom.kind === "digital" ? "Digital framing" : "Camera zoom"}`;
  const structure = JSON.stringify(Object.entries(state.controls).map(([name, control]) => [name, control.type, control.min, control.max, control.step, control.options, control.kind]));
  if ($("control-fields").dataset.structure !== structure) {
    const groups = new Map();
    for (const [name, control] of Object.entries(state.controls)) {
      const group = CameraSettings.definitions[name]?.group || "Image";
      if (!groups.has(group)) {
        const section = document.createElement(group === "Framing" ? "section" : "details");
        section.className = "control-group";
        const heading = document.createElement(group === "Framing" ? "h2" : "summary");
        heading.textContent = group;
        section.append(heading);
        groups.set(group, section);
      }
      const row = document.createElement("div"); row.className = "control-field";
      const line = document.createElement("div"); line.className = "control-label";
      const label = document.createElement("label"); label.htmlFor = "setting-" + name; label.textContent = control.label;
      const output = document.createElement("output"); output.id = "value-" + name;
      line.append(label, output);
      const input = document.createElement(control.type === "select" ? "select" : "input");
      input.id = "setting-" + name; input.dataset.control = name;
      if (control.type === "select") input.replaceChildren(...control.options.map(value => new Option(value.replaceAll("-", " "), value)));
      else input.type = control.type === "toggle" ? "checkbox" : "range";
      if (control.type === "range") { input.min = control.min; input.max = control.max; input.step = control.step; }
      input.oninput = () => { if (control.type === "range") output.textContent = CameraSettings.formatValue(name, Number(input.value), control.unit); };
      input.onchange = () => localControl({name, value: control.type === "toggle" ? input.checked : control.type === "range" ? Number(input.value) : input.value});
      row.append(line, input);
      if (name === "zoom") {
        const stops = document.createElement("div"); stops.className = "zoom-stops";
        CameraSettings.zoomStops(control).forEach(value => { const button = document.createElement("button"); button.type = "button"; button.textContent = `${value}×`; button.dataset.zoom = value; button.onclick = () => localControl({name: "zoom", value}); stops.append(button); });
        row.append(stops);
      }
      groups.get(group).append(row);
    }
    $("control-fields").replaceChildren(...groups.values());
    $("control-fields").dataset.structure = structure;
  }
  for (const [name, control] of Object.entries(state.controls)) {
    const input = $("setting-" + name);
    if (!state.busy || document.activeElement !== input) {
      if (control.type === "toggle") input.checked = control.value;
      else input.value = control.value;
      $("value-" + name).textContent = control.type === "toggle" ? control.value ? "On" : "Off" : control.type === "select" ? "" : CameraSettings.formatValue(name, control.value, control.unit);
    }
    input.disabled = state.busy;
  }
  $("control-fields").querySelectorAll("[data-zoom]").forEach(button => {
    button.disabled = state.busy;
    button.classList.toggle("selected", Math.abs(Number(button.dataset.zoom) - state.controls.zoom.value) < 0.03);
  });
  const nativeCount = Object.values(state.controls).filter(control => control.kind === "camera").length;
  $("native-help").textContent = nativeCount ? "Native controls shown here are reported by this camera. Lens names come from Safari." : "Safari does not expose manual exposure, focus or lens zoom on this camera. Digital zoom still changes the video sent to OBS.";
}

function connectSocket(current) {
  return new Promise((resolve, reject) => {
    const address = new URL("upload", location.href);
    address.protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const connection = new WebSocket(address);
    socket = connection;
    let opened = false;
    const timeout = setTimeout(() => { connection.close(); reject(new Error("Cannot reach the PC. Check camera mode and your local network.")); }, 10000);
    connection.onopen = () => { opened = true; clearTimeout(timeout); publishState(); resolve(connection); };
    connection.onmessage = event => {
      if (generation !== current || socket !== connection) return;
      if (event.data === "ok" && ack) { clearTimeout(ackTimer); const pending = ack; ack = null; pending.resolve(); return; }
      if (typeof event.data !== "string" || event.data.length > 4096) return;
      let message;
      try { message = JSON.parse(event.data); } catch (_) { return; }
      if (message.type !== "camera-command" || typeof message.id !== "string" || message.id.length > 96) return;
      queueControl(message.command).then(state => sendJson({type: "camera-result", id: message.id, ok: true, state}))
        .catch(error => sendJson({type: "camera-result", id: message.id, ok: false, error: cameraError(error), state: running ? cameraState() : null}));
    };
    connection.onerror = () => { clearTimeout(timeout); if (!opened) reject(new Error("Cannot connect to the PC. Check Wi-Fi, certificate trust, and camera mode.")); };
    connection.onclose = () => {
      clearTimeout(timeout);
      if (!opened) reject(new Error("The PC refused the camera connection. Close any other sender and try again."));
      if (opened && generation === current && running) { stopCamera(); status("Connection ended", "The PC disconnected. Tap Start camera to reconnect.", "error"); }
    };
  });
}

function sendFrame(connection, blob) {
  return new Promise((resolve, reject) => {
    if (connection.readyState !== WebSocket.OPEN) { reject(new Error("The connection to the PC closed.")); return; }
    ack = {resolve, reject};
    ackTimer = setTimeout(() => { ack = null; reject(new Error("Your PC stopped receiving video. Check the connection and try again.")); }, 5000);
    try { connection.send(blob); } catch (error) { clearTimeout(ackTimer); ack = null; reject(error); }
  });
}

function delay(milliseconds) {
  return new Promise(resolve => { resumeFrame = resolve; frameTimer = setTimeout(() => { resumeFrame = null; resolve(); }, milliseconds); });
}

async function capture(current, connection) {
  let firstFrame = true, dimensions = "";
  try {
    while (generation === current && running) {
      const started = performance.now();
      if (!switching && video.readyState >= 2 && video.videoWidth && video.videoHeight) {
        const scale = Math.min(1, 1280 / Math.max(video.videoWidth, video.videoHeight));
        const width = Math.max(1, Math.round(video.videoWidth * scale)), height = Math.max(1, Math.round(video.videoHeight * scale));
        if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
        const size = `${video.videoWidth}/${video.videoHeight}`;
        if (dimensions !== size) { previewAspect = video.videoWidth / video.videoHeight; fitPreview(); dimensions = size; publishState(); }
        const cropWidth = video.videoWidth / digitalZoom, cropHeight = video.videoHeight / digitalZoom;
        context.drawImage(video, (video.videoWidth - cropWidth) / 2, (video.videoHeight - cropHeight) / 2, cropWidth, cropHeight, 0, 0, width, height);
        let blob = await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", 0.8));
        if (generation !== current || !running) break;
        if (blob?.size > MAX_FRAME_BYTES) blob = await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", 0.5));
        if (generation !== current || !running) break;
        if (blob && blob.size <= MAX_FRAME_BYTES) {
          await sendFrame(connection, blob);
          if (generation !== current || !running) break;
          if (firstFrame) { firstFrame = false; status("Camera live", "Adjust the camera here or from your PC. Keep Safari open.", "live"); }
        }
      }
      if (generation === current && running) await delay(Math.max(1, FRAME_INTERVAL - (performance.now() - started)));
    }
  } catch (error) { if (generation === current) { stopCamera(); status("Camera stopped", cameraError(error), "error"); } }
}

async function startCamera() {
  const selected = $("camera").value;
  stopCamera();
  const current = generation;
  running = true;
  controls(true, true);
  status("Opening camera", "Allow camera access in Safari. Your microphone stays off.", "busy");
  try {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw new Error("Secure camera access unavailable.");
    const choice = selected.startsWith("device:") ? {deviceId: selected.slice(7)} : {facingMode: selected.replace("facing:", "") || "environment"};
    await attachCamera(choice, current);
    if (generation !== current) return;
    status("Connecting", "Waiting for your PC to receive video…", "busy");
    const connection = await connectSocket(current);
    if (generation !== current) { connection.close(); return; }
    controls(true);
    publishState();
    if (navigator.wakeLock?.request) navigator.wakeLock.request("screen").then(lock => { if (generation === current) wakeLock = lock; else lock.release().catch(() => {}); }).catch(() => {});
    capture(current, connection);
  } catch (error) {
    if (generation !== current) return;
    stopCamera();
    if (error.name === "NotAllowedError") $("certificate-help").hidden = false;
    status("Camera could not start", cameraError(error), "error");
  }
}

$("start").onclick = startCamera;
$("stop").onclick = () => { stopCamera(); status("Camera stopped", "Tap Start camera whenever you are ready."); };
$("camera").onchange = () => {
  if (!running) return;
  const value = $("camera").value;
  localControl(value.startsWith("device:") ? {name: "deviceId", value: value.slice(7)} : {name: "facingMode", value: value.replace("facing:", "")});
};
$("reset-camera").onclick = () => localControl({name: "reset"});
if (window.ResizeObserver) new ResizeObserver(fitPreview).observe($("preview-area"));
else window.addEventListener("resize", fitPreview);
fitPreview();
if (navigator.mediaDevices?.addEventListener) navigator.mediaDevices.addEventListener("devicechange", async () => { if (running) { await refreshDevices(); publishState(); } });
window.addEventListener("pagehide", stopCamera);
document.addEventListener("visibilitychange", () => {
  if (document.hidden && running) { stopCamera(); status("Camera paused", "Keep Safari visible, then tap Start camera to resume."); }
});
if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) status("Camera access needs setup", cameraError(new Error()), "error");
