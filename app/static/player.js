const video = document.getElementById("videoPlayer");
const shell = document.querySelector(".player-shell");
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
let volumeControl = null;
let volumeValue = null;
let exposureControl = null;
let exposureValue = null;
let exposure = 1;
let mediaTimeOffset = 0;
let applyQualityAt = null;
let hlsPlayer = null;
const qualityLabels = {
  original: "原画",
  low: "高清",
  high: "流畅",
};
const totalDuration = Number(shell?.dataset.duration || 0);
const transcodeOverlay = document.querySelector("[data-transcode-overlay]");
const seekControl = document.querySelector("[data-seek-control]");
const timeValue = document.querySelector("[data-time-value]");
const flatPlayButton = document.querySelector("[data-flat-play]");
const centerAction = document.querySelector("[data-player-center-action]");

const syncVolumeUi = () => {
  if (!video || !volumeControl) return;
  const percent = String(Math.round(video.volume * 100));
  volumeControl.value = percent;
  if (volumeValue) volumeValue.textContent = `${percent}%`;
  video.dataset.appliedVolume = String(video.volume);
};

const setVideoVolume = (nextVolume) => {
  if (!video) return;
  video.volume = clamp(nextVolume, 0, 1);
  syncVolumeUi();
};

const togglePlayback = () => {
  if (!video) return;
  if (video.paused) {
    video.play().catch(() => {});
  } else {
    video.pause();
  }
};

const seekBy = (seconds) => {
  if (!video) return;
  setLogicalCurrentTime(getLogicalCurrentTime() + seconds);
};

const getLogicalDuration = () => {
  if (Number.isFinite(totalDuration) && totalDuration > 0) return totalDuration;
  if (Number.isFinite(video?.duration)) return mediaTimeOffset + video.duration;
  return Number.POSITIVE_INFINITY;
};

const getLogicalCurrentTime = () => {
  if (!video || !Number.isFinite(video.currentTime)) return mediaTimeOffset;
  return mediaTimeOffset + video.currentTime;
};

const setLogicalCurrentTime = (time) => {
  if (!video) return;
  const duration = getLogicalDuration();
  const nextTime = clamp(time, 0, Number.isFinite(duration) ? duration : time);
  const currentQuality = video.dataset.currentQuality || "original";
  const relativeTime = nextTime - mediaTimeOffset;
  if (currentQuality !== "original" && (relativeTime < 0 || relativeTime > Math.max(0, video.duration || 0))) {
    applyQualityAt?.(currentQuality, nextTime, !video.paused);
    return;
  }
  video.currentTime = clamp(relativeTime, 0, Math.max(0, video.duration || 0));
};

const showTranscodeOverlay = (message = "正在切换转码中...") => {
  if (!transcodeOverlay) return;
  transcodeOverlay.textContent = message;
  transcodeOverlay.hidden = false;
};

const hideTranscodeOverlay = () => {
  if (transcodeOverlay) transcodeOverlay.hidden = true;
};

const formatClock = (seconds) => {
  if (!Number.isFinite(seconds) || seconds < 0) return "00:00";
  const total = Math.floor(seconds);
  const minutes = Math.floor(total / 60);
  const secs = total % 60;
  return `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
};

const syncProgressUi = () => {
  const duration = getLogicalDuration();
  const progress = Number.isFinite(duration) && duration > 0 ? clamp(getLogicalCurrentTime() / duration, 0, 1) : 0;
  if (seekControl && document.activeElement !== seekControl) {
    seekControl.value = String(Math.round(progress * 1000));
  }
  if (timeValue) {
    const current = formatClock(getLogicalCurrentTime());
    const total = Number.isFinite(duration) ? formatClock(duration) : "00:00";
    timeValue.textContent = shell?.dataset.videoType === "panorama" ? current : `${current} / ${total}`;
  }
};

const syncPlayUi = () => {
  const label = video?.paused ? "播放" : "暂停";
  if (flatPlayButton) flatPlayButton.textContent = label;
  if (centerAction) {
    centerAction.textContent = label;
    centerAction.classList.toggle("is-hidden", !video?.paused);
  }
};

const syncExposureUi = () => {
  const percent = String(Math.round(exposure * 100));
  if (exposureControl) exposureControl.value = percent;
  if (exposureValue) exposureValue.textContent = `${percent}%`;
  if (video && shell?.dataset.videoType !== "panorama") {
    video.style.filter = `brightness(${exposure})`;
  }
};

const setExposure = (nextExposure) => {
  exposure = clamp(nextExposure, 0.5, 1.5);
  syncExposureUi();
};

if (video) {
  const configuredVolume = Number(shell?.dataset.defaultVolume ?? 0.2);
  video.volume = clamp(Number.isFinite(configuredVolume) ? configuredVolume : 0.2, 0, 1);
  video.dataset.appliedVolume = String(video.volume);
  volumeControl = document.querySelector("[data-volume-control]");
  volumeValue = document.querySelector("[data-volume-value]");
  if (volumeControl) {
    syncVolumeUi();
    volumeControl.addEventListener("input", () => {
      setVideoVolume(Number(volumeControl.value) / 100);
    });
  }
  exposureControl = document.querySelector("[data-exposure-control]");
  exposureValue = document.querySelector("[data-exposure-value]");
  if (exposureControl) {
    setExposure(Number(exposureControl.value) / 100);
    exposureControl.addEventListener("input", () => {
      setExposure(Number(exposureControl.value) / 100);
    });
  }

  const speedSlider = document.querySelector("[data-speed-slider]");
  const speedValue = document.querySelector("[data-speed-value]");
  const qualityButtons = [...document.querySelectorAll("[data-quality-option]")];
  const qualityValue = document.querySelector("[data-quality-value]");
  if (qualityButtons.length) {
    const qualityValues = qualityButtons.map((button) => button.dataset.qualityOption).filter((item) => item in qualityLabels);
    video.dataset.currentQuality = qualityValues[0] || "original";
    let qualitySwitchId = 0;
    const qualityUrl = (quality, startAt = 0) => {
      const base = video.dataset.mediaBase || video.getAttribute("src") || "";
      const cleanBase = base.split("#")[0];
      const time = Math.max(0, startAt).toFixed(3);
      if (quality === "original") return `${cleanBase}#t=${time}`;
      return `${cleanBase}/hls/${encodeURIComponent(quality)}/${Math.round(Math.max(0, startAt) * 1000)}/index.m3u8`;
    };
    const destroyHls = () => {
      if (!hlsPlayer) return;
      hlsPlayer.destroy();
      hlsPlayer = null;
    };
    const canUseNativeHls = () => {
      return Boolean(
        video.canPlayType("application/vnd.apple.mpegurl") ||
        video.canPlayType("application/x-mpegURL")
      );
    };
    const syncQualityUi = (quality = video.dataset.currentQuality || "original") => {
      for (const button of qualityButtons) {
        button.classList.toggle("active", button.dataset.qualityOption === quality);
      }
      if (qualityValue) qualityValue.textContent = qualityLabels[quality] || quality;
      return quality;
    };
    const applyQuality = (quality) => {
      syncQualityUi(quality);
      if (video.dataset.currentQuality === quality) return;
      applyQualityAt(quality, getLogicalCurrentTime(), false);
    };

    applyQualityAt = (quality, resumeAt, forcePlay = false) => {
      const wasPaused = video.paused;
      const playbackRate = video.playbackRate;
      const volume = video.volume;
      const switchId = ++qualitySwitchId;
      const shouldPlay = forcePlay || !wasPaused;
      const resume = () => {
        if (switchId !== qualitySwitchId) return;
        video.playbackRate = playbackRate;
        video.defaultPlaybackRate = playbackRate;
        video.volume = volume;
        syncVolumeUi();
        syncProgressUi();
        hideTranscodeOverlay();
        if (shouldPlay) video.play().catch(() => {});
      };
      video.dataset.currentQuality = quality;
      video.addEventListener("canplay", resume, { once: true });
      video.addEventListener("playing", resume, { once: true });
      video.addEventListener("seeked", resume, { once: true });

      if (quality === "original") {
        destroyHls();
        mediaTimeOffset = 0;
        video.addEventListener("loadedmetadata", () => {
          if (switchId !== qualitySwitchId) return;
          if (Number.isFinite(video.duration)) {
            video.currentTime = clamp(resumeAt, 0, Math.max(0, video.duration - 0.25));
          }
        }, { once: true });
        video.src = qualityUrl(quality, resumeAt);
        video.load();
        return;
      }

      showTranscodeOverlay();
      mediaTimeOffset = Math.max(0, resumeAt);
      const url = qualityUrl(quality, resumeAt);
      if (canUseNativeHls()) {
        destroyHls();
        video.src = url;
        video.load();
        return;
      }
      if (window.Hls?.isSupported()) {
        destroyHls();
        hlsPlayer = new window.Hls({
          backBufferLength: 30,
          lowLatencyMode: false,
          maxBufferLength: 20,
        });
        hlsPlayer.on(window.Hls.Events.ERROR, (_event, data) => {
          if (data?.fatal && switchId === qualitySwitchId) {
            showTranscodeOverlay("转码暂不可播放，正在重试...");
            hlsPlayer?.startLoad();
          }
        });
        hlsPlayer.loadSource(url);
        hlsPlayer.attachMedia(video);
        return;
      }
      hideTranscodeOverlay();
      video.dataset.currentQuality = "original";
      video.src = qualityUrl("original", resumeAt);
      video.load();
    };
    syncQualityUi();
    for (const button of qualityButtons) {
      button.addEventListener("click", () => applyQuality(button.dataset.qualityOption));
    }
  }
  if (speedSlider) {
    const speedValues = (speedSlider.dataset.speedValues || "0.5,1,1.5,2,4")
      .split(",")
      .map((item) => Number(item))
      .filter((item) => Number.isFinite(item) && item > 0);
    const applySpeed = () => {
      const index = clamp(Math.round(Number(speedSlider.value)), 0, speedValues.length - 1);
      const nextRate = speedValues[index] || 1;
      speedSlider.value = String(index);
      speedSlider.style.setProperty("--speed-index", String(index));
      if (speedValue) speedValue.textContent = `${nextRate}×`;
      if (video.playbackRate !== nextRate) {
        video.playbackRate = nextRate;
        video.defaultPlaybackRate = nextRate;
      }
    };
    applySpeed();
    speedSlider.addEventListener("input", applySpeed);
    speedSlider.addEventListener("change", applySpeed);
  }
  video.addEventListener("timeupdate", syncProgressUi);
  video.addEventListener("durationchange", syncProgressUi);
  video.addEventListener("loadedmetadata", syncProgressUi);
  video.addEventListener("play", syncPlayUi);
  video.addEventListener("pause", syncPlayUi);
  video.addEventListener("ended", syncPlayUi);
  seekControl?.addEventListener("input", () => {
    const duration = getLogicalDuration();
    if (!Number.isFinite(duration) || duration <= 0) return;
    const nextTime = duration * (Number(seekControl.value) / 1000);
    if (timeValue) timeValue.textContent = `${formatClock(nextTime)} / ${formatClock(duration)}`;
  });
  seekControl?.addEventListener("change", () => {
    const duration = getLogicalDuration();
    if (!Number.isFinite(duration) || duration <= 0) return;
    setLogicalCurrentTime(duration * (Number(seekControl.value) / 1000));
  });
  flatPlayButton?.addEventListener("click", togglePlayback);
  centerAction?.addEventListener("click", togglePlayback);
  if (shell?.dataset.videoType !== "panorama") {
    video.addEventListener("click", togglePlayback);
  }
  syncProgressUi();
  syncPlayUi();
}

if (shell?.dataset.videoType === "panorama" && video) {
  queueMicrotask(() => {
    initPanorama().catch(() => {
      video.classList.remove("hidden-video");
      video.controls = true;
      const error = document.createElement("div");
      error.className = "pano-error";
      error.textContent = "全景渲染加载失败，已切换为普通播放";
      document.querySelector(".player-stage")?.append(error);
    });
  });
}

document.querySelector("[data-player-close]")?.addEventListener("click", () => {
  const referrer = document.referrer ? new URL(document.referrer) : null;
  if (referrer && referrer.origin === window.location.origin && window.history.length > 1) {
    window.history.back();
  } else {
    window.location.assign("/");
  }
});

async function initPanorama() {
  const canvas = document.getElementById("panoramaCanvas");
  if (!canvas) throw new Error("Missing panorama canvas");

  const gl = canvas.getContext("webgl", { antialias: true });
  if (!gl) throw new Error("WebGL is not available");

  if (!canvas.hasAttribute("tabindex")) {
    canvas.tabIndex = 0;
  }

  const program = createProgram(gl, vertexShaderSource, fragmentShaderSource);
  const uniforms = {
    yaw: gl.getUniformLocation(program, "uYaw"),
    pitch: gl.getUniformLocation(program, "uPitch"),
    fov: gl.getUniformLocation(program, "uFov"),
    aspect: gl.getUniformLocation(program, "uAspect"),
    exposure: gl.getUniformLocation(program, "uExposure"),
    texture: gl.getUniformLocation(program, "uTexture"),
  };

  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(
    gl.ARRAY_BUFFER,
    new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]),
    gl.STATIC_DRAW
  );

  const position = gl.getAttribLocation(program, "aPosition");
  gl.enableVertexAttribArray(position);
  gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);

  const texture = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, texture);
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texImage2D(
    gl.TEXTURE_2D,
    0,
    gl.RGBA,
    1,
    1,
    0,
    gl.RGBA,
    gl.UNSIGNED_BYTE,
    new Uint8Array([0, 0, 0, 255])
  );

  let yaw = 0;
  let pitch = 0;
  let moved = false;
  let fov = 75;
  let aspect = 16 / 9;
  const pointers = new Map();
  let lastPinchDistance = 0;

  const setFov = (next) => {
    fov = clamp(next, 35, 105);
  };
  const applyViewDelta = (dx, dy, factor = 0.004) => {
    yaw += dx * factor;
    pitch = clamp(pitch - dy * factor, -1.35, 1.35);
  };
  const distanceBetweenPointers = () => {
    const points = [...pointers.values()];
    if (points.length < 2) return 0;
    return Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y);
  };
  const toggleFullscreen = () => {
    const target = canvas.parentElement;
    if (!target) return;
    if (document.fullscreenElement) {
      document.exitFullscreen?.();
      return;
    }
    target.requestFullscreen?.();
  };
  const resize = () => {
    const rect = canvas.parentElement.getBoundingClientRect();
    const scale = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(1, Math.floor(rect.width * scale));
    const height = Math.max(1, Math.floor(rect.height * scale));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    aspect = rect.width / Math.max(1, rect.height);
    gl.viewport(0, 0, width, height);
  };

  const pointerDown = (event) => {
    moved = false;
    canvas.focus({ preventScroll: true });
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointers.size === 2) {
      lastPinchDistance = distanceBetweenPointers();
    }
    canvas.setPointerCapture(event.pointerId);
  };
  const pointerMove = (event) => {
    const previous = pointers.get(event.pointerId);
    if (!previous) return;
    event.preventDefault();
    const dx = event.clientX - previous.x;
    const dy = event.clientY - previous.y;
    if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });

    if (pointers.size >= 2) {
      const nextDistance = distanceBetweenPointers();
      if (lastPinchDistance) setFov(fov - (nextDistance - lastPinchDistance) * 0.08);
      lastPinchDistance = nextDistance;
      return;
    }

    applyViewDelta(dx, dy);
  };
  const pointerUp = (event) => {
    pointers.delete(event.pointerId);
    lastPinchDistance = pointers.size >= 2 ? distanceBetweenPointers() : 0;
  };
  const normalizeWheel = (event) => {
    const unit = event.deltaMode === WheelEvent.DOM_DELTA_LINE
      ? 16
      : event.deltaMode === WheelEvent.DOM_DELTA_PAGE
        ? window.innerHeight
        : 1;
    return { dx: event.deltaX * unit, dy: event.deltaY * unit };
  };
  const isMouseWheelZoom = (event, dx, dy) => {
    if (event.ctrlKey || event.metaKey) return true;
    return Math.abs(dx) < 4 && (event.deltaMode !== WheelEvent.DOM_DELTA_PIXEL || Math.abs(dy) >= 40);
  };
  const ignoreShortcut = (event) => {
    const target = event.target;
    return target?.isContentEditable || ["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(target?.tagName);
  };
  const keyDown = (event) => {
    if (ignoreShortcut(event)) return;
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setVideoVolume(video.volume + 0.05);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      setVideoVolume(video.volume - 0.05);
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      seekBy(-15);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      seekBy(15);
    } else if (event.key === " " || event.code === "Space") {
      event.preventDefault();
      togglePlayback();
    }
  };

  window.addEventListener("resize", resize);
  canvas.addEventListener("pointerdown", pointerDown);
  canvas.addEventListener("pointermove", pointerMove);
  canvas.addEventListener("pointerup", pointerUp);
  canvas.addEventListener("pointercancel", pointerUp);
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    canvas.focus({ preventScroll: true });
    const { dx, dy } = normalizeWheel(event);
    if (isMouseWheelZoom(event, dx, dy)) {
      setFov(fov + dy * 0.045);
      return;
    }
    applyViewDelta(-dx, -dy, 0.003);
  }, { passive: false });
  canvas.addEventListener("dblclick", (event) => {
    event.preventDefault();
    toggleFullscreen();
  });
  canvas.addEventListener("click", () => {
    if (moved) return;
    togglePlayback();
  });
  document.addEventListener("keydown", keyDown);

  resize();

  const render = () => {
    resize();
    gl.useProgram(program);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, texture);
    if (video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) {
      try {
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, video);
      } catch {
        gl.texImage2D(
          gl.TEXTURE_2D,
          0,
          gl.RGBA,
          1,
          1,
          0,
          gl.RGBA,
          gl.UNSIGNED_BYTE,
          new Uint8Array([0, 0, 0, 255])
        );
      }
    }
    gl.uniform1f(uniforms.yaw, yaw);
    gl.uniform1f(uniforms.pitch, pitch);
    gl.uniform1f(uniforms.fov, fov * Math.PI / 180);
    gl.uniform1f(uniforms.aspect, aspect);
    gl.uniform1f(uniforms.exposure, exposure);
    gl.uniform1i(uniforms.texture, 0);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    requestAnimationFrame(render);
  };
  render();
}

function createProgram(gl, vertexSource, fragmentSource) {
  const vertexShader = compileShader(gl, gl.VERTEX_SHADER, vertexSource);
  const fragmentShader = compileShader(gl, gl.FRAGMENT_SHADER, fragmentSource);
  const program = gl.createProgram();
  gl.attachShader(program, vertexShader);
  gl.attachShader(program, fragmentShader);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    throw new Error(gl.getProgramInfoLog(program) || "Unable to link WebGL program");
  }
  return program;
}

function compileShader(gl, type, source) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    throw new Error(gl.getShaderInfoLog(shader) || "Unable to compile WebGL shader");
  }
  return shader;
}

const vertexShaderSource = `
attribute vec2 aPosition;
varying vec2 vUv;

void main() {
  vUv = aPosition * 0.5 + 0.5;
  gl_Position = vec4(aPosition, 0.0, 1.0);
}
`;

const fragmentShaderSource = `
precision mediump float;

uniform sampler2D uTexture;
uniform float uYaw;
uniform float uPitch;
uniform float uFov;
uniform float uAspect;
uniform float uExposure;
varying vec2 vUv;

const float PI = 3.141592653589793;

vec3 rotatePitch(vec3 direction, float angle) {
  float c = cos(angle);
  float s = sin(angle);
  return vec3(direction.x, direction.y * c - direction.z * s, direction.y * s + direction.z * c);
}

vec3 rotateYaw(vec3 direction, float angle) {
  float c = cos(angle);
  float s = sin(angle);
  return vec3(direction.x * c + direction.z * s, direction.y, -direction.x * s + direction.z * c);
}

void main() {
  vec2 point = vUv * 2.0 - 1.0;
  float scale = tan(uFov * 0.5);
  vec3 direction = normalize(vec3(point.x * scale * uAspect, -point.y * scale, -1.0));
  direction = rotateYaw(rotatePitch(direction, uPitch), uYaw);

  float lon = atan(direction.x, -direction.z);
  float lat = asin(clamp(direction.y, -1.0, 1.0));
  vec2 uv = vec2(fract(0.5 - lon / (2.0 * PI)), clamp(0.5 - lat / PI, 0.0, 1.0));
  vec4 color = texture2D(uTexture, uv);
  gl_FragColor = vec4(color.rgb * uExposure, color.a);
}
`;
