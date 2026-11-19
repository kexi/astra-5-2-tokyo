import * as T from 'three';
import { surfaceMaterial } from './materials.js';

const base = 'assets/buildings/';
async function checked(url, options) {
  const response = await fetch(base + url, options);
  if (!response.ok) throw new Error(`${url} (${response.status})`);
  return response;
}

async function inflate(bytes) {
  if (bytes[0] !== 0x1f || bytes[1] !== 0x8b) return new Uint8Array(bytes);
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

export async function buildingCatalog() {
  const response = await checked('catalog.json.gz');
  const bytes = await inflate(new Uint8Array(await response.arrayBuffer()));
  const catalog = JSON.parse(new TextDecoder().decode(bytes));
  if (catalog.version !== 1) throw new Error('建物索引の形式が一致しません');
  return catalog;
}

async function range(files, [file, start, length], signal) {
  const end = start + length - 1;
  const res = await checked(files[file], { headers: { Range: `bytes=${start}-${end}` }, signal });
  const bytes = new Uint8Array(await res.arrayBuffer());
  if (res.status === 206 && bytes.length === length) return bytes;
  if (res.status === 200 && end < bytes.length) return bytes.subarray(start, end + 1);
  throw new Error('建物データの取得範囲が一致しません');
}

async function geometryParts(zipped) {
  const { buffer } = await inflate(zipped);
  const header = new DataView(buffer);
  if (header.getUint32(0) !== 0x42435432) throw new Error('建物メッシュの形式が一致しません');
  const n = header.getUint32(4, true),
    count = header.getUint32(8, true);
  const Index = header.getUint32(12, true) === 2 ? Uint16Array : Uint32Array;
  let offset = 16;
  const take = (Type, length) => {
    const array = new Type(buffer, offset, length);
    offset += Math.ceil(array.byteLength / 4) * 4;
    return array;
  };
  return {
    pos: take(Float32Array, n * 3),
    norm: take(Int16Array, n * 3),
    uv: take(Uint16Array, n * 2),
    atlas: take(Uint16Array, n * 2),
    kind: take(Uint8Array, n),
    index: take(Index, count),
  };
}

function makeGeometry(data, vertexStart, vertexEnd, indexStart, indexEnd, overview) {
  const geo = new T.BufferGeometry();
  const attr = (values, components, normalized) =>
    new T.BufferAttribute(
      values.subarray(vertexStart * components, vertexEnd * components),
      components,
      normalized,
    );
  geo.setAttribute('position', attr(data.pos, 3, false));
  geo.setAttribute('finishKind', attr(data.kind, 1, false));
  geo.setAttribute('normal', attr(data.norm, 3, true));
  geo.setAttribute('uv', attr(overview ? data.atlas : data.uv, 2, true));
  const indices = data.index.subarray(indexStart, indexEnd);
  geo.setIndex(
    new T.BufferAttribute(vertexStart ? indices.map((i) => i - vertexStart) : indices, 1),
  );
  geo.computeBoundingBox();
  geo.computeBoundingSphere();
  return geo;
}

async function texture(bytes, anisotropy, maxSize = Infinity) {
  const url = URL.createObjectURL(new Blob([bytes], { type: 'image/webp' }));
  try {
    const map = await new T.TextureLoader().loadAsync(url);
    const image = map.image;
    const scale = maxSize / Math.max(image.width, image.height);
    if (scale < 1) {
      const reduced = document.createElement('canvas');
      reduced.width = Math.max(1, Math.round(image.width * scale));
      reduced.height = Math.max(1, Math.round(image.height * scale));
      reduced.getContext('2d').drawImage(image, 0, 0, reduced.width, reduced.height);
      map.image = reduced;
    }
    map.colorSpace = T.SRGBColorSpace;
    map.anisotropy = Math.min(8, anisotropy);
    return map;
  } finally {
    URL.revokeObjectURL(url);
  }
}

function finishPaint(map, cityFog) {
  const material = surfaceMaterial('building', map);
  material.color.set('#f7f1e7');
  material.roughness = 0.82;
  material.fog = cityFog;
  const compile = material.onBeforeCompile.bind(material);
  const cacheKey = material.customProgramCacheKey();
  material.customProgramCacheKey = () => cacheKey + '-building-paint';
  material.onBeforeCompile = (shader, renderer) => {
    compile(shader, renderer);
    shader.vertexShader =
      'attribute float finishKind; varying float vFinishKind; varying vec3 vFinishPoint;\n' +
      shader.vertexShader.replace(
        '#include <begin_vertex>',
        `#include <begin_vertex>
      vFinishKind = finishKind; vFinishPoint = position;`,
      );
    shader.fragmentShader =
      'varying float vFinishKind; varying vec3 vFinishPoint;\n' +
      shader.fragmentShader.replace(
        '#include <normal_fragment_maps>',
        `
      #include <normal_fragment_maps>
      float painted = step(.5, vFinishKind);
      float wallFinish = step(abs(vFinishKind - 1.), .1) + step(abs(vFinishKind - 4.), .1);
      float paneFinish = wallFinish * smoothstep(.012, .065, sampledDiffuseColor.b - sampledDiffuseColor.r);
      // The Blender shader uses 24 cycles/m and 0.45 mm relief. Fade subpixel grains at distance.
      vec3 freqA = vec3(91., 83., 72.), freqB = vec3(-73., 95., 82.);
      float pa = dot(vFinishPoint, freqA), pb = dot(vFinishPoint, freqB);
      float closeGrain = 1. - smoothstep(.012, .055, length(fwidth(vFinishPoint)));
      float grain = .5 + .25 * closeGrain * (sin(pa) + sin(pb));
      vec3 slope = .00045 * .25 * closeGrain * (cos(pa) * freqA + cos(pb) * freqB);
      vec3 grad = mat3(viewMatrix) * slope;
      normal = normalize(normal - painted * (1. - paneFinish) * (grad - normal * dot(normal, grad)));
      roughnessFactor = mix(roughnessFactor, mix(.67 + .19 * grain, .22, paneFinish), painted);
      `,
      );
  };
  return material;
}

export async function isolatedBuilding(catalog, tile, index, anisotropy, signal) {
  const row = tile.buildings[index];
  const [data, image] = await Promise.all([
    range(catalog.overview, tile.geometry, signal).then(geometryParts),
    range(catalog.photos, row[4], signal),
  ]);
  const previous = tile.buildings[index - 1];
  const geometry = makeGeometry(
    data,
    previous?.[2] ?? 0,
    row[2],
    previous?.[3] ?? 0,
    row[3],
    false,
  );
  return new T.Mesh(geometry, finishPaint(await texture(image, anisotropy), false));
}

export async function cityBuildings(catalog, anisotropy, onTile) {
  const loadPacks = (files) =>
    Promise.all(
      files.map(async (file) => new Uint8Array(await (await checked(file)).arrayBuffer())),
    );
  const [buffers, focus] = await Promise.all([
    loadPacks(catalog.overview),
    loadPacks(catalog.focus?.files ?? []),
  ]);
  const bytes = (pack, [file, start, length]) => pack[file].subarray(start, start + length);
  const maxFocusSize = matchMedia('(max-width: 700px)').matches ? 1024 : Infinity;
  const group = new T.Group();
  function addMesh(name, geometry, map) {
    const mesh = new T.Mesh(geometry, finishPaint(map, true));
    mesh.name = name;
    mesh.layers.enable(1);
    mesh.castShadow = mesh.receiveShadow = true;
    group.add(mesh);
  }
  let index = 0;
  async function worker() {
    while (index < catalog.tiles.length) {
      const tile = catalog.tiles[index++];
      const [data, map] = await Promise.all([
        geometryParts(bytes(buffers, tile.geometry)),
        texture(bytes(buffers, tile.atlas), anisotropy),
      ]);
      const remaining = [];
      let vertex = 0,
        indexStart = 0,
        kept = 0;
      for (const [id, , endVertex, endIndex] of tile.buildings) {
        const address = catalog.focus?.buildings[id];
        if (address) {
          const photo = await texture(bytes(focus, address), anisotropy, maxFocusSize);
          addMesh(id, makeGeometry(data, vertex, endVertex, indexStart, endIndex, false), photo);
        } else {
          remaining.push(data.index.subarray(indexStart, endIndex));
          kept += endIndex - indexStart;
        }
        vertex = endVertex;
        indexStart = endIndex;
      }
      if (kept) {
        const geometry = makeGeometry(data, 0, data.pos.length / 3, 0, data.index.length, true);
        if (kept < data.index.length) {
          const indices = new data.index.constructor(kept);
          let at = 0;
          for (const part of remaining) {
            indices.set(part, at);
            at += part.length;
          }
          geometry.setIndex(new T.BufferAttribute(indices, 1));
        }
        addMesh(tile.id, geometry, map);
      } else map.dispose();
      onTile(data.index.length / 3);
    }
  }
  await Promise.all(Array.from({ length: 6 }, worker));
  return group;
}
