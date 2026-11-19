// Rank buildings by their visible area in the eight actual flight shots.
import { readFile, writeFile } from 'node:fs/promises';
import { registerHooks } from 'node:module';
import { join } from 'node:path';
import { gunzipSync } from 'node:zlib';
import * as T from '../vendor/build/three.module.js';

const threeUrl = new URL('../vendor/build/three.module.js', import.meta.url).href;
registerHooks({
  resolve(specifier, context, next) {
    return next(specifier === 'three' ? threeUrl : specifier, context);
  },
});
const { duration, flightPose } = await import('../src/flight.js');
const [folder = 'assets/buildings', destination = join(folder, 'flight-details.json')] =
  process.argv.slice(2);
const catalog = JSON.parse(gunzipSync(await readFile(join(folder, 'catalog.json.gz'))));
const packs = await Promise.all(catalog.overview.map((file) => readFile(join(folder, file))));
const rows = [null];
const tiles = [];
for (const tile of catalog.tiles) {
  const [file, start, bytes] = tile.geometry;
  const raw = gunzipSync(packs[file].subarray(start, start + bytes));
  const n = raw.readUInt32LE(4);
  const count = raw.readUInt32LE(8);
  const small = raw.readUInt32LE(12) === 2;
  const buf = raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.length);
  const xyz = new Float32Array(buf, 16, 3 * n);
  let offset = 16;
  for (const stride of [12, 6, 4, 4, 1]) offset += Math.ceil((n * stride) / 4) * 4;
  const idx = small ? new Uint16Array(buf, offset, count) : new Uint32Array(buf, offset, count);
  const triangles = new Uint32Array(count / 3);
  let last = 0;
  const extent = new T.Box3().setFromArray(xyz);
  for (const row of tile.buildings) {
    const record = { id: row[0], peak: 0 };
    triangles.fill(rows.length, last, row[3] / 3);
    last = row[3] / 3;
    rows.push(record);
  }
  tiles.push({ xyz, idx, triangles, extent, screen: new Float32Array(xyz.length) });
}

const width = 160,
  height = 90;
const camera = new T.PerspectiveCamera(50, width / height, 0.2, 8000);
const frustum = new T.Frustum();
const projection = new T.Matrix4();
const pixels = new Uint32Array(width * height);
const depth = new Float32Array(pixels.length);
const counts = new Uint32Array(rows.length);

function raster(tile, matrix) {
  const { xyz, idx, screen, triangles } = tile;
  for (let i = 0; i < xyz.length; i += 3) {
    const x = xyz[i],
      y = xyz[i + 1],
      z = xyz[i + 2];
    const w = matrix[3] * x + matrix[7] * y + matrix[11] * z + matrix[15];
    screen[i] =
      ((1 + (matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12]) / w) * width) / 2;
    screen[i + 1] =
      ((1 - (matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13]) / w) * height) / 2;
    screen[i + 2] = w >= 0.2 ? 1 / w : -1;
  }
  for (let t = 0; t < idx.length; t += 3) {
    const a = 3 * idx[t],
      b = 3 * idx[t + 1],
      c = 3 * idx[t + 2];
    const x0 = screen[a],
      y0 = screen[a + 1],
      z0 = screen[a + 2];
    const x1 = screen[b],
      y1 = screen[b + 1],
      z1 = screen[b + 2];
    const x2 = screen[c],
      y2 = screen[c + 1],
      z2 = screen[c + 2];
    if (Math.min(z0, z1, z2) < 0) continue;
    const left = Math.max(0, Math.ceil(Math.min(x0, x1, x2) - 0.5));
    const right = Math.min(width - 1, Math.floor(Math.max(x0, x1, x2) - 0.5));
    const top = Math.max(0, Math.ceil(Math.min(y0, y1, y2) - 0.5));
    const bottom = Math.min(height - 1, Math.floor(Math.max(y0, y1, y2) - 0.5));
    const area = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2);
    if (left > right || top > bottom || Math.abs(area) < 1e-6) continue;
    for (let y = top; y <= bottom; y++) {
      for (let x = left; x <= right; x++) {
        const dx = x + 0.5 - x2,
          dy = y + 0.5 - y2;
        const u = ((y1 - y2) * dx + (x2 - x1) * dy) / area;
        const v = ((y2 - y0) * dx + (x0 - x2) * dy) / area;
        if (u < 0 || v < 0 || u + v > 1) continue;
        const z = z2 + u * (z0 - z2) + v * (z1 - z2);
        const p = y * width + x;
        if (z <= depth[p]) continue;
        pixels[p] = triangles[t / 3];
        depth[p] = z;
      }
    }
  }
}

for (let time = 0; time < duration; time += 0.5) {
  flightPose(camera, time);
  camera.updateMatrixWorld();
  projection.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
  frustum.setFromProjectionMatrix(projection);
  pixels.fill(0);
  depth.fill(0);
  counts.fill(0);
  for (const tile of tiles) {
    if (frustum.intersectsBox(tile.extent)) raster(tile, projection.elements);
  }
  for (const id of pixels) counts[id]++;
  for (let i = 1; i < counts.length; i++) rows[i].peak = Math.max(rows[i].peak, counts[i]);
}
const priority = rows
  .slice(1)
  .filter((r) => r.peak >= width * height * 0.005)
  .sort((a, b) => b.peak - a.peak || a.id.localeCompare(b.id))
  .slice(0, 72);
await writeFile(destination, JSON.stringify(priority.map((r) => r.id)) + '\n');
console.log(
  JSON.stringify({
    buildings: priority.length,
    peakPixels: priority[0]?.peak,
    smallestPeak: priority.at(-1)?.peak,
    sampleSize: [width, height],
  }),
);
