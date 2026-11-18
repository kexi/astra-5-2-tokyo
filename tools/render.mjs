import { createReadStream } from 'node:fs';
import { mkdir, mkdtemp, rm, stat, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { tmpdir } from 'node:os';
import { dirname, extname, join, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

const root = resolve(fileURLToPath(new URL('../', import.meta.url)), process.env.FLIGHT_ROOT ?? '.');
const prefix = process.env.FLIGHT_PREFIX ?? '';
const published = process.env.FLIGHT_URL;
const [mode = 'review', destination = 'review/current'] = process.argv.slice(2);
const cinema = mode === 'film' || mode === 'film-stills';
const width = Number(process.env.FLIGHT_WIDTH ?? (mode === 'film' ? 1920 : 1280));
const height = Number(process.env.FLIGHT_HEIGHT ?? Math.round(width * 9 / 16 / 2) * 2);
const output = resolve(destination);
const chromePath = process.env.CHROME_BIN ?? (
  process.platform === 'darwin'
    ? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
    : 'chromium'
);
const media = new Map([
  ['.html', 'text/html; charset=utf-8'], ['.js', 'text/javascript; charset=utf-8'],
  ['.css', 'text/css; charset=utf-8'], ['.json', 'application/json; charset=utf-8'],
  ['.jpg', 'image/jpeg'], ['.png', 'image/png'], ['.wasm', 'application/wasm'],
]);

const server = createServer(async (request, response) => {
  try {
    const { pathname } = new URL(request.url, 'http://127.0.0.1');
    if (prefix && !pathname.startsWith(prefix + '/')) {
      response.writeHead(404).end();
      return;
    }
    const relative = pathname.slice(prefix.length);
    const path = resolve(root, '.' + decodeURIComponent(relative === '/' ? '/index.html' : relative));
    if (!path.startsWith(root.endsWith(sep) ? root : root + sep)) {
      response.writeHead(403).end();
      return;
    }
    const metadata = await stat(path);
    if (!metadata.isFile()) throw new Error('Not a file');
    response.writeHead(200, {
      'Content-Type': media.get(extname(path)) ?? 'application/octet-stream',
      'Content-Length': metadata.size,
    });
    createReadStream(path).pipe(response);
  } catch {
    response.writeHead(404).end();
  }
});

function connect(chrome) {
  const pending = new Map();
  let id = 0, buffer = '';
  chrome.stdio[4].setEncoding('utf-8');
  chrome.stdio[4].on('data', part => {
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
      if (message.method === 'Runtime.exceptionThrown') console.error(message.params.exceptionDetails.text);
      if (message.method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(message.params.type)) {
        console.error(...message.params.args.map(arg => arg.value ?? arg.description));
      }
    }
  });
  chrome.once('error', fail);
  chrome.once('exit', code => fail(new Error(`Chrome exited (${code})`)));
  function fail(error) {
    for (const { reject } of pending.values()) reject(error);
    pending.clear();
  }
  return (method, params = {}, sessionId) => new Promise((resolve, reject) => {
    const command = ++id;
    pending.set(command, { resolve, reject });
    chrome.stdio[3].write(JSON.stringify({ id: command, method, params, sessionId }) + '\0');
  });
}

async function openChrome(profile, url) {
  const chrome = spawn(chromePath, [
    '--headless=new', '--remote-debugging-pipe', '--no-first-run', '--no-default-browser-check',
    '--disable-background-networking', '--disable-features=Translate', '--enable-unsafe-swiftshader',
    '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
    '--hide-scrollbars', `--user-data-dir=${profile}`, `--window-size=${width},${height}`,
    ...(process.env.FLIGHT_SOFTWARE ? ['--use-angle=swiftshader'] : []),
  ], { stdio: ['ignore', 'ignore', 'pipe', 'pipe', 'pipe'] });
  chrome.stderr.on('data', chunk => {
    const line = String(chunk);
    if (process.env.FLIGHT_CHROME_LOG) console.error(line.trim());
  });
  const call = connect(chrome);
  const { targetId } = await call('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await call('Target.attachToTarget', { targetId, flatten: true });
  const send = (method, params) => call(method, params, sessionId);
  await send('Runtime.enable');
  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false });
  const interactive = mode === 'viewer';
  await send('Page.navigate', { url: interactive ? url : url + (cinema ? '?capture&cinema' : '?capture') });
  const evaluate = async expression => {
    const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description ?? result.exceptionDetails.text);
    return result.result.value;
  };
  try {
    for (let i = 0; i < 300; i++) {
      const state = await evaluate('window.flightFailure || (window.flightCapture && document.getElementById("loading").hidden ? "ready" : "")');
      if (state === 'ready') {
        console.log(await evaluate('flightCapture.stats()'));
        return {
          chrome, send, evaluate,
          async screenshot() {
            await evaluate('new Promise(requestAnimationFrame)');
            const { data } = await send('Page.captureScreenshot', { format: 'jpeg', quality: 94, fromSurface: true });
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
  if (mode === 'review') {
    for (const degrees of [0, 45, 90, 135, 180, 225, 270, 315]) {
      await shot(`orbit-${String(degrees).padStart(3, '0')}`, `flightCapture.orbit(${degrees}, 280, 520)`);
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
  for (const seconds of [1.7, 5.5, 7.3, 9.4, 12.1, 15.6, 18.2, 22.8, 25.3, 27.2, 28.6, 30.0, 31.5, 33.5, 37.7]) {
    await shot(`flight-${seconds.toFixed(1).padStart(4, '0')}`, `flightCapture.frame(${seconds})`);
  }
}

async function makeFilm(browser) {
  const fps = Number(process.env.FLIGHT_FPS ?? 30);
  const duration = await browser.evaluate('flightCapture.duration');
  const frames = Math.round(duration * fps);
  await mkdir(dirname(output), { recursive: true });
  const encoder = spawn('ffmpeg', [
    '-hide_banner', '-loglevel', 'error', '-y', '-f', 'image2pipe', '-framerate', String(fps),
    '-vcodec', 'mjpeg', '-i', 'pipe:0', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '17',
    '-maxrate', '18M', '-bufsize', '24M',
     '-vf', `scale=in_color_matrix=bt601:out_color_matrix=bt709:in_range=full:out_range=tv,format=yuv420p,fade=t=in:d=0.22,fade=t=out:st=${duration - .55}:d=0.55`,
    '-movflags', '+faststart', '-color_range', 'tv', '-colorspace', 'bt709',
    '-color_trc', 'bt709', '-color_primaries', 'bt709', output,
  ], { stdio: ['pipe', 'ignore', 'inherit'] });
  const ended = once(encoder, 'exit');
  let streamError;
  encoder.stdin.on('error', error => { streamError = error; });
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
    if (values.orbitAfterScrub !== 'false' || values.shot !== '線路と街区' || values.playing !== '一時停止') {
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
