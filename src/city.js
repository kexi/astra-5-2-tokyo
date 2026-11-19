import * as T from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { DRACOLoader, DRACO_GLTF_CONFIG } from 'three/addons/loaders/DRACOLoader.js';
import { surfaceMaterial, waterMaterial } from './materials.js';
import { applyStreetSurface, makeStreetMaterials, roadLift, streetTiles } from './street.js';
import { applyDeckSurface, deckTiles, loadDeckMaterial } from './expressway.js';
import { applyHedgeSurface, hedgeTile } from './hedges.js';
import { loadParkTrees } from './trees.js';
import { buildingCatalog, cityBuildings } from './building-assets.js';
import treeLayout from '../tools/tree-layout.json' with { type: 'json' };

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
  const l = T.MathUtils.degToRad(lon),
    p = T.MathUtils.degToRad(lat);
  const sl = Math.sin(l),
    cl = Math.cos(l),
    sp = Math.sin(p),
    cp = Math.cos(p);
  const radius = 6378137 / Math.sqrt(1 - 0.00669437999014 * sp ** 2);
  const center = new T.Vector3(
    (radius + alt) * cp * cl,
    (radius + alt) * cp * sl,
    (radius * (1 - 0.00669437999014) + alt) * sp,
  );
  // Plane IX local axes operate near zero after translation from ~6.4 million metres.
  const ecefToLocal = new T.Matrix4().set(
    -sl,
    cl,
    0,
    0,
    cp * cl,
    cp * sl,
    sp,
    0,
    sp * cl,
    sp * sl,
    -cp,
    0,
    0,
    0,
    0,
    1,
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

function mapper(frame, { zoom, bounds: [left, top, right, bottom] }) {
  const grid = 2 ** zoom;
  return (x, z) => {
    const [lon, lat] = frame.coordinates(x, z);
    return [
      (((lon + 180) / 360) * grid - left) / (right - left),
      (((1 - Math.asinh(Math.tan(T.MathUtils.degToRad(lat))) / Math.PI) * grid) / 2 - top) /
        (bottom - top),
    ];
  };
}

function elevationSampler(frame, layer, image) {
  const map = mapper(frame, layer);
  const bitmap = pixels(image, Infinity);
  const { width, height, data } = bitmap;
  function heightAt(x, y) {
    const index = (y * width + x) * 4;
    const raw = (data[index] << 16) + (data[index + 1] << 8) + data[index + 2];
    return raw === 0x800000 || !data[index + 3]
      ? null
      : (raw >= 0x800000 ? raw - 0x1000000 : raw) * 0.01;
  }
  return (x, z) => {
    const [u, v] = map(x, z);
    const edge = Math.min(u, v, 1 - u, 1 - v);
    if (edge < 0) return null;
    const px = T.MathUtils.clamp(u * width - 0.5, 0, width - 1);
    const py = T.MathUtils.clamp(v * height - 0.5, 0, height - 1);
    const i = Math.floor(px),
      j = Math.floor(py);
    const ni = Math.min(i + 1, width - 1),
      nj = Math.min(j + 1, height - 1);
    const fx = px - i,
      fy = py - j;
    const weighted = [
      [heightAt(i, j), (1 - fx) * (1 - fy)],
      [heightAt(ni, j), fx * (1 - fy)],
      [heightAt(i, nj), (1 - fx) * fy],
      [heightAt(ni, nj), fx * fy],
    ];
    let value = 0,
      total = 0;
    for (const [height, weight] of weighted) {
      if (height === null) continue;
      value += height * weight;
      total += weight;
    }
    if (!total) return null;
    return { value: value / total, opacity: T.MathUtils.smoothstep(edge, 0, 0.028) * total };
  };
}

function sampleGrid(from, to, fineFrom, fineTo) {
  const axis = [];
  for (let point = from; point < to;) {
    axis.push(point);
    const step = point >= fineFrom && point < fineTo ? 2.5 : 16;
    const boundary = point < fineFrom ? fineFrom : point < fineTo ? fineTo : to;
    point = Math.min(point + step, boundary);
  }
  axis.push(to);
  return axis;
}

function lowerIndex(axis, value) {
  let left = 0,
    right = axis.length - 1;
  while (left < right) {
    const middle = (left + right) >> 1;
    if (axis[middle] < value) left = middle + 1;
    else right = middle;
  }
  return left;
}

function projectedTexture(frame, layer) {
  const map = mapper(frame, layer);
  const [u0, v0] = map(0, 0);
  const [u1, v1] = map(1000, 1000);
  return new T.Vector4((u1 - u0) / 1000, (v0 - v1) / 1000, u0, 1 - v0);
}

async function groundModel(manifest, frame, photo, elevations, overlays, geoid) {
  const uv = mapper(frame, manifest.photo);
  const samplers = elevations.map(({ layer, texture }) =>
    elevationSampler(frame, layer, texture.image),
  );
  const groundHeight = (x, z) => {
    let value = 1.5;
    for (let i = samplers.length - 1; i >= 0; i--) {
      const sample = samplers[i](x, z);
      if (!sample) continue;
      const amount = i === samplers.length - 1 ? 1 : sample.opacity;
      value = T.MathUtils.lerp(value, sample.value, amount);
    }
    const bulge = (x * x + z * z) / (2 * 6371000);
    return value + geoid - manifest.origin[2] - bulge - 0.78;
  };

  const ground = new T.Group();
  const extent = 2770;
  const xs = sampleGrid(-extent, extent, -910, 610);
  const zs = sampleGrid(-extent, extent, -550, 540);
  const columns = xs.length,
    rows = zs.length;
  const land = new T.PlaneGeometry(2 * extent, 2 * extent, columns - 1, rows - 1).rotateX(
    -Math.PI / 2,
  );
  const { position, uv: coords } = land.attributes;
  for (let row = 0; row < rows; row++) {
    if (row && row % 24 === 0) {
      await (globalThis.scheduler?.yield?.() ?? new Promise((resolve) => setTimeout(resolve, 0)));
    }
    for (let col = 0; col < columns; col++) {
      const index = row * columns + col;
      const x = xs[col],
        z = zs[row];
      const [u, v] = uv(x, z);
      position.setXYZ(index, x, groundHeight(x, z), z);
      coords.setXY(index, u, 1 - v);
    }
  }
  const mesh = new T.Mesh(land, surfaceMaterial('ground', photo, null, overlays));
  mesh.receiveShadow = true;
  ground.add(mesh);

  // The DEM interpolates across the moats. Lower the terrain cells covered by
  // the surveyed water polygons so their actual water levels remain visible.
  function carveWaterbed(object) {
    const vertices = object.geometry.attributes.position;
    const indices = object.geometry.index;
    const count = indices?.count ?? vertices.count;
    const a = new T.Vector3(),
      b = new T.Vector3(),
      c = new T.Vector3();
    const point = (target, i) =>
      target
        .fromBufferAttribute(vertices, indices ? indices.getX(i) : i)
        .applyMatrix4(object.matrixWorld);
    for (let i = 0; i < count; i += 3) {
      point(a, i);
      point(b, i + 1);
      point(c, i + 2);
      const denominator = (b.z - c.z) * (a.x - c.x) + (c.x - b.x) * (a.z - c.z);
      if (Math.abs(denominator) < 0.001) continue;
      const minCol = Math.max(0, lowerIndex(xs, Math.min(a.x, b.x, c.x)) - 1);
      const maxCol = lowerIndex(xs, Math.max(a.x, b.x, c.x));
      const minRow = Math.max(0, lowerIndex(zs, Math.min(a.z, b.z, c.z)) - 1);
      const maxRow = lowerIndex(zs, Math.max(a.z, b.z, c.z));
      const inverse = 1 / Math.abs(denominator);
      const edgeA = (Math.abs(b.z - c.z) + Math.abs(c.x - b.x)) * inverse;
      const edgeB = (Math.abs(c.z - a.z) + Math.abs(a.x - c.x)) * inverse;
      const edgeC = (Math.abs(a.z - b.z) + Math.abs(b.x - a.x)) * inverse;
      const bed = Math.min(a.y, b.y, c.y) - 0.75;
      for (let row = minRow; row <= maxRow; row++) {
        for (let col = minCol; col <= maxCol; col++) {
          const x = xs[col] - c.x,
            z = zs[row] - c.z;
          const pad =
            0.72 * Math.max(xs[col] - xs[Math.max(0, col - 1)], zs[row] - zs[Math.max(0, row - 1)]);
          const u = ((b.z - c.z) * x + (c.x - b.x) * z) / denominator;
          const v = ((c.z - a.z) * x + (a.x - c.x) * z) / denominator;
          if (u < -pad * edgeA || v < -pad * edgeB || 1 - u - v < -pad * edgeC) continue;
          const index = row * columns + col;
          position.setY(index, Math.min(position.getY(index), bed));
        }
      }
    }
  }
  return { ground, groundHeight, carveWaterbed, land };
}

function parkSites(groundHeight, heights, surveyedTrees, photographedTrees) {
  const tree = new T.Object3D();
  return [...treeLayout, ...photographedTrees].flatMap(
    ([x, north, scale, angle, estimatedHeight, , area], index) => {
      const overlaps = surveyedTrees.some(
        ([east, y, radius]) => Math.hypot(east - x, y - north) < Math.max(2.1, radius * 0.8),
      );
      if (overlaps) return [];
      const species = index % 2;
      const modelHeight = species === 0 ? 9.4745 : 9.204;
      const measured = heights[index];
      const treeHeight = measured ?? estimatedHeight ?? modelHeight * scale;
      tree.position.set(x, groundHeight(x, -north), -north);
      tree.scale.set(scale, treeHeight / modelHeight, scale);
      tree.rotation.y = angle;
      tree.updateMatrix();
      const tone = 0.84 + 0.16 * (0.5 + 0.5 * Math.sin(index * 2.39996));
      const color = new T.Color(0.94 * tone, tone, 0.86 * tone);
      if (area) {
        const olive = area === 'lawn' ? 0.64 : 0.77;
        color.r *= olive;
        color.g *= olive * 0.8;
        color.b *= olive * 0.93;
      }
      const center = tree.position.clone();
      center.y += treeHeight * 0.52;
      return [
        {
          matrix: tree.matrix.clone(),
          color,
          species,
          center,
          radius: Math.max(8.8 * scale, treeHeight * 0.64),
        },
      ];
    },
  );
}

export async function loadCity(progress, anisotropy) {
  const [manifest, survey, railway, parkGround, catalog] = await Promise.all([
    fetchAsset('manifest.json', 'json'),
    fetchAsset('survey.json', 'json'),
    fetchAsset('rail-reference.json', 'json'),
    fetchAsset('park-ground.json', 'json'),
    buildingCatalog(),
  ]);
  const frame = localFrame(manifest.origin);
  const textureLoader = new T.TextureLoader();
  async function loadLayer(layer) {
    return { layer, texture: await textureLoader.loadAsync('assets/' + layer.file) };
  }
  const [archives, basePhoto, photoLayers, elevations, treeHeights] = await Promise.all([
    Promise.all(catalog.infrastructure.files.map((file) => cityArchive('buildings/' + file))),
    loadLayer(manifest.photo),
    Promise.all([parkGround.layer, ...survey.photos].map(loadLayer)),
    Promise.all([...survey.elevations, manifest.elevation].map(loadLayer)),
    fetchAsset(survey.trees.file, 'json'),
  ]);
  for (const { texture } of [basePhoto, ...photoLayers]) {
    texture.colorSpace = T.SRGBColorSpace;
    texture.anisotropy = Math.min(12, anisotropy);
  }
  const overlays = photoLayers.map(({ layer, texture }) => ({
    texture,
    transform: projectedTexture(frame, layer),
    parkSurface: layer.file === parkGround.layer.file,
  }));
  const photo = basePhoto.texture;
  const geoid = railway.verticalDatum.geoidHeight;
  const { ground, groundHeight, carveWaterbed, land } = await groundModel(
    manifest,
    frame,
    photo,
    elevations,
    overlays,
    geoid,
  );
  const draco = new DRACOLoader().setDecoderPath(DRACO_GLTF_CONFIG).setWorkerLimit(6);
  const loader = new GLTFLoader().setDRACOLoader(draco);
  const sites = parkSites(
    groundHeight,
    treeHeights.heights,
    manifest.surveyedTrees ?? [],
    parkGround.trees,
  );
  const park = loadParkTrees(ground, sites, loader);
  const root = new T.Group();
  const buildings = new T.Group();
  root.add(ground, buildings);
  const rooftop = loader.loadAsync('assets/marunouchi-nichome.glb').then(({ scene }) => {
    scene.traverse((object) => {
      if (!object.isMesh) return;
      object.castShadow = object.receiveShadow = true;
      object.layers.enable(1);
      if (object.material.map) object.material.map.anisotropy = Math.min(8, anisotropy);
    });
    buildings.add(scene);
  });
  const streetTreeScene = await loader.loadAsync('assets/yaesu-street-trees.glb');
  const deckMaterial = await loadDeckMaterial(textureLoader, anisotropy);
  const streetMaterials = makeStreetMaterials();
  const hedgeLeaves = streetTreeScene.scene.getObjectByProperty('isMesh', true).material.map;
  const streetTrees = new Map(
    streetTreeScene.scene.children.map((group) => [group.userData.sourceTile, group]),
  );
  const rail = loader.loadAsync('assets/rail-model.glb').then((gltf) => {
    gltf.scene.traverse((object) => {
      if (!object.isMesh) return;
      const original = object.material;
      const material = surfaceMaterial('furniture');
      material.color.copy(original.color);
      material.roughness = original.roughness;
      material.metalness = original.metalness;
      object.material = material;
      object.receiveShadow = true;
    });
    root.add(gltf.scene);
    return gltf.parser.json.extras.trackIds.length;
  });
  const water = waterMaterial();
  const roads = surfaceMaterial('road', photo, projectedTexture(frame, manifest.photo), overlays);
  const grass = surfaceMaterial('grass');
  const leafMesh = surfaceMaterial('leaf');
  roads.polygonOffset = true;
  roads.polygonOffsetFactor = -1;
  let done = 0,
    next = 0,
    triangles = 0,
    textured = 0;
  const failures = [];

  const infrastructure = manifest.tiles.filter((tile) => tile.kind !== 'building');
  function countTile() {
    if (++done % 24 === 0 || done === manifest.tiles.length) progress(done, manifest.tiles.length);
  }
  const city = cityBuildings(catalog, anisotropy, (faces) => {
    triangles += faces;
    textured++;
    countTile();
  }).then((group) => buildings.add(group));

  async function worker() {
    while (next < infrastructure.length) {
      const tile = infrastructure[next++];
      try {
        let group = streetTrees.get(tile.id);
        if (!group) {
          const [archive, offset, length] = catalog.infrastructure.tiles[tile.id];
          const bytes = archives[archive].slice(offset, offset + length);
          group = (await loader.parseAsync(bytes, '')).scene;
          frame.place(group, tile.center);
        }
        let mapped = false;
        group.traverse((object) => {
          if (!object.isMesh) return;
          object.castShadow = ['tree', 'bridge', 'furniture'].includes(tile.kind);
          object.receiveShadow = tile.kind !== 'water';
          const conventional = { road: roads, grass, water, tree: leafMesh }[tile.kind];
          const original = object.material;
          const isInfrastructure = tile.kind === 'bridge' || tile.kind === 'furniture';
          if (
            isInfrastructure ||
            (tile.kind !== 'road' &&
              tile.kind !== 'water' &&
              (original.map || original.vertexColors))
          ) {
            const kind = isInfrastructure ? tile.kind : tile.kind === 'grass' ? 'grass' : 'leaf';
            const material = surfaceMaterial(kind, original.map);
            if (original.map) {
              material.color.set('#f3efe5');
              original.map.anisotropy = Math.min(anisotropy, 8);
              mapped = true;
            } else if (original.vertexColors || original.color?.getHex() !== 0xffffff) {
              material.color.copy(original.color);
            }
            material.vertexColors = original.vertexColors;
            object.material = material;
          } else {
            object.material = conventional;
          }
          if (!object.geometry.hasAttribute('normal')) object.geometry.computeVertexNormals();
          if (tile.id in streetTiles) applyStreetSurface(object, tile, streetMaterials);
          if (tile.id in deckTiles) applyDeckSurface(object, tile, deckMaterial);
          if (tile.id === hedgeTile) applyHedgeSurface(object, hedgeLeaves);
          triangles +=
            (object.geometry.index?.count ?? object.geometry.attributes.position.count) / 3;
        });
        textured += Number(mapped);
        if (tile.kind === 'water') group.position.y += 0.7;
        if (tile.kind === 'road') group.position.y += roadLift;
        root.add(group);
      } catch (error) {
        failures.push(tile.id);
        console.error(tile.id, error);
      }
      countTile();
    }
  }
  const [trees, tracks] = await Promise.all([
    park,
    rail,
    rooftop,
    city,
    ...Array.from({ length: 12 }, worker),
  ]);
  draco.dispose();
  if (failures.length) throw new Error(`${failures.length}件の都市タイルを読み込めませんでした`);
  root.updateMatrixWorld(true);
  root.traverse((object) => {
    if (object.material === water) carveWaterbed(object);
  });
  land.attributes.position.needsUpdate = true;
  land.computeVertexNormals();
  return {
    root,
    buildings,
    water,
    trees,
    counts: {
      tiles: done,
      textured,
      triangles,
      buildingCount: catalog.count,
      landmarks: catalog.landmarks,
      trees: trees.count,
      surveyedTrees: manifest.surveyedTrees.length,
      tracks,
    },
  };
}
