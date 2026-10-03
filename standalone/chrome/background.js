import { endpoint, sha, verifyResults, canonical } from "./protocol.js";
import * as cache from "./cache.js";
const defaults = {
  endpoint: "http://127.0.0.1:8098",
  token: "",
  identity: "",
  source: "auto",
  target: "zh",
  glossary: {},
  rules: {},
  viewport: true,
  floating_position: null,
};
const trusted = chrome.storage.local.setAccessLevel({
  accessLevel: "TRUSTED_CONTEXTS",
});
const sessionTrusted = chrome.storage.session.setAccessLevel({
  accessLevel: "TRUSTED_CONTEXTS",
});
const releaseUrl =
  "https://api.github.com/repos/T8mars/Comfyui-Index-Translate-T8/releases/latest";
const releasePage =
  "https://github.com/T8mars/Comfyui-Index-Translate-T8/releases/latest";
function releaseVersion(value) {
  const match = /^v?(\d+)\.(\d+)\.(\d+)$/.exec(value || "");
  return match ? match.slice(1).map(Number) : null;
}
function newerRelease(latest, current) {
  const a = releaseVersion(latest);
  const b = releaseVersion(current);
  if (!a || !b) return false;
  for (let index = 0; index < 3; index++) {
    if (a[index] !== b[index]) return a[index] > b[index];
  }
  return false;
}
async function checkUpdate() {
  const response = await fetch(releaseUrl, { cache: "no-store" });
  if (response.status === 404) return { available: false };
  if (!response.ok) throw new Error(`更新检查失败：HTTP ${response.status}`);
  const release = await response.json();
  const current = chrome.runtime.getManifest().version;
  const asset = `IndexTranslate-Chrome-${release.tag_name}.zip`;
  const available =
    !release.draft && !release.prerelease &&
    newerRelease(release.tag_name, current) &&
    release.assets?.some((item) => item.name === asset);
  const state = {
    available: !!available,
    version: available ? release.tag_name : current,
    url: releasePage,
    checkedAt: Date.now(),
  };
  await chrome.storage.local.set({ extensionUpdate: state });
  await chrome.action.setBadgeText({ text: available ? "NEW" : "" });
  if (available) await chrome.action.setBadgeBackgroundColor({ color: "#e05497" });
  return state;
}
async function config() {
  await trusted;
  const data = await chrome.storage.local.get("config");
  return { ...defaults, ...data.config };
}
async function save(value) {
  await trusted;
  await chrome.storage.local.set({ config: value });
}
let configurationWrites = Promise.resolve();
function updateConfig(change) {
  const result = configurationWrites.then(async () => {
    const value = change(await config());
    await save(value);
    return value;
  });
  configurationWrites = result.catch(() => {});
  return result;
}
function preferences(value) {
  const languages = [
    "zh",
    "en",
    "ja",
    "ko",
    "de",
    "fr",
    "es",
    "ru",
    "ar",
    "pt",
    "it",
    "vi",
    "th",
    "hi",
  ];
  if (
    !value ||
    !["auto", ...languages].includes(value.source) ||
    !languages.includes(value.target) ||
    !value.glossary ||
    typeof value.glossary !== "object" ||
    Array.isArray(value.glossary) ||
    Object.keys(value.glossary).length > 100 ||
    Object.values(value.glossary).some((v) => typeof v !== "string")
  )
    throw new Error("语言或术语设置错误");
  return {
    source: value.source,
    target: value.target,
    glossary: value.glossary,
  };
}
async function api(config, path, method = "GET", body, timeoutMs = 8000) {
  const base = endpoint(config.endpoint);
  const response = await fetch(base + path, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(config.token ? { Authorization: "Bearer " + config.token } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(timeoutMs),
  });
  let value;
  try {
    value = await response.json();
  } catch {
    throw new Error("本地服务返回了无效响应");
  }
  if (!response.ok) {
    const error = new Error(
      typeof value.detail === "string"
        ? value.detail
        : "本地服务请求失败：" + response.status,
    );
    error.status = response.status;
    throw error;
  }
  return value;
}
async function backend(config) {
  if (!config.token) throw new Error("请先在扩展设置中配对本地服务");
  const status = await api(config, "/api/status");
  if (status.settings.identity !== config.identity)
    throw new Error("服务身份已变化，请重新配对");
  return status;
}

const audioSessions = new Map();
const captureGrants = new Map();
let audioStartGeneration = 0;
let audioStarts = Promise.resolve();
let offscreenCreation = null;

async function translateVideo(cfg, value) {
  const text = String(value || "").trim();
  if (!text || text.length > 1200) throw new Error("视频字幕长度无效");
  await backend(cfg);
  const source_hash = await sha(text);
  const request_id = crypto.randomUUID();
  const submitted = await api(cfg, "/api/jobs", "POST", {
    request_id, expected_identity: cfg.identity, page_epoch: "video",
    ...preferences(cfg), output_budget: 512,
    paragraphs: [{id: "cue", text, source_hash}],
  }, 30000);
  for (let retry = 0; retry < 120; retry++) {
    const result = await api(cfg, "/api/jobs/" + submitted.id);
    if (result.status === "completed") {
      verifyResults(result, [{id: "cue", text, source_hash}]);
      return {text: result.results[0].text};
    }
    if (["failed", "cancelled"].includes(result.status))
      throw new Error(result.error || "视频字幕翻译失败");
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
  throw new Error("视频字幕翻译等待超时");
}

async function ensureOffscreen() {
  if (!offscreenCreation)
    offscreenCreation = (async () => {
      if (!(await chrome.offscreen.hasDocument()))
        await chrome.offscreen.createDocument({
          url: "offscreen.html",
          reasons: ["USER_MEDIA", "AUDIO_PLAYBACK"],
          justification: "用户主动启用视频翻译时采集当前标签页声音，在本地识别并继续播放原声音频",
        });
    })().finally(() => { offscreenCreation = null; });
  return offscreenCreation;
}

async function cancelSpeech(cfg, sid) {
  try { await api(cfg, `/api/speech/${encodeURIComponent(sid)}/cancel`, "POST", {}, 30000); }
  catch {}
}

async function audioStop(tabId, expectedSession = null, invalidateStart = true) {
  if (expectedSession && audioSessions.get(tabId) !== expectedSession) return;
  if (invalidateStart) ++audioStartGeneration;
  captureGrants.delete(tabId);
  const session = audioSessions.get(tabId);
  audioSessions.delete(tabId);
  if (!session) return;
  try { await chrome.runtime.sendMessage({type:"OFFSCREEN_STOP",tabId,captureId:session.captureId}); } catch {}
  if (session.sid) await cancelSpeech(session.cfg, session.sid);
}

async function sendSpeech(tabId, events, session) {
  for (const event of events || []) {
    if (audioSessions.get(tabId) !== session) return;
    if (!event.segment_final && !event.final) {
      const preview = String(event.preview_text || "");
      const text = (preview.startsWith(session.committed)
        ? preview.slice(session.committed.length) : preview).trim().slice(-300);
      if (text && text !== session.lastPreview) {
        session.lastPreview = text;
        try { await chrome.tabs.sendMessage(tabId, {type:"IT_VIDEO_SPEECH_PREVIEW",source:text}); }
        catch { await audioStop(tabId, session); }
      }
      continue;
    }
    const stable = String(event.stable_text || "");
    if (!stable.startsWith(session.committed)) session.committed = "";
    const text = stable.slice(session.committed.length).trim();
    session.committed = stable;
    session.lastPreview = "";
    if (text) {
      try { await chrome.tabs.sendMessage(tabId, {type:"IT_VIDEO_SPEECH",source:text}); }
      catch { await audioStop(tabId, session); }
    }
  }
}

async function audioFeed(tabId, value) {
  const session = audioSessions.get(tabId);
  if (!session?.sid || session.captureId !== value.captureId) return {};
  if (!Number.isInteger(value.samples) || value.samples < 1 || value.samples > 32000 ||
      typeof value.pcm_f32le_b64 !== "string" || value.pcm_f32le_b64.length > 180000)
    throw new Error("无效的音频帧");
  const current = session;
  current.queue = current.queue.then(async () => {
    if (audioSessions.get(tabId) !== current) return;
    const result = await api(current.cfg, `/api/speech/${encodeURIComponent(current.sid)}/feed`, "POST", {
      seq: current.seq, start_sample: current.samples,
      pcm_f32le_b64: value.pcm_f32le_b64,
    }, 180000);
    current.seq++;
    current.samples += value.samples;
    if (audioSessions.get(tabId) !== current) return;
    await sendSpeech(tabId, result.events, current);
    if (current.samples >= 9 * 60 * 16000) {
      const final = await api(current.cfg, `/api/speech/${encodeURIComponent(current.sid)}/finish`, "POST", {
        last_seq: current.seq - 1, total_samples: current.samples,
      }, 180000);
      await sendSpeech(tabId, final.events, current);
      const next = await api(current.cfg, "/api/speech/start", "POST", {}, 180000);
      if (audioSessions.get(tabId) !== current) {
        await cancelSpeech(current.cfg, next.session_id);
        return;
      }
      current.sid = next.session_id;
      current.seq = current.samples = 0;
      current.committed = "";
    }
  });
  try { await current.queue; return {}; }
  catch (error) {
    if (audioSessions.get(tabId) === current) {
      await audioStop(tabId, current);
      try { await chrome.tabs.sendMessage(tabId, {type:"IT_VIDEO_SPEECH",source:"",note:error.message}); } catch {}
    }
    throw error;
  }
}

function audioStart(tabId, streamId, cfg) {
  const generation = ++audioStartGeneration;
  const result = audioStarts.then(() => audioStartNow(tabId, streamId, cfg, generation));
  audioStarts = result.catch(() => {});
  return result;
}
async function audioStartNow(tabId, streamId, cfg, generation) {
  if (generation !== audioStartGeneration) return;
  for (const id of [...audioSessions.keys()]) await audioStop(id, null, false);
  if (generation !== audioStartGeneration) return;
  const session = {cfg, captureId:crypto.randomUUID(), sid:null, seq:0, samples:0,
    committed:"", lastPreview:"", queue:Promise.resolve()};
  audioSessions.set(tabId, session);
  try {
    await ensureOffscreen();
    if (audioSessions.get(tabId) !== session) return;
    const started = await chrome.runtime.sendMessage({type:"OFFSCREEN_CAPTURE",tabId,streamId,captureId:session.captureId});
    if (!started?.ok) throw new Error(started?.error || "标签页音频采集失败");
    if (audioSessions.get(tabId) !== session) {
      try { await chrome.runtime.sendMessage({type:"OFFSCREEN_STOP",tabId,captureId:session.captureId}); } catch {}
      return;
    }
    const status = await backend(cfg);
    if (audioSessions.get(tabId) !== session) return;
    if (!status.capabilities?.includes("speech_r2t2_v1"))
      throw new Error("请升级独立整合包，安装 R2T2 语音组件");
    const result = await api(cfg, "/api/speech/start", "POST", {}, 180000);
    if (audioSessions.get(tabId) !== session) {
      await cancelSpeech(cfg, result.session_id);
      return;
    }
    session.sid = result.session_id;
    await chrome.runtime.sendMessage({type:"OFFSCREEN_READY",tabId,captureId:session.captureId});
    if (audioSessions.get(tabId) !== session) return;
    await chrome.tabs.sendMessage(tabId, {type:"IT_VIDEO_SPEECH",source:"",ready:true});
  } catch (error) {
    if (audioSessions.get(tabId) === session) {
      await audioStop(tabId, session);
      try { await chrome.tabs.sendMessage(tabId, {type:"IT_VIDEO_SPEECH",source:"",note:error.message}); } catch {}
    }
  }
}
function ui(sender) {
  return (
    sender.id === chrome.runtime.id &&
    sender.url?.startsWith("chrome-extension://" + chrome.runtime.id + "/")
  );
}
async function startVideoForTab(tabId, forceSpeech, targetIndex = null) {
  await audioStop(tabId);
  const generation = audioStartGeneration;
  await audioStarts;
  if (generation !== audioStartGeneration) return {mode:"none",reason:"视频翻译已取消，请重试"};
  await chrome.scripting.executeScript({target:{tabId},files:["video.js"]});
  const result = await chrome.tabs.sendMessage(tabId,
    {type:"IT_VIDEO_START",forceSpeech,targetIndex}, {frameId:0});
  if (!result || result.mode === "none") throw new Error(result?.reason || "未找到可见视频");
  if (result.mode === "speech") {
    try {
      const streamId = await chrome.tabCapture.getMediaStreamId({targetTabId:tabId});
      const cfg = await config();
      if (generation !== audioStartGeneration) return {mode:"none",reason:"视频翻译已取消，请重试"};
      audioStart(tabId, streamId, cfg).catch(() => {});
    } catch {
      const note = "请先点击 Chrome 工具栏的 T8 扩展，再点「直接识别声音」授权当前标签页；之后可用视频内按钮。";
      await chrome.tabs.sendMessage(tabId, {type:"IT_VIDEO_SPEECH",source:"",note}, {frameId:0});
      return {...result, authorizationRequired:true, note};
    }
  }
  return result;
}
function page(sender) {
  return (
    sender.id === chrome.runtime.id &&
    sender.tab &&
    sender.frameId === 0 &&
    /^https?:\/\//.test(sender.url || "")
  );
}
let registrationWrites = Promise.resolve();
function registrations() {
  const result = registrationWrites.then(reconcileRegistrations);
  registrationWrites = result.catch(() => {});
  return result;
}
async function reconcileRegistrations() {
  // Static content scripts show controls on every normal webpage. Remove
  // legacy per-site registrations during upgrades; cfg.rules only controls
  // automatic translation, not whether buttons appear.
  const existing = await chrome.scripting.getRegisteredContentScripts();
  const legacy = existing.filter((item) => item.id.startsWith("site-"));
  if (legacy.length)
    await chrome.scripting.unregisterContentScripts({
      ids: legacy.map((item) => item.id),
    });
}
async function cacheKey(cfg, status, paragraph) {
  return sha(
    JSON.stringify(
      canonical({
        endpoint: cfg.endpoint,
        identity: cfg.identity,
        revision: status.revision,
        prompt: status.prompt_version,
        runtime: {
          device: status.settings.device,
          precision: status.settings.precision,
          context_limit: status.settings.context_limit,
          output_budget: 512,
        },
        source: cfg.source,
        target: cfg.target,
        glossary: cfg.glossary,
        text: paragraph.text,
      }),
    ),
  );
}
const recordKey = (tab, request) => `job:${tab}:${request}`;
const submissions = new Map();
async function matchingTabs(origin) {
  await sessionTrusted;
  const values = await chrome.storage.session.get(null);
  const ids = new Set(
    Object.entries(values)
      .filter(
        ([key, value]) => key.startsWith("page:") && value.origin === origin,
      )
      .map(([key]) => Number(key.slice(5))),
  );
  try {
    for (const tab of await chrome.tabs.query({ url: origin + "/*" }))
      ids.add(tab.id);
  } catch {}
  return [...ids].map((id) => ({ id }));
}
const stopToken = (marker) => marker?.generation ?? marker?.created ?? null;
async function isCancelled(tab, epoch, generation, legacyStarted = 0) {
  await sessionTrusted;
  const keys = [`cancel:${tab}:${epoch}`, `stopped:${tab}`];
  const values = await chrome.storage.session.get(keys);
  if (values[keys[0]]) return true;
  if (generation === undefined)
    return (values[keys[1]]?.created ?? 0) >= legacyStarted;
  return stopToken(values[keys[1]]) !== generation;
}
async function record(sender, requestId) {
  await sessionTrusted;
  const key = recordKey(sender.tab.id, requestId);
  const result = (await chrome.storage.session.get(key))[key];
  if (!result) throw new Error("任务状态已丢失，请重试");
  return { key, result };
}
async function cancelRecord(key, value, cfg) {
  if (value.terminal || (!value.id && !value.request_body)) {
    await chrome.storage.session.remove(key);
    return;
  }
  // Keep credentials only in trusted session storage so re-pairing cannot strand a job.
  const owner = {
    endpoint: value.endpoint,
    token:
      value.credential ||
      (value.endpoint === cfg.endpoint && value.identity === cfg.identity
        ? cfg.token
        : ""),
  };
  await chrome.storage.session.set({
    [key]: { ...value, cancel_pending: true },
  });
  if (!owner.token) return;
  const cancellation = value.cancel_by_request && value.request_id
    ? "/api/requests/" + encodeURIComponent(value.request_id)
    : "/api/jobs/" + value.id;
  try {
    await api(owner, cancellation, "DELETE");
    await chrome.storage.session.remove(key);
  } catch (error) {
    if (
      error.status === 401 &&
      cfg.endpoint === value.endpoint &&
      cfg.identity === value.identity &&
      cfg.token &&
      cfg.token !== owner.token
    ) {
      try {
        await api(cfg, cancellation, "DELETE");
        await chrome.storage.session.remove(key);
      } catch {}
    }
    // A later alarm or repeated cancellation retries after a transient service outage.
  }
}
async function cancelTab(tabId, epoch) {
  await sessionTrusted;
  const marker = epoch ? `cancel:${tabId}:${epoch}` : `stopped:${tabId}`;
  await chrome.storage.session.set({
    [marker]: { created: Date.now(), generation: crypto.randomUUID() },
  });
  const cfg = await config();
  const values = await chrome.storage.session.get(null);
  for (const [key, value] of Object.entries(values)) {
    if (
      key.startsWith("job:" + tabId + ":") &&
      (!epoch || value.epoch === epoch)
    ) {
      await cancelRecord(key, value, cfg);
    }
  }
}
async function submit(message, sender, cfg) {
  const started = Date.now();
  await sessionTrusted;
  const stoppedKey = `stopped:${sender.tab.id}`;
  const generation = stopToken(
    (await chrome.storage.session.get(stoppedKey))[stoppedKey],
  );
  if (
    typeof message.request_id !== "string" ||
    message.request_id.length < 8 ||
    message.request_id.length > 128 ||
    typeof message.page_epoch !== "string" ||
    !message.page_epoch.length ||
    message.page_epoch.length > 128 ||
    !Array.isArray(message.paragraphs) ||
    !message.paragraphs.length ||
    message.paragraphs.length > 16
  )
    throw new Error("无效的段落请求");
  cfg = { ...cfg, ...preferences(message.preferences || cfg) };
  const paragraphs = [],
    ids = new Set();
  let total = 0;
  for (const p of message.paragraphs) {
    if (
      !p ||
      typeof p.id !== "string" ||
      !p.id.length ||
      p.id.length > 100 ||
      ids.has(p.id) ||
      typeof p.text !== "string" ||
      !p.text.trim() ||
      p.text.length > 12000
    )
      throw new Error("段落或原文校验错误");
    ids.add(p.id);
    total += p.text.length;
    if (total > 48000) throw new Error("请求总长度超过限制");
    const checksum = await sha(p.text);
    if (p.source_hash && p.source_hash !== checksum)
      throw new Error("原文校验错误");
    paragraphs.push({ id: p.id, text: p.text, source_hash: checksum });
  }
  const signature = await sha(
    JSON.stringify(
      canonical({
        epoch: message.page_epoch,
        paragraphs,
        ...preferences(cfg),
        endpoint: cfg.endpoint,
        identity: cfg.identity,
      }),
    ),
  );
  const key = recordKey(sender.tab.id, message.request_id);
  const concurrent = submissions.get(key);
  if (concurrent) {
    if (concurrent.signature !== signature)
      throw new Error("同一 request_id 不能提交不同内容");
    return concurrent.promise;
  }
  const promise = submitPrepared(
    message,
    sender,
    cfg,
    paragraphs,
    signature,
    started,
    generation,
    key,
  );
  submissions.set(key, { signature, promise });
  try {
    return await promise;
  } finally {
    if (submissions.get(key)?.promise === promise) submissions.delete(key);
  }
}
async function submitPrepared(
  message,
  sender,
  cfg,
  paragraphs,
  signature,
  started,
  generation,
  key,
) {
  if (await isCancelled(sender.tab.id, message.page_epoch, generation))
    throw new Error("该页面任务已取消");
  const previous = (await chrome.storage.session.get(key))[key];
  if (previous) {
    if (previous.request_signature !== signature)
      throw new Error("同一 request_id 不能提交不同内容");
    if (previous.cancel_pending) throw new Error("该页面任务已取消");
    if (!previous.id && previous.pending.length) {
      await recoverSubmission(key, previous, cfg, sender.tab.id);
    }
    return { request_id: message.request_id };
  }
  const status = await backend(cfg);
  const cached = [],
    pending = [],
    keys = Object.create(null);
  for (const p of paragraphs) {
    keys[p.id] = await cacheKey(cfg, status, p);
    let hit;
    try {
      hit = await cache.get(keys[p.id]);
    } catch {}
    if (hit) cached.push({ ...hit, id: p.id, source_hash: p.source_hash });
    else pending.push(p);
  }
  const info = {
    epoch: message.page_epoch,
    endpoint: cfg.endpoint,
    identity: cfg.identity,
    credential: cfg.token,
    request_signature: signature,
    revision: status.revision,
    prompt: status.prompt_version,
    runtime: {
      device: status.settings.device,
      precision: status.settings.precision,
      context_limit: status.settings.context_limit,
      output_budget: 512,
    },
    paragraphs,
    pending,
    cached,
    keys,
    created: started,
    stop_generation: generation,
    id: null,
  };
  if (pending.length) {
    if (!status.capabilities?.includes("cancel_by_request_v1"))
      throw new Error("请升级到 0.1.2 整合包，以便安全恢复或取消网页任务");
    if (!status.capabilities.includes("expected_identity_v1"))
      throw new Error("请升级到 0.1.3 整合包，以便验证翻译任务的服务身份");
    info.cancel_by_request = true;
    info.request_id = message.request_id;
    info.request_body = {
      request_id: message.request_id,
      page_epoch: message.page_epoch,
      ...preferences(cfg),
      output_budget: 512,
      expected_identity: cfg.identity,
      paragraphs: pending,
    };
    // Preserve ownership before POST: its response may be lost after the server accepted it.
    await chrome.storage.session.set({ [key]: info });
    if (await isCancelled(sender.tab.id, message.page_epoch, generation)) {
      await cancelRecord(key, info, cfg);
      throw new Error("该页面任务已取消");
    }
    const job = await api(cfg, "/api/jobs", "POST", info.request_body);
    info.id = job.id;
  }
  const current = await config();
  if (
    (await isCancelled(sender.tab.id, message.page_epoch, generation)) ||
    current.endpoint !== cfg.endpoint ||
    current.identity !== cfg.identity ||
    current.token !== cfg.token
  ) {
    await cancelRecord(key, info, cfg);
    throw new Error("该页面任务已取消或配对已变化");
  }
  await chrome.storage.session.set({ [key]: info });
  if (await isCancelled(sender.tab.id, message.page_epoch, generation)) {
    await cancelTab(sender.tab.id, message.page_epoch);
    throw new Error("该页面任务已取消");
  }
  return { request_id: message.request_id };
}
async function recoverSubmission(key, info, cfg, tabId) {
  if (info.cancel_pending || await isCancelled(tabId, info.epoch, info.stop_generation, info.created))
    throw new Error("该页面任务已取消");
  if (cfg.endpoint !== info.endpoint || cfg.identity !== info.identity)
    throw new Error("任务的服务版本已变化");
  let job;
  try {
    job = await api(cfg, "/api/requests/" + encodeURIComponent(info.request_id));
  } catch (error) {
    if (error.status !== 404) throw error;
    if (await isCancelled(tabId, info.epoch, info.stop_generation, info.created))
      throw new Error("该页面任务已取消");
    const status = await backend(cfg);
    if (!status.capabilities?.includes("expected_identity_v1"))
      throw new Error("请升级到 0.1.3 整合包，以便验证翻译任务的服务身份");
    job = await api(cfg, "/api/jobs", "POST", {
      ...info.request_body,
      expected_identity: info.identity,
    });
  }
  if (job.identity !== info.identity)
    throw new Error("任务的服务身份已变化，请重新配对");
  const value = { ...info, id: job.id, credential: cfg.token };
  const current = await config();
  if (
    await isCancelled(tabId, info.epoch, info.stop_generation, info.created) ||
    current.endpoint !== cfg.endpoint || current.identity !== cfg.identity ||
    current.token !== cfg.token
  ) {
    await cancelRecord(key, value, cfg);
    throw new Error("该页面任务已取消或配对已变化");
  }
  await chrome.storage.session.set({ [key]: value });
  if (await isCancelled(tabId, info.epoch, info.stop_generation, info.created)) {
    await cancelRecord(key, value, cfg);
    throw new Error("该页面任务已取消");
  }
  return value;
}
async function handle(message, sender) {
  if (sender.id !== chrome.runtime.id) throw new Error("无效的消息来源");
  if (message.type === "VIDEO_CONTROL" && page(sender)) {
    if (message.action === "stop") {
      await audioStop(sender.tab.id);
      await chrome.tabs.sendMessage(sender.tab.id, {type:"IT_VIDEO_STOP"}, {frameId:0});
      return {stopped:true};
    }
    if (message.action !== "start" || !Number.isInteger(message.targetIndex) || message.targetIndex < 0)
      throw new Error("无效的视频操作");
    return startVideoForTab(sender.tab.id, !!message.forceSpeech, message.targetIndex);
  }
  if (message.type === "VIDEO_CAPTURE_ID" && ui(sender)) {
    if (!Number.isInteger(message.tabId)) throw new Error("无效的标签页");
    await audioStop(message.tabId);
    const generation = audioStartGeneration;
    const streamId = await chrome.tabCapture.getMediaStreamId({targetTabId:message.tabId});
    if (generation !== audioStartGeneration) throw new Error("页面音频授权已取消，请重试");
    captureGrants.set(message.tabId, streamId);
    return {streamId};
  }
  const cfg = await config();
  if (message.type === "VIDEO_TRANSLATE" && page(sender))
    return translateVideo(cfg, message.text);
  if (message.type === "VIDEO_SCOPE" && page(sender)) {
    const status = await backend(cfg);
    return {scope: JSON.stringify(canonical({endpoint:cfg.endpoint,identity:cfg.identity,
      source:cfg.source,target:cfg.target,glossary:cfg.glossary,
      revision:status.revision,prompt:status.prompt_version,
      runtime:{device:status.settings.device,precision:status.settings.precision,
        context_limit:status.settings.context_limit}}))};
  }
  if (message.type === "VIDEO_AUDIO_STOP_PAGE" && page(sender)) {
    await audioStop(sender.tab.id);
    return {};
  }
  if (message.type === "VIDEO_PCM" &&
      sender.url === chrome.runtime.getURL("offscreen.html"))
    return audioFeed(message.tabId, message);
  if (message.type === "VIDEO_CAPTURE_ENDED" &&
      sender.url === chrome.runtime.getURL("offscreen.html")) {
    if (audioSessions.get(message.tabId)?.captureId !== message.captureId) return {};
    await audioStop(message.tabId);
    try { await chrome.tabs.sendMessage(message.tabId, {
      type:"IT_VIDEO_SPEECH",source:"",note:message.reason || "视频声音采集已结束"}); } catch {}
    return {};
  }
  if (message.type === "PUBLIC" && page(sender)) {
    const origin = new URL(sender.url).origin;
    await sessionTrusted;
    await chrome.storage.session.set({
      ["page:" + sender.tab.id]: { origin, created: Date.now() },
    });
    return {
      source: cfg.source,
      target: cfg.target,
      glossary: cfg.glossary,
      auto: !!cfg.rules[origin],
      viewport: cfg.viewport,
      floating_position: cfg.floating_position,
    };
  }
  if (message.type === "FLOAT_POSITION" && page(sender)) {
    if (![message.x, message.y].every((value) =>
      Number.isFinite(value) && value >= 0 && value <= 1))
      throw new Error("悬浮按钮位置无效");
    await updateConfig((current) => ({
      ...current,
      floating_position: { x: message.x, y: message.y },
    }));
    return {};
  }
  if (message.type === "SUBMIT" && page(sender))
    return submit(message, sender, cfg);
  if (message.type === "POLL" && page(sender)) {
    const found = await record(sender, message.request_id);
    const key = found.key;
    let info = found.result;
    if (
      info.epoch !== message.page_epoch ||
      cfg.endpoint !== info.endpoint ||
      cfg.identity !== info.identity ||
      info.cancel_pending
    )
      throw new Error("任务的页面或服务版本已变化");
    if (!info.id && info.pending.length)
      info = await recoverSubmission(key, info, cfg, sender.tab.id);
    let job = info.id
      ? verifyResults(await api(cfg, "/api/jobs/" + info.id), info.pending)
      : {
          status: "completed",
          results: [],
          done: 0,
          total: 0,
          page_epoch: info.epoch,
        };
    if (job.page_epoch !== info.epoch)
      throw new Error("服务返回了其他页面的结果");
    if (
      info.id &&
      (job.id !== info.id ||
        job.identity !== info.identity ||
        job.model_revision !== info.revision ||
        job.prompt_version !== info.prompt)
    )
      throw new Error("任务模型版本已变化，请重新提交");
    if (info.id && !job.runtime)
      throw new Error("本地服务缺少任务推理快照，请升级到 0.1.1 整合包");
    if (
      info.id &&
      JSON.stringify(canonical(job.runtime)) !==
        JSON.stringify(canonical(info.runtime))
    )
      throw new Error("任务推理档位已变化，请重新提交");
    if (["completed", "failed", "cancelled"].includes(job.status)) {
      await chrome.storage.session.set({ [key]: { ...info, terminal: true } });
      for (const result of job.results) {
        const { id, source_hash, ...value } = result;
        try {
          await cache.put(info.keys[id], value);
        } catch {}
      }
    }
    job = {
      ...job,
      results: [...info.cached, ...job.results],
      done: info.cached.length + job.done,
      total: info.paragraphs.length,
    };
    verifyResults(job, info.paragraphs);
    return job;
  }
  if (message.type === "FORGET" && page(sender)) {
    const { key, result: info } = await record(sender, message.request_id);
    if (info.epoch === message.page_epoch) await cancelRecord(key, info, cfg);
    return {};
  }
  if (message.type === "CANCEL" && page(sender)) {
    await cancelTab(sender.tab.id, message.page_epoch);
    return {};
  }
  if (!ui(sender)) throw new Error("该操作只允许扩展设置页面使用");
  if (message.type === "RESET_FLOAT_POSITION") {
    await updateConfig(current => ({...current,floating_position:null}));
    for (const tab of await chrome.tabs.query({}))
      try { await chrome.tabs.sendMessage(tab.id,{type:"IT_FLOAT_RESET"}, {frameId:0}); } catch {}
    return {};
  }
  if (message.type === "VIDEO_WAIT_IDLE") { await audioStarts; return {}; }
  if (message.type === "VIDEO_START_UI") {
    if (!Number.isInteger(message.tabId)) throw new Error("无效的标签页");
    return startVideoForTab(message.tabId, !!message.forceSpeech);
  }
  if (message.type === "VIDEO_AUDIO_START") {
    if (!Number.isInteger(message.tabId) || typeof message.streamId !== "string" ||
        !message.streamId || message.streamId.length > 4096 || /[\x00-\x1f]/.test(message.streamId))
      throw new Error("无效的标签页音频授权");
    if (captureGrants.get(message.tabId) !== message.streamId)
      throw new Error("标签页音频授权已过期，请重新启动视频翻译");
    captureGrants.delete(message.tabId);
    audioStart(message.tabId, message.streamId, cfg).catch(async error => {
      try { await chrome.tabs.sendMessage(message.tabId, {
        type:"IT_VIDEO_SPEECH",source:"",note:error.message}); } catch {}
    });
    return {starting:true};
  }
  if (message.type === "VIDEO_AUDIO_STOP") {
    await audioStop(message.tabId);
    return {stopped:true};
  }
  if (message.type === "CONFIG") {
    const { token, ...publicConfig } = cfg;
    return { ...publicConfig, paired: !!token };
  }
  if (message.type === "PAIR") {
    const base = endpoint(message.endpoint);
    const paired = await api({ endpoint: base }, "/api/pair", "POST", {
      code: message.code,
      extension_id: chrome.runtime.id,
    });
    const values = await chrome.storage.session.get(null);
    for (const [key, info] of Object.entries(values))
      if (key.startsWith("job:")) await cancelRecord(key, info, cfg);
    const current = await updateConfig((current) => ({
      ...current,
      endpoint: base,
      token: paired.token,
      identity: paired.identity,
    }));
    const remaining = await chrome.storage.session.get(null);
    for (const [key, info] of Object.entries(remaining))
      if (
        key.startsWith("job:") &&
        info.cancel_pending &&
        info.endpoint === current.endpoint &&
        info.identity === current.identity
      )
        await cancelRecord(key, info, current);
    return { paired: true };
  }
  if (message.type === "PREFERENCES") {
    const value = preferences(message);
    await updateConfig((current) => ({
      ...current,
      ...value,
      viewport: !!message.viewport,
    }));
    return {};
  }
  if (message.type === "RULE") {
    const origin = new URL(message.origin).origin;
    if (!/^https?:\/\//.test(origin)) throw new Error("只支持 HTTP/HTTPS 网页");
    if (
      message.enabled &&
      !(await chrome.permissions.contains({ origins: [origin + "/*"] }))
    )
      throw new Error("未授予本站权限");
    await updateConfig((current) => ({
      ...current,
      rules: { ...current.rules, [origin]: !!message.enabled },
    }));
    await registrations();
    if (!message.enabled) {
      const tabs = await matchingTabs(origin);
      for (const tab of tabs)
        try {
          await chrome.tabs.sendMessage(tab.id, { type: "IT_STOP" });
        } catch {}
    }
    return {};
  }
  if (message.type === "CHECK") {
    const status = await backend(cfg);
    return {
      connected: true,
      model: status.settings.model_id,
      model_ready: status.model_ready,
    };
  }
  if (message.type === "CLEAR_CACHE") {
    await cache.clear();
    return {};
  }
  if (message.type === "UPDATE_STATUS") {
    const data = await chrome.storage.local.get("extensionUpdate");
    return data.extensionUpdate || { available: false, url: releasePage };
  }
  if (message.type === "UPDATE_CHECK") return checkUpdate();
  throw new Error("未知操作");
}
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  handle(message, sender).then(
    (value) => respond({ ok: true, value }),
    (error) => respond({ ok: false, error: error.message }),
  );
  return true;
});
chrome.runtime.onInstalled.addListener(() => {
  registrations();
  checkUpdate().catch(() => {});
});
chrome.runtime.onStartup.addListener(() => {
  registrations();
  checkUpdate().catch(() => {});
});
chrome.tabs.onRemoved.addListener(async (tabId) => {
  await cancelTab(tabId);
  await audioStop(tabId);
  await chrome.storage.session.remove("page:" + tabId);
});
chrome.tabs.onUpdated.addListener((tabId, change) => {
  if (change.status === "loading") { cancelTab(tabId); audioStop(tabId); }
});
chrome.permissions.onRemoved.addListener(async () => {
  const cfg = await config();
  for (const [origin, enabled] of Object.entries(cfg.rules))
    if (
      enabled &&
      !(await chrome.permissions.contains({ origins: [origin + "/*"] }))
    ) {
      await updateConfig((current) => ({
        ...current,
        rules: { ...current.rules, [origin]: false },
      }));
      const tabs = await matchingTabs(origin);
      for (const tab of tabs) {
        await cancelTab(tab.id);
        try {
          await chrome.tabs.sendMessage(tab.id, { type: "IT_STOP" });
        } catch {}
      }
    }
  await registrations();
});
chrome.alarms.create("clean-jobs", { periodInMinutes: 5 });
chrome.alarms.create("check-update", { periodInMinutes: 1440 });
chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === "check-update") {
    await checkUpdate().catch(() => {});
    return;
  }
  if (alarm.name !== "clean-jobs") return;
  const data = await chrome.storage.session.get(null);
  const cfg = await config();
  for (const [key, value] of Object.entries(data))
    if (key.startsWith("job:") && value.cancel_pending)
      await cancelRecord(key, value, cfg);
    else if (
      (key.startsWith("cancel:") || key.startsWith("stopped:")) &&
      Date.now() - value.created > 60 * 60 * 1000
    )
      await chrome.storage.session.remove(key);
    else if (
      key.startsWith("job:") &&
      Date.now() - value.created > 60 * 60 * 1000
    ) {
      const tabId = Number(key.split(":")[1]);
      await cancelTab(tabId, value.epoch);
    }
});
