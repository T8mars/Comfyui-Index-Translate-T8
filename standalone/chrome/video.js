(() => {
  "use strict";
  if (globalThis.__indexVideoLoaded) return;
  globalThis.__indexVideoLoaded = true;

  let active = false;
  let mode = "", video = null, track = null, formerMode = "";
  let host = null, box = null, timer = null, revision = 0, speechFinalId = 0, lastCue = "";
  const cache = new Map();

  function overlay() {
    if (!host) {
      host = document.createElement("div");
      host.dataset.indexOwned = "video-subtitles";
      host.style.cssText = "position:fixed;z-index:2147483646;pointer-events:none;box-sizing:border-box;display:none;";
      const shadow = host.attachShadow({mode: "open"});
      const style = document.createElement("style");
      style.textContent = `div{box-sizing:border-box;max-height:30vh;overflow:hidden;padding:8px 14px;border-radius:10px;background:#102a30d9;color:white;text-align:center;font:600 clamp(15px,2vw,25px)/1.35 Segoe UI,Microsoft YaHei,sans-serif;text-shadow:0 1px 2px #0009;white-space:pre-wrap}small{display:block;color:#bce8e5;font:400 .67em/1.35 Segoe UI,Microsoft YaHei,sans-serif;margin-bottom:4px}`;
      box = document.createElement("div");
      shadow.append(style, box);
    }
    const parent = document.fullscreenElement && document.fullscreenElement !== video
      ? document.fullscreenElement : document.documentElement;
    if (host.parentNode !== parent) parent.append(host);
    return host;
  }

  function position() {
    if (!host) return;
    if (!video) {
      host.style.width = "min(80vw,900px)";
      host.style.left = "10vw";
      host.style.bottom = "50px";
      return;
    }
    if (!video.isConnected) return;
    const rect = video.getBoundingClientRect();
    if (rect.width < 120 || rect.height < 80 || rect.bottom < 0 || rect.top > innerHeight) {
      host.style.display = "none";
      return;
    }
    host.style.width = Math.min(rect.width * .88, innerWidth - 24) + "px";
    host.style.left = Math.max(12, rect.left + rect.width * .06) + "px";
    host.style.bottom = Math.max(12, innerHeight - rect.bottom + rect.height * .08) + "px";
  }

  function show(source, translated = "", note = "") {
    if (!active) return;
    overlay();
    position();
    box.replaceChildren();
    if (source) {
      const small = document.createElement("small");
      small.textContent = source;
      box.append(small);
    }
    box.append(document.createTextNode(translated || note || "翻译中…"));
    host.style.display = "block";
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
      let translated = cache.get(text);
      if (!translated) {
        const reply = await chrome.runtime.sendMessage({type:"VIDEO_TRANSLATE",text});
        if (!reply?.ok) throw new Error(reply?.error || "翻译失败");
        translated = reply.value.text;
        if (cache.size > 200) cache.delete(cache.keys().next().value);
        cache.set(text, translated);
      }
      if (current()) show(text, translated);
    } catch (error) {
      if (current()) show(text, "", error.message);
    }
  }

  function cueChanged() {
    if (!active || mode !== "captions" || !track) return;
    const text = [...(track.activeCues || [])].map(cue => cue.text || "")
      .join("\n").replace(/<[^>]*>/g, "").trim().slice(0, 1200);
    if (text === lastCue) return;
    lastCue = text;
    const stamp = ++revision;
    if (!text) { if (host) host.style.display = "none"; return; }
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
    }, 1000);
    return {mode};
  }

  function stop(notify = false) {
    const wasSpeech = notify && active && mode === "speech";
    active = false;
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
  addEventListener("pagehide", () => stop(true));
})();
