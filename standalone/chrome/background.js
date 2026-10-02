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
async function api(config, path, method = "GET", body) {
  const base = endpoint(config.endpoint);
  const response = await fetch(base + path, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(config.token ? { Authorization: "Bearer " + config.token } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(8000),
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
function ui(sender) {
  return (
    sender.id === chrome.runtime.id &&
    sender.url?.startsWith("chrome-extension://" + chrome.runtime.id + "/")
  );
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
  const cfg = await config();
  const existing = await chrome.scripting.getRegisteredContentScripts();
  if (existing.length)
    await chrome.scripting.unregisterContentScripts({
      ids: existing.map((item) => item.id),
    });
  const scripts = [];
  for (const origin of Object.keys(cfg.rules)) {
    if (
      (await chrome.permissions.contains({ origins: [origin + "/*"] }))
    )
      scripts.push({
        id: "site-" + (await sha(origin)),
        matches: [origin + "/*"],
        js: ["content.js"],
        css: ["content.css"],
        runAt: "document_idle",
        allFrames: false,
        persistAcrossSessions: true,
      });
  }
  if (scripts.length) await chrome.scripting.registerContentScripts(scripts);
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
  const cfg = await config();
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
  await chrome.storage.session.remove("page:" + tabId);
});
chrome.tabs.onUpdated.addListener((tabId, change) => {
  if (change.status === "loading") cancelTab(tabId);
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
