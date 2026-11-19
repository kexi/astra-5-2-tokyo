import * as T from 'three';
import settings from '../tools/nihonbashi-expressway.json' with { type: 'json' };
import { surfaceMaterial } from './materials.js';

export const deckTiles = settings.tiles;

export async function loadDeckMaterial(loader, anisotropy) {
  const map = await loader.loadAsync('assets/' + settings.file);
  map.colorSpace = T.SRGBColorSpace;
  map.channel = 1;
  map.anisotropy = Math.min(8, anisotropy);
  const material = surfaceMaterial('bridge', map);
  material.color.setRGB(.96, .965, .99);
  material.roughness = .91;
  material.polygonOffset = true;
  material.polygonOffsetFactor = -1;
  return material;
}

export function applyDeckSurface(object, tile, material) {
  const source = object.geometry;
  const batches = source.getAttribute('_batchid');
  const vertices = source.getAttribute('position');
  const selected = new Set(deckTiles[tile.id]);
  const [west, south, east, north] = settings.bounds;
  const ordinary = [], pavement = [];
  const points = [new T.Vector3(), new T.Vector3(), new T.Vector3()];
  const normal = new T.Vector3();
  const uv = new T.Float32BufferAttribute(new Float32Array(vertices.count * 2), 2);
  const count = source.index?.count ?? vertices.count;
  object.updateWorldMatrix(true, false);
  for (let i = 0; i < count; i += 3) {
    const face = [0, 1, 2].map(j => source.index?.getX(i + j) ?? i + j);
    if (!selected.has(batches.getX(face[0]))) {
      ordinary.push(...face);
      continue;
    }
    points.forEach((point, j) => point.fromBufferAttribute(vertices, face[j]).applyMatrix4(object.matrixWorld));
    T.Triangle.getNormal(...points, normal);
    const elevated = tile.kind !== 'bridge' || points.every(p => p.y >= settings.bridgeDeckHeight);
    const atRiver = !points.every(p => p.x < west) && !points.every(p => p.x > east) &&
      !points.every(p => -p.z < south) && !points.every(p => -p.z > north);
    const isPavement = elevated && atRiver && normal.y > settings.minimumUpNormal;
    (isPavement ? pavement : ordinary).push(...face);
    if (!isPavement) continue;
    points.forEach((p, j) => uv.setXY(face[j], (p.x - west) / (east - west), (-p.z - south) / (north - south)));
  }
  if (!pavement.length) return;
  // Deck UVs have their own channel because the original atlas is still used on adjoining girders.
  source.setAttribute('uv1', uv);
  source.setIndex(ordinary.concat(pavement));
  source.clearGroups();
  if (ordinary.length) source.addGroup(0, ordinary.length, 0);
  source.addGroup(ordinary.length, pavement.length, 1);
  object.material = [object.material, material];
}
