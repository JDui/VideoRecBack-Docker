import { createPlayerAudio } from "./player-audio.js?v=1";

const video = document.getElementById("videoPlayer");
const audio = video ? createPlayerAudio(video) : null;
const shell = document.querySelector(".player-shell");
const stage = document.querySelector(".player-stage");
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
let volumeControl = null;
let volumeValue = null;
let exposureControl = null;
let exposureValue = null;
let exposure = 1;
let mediaTimeOffset = 0;
let applyQualityAt = null;
let hlsPlayer = null;
let activeHlsSession = null;
let hlsHeartbeatTimer = null;
let hlsStopTimer = null;
let stallRecoveryTimer = null;
let bufferingStartedAt = 0;
let transcodeBlocked = false;
let activeTranscodeInfo = null;
let transcodeInfoPromise = null;
const hdrNotice = document.querySelector('[data-hdr-playback-notice]');
const loadTranscodeInfo = () => {
  if (!transcodeInfoPromise) {
    transcodeInfoPromise = fetch(`${video.dataset.mediaBase}/playback-info`, { cache: 'no-store' })
      .then(async response => {
        const info = await response.json();
        if (!response.ok) throw new Error(info.detail || '无法读取视频色彩信息，请重试。');
        return info;
      }).catch(error => { transcodeInfoPromise = null; throw error; });
  }
  return transcodeInfoPromise;
};
const canDecodeHdrHls = () => {
  const mime = 'video/mp4; codecs="hvc1.2.4.L120.B0"';
  const nativeHls = video.canPlayType('application/vnd.apple.mpegurl');
  const mse = window.ManagedMediaSource || window.MediaSource;
  return Boolean(mse?.isTypeSupported(mime)) || Boolean(nativeHls && video.canPlayType(mime));
};
const canUseHdrMse = () => {
  const mse = window.ManagedMediaSource || window.MediaSource;
  return window.Hls?.isSupported() && mse?.isTypeSupported('video/mp4; codecs="hvc1.2.4.L120.B0"');
};
const blockTranscode = message => {
  transcodeBlocked = true;
  mediaTimeOffset = getLogicalCurrentTime();
  clearStallRecovery();
  video.pause();
  stopHlsHeartbeat(true);
  hlsPlayer?.destroy();
  hlsPlayer = null;
  video.removeAttribute('src');
  video.load();
  showTranscodeOverlay(message);
  transcodeOverlay?.classList.add('is-error');
  syncPlayUi();
};
const qualityLabels = {
  original: "原画",
  ultra: "超清",
  low: "高清",
  high: "流畅",
};
const totalDuration = Number(shell?.dataset.duration || 0);
const videoBitDepth = Number(shell?.dataset.videoBitDepth || 8);
const isTenBitVideo = Number.isFinite(videoBitDepth) && videoBitDepth >= 10;
const transcodeOverlay = document.querySelector("[data-transcode-overlay]");
const seekControl = document.querySelector("[data-seek-control]");
const seekTrack = document.querySelector("[data-seek-track]");
const timeValue = document.querySelector("[data-time-value]");
const flatPlayButton = document.querySelector("[data-flat-play]");
const centerAction = document.querySelector("[data-player-center-action]");
const muteToggle = document.querySelector("[data-mute-toggle]");
const fullscreenToggle = document.querySelector("[data-fullscreen-toggle]");
const playerPoster = document.querySelector("[data-player-poster]");
const favoriteToggle = document.querySelector("[data-favorite-toggle]");
const favoriteLabel = document.querySelector("[data-favorite-label]");
const RETURN_STATE_KEY = "videorecback-return-state";
const RETURNING_FROM_PLAYER_KEY = "videorecback-returning-from-player";
const playerFeedback = document.querySelector("[data-player-feedback]");
let feedbackTimer = null;
const showPlayerFeedback = (message) => {
  if (!playerFeedback) return;
  window.clearTimeout(feedbackTimer);
  playerFeedback.textContent = message;
  playerFeedback.classList.add("is-visible");
  feedbackTimer = window.setTimeout(() => playerFeedback.classList.remove("is-visible"), 1200);
};

const toggleFullscreen = async () => {
  try {
    if (document.fullscreenElement) await document.exitFullscreen?.();
    else if (shell?.requestFullscreen) await shell.requestFullscreen();
    else if (video?.webkitEnterFullscreen) video.webkitEnterFullscreen();
    else showPlayerFeedback("当前浏览器不支持全屏");
  } catch {
    showPlayerFeedback("暂时无法进入全屏");
  }
};

let mediaLoaded = false;
let seekingWithControl = false;
let fallbackOriginalToTranscode = () => false;

const hlsControlUrl = (session, action) => {
  if (!session) return "";
  return `${session.base}/hls/${encodeURIComponent(session.quality)}/${session.startMs}/${action}`;
};

const sendHlsControl = (action, session = activeHlsSession) => {
  const url = hlsControlUrl(session, action);
  if (!url) return;
  if (navigator.sendBeacon && action === "stop") {
    navigator.sendBeacon(url, new Blob([], { type: "application/octet-stream" }));
    return;
  }
  fetch(url, { method: "POST", keepalive: true }).catch(() => {});
};

const clearScheduledHlsStop = () => {
  if (!hlsStopTimer) return;
  window.clearTimeout(hlsStopTimer);
  hlsStopTimer = null;
};

const stopHlsHeartbeat = (sendStop = true) => {
  clearScheduledHlsStop();
  if (hlsHeartbeatTimer) {
    window.clearInterval(hlsHeartbeatTimer);
    hlsHeartbeatTimer = null;
  }
  if (sendStop && activeHlsSession) sendHlsControl("stop");
  activeHlsSession = null;
};

const scheduleHlsStop = (delayMs = 60000) => {
  if (!activeHlsSession) return;
  clearScheduledHlsStop();
  hlsStopTimer = window.setTimeout(() => {
    stopHlsHeartbeat(true);
  }, delayMs);
};

const startHlsHeartbeat = (quality, startAt) => {
  clearScheduledHlsStop();
  const base = video?.dataset.mediaBase || video?.getAttribute("src") || "";
  const cleanBase = base.split("#")[0];
  activeHlsSession = {
    base: cleanBase,
    quality,
    startMs: Math.round(Math.max(0, startAt) * 1000),
  };
  sendHlsControl("heartbeat");
  if (hlsHeartbeatTimer) window.clearInterval(hlsHeartbeatTimer);
  hlsHeartbeatTimer = window.setInterval(() => {
    if (!video || video.ended || document.hidden) return;
    sendHlsControl("heartbeat");
  }, 3000);
};

const clearStallRecovery = () => {
  bufferingStartedAt = 0;
  if (!stallRecoveryTimer) return;
  window.clearTimeout(stallRecoveryTimer);
  stallRecoveryTimer = null;
};

const syncVolumeUi = () => {
  if (!video || !volumeControl) return;
  const level = audio.getVolume();
  const percent = String(Math.round((video.muted ? 0 : level) * 100));
  volumeControl.value = percent;
  if (volumeValue) volumeValue.textContent = `${percent}%`;
  volumeControl.setAttribute("aria-valuetext", `${percent}%${level > 1 && !video.muted ? "，音量增强" : ""}`);
  volumeControl.closest("[data-volume-ramp]")?.style.setProperty("--volume-fill", `${Number(percent) / 1.5}%`);
  video.dataset.appliedVolume = String(level);
};

const setVideoVolume = (nextVolume) => {
  if (!video) return;
  const applied = audio.setVolume(nextVolume);
  if (nextVolume > 1 && applied <= 1) showPlayerFeedback("当前浏览器不支持音量增强，最高为 100%");
  syncVolumeUi();
};

const toggleMute = () => {
  if (!video) return;
  if (!video.muted && video.volume > 0) {
    video.dataset.previousVolume = String(audio.getVolume());
    video.muted = true;
  } else {
    const previousVolume = Number(video.dataset.previousVolume || video.dataset.appliedVolume || 0.6);
    audio.setVolume(Number.isFinite(previousVolume) && previousVolume > 0 ? previousVolume : 0.6);
  }
  syncVolumeUi();
  syncMuteUi();
};

const togglePlayback = () => {
  if (!video) return;
  if (transcodeBlocked) {
    applyQualityAt?.(video.dataset.currentQuality || 'original', getLogicalCurrentTime(), true);
    return;
  }
  void audio.resume();
  if (!mediaLoaded) {
    const quality = video.dataset.currentQuality || "original";
    applyQualityAt?.(quality, getLogicalCurrentTime(), true);
    return;
  }
  if (video.paused) {
    video.play().catch(() => {});
  } else {
    video.pause();
  }
};

const seekBy = (seconds) => {
  if (!video) return;
  setLogicalCurrentTime(getLogicalCurrentTime() + seconds);
  showPlayerFeedback(`${seconds > 0 ? "前进" : "后退"} ${Math.abs(seconds)} 秒`);
};

const changeVolumeBy = (delta) => {
  if (!video) return;
  const baseVolume = audio.getVolume();
  video.muted = false;
  setVideoVolume(baseVolume + delta);
  syncMuteUi();
};

const isEditableKeyTarget = (target) => {
  if (!(target instanceof Element)) return false;
  if (target.closest("[contenteditable='true']")) return true;
  return Boolean(target.closest("input, select, textarea, button, summary, a"));
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
  if (!mediaLoaded) {
    mediaTimeOffset = 0;
    return;
  }
  const relativeTime = nextTime - mediaTimeOffset;
  if (currentQuality !== "original" && (relativeTime < 0 || relativeTime > Math.max(0, video.duration || 0))) {
    applyQualityAt?.(currentQuality, nextTime, !video.paused);
    return;
  }
  video.currentTime = clamp(relativeTime, 0, Math.max(0, video.duration || 0));
};

const showTranscodeOverlay = (message = "正在切换转码中...") => {
  if (!transcodeOverlay) return;
  transcodeOverlay.classList.remove('is-error');
  transcodeOverlay.textContent = message;
  transcodeOverlay.hidden = false;
  shell?.classList.add("is-transcoding");
};

const hideTranscodeOverlay = () => {
  if (transcodeOverlay) transcodeOverlay.hidden = true;
  shell?.classList.remove("is-transcoding");
};

const schedulePlaybackRecovery = () => {
  if (transcodeBlocked || !video || !mediaLoaded || video.paused || video.ended) return;
  const quality = video.dataset.currentQuality || "original";
  if (!bufferingStartedAt) bufferingStartedAt = Date.now();
  showTranscodeOverlay(quality === "original" ? "正在缓冲..." : "正在等待转码缓冲...");
  if (stallRecoveryTimer) return;
  stallRecoveryTimer = window.setTimeout(() => {
    stallRecoveryTimer = null;
    if (!video || !mediaLoaded || video.paused || video.ended || !bufferingStartedAt) return;
    if (Date.now() - bufferingStartedAt < 8000) return;
    applyQualityAt?.(quality, getLogicalCurrentTime(), true);
  }, 9000);
};

const markPlaybackResponsive = () => {
  if (transcodeBlocked) return;
  clearStallRecovery();
  hideTranscodeOverlay();
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
  if (seekControl && !seekingWithControl && document.activeElement !== seekControl) {
    seekControl.value = String(Math.round(progress * 1000));
  }
  const displayedProgress = seekControl ? Number(seekControl.value) / 1000 : progress;
  seekTrack?.style.setProperty("--seek-progress", `${displayedProgress * 100}%`);
  let buffered = 0;
  for (let index = 0; index < (video?.buffered.length || 0); index++) {
    if (video.buffered.start(index) <= video.currentTime && video.buffered.end(index) >= video.currentTime) {
      buffered = (mediaTimeOffset + video.buffered.end(index)) / duration;
      break;
    }
  }
  seekTrack?.style.setProperty("--seek-buffer", `${clamp(buffered, 0, 1) * 100}%`);
  seekControl?.setAttribute("aria-valuetext", `${formatClock(displayedProgress * duration)} / ${formatClock(duration)}`);
  if (timeValue) {
    const current = formatClock(getLogicalCurrentTime());
    const total = Number.isFinite(duration) ? formatClock(duration) : "00:00";
    timeValue.textContent = `${current} / ${total}`;
  }
};

const syncPlayUi = () => {
  const paused = transcodeBlocked || video?.paused;
  const label = paused ? "播放" : "暂停";
  if (flatPlayButton) {
    flatPlayButton.classList.toggle("is-playing", !paused);
    flatPlayButton.setAttribute("aria-label", label);
    flatPlayButton.setAttribute("title", label);
  }
  if (centerAction) {
    centerAction.setAttribute("aria-label", label);
    centerAction.tabIndex = paused ? 0 : -1;
    centerAction.classList.toggle("is-hidden", !paused);
  }
  shell?.classList.toggle("is-playing", Boolean(video && !paused && !video.ended));
  shell?.classList.toggle("is-paused", Boolean(video && (paused || video.ended)));
};

const syncMuteUi = () => {
  if (!video || !muteToggle) return;
  const muted = video.muted || video.volume === 0;
  muteToggle.classList.toggle("is-muted", muted);
  muteToggle.setAttribute("aria-pressed", String(muted));
  muteToggle.setAttribute("aria-label", muted ? "取消静音" : "静音");
  muteToggle.setAttribute("title", muted ? "取消静音" : "静音");
};

const syncExposureUi = () => {
  const percent = String(Math.round(exposure * 100));
  if (exposureControl) exposureControl.value = percent;
  if (exposureValue) exposureValue.textContent = `${percent}%`;
  if (video && shell?.dataset.videoType !== "panorama") {
    video.style.filter = exposure === 1 ? 'none' : `brightness(${exposure})`;
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
      const nextVolume = Number(volumeControl.value) / 100;
      setVideoVolume(nextVolume);
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
  if (favoriteToggle && shell?.dataset.videoId) {
    const syncFavoriteUi = (favorite) => {
      favoriteToggle.dataset.favoriteState = favorite ? "1" : "0";
      favoriteToggle.classList.toggle("active", favorite);
      favoriteToggle.setAttribute("aria-pressed", favorite ? "true" : "false");
      favoriteToggle.setAttribute("aria-label", favorite ? "取消收藏" : "收藏视频");
      if (favoriteLabel) favoriteLabel.textContent = favorite ? "已收藏" : "收藏";
    };
    favoriteToggle.addEventListener("click", async () => {
      const nextFavorite = favoriteToggle.dataset.favoriteState !== "1";
      favoriteToggle.disabled = true;
      try {
        const body = new URLSearchParams({ favorite: nextFavorite ? "1" : "0" });
        const response = await fetch(`/video/${encodeURIComponent(shell.dataset.videoId)}/favorite`, {
          method: "POST",
          body,
        });
        if (!response.ok) throw new Error("Favorite request failed");
        const payload = await response.json();
        syncFavoriteUi(Boolean(payload.favorite));
      } catch {
        syncFavoriteUi(!nextFavorite);
      } finally {
        favoriteToggle.disabled = false;
      }
    });
  }

  const speedSlider = document.querySelector("[data-speed-slider]");
  const speedValue = document.querySelector("[data-speed-value]");
  const qualityButtons = [...document.querySelectorAll("[data-quality-option]")];
  const qualityValue = document.querySelector("[data-quality-value]");
  if (qualityButtons.length) {
    const qualityValues = qualityButtons.map((button) => button.dataset.qualityOption).filter((item) => item in qualityLabels);
    const defaultQuality = shell?.dataset.defaultQuality || "original";
    video.dataset.currentQuality = qualityValues.includes(defaultQuality) ? defaultQuality : (qualityValues[0] || "original");
    let qualitySwitchId = 0;
    let originalFallbackUsed = false;
    const qualityUrl = (quality, startAt = 0) => {
      const base = video.dataset.mediaBase || video.getAttribute("src") || "";
      const cleanBase = base.split("#")[0];
      if (quality === "original") return cleanBase;
      return `${cleanBase}/hls/${encodeURIComponent(quality)}/${Math.round(Math.max(0, startAt) * 1000)}/index.m3u8`;
    };
    const destroyHls = () => {
      if (!hlsPlayer) return;
      hlsPlayer.destroy();
      hlsPlayer = null;
    };
    const prepareMediaSwitch = () => {
      transcodeBlocked = false;
      activeTranscodeInfo = null;
      clearStallRecovery();
      stopHlsHeartbeat(true);
      destroyHls();
      video.pause();
      video.removeAttribute("src");
      video.load();
      video.preload = "auto";
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
      if (!mediaLoaded) {
        video.dataset.currentQuality = quality;
        return;
      }
      if (video.dataset.currentQuality === quality) return;
      applyQualityAt(quality, getLogicalCurrentTime(), false);
    };
    const fallbackQuality = () => {
      if (qualityValues.includes("ultra")) return "ultra";
      return qualityValues.find((item) => item !== "original") || "original";
    };

    applyQualityAt = async (quality, resumeAt, forcePlay = false) => {
      const wasPaused = video.paused;
      const playbackRate = video.playbackRate;
      const volume = video.volume;
      const switchId = ++qualitySwitchId;
      const shouldPlay = forcePlay || !wasPaused;
      const resume = () => {
        if (switchId !== qualitySwitchId || transcodeBlocked) return;
        video.playbackRate = playbackRate;
        video.defaultPlaybackRate = playbackRate;
        video.volume = volume;
        syncVolumeUi();
        syncProgressUi();
        hideTranscodeOverlay();
        if (shouldPlay) video.play().catch(() => {});
      };
      video.dataset.currentQuality = quality;
      mediaLoaded = true;
      shell?.classList.add("has-media");
      if (playerPoster) playerPoster.hidden = true;
      prepareMediaSwitch();
      if (hdrNotice) hdrNotice.hidden = true;
      if (quality !== 'original') {
        showTranscodeOverlay('正在检查转码格式...');
        try {
          const info = await loadTranscodeInfo();
          if (switchId !== qualitySwitchId) return;
          activeTranscodeInfo = info;
          if (info.hdr && !canDecodeHdrHls()) {
            blockTranscode('当前浏览器无法解码 HDR 转码（HEVC 10bit）。请使用支持的浏览器或播放/下载原画。');
            return;
          }
          if (info.hdr && hdrNotice) {
            hdrNotice.textContent = `${info.format} 转码 · ${info.note || '保留 HDR；实际显示取决于浏览器和屏幕。'}${document.getElementById('panoramaCanvas') ? ' 全景画布尚不能保证 HDR 显示。' : ''}`;
            hdrNotice.hidden = false;
          }
        } catch (error) {
          if (switchId === qualitySwitchId) blockTranscode(error.message || '转码格式检查失败，请重试。');
          return;
        }
      }
      video.addEventListener("canplay", resume, { once: true });
      video.addEventListener("playing", resume, { once: true });
      video.addEventListener("seeked", resume, { once: true });

      if (quality === "original") {
        hideTranscodeOverlay();
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
      startHlsHeartbeat(quality, resumeAt);
      if (canUseNativeHls() && !(activeTranscodeInfo?.hdr && canUseHdrMse())) {
        destroyHls();
        video.src = url;
        video.load();
        return;
      }
      if (window.Hls?.isSupported()) {
        hlsPlayer = new window.Hls({
          backBufferLength: 90,
          fragLoadingMaxRetry: 8,
          fragLoadingRetryDelay: 650,
          levelLoadingMaxRetry: 8,
          levelLoadingRetryDelay: 650,
          lowLatencyMode: false,
          manifestLoadingMaxRetry: 8,
          manifestLoadingRetryDelay: 650,
          maxBufferLength: 60,
          maxMaxBufferLength: 120,
          startFragPrefetch: true,
        });
        hlsPlayer.on(window.Hls.Events.MEDIA_ATTACHED, () => {
          if (switchId !== qualitySwitchId) return;
          hlsPlayer?.loadSource(url);
        });
        hlsPlayer.on(window.Hls.Events.MANIFEST_PARSED, () => {
          if (switchId !== qualitySwitchId || !shouldPlay) return;
          video.play().catch(() => {});
        });
        hlsPlayer.on(window.Hls.Events.ERROR, (_event, data) => {
          if (!data?.fatal || switchId !== qualitySwitchId) return;
          const status = data.response?.code || data.networkDetails?.status;
          if ((status >= 400 && status !== 503) || (activeTranscodeInfo?.hdr && data.type !== window.Hls.ErrorTypes.NETWORK_ERROR)) {
            blockTranscode(activeTranscodeInfo?.hdr ? 'HDR 转码播放失败，请检查 HEVC 10bit 支持及服务端编码器，或使用原画。' : '转码失败，请重试或使用原画。');
            return;
          }
          showTranscodeOverlay("播放缓冲中，正在重试...");
          if (data.type === window.Hls.ErrorTypes.NETWORK_ERROR) {
            hlsPlayer?.startLoad();
          } else if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR) {
            hlsPlayer?.recoverMediaError();
          } else {
            window.setTimeout(() => {
              if (switchId === qualitySwitchId) applyQualityAt?.(quality, getLogicalCurrentTime(), shouldPlay);
            }, 900);
          }
        });
        hlsPlayer.attachMedia(video);
        return;
      }
      hideTranscodeOverlay();
      video.dataset.currentQuality = "original";
      mediaTimeOffset = 0;
      video.addEventListener("loadedmetadata", () => {
        if (switchId !== qualitySwitchId) return;
        if (Number.isFinite(video.duration)) {
          video.currentTime = clamp(resumeAt, 0, Math.max(0, video.duration - 0.25));
        }
      }, { once: true });
      video.src = qualityUrl("original", resumeAt);
      video.load();
    };
    fallbackOriginalToTranscode = (resumeAt = 0, forcePlay = true) => {
      const quality = fallbackQuality();
      if (!isTenBitVideo || originalFallbackUsed || video.dataset.currentQuality !== "original" || quality === "original") {
        return false;
      }
      originalFallbackUsed = true;
      showTranscodeOverlay("当前浏览器无法直接播放 10bit 原画，正在切换转码...");
      syncQualityUi(quality);
      applyQualityAt(quality, resumeAt, forcePlay);
      return true;
    };
    syncQualityUi();
    for (const button of qualityButtons) {
      button.addEventListener("click", () => {
        applyQuality(button.dataset.qualityOption);
        button.closest("details")?.removeAttribute("open");
      });
    }
  }
  if (speedSlider) {
    const speedValues = (speedSlider.dataset.speedValues || "0.25,0.5,1,1.5,2,4")
      .split(",")
      .map((item) => Number(item))
      .filter((item) => Number.isFinite(item) && item > 0);
    const applySpeed = () => {
      const index = clamp(Math.round(Number(speedSlider.value)), 0, speedValues.length - 1);
      const nextRate = speedValues[index] || 1;
      speedSlider.value = String(index);
      speedSlider.style.setProperty("--speed-index", String(index));
      speedSlider.setAttribute("aria-valuetext", `${nextRate} 倍速`);
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
  video.addEventListener("progress", syncProgressUi);
  video.addEventListener("durationchange", syncProgressUi);
  video.addEventListener("loadedmetadata", () => {
    if (video.videoWidth && video.videoHeight) shell?.style.setProperty("--video-aspect-ratio", String(video.videoWidth / video.videoHeight));
    syncProgressUi();
  });
  video.addEventListener("canplay", markPlaybackResponsive);
  video.addEventListener("playing", markPlaybackResponsive);
  video.addEventListener("waiting", schedulePlaybackRecovery);
  video.addEventListener("stalled", schedulePlaybackRecovery);
  video.addEventListener("error", () => {
    if (transcodeBlocked) return;
    if (activeTranscodeInfo?.hdr && video.dataset.currentQuality !== 'original') {
      blockTranscode('HDR 转码播放失败，请使用支持 HEVC 10bit 的浏览器或原画。');
      return;
    }
    if (fallbackOriginalToTranscode(getLogicalCurrentTime(), true)) return;
    if (!mediaLoaded || video.paused || video.ended) return;
    window.setTimeout(() => {
      if (!video.paused && !video.ended) applyQualityAt?.(video.dataset.currentQuality || "original", getLogicalCurrentTime(), true);
    }, 900);
  });
  video.addEventListener("play", syncPlayUi);
  video.addEventListener("play", () => {
    if (transcodeBlocked) { video.pause(); return; }
    void audio.resume();
    syncPlayUi();
    clearScheduledHlsStop();
    if (video.dataset.currentQuality !== "original" && !activeHlsSession) {
      applyQualityAt?.(video.dataset.currentQuality, getLogicalCurrentTime(), true);
      return;
    }
    if (video.dataset.currentQuality !== "original" && activeHlsSession) sendHlsControl("heartbeat");
  });
  video.addEventListener("pause", () => {
    syncPlayUi();
    clearStallRecovery();
    if (video.dataset.currentQuality !== "original") scheduleHlsStop(60000);
  });
  video.addEventListener("ended", () => {
    syncPlayUi();
    stopHlsHeartbeat(true);
  });
  video.addEventListener("volumechange", () => {
    syncVolumeUi();
    syncMuteUi();
  });
  seekControl?.addEventListener("pointerdown", () => {
    seekingWithControl = true;
  });
  seekControl?.addEventListener("input", () => {
    seekingWithControl = true;
    const duration = getLogicalDuration();
    if (!Number.isFinite(duration) || duration <= 0) return;
    const nextTime = duration * (Number(seekControl.value) / 1000);
    if (timeValue) timeValue.textContent = `${formatClock(nextTime)} / ${formatClock(duration)}`;
    seekTrack?.style.setProperty("--seek-progress", `${Number(seekControl.value) / 10}%`);
    seekControl.setAttribute("aria-valuetext", `${formatClock(nextTime)} / ${formatClock(duration)}`);
  });
  seekControl?.addEventListener("change", () => {
    const duration = getLogicalDuration();
    if (Number.isFinite(duration) && duration > 0) {
      setLogicalCurrentTime(duration * (Number(seekControl.value) / 1000));
    }
    seekingWithControl = false;
    window.requestAnimationFrame(syncProgressUi);
  });
  flatPlayButton?.addEventListener("click", togglePlayback);
  centerAction?.addEventListener("click", togglePlayback);
  stage?.addEventListener("click", (event) => {
    if (event.target.closest("button, input, select, textarea, label, a, summary, details, [data-player-center-action]")) return;
    if (shell?.dataset.videoType === "panorama" && event.target.closest("#panoramaCanvas")) return;
    togglePlayback();
  });
  muteToggle?.addEventListener("click", toggleMute);
  for (const button of document.querySelectorAll("[data-seek-step]")) {
    button.addEventListener("click", () => seekBy(Number(button.dataset.seekStep || 0)));
  }
  fullscreenToggle?.addEventListener("click", toggleFullscreen);
  document.addEventListener("fullscreenchange", () => {
    const fullscreen = Boolean(document.fullscreenElement);
    fullscreenToggle?.setAttribute("aria-label", fullscreen ? "退出全屏" : "全屏");
    fullscreenToggle?.setAttribute("title", fullscreen ? "退出全屏" : "全屏");
  });
  if (seekControl) seekControl.addEventListener("pointerup", () => {
    seekingWithControl = false;
    window.requestAnimationFrame(syncProgressUi);
  });
  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || isEditableKeyTarget(event.target)) return;
    if (event.key === " " || event.key === "Spacebar") {
      event.preventDefault();
      togglePlayback();
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      seekBy(-10);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      seekBy(10);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      changeVolumeBy(0.05);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      changeVolumeBy(-0.05);
    }
  });
  syncProgressUi();
  syncPlayUi();
  syncMuteUi();
}

const playbackSettings = document.querySelector("[data-playback-settings]");
const playbackToggle = document.querySelector("[data-playback-toggle]");
playbackToggle?.addEventListener("click", () => { playbackSettings.open = !playbackSettings.open; });
playbackSettings?.addEventListener("toggle", () => {
  playbackToggle?.setAttribute("aria-expanded", String(playbackSettings.open));
});
window.addEventListener("pagehide", event => { if (event.persisted) void audio?.suspend(); else void audio?.close(); });
window.addEventListener("pageshow", event => { if (event.persisted) void audio?.resume(); });
document.addEventListener("pointerdown", () => { void audio?.resume(); }, { passive: true });
document.addEventListener("keydown", () => { void audio?.resume(); });

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

const readReturnState = () => {
  for (const storage of [sessionStorage, localStorage]) {
    try {
      const state = JSON.parse(storage.getItem(RETURN_STATE_KEY) || "null");
      if (state && Date.now() - Number(state.savedAt || 0) < 24 * 60 * 60 * 1000) return state;
    } catch {
      storage.removeItem(RETURN_STATE_KEY);
    }
  }
  return null;
};

const markReturningFromPlayer = () => {
  for (const storage of [sessionStorage, localStorage]) {
    try {
      storage.setItem(RETURNING_FROM_PLAYER_KEY, "1");
    } catch {}
  }
};

const playerReturnTarget = () => {
  const state = readReturnState();
  const target = shell?.dataset.returnUrl || (state?.url ? state.url + (state.hash || '') : '/');
  try {
    const parsed = new URL(target, window.location.origin);
    return target.startsWith('/') && parsed.origin === window.location.origin ? target : '/library';
  } catch { return '/library'; }
};
const closePlayerPage = () => {
  shell?.classList.add("is-closing");
  const target = playerReturnTarget();

  markReturningFromPlayer();
  window.VideoRecBackNavigate(target, { replace: true });
  window.setTimeout(() => {
    try {
      video?.pause();
    } catch {}
    stopHlsHeartbeat(true);
  }, 0);
};

const playerMenus = [...document.querySelectorAll('[data-quality-menu], [data-download-menu], [data-player-settings-menu]')];
const downloadStatus = document.querySelector('[data-download-status]');
document.querySelectorAll('[data-download-quality]').forEach(link => {
  link.addEventListener('click', () => {
    if (downloadStatus) {
      downloadStatus.hidden = false;
      downloadStatus.textContent = link.dataset.downloadQuality === 'original' ?
        '已请求原画下载，请查看浏览器下载列表。' :
        `正在准备${qualityLabels[link.dataset.downloadQuality]}完整视频，准备完成后浏览器将开始下载。`;
    }
    link.closest('details').open = false;
  });
});
document.querySelector('[data-download-frame]')?.addEventListener('load', event => {
  try {
    const body = event.target.contentDocument?.body?.textContent || '';
    if (body && JSON.parse(body).detail && downloadStatus) {
      downloadStatus.hidden = false;
      downloadStatus.textContent = '下载未能开始，请重试或选择其他画质。';
    }
  } catch {}
});
for (const menu of playerMenus) {
  menu.addEventListener('toggle', () => {
    if (menu.open) for (const other of playerMenus) if (other !== menu) other.open = false;
  });
}
document.addEventListener("click", (event) => {
  for (const menu of playerMenus) if (menu.open && !menu.contains(event.target)) menu.open = false;
});
document.addEventListener("keydown", (event) => {
  const menu = playerMenus.find(item => item.open);
  if (event.key === "Escape" && menu) {
    menu.open = false;
    menu.querySelector("summary")?.focus();
  }
});

document.querySelector("[data-player-close]")?.addEventListener("click", closePlayerPage);
window.addEventListener("pagehide", () => stopHlsHeartbeat(true));
window.addEventListener("beforeunload", () => stopHlsHeartbeat(true));
document.addEventListener("visibilitychange", () => {
  if (video?.dataset.currentQuality === "original") return;
  if (document.hidden) {
    scheduleHlsStop(30000);
  } else {
    clearScheduledHlsStop();
    if (activeHlsSession) sendHlsControl("heartbeat");
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
  let suppressClickUntil = 0;
  let fov = 75;
  let aspect = 16 / 9;
  const pointers = new Map();
  let lastPinchDistance = 0;
  let primaryPointerStart = null;

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
    primaryPointerStart = { x: event.clientX, y: event.clientY, time: Date.now() };
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
    const totalDx = primaryPointerStart ? event.clientX - primaryPointerStart.x : dx;
    const totalDy = primaryPointerStart ? event.clientY - primaryPointerStart.y : dy;
    if (Math.abs(dx) + Math.abs(dy) > 3 || Math.hypot(totalDx, totalDy) > 6) moved = true;
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });

    if (pointers.size >= 2) {
      moved = true;
      const nextDistance = distanceBetweenPointers();
      if (lastPinchDistance) setFov(fov - (nextDistance - lastPinchDistance) * 0.08);
      lastPinchDistance = nextDistance;
      return;
    }

    applyViewDelta(dx, dy);
  };
  const pointerUp = (event) => {
    pointers.delete(event.pointerId);
    if (moved) suppressClickUntil = Date.now() + 420;
    if (!pointers.size) primaryPointerStart = null;
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
  canvas.addEventListener("click", (event) => {
    event.stopPropagation();
    if (moved || Date.now() < suppressClickUntil) {
      event.preventDefault();
      return;
    }
    togglePlayback();
  });

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
  vec2 uv = vec2(fract(0.5 + lon / (2.0 * PI)), clamp(0.5 - lat / PI, 0.0, 1.0));
  vec4 color = texture2D(uTexture, uv);
  gl_FragColor = vec4(color.rgb * uExposure, color.a);
}
`;
