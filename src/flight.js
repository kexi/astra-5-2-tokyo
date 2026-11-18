import * as T from 'three';

const shots = [
  {
    name: '丸の内へ', duration: 6.5, fov: 49,
    positions: [[-749, 113, 173], [-679, 173, 126], [-555, 249, 58]],
    targets: [[-348, 11, -40], [-104, 31, -45], [37, 49, -10]],
  },
  {
    name: '堀沿いの木漏れ日', duration: 4, fov: 50,
    positions: [[-527, 9.1, 31], [-530, 8.1, 25], [-531, 7.2, 19.5]],
    targets: [[-543, 8.0, 11], [-543, 8.4, 11.5], [-542, 8.9, 12.5]],
  },
  {
    name: '東京駅 丸の内駅舎', duration: 6.5, fov: 55,
    positions: [[-365, 94, -71], [-253, 80, -43], [-128, 63, -16]],
    targets: [[-27, 16, 0], [-23, 18, -4], [-20, 19, -6]],
  },
  {
    name: '線路と街区', duration: 7, fov: 48,
    positions: [[434, 386, 294], [368, 360, 418], [232, 323, 494]],
    targets: [[-41, 22, -35], [-49, 20, -28], [-38, 28, -43]],
  },
  {
    name: '夕陽のスカイライン', duration: 7.5, fov: 56,
    positions: [[625, 230, 400], [511, 290, 499], [347, 382, 591]],
    targets: [[-356, 166, -297], [-457, 135, -313], [-506, 128, -314]],
  },
].map(shot => ({
  ...shot,
  path: new T.CatmullRomCurve3(shot.positions.map(position => new T.Vector3(...position))),
  aim: new T.CatmullRomCurve3(shot.targets.map(position => new T.Vector3(...position))),
}));

export const duration = shots.reduce((sum, shot) => sum + shot.duration, 0);

export function flightPose(camera, time) {
  let index = 0;
  let elapsed = time;
  while (index < shots.length - 1 && elapsed >= shots[index].duration) {
    elapsed -= shots[index++].duration;
  }
  const shot = shots[index];
  const t = T.MathUtils.clamp(elapsed / shot.duration, 0, 1);
  const softened = .78 * t + .22 * t * t * (3 - 2 * t);
  const target = shot.aim.getPoint(softened);
  camera.position.copy(shot.path.getPoint(softened));
  camera.fov = shot.fov;
  camera.up.set(0, 1, 0);
  camera.lookAt(target);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld();
  return { index, name: shot.name, target, t };
}

export function reviewPose(camera, bearing, height, distance) {
  const angle = T.MathUtils.degToRad(bearing);
  camera.fov = 51;
  camera.up.set(0, 1, 0);
  camera.position.set(distance * Math.sin(angle), height, -distance * Math.cos(angle));
  camera.lookAt(0, 24, 0);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld();
}

const detailViews = {
  moat: { position: [-500, 35, 195], target: [-430, 2, -32] },
  leaves: { position: [-530, 7.2, 20], target: [-542, 8.9, 12.5] },
  trees: { position: [-560, 10.7, 27], target: [-543, 6.2, 12.5] },
  glass: { position: [-314, 88, -59], target: [-145, 32, -2] },
  grass: { position: [-930, 70, 62], target: [-786, 3, 125] },
};

export function detailPose(camera, name) {
  const view = detailViews[name];
  if (!view) throw new RangeError(`Unknown detail view: ${name}`);
  camera.fov = 54;
  camera.up.set(0, 1, 0);
  camera.position.set(...view.position);
  camera.lookAt(...view.target);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld();
}
