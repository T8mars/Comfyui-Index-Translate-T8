"use strict";
const $ = (id) => document.getElementById(id);
let tab, cfg;
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
    files: ["content.js"],
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
  try {
    if (!tab || !/^https?:\/\//.test(tab.url))
      throw new Error("请选择含 HTML5 视频的普通网页");
    await preferences();
    await chrome.scripting.executeScript({target:{tabId:tab.id},files:["video.js"]});
    const result = await chrome.tabs.sendMessage(tab.id,{type:"IT_VIDEO_START",forceSpeech});
    if (result.mode === "none") throw new Error(result.reason);
    if (result.mode === "speech") {
      // The opaque capture ID expires quickly; consume it in the offscreen document now.
      const {streamId} = await message({type:"VIDEO_CAPTURE_ID",tabId:tab.id});
      await message({type:"VIDEO_AUDIO_START",tabId:tab.id,streamId});
      tell("正在加载本地 R2T2 语音模型，随后识别并翻译当前视频声音。");
    } else tell("已开始翻译视频字幕轨。");
  } catch (error) {
    if (tab) {
      try { await message({type:"VIDEO_AUDIO_STOP",tabId:tab.id}); } catch {}
      try { await chrome.tabs.sendMessage(tab.id,{type:"IT_VIDEO_STOP"}); } catch {}
    }
    tell(error.message,true);
  }
}
$("video").addEventListener("click", () => startVideo(false));
$("video-speech").addEventListener("click", () => startVideo(true));
$("video-stop").addEventListener("click", async () => {
  try {
    if (!tab) return;
    await message({type:"VIDEO_AUDIO_STOP",tabId:tab.id});
    try { await chrome.tabs.sendMessage(tab.id,{type:"IT_VIDEO_STOP"}); } catch {}
    tell("视频翻译已停止，标签页声音恢复正常播放。");
  } catch (error) { tell(error.message,true); }
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
    setInterval(refresh, 1500);
  } catch (error) {
    tell(error.message, true);
  }
}
initialize();
