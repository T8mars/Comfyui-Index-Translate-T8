(() => {
  "use strict";
  if (globalThis.__indexVideoLoaded) return;
  globalThis.__indexVideoLoaded = true;

  let active = false;
  let mode = "", video = null, track = null, formerMode = "";
  let host = null, box = null, timer = null, revision = 0, speechFinalId = 0, lastCue = "";
  let cacheScope = "", hasSubtitle = false;
  const cache = new Map();

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
  }

  function chosenVideo() {
    return [...document.querySelectorAll("video")]
      .filter((item) => item.getBoundingClientRect().width >= 120)
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
      lastCue = "";
      cueChanged();
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
    const text = [...(track.activeCues || [])].map(cue => cue.text || "")
      .join("\n").replace(/<[^>]*>/g, "").trim().slice(0, 1200);
    if (text === lastCue) return;
    lastCue = text;
    const stamp = ++revision;
    if (!text) { hasSubtitle = false; if (host) host.style.display = "none"; return; }
    show(text);
    translate(text, stamp);
  }

  function start(forceSpeech = false) {
    stop();
    video = chosenVideo() || [...document.querySelectorAll("iframe")]
      .filter(item => item.getBoundingClientRect().width >= 120)
      .sort((a,b) => b.getBoundingClientRect().width * b.getBoundingClientRect().height -
        a.getBoundingClientRect().width * a.getBoundingClientRect().height)[0];
    active = true;
    const candidates = [...(video?.textTracks || [])].filter(t =>
      ["subtitles","captions"].includes(t.kind));
    track = forceSpeech ? null : candidates.find(t => t.mode === "showing") || candidates[0] || null;
    mode = track ? "captions" : "speech";
    lastCue = "";
    cacheScope = "";
    cache.clear();
    hasSubtitle = false;
    overlay();
    if (track) {
      formerMode = track.mode;
      track.mode = "hidden";
      track.addEventListener("cuechange", cueChanged);
      cueChanged();
    } else show("", "", "准备识别视频声音…");
    timer = setInterval(() => {
      if (video && !video.isConnected) { stop(true); return; }
      position();
      if (mode === "captions") cueChanged();
      refreshScope();
    }, 1000);
    return {mode};
  }

  function stop(notify = false) {
    const wasSpeech = notify && active && mode === "speech";
    active = false;
    hasSubtitle = false;
    ++revision;
    ++speechFinalId;
    detachTrack();
    if (timer) clearInterval(timer);
    timer = null;
    if (host) host.remove();
    mode = ""; video = null; lastCue = "";
    if (wasSpeech) chrome.runtime.sendMessage({type:"VIDEO_AUDIO_STOP_PAGE"}).catch(() => {});
  }

  chrome.runtime.onMessage.addListener((message, _sender, respond) => {
    if (message.type === "IT_VIDEO_START") respond(start(!!message.forceSpeech));
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
  });
  addEventListener("pagehide", () => stop(true));
})();
