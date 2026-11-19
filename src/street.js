import * as T from 'three';
import settings from '../tools/yaesu-street.json' with { type: 'json' };
import { surfaceMaterial } from './materials.js';

export const streetTiles = settings.tiles;
export const roadLift = settings.roadLift;

export function makeStreetMaterials() {
  return Object.fromEntries(Object.entries(settings.surfaces).map(([kind, spec]) => {
    const material = surfaceMaterial('bridge');
    material.color.fromArray(spec.color);
    material.roughness = spec.roughness;
    material.metalness = spec.metalness;
    if (kind === 'asphalt') {
      const compile = material.onBeforeCompile;
      material.onBeforeCompile = shader => {
        compile(shader);
        shader.fragmentShader = shader.fragmentShader.replace('#include <roughnessmap_fragment>', `#include <roughnessmap_fragment>
          float footprint = length(fwidth(terrainPoint.xz));
          float stones = mix(surfaceDetail(terrainPoint * 18.), .5, smoothstep(.025, .12, footprint));
          diffuseColor.rgb *= .76 + .22 * surfaceDetail(terrainPoint * 3.7) + .26 * stones;`);
      };
      material.customProgramCacheKey = () => 'yaesu-asphalt';
    }
    return [kind, material];
  }));
}

export function applyStreetSurface(object, tile, materials) {
  const geometry = object.geometry;
  const ids = geometry.getAttribute('_batchid');
  const positions = geometry.getAttribute('position');
  const lookup = streetTiles[tile.id];
  const channels = new Map([['original', []]]);
  const lifted = new Set(), drawn = new Set();
  const count = geometry.index?.count ?? positions.count;
  for (let i = 0; i < count; i += 3) {
    const face = [0, 1, 2].map(j => geometry.index?.getX(i + j) ?? i + j);
    const kind = lookup[ids.getX(face[0])] ?? 'original';
    if (kind === 'duplicate') continue;
    if (kind === 'asphalt') {
      // The same TrafficArea contains two coincident copies of every measured triangle.
      const key = face.map(j => [positions.getX(j), positions.getY(j), positions.getZ(j)].map(v => Math.round(v * 1000)).join('/')).sort().join('|');
      if (drawn.has(key)) continue;
      drawn.add(key);
    } else if (kind !== 'original') {
      for (const j of face) lifted.add(j);
    }
    if (!channels.has(kind)) channels.set(kind, []);
    channels.get(kind).push(...face);
  }
  if (lifted.size) {
    object.updateWorldMatrix(true, false);
    const up = new T.Vector3(0, 1, 0).transformDirection(object.matrixWorld.clone().invert()).multiplyScalar(roadLift);
    for (const j of lifted) positions.setXYZ(j, positions.getX(j) + up.x, positions.getY(j) + up.y, positions.getZ(j) + up.z);
    positions.needsUpdate = true;
    geometry.computeBoundingBox();
    geometry.computeBoundingSphere();
  }
  geometry.clearGroups();
  const indices = [], slots = [];
  for (const [kind, faces] of channels) {
    if (!faces.length) continue;
    geometry.addGroup(indices.length, faces.length, slots.length);
    slots.push(kind === 'original' ? object.material : materials[kind]);
    for (const index of faces) indices.push(index);
  }
  geometry.setIndex(indices);
  object.material = slots;
}
