(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.CameraSettings = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const definitions = {
    zoom: {label: "Zoom", unit: "×", group: "Framing"},
    exposureMode: {label: "Exposure mode", group: "Exposure"},
    exposureCompensation: {label: "Exposure compensation", unit: "EV", group: "Exposure"},
    exposureTime: {label: "Shutter", unit: "100 µs", group: "Exposure"},
    iso: {label: "ISO", group: "Exposure"},
    focusMode: {label: "Focus mode", group: "Focus & color"},
    focusDistance: {label: "Focus distance", group: "Focus & color"},
    whiteBalanceMode: {label: "White balance", group: "Focus & color"},
    colorTemperature: {label: "Temperature", unit: "K", group: "Focus & color"},
    torch: {label: "Torch", group: "Framing"},
    brightness: {label: "Brightness", group: "Image"},
    contrast: {label: "Contrast", group: "Image"},
    saturation: {label: "Saturation", group: "Image"},
    sharpness: {label: "Sharpness", group: "Image"}
  };
  const finite = value => typeof value === "number" && Number.isFinite(value);
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

  function normalizeValue(control, value) {
    if (!control) throw new Error("This control is not available on this camera.");
    if (control.type === "range") {
      if (!finite(value)) throw new Error("Enter a valid camera value.");
      const bounded = clamp(value, control.min, control.max);
      return clamp(Number((control.min + Math.round((bounded - control.min) / control.step) * control.step).toPrecision(12)), control.min, control.max);
    }
    if (control.type === "select") {
      if (typeof value !== "string" || !control.options.includes(value)) throw new Error("That camera mode is not available.");
      return value;
    }
    if (control.type === "toggle" && typeof value === "boolean") return value;
    throw new Error("Enter a valid camera value.");
  }

  function buildState(capabilities = {}, settings = {}, devices = [], options = {}) {
    const controls = {};
    for (const [name, definition] of Object.entries(definitions)) {
      const capability = capabilities[name];
      const meta = {label: definition.label, unit: definition.unit || "", kind: "camera"};
      if (["exposureMode", "focusMode", "whiteBalanceMode"].includes(name)) {
        const values = Array.isArray(capability) ? [...new Set(capability.filter(value => typeof value === "string" && value.length <= 64))].slice(0, 16) : [];
        if (values.length > 1 && values.includes(settings[name])) controls[name] = {...meta, type: "select", options: values, value: settings[name]};
      } else if (name === "torch") {
        // WebKit exposes torch as a boolean capability; the W3C draft uses a
        // sequence of both accepted states. Support both reported shapes.
        const adjustable = capability === true || (Array.isArray(capability) && capability.includes(true) && capability.includes(false));
        if (adjustable && typeof settings.torch === "boolean") controls[name] = {...meta, type: "toggle", value: settings.torch};
      } else if (capability && finite(capability.min) && finite(capability.max) && capability.max > capability.min && finite(settings[name])) {
        const range = {...meta, type: "range", min: capability.min, max: capability.max,
          step: finite(capability.step) && capability.step > 0 ? capability.step : Math.max((capability.max - capability.min) / 100, 0.001)};
        range.value = normalizeValue(range, finite(settings[name]) ? settings[name] : clamp(name === "zoom" ? 1 : 0, range.min, range.max));
        controls[name] = range;
      }
    }
    if (!controls.zoom) controls.zoom = {type: "range", label: "Digital zoom", min: 1, max: 4, step: 0.05,
      value: clamp(finite(options.digitalZoom) ? options.digitalZoom : 1, 1, 4), kind: "digital", unit: "×"};
    const current = {};
    for (const name of [...Object.keys(definitions), "width", "height", "frameRate", "deviceId", "facingMode"]) {
      if (finite(settings[name]) || typeof settings[name] === "boolean" || (typeof settings[name] === "string" && settings[name].length <= 256)) current[name] = settings[name];
    }
    return {devices: devices.filter(device => device && typeof device.id === "string" && device.id).slice(0, 16)
      .map((device, index) => ({id: device.id.slice(0, 256), label: (device.label || `Camera ${index + 1}`).slice(0, 96)})),
      selectedDevice: typeof settings.deviceId === "string" ? settings.deviceId.slice(0, 256) : "",
      facingMode: typeof settings.facingMode === "string" ? settings.facingMode : options.facingMode || "environment",
      width: finite(settings.width) ? settings.width : 0, height: finite(settings.height) ? settings.height : 0,
      settings: current, controls, busy: options.busy === true};
  }

  function constraintFor(controls, name, value) {
    const control = controls[name];
    const normalized = normalizeValue(control, value);
    if (control.kind === "digital") return {value: normalized, constraints: null};
    const values = {[name]: normalized};
    const mode = {exposureTime: "exposureMode", iso: "exposureMode", focusDistance: "focusMode", colorTemperature: "whiteBalanceMode"}[name];
    if (mode && controls[mode]?.options?.includes("manual")) values[mode] = "manual";
    if (name === "exposureCompensation" && controls.exposureMode?.options?.includes("continuous")) values.exposureMode = "continuous";
    return {value: normalized, constraints: {advanced: [values]}};
  }

  function formatValue(name, value, unit = "") {
    if (!finite(value)) return String(value);
    if (name === "exposureTime" && value > 0) {
      const seconds = value / 10000;
      return seconds < 1 ? `1/${Math.round(1 / seconds)} s` : `${Number(seconds.toFixed(2))} s`;
    }
    return `${Number(value.toFixed(2))}${unit ? " " + unit : ""}`;
  }

  function zoomStops(control) {
    return [...new Set([control.min, 1, 2, 3, 4, control.max].filter(value => value >= control.min && value <= control.max)
      .map(value => Number(value.toFixed(2))))].slice(0, 6);
  }

  return {definitions, normalizeValue, buildState, constraintFor, formatValue, zoomStops};
});
