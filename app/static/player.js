const video = document.getElementById("videoPlayer");
const shell = document.querySelector(".player-shell");

if (video) {
  const configuredVolume = Number(shell?.dataset.defaultVolume ?? 0.2);
  video.volume = Math.max(0, Math.min(1, Number.isFinite(configuredVolume) ? configuredVolume : 0.2));
  video.dataset.appliedVolume = String(video.volume);
  const volumeControl = document.querySelector("[data-volume-control]");
  const volumeValue = document.querySelector("[data-volume-value]");
  if (volumeControl) {
    volumeControl.value = String(Math.round(video.volume * 100));
    if (volumeValue) volumeValue.textContent = `${volumeControl.value}%`;
    volumeControl.addEventListener("input", () => {
      video.volume = Math.max(0, Math.min(1, Number(volumeControl.value) / 100));
      video.dataset.appliedVolume = String(video.volume);
      if (volumeValue) volumeValue.textContent = `${volumeControl.value}%`;
    });
  }

  let speedTimer = null;
  for (const button of document.querySelectorAll("[data-speed]")) {
    button.addEventListener("click", () => {
      document.querySelectorAll("[data-speed]").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      window.clearTimeout(speedTimer);
      speedTimer = window.setTimeout(() => {
        const nextRate = Number(button.dataset.speed);
        if (video.playbackRate !== nextRate) {
          video.playbackRate = nextRate;
          video.defaultPlaybackRate = nextRate;
        }
      }, 80);
    });
  }
}

if (shell?.dataset.videoType === "panorama" && video) {
  initPanorama().catch(() => {
    video.classList.remove("hidden-video");
    video.controls = true;
    document.querySelector(".pano-hint")?.replaceChildren("全景渲染加载失败，已切换为普通播放");
  });
}

async function initPanorama() {
  const THREE = await import("https://unpkg.com/three@0.160.0/build/three.module.js");
  const canvas = document.getElementById("panoramaCanvas");
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(75, 16 / 9, 0.1, 1000);
  camera.position.set(0, 0, 0.1);

  const texture = new THREE.VideoTexture(video);
  texture.colorSpace = THREE.SRGBColorSpace;
  const geometry = new THREE.SphereGeometry(500, 64, 48);
  geometry.scale(-1, 1, 1);
  const material = new THREE.MeshBasicMaterial({ map: texture });
  scene.add(new THREE.Mesh(geometry, material));

  let yaw = 0;
  let pitch = 0;
  let moved = false;
  let fov = 75;
  const pointers = new Map();
  let lastPinchDistance = 0;

  const clampPitch = () => {
    pitch = Math.max(-1.35, Math.min(1.35, pitch));
  };
  const setFov = (next) => {
    fov = Math.max(35, Math.min(105, next));
    camera.fov = fov;
    camera.updateProjectionMatrix();
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
  const applyViewDelta = (dx, dy, factor = 0.004) => {
    yaw += dx * factor;
    pitch += dy * factor;
    clampPitch();
  };
  const distanceBetweenPointers = () => {
    const points = [...pointers.values()];
    if (points.length < 2) return 0;
    return Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y);
  };

  const resize = () => {
    const rect = canvas.parentElement.getBoundingClientRect();
    renderer.setSize(rect.width, rect.height, false);
    camera.aspect = rect.width / rect.height;
    camera.updateProjectionMatrix();
  };
  window.addEventListener("resize", resize);
  resize();

  const pointerDown = (event) => {
    moved = false;
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
    if (Math.abs(dx) + Math.abs(dy) > 3) {
      moved = true;
    }
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointers.size >= 2) {
      const nextDistance = distanceBetweenPointers();
      if (lastPinchDistance) {
        setFov(fov - (nextDistance - lastPinchDistance) * 0.08);
      }
      lastPinchDistance = nextDistance;
      return;
    }
    applyViewDelta(dx, dy);
  };
  const pointerUp = (event) => {
    pointers.delete(event.pointerId);
    lastPinchDistance = pointers.size >= 2 ? distanceBetweenPointers() : 0;
  };
  canvas.addEventListener("pointerdown", pointerDown);
  canvas.addEventListener("pointermove", pointerMove);
  canvas.addEventListener("pointerup", pointerUp);
  canvas.addEventListener("pointercancel", pointerUp);
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    if (event.ctrlKey || event.metaKey) {
      setFov(fov + event.deltaY * 0.04);
      return;
    }
    applyViewDelta(-event.deltaX, -event.deltaY, 0.003);
  }, { passive: false });
  canvas.addEventListener("dblclick", (event) => {
    event.preventDefault();
    toggleFullscreen();
  });
  canvas.addEventListener("click", () => {
    if (moved) return;
    if (video.paused) {
      video.play();
    } else {
      video.pause();
    }
  });

  const render = () => {
    camera.rotation.order = "YXZ";
    camera.rotation.y = yaw;
    camera.rotation.x = pitch;
    renderer.render(scene, camera);
    requestAnimationFrame(render);
  };
  render();
}
