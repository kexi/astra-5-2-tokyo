import * as T from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { DRACOLoader, DRACO_GLTF_CONFIG } from 'three/addons/loaders/DRACOLoader.js';
import { surfaceMaterial, waterMaterial } from './materials.js';

async function fetchAsset(file, format) {
  const response = await fetch('assets/' + file);
  if (!response.ok) throw new Error(`${file} (${response.status})`);
  return response[format]();
}

async function cityArchive(file) {
  const buffer = await fetchAsset(file, 'arrayBuffer');
  const header = new Uint8Array(buffer, 0, 2);
  if (header[0] !== 0x1f || header[1] !== 0x8b) return buffer;
  const bytes = new Blob([buffer]).stream();
  const decompressed = bytes.pipeThrough(new DecompressionStream('gzip'));
  return new Response(decompressed).arrayBuffer();
}

function localFrame([lon, lat, alt]) {
  const l = T.MathUtils.degToRad(lon), p = T.MathUtils.degToRad(lat);
  const sl = Math.sin(l), cl = Math.cos(l), sp = Math.sin(p), cp = Math.cos(p);
  const radius = 6378137 / Math.sqrt(1 - .00669437999014 * sp ** 2);
  const center = new T.Vector3((radius + alt) * cp * cl, (radius + alt) * cp * sl,
    (radius * (1 - .00669437999014) + alt) * sp);
  // Plane IX local axes operate near zero after translation from ~6.4 million metres.
  const ecefToLocal = new T.Matrix4().set(
    -sl, cl, 0, 0,
    cp * cl, cp * sl, sp, 0,
    sp * cl, sp * sl, -cp, 0,
    0, 0, 0, 1,
  );
  const gltfToLocal = ecefToLocal.clone().multiply(new T.Matrix4().makeRotationX(Math.PI / 2));
  const rotation = new T.Quaternion().setFromRotationMatrix(gltfToLocal);
  return {
    place(group, tileCenter) {
      group.position.fromArray(tileCenter).sub(center).applyMatrix4(ecefToLocal);
      group.quaternion.copy(rotation);
    },
    coordinates(x, z) {
      return [lon + x / 90528, lat - z / 110953];
    },
  };
}

function pixels(image, maxSize) {
  const canvas = document.createElement('canvas');
  const scale = Math.min(1, maxSize / Math.max(image.width, image.height));
  canvas.width = Math.round(image.width * scale);
  canvas.height = Math.round(image.height * scale);
  const context = canvas.getContext('2d', { willReadFrequently: true });
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  const { width, height, data } = context.getImageData(0, 0, canvas.width, canvas.height);
  return { width, height, data };
}

function bitmapOffset(bitmap, u, v) {
  const x = T.MathUtils.clamp(Math.floor(u * bitmap.width), 0, bitmap.width - 1);
  const y = T.MathUtils.clamp(Math.floor(v * bitmap.height), 0, bitmap.height - 1);
  return (y * bitmap.width + x) * 4;
}

function mapper(frame, { zoom, bounds: [left, top, right, bottom] }) {
  const grid = 2 ** zoom;
  return (x, z) => {
    const [lon, lat] = frame.coordinates(x, z);
    return [((lon + 180) / 360 * grid - left) / (right - left),
      ((1 - Math.asinh(Math.tan(T.MathUtils.degToRad(lat))) / Math.PI) * grid / 2 - top) / (bottom - top)];
  };
}

function groundModel(manifest, frame, photo, dem) {
  const uv = mapper(frame, manifest.photo);
  const elevationUV = mapper(frame, manifest.elevation);
  const elevations = pixels(dem.image, Infinity);
  const groundHeight = (x, z) => {
    const [u, v] = elevationUV(x, z);
    const index = bitmapOffset(elevations, u, v);
    const row = elevations.data;
    const raw = (row[index] << 16) + (row[index + 1] << 8) + row[index + 2];
    const value = raw === 0x800000 ? 1.5 : (raw >= 0x800000 ? raw - 0x1000000 : raw) * .01;
    const bulge = (x * x + z * z) / (2 * 6371000);
    return value + 36.6 - manifest.origin[2] - bulge - .78;
  };

  const ground = new T.Group();
  const extent = 2770;
  const land = new T.PlaneGeometry(2 * extent, 2 * extent, 370, 370).rotateX(-Math.PI / 2);
  const { position, uv: coords } = land.attributes;
  for (let i = 0; i < position.count; i++) {
    const x = position.getX(i), z = position.getZ(i);
    const [u, v] = uv(x, z);
    position.setY(i, groundHeight(x, z));
    coords.setXY(i, u, 1 - v);
  }
  land.computeVertexNormals();
  const mesh = new T.Mesh(land, surfaceMaterial('ground', photo));
  mesh.receiveShadow = true;
  ground.add(mesh);

  // The DEM interpolates across the moats. Lower the terrain cells covered by
  // the surveyed water polygons so their actual water levels remain visible.
  function carveWaterbed(object) {
    const vertices = object.geometry.attributes.position;
    const indices = object.geometry.index;
    const count = indices?.count ?? vertices.count;
    const { widthSegments, heightSegments } = land.parameters;
    const cell = 2 * extent / widthSegments;
    const a = new T.Vector3(), b = new T.Vector3(), c = new T.Vector3();
    const point = (target, i) => target.fromBufferAttribute(vertices, indices ? indices.getX(i) : i)
      .applyMatrix4(object.matrixWorld);
    for (let i = 0; i < count; i += 3) {
      point(a, i);
      point(b, i + 1);
      point(c, i + 2);
      const denominator = (b.z-c.z)*(a.x-c.x) + (c.x-b.x)*(a.z-c.z);
      if (Math.abs(denominator) < .001) continue;
      const minCol = Math.max(0, Math.floor((Math.min(a.x,b.x,c.x) + extent) / cell));
      const maxCol = Math.min(widthSegments, Math.ceil((Math.max(a.x,b.x,c.x) + extent) / cell));
      const minRow = Math.max(0, Math.floor((Math.min(a.z,b.z,c.z) + extent) / cell));
      const maxRow = Math.min(heightSegments, Math.ceil((Math.max(a.z,b.z,c.z) + extent) / cell));
      const padA = .8 * cell * (Math.abs(b.z-c.z)+Math.abs(c.x-b.x)) / Math.abs(denominator);
      const padB = .8 * cell * (Math.abs(c.z-a.z)+Math.abs(a.x-c.x)) / Math.abs(denominator);
      const padC = .8 * cell * (Math.abs(a.z-b.z)+Math.abs(b.x-a.x)) / Math.abs(denominator);
      const bed = Math.min(a.y, b.y, c.y) - .75;
      for (let row = minRow; row <= maxRow; row++) {
        for (let col = minCol; col <= maxCol; col++) {
          const x = col * cell - extent - c.x, z = row * cell - extent - c.z;
          const u = ((b.z-c.z)*x + (c.x-b.x)*z) / denominator;
          const v = ((c.z-a.z)*x + (a.x-c.x)*z) / denominator;
          if (u < -padA || v < -padB || 1-u-v < -padC) continue;
          const index = row * (widthSegments + 1) + col;
          position.setY(index, Math.min(position.getY(index), bed));
        }
      }
    }
  }
  return { ground, groundHeight, uv, carveWaterbed, land };
}

function addParkTrees(parent, groundHeight, uv, photo) {
  const bitmap = pixels(photo.image, 2500);
  const foliage = new T.IcosahedronGeometry(1, 1);
  const leafPoints = foliage.attributes.position;
  for (let i = 0; i < leafPoints.count; i++) {
    const x = leafPoints.getX(i), y = leafPoints.getY(i), z = leafPoints.getZ(i);
    const offset = .86 + .18 * Math.sin(x * 10.2 + z * 9.2) * Math.cos(y * 6.3);
    leafPoints.setXYZ(i, x * offset, y * offset, z * offset);
  }
  foliage.computeVertexNormals();
  const leaves = new T.InstancedMesh(foliage, surfaceMaterial('leaf'), 3200);
  const dummy = new T.Object3D();
  const color = new T.Color();
  let count = 0, seed = 156813;
  const rand = () => {
    seed = (Math.imul(1664525, seed) + 1013904223) >>> 0;
    return seed / 2 ** 32;
  };
  for (let z = -1450; z < 1150; z += 8.4) {
    for (let x = -1960; x < -350; x += 8.4) {
      const tx = x + (rand() - .5) * 8, tz = z + (rand() - .5) * 8;
      const [u, v] = uv(tx, tz);
      const index = bitmapOffset(bitmap, u, v);
      const [r, g, b] = bitmap.data.subarray(index, index + 3);
      if (g < r * 1.085 || g < b * 1.19 || g < 26 || g > 140 || count >= leaves.instanceMatrix.count) continue;
      const scale = 2.9 + rand() * 3;
      dummy.position.set(tx, groundHeight(tx, tz) + scale * 1.2, tz);
      dummy.scale.set(scale, scale * (1.06 + rand() * .3), scale * (.85 + rand() * .24));
      dummy.rotation.y = rand() * Math.PI;
      dummy.updateMatrix();
      leaves.setMatrixAt(count, dummy.matrix);
      color.setHSL(.17 + rand() * .075, .18 + rand() * .18, .47 + rand() * .26);
      leaves.setColorAt(count++, color);
    }
  }
  leaves.count = count;
  leaves.castShadow = true;
  leaves.receiveShadow = true;
  parent.add(leaves);
  return count;
}

export async function loadCity(progress, anisotropy) {
  const manifest = await fetchAsset('manifest.json', 'json');
  const frame = localFrame(manifest.origin);
  const textureLoader = new T.TextureLoader();
  const [archives, photo, dem] = await Promise.all([
    Promise.all(manifest.archives.map(cityArchive)),
    textureLoader.loadAsync('assets/' + manifest.photo.file),
    textureLoader.loadAsync('assets/' + manifest.elevation.file),
  ]);
  photo.colorSpace = T.SRGBColorSpace;
  photo.anisotropy = Math.min(12, anisotropy);
  const { ground, groundHeight, uv, carveWaterbed, land } = groundModel(manifest, frame, photo, dem);
  const trees = addParkTrees(ground, groundHeight, uv, photo);
  const draco = new DRACOLoader().setDecoderPath(DRACO_GLTF_CONFIG).setWorkerLimit(6);
  const loader = new GLTFLoader().setDRACOLoader(draco);
  const root = new T.Group();
  const buildings = new T.Group();
  root.add(ground, buildings);
  const water = waterMaterial();
  const [u0, v0] = uv(0, 0);
  const [u1, v1] = uv(1000, 1000);
  const roads = surfaceMaterial('road', photo, new T.Vector4((u1-u0)/1000, (v0-v1)/1000, u0, 1-v0));
  const grass = surfaceMaterial('grass');
  const clay = surfaceMaterial('building');
  const leafMesh = surfaceMaterial('leaf');
  roads.polygonOffset = true;
  roads.polygonOffsetFactor = -1;
  let done = 0, next = 0, triangles = 0, textured = 0;
  const failures = [];

  async function worker() {
    while (next < manifest.tiles.length) {
      const tile = manifest.tiles[next++];
      try {
        const bytes = archives[tile.archive].slice(tile.offset, tile.offset + tile.length);
        const gltf = await loader.parseAsync(bytes, '');
        const group = gltf.scene;
        frame.place(group, tile.center);
        const isBuilding = tile.kind === 'building';
        let mapped = false;
        group.traverse(object => {
          if (!object.isMesh) return;
          triangles += (object.geometry.index?.count ?? object.geometry.attributes.position.count) / 3;
          object.castShadow = isBuilding || tile.kind === 'tree';
          object.receiveShadow = tile.kind !== 'water';
          if (isBuilding) {
            object.layers.enable(1);
            const original = object.material;
            if (original.map) {
              original.map.anisotropy = Math.min(anisotropy, 8);
              const material = surfaceMaterial('building', original.map);
              material.color.set('#f7f1e7');
              material.roughness = Math.max(.53, original.roughness ?? .82);
              material.metalness = Math.min(.12, original.metalness ?? 0);
              if (!object.geometry.hasAttribute('normal')) object.geometry.computeVertexNormals();
              object.material = material;
              mapped = true;
            } else {
              if (!object.geometry.hasAttribute('normal')) object.geometry.computeVertexNormals();
              object.material = clay;
            }
          } else {
            object.material = { road: roads, grass, water, tree: leafMesh }[tile.kind];
            if (!object.geometry.hasAttribute('normal')) object.geometry.computeVertexNormals();
          }
        });
        textured += Number(mapped);
        if (tile.kind === 'water') group.position.y += .70;
        if (tile.kind === 'road') group.position.y += .08;
        (isBuilding ? buildings : root).add(group);
      } catch (error) {
        failures.push(tile.id);
        console.error(tile.id, error);
      }
      if (++done % 24 === 0 || done === manifest.tiles.length) progress(done, manifest.tiles.length);
    }
  }
  await Promise.all(Array.from({ length: 12 }, worker));
  draco.dispose();
  if (failures.length) throw new Error(`${failures.length}件の都市タイルを読み込めませんでした`);
  root.updateMatrixWorld(true);
  root.traverse(object => {
    if (object.material === water) carveWaterbed(object);
  });
  land.attributes.position.needsUpdate = true;
  land.computeVertexNormals();
  return { root, buildings, water, counts: { tiles: done, textured, triangles, trees } };
}
