import { createReadStream } from 'node:fs';
import { mkdir, mkdtemp, rm, stat, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { tmpdir } from 'node:os';
import { dirname, extname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

const root = resolve(
  fileURLToPath(new URL('../', import.meta.url)),
  process.env.FLIGHT_ROOT ?? '.',
);
const prefix = process.env.FLIGHT_PREFIX ?? '';
const published = process.env.FLIGHT_URL;
const [mode = 'review', destination = 'review/current'] = process.argv.slice(2);
const cinema = mode === 'film' || mode === 'film-stills';
const width = Number(process.env.FLIGHT_WIDTH ?? (mode === 'film' ? 1920 : 1280));
const height = Number(process.env.FLIGHT_HEIGHT ?? Math.round((width * 9) / 16 / 2) * 2);
const output = resolve(destination);
const chromePath =
  process.env.CHROME_BIN ??
  (process.platform === 'darwin'
    ? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
    : 'chromium');
const media = new Map([
  ['.html', 'text/html; charset=utf-8'],
  ['.js', 'text/javascript; charset=utf-8'],
  ['.css', 'text/css; charset=utf-8'],
  ['.json', 'application/json; charset=utf-8'],
  ['.jpg', 'image/jpeg'],
  ['.png', 'image/png'],
  ['.wasm', 'application/wasm'],
]);

const server = createServer(async (request, response) => {
  try {
    const { pathname } = new URL(request.url, 'http://127.0.0.1');
    if (prefix && !pathname.startsWith(prefix + '/')) {
      response.writeHead(404).end();
      return;
    }
    const relative = pathname.slice(prefix.length);
    const path = resolve(
      root,
      '.' + decodeURIComponent(relative === '/' ? '/index.html' : relative),
    );
    if (!path.startsWith(root.endsWith(sep) ? root : root + sep)) {
      response.writeHead(403).end();
      return;
    }
    const metadata = await stat(path);
    if (!metadata.isFile()) throw new Error('Not a file');
    const headers = {
      'Content-Type': media.get(extname(path)) ?? 'application/octet-stream',
      'Accept-Ranges': 'bytes',
    };
    const range = request.headers.range;
    if (range) {
      const match = /^bytes=(\d+)-(\d+)$/.exec(range);
      const start = Number(match?.[1]),
        end = Number(match?.[2]);
      if (!match || start > end || end >= metadata.size) {
        response.writeHead(416, { 'Content-Range': `bytes */${metadata.size}` }).end();
        return;
      }
      response.writeHead(206, {
        ...headers,
        'Content-Length': end - start + 1,
        'Content-Range': `bytes ${start}-${end}/${metadata.size}`,
      });
      createReadStream(path, { start, end }).pipe(response);
      return;
    }
    response.writeHead(200, { ...headers, 'Content-Length': metadata.size });
    createReadStream(path).pipe(response);
  } catch {
    response.writeHead(404).end();
  }
});

function connect(chrome) {
  const pending = new Map();
  let id = 0,
    buffer = '';
  chrome.stdio[4].setEncoding('utf-8');
  chrome.stdio[4].on('data', (part) => {
    const messages = (buffer + part).split('\0');
    buffer = messages.pop();
    for (const line of messages) {
      const message = JSON.parse(line);
      const request = pending.get(message.id);
      if (request) {
        pending.delete(message.id);
        if (message.error) request.reject(new Error(message.error.message));
        else request.resolve(message.result);
      }
      if (message.method === 'Runtime.exceptionThrown')
        console.error(message.params.exceptionDetails.text);
      if (
        message.method === 'Runtime.consoleAPICalled' &&
        ['error', 'warning'].includes(message.params.type)
      ) {
        console.error(...message.params.args.map((arg) => arg.value ?? arg.description));
      }
    }
  });
  chrome.once('error', fail);
  chrome.once('exit', (code) => fail(new Error(`Chrome exited (${code})`)));
  function fail(error) {
    for (const { reject } of pending.values()) reject(error);
    pending.clear();
  }
  return (method, params = {}, sessionId) =>
    new Promise((resolve, reject) => {
      const command = ++id;
      pending.set(command, { resolve, reject });
      chrome.stdio[3].write(JSON.stringify({ id: command, method, params, sessionId }) + '\0');
    });
}

async function openChrome(profile, url) {
  const chrome = spawn(
    chromePath,
    [
      '--headless=new',
      '--remote-debugging-pipe',
      '--no-first-run',
      '--no-default-browser-check',
      '--disable-background-networking',
      '--disable-features=Translate',
      '--enable-unsafe-swiftshader',
      '--disable-background-timer-throttling',
      '--disable-renderer-backgrounding',
      '--hide-scrollbars',
      `--user-data-dir=${profile}`,
      `--window-size=${width},${height}`,
      ...(process.env.FLIGHT_SOFTWARE ? ['--use-angle=swiftshader'] : []),
    ],
    { stdio: ['ignore', 'ignore', 'pipe', 'pipe', 'pipe'] },
  );
  chrome.stderr.on('data', (chunk) => {
    const line = String(chunk);
    if (process.env.FLIGHT_CHROME_LOG) console.error(line.trim());
  });
  const call = connect(chrome);
  const { targetId } = await call('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await call('Target.attachToTarget', { targetId, flatten: true });
  const send = (method, params) => call(method, params, sessionId);
  await send('Runtime.enable');
  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', {
    width,
    height,
    deviceScaleFactor: 1,
    mobile: false,
  });
  const interactive = mode === 'viewer' || mode === 'city-ui';
  const study = ['buildings', 'underground', 'city-buildings', 'city-ui'].includes(mode);
  const page = new URL(url);
  if (!interactive) page.searchParams.set('capture', '');
  if (cinema) page.searchParams.set('cinema', '');
  if (study) page.searchParams.set('model', mode === 'city-ui' ? 'city-buildings' : mode);
  if (['city-buildings', 'city-ui'].includes(mode) && process.env.BUILDING_ID)
    page.searchParams.set('id', process.env.BUILDING_ID);
  await send('Page.navigate', { url: page.href });
  const evaluate = async (expression) => {
    const result = await send('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (result.exceptionDetails)
      throw new Error(
        result.exceptionDetails.exception?.description ?? result.exceptionDetails.text,
      );
    return result.result.value;
  };
  try {
    for (let i = 0; i < 300; i++) {
      const state = await evaluate(
        'window.flightFailure || ((window.flightCapture || window.architectureCapture || window.buildingCapture || document.getElementById("city-count").textContent) && document.getElementById("loading").hidden ? "ready" : "")',
      );
      if (state === 'ready') {
        if (!study) console.log(await evaluate('flightCapture.stats()'));
        return {
          chrome,
          send,
          evaluate,
          async screenshot() {
            await evaluate('new Promise(requestAnimationFrame)');
            const { data } = await send('Page.captureScreenshot', {
              format: 'jpeg',
              quality: 94,
              fromSurface: true,
            });
            return Buffer.from(data, 'base64');
          },
        };
      }
      if (state) throw new Error(state);
      await delay(450);
    }
    throw new Error('Timed out while loading the city');
  } catch (error) {
    chrome.kill();
    throw error;
  }
}

async function makeStills(browser) {
  await mkdir(output, { recursive: true });
  async function shot(name, expression) {
    await browser.evaluate(expression);
    await writeFile(join(output, name + '.jpg'), await browser.screenshot());
    console.log(name);
  }
  if (mode === 'city-ui') {
    const id = process.env.BUILDING_COMPARE_ID ?? '13101-bldg-1826';
    const state = await browser.evaluate(`(async () => {
      const input = document.getElementById('city-search'), status = document.getElementById('city-status');
      const rotate = document.getElementById('city-rotate'), form = document.getElementById('city-find');
      const label = rotate.textContent;
      rotate.click();
      const toggleChanged = rotate.textContent !== label;
      if (rotate.textContent === '回転を止める') rotate.click();
      input.value = 'invalid-building-id';
      form.requestSubmit();
      const rejected = status.textContent.includes('該当する建物がありません');
      input.value = ${JSON.stringify(id)};
      input.dispatchEvent(new Event('input', {bubbles:true}));
      form.requestSubmit();
      for (let i=0; i<100; i++) {
        if (new URLSearchParams(location.search).get('id') === ${JSON.stringify(id)} && !status.textContent.includes('読み込み中')) break;
        await new Promise(resolve => setTimeout(resolve, 60));
      }
      return {selected:new URLSearchParams(location.search).get('id'),toggleChanged,rejected,
        label:input.labels[0].textContent,status:status.textContent, width:innerWidth,
        horizontalOverflow:document.documentElement.scrollWidth > innerWidth};
    })()`);
    console.log(state);
    if (
      state.selected !== id ||
      !state.toggleChanged ||
      !state.rejected ||
      state.horizontalOverflow
    )
      throw new Error('建物ビューアの検索・回転・表示幅テストに失敗しました');
    await writeFile(join(output, 'viewer.jpg'), await browser.screenshot());
    return;
  }
  if (mode === 'city-buildings') {
    const initial = await browser.evaluate('buildingCapture.state');
    console.log(initial);
    const selected = process.env.BUILDING_IDS?.split(',') ?? [initial.id];
    for (const id of selected) {
      if (id !== initial.id || selected.length > 1) {
        await browser.evaluate('buildingCapture.select(' + JSON.stringify(id) + ')');
      }
      for (let angle = 0; angle < 360; angle += 45) {
        await shot(id + '-' + angle, `buildingCapture.frame(${angle})`);
      }
    }
    const requests = await browser.evaluate(
      'performance.getEntriesByType("resource").filter(r => r.name.includes("/assets/buildings/")).map(r => [new URL(r.name).pathname.split("/").pop(),r.responseStatus,r.encodedBodySize])',
    );
    console.log({ requests });
    return;
  }
  if (mode === 'review') {
    for (const degrees of [0, 45, 90, 135, 180, 225, 270, 315]) {
      await shot(
        `orbit-${String(degrees).padStart(3, '0')}`,
        `flightCapture.orbit(${degrees}, 280, 520)`,
      );
    }
    for (const degrees of [45, 135, 225, 315]) {
      await shot(`low-${degrees}`, `flightCapture.orbit(${degrees}, 115, 305)`);
    }
    await shot('high-nadir', 'flightCapture.orbit(0, 850, 105, false)');
    await shot('detail-water', "flightCapture.detail('moat')");
    await shot('detail-grass', "flightCapture.detail('grass')");
    await shot('detail-leaves', "flightCapture.detail('leaves')");
    await shot('detail-leaves-wind', "flightCapture.detail('leaves', 4.1)");
    await shot('detail-trees', "flightCapture.detail('trees')");
    await shot('detail-rail', "flightCapture.detail('rail')");
    await shot('detail-furniture', "flightCapture.detail('furniture')");
    await shot('detail-bridge', "flightCapture.detail('bridge')");
    await shot('detail-glass', "flightCapture.detail('glass')");
    await shot('flare-on', 'flightCapture.frame(33.5, true)');
    await shot('flare-off', 'flightCapture.frame(33.5, false)');
  }
  if (mode === 'buildings' || mode === 'underground') {
    const choices = await browser.evaluate('Object.keys(architectureCapture.choices)');
    if (!choices.length) throw new Error('Empty model selection');
    for (let bearing = 0; bearing < 360; bearing += 45) {
      await shot('orbit-' + bearing, `architectureCapture.orbit(${bearing})`);
    }
    for (const id of choices) {
      await shot(id, 'architectureCapture.select(' + JSON.stringify(id) + ')');
      if (mode === 'buildings') {
        for (let bearing = 0; bearing < 360; bearing += 45) {
          await shot(`${id}-${bearing}`, `architectureCapture.orbit(${bearing})`);
        }
      }
    }
    if (mode === 'underground') {
      await shot('interior', 'document.getElementById("finish").click()');
      await shot('enclosed', 'document.getElementById("cutaway").click()');
    }
    return;
  }
  for (const seconds of [
    1.7, 5.5, 7.3, 9.4, 12.1, 15.6, 18.2, 21.1, 22.8, 23.9, 25.3, 27.2, 28.6, 30.0, 31.5, 33.5,
    37.7,
  ]) {
    await shot(`flight-${seconds.toFixed(1).padStart(4, '0')}`, `flightCapture.frame(${seconds})`);
  }
}

async function makeFilm(browser) {
  const fps = Number(process.env.FLIGHT_FPS ?? 30);
  const duration = await browser.evaluate('flightCapture.duration');
  const frames = Math.round(duration * fps);
  await mkdir(dirname(output), { recursive: true });
  const encoder = spawn(
    'ffmpeg',
    [
      '-hide_banner',
      '-loglevel',
      'error',
      '-y',
      '-f',
      'image2pipe',
      '-framerate',
      String(fps),
      '-vcodec',
      'mjpeg',
      '-i',
      'pipe:0',
      '-c:v',
      'libx264',
      '-preset',
      'veryfast',
      '-crf',
      '17',
      '-maxrate',
      '18M',
      '-bufsize',
      '24M',
      '-vf',
      `scale=in_color_matrix=bt601:out_color_matrix=bt709:in_range=full:out_range=tv,format=yuv420p,fade=t=in:d=0.22,fade=t=out:st=${duration - 0.55}:d=0.55`,
      '-movflags',
      '+faststart',
      '-color_range',
      'tv',
      '-colorspace',
      'bt709',
      '-color_trc',
      'bt709',
      '-color_primaries',
      'bt709',
      output,
    ],
    { stdio: ['pipe', 'ignore', 'inherit'] },
  );
  const ended = once(encoder, 'exit');
  let streamError;
  encoder.stdin.on('error', (error) => {
    streamError = error;
  });
  try {
    for (let i = 0; i < frames; i++) {
      await browser.evaluate(`flightCapture.frame(${i / fps})`);
      const image = await browser.screenshot();
      if (streamError) throw streamError;
      if (!encoder.stdin.write(image)) await once(encoder.stdin, 'drain');
      if ((i + 1) % fps === 0 || i === frames - 1) console.log(`Rendered ${i + 1}/${frames}`);
    }
    encoder.stdin.end();
    const [code] = await ended;
    if (code !== 0) throw new Error(`ffmpeg exited (${code})`);
  } catch (error) {
    encoder.kill();
    throw error;
  }
}

const profile = await mkdtemp(join(tmpdir(), 'tokyo-chrome-'));
let browser;
try {
  if (!published) {
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
  }
  const url = published ?? `http://127.0.0.1:${server.address().port}${prefix}/`;
  browser = await openChrome(profile, url);
  if (mode === 'film') await makeFilm(browser);
  else if (mode === 'viewer') {
    const values = await browser.evaluate(`(async () => {
      const play = document.getElementById("play");
      const orbit = document.getElementById("orbit");
      const flare = document.getElementById("flare");
      const seek = document.getElementById("seek");
      const initial = play.textContent;
      play.click();
      const playing = play.textContent;
      orbit.click();
      const orbiting = orbit.getAttribute("aria-pressed");
      flare.click();
      const flareOff = flare.getAttribute("aria-pressed");
      seek.value = 18;
      seek.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise(requestAnimationFrame);
      return {initial, playing, orbiting, flareOff, orbitAfterScrub: orbit.getAttribute("aria-pressed"), shot: document.getElementById("shot").textContent};
    })()`);
    console.log(values);
    if (
      values.orbitAfterScrub !== 'false' ||
      values.shot !== '線路と街区' ||
      values.playing !== '一時停止'
    ) {
      throw new Error('Viewer controls did not reach the expected state');
    }
    await mkdir(dirname(output), { recursive: true });
    await writeFile(output, await browser.screenshot());
  } else {
    await makeStills(browser);
  }
} finally {
  if (browser) {
    const close = once(browser.chrome, 'exit');
    browser.chrome.kill();
    await close;
  }
  server.closeAllConnections();
  server.close();
  await rm(profile, { recursive: true, force: true, maxRetries: 3 });
}
