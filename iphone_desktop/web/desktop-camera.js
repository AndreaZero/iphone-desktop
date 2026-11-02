(function () {
  "use strict";

  const groups = [
    {title: "Lens & framing", names: ["zoom"], open: true},
    {title: "Exposure", names: ["exposureMode", "exposureCompensation", "exposureTime", "iso"]},
    {title: "Focus", names: ["focusMode", "focusDistance"]},
    {title: "Color & image", names: ["whiteBalanceMode", "colorTemperature", "brightness", "contrast", "saturation", "sharpness"]},
    {title: "Light", names: ["torch"]},
  ];
  const modes = {continuous: "Auto", manual: "Manual", "single-shot": "Single shot", none: "Off"};
  const labels = {zoom: "Zoom", exposureMode: "Exposure", exposureCompensation: "Exposure compensation", exposureTime: "Shutter", iso: "ISO", focusMode: "Focus", focusDistance: "Focus distance", whiteBalanceMode: "White balance", colorTemperature: "Temperature", brightness: "Brightness", contrast: "Contrast", saturation: "Saturation", sharpness: "Sharpness", torch: "Torch"};

  function element(tag, className, text) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== undefined) result.textContent = text;
    return result;
  }

  function formatted(name, value, control) {
    if (typeof value !== "number") return modes[value] || String(value ?? "");
    if (name === "exposureTime") {
      const seconds = value / 10000;
      return seconds > 0 && seconds < 1 ? `1/${Math.round(1 / seconds)} s` : `${Number(seconds.toFixed(3))} s`;
    }
    if (name === "zoom") return `${Number(value.toFixed(2))}×`;
    if (name === "exposureCompensation") return `${value > 0 ? "+" : ""}${Number(value.toFixed(2))} EV`;
    const number = Number(value.toFixed(3));
    return `${number}${control.unit ? " " + control.unit : ""}`;
  }

  class DesktopCameraControls {
    constructor(content, status, send) {
      this.content = content;
      this.status = status;
      this.send = send;
      this.state = null;
      this.schema = null;
      this.fields = new Map();
      this.pending = new Map();
      this.sending = null;
      this.timer = null;
      this.generation = 0;
      this.errorUntil = 0;
    }

    reset() {
      this.generation++;
      clearTimeout(this.timer);
      this.pending.clear();
      this.sending = null;
      this.schema = null;
      this.state = null;
      this.fields.clear();
      this.content.replaceChildren();
      this.errorUntil = 0;
    }

    render(state, connected, force = false) {
      this.connected = connected;
      this.state = state;
      if (!state) {
        if (this.schema !== null) this.reset();
        this.status.textContent = connected ? "Camera connected. Open the updated camera page in Safari to enable controls." : "Start the camera in Safari to see its available controls.";
        return;
      }
      // Values change on every adjustment. Rebuild only if the available controls change.
      const definition = Object.fromEntries(Object.entries(state.controls || {}).map(([name, control]) => [name, {...control, value: undefined}]));
      const schema = JSON.stringify({devices: state.devices, controls: definition});
      if (schema !== this.schema) {
        this.schema = schema;
        this.build(state);
      }
      const busy = !connected || !!state.busy;
      for (const [name, field] of this.fields) {
        const value = name === "deviceId" ? state.selectedDevice : name === "facingMode" ? state.facingMode : state.controls?.[name]?.value;
        const dependent = {exposureTime: "exposureMode", iso: "exposureMode", focusDistance: "focusMode", colorTemperature: "whiteBalanceMode"}[name];
        const mode = dependent ? state.controls?.[dependent]?.value : null;
        const manualRequired = mode !== null && mode !== undefined && mode !== "manual";
        field.input.disabled = busy || manualRequired;
        field.wrapper.classList.toggle("unavailable", manualRequired);
        if (manualRequired) field.wrapper.title = "Choose Manual in this section to adjust this setting.";
        else field.wrapper.removeAttribute("title");
        if (force || (!this.pending.has(name) && this.sending?.name !== name && document.activeElement !== field.input)) {
          if (field.input.type === "checkbox") field.input.checked = !!value;
          else if (value !== undefined) field.input.value = value;
          if (field.output) field.output.textContent = formatted(name, value, field.control);
          if (field.chips) for (const chip of field.chips.children) chip.classList.toggle("selected", Number(chip.dataset.value) === Number(value));
        }
        if (field.chips) for (const chip of field.chips.children) chip.disabled = field.input.disabled;
      }
      if (this.resetButton) this.resetButton.disabled = busy || !!this.sending || this.pending.size > 0;
      if (performance.now() >= this.errorUntil) {
        this.status.classList.remove("error");
        this.status.textContent = !connected ? "Camera disconnected. Keep Safari open to reconnect." : state.busy ? "Updating camera…" : this.sending || this.pending.size ? "Applying camera settings…" : "Changes apply to the live video and OBS.";
      }
    }

    build(state) {
      this.fields.clear();
      this.content.replaceChildren();
      for (const group of groups) {
        const names = group.names.filter(name => state.controls?.[name]);
        const hasDevices = group.open && state.devices?.length > 0;
        const hasFacing = group.open && !hasDevices && ["user", "environment"].includes(state.facingMode);
        if (!names.length && !hasDevices && !hasFacing) continue;
        const details = element("details", "camera-control-group");
        details.open = !!group.open;
        details.append(element("summary", "", group.title));
        if (hasDevices) {
          const control = {type: "select", label: "Camera / lens", value: state.selectedDevice, options: state.devices.map(device => device.id)};
          this.addField(details, "deviceId", control, new Map(state.devices.map(device => [device.id, device.label])));
        }
        if (hasFacing) this.addField(details, "facingMode", {type: "select", label: "Camera", value: state.facingMode, options: ["environment", "user"]}, new Map([["environment", "Back camera"], ["user", "Front camera"]]));
        for (const name of names) this.addField(details, name, state.controls[name]);
        this.content.append(details);
      }
      const hint = element("p", "camera-control-hint", "Available settings depend on the selected camera and Safari. Unavailable manual settings remain automatic.");
      this.resetButton = element("button", "camera-control-reset", "Reset camera settings");
      this.resetButton.type = "button";
      this.resetButton.onclick = () => this.queue("reset", null);
      this.content.append(hint, this.resetButton);
    }

    addField(parent, name, control, optionLabels = null) {
      const wrapper = element("div", "camera-control-field");
      const row = element("div", "camera-control-row");
      const label = element("label", "", control.label || labels[name] || name);
      const id = `desktop-camera-${name}`;
      label.htmlFor = id;
      row.append(label);
      const input = element(control.type === "select" ? "select" : "input");
      input.id = id;
      let output = null, chips = null, bounds = null;
      if (control.type === "select") {
        input.className = "camera-control-select";
        if (name === "deviceId" && !control.options.includes(control.value)) input.add(new Option("Current camera", ""));
        for (const option of control.options || []) input.add(new Option(optionLabels?.get(option) || modes[option] || String(option), option));
        input.onchange = () => this.queue(name, input.value);
      } else if (control.type === "toggle") {
        input.type = "checkbox";
        input.className = "camera-control-toggle";
        input.onchange = () => this.queue(name, input.checked);
        row.append(input);
      } else {
        input.type = "range";
        input.className = "camera-control-input";
        input.min = control.min;
        input.max = control.max;
        input.step = control.step || "any";
        output = element("output", "", formatted(name, control.value, control));
        output.htmlFor = id;
        row.append(output);
        input.oninput = () => {
          output.textContent = formatted(name, Number(input.value), control);
          this.queue(name, Number(input.value), 110);
        };
        input.onchange = () => this.queue(name, Number(input.value));
        bounds = element("div", "camera-control-bounds");
        bounds.append(element("span", "", formatted(name, control.min, control)), element("span", "", formatted(name, control.max, control)));
        if (name === "zoom") {
          chips = element("div", "camera-control-chips");
          const values = [...new Set([control.min, 1, 2, 3, 4, control.max].filter(value => value >= control.min && value <= control.max).map(value => {
            const step = control.step || .01;
            return Number((control.min + Math.round((value - control.min) / step) * step).toFixed(6));
          }))];
          for (const value of values) {
            const chip = element("button", "", `${Number(value.toFixed(2))}×`);
            chip.type = "button";
            chip.dataset.value = value;
            chip.onclick = () => { input.value = value; output.textContent = formatted(name, value, control); this.queue(name, value); };
            chips.append(chip);
          }
        }
      }
      wrapper.append(row);
      if (control.type !== "toggle") wrapper.append(input);
      if (bounds) wrapper.append(bounds);
      if (chips) wrapper.append(chips);
      if (name === "zoom" && control.kind === "digital") wrapper.append(element("p", "camera-control-hint", "Digital crop · higher zoom reduces image detail."));
      parent.append(wrapper);
      this.fields.set(name, {input, output, control, wrapper, chips});
    }

    queue(name, value, debounce = 0) {
      if (!this.connected || this.state?.busy) return;
      if (name === "reset") this.pending.clear();
      this.pending.set(name, value);
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.flush(), debounce);
      this.render(this.state, this.connected);
    }

    async flush() {
      if (this.sending || !this.pending.size || !this.connected) return;
      const [name, value] = this.pending.entries().next().value;
      this.pending.delete(name);
      const generation = this.generation;
      const operation = {name, value};
      this.sending = operation;
      try {
        const state = await this.send({name, value});
        if (generation === this.generation) {
          this.errorUntil = 0;
          this.render(state, this.connected);
        }
      } catch (error) {
        if (generation === this.generation) {
          this.pending.clear();
          this.errorUntil = performance.now() + 6000;
          this.status.textContent = error.message;
          this.status.classList.add("error");
          this.render(this.state, this.connected, true);
        }
      } finally {
        if (this.sending === operation) this.sending = null;
        if (generation === this.generation) {
          this.render(this.state, this.connected);
          if (this.pending.size) this.flush();
        }
      }
    }
  }

  window.DesktopCameraControls = DesktopCameraControls;
})();
