import * as T from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { buildingCatalog, isolatedBuilding } from './building-assets.js';

export async function initializeBuildingViewer(capture) {
  const byId = (id) => document.getElementById(id);
  const canvas = byId('view'),
    search = byId('city-search'),
    status = byId('city-status');
  document.body.classList.add('model-study');
  document.querySelector('.flight-panel:not([id])').hidden = true;
  byId('city-study-panel').hidden = false;
  document.querySelector('.telemetry').hidden = true;
  document.querySelector('.coordinates').hidden = true;
  document.querySelector('.mast h1').textContent = '東京の建物';
  document.querySelector('.subheading').textContent = 'Tokyo building archive';
  document.title = '東京の建物 — 東京駅上空';
  canvas.setAttribute('aria-label', '選択した建物の3D表示');
  canvas.tabIndex = 0;
  const renderer = new T.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: capture });
  renderer.setPixelRatio(capture ? 1 : Math.min(devicePixelRatio, 1.6));
  const scene = new T.Scene();
  scene.background = new T.Color('#465760');
  scene.add(new T.HemisphereLight('#fbfbff', '#a8a299', 1.85));
  const key = new T.DirectionalLight('#fff8ec', 1.65);
  key.position.set(-0.6, 1, 0.5);
  scene.add(key);
  const camera = new T.PerspectiveCamera(42, 1, 0.1, 5000);
  const controls = new OrbitControls(camera, canvas);
  controls.enablePan = false;
  controls.autoRotateSpeed = 1.2;
  const rotate = byId('city-rotate');
  let last = 0;
  function animate(time) {
    controls.update(Math.min(0.1, (time - last) / 1000));
    last = time;
  }
  const paint = () => renderer.render(scene, camera);
  controls.addEventListener('change', paint);
  function setRotate(on) {
    controls.autoRotate = on;
    last = performance.now();
    renderer.setAnimationLoop(on && !document.hidden ? animate : null);
    rotate.textContent = on ? '回転を止める' : '自動で回転';
  }
  const motion = matchMedia('(prefers-reduced-motion: reduce)');
  setRotate(!capture && !motion.matches);
  motion.addEventListener('change', () => {
    if (motion.matches) setRotate(false);
  });
  document.addEventListener('visibilitychange', () => setRotate(controls.autoRotate));
  canvas.addEventListener('keydown', (event) => {
    const step = Math.PI / 12;
    const offsets = {
      ArrowLeft: [step, 0],
      ArrowRight: [-step, 0],
      ArrowUp: [0, step],
      ArrowDown: [0, -step],
    };
    const turn = offsets[event.key];
    if (!turn) return;
    event.preventDefault();
    setRotate(false);
    controls.rotateLeft(turn[0]);
    controls.rotateUp(turn[1]);
  });
  rotate.onclick = () => setRotate(!controls.autoRotate);
  const catalog = await buildingCatalog();
  const entries = catalog.tiles.flatMap((tile) =>
    tile.buildings.map((row, index) => ({ tile, index, row })),
  );
  const ids = new Map(entries.map((entry) => [entry.row[0], entry]));
  let current, mesh, active, distance;
  function resize() {
    const { width, height } = canvas.getBoundingClientRect();
    renderer.setSize(width, height, false);
    camera.aspect = width / Math.max(1, height);
    camera.updateProjectionMatrix();
    paint();
  }
  new ResizeObserver(resize).observe(canvas);
  resize();
  function pose(degrees = 145) {
    const a = T.MathUtils.degToRad(degrees);
    camera.position
      .set(Math.sin(a) * distance, distance * 0.36, -Math.cos(a) * distance)
      .add(controls.target);
    camera.lookAt(controls.target);
    controls.update(0);
    renderer.render(scene, camera);
  }
  function dispose(object) {
    if (!object) return;
    scene.remove(object);
    object.geometry.dispose();
    object.material.map.dispose();
    object.material.dispose();
  }
  async function select(entry, historyMode = 'replaceState') {
    active?.abort();
    const request = new AbortController();
    active = request;
    status.textContent = `${entry.row[0]} を読み込み中`;
    try {
      const next = await isolatedBuilding(
        catalog,
        entry.tile,
        entry.index,
        renderer.capabilities.getMaxAnisotropy(),
        request.signal,
      );
      if (request.signal.aborted) {
        dispose(next);
        return;
      }
      dispose(mesh);
      mesh = next;
      scene.add(mesh);
      current = entry;
      const sphere = mesh.geometry.boundingSphere;
      controls.target.copy(sphere.center);
      const halfFov = T.MathUtils.degToRad(camera.fov * 0.5);
      distance = Math.max(
        2,
        (sphere.radius * 1.15) /
          Math.sin(Math.min(halfFov, Math.atan(Math.tan(halfFov) * camera.aspect))),
      );
      controls.minDistance = Math.max(0.5, sphere.radius * 0.16);
      controls.maxDistance = distance * 6;
      camera.far = Math.max(2000, distance * 12);
      camera.updateProjectionMatrix();
      pose();
      search.value = entry.row[0];
      const [photo, wall, roof, bottom, repaired] = entry.row[5];
      const orthophoto = entry.row[6],
        prior = entry.row[7];
      const inferred = wall + roof + bottom + repaired + prior - orthophoto;
      status.textContent = [
        entry.row[1],
        entry.row[0],
        `元画像 ${(photo - prior).toLocaleString('ja')}面・推定／補修 ${inferred.toLocaleString('ja')}面`,
        orthophoto ? `航空写真の屋根 ${orthophoto.toLocaleString('ja')}面` : '',
        entry.row.length > 8
          ? '撮影画像に判定保留の範囲あり' + (entry.row[8] ? `（${entry.row[8]}箇所）` : '')
          : '',
      ]
        .filter(Boolean)
        .join('　');
      const url = new URL(location.href);
      url.searchParams.set('id', entry.row[0]);
      if (historyMode) history[historyMode](null, '', url);
    } catch (error) {
      if (!request.signal.aborted) {
        status.textContent = `読み込めませんでした。 ${error.message}`;
        throw error;
      }
    }
  }
  function matches(value) {
    const text = value.trim().toLowerCase();
    return entries.filter(
      ({ row }) => row[0].includes(text) || row[1].toLowerCase().includes(text),
    );
  }
  function suggest() {
    const list = byId('city-matches');
    const options = matches(search.value)
      .slice(0, 24)
      .map(({ row }) => {
        const choice = document.createElement('option');
        choice.value = row[0];
        choice.label = row[1];
        return choice;
      });
    list.replaceChildren(...options);
  }
  search.addEventListener('input', suggest);
  search.addEventListener('focus', () => search.select());
  byId('city-find').onsubmit = (event) => {
    event.preventDefault();
    const exact = ids.get(search.value.trim().toLowerCase());
    const candidates = exact ? [exact] : matches(search.value);
    if (candidates.length !== 1) {
      status.textContent = candidates.length
        ? `${candidates.length.toLocaleString('ja')}件が一致しました。候補から建物IDを選んでください。`
        : '該当する建物がありません。';
      suggest();
      search.focus();
      return;
    }
    select(candidates[0], 'pushState').catch(console.error);
  };
  byId('city-reset').onclick = () => pose();
  const fallback = ids.get('13101-bldg-1663') ?? entries[0];
  const initialId = new URLSearchParams(location.search).get('id');
  await select(ids.get(initialId) ?? fallback);
  if (initialId && !ids.has(initialId))
    status.textContent = `建物ID ${initialId} は索引にありません。 ${status.textContent}`;
  window.addEventListener('popstate', () =>
    select(ids.get(new URLSearchParams(location.search).get('id')) ?? fallback, null).catch(
      console.error,
    ),
  );
  byId('loading').hidden = true;
  byId('city-count').textContent =
    `${catalog.count.toLocaleString('ja')}棟 · ドラッグで回転・スクロールで拡大`;
  if (capture)
    window.buildingCapture = {
      select: (id) => select(ids.get(id)),
      frame: (degrees) => {
        setRotate(false);
        pose(degrees);
        return controls.getAzimuthalAngle();
      },
      get state() {
        return {
          id: current.row[0],
          triangles: mesh.geometry.index.count / 3,
          count: catalog.count,
        };
      },
    };
}
