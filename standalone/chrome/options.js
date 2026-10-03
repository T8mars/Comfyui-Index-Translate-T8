"use strict";
const $ = (id) => document.getElementById(id);
let cfg;
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
for (const [value, label] of Object.entries(languages)) {
  for (const id of value === "auto" ? ["source"] : ["source", "target"]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    $(id).append(option);
  }
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
$("pair").addEventListener("click", async () => {
  try {
    await message({
      type: "PAIR",
      endpoint: $("endpoint").value.trim(),
      code: $("code").value.trim(),
    });
    $("code").value = "";
    tell("配对成功，可以开始翻译网页。");
    cfg = await message({ type: "CONFIG" });
  } catch (error) {
    tell(error.message, true);
  }
});
$("check").addEventListener("click", async () => {
  try {
    const value = await message({ type: "CHECK" });
    tell(
      "服务已连接 · " +
        value.model +
        " · " +
        (value.model_ready ? "模型已加载" : "首次翻译时加载模型"),
    );
  } catch (error) {
    tell(error.message, true);
  }
});
$("save").addEventListener("click", async () => {
  try {
    await message({
      type: "PREFERENCES",
      source: $("source").value,
      target: $("target").value,
      glossary: JSON.parse($("glossary").value || "{}"),
      viewport: $("viewport").checked,
    });
    tell("偏好已保存，正在翻译的页面可还原后重新开始。");
  } catch (error) {
    tell(error.message, true);
  }
});
$("clear").addEventListener("click", async () => {
  try {
    await message({ type: "CLEAR_CACHE" });
    tell("本机译文缓存已清空。");
  } catch (error) {
    tell(error.message, true);
  }
});
$("reset-floating").addEventListener("click", async () => {
  try { await message({type:"RESET_FLOAT_POSITION"}); tell("悬浮按钮已回到右边，仍可自由拖动。"); }
  catch (error) { tell(error.message,true); }
});
function rules() {
  const list = $("rules");
  list.replaceChildren();
  for (const [origin, enabled] of Object.entries(cfg.rules)) {
    if (!enabled) continue;
    const item = document.createElement("li");
    const label = document.createElement("span");
    label.textContent = origin;
    const button = document.createElement("button");
    button.className = "secondary";
    button.textContent = "关闭";
    button.addEventListener("click", async () => {
      try {
        await message({ type: "RULE", origin, enabled: false });
        cfg = await message({ type: "CONFIG" });
        rules();
        tell("本站自动翻译已关闭。");
      } catch (error) {
        tell(error.message, true);
      }
    });
    item.append(label, button);
    list.append(item);
  }
  if (!list.children.length) {
    const item = document.createElement("li");
    item.textContent = "暂无自动翻译站点";
    list.append(item);
  }
}
function showUpdate(state) {
  $("update-status").textContent = state.available
    ? `发现新版本 ${state.version}。请下载代码包并重新加载扩展。`
    : `当前版本 ${chrome.runtime.getManifest().version}；未发现更新。`;
}
$("check-update").addEventListener("click", async () => {
  $("update-status").textContent = "正在检查更新…";
  try {
    showUpdate(await message({ type: "UPDATE_CHECK" }));
  } catch (error) {
    $("update-status").textContent = error.message;
  }
});
async function initialize() {
  try {
    cfg = await message({ type: "CONFIG" });
    $("endpoint").value = cfg.endpoint;
    $("source").value = cfg.source;
    $("target").value = cfg.target;
    $("viewport").checked = cfg.viewport;
    $("glossary").value = JSON.stringify(cfg.glossary, null, 2);
    rules();
    showUpdate(await message({ type: "UPDATE_STATUS" }));
    tell(
      cfg.paired
        ? "已配对。可检查连接或直接翻译网页。"
        : "尚未配对，请先启动整合包并生成配对码。",
    );
  } catch (error) {
    tell(error.message, true);
  }
}
$("reload-extension").addEventListener("click", async () => {
  $("reload-extension").disabled = true;
  tell("正在停止视频翻译并重新加载扩展…");
  try {
    for (const tab of await chrome.tabs.query({})) {
      await message({type:"VIDEO_AUDIO_STOP",tabId:tab.id});
      try { await chrome.tabs.sendMessage(tab.id,{type:"IT_VIDEO_STOP"}, {frameId:0}); } catch {}
    }
    await message({type:"VIDEO_WAIT_IDLE"}).catch(() => {});
    chrome.runtime.reload();
  } catch (error) {
    $("reload-extension").disabled = false;
    tell(error.message,true);
  }
});
initialize();
