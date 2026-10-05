"use strict";
const $ = (id) => document.getElementById(id);
const languages = {
  auto: "自动识别",
  zh: "中文",
  en: "英语",
  ja: "日语",
  ko: "韩语",
  de: "德语",
  fr: "法语",
  es: "西班牙语",
  ru: "俄语",
  ar: "阿拉伯语",
  pt: "葡萄牙语",
  it: "意大利语",
  vi: "越南语",
  th: "泰语",
  hi: "印地语",
};
let running = null,
  recovering = null,
  epoch = 0,
  status,
  pageIdentity = null,
  initialized = false;
let warmupJob = null, warmupWatching = false, warmupSubmitting = false,
  warmupError = "", warmupConnectionError = "";
const terminal = new Set(["completed", "failed", "cancelled"]);
function notice(message, error = false) {
  $("notice").textContent = message;
  $("notice").hidden = !message;
  $("notice").classList.toggle("error", error);
}
async function api(path, options = {}, retrySession = true) {
  const headers = { "Content-Type": "application/json", ...options.headers };
  if (options.method && options.method !== "GET" && path.startsWith("/api/")) {
    if (!pageIdentity) throw new Error("本地服务尚未就绪，请稍后重试");
    headers["X-Expected-Identity"] ??= pageIdentity;
  }
  const response = await fetch(path, {
    signal: /^\/api\/(jobs|requests)(\/|$)/.test(path)
      ? AbortSignal.timeout(8000)
      : undefined,
    ...options,
    headers,
  });
  if (response.status === 401 && retrySession && path.startsWith("/api/")) {
    const home = await fetch("/", { cache: "no-store" });
    if (home.ok) return api(path, { ...options, headers }, false);
  }
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(
      typeof result.detail === "string"
        ? result.detail
        : JSON.stringify(result.detail || result.error),
    );
    error.status = response.status;
    throw error;
  }
  return result;
}
function post(path, value = {}) {
  return api(path, { method: "POST", body: JSON.stringify(value) });
}
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function hash(text) {
  return [
    ...new Uint8Array(
      await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)),
    ),
  ]
    .map((x) => x.toString(16).padStart(2, "0"))
    .join("");
}
function option(select, value, label, disabled = false) {
  const el = document.createElement("option");
  el.value = value;
  el.textContent = label;
  el.disabled = disabled;
  select.append(el);
}
for (const [id, label] of Object.entries(languages)) {
  option($("source"), id, label);
  if (id !== "auto") option($("target"), id, label);
}
$("target").value = "zh";
function showPanel(panelId) {
  for (const button of document.querySelectorAll(".tab"))
    button.classList.toggle("active", button.dataset.panel === panelId);
  for (const panel of document.querySelectorAll(".panel"))
    panel.hidden = panel.id !== panelId;
}
for (const button of document.querySelectorAll(".tab"))
  button.addEventListener("click", () => showPanel(button.dataset.panel));
for (const button of document.querySelectorAll(".quick-action"))
  button.addEventListener("click", () => {
    showPanel(button.dataset.openPanel);
    if (button.dataset.focus) $(button.dataset.focus).focus();
  });
$("endpoint").textContent = location.origin;
$("input").addEventListener("input", () => {
  $("length").textContent = `${$("input").value.length} 字符`;
});
$("swap").addEventListener("click", () => {
  if ($("source").value === "auto") {
    notice("请先选择原文语言，再交换语言。");
    return;
  }
  const source = $("source").value;
  $("source").value = $("target").value;
  $("target").value = source;
  const text = $("output").value;
  if (text) {
    $("input").value = text;
    $("output").value = "";
    $("input").dispatchEvent(new Event("input"));
  }
});
$("copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("output").value);
    notice("译文已复制。");
  } catch {
    notice("无法访问剪贴板，可直接选中译文复制。", true);
  }
});
function busy(value) {
  $("submit").disabled = value;
  $("cancel").disabled = !value;
}
function split(text) {
  const pieces = [];
  for (const block of text.trim().split(/\n\s*\n/)) {
    let part = "";
    for (const sentence of block.match(
      /[^。！？.!?\n]+[。！？.!?\n]*|[。！？.!?\n]+/g,
    ) || [block]) {
      if ((part + sentence).length > 800 && part) {
        pieces.push(part);
        part = "";
      }
      if (sentence.length > 800) {
        if (part) pieces.push(part);
        part = "";
        const characters = Array.from(sentence);
        for (let i = 0; i < characters.length; i += 800)
          pieces.push(characters.slice(i, i + 800).join(""));
      } else part += sentence;
    }
    if (part.trim()) pieces.push(part.trim());
  }
  return pieces;
}
function saveRequest(info) {
  sessionStorage.setItem("index-request", JSON.stringify(info));
}
function clearRequest(info) {
  const saved = sessionStorage.getItem("index-request");
  if (saved && JSON.parse(saved).request_id === info.request_id)
    sessionStorage.removeItem("index-request");
  if (sessionStorage.getItem("index-job") === info.id)
    sessionStorage.removeItem("index-job");
}
async function cancelRequest(info) {
  info.cancel_pending = true;
  saveRequest(info);
  try {
    await api(
      info.request_id
        ? `/api/requests/${encodeURIComponent(info.request_id)}`
        : `/api/jobs/${info.id}`,
      { method: "DELETE" },
    );
    clearRequest(info);
    if (running === info) {
      running = null;
      busy(false);
      $("progress").textContent = "已请求取消";
    }
  } catch (error) {
    notice("取消尚未确认，将继续重试：" + error.message, true);
    if (running === info) busy(true);
  }
}
async function watch(job, info, myEpoch) {
  if (myEpoch !== epoch) return;
  info.id = job.id;
  saveRequest(info);
  busy(true);
  while (myEpoch === epoch) {
    $("progress").textContent =
      `${{ queued: "排队中", loading: "正在加载模型", running: "翻译中", completed: "已完成", failed: "失败", cancelled: "已取消" }[job.status]} · ${job.done}/${job.total} 段`;
    $("output").value = job.results.map((item) => item.text).join("\n\n");
    if (terminal.has(job.status)) {
      if (job.error) notice(job.error, job.status === "failed");
      clearRequest(info);
      running = null;
      busy(false);
      await refresh();
      return;
    }
    await delay(900);
    if (myEpoch !== epoch) return;
    job = await api(`/api/jobs/${job.id}`);
  }
}
async function resumeRequest(info, myEpoch) {
  if (recovering === info) return;
  recovering = info;
  try {
    const service = await api("/api/status");
    if (info.identity && info.identity !== service.settings.identity) {
      clearRequest(info);
      running = null;
      busy(false);
      notice("本地服务已更换，请重新提交文本。", true);
      return;
    }
    if (info.cancel_pending) return await cancelRequest(info);
    let job;
    try {
      job = await api(
        info.id
          ? `/api/jobs/${info.id}`
          : `/api/requests/${encodeURIComponent(info.request_id)}`,
      );
    } catch (error) {
      if (error.status !== 404 || !info.body || myEpoch !== epoch) throw error;
      if (info.identity && !info.body.expected_identity)
        info.body.expected_identity = info.identity;
      job = await post("/api/jobs", info.body);
    }
    if (myEpoch !== epoch) return;
    await watch(job, info, myEpoch);
  } catch (error) {
    if (myEpoch !== epoch) return;
    if (error.status >= 400 && error.status < 500 && error.status !== 401) {
      clearRequest(info);
      running = null;
      busy(false);
    } else {
      $("progress").textContent = "任务确认待恢复";
      busy(true);
    }
    notice(error.message, true);
  } finally {
    if (recovering === info) recovering = null;
  }
}
$("submit").addEventListener("click", async () => {
  notice("");
  const text = $("input").value;
  if (!text.trim()) return notice("请输入要翻译的文本。");
  const pieces = split(text);
  if (pieces.length > 16)
    return notice("一次最多翻译 16 段，请分次提交。", true);
  const myEpoch = ++epoch;
  busy(true);
  $("output").value = "";
  try {
    const glossary = JSON.parse($("glossary").value || "{}");
    const paragraphs = await Promise.all(
      pieces.map(async (text, i) => ({
        id: String(i),
        text,
        source_hash: await hash(text),
      })),
    );
    if (myEpoch !== epoch) return;
    const body = {
      request_id: crypto.randomUUID(),
      expected_identity: status.settings.identity,
      page_epoch: "manual",
      source: $("source").value,
      target: $("target").value,
      output_budget: Number($("budget").value),
      glossary,
      paragraphs,
    };
    running = {
      request_id: body.request_id,
      identity: status.settings.identity,
      body,
      id: null,
    };
    saveRequest(running);
    await resumeRequest(running, myEpoch);
  } catch (error) {
    if (myEpoch !== epoch) return;
    notice(error.message, true);
    busy(false);
  }
});
$("cancel").addEventListener("click", async () => {
  ++epoch;
  if (running) await cancelRequest(running);
  else busy(false);
});
$("save-settings").addEventListener("click", async () => {
  try {
    await post("/api/settings", {
      model_id: $("model-id").value,
      model_path: $("model-path").value,
      device: $("device").value,
      precision: $("precision").value,
      context_limit: Number($("context").value),
      idle_unload_seconds: Number($("idle").value),
    });
    notice("模型设置已保存。");
    await refresh();
  } catch (error) {
    notice(error.message, true);
  }
});
$("model-id").addEventListener("change", () => {
  $("model-path").value = "models/" + $("model-id").value.split("/").at(-1);
});
$("browse-model-path").addEventListener("click", async () => {
  const button = $("browse-model-path");
  button.disabled = true;
  try {
    const result = await post("/api/model-directory/pick");
    if (!result.path) return;
    $("model-path").value = result.path;
    const folder = result.path.replace(/[\\/]+$/, "").split(/[\\/]/).at(-1);
    const quantized = /-ConvRot-INT8$/i.test(folder);
    const modelId = "IndexTeam/" + folder.replace(/-ConvRot-INT8$/i, '');
    if ([...$("model-id").options].some((option) => option.value === modelId && !option.disabled)) {
      $("model-id").value = modelId;
    }
    if (quantized) $("precision").value = 'convrot-int8';
    notice("已选择模型目录；点击“保存设置”后生效。");
  } catch (error) {
    notice(error.message, true);
  } finally {
    button.disabled = false;
  }
});
function speechStatus(value) {
  $("speech-model-status").textContent = value.active ? "正在识别视频声音；停止后可修改路径。"
    : value.available ? "语音模型已就绪；启动视频翻译时直接读取此目录。"
    : "语音模型未就绪，请选择已有 R2T2 模型目录。";
}
$("browse-speech-path").addEventListener("click", async () => {
  const button = $("browse-speech-path");
  button.disabled = true;
  try {
    const result = await post("/api/speech/model-directory/pick");
    if (result.path) {
      $("speech-model-path").value = result.path;
      notice("已选择语音模型目录；点击“保存语音模型路径”后生效。");
    }
  } catch (error) { notice(error.message, true); }
  finally { button.disabled = false; }
});
$("save-speech").addEventListener("click", async () => {
  const button = $("save-speech");
  button.disabled = true;
  try {
    const result = await post("/api/speech/settings", {model_path: $("speech-model-path").value});
    $("speech-model-path").value = result.model_path;
    speechStatus(result);
    notice("语音模型路径已保存，将直接复用文件，不复制模型。");
  } catch (error) { notice(error.message, true); }
  finally { button.disabled = false; }
});
$("verify-speech").addEventListener("click", async () => {
  const button = $("verify-speech");
  button.disabled = true;
  notice("正在校验两个语音模型的 SHA256…");
  try {
    await post("/api/speech/verify", {model_path: $("speech-model-path").value});
    notice("两个官方 Q8 语音模型校验通过。");
  } catch (error) { notice(error.message, true); }
  finally { button.disabled = false; }
});
$("verify").addEventListener("click", async () => {
  const button = $("verify");
  button.disabled = true;
  notice("正在逐文件校验 SHA256…");
  try {
    const result = await post("/api/verify");
    notice(
      result.complete ? "模型文件完整，校验通过。" : result.problems.join("；"),
      !result.complete,
    );
  } catch (error) {
    notice(error.message, true);
  } finally {
    button.disabled = false;
  }
});
$("unload").addEventListener("click", async () => {
  try {
    await post("/api/unload");
    notice("模型已释放。");
    await refresh();
  } catch (error) {
    notice(error.message, true);
  }
});
$("download-start").addEventListener("click", async () => {
  try {
    await post("/api/download", {
      model_id: $("download-model").value,
      source: $("download-source").value,
    });
    notice("已开始下载，取消后可以继续。");
    await refresh();
  } catch (error) {
    notice(error.message, true);
  }
});
$("download-cancel").addEventListener("click", async () => {
  try {
    await api("/api/download", { method: "DELETE" });
    notice("已请求取消下载。");
  } catch (error) {
    notice(error.message, true);
  }
});
$("pair").addEventListener("click", async () => {
  try {
    const result = await post("/api/pair-code");
    $("pair-code").textContent = result.code;
    $("pair-expiry").textContent = "5 分钟内有效，配对成功后自动作废。";
  } catch (error) {
    notice(error.message, true);
  }
});
$("revoke").addEventListener("click", async () => {
  try {
    await api("/api/clients", { method: "DELETE" });
    notice("所有 Chrome 扩展凭据已撤销。");
  } catch (error) {
    notice(error.message, true);
  }
});
$("refresh-hardware").addEventListener("click", async () => {
  try {
    $("hardware").textContent = "正在读取…";
    const info = await api("/api/hardware");
    const gib = (x) => (x / 2 ** 30).toFixed(2) + " GiB";
    $("hardware").textContent = [
      `可用内存：${gib(info.ram_available_bytes)} / ${gib(info.ram_total_bytes)}`,
      `PyTorch：${info.torch}`,
      `CUDA：${info.cuda_available ? "可用" : "不可用"}`,
      ...info.gpus.map(
        (g) =>
          `${g.name}\n  可用显存：${gib(g.free_bytes)} / ${gib(g.total_bytes)}\n  Compute capability：${g.capability.join(".")}`,
      ),
    ].join("\n");
  } catch (error) {
    $("hardware").textContent = error.message;
  }
});
async function refresh() {
  try {
    const refreshed = await api("/api/status");
    if (pageIdentity && refreshed.settings.identity !== pageIdentity) {
      status = null;
      warmupJob = null;
      saveWarmup();
      warmupError = "本地服务已更换，请刷新页面后再加载与预热。";
      renderWarmup();
      $("service").textContent = "服务已更换";
      notice("本地服务已更换，请重新加载页面后再操作。", true);
      return;
    }
    pageIdentity ??= refreshed.settings.identity;
    status = refreshed;
    renderWarmup();
    $("service").textContent = status.busy ? "正在翻译" : "本地服务就绪";
    $("model-name").textContent =
      status.settings.model_id.split("/").at(-1) +
      (status.model_ready ? " · 已加载" : " · 待加载");
    $("footer-model").textContent = status.settings.device.toUpperCase();
    $("raw-status").textContent = JSON.stringify(status, null, 2);
    const d = status.download;
    $("download-progress").max = d.total_bytes || 1;
    $("download-progress").value = d.completed_bytes || 0;
    $("download-status").textContent =
      d.status === "idle"
        ? "暂无下载"
        : `${d.status} · ${((d.completed_bytes || 0) / 1e9).toFixed(2)} / ${((d.total_bytes || 0) / 1e9).toFixed(2)} GB${d.file ? " · " + d.file : ""}${d.error ? " · " + d.error : ""}`;
    if (!initialized) {
      const models = await api("/api/models");
      for (const model of models) {
        option(
          $("model-id"),
          model.id,
          model.id.split("/").at(-1),
          !model.available,
        );
        if (model.available)
          option($("download-model"), model.id, model.id.split("/").at(-1));
      }
      const s = status.settings;
      $("model-id").value = s.model_id;
      $("model-path").value = s.model_path;
      $("device").value = s.device;
      $("precision").value = s.precision;
      $("context").value = s.context_limit;
      $("idle").value = s.idle_unload_seconds;
      const speech = await api("/api/speech/settings");
      $("speech-model-path").value = speech.model_path;
      speechStatus(speech);
      initialized = true;
    }
  } catch (error) {
    status = null;
    renderWarmup();
    $("service").textContent = "服务未连接";
    notice(error.message, true);
  }
}
async function start() {
  await refresh();
  try {
    warmupJob = JSON.parse(sessionStorage.getItem("index-warmup") || "null");
    if (warmupJob && (!warmupJob.id || warmupJob.identity !== pageIdentity)) {
      warmupJob = null;
      sessionStorage.removeItem("index-warmup");
    }
  } catch { sessionStorage.removeItem("index-warmup"); }
  renderWarmup();
  if (warmupJob) watchWarmup();
  const id = sessionStorage.getItem("index-job");
  try {
    const saved = sessionStorage.getItem("index-request");
    running = saved ? JSON.parse(saved) : id ? { id } : null;
  } catch {
    sessionStorage.removeItem("index-request");
    sessionStorage.removeItem("index-job");
  }
  if (running) {
    busy(true);
    resumeRequest(running, ++epoch);
  }
  setInterval(() => {
    refresh();
    if (warmupJob) watchWarmup();
    if (running) resumeRequest(running, epoch);
  }, 5000);
}
function renderWarmup() {
  const button = $("warmup"), panel = $("warmup-state"), label = $("warmup-status");
  const pending = warmupSubmitting || !!warmupJob;
  button.disabled = pending || !status;
  button.setAttribute("aria-busy", String(pending));
  $("warmup-cancel").hidden = !warmupJob;
  $("warmup-cancel").disabled = !!warmupJob?.cancelling;
  if (pending) {
    panel.dataset.state = "working";
    const seconds = warmupJob ? Math.max(0, Math.floor((Date.now() - warmupJob.started) / 1000)) : 0;
    const phase = warmupSubmitting ? "正在提交预热请求" : warmupJob.cancelling ? "正在取消预热"
      : warmupConnectionError ? "连接中断，正在恢复预热状态"
      : {queued:"预热排队中",loading:"正在加载模型并预热",running:"正在验证预热效果"}[warmupJob.phase] || "正在加载模型并预热";
    button.textContent = phase + "…";
    label.textContent = `${phase} · 已等待 ${seconds} 秒。${warmupConnectionError || (seconds >= 120 ? "任务仍在处理中；可等待完成或取消预热。" : "首次编译可能需要一到两分钟，完成后会明确显示。")}`;
  } else if (warmupError) {
    panel.dataset.state = "error";
    button.textContent = "重试加载与预热";
    label.textContent = warmupError;
  } else if (!status) {
    panel.dataset.state = "connecting";
    button.textContent = "等待服务连接";
    label.textContent = "服务尚未连接，正在重试；当前不能确认模型是否加载。";
  } else if (status.model_ready) {
    panel.dataset.state = "ready";
    button.textContent = "重新预热";
    const seconds = Number(status.acceleration?.warmup_seconds);
    label.textContent = status.acceleration?.compiled
      ? `预热已完成，可以开始翻译 · 编译加速已启用${Number.isFinite(seconds) ? ` · 首次预热 ${seconds.toFixed(1)} 秒` : ""}`
      : "模型已加载，可以开始翻译 · 当前使用常规推理。";
  } else {
    panel.dataset.state = "idle";
    button.textContent = "加载并预热";
    label.textContent = "模型尚未加载。点击“加载并预热”，此处会持续显示进度和完成状态。";
  }
}
function saveWarmup() {
  if (warmupJob) sessionStorage.setItem("index-warmup", JSON.stringify(warmupJob));
  else sessionStorage.removeItem("index-warmup");
}
async function watchWarmup() {
  if (!warmupJob || warmupWatching) return;
  const info = warmupJob;
  warmupWatching = true;
  try {
    while (warmupJob === info) {
      if (status && status.settings.identity !== info.identity) throw new Error("本地服务已更换，请刷新页面后重新预热。");
      const result = await api("/api/jobs/" + encodeURIComponent(info.id) + "?wait_ms=10000", {signal:AbortSignal.timeout(12000)});
      if (warmupJob !== info) return;
      warmupConnectionError = "";
      info.phase = result.status;
      saveWarmup();
      if (terminal.has(result.status)) {
        warmupJob = null;
        saveWarmup();
        if (result.status !== "completed") warmupError = result.status === "cancelled" ? "预热已取消，可以重新加载。" : "预热失败：" + (result.error || "请检查模型目录及设备设置。");
        await refresh();
        renderWarmup();
        return;
      }
      renderWarmup();
      await delay(300);
    }
  } catch (error) {
    if (warmupJob !== info) return;
    if (error.status && [400,403,404,409,422].includes(error.status)) {
      warmupJob = null; saveWarmup();
      warmupError = "无法继续确认预热任务：" + error.message;
    } else warmupConnectionError = "状态确认失败，自动重试：" + error.message;
    renderWarmup();
  } finally { warmupWatching = false; }
}
$("warmup").addEventListener("click", async () => {
  if (warmupJob || warmupSubmitting) return;
  warmupSubmitting = true;
  warmupError = ""; warmupConnectionError = "";
  renderWarmup();
  try {
    const job = await post("/api/warmup");
    warmupJob = {id:job.id,identity:pageIdentity,started:Date.now(),phase:job.status};
    saveWarmup();
  } catch (error) { warmupError = "预热请求未确认：" + error.message; }
  finally { warmupSubmitting = false; renderWarmup(); }
  if (warmupJob) watchWarmup();
});
$("warmup-cancel").addEventListener("click", async () => {
  const info = warmupJob;
  if (!info) return;
  info.cancelling = true; renderWarmup();
  try { await api("/api/jobs/" + encodeURIComponent(info.id), {method:"DELETE"}); }
  catch (error) { info.cancelling = false; warmupConnectionError = "取消未确认：" + error.message; renderWarmup(); }
});
setInterval(renderWarmup, 1000);
start();
