"use strict";
const token = document.querySelector('meta[name="app-token"]').content;
const $ = id => document.getElementById(id);
let transport = "usb", snapshot = null, viewerUrl = null, toastTimer;
const busyStates = new Set(["connecting", "preparing"]);

function notify(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 6500);
}

async function api(path, options = {}) {
  const response = await fetch(path, {...options, headers: {"X-App-Token": token, "Content-Type": "application/json", ...options.headers}});
  if (!response.ok) throw new Error(response.status === 409 ? "An operation is already in progress." : "Operation failed. Try again or check the log.");
  return response.json();
}

async function action(name) {
  try {
    await api("/api/action", {method: "POST", body: JSON.stringify({action: name, device: $("device").value || null, transport, wifi_host: name === "wifi" ? $("wifi-ip").value.trim() || null : null})});
    if (name === "quit") {
      $("empty-message").textContent = "Application stopped. You can close this window.";
      $("empty").hidden = false;
      $("viewer").removeAttribute("src");
      $("viewer").hidden = true;
      window.close();
      return;
    }
    await update();
  } catch (error) { notify(error.message); }
}

function render(data) {
  snapshot = data;
  const connected = data.state === "connected";
  const busy = data.busy || busyStates.has(data.state);
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
  $("device").disabled = busy || connected;
  $("connect").disabled = !selected || busy || connected;
  $("connect").hidden = connected;
  $("scan").disabled = busy || connected;
  $("disconnect").hidden = !busy && !connected;
  $("disconnect").textContent = busy ? "Cancel" : "Disconnect";
  for (const name of ["prepare", "reveal", "setup-wifi"]) $(name).disabled = !selected || transport !== "usb" || busy || connected;
  $("setup-wifi").hidden = transport !== "usb";
  $("wifi-config").hidden = transport !== "usb";
  $("wifi-ip").disabled = busy || connected;
  if ($("wifi-ip").dataset.device !== (selected?.id || "")) {
    $("wifi-ip").value = selected?.wifi_host || "";
    $("wifi-ip").dataset.device = selected?.id || "";
  }
  $("usb").disabled = busy || connected;
  $("wifi").disabled = busy || connected;
  $("status-card").className = `status-card ${data.state}`;
  $("status-title").textContent = data.problem ? data.problem.title : ({idle: "Ready to start", connecting: "Connecting", preparing: "Preparing", connected: "Session active", error: "Check the connection"}[data.state] || "iPhone Desktop");
  $("status-text").textContent = data.problem ? data.problem.detail : data.message;
  $("preview-dot").classList.toggle("live", connected);
  $("preview-title").textContent = connected && selected ? selected.name : "Your screen, right here.";
  $("footer-status").textContent = connected ? "Local session · click and drag on the screen" : busy ? "Preparing connection…" : "Waiting for a device";
  $("version").textContent = `v${data.version} · preview`;
  $("fullscreen").hidden = !connected;
  $("empty").hidden = connected;
  $("viewer").hidden = !connected;
  if (connected && data.viewer !== viewerUrl) { viewerUrl = data.viewer; $("viewer").src = viewerUrl; $("setup").open = false; }
  if (!connected && viewerUrl) { viewerUrl = null; $("viewer").removeAttribute("src"); }
  if ($("log-dialog").open) renderLog();
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
  catch (error) { if (++failures === 3) { $("status-title").textContent = "Application unavailable"; $("status-text").textContent = "Restart iPhone Desktop from the application folder."; } }
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
$("disconnect").onclick = () => action("disconnect");
$("prepare").onclick = () => action("prepare");
$("reveal").onclick = () => action("reveal");
$("setup-wifi").onclick = () => action("wifi");
$("quit").onclick = () => action("quit");
$("show-log").onclick = () => { renderLog(); $("log-dialog").showModal(); };
$("close-log").onclick = () => $("log-dialog").close();
$("fullscreen").onclick = () => $("viewer").requestFullscreen().catch(() => notify("Could not enter full screen."));
update().then(() => action("scan"));
setInterval(update, 1200);
