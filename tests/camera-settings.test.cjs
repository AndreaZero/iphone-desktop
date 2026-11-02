const test = require('node:test');
const assert = require('node:assert/strict');
const settings = require('../iphone_desktop/web/camera-settings.js');

test('unexposed image capabilities remain absent and zoom has an explicit digital fallback', () => {
  const state = settings.buildState({}, {width: 1280, height: 720}, [], {digitalZoom: 2});
  assert.deepEqual(Object.keys(state.controls), ['zoom']);
  assert.equal(state.controls.zoom.kind, 'digital');
  assert.equal(state.controls.zoom.value, 2);
  assert.equal(settings.constraintFor(state.controls, 'zoom', 3).constraints, null);
});

test('native camera range bounds and steps control the requested value', () => {
  const state = settings.buildState({zoom: {min: 0.5, max: 6, step: 0.25}, exposureCompensation: {min: -2, max: 2, step: 0.5}}, {zoom: 1, exposureCompensation: 0});
  assert.equal(state.controls.zoom.kind, 'camera');
  assert.equal(settings.normalizeValue(state.controls.zoom, 20), 6);
  assert.equal(settings.normalizeValue(state.controls.exposureCompensation, -1.3), -1.5);
  assert.throws(() => settings.normalizeValue(state.controls.zoom, NaN));
  assert.throws(() => settings.normalizeValue(state.controls.zoom, '2'));
});

test('manual controls select the correct native metering mode', () => {
  const controls = settings.buildState({exposureMode: ['continuous', 'manual'], iso: {min: 50, max: 1600, step: 1}, focusMode: ['continuous', 'manual'], focusDistance: {min: 0, max: 10, step: 0.1}}, {exposureMode: 'continuous', iso: 100, focusMode: 'continuous', focusDistance: 1}).controls;
  assert.deepEqual(settings.constraintFor(controls, 'iso', 200).constraints, {advanced: [{iso: 200, exposureMode: 'manual'}]});
  assert.deepEqual(settings.constraintFor(controls, 'focusDistance', 2).constraints, {advanced: [{focusDistance: 2, focusMode: 'manual'}]});
  assert.throws(() => settings.constraintFor(controls, 'focusMode', 'invented'));
});

test('torch is displayed only when both on and off can be controlled', () => {
  assert.equal(settings.buildState({torch: [false]}, {}).controls.torch, undefined);
  assert.equal(settings.buildState({torch: [true]}, {}).controls.torch, undefined);
  const control = settings.buildState({torch: [true, false]}, {torch: true}).controls.torch;
  assert.equal(control.value, true);
  assert.throws(() => settings.normalizeValue(control, 'true'));
});

test('WebKit boolean torch capability exposes the readable flashlight switch', () => {
  assert.equal(settings.buildState({torch: true}, {torch: false}).controls.torch.value, false);
  assert.equal(settings.buildState({torch: false}, {torch: false}).controls.torch, undefined);
});

test('invalid ranges are not offered as camera controls', () => {
  const state = settings.buildState({zoom: {}, exposureTime: {min: 1, max: Infinity}, iso: {min: 100, max: 100}}, {});
  assert.deepEqual(Object.keys(state.controls), ['zoom']);
  assert.equal(state.controls.zoom.kind, 'digital');
});

test('capabilities without readable settings do not pretend to expose working native adjustments', () => {
  const state = settings.buildState({zoom: {min: 1, max: 5}, exposureMode: ['continuous', 'manual'], iso: {min: 50, max: 1600}, torch: [true, false]}, {});
  assert.deepEqual(Object.keys(state.controls), ['zoom']);
  assert.equal(state.controls.zoom.kind, 'digital');
});

test('shutter display uses the specified one hundred microsecond units', () => {
  assert.equal(settings.formatValue('exposureTime', 100), '1/100 s');
  assert.equal(settings.formatValue('exposureTime', 10000), '1 s');
  assert.equal(settings.formatValue('exposureCompensation', -0.5, 'EV'), '-0.5 EV');
});

test('device names remain the browser labels rather than invented lens multipliers', () => {
  const state = settings.buildState({}, {deviceId: 'rear-id'}, [{id: 'rear-id', label: 'Back Triple Camera'}]);
  assert.equal(state.selectedDevice, 'rear-id');
  assert.equal(state.devices[0].label, 'Back Triple Camera');
  assert.deepEqual(settings.zoomStops({min: 1.5, max: 3}), [1.5, 2, 3]);
});
