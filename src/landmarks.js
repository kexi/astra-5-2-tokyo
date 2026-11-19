import { surfaceMaterial } from './materials.js';

export function finishLandmarks(group, anisotropy, cityFog) {
  group.traverse((object) => {
    if (!object.isMesh) return;
    object.castShadow = object.receiveShadow = true;
    object.layers.enable(1);
    const original = object.material;
    if (!original.map) return;
    original.map.anisotropy = Math.min(8, anisotropy);
    const material = surfaceMaterial('building', original.map);
    material.color.set('#f7f1e7');
    material.roughness = Math.max(0.53, original.roughness);
    material.metalness = Math.min(0.12, original.metalness);
    material.fog = cityFog;
    object.material = material;
  });
}

// The edited GLB replaces only its own building features within the source tiles.
export function removeLandmarks(geometry, batches) {
  const feature = geometry.getAttribute('_batchid');
  const index = geometry.index;
  const count = index?.count ?? feature.count;
  const retained = [];
  for (let i = 0; i < count; i += 3) {
    const first = index?.getX(i) ?? i;
    if (!batches.has(feature.getX(first))) {
      retained.push(first, index?.getX(i + 1) ?? i + 1, index?.getX(i + 2) ?? i + 2);
    }
  }
  if (retained.length !== count) geometry.setIndex(retained);
  return (count - retained.length) / 3;
}
