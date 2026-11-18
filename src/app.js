import * as T from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { loadCity } from './city.js';
import { captureMoatReflection, makeFlare, makeSky, mist, sun } from './materials.js';
import { detailPose, duration, flightPose, reviewPose } from './flight.js';

const $ = id => document.getElementById(id);
const canvas = $('view');
const query = new URLSearchParams(location.search);
const capture = query.has('capture');
const cinema = query.has('cinema');
const motion = matchMedia('(prefers-reduced-motion: reduce)');
document.body.classList.toggle('capture', capture);
document.body.classList.toggle('cinema', cinema);

async function initialize() {
  const renderer = new T.WebGLRenderer({
    canvas, antialias: true, powerPreference: 'high-performance', preserveDrawingBuffer: capture,
  });
  renderer.toneMapping = T.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.24;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = T.PCFShadowMap;
  renderer.shadowMap.autoUpdate = false;
  renderer.setPixelRatio(capture ? 1 : Math.min(devicePixelRatio, 1.5));

  const scene = new T.Scene();
  scene.background = mist.clone();
  const sky = makeSky();
  scene.add(sky);
  const camera = new T.PerspectiveCamera(49, 16 / 9, 2, 95000);
  const controls = new OrbitControls(camera, canvas);
  controls.enabled = false;
  controls.enableDamping = !motion.matches;
  controls.minDistance = 83;
  controls.maxDistance = 1700;
  controls.maxPolarAngle = 1.40;
  controls.minPolarAngle = .10;
  controls.screenSpacePanning = false;

  const sunlight = new T.DirectionalLight('#ffcf9c', 2.32);
  sunlight.position.copy(sun).multiplyScalar(2600);
  sunlight.target.position.set(-180, 0, 20);
  sunlight.position.add(sunlight.target.position);
  sunlight.castShadow = true;
  sunlight.shadow.mapSize.set(4096, 4096);
  sunlight.shadow.camera.layers.enable(2);
  sunlight.shadow.camera.left = sunlight.shadow.camera.bottom = -1530;
  sunlight.shadow.camera.right = sunlight.shadow.camera.top = 1530;
  sunlight.shadow.camera.near = 750;
  sunlight.shadow.camera.far = 5250;
  sunlight.shadow.normalBias = .64;
  sunlight.shadow.bias = -.00007;
  sunlight.shadow.intensity = .83;
  scene.add(sunlight, sunlight.target, new T.HemisphereLight('#d2def0', '#afa090', 1.79));

  const bar = $('loading').querySelector('progress');
  const city = await loadCity((done, total) => {
    bar.max = total;
    bar.value = done;
    $('loading').querySelector('span').textContent = `都市モデルを読み込み中 ${Math.floor(100 * done / total)}%`;
  }, renderer.capabilities.getMaxAnisotropy());
  scene.add(city.root);
  captureMoatReflection(renderer, scene, city.water);

  const flare = makeFlare(renderer);
  let time = 0, paused = true, orbit = false, showFlare = true;
  let previous = 0;

  function resize() {
    const { width, height } = canvas.getBoundingClientRect();
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  }
  resize();
  if (!capture) new ResizeObserver(resize).observe(canvas);

  const clockLabel = seconds => `0:${String(Math.floor(seconds)).padStart(2, '0')}`;
  const direction = new T.Vector3();
  function info(pose) {
    const bearing = (Math.atan2(camera.getWorldDirection(direction).x, -direction.z) * 180 / Math.PI + 360) % 360;
    $('bearing').textContent = `${String(Math.round(bearing) % 360).padStart(3, '0')}°`;
    $('height').textContent = `${Math.round(camera.position.y)} m`;
    $('needle').style.transform = `rotate(${-bearing}deg)`;
    if (!pose) return;
    $('shot').textContent = pose.name;
    $('seek').value = time;
    $('time').textContent = `${clockLabel(time)} / ${clockLabel(duration)}`;
  }

  function setPaused(value) {
    paused = value;
    $('play').textContent = paused ? '空撮を再生' : '一時停止';
  }

  function setOrbit(value) {
    orbit = controls.enabled = value;
    $('orbit').setAttribute('aria-pressed', String(value));
    $('hint').textContent = value ? 'ドラッグで回転・スクロールでズーム・矢印キーで方位と高度を変更' : '東京駅・丸の内・八重洲';
    if (!value) return;
    setPaused(true);
    const pose = flightPose(camera, time);
    controls.target.copy(pose.target);
    controls.update();
  }

  function paint(seconds, withFlare = showFlare) {
    sky.material.uniforms.seconds.value = seconds;
    city.water.uniforms.seconds.value = seconds;
    city.trees.update(camera, seconds);
    renderer.render(scene, camera);
    flare(camera, city.buildings, seconds, withFlare, capture);
  }

  const intro = $('film-title');
  const credit = $('film-credit');
  const fade = T.MathUtils.smoothstep;
  function filmOverlays(seconds) {
    intro.style.opacity = fade(seconds, .15, 1.0) * (1 - fade(seconds, 3.65, 5.1));
    credit.style.opacity = fade(seconds, duration - 5.3, duration - 4.6);
  }

  window.flightCapture = {
    duration,
    counts: city.counts,
    stats() {
      const gl = renderer.getContext();
      const id = gl.getExtension('WEBGL_debug_renderer_info');
      return { gpu: id ? gl.getParameter(id.UNMASKED_RENDERER_WEBGL) : 'WebGL 2', ...city.counts };
    },
    frame(seconds, withFlare = true) {
      flightPose(camera, seconds);
      if (cinema) filmOverlays(seconds);
      paint(seconds, withFlare);
      return { position: camera.position.toArray(), triangles: renderer.info.render.triangles };
    },
    orbit(bearing, height, radius, withFlare = true) {
      reviewPose(camera, bearing, height, radius);
      paint(3, withFlare);
    },
    detail(name, seconds = 3) {
      detailPose(camera, name);
      paint(seconds);
    },
  };

  if (capture) {
    renderer.shadowMap.needsUpdate = true;
    window.flightCapture.frame(2);
    $('loading').hidden = true;
    return;
  }

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      previous = 0;
      renderer.setAnimationLoop(null);
    } else {
      renderer.setAnimationLoop(animate);
    }
  });
  motion.addEventListener('change', () => {
    controls.enableDamping = !motion.matches;
    if (motion.matches) setPaused(true);
  });
  $('play').addEventListener('click', () => {
    if (orbit) setOrbit(false);
    if (time >= duration) time = 0;
    setPaused(!paused);
  });
  $('orbit').addEventListener('click', () => setOrbit(!orbit));
  $('flare').addEventListener('click', () => {
    showFlare = !showFlare;
    $('flare').setAttribute('aria-pressed', String(showFlare));
  });
  $('seek').max = duration;
  $('seek').addEventListener('input', event => {
    setOrbit(false);
    setPaused(true);
    time = Number(event.target.value);
  });
  $('orbit').addEventListener('keydown', event => {
    const offsets = { ArrowLeft: [-.16, 0], ArrowRight: [.16, 0], ArrowUp: [0, -.11], ArrowDown: [0, .11] };
    const offset = offsets[event.key];
    if (!orbit || !offset) return;
    event.preventDefault();
    const position = new T.Spherical().setFromVector3(camera.position.clone().sub(controls.target));
    position.theta += offset[0];
    position.phi = T.MathUtils.clamp(position.phi + offset[1], controls.minPolarAngle, controls.maxPolarAngle);
    camera.position.setFromSpherical(position).add(controls.target);
    controls.update();
  });
  for (const id of ['play', 'orbit', 'flare', 'seek']) $(id).disabled = false;
  $('loading').hidden = true;
  renderer.shadowMap.needsUpdate = true;
  renderer.setAnimationLoop(animate);

  function animate(ms) {
    const dt = previous ? Math.min(.10, (ms - previous) / 1000) : 0;
    previous = ms;
    if (!paused) {
      time = Math.min(time + dt, duration);
      if (time === duration) setPaused(true);
    }
    if (orbit) controls.update();
    const pose = orbit ? null : flightPose(camera, time);
    info(pose);
    paint(time);
  }
}

initialize().catch(error => {
  window.flightFailure = error.message;
  console.error(error);
  $('loading').querySelector('span').textContent = `表示できませんでした。 ${error.message}`;
  $('loading').querySelector('progress').hidden = true;
});
