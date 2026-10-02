(() => {
  "use strict";
  if (globalThis.__indexTranslateLoaded) return;
  globalThis.__indexTranslateLoaded = true;
  const BLOCK =
    "p,h1,h2,h3,h4,h5,h6,li,td,th,blockquote,figcaption,dt,dd,summary,legend,caption,div,section,article,main,body";
  const SKIP =
    'script,style,noscript,pre,code,textarea,input,select,button,nav,footer,canvas,video,audio,iframe,rt,rp,[contenteditable]:not([contenteditable="false"]),[translate="no"],[aria-hidden="true"],[data-index-owned],.notranslate';
  const PROTECT = "a,code,kbd,samp,math,.katex,.MathJax,svg,img,meter,progress";
  const uuid = () =>
    [...crypto.getRandomValues(new Uint32Array(4))]
      .map((x) => x.toString(16).padStart(8, "0"))
      .join("");
  let pageEpoch = uuid(),
    url = location.href,
    enabled = false,
    paused = false,
    cfg,
    scanning = false,
    scanAgain = false,
    scanTimer,
    pumping = false,
    active = 0,
    lastError = "",
    toolbar;
  let lifecycle = 0,
    resumeOnPageShow = false,
    manuallyPaused = false;
  let records = new Map(),
    nodeKeys = new WeakMap(),
    nextNode = 0,
    io;
  let observedParents = new Set();
  function message(value) {
    return chrome.runtime.sendMessage(value).then((response) => {
      if (!response?.ok) throw new Error(response?.error || "扩展后台未响应");
      return response.value;
    });
  }
  const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  function nodeKey(node) {
    if (!nodeKeys.has(node)) nodeKeys.set(node, ++nextNode);
    return nodeKeys.get(node);
  }
  function simpleHash(text) {
    let n = 2166136261;
    for (let i = 0; i < text.length; i++) {
      n ^= text.charCodeAt(i);
      n = Math.imul(n, 16777619);
    }
    return (n >>> 0).toString(36) + "_" + text.length;
  }
  function visible(element, protectedSelf = false) {
    const skipped = protectedSelf
      ? element.parentElement?.closest(SKIP)
      : element.closest(SKIP);
    if (skipped || element.parentElement?.closest(PROTECT))
      return false;
    const style = getComputedStyle(element);
    if (style.display === "none") return false;
    const visibilityOptions = { checkOpacity: true, checkVisibilityCSS: true };
    if (style.display === "contents") {
      let ancestor = element.parentElement;
      while (ancestor && getComputedStyle(ancestor).display === "contents")
        ancestor = ancestor.parentElement;
      if (ancestor?.checkVisibility?.(visibilityOptions) === false) return false;
    } else if (element.checkVisibility?.(visibilityOptions) === false) return false;
    const rendered =
      style.display === "contents"
        ? (() => {
            const range = document.createRange();
            range.selectNodeContents(element);
            return range.getClientRects().length > 0;
          })()
        : element.getClientRects().length > 0;
    return (
      style.visibility !== "hidden" &&
      style.visibility !== "collapse" &&
      rendered
    );
  }
  function visibleOverride(element) {
    if (getComputedStyle(element).visibility !== "hidden") return false;
    return [...element.querySelectorAll("*")].some((child) => visible(child));
  }
  function hiddenObjectFallback(element) {
    if (element.tagName !== "OBJECT") return false;
    const range = document.createRange();
    range.selectNodeContents(element);
    return range.getClientRects().length === 0;
  }
  function isBoundary(node) {
    if (node.nodeType !== 1 || node.matches(PROTECT)) return false;
    if (node.matches(BLOCK)) return true;
    return [...node.querySelectorAll(BLOCK)].some(
      (block) => !block.parentElement?.closest(PROTECT),
    );
  }
  function eligible(node) {
    if (node.nodeType === 3)
      return !!node.textContent.trim() &&
        !(node.parentElement?.tagName === "DETAILS" && !node.parentElement.open);
    if (node.nodeType !== 1) return false;
    if (node.tagName === "WBR") return true;
    if (node.matches(PROTECT)) return visible(node, true);
    return !node.matches(SKIP) && !hiddenObjectFallback(node) &&
      (visible(node) || visibleOverride(node));
  }
  function extract(nodes) {
    const protectedNodes = [];
    const parts = [];
    function walk(node) {
      if (node.nodeType === 3) {
        if (getComputedStyle(node.parentElement).visibility === "visible")
          parts.push(node.textContent);
        return;
      }
      if (node.nodeType !== 1 || node.matches("[data-index-owned]")) return;
      if (node.tagName === "WBR") return;
      if (node.matches(PROTECT)) {
        const i = protectedNodes.length;
        protectedNodes.push(node);
        parts.push({ slot: i });
        return;
      }
      if (node.matches(SKIP) || hiddenObjectFallback(node) ||
        (!visible(node) && !visibleOverride(node))) return;
      if (node.tagName === "BR") {
        parts.push("\n");
        return;
      }
      for (const child of node.childNodes) walk(child);
    }
    for (const node of nodes) walk(node);
    const signature = JSON.stringify([
      parts,
      protectedNodes.map((n) => n.outerHTML),
    ]);
    const nonce = simpleHash(signature);
    const protectedTokens = protectedNodes.map(
      (_, i) => `%%IT_${nonce}_${i}%%`,
    );
    const text = parts
      .map((part) =>
        typeof part === "string" ? part : protectedTokens[part.slot],
      )
      .join("")
      .trim();
    return { text, signature, protectedNodes, protectedTokens, nonce };
  }
  function chunks(text) {
    const tokens = text.match(
      /%%IT_[A-Za-z0-9_]+%%|[^\s。！？.!?]+|\s+|[。！？.!?]+/g,
    ) || [text];
    const result = [];
    let current = "";
    for (const token of tokens) {
      if (current.length + token.length > 700 && current.trim()) {
        result.push(current.trim());
        current = "";
      }
      if (token.length > 700 && !token.startsWith("%%IT_")) {
        for (let i = 0; i < token.length;) {
          if (current.trim()) {
            result.push(current.trim());
            current = "";
          }
          let end = Math.min(i + 700, token.length);
          if (end < token.length && /[\uD800-\uDBFF]/.test(token[end - 1]))
            end++;
          result.push(token.slice(i, end));
          i = end;
        }
      } else current += token;
    }
    if (current.trim()) result.push(current.trim());
    return result;
  }
  function sameLanguage(text) {
    const plain = text.replace(/%%IT_[A-Za-z0-9_]+%%/g, "");
    const letters = plain.match(/\p{L}/gu) || [];
    if (!letters.length) return true;
    if (cfg?.target === "zh")
      return (
        (plain.match(/\p{Script=Han}/gu) || []).length / letters.length > 0.85
      );
    if (cfg?.target === "ja")
      return /[ぁ-んァ-ン]/.test(plain) && !/\b[A-Za-z]{3,}\b/.test(plain);
    if (cfg?.target === "ko")
      return (plain.match(/[가-힣]/g) || []).length / letters.length > 0.85;
    return false;
  }
  function stats() {
    const counts = {
      waiting: 0,
      running: 0,
      completed: 0,
      failed: 0,
      skipped: 0,
    };
    for (const record of records.values())
      if (record.state in counts) counts[record.state]++;
    return counts;
  }
  function update() {
    if (!toolbar) return;
    const count = stats();
    toolbar.root.getElementById("status").textContent = paused
      ? "已暂停"
      : `${enabled ? "翻译中" : "未启动"} · 完成 ${count.completed} / ${records.size} · 等待 ${count.waiting} · 失败 ${count.failed}`;
    toolbar.root.getElementById("error").textContent = lastError;
  }
  function createToolbar() {
    if (toolbar) return;
    const host = document.createElement("div");
    host.dataset.indexOwned = "toolbar";
    host.style.cssText =
      "position:fixed;bottom:20px;right:20px;z-index:2147483647;max-width:380px";
    const root = host.attachShadow({ mode: "closed" });
    const style = document.createElement("style");
    style.textContent =
      ":host{font:13px Segoe UI,Microsoft YaHei,sans-serif;color:#203343}section{padding:13px 16px;background:white;border:1px solid #ccdadd;border-radius:12px;box-shadow:0 5px 24px #172b3d22}strong{font-size:13px}p{margin:8px 0;line-height:1.5}#error{color:#af5b37;max-width:320px;font-size:12px}button{font:inherit;border:0;padding:7px 11px;margin-right:5px;border-radius:6px;background:#eaf1f2;color:#176e74;cursor:pointer}";
    root.append(style);
    const section = document.createElement("section");
    const title = document.createElement("strong");
    title.textContent = "Index Translate";
    section.append(title);
    for (const id of ["status", "error"]) {
      const p = document.createElement("p");
      p.id = id;
      section.append(p);
    }
    for (const [name, action] of [
      ["继续", () => start(true)],
      ["停止", () => stop(false)],
      ["还原", () => stop(true)],
      ["重试", () => retry()],
    ]) {
      const b = document.createElement("button");
      b.textContent = name;
      b.addEventListener("click", action);
      section.append(b);
    }
    root.append(section);
    document.documentElement.append(host);
    toolbar = { host, root };
    update();
  }
  function removeOutput(record) {
    record.output?.remove();
    record.error?.remove();
    record.output = null;
    record.error = null;
  }
  function valid(record) {
    return (
      enabled &&
      !paused &&
      record.epoch === pageEpoch &&
      records.get(record.key) === record &&
      visible(record.parent) &&
      record.nodes.every(
        (n) => n.isConnected && n.parentNode === record.parent,
      ) &&
      extract(record.nodes).signature === record.signature
    );
  }
  function cloneProtected(node, recordId, slot) {
    const copy = node.cloneNode(true);
    const elements = [copy, ...copy.querySelectorAll("*")];
    const ids = new Map();
    for (const element of elements)
      if (element.id) {
        const value = `index-copy-${recordId}-${slot}-${ids.size}`;
        ids.set(element.id, value);
        element.id = value;
      }
    for (const element of elements)
      for (const attribute of [...element.attributes]) {
        if (attribute.name.startsWith("on")) {
          element.removeAttribute(attribute.name);
          continue;
        }
        let value = attribute.value;
        if (
          ["href", "xlink:href"].includes(attribute.name) &&
          value.startsWith("#") &&
          ids.has(value.slice(1))
        )
          value = "#" + ids.get(value.slice(1));
        if (
          [
            "for",
            "aria-labelledby",
            "aria-describedby",
            "aria-controls",
          ].includes(attribute.name)
        )
          value = value
            .split(" ")
            .map((id) => ids.get(id) || id)
            .join(" ");
        value = value.replace(/url\(#([^)]*)\)/g, (match, id) =>
          ids.has(id) ? `url(#${ids.get(id)})` : match,
        );
        if (value !== attribute.value)
          element.setAttribute(attribute.name, value);
      }
    return copy;
  }
  function insert(record, text) {
    if (!valid(record)) return false;
    const tokens = text.match(/%%IT_[A-Za-z0-9_]+%%/g) || [];
    const expected = record.text.match(/%%IT_[A-Za-z0-9_]+%%/g) || [];
    if (
      JSON.stringify(tokens.slice().sort()) !==
      JSON.stringify(expected.slice().sort())
    )
      throw new Error("占位符不完整，已保留原文");
    removeOutput(record);
    const span = document.createElement("span");
    span.dataset.indexOwned = "translation";
    span.dataset.indexTranslation = record.id;
    span.lang = record.preferences.target;
    span.dir = "auto";
    let offset = 0;
    const pattern = /%%IT_[A-Za-z0-9_]+%%/g;
    let match;
    while ((match = pattern.exec(text))) {
      span.append(document.createTextNode(text.slice(offset, match.index)));
      const protectedIndex = record.protectedTokens.indexOf(match[0]);
      if (protectedIndex < 0) span.append(document.createTextNode(match[0]));
      else
        span.append(
          cloneProtected(
            record.protectedNodes[protectedIndex],
            record.id,
            protectedIndex,
          ),
        );
      offset = match.index + match[0].length;
    }
    span.append(document.createTextNode(text.slice(offset)));
    record.nodes.at(-1).after(span);
    record.output = span;
    return true;
  }
  function fail(record, error) {
    if (!valid(record)) return;
    removeOutput(record);
    record.state = "failed";
    const mark = document.createElement("span");
    mark.dataset.indexOwned = "error";
    mark.dataset.indexError = "";
    mark.textContent = "翻译未完成 · " + error;
    record.nodes.at(-1).after(mark);
    record.error = mark;
    lastError = error;
  }
  async function scan() {
    if (!enabled || paused) return;
    if (scanning) {
      scanAgain = true;
      return;
    }
    scanning = true;
    try {
      for (const [key, record] of records)
        if (
          !visible(record.parent) ||
          !record.nodes.every((n) =>
            n.isConnected && n.parentNode === record.parent && eligible(n)
          )
        ) {
          removeOutput(record);
          records.delete(key);
        }
      const seen = new Set();
      let budget = 0;
      const blocks = [...document.querySelectorAll(BLOCK)];
      const parents = new Set(blocks);
      for (const block of blocks) {
        if (block.parentElement?.closest(PROTECT)) continue;
        for (
          let parent = block.parentElement;
          parent && !parent.matches(BLOCK);
          parent = parent.parentElement
        ) {
          if (parent.matches(PROTECT) || parent.closest(SKIP)) break;
          parents.add(parent);
        }
      }
      for (const parent of parents) {
        if (!visible(parent)) {
          if (getComputedStyle(parent).visibility === "hidden")
            for (const child of parent.querySelectorAll("*")) {
              if (!visible(child)) continue;
              let ancestor = child.parentElement;
              while (ancestor && ancestor !== parent && !visible(ancestor))
                ancestor = ancestor.parentElement;
              if (ancestor === parent) parents.add(child);
            }
          continue;
        }
        let run = [];
        function finish() {
          if (!run.length) return;
          const nodes = run;
          run = [];
          const key = nodes.map(nodeKey).join("-");
          seen.add(key);
          const value = extract(nodes);
          if (!value.text) return;
          budget += value.text.length;
          if (budget > 250000 || (records.size >= 1000 && !records.has(key))) {
            lastError = "当前页面达到翻译预算，请按需分段翻译。";
            return;
          }
          const old = records.get(key);
          if (
            old &&
            old.parent === parent &&
            old.signature === value.signature &&
            old.epoch === pageEpoch &&
            (old.state !== "completed" || old.output?.isConnected) &&
            (old.state !== "failed" || old.error?.isConnected)
          )
            return;
          if (old) {
            removeOutput(old);
          }
          const record = {
            key,
            id: uuid(),
            epoch: pageEpoch,
            nodes,
            parent,
            preferences: {
              source: cfg.source,
              target: cfg.target,
              glossary: structuredClone(cfg.glossary),
            },
            ...value,
            state: sameLanguage(value.text) ? "skipped" : "waiting",
            near: !cfg.viewport,
          };
          records.set(key, record);
          const rect = parent.getBoundingClientRect();
          record.near ||= rect.bottom > -600 && rect.top < innerHeight + 600;
        }
        for (const node of parent.childNodes) {
          if (node.nodeType === 1 && node.matches("[data-index-owned]"))
            continue;
          if (isBoundary(node) || !eligible(node)) {
            finish();
            continue;
          }
          run.push(node);
        }
        finish();
      }
      for (const [key, record] of records)
        if (!seen.has(key) || !record.nodes.every((n) => n.isConnected)) {
          removeOutput(record);
          records.delete(key);
        }
      const currentParents = new Set(
        [...records.values()].map((record) => record.parent),
      );
      for (const parent of observedParents)
        if (!currentParents.has(parent)) io?.unobserve(parent);
      for (const parent of currentParents)
        if (!observedParents.has(parent)) io?.observe(parent);
      observedParents = currentParents;
      update();
      pump();
    } finally {
      scanning = false;
      if (scanAgain) {
        scanAgain = false;
        schedule();
      }
    }
  }
  function schedule() {
    clearTimeout(scanTimer);
    scanTimer = setTimeout(scan, 200);
  }
  async function translateRecord(record) {
    const myEpoch = pageEpoch;
    record.state = "running";
    active++;
    update();
    const requestId = uuid();
    const pieces = chunks(record.text);
    try {
      if (pieces.length > 16) throw new Error("段落过长，请分段选择文本");
      const paragraphs = pieces.map((text, i) => ({
        id: record.id + ":" + i,
        text,
      }));
      const submit = {
        type: "SUBMIT",
        request_id: requestId,
        page_epoch: myEpoch,
        preferences: record.preferences,
        paragraphs,
      };
      try {
        await message(submit);
      } catch {
        await delay(800);
        if (!valid(record)) return;
        await message(submit);
      }
      let job;
      while (valid(record)) {
        job = await message({
          type: "POLL",
          request_id: requestId,
          page_epoch: myEpoch,
        });
        if (["completed", "failed", "cancelled"].includes(job.status)) break;
        await delay(1200);
      }
      if (!valid(record)) return;
      if (job?.status !== "completed")
        throw new Error(job?.error || "任务已取消");
      const values = new Map(job.results.map((r) => [r.id, r.text]));
      const result = paragraphs
        .map((p) => {
          if (!values.has(p.id)) throw new Error("缺少译文");
          return values.get(p.id);
        })
        .join("\n");
      if (insert(record, result)) record.state = "completed";
    } catch (error) {
      if (myEpoch === pageEpoch) fail(record, error.message);
    } finally {
      if (myEpoch === pageEpoch) {
        active--;
        update();
        pump();
      }
      try {
        await message({
          type: "FORGET",
          request_id: requestId,
          page_epoch: myEpoch,
        });
      } catch {}
    }
  }
  function pump() {
    if (pumping || !enabled || paused) return;
    pumping = true;
    try {
      for (const record of records.values()) {
        if (active >= 2) break;
        if (record.state === "waiting" && record.near) translateRecord(record);
      }
    } finally {
      pumping = false;
    }
  }
  async function start(manual = false) {
    const invocation = ++lifecycle;
    try {
      const next = await message({ type: "PUBLIC" });
      if (invocation !== lifecycle) return;
      if (!manual && !next.auto) return;
      if (
        enabled &&
        !paused &&
        JSON.stringify([cfg.source, cfg.target, cfg.glossary, cfg.viewport]) !==
          JSON.stringify([
            next.source,
            next.target,
            next.glossary,
            next.viewport,
          ])
      ) {
        const stopping = stop(true, false);
        const reset = lifecycle;
        await stopping;
        if (reset !== lifecycle) return;
      }
      cfg = next;
      if (manual) manuallyPaused = false;
      if (enabled && !paused) {
        schedule();
        return;
      }
      enabled = true;
      paused = false;
      lastError = "";
      createToolbar();
      io?.disconnect();
      observedParents.clear();
      io = new IntersectionObserver(
        (entries) => {
          for (const entry of entries)
            if (entry.isIntersecting) {
              for (const record of records.values())
                if (record.parent === entry.target) {
                  record.near = true;
                }
              pump();
            }
        },
        { rootMargin: "600px" },
      );
      for (const record of records.values())
        if (record.state === "running") record.state = "waiting";
      await scan();
    } catch (error) {
      lastError = error.message;
      createToolbar();
      update();
    }
  }
  async function stop(restore, userInitiated = true) {
    ++lifecycle;
    if (userInitiated) manuallyPaused = true;
    resumeOnPageShow = false;
    const old = pageEpoch;
    pageEpoch = uuid();
    enabled = false;
    paused = true;
    active = 0;
    io?.disconnect();
    observedParents.clear();
    clearTimeout(scanTimer);
    if (restore) {
      for (const record of records.values()) removeOutput(record);
      records.clear();
    } else
      for (const record of records.values())
        if (record.state === "running") record.state = "waiting";
    update();
    try {
      await message({ type: "CANCEL", page_epoch: old });
    } catch {}
  }
  async function retry() {
    for (const record of records.values())
      if (record.state === "failed") {
        removeOutput(record);
        record.state = "waiting";
      }
    lastError = "";
    await start(true);
  }
  new MutationObserver((changes) => {
    if (!enabled || paused) return;
    const relevant = changes.some((change) => {
      const el =
        change.target.nodeType === 1
          ? change.target
          : change.target.parentElement;
      if (el?.closest("[data-index-owned]")) return false;
      if (
        change.type === "childList" &&
        [...change.addedNodes, ...change.removedNodes].every(
          (n) => n.nodeType === 1 && n.matches("[data-index-owned]"),
        )
      )
        return [...change.removedNodes].some((n) =>
          [...records.values()].some(
            (record) => record.output === n || record.error === n,
          ),
        );
      return true;
    });
    if (relevant) schedule();
  }).observe(document.documentElement, {
    subtree: true,
    childList: true,
    characterData: true,
    attributes: true,
    attributeFilter: [
      "class", "style", "hidden", "open", "aria-hidden", "href", "translate", "contenteditable",
    ],
  });
  addEventListener("animationend", schedule, true);
  addEventListener("transitionend", schedule, true);
  addEventListener("load", (event) => {
    if (event.target?.tagName === "OBJECT") schedule();
  }, true);
  addEventListener("error", (event) => {
    if (event.target?.tagName === "OBJECT") schedule();
  }, true);
  setInterval(async () => {
    if (location.href !== url) {
      const route = location.href;
      url = route;
      const wasEnabled = enabled;
      const stopping = stop(true, false);
      const transition = lifecycle;
      try {
        await stopping;
        if (transition !== lifecycle || location.href !== route) return;
        const next = await message({ type: "PUBLIC" });
        if (transition !== lifecycle || location.href !== route) return;
        cfg = next;
        paused = manuallyPaused;
        if (!manuallyPaused && (wasEnabled || cfg.auto)) await start(true);
      } catch (error) {
        if (transition === lifecycle) {
          lastError = error.message;
          update();
        }
      }
    }
  }, 750);
  chrome.runtime.onMessage.addListener((msg, sender, respond) => {
    if (sender.id !== chrome.runtime.id) return;
    if (msg.type === "IT_START") {
      start(true).then(() => respond({ ok: true }));
      return true;
    }
    if (msg.type === "IT_RESTORE") {
      stop(true).then(() => respond({ ok: true }));
      return true;
    }
    if (msg.type === "IT_STOP") {
      stop(false).then(() => respond({ ok: true }));
      return true;
    }
    if (msg.type === "IT_STATUS") {
      respond({ enabled, paused, ...stats(), error: lastError });
    }
  });
  addEventListener("pagehide", () => {
    const resume = enabled && !paused;
    stop(false, false);
    resumeOnPageShow = resume;
  });
  addEventListener("pageshow", (event) => {
    if (event.persisted && resumeOnPageShow) {
      resumeOnPageShow = false;
      start(true);
    }
  });
  start(false);
})();
