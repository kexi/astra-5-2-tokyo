import * as T from 'three';
import { mist, sun, surfaceMaterial } from './materials.js';

const levels = [
  { name: 'near', enter: 0, leave: 25, feather: 4 },
  { name: 'mid', enter: 25, leave: 66, feather: 9 },
];
const species = ['Zelkova', 'Camphor'];
const screenNoise = `
  float screenNoise() {
    uvec2 pixel = uvec2(gl_FragCoord.xy);
    uint h = pixel.x * 1597334677u ^ pixel.y * 3812015801u;
    h ^= h >> 16u;
    h *= 2146121005u;
    h ^= h >> 15u;
    return float(h) / 4294967295.;
  }`;

function specimenMaterial(isLeaf, level, clock) {
  const material = surfaceMaterial(isLeaf ? 'leaf' : 'trunk');
  material.color.set(isLeaf ? '#809a56' : '#786553');
  material.vertexColors = isLeaf;
  material.roughness = isLeaf ? 0.61 : 0.92;
  const compileSurface = material.onBeforeCompile;
  const previous = levels[levels.indexOf(level) - 1];
  material.onBeforeCompile = (shader) => {
    compileSurface(shader);
    shader.uniforms.treeTime = clock;
    shader.uniforms.lodRange = {
      value: new T.Vector4(
        level.enter,
        previous?.feather ?? 0,
        Number.isFinite(level.leave) ? level.leave : 1e6,
        level.feather,
      ),
    };
    shader.vertexShader =
      `
      varying vec3 treePoint;
      varying vec2 bladeUV;
      varying float treeDistance;
      uniform float treeTime;
    ` +
      shader.vertexShader.replace(
        '#include <begin_vertex>',
        `
      #include <begin_vertex>
      treePoint = position;
      bladeUV = uv;
      vec3 rootPosition = instanceMatrix[3].xyz;
      treeDistance = length(cameraPosition - (rootPosition + vec3(0.,4.7*length(instanceMatrix[1].xyz),0.)));
      float gust = treeTime*1.37 + dot(rootPosition.xz,vec2(.049,.024));
      float flexibility = pow(clamp(position.y / 9.,0.,1.25),1.76);
      transformed.x += flexibility*(.085*sin(gust)+.037*sin(gust*1.83+position.z*.68));
      transformed.z += flexibility*.051*cos(gust*.77+position.x*.31);
      ${isLeaf ? `transformed.y += .027*bladeUV.y*sin(treeTime*3.1+1.87*treePoint.x+.87*treePoint.z);` : ''}
    `,
      );
    shader.fragmentShader =
      `
      varying vec3 treePoint;
      varying vec2 bladeUV;
      varying float treeDistance;
      uniform vec4 lodRange;
      ${screenNoise}
    ` +
      shader.fragmentShader.replace(
        '#include <color_fragment>',
        `
      #include <color_fragment>
      float dither = screenNoise();
      if (lodRange.y > 0. && dither >= smoothstep(lodRange.x-lodRange.y,lodRange.x+lodRange.y,treeDistance)) discard;
      if (lodRange.w > 0. && dither < smoothstep(lodRange.z-lodRange.w,lodRange.z+lodRange.w,treeDistance)) discard;
      ${
        isLeaf
          ? `
        float across = abs(2.*bladeUV.x-1.);
        float width = max(fwidth(across),.014);
        float midrib = 1.-smoothstep(.014-width,.034+width,across);
        float veins = abs(sin(3.14159*(7.5*bladeUV.y - (1.02*across-.30*across*across))));
        float vein = (1.-smoothstep(.04,.19+fwidth(veins),veins))*(1.-across*across);
        float resolved = 1.-smoothstep(.025,.14,length(fwidth(bladeUV)));
        diffuseColor.rgb *= .94 + resolved*(.29*midrib+.11*vein);
      `
          : `
        float angle = atan(treePoint.z, treePoint.x);
        float split = sin(53.*angle + 3.*sin(13.*angle + treePoint.y*1.57)
                         + 1.6*sin(5.*treePoint.y + angle*7.));
        float resolved = 1.-smoothstep(.015,.075,length(fwidth(treePoint)));
        diffuseColor.rgb *= 1.-.33*smoothstep(.42,.91,split)*resolved;
      `
      }
    `,
      );
    if (isLeaf) {
      shader.fragmentShader = shader.fragmentShader.replace(
        '#include <opaque_fragment>',
        `
        vec3 toEye = normalize(cameraPosition-terrainPoint);
        float againstSun = pow(max(dot(-toEye,sunDirection),0.),3.2);
        float edge = 1.-abs(dot(normal,normalize(vViewPosition)));
        outgoingLight += diffuseColor.rgb * vec3(1.36,1.12,.54) * againstSun * (.33+.58*edge);
        #include <opaque_fragment>`,
      );
    }
  };
  material.customProgramCacheKey = () => `park-${level.name}-${isLeaf ? 'leaf' : 'bark'}`;
  return material;
}

function atlasMaterial(texture, live, level) {
  texture.colorSpace = T.SRGBColorSpace;
  return new T.ShaderMaterial({
    lights: true,
    uniforms: {
      ...T.UniformsUtils.clone(T.UniformsLib.lights),
      canopyMap: { value: texture },
      live,
      fadeStart: { value: level.leave - level.feather },
      fadeEnd: { value: level.leave + level.feather },
      sunDirection: { value: sun },
      mist: { value: mist },
    },
    vertexShader: `
      #include <common>
      #include <shadowmap_pars_vertex>
      uniform vec3 sunDirection;
      varying vec2 tileUV, atlasView;
      varying vec3 worldPoint, tint;
      varying float metres, depth;
      void main() {
        vec3 root = instanceMatrix[3].xyz;
        float scale = length(instanceMatrix[1].xyz);
        vec3 center = root + vec3(0.,4.65*scale,0.);
        vec3 observer = cameraPosition-center;
        metres = length(observer);
        vec3 towardEye = observer/max(metres,.001);
        vec3 right = vec3(towardEye.z,0.,-towardEye.x);
        right = dot(right,right)>.0001 ? normalize(right) : vec3(1.,0.,0.);
        vec3 up = normalize(cross(towardEye,right));
        float breadth = length(instanceMatrix[0].xyz);
        float projectedHeight = length(vec2(up.y*scale, length(up.xz)*breadth));
        worldPoint = center + (right*position.x*breadth + up*position.y*projectedHeight)*12.;
        float localX = dot(towardEye,normalize(instanceMatrix[0].xyz));
        float localZ = dot(towardEye,normalize(instanceMatrix[2].xyz));
        atlasView = vec2(fract(atan(localX,localZ)/6.2831853)*4.,
                        clamp((degrees(asin(clamp(towardEye.y,-1.,1.)))-6.)/34.,0.,2.));
        tileUV = uv;
        tint = instanceColor;
        vec4 viewPoint = viewMatrix * vec4(worldPoint,1.);
        // The atlas already contains the crown's self-shading. Sample daylight
        // on its sunward side so taller trees and buildings still shade it.
        vec4 worldPosition = vec4(worldPoint + 2.7*breadth*sunDirection,1.);
        vec3 transformedNormal = mat3(viewMatrix)*vec3(0.,1.,0.);
        #include <shadowmap_vertex>
        depth = -viewPoint.z;
        gl_Position = projectionMatrix * viewPoint;
      }`,
    fragmentShader: `
      #include <common>
      #include <packing>
      #include <shadowmap_pars_fragment>
      uniform bool receiveShadow;
      #include <shadowmask_pars_fragment>
      uniform sampler2D canopyMap;
      uniform float live, fadeStart, fadeEnd;
      uniform vec3 sunDirection, mist;
      varying vec2 tileUV, atlasView;
      varying vec3 worldPoint, tint;
      varying float metres, depth;
      ${screenNoise}
      vec4 viewSample(vec2 tile) {
        vec2 scaled = vec2(mod(tile.x,4.),min(tile.y,2.)) + clamp(tileUV,.003,.997);
        vec4 texel = texture2D(canopyMap,scaled / vec2(4.,3.));
        texel.rgb *= texel.a;
        return texel;
      }
      void main() {
        float noise = screenNoise();
        if (live>.5 && noise >= smoothstep(fadeStart,fadeEnd,metres)) discard;
        vec2 tile = floor(atlasView), f = fract(atlasView);
        vec4 lower = mix(viewSample(tile),viewSample(tile+vec2(1,0)),f.x);
        vec4 upper = mix(viewSample(tile+vec2(0,1)),viewSample(tile+1.),f.x);
        vec4 sampleColor = mix(lower,upper,f.y);
        if (sampleColor.a < .38) discard;
        vec3 rgb = sampleColor.rgb / max(sampleColor.a,.001) * tint * mix(.43,1.,getShadowMask());
        vec3 dir = normalize(worldPoint-cameraPosition);
        float sunny = max(dot(dir,sunDirection),0.);
        vec3 haze = mist*1.22 + vec3(.63,.35,.14)*pow(sunny,9.) + vec3(1.8,1.01,.43)*pow(sunny,95.);
        float fog = smoothstep(720.,3150.,depth);
        gl_FragColor = vec4(mix(rgb,haze,fog),1.);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }`,
  });
}

export async function loadParkTrees(parent, sites, loader) {
  const clock = { value: 0 },
    live = { value: 0 };
  const makeBatch = async (level) => {
    const gltf = await loader.loadAsync(`assets/park-trees-${level.name}.glb`);
    const bySpecies = species.map(() => []);
    for (const object of gltf.scene.children) {
      const speciesIndex = species.findIndex((name) => object.name.startsWith(name));
      const shadow = level.name === 'horizon';
      const material = shadow
        ? new T.MeshBasicMaterial()
        : specimenMaterial(object.name.endsWith('Foliage'), level, clock);
      const instances = new T.InstancedMesh(object.geometry, material, sites.length);
      instances.name = object.name;
      instances.instanceMatrix.setUsage(T.DynamicDrawUsage);
      instances.castShadow = shadow;
      instances.receiveShadow = !shadow;
      if (shadow) instances.layers.set(2);
      instances.frustumCulled = false;
      instances.count = 0;
      parent.add(instances);
      bySpecies[speciesIndex].push(instances);
    }
    return bySpecies;
  };
  const atlas = async (name) => {
    const texture = await new T.TextureLoader().loadAsync(
      `assets/park-foliage-${name.toLowerCase()}.png`,
    );
    const mesh = new T.InstancedMesh(
      new T.PlaneGeometry(1, 1),
      atlasMaterial(texture, live, levels[1]),
      sites.length,
    );
    mesh.name = `${name} | baked leaf silhouette`;
    mesh.receiveShadow = true;
    mesh.count = 0;
    mesh.frustumCulled = false;
    parent.add(mesh);
    return [mesh];
  };
  const [batches, shadows, distant] = await Promise.all([
    Promise.all(levels.map(makeBatch)),
    makeBatch({ name: 'horizon' }),
    Promise.all(species.map(atlas)),
  ]);
  const projection = new T.Matrix4();
  const frustum = new T.Frustum();
  const bounds = new T.Sphere();
  const previousPosition = new T.Vector3(Infinity, Infinity, Infinity);
  const previousDirection = new T.Quaternion();
  let previousFov = -1;
  let previousAspect = -1;
  const drawSite = (groups, site) => {
    for (const mesh of groups[site.species]) {
      mesh.setMatrixAt(mesh.count, site.matrix);
      mesh.setColorAt(mesh.count, site.color);
      mesh.count++;
    }
  };
  const flush = (groups) => {
    for (const pair of groups)
      for (const mesh of pair) {
        mesh.instanceMatrix.needsUpdate = true;
        if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
      }
  };
  for (const site of sites) {
    drawSite(shadows, site);
    drawSite(distant, site);
  }
  flush(shadows);
  flush(distant);
  function update(camera, seconds) {
    clock.value = seconds;
    live.value = 1;
    const sameView =
      previousPosition.distanceToSquared(camera.position) < 0.0625 &&
      previousDirection.angleTo(camera.quaternion) < 0.012 &&
      previousFov === camera.fov &&
      previousAspect === camera.aspect;
    if (sameView) return;
    previousPosition.copy(camera.position);
    previousDirection.copy(camera.quaternion);
    previousFov = camera.fov;
    previousAspect = camera.aspect;
    projection.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    frustum.setFromProjectionMatrix(projection);
    for (const bySpecies of batches) {
      for (const pair of bySpecies) for (const mesh of pair) mesh.count = 0;
    }
    for (const site of sites) {
      const metres = camera.position.distanceTo(site.center);
      if (metres > 80) continue;
      bounds.center.copy(site.center);
      bounds.radius = site.radius;
      if (!frustum.intersectsSphere(bounds)) continue;
      for (let i = 0; i < levels.length; i++) {
        const level = levels[i];
        const overlap = (levels[i - 1]?.feather ?? 0) + 3;
        if (metres >= level.enter - overlap && metres <= level.leave + level.feather + 3)
          drawSite(batches[i], site);
      }
    }
    for (const bySpecies of batches) flush(bySpecies);
  }
  return { count: sites.length, update };
}
