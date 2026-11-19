import * as T from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { finishLandmarks } from './landmarks.js';

export async function initializeStudy(model, capture) {
  const $ = (id) => document.getElementById(id);
  const underground = model === 'underground';
  const title = underground ? '東京駅周辺の地下街' : '大丸有の22棟';
  document.body.classList.add('model-study');
  document.title = title + ' — 東京駅上空';
  document.querySelector('.mast h1').textContent = title;
  document.querySelector('.subheading').textContent = underground
    ? 'Tokyo underground'
    : 'Otemachi · Marunouchi · Yurakucho';
  document.querySelector('.coordinates').hidden = true;
  document.querySelector('.telemetry').hidden = true;
  document.querySelector('.flight-panel:not([id])').hidden = true;
  $('model-panel').hidden = false;
  $('view').setAttribute('aria-label', title + 'の3D表示');
  $('view').tabIndex = 0;
  $('building-label').textContent = underground ? '階層' : '建物';
  $('building').options[0].text = underground ? '地下街全体' : '22棟を俯瞰';
  $('cutaway').hidden = !underground;
  $('finish').hidden = !underground;
  $('loading').querySelector('span').textContent = title + 'を読み込み中';

  const canvas = $('view');
  const renderer = new T.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: capture });
  renderer.setPixelRatio(capture ? 1 : Math.min(devicePixelRatio, 1.5));
  renderer.toneMapping = T.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.15;
  const scene = new T.Scene();
  scene.background = new T.Color(underground ? '#242f35' : '#465760');
  scene.add(new T.HemisphereLight('#e6f0ff', '#a7a29a', 2.15));
  const key = new T.DirectionalLight('#fff1d8', 2.5);
  key.position.set(-310, 720, 435);
  scene.add(key);
  const camera = new T.PerspectiveCamera(43, 1, 0.2, 17000);
  const controls = new OrbitControls(camera, canvas);
  controls.maxPolarAngle = Math.PI * 0.94;
  controls.minDistance = 1.5;
  controls.maxDistance = 13000;
  controls.listenToKeyEvents(canvas);

  const loader = new GLTFLoader();
  const load = async (file) => (await loader.loadAsync('assets/' + file)).scene;
  const [assets, detail] = await Promise.all([
    load(underground ? 'omy-underground.glb' : 'omy-buildings.glb'),
    underground ? Promise.resolve(null) : load('marunouchi-nichome.glb'),
  ]);
  scene.add(assets);
  if (detail) scene.add(detail);
  const choices = new Map();
  for (const object of assets.children) {
    const data = object.userData;
    const id = underground ? data.storeyId : data.id;
    if (id) choices.set(id, underground ? data.storeyName : data.name);
  }
  const maxAnisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy());
  if (!underground) finishLandmarks(assets, maxAnisotropy, false);
  for (const [id, label] of [...choices].sort((a, b) => a[1].localeCompare(b[1], 'ja'))) {
    $('building').add(new Option(label.replaceAll('_', ' '), id));
  }
  let openCeilings = true;
  const bounds = new T.Box3();
  const center = new T.Vector3();
  const meshes = [];
  function filter() {
    const id = $('building').value;
    bounds.makeEmpty();
    meshes.length = 0;
    for (const object of assets.children) {
      const data = object.userData;
      const matches = !id || id === (underground ? data.storeyId : data.id);
      object.visible = matches && !(openCeilings && data.cutawayHide);
      if (object.visible) {
        bounds.expandByObject(object, true);
        object.traverse((mesh) => {
          if (mesh.isMesh) meshes.push(mesh);
        });
      }
    }
    if (detail) detail.visible = !id || choices.get(id) === '丸の内二丁目ビル';
    bounds.getCenter(center);
  }
  function pointCamera(bearing = 145) {
    const phi = T.MathUtils.degToRad(underground ? 32 : 64);
    const eye = new T.Vector3().setFromSphericalCoords(1, phi, T.MathUtils.degToRad(bearing));
    const right = new T.Vector3().crossVectors(camera.up, eye).normalize();
    const up = new T.Vector3().crossVectors(eye, right);
    const halfY = Math.tan(T.MathUtils.degToRad(camera.fov / 2));
    const point = new T.Vector3();
    let distance = 6;
    for (const mesh of meshes) {
      const position = mesh.geometry.getAttribute('position');
      for (let i = 0; i < position.count; i++) {
        point.fromBufferAttribute(position, i).applyMatrix4(mesh.matrixWorld).sub(center);
        const fit = Math.max(Math.abs(point.dot(right)) / camera.aspect, Math.abs(point.dot(up)));
        distance = Math.max(distance, point.dot(eye) + (fit * (capture ? 1.1 : 1.4)) / halfY);
      }
    }
    camera.position.copy(eye).multiplyScalar(distance).add(center);
    controls.target.copy(center);
    controls.update();
  }
  const paint = () => renderer.render(scene, camera);
  function resize() {
    const { width, height } = canvas.getBoundingClientRect();
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    pointCamera();
    paint();
  }
  filter();
  resize();
  controls.addEventListener('change', paint);
  const select = (id) => {
    $('building').value = id;
    filter();
    pointCamera();
    paint();
  };
  $('building').addEventListener('change', () => select($('building').value));
  $('reset-view').addEventListener('click', () => {
    pointCamera();
    paint();
  });
  $('cutaway').addEventListener('click', () => {
    openCeilings = !openCeilings;
    $('cutaway').setAttribute('aria-pressed', String(openCeilings));
    filter();
    paint();
  });
  $('finish').addEventListener('click', () => {
    $('building').value = '';
    filter();
    camera.position.set(-125.8712, -4.0373, -106.7526);
    controls.target.set(-132.2377, -5.8877, -104.5117);
    controls.update();
    paint();
  });
  const requested = new URLSearchParams(location.search).get('id');
  if (choices.has(requested)) select(requested);
  for (const id of ['building', 'cutaway', 'finish', 'reset-view']) $(id).disabled = false;
  $('loading').hidden = true;
  if (!capture) new ResizeObserver(resize).observe(canvas);
  window.architectureCapture = {
    choices: Object.fromEntries(choices),
    select,
    orbit(bearing) {
      pointCamera(bearing);
      paint();
    },
  };
}
