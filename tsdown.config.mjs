import { existsSync } from 'node:fs';
import { cp, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { defineConfig } from 'tsdown';

const html = await readFile('index.html', 'utf8');
const importMap = html.match(/<script type="importmap">([\s\S]*?)<\/script>/);
const { imports } = JSON.parse(importMap[1]);
const alias = Object.fromEntries(Object.entries(imports).map(([name, path]) => [
  name.endsWith('/') ? name.slice(0, -1) : name + '$',
  resolve(path),
]));

export default defineConfig({
  entry: { 'src/app': 'src/app.js' },
  platform: 'browser',
  format: 'esm',
  target: 'es2022',
  minify: true,
  dts: false,
  alias,
  hooks: {
    async 'build:done'() {
      // DRACOLoader resolves ../libs/draco relative to the bundled module in dist/src/.
      const manifest = JSON.parse(await readFile('assets/manifest.json', 'utf8'));
      const models = ['near', 'mid', 'far', 'horizon'].map(level => `park-trees-${level}.glb`);
      const foliage = ['park-foliage-zelkova.png', 'park-foliage-camphor.png'];
      const survey = JSON.parse(await readFile('assets/survey.json', 'utf8'));
      const surveyFiles = [...survey.photos, ...survey.elevations, ...(survey.surfaces ?? []), survey.trees].map(layer => layer.file);
      const railway = ['rail-model.glb', 'rail-reference.json', 'rail-tracks.json', 'survey-rail.json', 'RAIL-SOURCES.md'];
      const cityFiles = ['manifest.json', manifest.photo.file, manifest.elevation.file, ...manifest.archives, ...models, ...foliage, 'survey.json', ...surveyFiles, ...railway];
      await Promise.all([
        writeFile('dist/index.html', html.replace(importMap[0], '')),
        cp('src/style.css', 'dist/src/style.css'),
        cp('vendor/LICENSE', 'dist/vendor/LICENSE'),
        cp('vendor/addons/libs/draco', 'dist/libs/draco', { recursive: true }),
        ...cityFiles.map(file => cp(`assets/${file}`, `dist/assets/${file}`)),
      ]);
      for (const path of ['output/tokyo-station.mp4', 'output/tokyo-station.jpg']) {
        const isRendered = existsSync(path);
        if (isRendered) await cp(path, `dist/${path}`);
      }
    },
  },
});
