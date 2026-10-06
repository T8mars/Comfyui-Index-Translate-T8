"use strict";
(() => {
  const storageKey = "index-srt-request";
  let active = null, generation = 0, watching = false, importedName = "subtitles.srt";
  for (const [id, label] of Object.entries(languages)) {
    option($("srt-source"), id, label);
    if (id !== "auto") option($("srt-target"), id, label);
  }
  $("srt-source").value = "zh";
  $("srt-target").value = "en";
  function remember() {
    if (active) sessionStorage.setItem(storageKey, JSON.stringify(active));
    else sessionStorage.removeItem(storageKey);
  }
  function controls(busy, downloadable = false) {
    for (const id of ["srt-submit", "srt-input", "srt-file", "srt-source", "srt-target", "srt-budget", "srt-bilingual"])
      $(id).disabled = busy;
    $("srt-cancel").disabled = !busy;
    $("srt-download").disabled = !downloadable;
  }
  function decode(bytes) {
    const data = new Uint8Array(bytes);
    if (data[0] === 255 && data[1] === 254) return new TextDecoder("utf-16le", {fatal: true}).decode(data);
    if (data[0] === 254 && data[1] === 255) return new TextDecoder("utf-16be", {fatal: true}).decode(data);
    try { return new TextDecoder("utf-8", {fatal: true}).decode(data); }
    catch { return new TextDecoder("gb18030", {fatal: true}).decode(data); }
  }
  $("srt-file").addEventListener("change", async () => {
    const file = $("srt-file").files[0];
    if (!file) return;
    const token = ++generation;
    try {
      if (file.size > 1024 * 1024) throw new Error("SRT 文件超过 1 MiB，请分段处理。");
      const text = decode(await file.arrayBuffer());
      if (token !== generation) return;
      $("srt-input").value = text;
      active = null; remember();
      importedName = file.name;
      $("srt-file-name").textContent = `已导入 ${file.name} · ${file.size} 字节`;
      $("srt-download").disabled = true;
    } catch (error) {
      if (token === generation) notice(error.message, true);
    }
  });
  $("srt-input").addEventListener("input", () => {
    if (active?.completed) { active = null; remember(); }
    $("srt-download").disabled = true;
  });
  async function poll(token) {
    if (watching || !active) return;
    watching = true;
    try {
      while (active && token === generation) {
        const service = await api("/api/status");
        if (service.settings.identity !== active.identity) throw new Error("本地服务已更换，请重新提交字幕。");
        if (active.cancel_pending) {
          await api(`/api/requests/${encodeURIComponent(active.request_id)}`, {method: "DELETE"});
          active = null; remember(); controls(false);
          $("srt-progress").textContent = "已请求取消";
          return;
        }
        const job = await api(active.id ? `/api/jobs/${active.id}` : `/api/requests/${encodeURIComponent(active.request_id)}`);
        if (token !== generation || !active) return;
        active.id = job.id;
        active.subtitle = job.subtitle;
        if (job.subtitle) $("srt-file-name").textContent = `任务文件：${job.subtitle.filename} · ${languages[job.subtitle.source] || job.subtitle.source} → ${languages[job.subtitle.target] || job.subtitle.target}`;
        remember();
        $("srt-progress").textContent = `${{queued:"排队中", loading:"正在加载模型", running:"正在翻译", completed:"全部完成", cancelled:"已取消", failed:"失败"}[job.status]} · ${job.done}/${job.total} 条`;
        if (terminal.has(job.status)) {
          controls(false, job.status === "completed");
          if (job.error) notice(job.error, job.status === "failed");
          active.completed = job.status === "completed";
          if (!active.completed) active = null;
          remember();
          return;
        }
        controls(true);
        await delay(1000);
      }
    } catch (error) {
      if (token !== generation) return;
      if (error.status === 404 || error.status === 409 || error.message.includes("服务已更换")) {
        active = null; remember(); controls(false);
      } else controls(!active?.completed, Boolean(active?.completed));
      notice(error.message + (active ? "；连接恢复后会继续追踪，仍可取消。" : ""), true);
    } finally { watching = false; }
  }
  $("srt-submit").addEventListener("click", async () => {
    if (!pageIdentity) return notice("服务尚未就绪，请稍后重试。", true);
    const text = $("srt-input").value;
    if (!text.trim()) return notice("请导入或粘贴中文 SRT。", true);
    if (new TextEncoder().encode(text).length > 1024 * 1024) return notice("字幕超过 1 MiB，请分段处理。", true);
    const token = ++generation;
    let created = false;
    controls(true); notice("");
    $("srt-progress").textContent = "正在校验和提交字幕…";
    try {
      const body = {request_id:crypto.randomUUID(), expected_identity:pageIdentity, text,
        filename:importedName, source:$("srt-source").value, target:$("srt-target").value,
        output_budget:Number($("srt-budget").value), bilingual:$("srt-bilingual").checked,
        glossary:JSON.parse($("glossary").value || "{}")};
      // Keep only identity/request metadata in storage, never the large file.
      active = {request_id:body.request_id, identity:pageIdentity, id:null}; remember();
      created = true;
      const job = await post("/api/subtitles", body);
      if (token !== generation) return;
      active.id = job.id; remember();
      await poll(token);
    } catch (error) {
      if (token !== generation) return;
      if (!created) {
        controls(false, Boolean(active?.completed));
        $("srt-progress").textContent = "字幕设置无效，请修正后重试";
        notice(error.message, true);
        return;
      }
      if (error.status >= 400 && error.status < 500 && error.status !== 401) active = null;
      if (!active || !error.status) {
        // A lost POST response may already be accepted. Keep the request id
        // for GET recovery; malformed JSON is rejected before it is created.
        if (!active) controls(false);
      }
      remember();
      $("srt-progress").textContent = active ? "提交结果待确认，正在恢复任务" : "字幕校验或提交失败";
      notice(error.message, true);
    }
  });
  $("srt-cancel").addEventListener("click", async () => {
    if (!active) return;
    active.cancel_pending = true; remember();
    $("srt-progress").textContent = "正在取消…";
    if (!watching) await poll(generation);
  });
  $("srt-download").addEventListener("click", async () => {
    if (!active?.completed) return;
    const job = active;
    try {
      const service = await api("/api/status");
      if (service.settings.identity !== job.identity) throw new Error("本地服务已更换，请重新翻译字幕。");
      const response = await fetch(`/api/subtitles/jobs/${job.id}/download`, {signal:AbortSignal.timeout(30000),
        headers:{"X-Expected-Identity":job.identity}});
      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || "下载失败，请稍后重试。");
      }
      const blob = await response.blob();
      const filename = response.headers.get("Content-Disposition")?.match(/filename\*=UTF-8''([^;]+)/)?.[1];
      const url = URL.createObjectURL(blob), link = document.createElement("a");
      link.href = url; link.download = filename ? decodeURIComponent(filename) : "subtitles.en.srt";
      document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      notice("SRT 已导出，原序号和时间轴保留。");
    } catch (error) { notice(error.message, true); }
  });
  try { active = JSON.parse(sessionStorage.getItem(storageKey) || "null"); }
  catch { sessionStorage.removeItem(storageKey); }
  if (active) { controls(!active.completed, active.completed); $("srt-progress").textContent = "正在恢复字幕任务…"; }
  setInterval(() => { if (pageIdentity && active && !active.completed && !watching) poll(generation); }, 2500);
  if (active?.completed) setTimeout(() => poll(generation), 1000);
})();
