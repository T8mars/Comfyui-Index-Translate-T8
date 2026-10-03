(() => {
  "use strict";
  if (globalThis.__indexVideoLoaded) return;
  globalThis.__indexVideoLoaded = true;

  let active = false;
  let mode = "", video = null, track = null, formerMode = "";
  let host = null, box = null, timer = null, revision = 0, speechFinalId = 0, lastCue = "";
  let cacheScope = "", hasSubtitle = false;
  const cache = new Map();
  const controls = new Map();
  let controlRevision = 0;
  let provider = null, videoLifecycle = 0;

  function vimeoChannel(item) {
    if (item?.tagName !== "IFRAME") return null;
    let origin;
    try { const url = new URL(item.src); if (!/^(player\.)?vimeo\.com$/.test(url.hostname)) return null; origin = url.origin; }
    catch { return null; }
    const pending = new Map();
    const receive = event => {
      if (event.source !== item.contentWindow || event.origin !== origin) return;
      let data = event.data;
      try { if (typeof data === "string") data = JSON.parse(data); } catch { return; }
      if (!data || typeof data !== "object") return;
      if (data.method && pending.has(data.method)) pending.get(data.method).resolve(data.value);
      if (data.event === "error" && pending.has(data.data?.method)) pending.get(data.data.method).reject(new Error("播放器字幕接口不可用"));
      if (data.event === "cuechange" && active && video === item && mode === "captions")
        acceptCue((Array.isArray(data.data?.cues) ? data.data.cues : []).map(cue => String(cue.text || "")).join("\n"));
    };
    addEventListener("message",receive);
    const send = (method,value) => item.contentWindow?.postMessage({method,value},origin);
    const call = (method,value) => new Promise((resolve,reject) => {
      const timeout = setTimeout(() => { pending.delete(method); reject(new Error("播放器字幕接口等待超时")); },2500);
      pending.set(method,{resolve:value => {clearTimeout(timeout);pending.delete(method);resolve(value);},
        reject:error => {clearTimeout(timeout);pending.delete(method);reject(error);}});
      send(method,value);
    });
    return {call,send,former:null,changed:false,close() {
      removeEventListener("message",receive);
      for (const request of [...pending.values()]) request.reject(new Error("字幕读取已停止"));
    }};
  }
  function releaseProvider(channel) {
    channel.send("removeEventListener","cuechange");
    if (channel.changed) {
      if (channel.former) channel.send("enableTextTrack",{language:channel.former.language,kind:channel.former.kind,showing:true});
      else channel.send("disableTextTrack");
    }
    channel.close();
  }

  function candidates() {
    return [...document.querySelectorAll("video,iframe")].filter(item => {
      if (item.tagName === "VIDEO") return true;
      return /video|vimeo|youtube|player|直播/i.test(item.src + " " + item.title) ||
        item.hasAttribute("allowfullscreen") || /fullscreen/.test(item.getAttribute("allow") || "");
    });
  }

  function visible(item) {
    const rect = item.getBoundingClientRect();
    const css = getComputedStyle(item);
    return rect.width >= 120 && rect.height >= 80 && rect.bottom > 0 && rect.top < innerHeight &&
      rect.right > 0 && rect.left < innerWidth && css.visibility !== "hidden" && css.display !== "none";
  }

  function placeControl(item, control) {
    const shown = visible(item);
    control.host.style.display = shown ? "block" : "none";
    if (!shown) return;
    const rect = item.getBoundingClientRect();
    const fullscreen = document.fullscreenElement;
    const parent = fullscreen && fullscreen !== item ? fullscreen : document.documentElement;
    if (control.host.parentNode !== parent) parent.append(control.host);
    if (fullscreen === item && !control.host.matches(":popover-open")) {
      control.host.popover = "manual";
      control.host.showPopover();
    } else if (fullscreen !== item && control.host.hasAttribute("popover")) {
      if (control.host.matches(":popover-open")) control.host.hidePopover();
      control.host.removeAttribute("popover");
    }
    const width = Math.max(100, Math.min(320, rect.width - 16));
    control.host.style.width = width + "px";
    control.host.style.left = Math.max(8, Math.min(innerWidth - width - 8, rect.right - width - 10)) + "px";
    control.host.style.top = Math.max(8, rect.top + 10) + "px";
  }

  function updateControls() {
    for (const [item, control] of controls) {
      if (!item.isConnected) { control.host.remove(); controls.delete(item); continue; }
      const current = active && item === video;
      control.stop.disabled = !current;
      control.caption.setAttribute("aria-pressed", String(current && mode === "captions"));
      control.speech.setAttribute("aria-pressed", String(current && mode === "speech"));
      placeControl(item, control);
    }
  }

  function discoverControls() {
    for (const item of candidates()) {
      if (controls.has(item)) continue;
      const h = document.createElement("div");
      h.dataset.indexOwned = "video-controls";
      h.style.cssText = "position:fixed;z-index:2147483647;display:none;inset:auto;margin:0;border:0;padding:0;background:transparent;pointer-events:none;";
      const shadow = h.attachShadow({mode:"open"});
      const style = document.createElement("style");
      style.textContent = `:host{font:12px Segoe UI,Microsoft YaHei,sans-serif}nav{display:flex;justify-content:flex-end;gap:4px}button{pointer-events:auto;border:1px solid #67d6d0;border-radius:6px;padding:5px 8px;background:#102a30ed;color:#bceeea;font:600 12px/1.3 Segoe UI,Microsoft YaHei,sans-serif;cursor:pointer}button:hover,button[aria-pressed=true]{background:#089b98;color:white}button:disabled{opacity:.5;cursor:default}button:focus-visible{outline:2px solid #0abab5;outline-offset:2px}p{pointer-events:none;margin:5px 0 0 0;padding:5px 8px;border-radius:6px;background:#102a30ed;color:white;font:12px/1.4 Segoe UI,Microsoft YaHei,sans-serif;overflow-wrap:anywhere}p:empty{display:none}`;
      const nav = document.createElement("nav");
      nav.setAttribute("aria-label", "T8 视频翻译");
      const caption = document.createElement("button"), speech = document.createElement("button"), end = document.createElement("button");
      caption.textContent = "字幕译"; caption.title = "翻译此视频字幕；没有可读字幕时识别声音";
      speech.textContent = "语音译"; speech.title = "在本机流式识别并翻译视频声音";
      end.textContent = "停止"; end.disabled = true;
      for (const button of [caption,speech,end]) button.type = "button";
      nav.append(caption,speech,end);
      const status = document.createElement("p");
      status.setAttribute("role","status");
      shadow.append(style,nav,status);
      document.documentElement.append(h);
      const control = {host:h,caption,speech,stop:end,status};
      controls.set(item,control);
      const run = async (action, forceSpeech = false) => {
        const stamp = ++controlRevision;
        status.textContent = action === "stop" ? "正在停止…" : "正在启动翻译…";
        try {
          const reply = await chrome.runtime.sendMessage({type:"VIDEO_CONTROL",action,forceSpeech,
            targetIndex:[...document.querySelectorAll("video,iframe")].indexOf(item)});
          if (stamp !== controlRevision) return;
          if (!reply?.ok) throw new Error(reply?.error || "视频翻译启动失败");
          status.textContent = reply.value.note || (action === "stop" ? "" :
            reply.value.mode === "captions" ? "字幕翻译已开启，播放视频后显示译文" : "正在加载本地语音模型；请播放并取消静音");
        } catch (error) { if (stamp === controlRevision) status.textContent = error.message; }
        updateControls();
      };
      caption.addEventListener("click", () => run("start"));
      speech.addEventListener("click", () => run("start",true));
      end.addEventListener("click", () => run("stop"));
    }
    updateControls();
  }

  function overlay() {
    if (!host) {
      host = document.createElement("div");
      host.dataset.indexOwned = "video-subtitles";
      host.style.cssText = "position:fixed;z-index:2147483646;pointer-events:none;box-sizing:border-box;display:none;inset:auto;margin:0;border:0;padding:0;background:transparent;";
      const shadow = host.attachShadow({mode: "open"});
      const style = document.createElement("style");
      style.textContent = `div{box-sizing:border-box;max-height:30vh;overflow:hidden;padding:8px 14px;border-radius:10px;background:#102a30d9;color:white;text-align:center;font:600 clamp(15px,2vw,25px)/1.35 Segoe UI,Microsoft YaHei,sans-serif;text-shadow:0 1px 2px #0009;white-space:pre-wrap}small{display:block;color:#bce8e5;font:400 .67em/1.35 Segoe UI,Microsoft YaHei,sans-serif;margin-bottom:4px}`;
      box = document.createElement("div");
      shadow.append(style, box);
    }
    const videoFullscreen = !!video && document.fullscreenElement === video;
    const parent = document.fullscreenElement && !videoFullscreen
      ? document.fullscreenElement : document.documentElement;
    if (host.parentNode !== parent) parent.append(host);
    if (videoFullscreen && !host.matches(":popover-open")) {
      host.popover = "manual";
      host.style.display = "block";
      host.showPopover();
    } else if (!videoFullscreen && host.hasAttribute("popover")) {
      if (host.matches(":popover-open")) host.hidePopover();
      host.removeAttribute("popover");
    }
    return host;
  }

  function position() {
    if (!host) return false;
    if (!video) {
      host.style.width = "min(80vw,900px)";
      host.style.left = "10vw";
      host.style.bottom = "50px";
      host.style.display = hasSubtitle ? "block" : "none";
      return true;
    }
    if (!video.isConnected) { host.style.display = "none"; return false; }
    const rect = video.getBoundingClientRect();
    if (rect.width < 120 || rect.height < 80 || rect.bottom < 0 || rect.top > innerHeight) {
      host.style.display = "none";
      return false;
    }
    host.style.width = Math.min(rect.width * .88, innerWidth - 24) + "px";
    host.style.left = Math.max(12, rect.left + rect.width * .06) + "px";
    host.style.bottom = Math.max(12, innerHeight - rect.bottom + rect.height * .08) + "px";
    host.style.display = hasSubtitle ? "block" : "none";
    return true;
  }

  function show(source, translated = "", note = "") {
    if (!active) return;
    overlay();
    box.replaceChildren();
    if (source) {
      const small = document.createElement("small");
      small.textContent = source;
      box.append(small);
    }
    box.append(document.createTextNode(translated || note || "翻译中…"));
    hasSubtitle = true;
    position();
    const control = controls.get(video);
    if (control) control.status.textContent = note || (translated ? "翻译中" : "正在翻译字幕…");
  }

  function chosenVideo() {
    return candidates()
      .filter(visible)
      .sort((a,b) => {
        const ar = a.getBoundingClientRect(), br = b.getBoundingClientRect();
        return (br.width * br.height * (b.paused ? .5 : 1)) -
               (ar.width * ar.height * (a.paused ? .5 : 1));
      })[0] || null;
  }

  function detachTrack() {
    if (track) {
      track.removeEventListener("cuechange", cueChanged);
      track.mode = formerMode;
      track = null;
    }
  }

  async function translate(text, stamp, finalId = null) {
    const current = () => active && (finalId === null ? stamp === revision : finalId === speechFinalId);
    try {
      const scope = await videoScope();
      if (!current()) return;
      adoptScope(scope);
      if (!current()) return;
      const key = scope + "\n" + text;
      let translated = cache.get(key);
      if (!translated) {
        const reply = await chrome.runtime.sendMessage({type:"VIDEO_TRANSLATE",text});
        if (!reply?.ok) throw new Error(reply?.error || "翻译失败");
        translated = reply.value.text;
        if ((await videoScope()) !== scope) return;
        if (cache.size > 200) cache.delete(cache.keys().next().value);
        cache.set(key, translated);
      }
      if (current()) show(text, translated);
    } catch (error) {
      if (current()) show(text, "", error.message);
    }
  }

  async function videoScope() {
    const reply = await chrome.runtime.sendMessage({type:"VIDEO_SCOPE"});
    if (!reply?.ok || typeof reply.value?.scope !== "string")
      throw new Error(reply?.error || "视频翻译设置不可用");
    return reply.value.scope;
  }

  function adoptScope(scope) {
    if (scope === cacheScope) return;
    const changed = !!cacheScope;
    cacheScope = scope;
    if (!changed) return;
    cache.clear();
    ++revision;
    ++speechFinalId;
    if (mode === "captions") {
      const currentCue = lastCue;
      lastCue = "";
      if (track) cueChanged(); else if (provider) acceptCue(currentCue);
    } else {
      hasSubtitle = false;
      if (host) host.style.display = "none";
    }
  }

  async function refreshScope() {
    try {
      const scope = await videoScope();
      if (active) adoptScope(scope);
    } catch {}
  }

  function cueChanged() {
    if (!active || mode !== "captions" || !track) return;
    acceptCue([...(track.activeCues || [])].map(cue => cue.text || "").join("\n"));
  }
  function acceptCue(value) {
    const text = value.replace(/<[^>]*>/g, "").trim().slice(0, 1200);
    if (text === lastCue) return;
    lastCue = text;
    const stamp = ++revision;
    if (!text) { hasSubtitle = false; if (host) host.style.display = "none"; return; }
    show(text);
    translate(text, stamp);
  }

  async function start(forceSpeech = false, targetIndex = null) {
    stop();
    const lifecycle = videoLifecycle;
    video = Number.isInteger(targetIndex)
      ? [...document.querySelectorAll("video,iframe")][targetIndex] : chosenVideo();
    if (!video || !visible(video)) { video = null; return {mode:"none",reason:"未找到可见视频，请滚动到播放器再启动"}; }
    active = true;
    lastCue = "";
    cacheScope = "";
    cache.clear();
    hasSubtitle = false;
    const candidates = [...(video?.textTracks || [])].filter(t =>
      ["subtitles","captions"].includes(t.kind));
    track = forceSpeech ? null : candidates.find(t => t.mode === "showing") || candidates[0] || null;
    if (!track && !forceSpeech) {
      const channel = vimeoChannel(video);
      if (channel) {
        provider = channel;
        show("", "", "正在读取播放器字幕…");
        try {
          const tracks = await channel.call("getTextTracks");
          if (lifecycle !== videoLifecycle) return {mode:"none",reason:"视频翻译已取消"};
          const available = (Array.isArray(tracks) ? tracks : []).filter(t => ["subtitles","captions"].includes(t.kind));
          const selected = available.every(t => ["showing","disabled"].includes(t.mode))
            ? available.find(t => t.mode === "showing") || available[0] : null;
          if (selected) {
            channel.former = available.find(t => t.mode === "showing") || null;
            mode = "captions";
            channel.send("addEventListener","cuechange");
            channel.changed = true;
            await channel.call("enableTextTrack",{language:selected.language,kind:selected.kind,showing:false});
            if (lifecycle !== videoLifecycle) return {mode:"none",reason:"视频翻译已取消"};
          }
        } catch { if (lifecycle === videoLifecycle) mode = ""; }
        if (lifecycle !== videoLifecycle) return {mode:"none",reason:"视频翻译已取消"};
        if (mode !== "captions") { releaseProvider(channel); provider = null; }
      }
    }
    mode = track || provider ? "captions" : "speech";
    overlay();
    if (track) {
      formerMode = track.mode;
      track.mode = "hidden";
      track.addEventListener("cuechange", cueChanged);
      show("", "", "字幕翻译已开启，播放视频后显示译文；无字幕时可点「语音译」");
      cueChanged();
    } else if (!lastCue) show("", "", mode === "captions" ? "字幕翻译已开启，播放视频后显示译文" : "准备识别视频声音…");
    timer = setInterval(() => {
      if (video && !video.isConnected) { stop(true); return; }
      position();
      if (mode === "captions") cueChanged();
      refreshScope();
    }, 1000);
    updateControls();
    return {mode};
  }

  function stop(notify = false) {
    ++videoLifecycle;
    const wasSpeech = notify && active && mode === "speech";
    active = false;
    hasSubtitle = false;
    ++revision;
    ++speechFinalId;
    detachTrack();
    if (provider) {
      releaseProvider(provider); provider = null;
    }
    if (timer) clearInterval(timer);
    timer = null;
    if (host) host.remove();
    const control = controls.get(video);
    if (control) control.status.textContent = "";
    mode = ""; video = null; lastCue = "";
    updateControls();
    if (wasSpeech) chrome.runtime.sendMessage({type:"VIDEO_AUDIO_STOP_PAGE"}).catch(() => {});
  }

  chrome.runtime.onMessage.addListener((message, _sender, respond) => {
    if (message.type === "IT_VIDEO_START") {
      start(!!message.forceSpeech, message.targetIndex).then(respond, error => respond({mode:"none",reason:error.message}));
      return true;
    }
    if (message.type === "IT_VIDEO_STOP") { stop(); respond({stopped:true}); }
    if (message.type === "IT_VIDEO_SPEECH" && active && mode === "speech") {
      const stamp = ++revision;
      const finalId = ++speechFinalId;
      show(message.source, message.text || "", message.note || "翻译中…");
      if (message.source && !message.text && !message.note)
        translate(message.source, stamp, finalId);
      respond({shown:true});
    }
    if (message.type === "IT_VIDEO_SPEECH_PREVIEW" && active && mode === "speech") {
      ++revision;
      show(message.source, "", "识别中…");
      respond({shown:true});
    }
  });
  addEventListener("fullscreenchange", () => {
    if (active) { overlay(); position(); }
    updateControls();
  });
  addEventListener("scroll", () => { updateControls(); if (active) position(); }, true);
  addEventListener("resize", () => { updateControls(); if (active) position(); });
  new MutationObserver(changes => {
    if (changes.some(change => change.type === "attributes" ? change.target.matches("video,iframe") :
      [...change.addedNodes,...change.removedNodes].some(node =>
        node.nodeType === 1 && (node.matches("video,iframe") || node.querySelector("video,iframe"))))) discoverControls();
  }).observe(document.documentElement,{subtree:true,childList:true,attributes:true,attributeFilter:["src","title","allow","allowfullscreen"]});
  discoverControls();
  addEventListener("pagehide", () => stop(true));
})();
