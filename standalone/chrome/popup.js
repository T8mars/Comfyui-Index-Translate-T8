"use strict";
const $ = (id) => document.getElementById(id);
let tab, cfg;
let videoStarting = false, videoStopping = false, videoRevision = 0, videoTouched = false, videoRefreshBusy = false;
const languages = {
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
for (const [value, label] of Object.entries(languages)) {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = label;
  $("target").append(option);
}
function tell(text, error = false) {
  $("message").textContent = text;
  $("message").classList.toggle("error", error);
}
async function message(payload) {
  const reply = await chrome.runtime.sendMessage(payload);
  if (!reply?.ok) throw new Error(reply?.error || "后台未响应");
  return reply.value;
}
async function inject() {
  if (!tab || !/^https?:\/\//.test(tab.url))
    throw new Error("请选择普通 HTTP/HTTPS 网页");
  await chrome.scripting.insertCSS({
    target: { tabId: tab.id },
    files: ["content.css"],
  });
  await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    files: ["content.js", "video.js"],
  });
}
async function preferences() {
  cfg.target = $("target").value;
  await message({
    type: "PREFERENCES",
    source: cfg.source,
    target: cfg.target,
    glossary: cfg.glossary,
    viewport: cfg.viewport,
  });
}
$("translate").addEventListener("click", async () => {
  try {
    await preferences();
    await inject();
    await chrome.tabs.sendMessage(tab.id, { type: "IT_RESTORE" });
    await chrome.tabs.sendMessage(tab.id, { type: "IT_START" });
    tell("翻译已开始。先处理视口附近的正文，滚动时继续翻译。");
  } catch (error) {
    tell(error.message, true);
  }
});
$("restore").addEventListener("click", async () => {
  try {
    await inject();
    await chrome.tabs.sendMessage(tab.id, { type: "IT_RESTORE" });
    tell("原文已还原，当前页面暂停自动翻译。");
  } catch (error) {
    tell(error.message, true);
  }
});
async function startVideo(forceSpeech = false) {
  if (videoStarting || videoStopping) return;
  const stamp = ++videoRevision;
  videoStarting = true;
  videoTouched = true;
  $("video").disabled = $("video-speech").disabled = true;
  videoTell(forceSpeech ? "正在准备声音识别并授权当前标签页…" : "正在读取视频字幕，必要时启动声音识别…");
  try {
    if (!cfg) throw new Error("扩展仍在初始化，请稍后重试。");
    if (!tab || !/^https?:\/\//.test(tab.url))
      throw new Error("请选择含 HTML5 视频的普通网页");
    await preferences();
    if (stamp !== videoRevision) return;
    const result = await message({type:"VIDEO_START_UI",tabId:tab.id,forceSpeech});
    if (stamp !== videoRevision) return;
    if (result.mode === "none") throw new Error(result.reason);
    if (result.mode === "speech") {
      videoTell(result.authorizationRequired ? result.note : "声音授权已通过，正在加载 R2T2 语音模型；首次可能需要几十秒，请播放有声音的视频。", !!result.authorizationRequired);
    } else videoTell("已开始翻译视频字幕轨。");
  } catch (error) {
    if (stamp !== videoRevision) return;
    videoTell(error.message,true);
    if (tab) {
      try { await message({type:"VIDEO_AUDIO_STOP",tabId:tab.id}); } catch {}
      if (stamp === videoRevision) {
        try { await chrome.tabs.sendMessage(tab.id,{type:"IT_VIDEO_STOP"}); } catch {}
      }
    }
  } finally {
    if (stamp === videoRevision) {
      videoStarting = false;
      $("video").disabled = $("video-speech").disabled = false;
    }
  }
}
function videoTell(text, error = false) {
  $("video-message").textContent = text;
  $("video-message").classList.toggle("error",error);
}
async function refreshVideo() {
  if (!tab || videoStarting || videoStopping || videoRefreshBusy) return;
  const stamp = videoRevision;
  videoRefreshBusy = true;
  try {
    const state = await message({type:"VIDEO_STATUS_UI",tabId:tab.id});
    if (stamp !== videoRevision || videoStarting || videoStopping) return;
    if (state.phase !== "idle" || !videoTouched) videoTell(state.note,!!state.error);
  } catch(error) {
    if (videoTouched && stamp === videoRevision) videoTell("声音识别状态暂时无法确认：" + error.message,true);
  } finally { videoRefreshBusy=false; }
}
$("video").addEventListener("click", () => startVideo(false));
$("video-speech").addEventListener("click", () => startVideo(true));
$("video-stop").addEventListener("click", async () => {
  if (videoStopping) return;
  const stamp = ++videoRevision;
  videoStarting=false;
  videoStopping=true;
  videoTouched=true;
  $("video").disabled = $("video-speech").disabled = $("video-stop").disabled = true;
  videoTell("正在停止声音采集和视频翻译…");
  try {
    if (!tab) return;
    await message({type:"VIDEO_AUDIO_STOP",tabId:tab.id});
    try { await chrome.tabs.sendMessage(tab.id,{type:"IT_VIDEO_STOP"}); } catch {}
    videoTell("视频翻译已停止，标签页声音恢复正常播放。");
  } catch (error) {
    if (stamp === videoRevision) videoTell(error.message,true);
  } finally {
    if (stamp === videoRevision) {
      videoStopping=false;
      $("video").disabled = $("video-speech").disabled = $("video-stop").disabled = false;
    }
  }
});
$("auto").addEventListener("change", async () => {
  try {
    if (!tab || !/^https?:\/\//.test(tab.url))
      throw new Error("该页面不支持自动翻译");
    const origin = new URL(tab.url).origin;
    const enabled = $("auto").checked;
    if (enabled) {
      const granted = await chrome.permissions.request({
        origins: [origin + "/*"],
      });
      if (!granted) {
        $("auto").checked = false;
        throw new Error("需要授予本站权限才能自动翻译");
      }
    }
    await preferences();
    await message({ type: "RULE", origin, enabled });
    await inject();
    await chrome.tabs.sendMessage(tab.id, {
      type: enabled ? "IT_START" : "IT_STOP",
    });
    tell(
      enabled
        ? "本站已启用自动翻译，刷新和动态正文也会生效。"
        : "本站自动翻译已关闭。",
    );
  } catch (error) {
    tell(error.message, true);
  }
});
async function refresh() {
  if (!tab) return;
  try {
    const result = await chrome.tabs.sendMessage(tab.id, { type: "IT_STATUS" });
    $("progress").textContent =
      `完成 ${result.completed} · 处理中 ${result.running} · 等待 ${result.waiting} · 跳过 ${result.skipped} · 失败 ${result.failed}`;
  } catch {}
}
async function initialize() {
  try {
    cfg = await message({ type: "CONFIG" });
    [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    $("target").value = cfg.target;
    $("site").textContent = tab?.url ? new URL(tab.url).hostname : "未选择网页";
    if (tab && /^https?:\/\//.test(tab.url))
      $("auto").checked = !!cfg.rules[new URL(tab.url).origin];
    if (!cfg.paired) tell("请先打开「配对与设置」连接整合包。");
    else {
      try {
        const check = await message({ type: "CHECK" });
        tell("本地服务已连接 · " + check.model.split("/").at(-1));
      } catch (error) {
        tell(error.message, true);
      }
    }
    await refresh();
    await refreshVideo();
    setInterval(refresh, 1500);
    setInterval(refreshVideo, 1500);
  } catch (error) {
    tell(error.message, true);
  }
}
initialize();
