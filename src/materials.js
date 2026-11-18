import * as T from 'three';

export const sun = new T.Vector3(-0.84, 0.22, -0.49).normalize();
export const mist = new T.Color('#b6b8ae');
const atmosphereUniforms = {
  horizon: { value: mist },
  sunDirection: { value: sun },
};
const atmosphere = `
  uniform vec3 sunDirection, horizon;
  vec3 airColor(vec3 dir) {
    float elevation = max(dir.y, 0.);
    float towardsSun = max(dot(dir, sunDirection), 0.);
    vec3 air = mix(horizon * 1.22, vec3(.077, .182, .355), 1. - exp(-elevation * 4.6));
    air += vec3(.63,.35,.14) * pow(towardsSun, 9.) * exp(-elevation * 1.9);
    air += vec3(1.8,1.01,.43) * pow(towardsSun, 95.);
    return air;
  }
  vec3 aerialPerspective(vec3 color, vec3 worldPoint, float viewDepth) {
    vec3 ray = worldPoint - cameraPosition;
    float upperAir = smoothstep(90., 570., .5 * (worldPoint.y + cameraPosition.y));
    float distanceHaze = smoothstep(720., mix(2850., 3460., upperAir), viewDepth);
    float edgeHaze = smoothstep(2100., 2720., max(abs(worldPoint.x), abs(worldPoint.z)));
    return mix(color, airColor(normalize(ray)), max(distanceHaze, edgeHaze));
  }
`;

const finish = `
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
`;

function cloudVolume() {
  const size = 64;
  const samples = new Uint8Array(size ** 3);
  let seed = 14251;
  const random = () => {
    seed = (Math.imul(1664525, seed) + 1013904223) >>> 0;
    return seed / 2 ** 32;
  };
  const layers = [4, 8, 16, 32].map(period => ({
    period, values: Float32Array.from({ length: period ** 3 }, random),
  }));
  const mix = T.MathUtils.lerp;
  const sample = ({ period, values }, x, y, z) => {
    const scale = period / size;
    const px = x * scale, py = y * scale, pz = z * scale;
    const ix = Math.floor(px), iy = Math.floor(py), iz = Math.floor(pz);
    const fade = t => t * t * (3 - 2 * t);
    const at = (a, b, c) => values[((c % period) * period + b % period) * period + a % period];
    const slice = c => mix(
      mix(at(ix, iy, c), at(ix + 1, iy, c), fade(px - ix)),
      mix(at(ix, iy + 1, c), at(ix + 1, iy + 1, c), fade(px - ix)),
      fade(py - iy),
    );
    return mix(slice(iz), slice(iz + 1), fade(pz - iz));
  };
  for (let z = 0; z < size; z++) {
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        const value = layers.reduce((sum, layer, i) => sum + sample(layer, x, y, z) * 0.53 ** i, 0);
        samples[(z * size + y) * size + x] = Math.round(value * 255 / 1.956);
      }
    }
  }
  const texture = new T.Data3DTexture(samples, size, size, size);
  texture.format = T.RedFormat;
  texture.minFilter = texture.magFilter = T.LinearFilter;
  texture.wrapS = texture.wrapT = texture.wrapR = T.RepeatWrapping;
  texture.needsUpdate = true;
  return texture;
}

export function makeSky() {
  const material = new T.ShaderMaterial({
    glslVersion: T.GLSL3, side: T.BackSide, depthWrite: false,
    uniforms: {
      ...atmosphereUniforms,
      volume: { value: cloudVolume() },
      seconds: { value: 0 },
    },
    vertexShader: `
      varying vec3 skyRay;
      void main() {
        skyRay = position;
        gl_Position = projectionMatrix * viewMatrix * vec4(cameraPosition + position, 1.);
        gl_Position.z = gl_Position.w;
      }`,
    fragmentShader: `
      precision highp sampler3D;
      out vec4 outputColor;
      #define gl_FragColor outputColor
      varying vec3 skyRay;
      ${atmosphere}
      uniform sampler3D volume;
      uniform float seconds;
      float density(vec3 p) {
        vec3 uvw = p * vec3(.00045, .0012, .00045) + vec3(seconds * .00075, .26, -.21);
        float n = texture(volume, uvw).r;
        n = .80*n + .20*texture(volume, uvw*.41+vec3(.32,.53,.77)).r;
        float profile = smoothstep(990., 1100., p.y) * (1. - smoothstep(1210., 1410., p.y));
        return smoothstep(.55, .68, n) * profile;
      }
      void main() {
        vec3 dir = normalize(skyRay);
        float towardsSun = max(dot(dir, sunDirection), 0.);
        vec3 air = airColor(dir);
        float disc = smoothstep(.999977, .999989, towardsSun);
        air += vec3(10., 7., 3.45) * disc;
        if (dir.y > .055) {
          float entry = max((995. - cameraPosition.y) / dir.y, 0.);
          float stepSize = (405. / dir.y) / 12.;
          vec3 scatter = vec3(0.);
          float transmittance = 1.;
          for (int i=0; i<12; i++) {
            vec3 p = cameraPosition + dir * (entry + (float(i)+.5) * stepSize);
            float d = density(p);
            float shadow = density(p + sunDirection * 135.);
            float alpha = 1. - exp(-d * stepSize * .0047);
            vec3 light = mix(vec3(.32,.39,.48), vec3(1.26,1.12,.89), smoothstep(-.3,.28,d-shadow+.13));
            scatter += light * alpha * transmittance;
            transmittance *= 1. - alpha;
          }
          vec3 withClouds = air * transmittance + scatter;
          air = mix(air, withClouds, smoothstep(.055,.16,dir.y));
        }
        gl_FragColor = vec4(air,1.);
        ${finish}
      }`,
  });
  const mesh = new T.Mesh(new T.SphereGeometry(6000, 32, 18), material);
  mesh.frustumCulled = false;
  mesh.renderOrder = -10;
  return mesh;
}

export function surfaceMaterial(kind, texture, projectMap) {
  const material = new T.MeshStandardMaterial({
    map: texture ?? null,
    color: { ground: '#e9e4db', road: '#e3dfd9', grass: '#6b7740', building: '#cfc6b6', leaf: '#70864c', trunk: '#655647' }[kind],
    roughness: kind === 'building' ? .78 : .95,
    side: T.DoubleSide,
  });
  material.onBeforeCompile = shader => {
    Object.assign(shader.uniforms, atmosphereUniforms);
    shader.vertexShader = 'varying vec3 terrainPoint;\n' + shader.vertexShader.replace(
      '#include <begin_vertex>',
      `#include <begin_vertex>
       vec4 terrainVertex = vec4(transformed,1.);
       #ifdef USE_INSTANCING
         terrainVertex = instanceMatrix * terrainVertex;
       #endif
       terrainPoint = (modelMatrix * terrainVertex).xyz;`,
    );
    if (projectMap) {
      shader.uniforms.projectMap = { value: projectMap };
      shader.vertexShader = 'uniform vec4 projectMap;\n' + shader.vertexShader.replace(
        'terrainPoint = (modelMatrix * terrainVertex).xyz;',
        'terrainPoint = (modelMatrix * terrainVertex).xyz; vMapUv = terrainPoint.xz * projectMap.xy + projectMap.zw;',
      );
    }
    shader.fragmentShader = `${atmosphere}
      varying vec3 terrainPoint;
      float hashDetail(vec3 p) {
        vec3 h = fract(p * .3183099 + vec3(.14,.26,.37)) * 17.;
        return fract(h.x*h.y*h.z*(h.x+h.y+h.z));
      }
      float surfaceDetail(vec3 p) {
        vec3 i = floor(p), f = fract(p);
        f = f*f*(3.-2.*f);
        return mix(mix(mix(hashDetail(i),hashDetail(i+vec3(1,0,0)),f.x),
                       mix(hashDetail(i+vec3(0,1,0)),hashDetail(i+vec3(1,1,0)),f.x),f.y),
                   mix(mix(hashDetail(i+vec3(0,0,1)),hashDetail(i+vec3(1,0,1)),f.x),
                       mix(hashDetail(i+vec3(0,1,1)),hashDetail(i+vec3(1,1,1)),f.x),f.y),f.z);
      }
      float grassCover(vec3 p) {
        float flecks = surfaceDetail(p * 1.9);
        float grain = mix(flecks, .5, smoothstep(.25, 1.7, length(fwidth(p.xz))));
        return .62 + .55 * surfaceDetail(p * .10) + .32 * grain;
      }
    ` + shader.fragmentShader.replace('#include <color_fragment>', {
      ground: `#include <color_fragment>
        diffuseColor.rgb *= .87 + .24 * surfaceDetail(terrainPoint * .2);
        float green = diffuseColor.g - max(diffuseColor.r, diffuseColor.b);
        float turf = smoothstep(.002,.037,green) * smoothstep(.045,.22,diffuseColor.g);
        diffuseColor.rgb *= mix(vec3(1.025,.992,.929), vec3(1.03,1.10,.91) * grassCover(terrainPoint), turf);`,
      road: `#include <color_fragment>
        diffuseColor.rgb *= .96+.075*surfaceDetail(terrainPoint*.85)+.05*surfaceDetail(terrainPoint*.073);`,
      grass: `#include <color_fragment>
        diffuseColor.rgb *= grassCover(terrainPoint);`,
      building: `#include <color_fragment>
        diffuseColor.rgb *= .83+.24*surfaceDetail(terrainPoint*.053)+.045*surfaceDetail(terrainPoint*1.7);`,
      leaf: `#include <color_fragment>
        diffuseColor.rgb *= .73+.49*surfaceDetail(terrainPoint*1.2);`,
      trunk: '#include <color_fragment>',
    }[kind]).replace('#include <tonemapping_fragment>', `
        gl_FragColor.rgb = aerialPerspective(gl_FragColor.rgb, terrainPoint, vViewPosition.z);
        #include <tonemapping_fragment>`);
  };
  material.customProgramCacheKey = () => kind;
  return material;
}

const reflectionOrigin = new T.Vector3(-445, 2, 55);

export function captureMoatReflection(renderer, scene, material) {
  const cube = new T.WebGLCubeRenderTarget(256, {
    type: T.HalfFloatType, generateMipmaps: true, minFilter: T.LinearMipmapLinearFilter,
  });
  const camera = new T.CubeCamera(1, 6500, cube);
  camera.position.copy(reflectionOrigin);
  material.visible = false;
  camera.update(renderer, scene);
  material.visible = true;
  material.uniforms.cityReflection.value = cube.texture;
}

export function waterMaterial() {
  return new T.ShaderMaterial({
    side: T.DoubleSide, polygonOffset: true, polygonOffsetFactor: -1,
    uniforms: {
      ...atmosphereUniforms, seconds: { value: 0 },
      cityReflection: { value: null }, reflectionOrigin: { value: reflectionOrigin },
    },
    vertexShader: `
      varying vec3 waterPoint;
      varying float viewDepth;
      void main() {
        waterPoint = (modelMatrix * vec4(position,1.)).xyz;
        vec4 mvPosition = viewMatrix * vec4(waterPoint,1.);
        viewDepth = -mvPosition.z;
        gl_Position = projectionMatrix * mvPosition;
      }`,
    fragmentShader: `
      varying vec3 waterPoint;
      varying float viewDepth;
      uniform float seconds;
      uniform samplerCube cityReflection;
      uniform vec3 reflectionOrigin;
      ${atmosphere}
      float waterHash(vec2 p) {
        return fract(sin(dot(mod(p,2048.),vec2(127.1,311.7))) * 43758.5453);
      }
      float wind(vec2 p) {
        vec2 i = floor(p), f = fract(p);
        f = f*f*(3.-2.*f);
        return mix(mix(waterHash(i), waterHash(i+vec2(1,0)),f.x),
                   mix(waterHash(i+vec2(0,1)),waterHash(i+1.),f.x),f.y);
      }
      vec2 ripple(vec2 p, vec2 frequency, float speed, float warp) {
        float phase = dot(p, frequency) + seconds*speed + warp;
        float visible = 1.-smoothstep(.65, 2.8, fwidth(phase));
        return normalize(frequency) * cos(phase) * visible;
      }
      void main() {
        vec2 p = waterPoint.xz;
        float swell = wind(p*.047 + vec2(seconds*.024,0.));
        float gust = wind(p*.123 + vec2(0.,seconds*.043));
        vec2 slope = .032*ripple(p,vec2(.57,.16),.74,7.*swell)
                   + .043*ripple(p,vec2(1.35,.72),1.13,9.*gust+3.*swell)
                   + .019*ripple(p,vec2(2.61,-.48),1.51,6.*gust);
        vec3 normal = normalize(vec3(slope.x, 1., slope.y));
        vec3 viewDir = normalize(cameraPosition-waterPoint);
        float fresnel = pow(1.-clamp(dot(viewDir,normal),0.,1.), 4.4);
        vec3 reflection = reflect(-viewDir, normal);
        float highlight = pow(max(dot(reflection,sunDirection),0.), 130.);
        vec3 deep = vec3(.026,.046,.034) * (.91+.11*swell+.08*gust);
        vec3 reflected = airColor(reflection);
        vec3 local = waterPoint - reflectionOrigin;
        float nearMoat = 1.-smoothstep(1.,1.8,length(local.xz/vec2(125.,310.)));
        // A local environment probe retains the skyline silhouette in the moat.
        vec3 probeRay = reflection * 175. + local;
        reflected = mix(reflected,textureCube(cityReflection,probeRay,1.2).rgb,nearMoat);
        gl_FragColor = vec4(mix(deep, reflected, .065+.62*fresnel) + vec3(.62,.42,.21)*highlight,1.);
        gl_FragColor.rgb = aerialPerspective(gl_FragColor.rgb, waterPoint, viewDepth);
        ${finish}
      }`,
  });
}

export function makeFlare(renderer) {
  const uniforms = {
    location: { value: new T.Vector2() },
    strength: { value: 0 },
    aspect: { value: 16 / 9 },
  };
  const screen = new T.Scene();
  const camera = new T.Camera();
  const plane = new T.Mesh(new T.PlaneGeometry(2, 2), new T.ShaderMaterial({
    uniforms, transparent: true, blending: T.AdditiveBlending, depthTest: false, depthWrite: false,
    vertexShader: `varying vec2 vUV;
      void main() { vUV = uv; gl_Position = vec4(position.xy,0.,1.); }`,
    fragmentShader: `
      varying vec2 vUV;
      uniform vec2 location;
      uniform float strength, aspect;
      float ghost(vec2 uv, vec2 origin, float position, float size) {
        vec2 p = uv - origin * position;
        float r = length(p);
        float rim = exp(-pow((r-size)/(size*.24),2.));
        return .6*exp(-r*r/(size*size*.46))+.4*rim;
      }
      void main() {
        vec2 uv = (vUV-.5)*vec2(aspect, 1.);
        vec2 origin = (location-.5)*vec2(aspect, 1.);
        vec2 d = uv-origin;
        float falloff = exp(-length(d)*8.);
        float streak = exp(-abs(d.x)*5. - abs(d.y)*112.)*.10;
        vec3 glow = vec3(1.,.64,.29) * (falloff*.22 + streak);
        glow += vec3(.28,.62,.64) * ghost(uv,origin,-.40,.047)*.17;
        glow += vec3(.81,.47,.22) * ghost(uv,origin,-.81,.105)*.13;
        glow += vec3(.35,.51,.72) * ghost(uv,origin,-1.28,.061)*.085;
        glow += vec3(.84,.63,.26) * ghost(uv,origin,.36,.027)*.075;
        gl_FragColor = vec4(glow, strength);
      }`,
  }));
  plane.frustumCulled = false;
  screen.add(plane);
  const probe = new T.Vector3();
  const forward = new T.Vector3();
  const ray = new T.Raycaster();
  ray.far = 3600;
  ray.layers.set(1);
  let visibility = 0;
  let nextTest = 0;
  let lastSeconds = -1;

  return (view, buildings, seconds, enabled, fixed) => {
    if (!enabled || view.getWorldDirection(forward).dot(sun) <= 0) return;
    probe.copy(sun).multiplyScalar(4500).add(view.position).project(view);
    const edge = Math.max(Math.abs(probe.x), Math.abs(probe.y));
    if (edge > 1.34) return;
    const now = performance.now();
    const seeked = seconds < lastSeconds || seconds - lastSeconds > .15;
    lastSeconds = seconds;
    if (fixed || seeked || now >= nextTest) {
      ray.set(view.position, sun);
      visibility = ray.intersectObject(buildings, true).length ? 0 : 1;
      nextTest = now + 120;
    }
    if (!visibility) return;
    uniforms.location.value.set(probe.x * .5 + .5, probe.y * .5 + .5);
    uniforms.aspect.value = view.aspect;
    uniforms.strength.value = 1 - T.MathUtils.smoothstep(edge, .68, 1.34);
    const autoClear = renderer.autoClear;
    renderer.autoClear = false;
    renderer.render(screen, camera);
    renderer.autoClear = autoClear;
  };
}
