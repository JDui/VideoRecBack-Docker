const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const source = readFileSync(resolve(__dirname, '../app/static/player-audio.js'), 'utf8');
const modulePromise = import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

function fixture() {
  const instances = [];
  class AudioContext {
    constructor() { this.state = 'suspended'; this.destination = {}; this.sources = 0; instances.push(this); }
    createGain() {
      this.gainNode = { gain: { value: 1 }, connect: destination => { this.output = destination; } };
      return this.gainNode;
    }
    createMediaElementSource(video) {
      this.sources++;
      this.video = video;
      return { connect: target => { this.target = target; } };
    }
    async resume() { this.state = 'running'; }
    async suspend() { this.state = 'suspended'; }
    async close() { this.state = 'closed'; }
  }
  return { AudioContext, instances, video: { volume: .2, muted: false } };
}

test('normal volume avoids audio routing; boost uses one connected gain node', async () => {
  const { createPlayerAudio } = await modulePromise;
  const { AudioContext, instances, video } = fixture();
  const audio = createPlayerAudio(video, AudioContext);
  audio.setVolume(.8);
  assert.equal(instances.length, 0);
  audio.setVolume(1.5);
  assert.equal(video.volume, 1);
  assert.equal(audio.getVolume(), 1.5);
  const context = instances[0];
  assert.equal(context.video, video);
  assert.equal(context.target, context.gainNode);
  assert.equal(context.output, context.destination);
  assert.equal(context.gainNode.gain.value, 1.5);
  audio.setVolume(1.2);
  assert.equal(instances.length, 1);
  assert.equal(context.sources, 1);
  assert.equal(context.gainNode.gain.value, 1.2);
  audio.setVolume(.4);
  assert.equal(context.gainNode.gain.value, 1);
  assert.equal(audio.getVolume(), .4);
});

test('mute preserves boosted level; zero and invalid values mute safely', async () => {
  const { createPlayerAudio } = await modulePromise;
  const { AudioContext, video } = fixture();
  const audio = createPlayerAudio(video, AudioContext);
  audio.setVolume(1.5);
  video.muted = true;
  assert.equal(audio.getVolume(), 1.5);
  audio.setVolume(audio.getVolume());
  assert.equal(video.muted, false);
  audio.setVolume(3);
  assert.equal(audio.getVolume(), 1.5);
  audio.setVolume(0);
  assert.equal(video.muted, true);
  audio.setVolume(Number.NaN);
  assert.equal(audio.getVolume(), 0);
});

test('unsupported audio enhancement falls back to native 100%', async () => {
  const { createPlayerAudio } = await modulePromise;
  for (const AudioContext of [null, class { constructor() { throw new Error('Unavailable'); } }]) {
    const video = { volume: .2, muted: false };
    const audio = createPlayerAudio(video, AudioContext);
    assert.equal(audio.setVolume(1.5), 1);
    assert.equal(audio.getVolume(), 1);
    audio.setVolume(.3);
    assert.equal(video.volume, .3);
  }
});

test('audio routing resumes after suspension and closes on teardown', async () => {
  const { createPlayerAudio } = await modulePromise;
  const { AudioContext, instances, video } = fixture();
  const audio = createPlayerAudio(video, AudioContext);
  audio.setVolume(1.5);
  const context = instances[0];
  await audio.suspend();
  assert.equal(context.state, 'suspended');
  await audio.resume();
  assert.equal(context.state, 'running');
  context.resume = () => Promise.reject(new Error('Gesture required'));
  context.state = 'suspended';
  assert.equal(await audio.resume(), false);
  await audio.close();
  assert.equal(context.state, 'closed');
  await audio.resume();
});
