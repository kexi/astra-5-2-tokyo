import { existsSync } from 'node:fs';
import { cp, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { defineConfig } from 'tsdown';

const html = await readFile('index.html', 'utf8');
const importMap = html.match(/<script type="importmap">([\s\S]*?)<\/script>/);
const { imports } = JSON.parse(importMap[1]);
const alias = Object.fromEntries(
  Object.entries(imports).map(([name, path]) => [
    name.endsWith('/') ? name.slice(0, -1) : name + '$',
    resolve(path),
  ]),
);

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
      const models = ['near', 'mid', 'far', 'horizon'].map((level) => `park-trees-${level}.glb`);
      models.push('yaesu-street-trees.glb');
      models.push('omy-buildings.glb', 'omy-underground.glb', 'marunouchi-nichome.glb');
      const foliage = ['park-foliage-zelkova.png', 'park-foliage-camphor.png'];
      const survey = JSON.parse(await readFile('assets/survey.json', 'utf8'));
      const surveyFiles = [
        ...survey.photos,
        ...survey.elevations,
        ...(survey.surfaces ?? []),
        survey.trees,
      ].map((layer) => layer.file);
      const railway = [
        'rail-model.glb',
        'rail-reference.json',
        'rail-tracks.json',
        'survey-rail.json',
        'RAIL-SOURCES.md',
      ];
      const cityFiles = [
        'manifest.json',
        manifest.photo.file,
        manifest.elevation.file,
        ...models,
        ...foliage,
        'park-ground.json',
        'park-ground.png',
        'nihonbashi-expressway.png',
        'survey.json',
        ...surveyFiles,
        ...railway,
      ];
      await Promise.all([
        writeFile('dist/index.html', html.replace(importMap[0], '')),
        cp('assets/buildings', 'dist/assets/buildings', { recursive: true }),
        cp('review/building-textures.csv', 'dist/review/building-textures.csv'),
        cp('output/tokyo-station-og.jpg', 'dist/output/tokyo-station-og.jpg'),
        cp('output/omy-architecture.blend', 'dist/output/omy-architecture.blend'),
        cp('assets/OMY-SOURCES.md', 'dist/assets/OMY-SOURCES.md'),
        ...[
          'omy-buildings.json',
          'omy-underground-alignment.json',
          'omy_facades.py',
          'omy_facade_blocks.py',
        ].map((file) => cp('tools/' + file, 'dist/tools/' + file)),
        ...[
          'MARUNOUCHI-SOURCES.md',
          'OMY-FACADE-REPAIRS.md',
          'OMY-UNDERGROUND-SOURCES.md',
          'omy-underground.json',
        ].map((file) => cp('assets/' + file, 'dist/assets/' + file)),
        cp('src/style.css', 'dist/src/style.css'),
        cp('vendor/LICENSE', 'dist/vendor/LICENSE'),
        cp('vendor/addons/libs/draco', 'dist/libs/draco', { recursive: true }),
        ...cityFiles.map((file) => cp(`assets/${file}`, `dist/assets/${file}`)),
      ]);
      for (const path of ['output/tokyo-station.mp4', 'output/tokyo-station.jpg']) {
        const isRendered = existsSync(path);
        if (isRendered) await cp(path, `dist/${path}`);
      }
    },
  },
});
