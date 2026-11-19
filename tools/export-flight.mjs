import { registerHooks } from 'node:module';
import { writeFile } from 'node:fs/promises';

const three = new URL('../vendor/build/three.module.js', import.meta.url).href;
registerHooks({
  resolve(specifier, context, next) {
    return next(specifier === 'three' ? three : specifier, context);
  },
});
const { PerspectiveCamera } = await import('three');
const { duration, flightPose } = await import('../src/flight.js');

const file = process.argv[2];
if (!file) throw new Error('Specify the output JSON file');
const fps = 30;
const camera = new PerspectiveCamera();
const frames = [];
for (let frame = 0; frame < Math.round(duration * fps); frame++) {
  const time = frame / fps;
  const pose = flightPose(camera, time);
  frames.push({
    shot: pose.index,
    name: pose.name,
    position: camera.position.toArray(),
    target: pose.target.toArray(),
    fov: camera.fov,
  });
}
await writeFile(file, JSON.stringify({ fps, frames }));
