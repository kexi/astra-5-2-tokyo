import * as T from 'three';
import settings from '../tools/yaesu-hedges.json' with { type: 'json' };
import { surfaceMaterial } from './materials.js';

export const hedgeTile = settings.tile;
const batches = new Set(settings.batches);

export function applyHedgeSurface(object, leafTexture) {
  const material = surfaceMaterial('leaf', leafTexture);
  material.color.fromArray(settings.leafColor);
  material.roughness = 0.94;
  const source = object.geometry;
  const geometry = source.index ? source.toNonIndexed() : source.clone();
  const ids = geometry.getAttribute('_batchid');
  const positions = geometry.getAttribute('position');
  const uv = geometry.getAttribute('uv');
  const ordinary = [],
    hedges = [];
  const vertices = [new T.Vector3(), new T.Vector3(), new T.Vector3()];
  const normal = new T.Vector3();
  object.updateWorldMatrix(true, false);
  for (let i = 0; i < positions.count; i += 3) {
    const batch = ids.getX(i);
    if (!batches.has(batch)) {
      ordinary.push(i, i + 1, i + 2);
      continue;
    }
    hedges.push(i, i + 1, i + 2);
    vertices.forEach((point, j) =>
      point.fromBufferAttribute(positions, i + j).applyMatrix4(object.matrixWorld),
    );
    T.Triangle.getNormal(...vertices, normal);
    const top = Math.abs(normal.y) > 0.62;
    const facesEast = Math.abs(normal.x) > Math.abs(normal.z);
    vertices.forEach(({ x, y, z }, j) => {
      const a = top || !facesEast ? x : -z;
      const b = top ? -z : y;
      const u = (0.932 * a - 0.362 * b) * settings.repeatsPerMeter + batch * 0.37;
      const v = (0.362 * a + 0.932 * b) * settings.repeatsPerMeter + batch * 0.19;
      uv.setXY(i + j, u, 1 - v);
    });
  }
  geometry.setIndex(ordinary.concat(hedges));
  geometry.clearGroups();
  geometry.addGroup(0, ordinary.length, 0);
  geometry.addGroup(ordinary.length, hedges.length, 1);
  object.geometry = geometry;
  object.material = [object.material, material];
}
