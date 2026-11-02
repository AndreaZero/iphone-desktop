const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {performance} = require('node:perf_hooks');
const CameraSettings = require('../iphone_desktop/web/camera-settings.js');

function createPhone({native = false, ignoreConstraints = false} = {}) {
  const elements = new Map(), sockets = [], tracks = [], draws = [];
  class Element {
    constructor(tag = 'div') {
      this.tag = tag; this.dataset = {}; this.style = {}; this.children = [];
      this.value = ''; this.clientWidth = 640; this.clientHeight = 480;
      this.classList = {toggle() {}};
    }
    set id(value) { this._id = value; elements.set(value, this); }
    get id() { return this._id; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    querySelectorAll() { return this.children.flatMap(node => [node, ...node.querySelectorAll()]).filter(node => node.dataset.zoom !== undefined); }
    getContext() { return {drawImage(...args) { draws.push(args); }}; }
    toBlob(callback) { callback({size: 100}); }
    pause() {}
    async play() {}
  }
  const element = id => { if (!elements.has(id)) { const value = new Element(); value.id = id; } return elements.get(id); };
  element('camera').value = 'facing:environment';
  const video = element('preview'); video.readyState = 2; video.videoWidth = 1280; video.videoHeight = 720;
  const media = {
    async getUserMedia(request) {
      const deviceId = request.video.deviceId?.exact || (request.video.facingMode?.ideal === 'user' ? 'front' : 'back');
      const settings = {width: 1280, height: 720, frameRate: 20, deviceId, facingMode: deviceId === 'front' ? 'user' : 'environment', ...(native ? {zoom: 1, exposureMode: 'continuous', exposureCompensation: 0, iso: 100} : {})};
      const capabilities = native ? {zoom: {min: 1, max: 6, step: .1}, exposureMode: ['continuous', 'manual'], exposureCompensation: {min: -2, max: 2, step: .1}, iso: {min: 50, max: 1600, step: 1}} : {};
      const track = {stopped: false, requests: [], getSettings: () => ({...settings}), getCapabilities: () => capabilities,
        async applyConstraints(constraints) { this.requests.push(constraints); if (this.pendingApply) await this.pendingApply; if (!ignoreConstraints) Object.assign(settings, constraints.advanced[0]); },
        stop() { this.stopped = true; }};
      tracks.push(track);
      return {getVideoTracks: () => [track], getTracks: () => [track]};
    },
    async enumerateDevices() { return [{kind: 'videoinput', deviceId: 'back', label: 'Back camera'}, {kind: 'videoinput', deviceId: 'front', label: 'Front camera'}]; },
    addEventListener() {}
  };
  class FakeSocket {
    static OPEN = 1;
    constructor() { this.readyState = 1; this.sent = []; sockets.push(this); setImmediate(() => this.onopen?.()); }
    send(value) {
      this.sent.push(typeof value === 'string' ? JSON.parse(value) : value);
      if (typeof value !== 'string') setImmediate(() => this.onmessage?.({data: 'ok'}));
    }
    close() { this.readyState = 3; this.closed = true; }
  }
  const context = vm.createContext({console, CameraSettings, URL, performance, setTimeout, clearTimeout, WebSocket: FakeSocket,
    Option: class extends Element { constructor(text, value) { super('option'); this.textContent = text; this.value = value; } },
    location: {href: 'https://camera.test/token/send', protocol: 'https:'},
    navigator: {mediaDevices: media},
    document: {getElementById: element, createElement: tag => new Element(tag), activeElement: null, addEventListener() {}},
    window: {isSecureContext: true, ResizeObserver: class {observe() {}}, addEventListener() {}}
  });
  // The script uses the browser global alias for ResizeObserver.
  context.ResizeObserver = context.window.ResizeObserver;
  vm.runInContext(fs.readFileSync(require.resolve('../iphone_desktop/web/camera-phone.js'), 'utf8'), context);
  return {context, element, sockets, tracks, draws, run: code => vm.runInContext(code, context), stop() { vm.runInContext('stopCamera()', context); }};
}

async function until(predicate) {
  const deadline = performance.now() + 2000;
  while (!predicate()) {
    if (performance.now() > deadline) throw new Error('Camera runtime did not produce the expected result');
    await new Promise(resolve => setTimeout(resolve, 5));
  }
}

test('remote camera switching preserves the upload socket and reports the actual selected device', async () => {
  const phone = createPhone();
  try {
    await phone.run('startCamera()');
    assert.equal(phone.sockets.length, 1);
    const socket = phone.sockets[0];
    socket.onmessage({data: JSON.stringify({type: 'camera-command', id: 'switch', command: {name: 'deviceId', value: 'front'}})});
    await until(() => socket.sent.some(message => message.type === 'camera-result' && message.id === 'switch'));
    const result = socket.sent.find(message => message.type === 'camera-result');
    assert.equal(result.ok, true);
    assert.equal(result.state.selectedDevice, 'front');
    assert.equal(phone.sockets.length, 1);
    assert.equal(socket.closed, undefined);
    assert.equal(phone.tracks[0].stopped, true);
    assert.equal(phone.tracks[1].stopped, false);
  } finally { phone.stop(); }
});

test('digital zoom crops the uploaded frame and matches the local preview without reopening the camera', async () => {
  const phone = createPhone();
  try {
    await phone.run('startCamera()');
    await phone.run('queueControl({name: "zoom", value: 2})');
    await until(() => phone.draws.some(args => args[3] === 640 && args[4] === 360));
    const crop = phone.draws.find(args => args[3] === 640 && args[4] === 360);
    assert.deepEqual(crop.slice(1, 5), [320, 180, 640, 360]);
    assert.equal(phone.element('preview').style.transform, 'scale(2, 2)');
    assert.equal(phone.tracks.length, 1);
    assert.equal(phone.sockets.length, 1);
  } finally { phone.stop(); }
});

test('native controls use manual mode where required and reject a silently ignored setting', async () => {
  const phone = createPhone({native: true});
  try {
    await phone.run('startCamera()');
    await phone.run('queueControl({name: "iso", value: 400})');
    assert.deepEqual(JSON.parse(JSON.stringify(phone.tracks[0].requests[0].advanced)), [{iso: 400, exposureMode: 'manual'}]);
    assert.equal(phone.tracks[0].requests[0].frameRate.max, 30);
    assert.equal(phone.run('cameraState().settings.iso'), 400);
  } finally { phone.stop(); }
  const ignored = createPhone({native: true, ignoreConstraints: true});
  try {
    await ignored.run('startCamera()');
    await assert.rejects(ignored.run('queueControl({name: "zoom", value: 3})'), /different value/);
    assert.equal(ignored.run('cameraState().controls.zoom.value'), 1);
    assert.equal(ignored.sockets[0].closed, undefined);
  } finally { ignored.stop(); }
});

test('a late reset from a stopped camera cannot clear controls in the restarted session', async () => {
  const phone = createPhone({native: true});
  let release;
  try {
    await phone.run('startCamera()');
    const original = phone.tracks[0];
    original.pendingApply = new Promise(resolve => { release = resolve; });
    const oldReset = phone.run('queueControl({name: "reset"})');
    // Attach a rejection handler before releasing the deliberately delayed work.
    const rejected = assert.rejects(oldReset, /Camera stopped/);
    await until(() => original.requests.length === 1);
    phone.stop();
    await phone.run('startCamera()');
    await phone.run('queueControl({name: "zoom", value: 3})');
    await phone.run('queueControl({name: "iso", value: 400})');
    const expected = phone.run('JSON.stringify(appliedNative)');
    release();
    await rejected;
    assert.equal(phone.run('JSON.stringify(appliedNative)'), expected);
    assert.equal(phone.run('cameraState().controls.zoom.value'), 3);
    assert.equal(phone.run('cameraState().settings.iso'), 400);
    assert.equal(phone.sockets[1].closed, undefined);
  } finally { release?.(); phone.stop(); }
});
