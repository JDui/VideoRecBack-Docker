export function createPlayerAudio(video, AudioContextClass = window.AudioContext || window.webkitAudioContext) {
  let context = null;
  let gain = null;
  let multiplier = 1;
  let unavailable = false;

  const resume = () => {
    if (context && context.state !== "running" && context.state !== "closed") {
      return context.resume().catch(() => false);
    }
    return Promise.resolve(true);
  };
  const enableBoost = () => {
    if (gain) return true;
    if (unavailable || !AudioContextClass) return false;
    try {
      context = new AudioContextClass();
      gain = context.createGain();
      gain.connect(context.destination);
      context.createMediaElementSource(video).connect(gain);
      return true;
    } catch {
      unavailable = true;
      gain = null;
      context?.close().catch(() => {});
      context = null;
      return false;
    }
  };
  return {
    getVolume: () => video.volume * multiplier,
    setVolume(value) {
      let next = Math.max(0, Math.min(1.5, Number.isFinite(value) ? value : 0));
      if (next > 1 && !enableBoost()) next = 1;
      multiplier = Math.max(1, next);
      if (gain) gain.gain.value = multiplier;
      video.volume = Math.min(1, next);
      video.muted = next === 0;
      void resume();
      return next;
    },
    resume,
    suspend: () => context?.state === "running" ? context.suspend().catch(() => {}) : Promise.resolve(),
    close: () => context && context.state !== "closed" ? context.close().catch(() => {}) : Promise.resolve(),
  };
}
