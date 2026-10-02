"use strict";
let capture = null;
let pending = null;
let generation = 0;

function encodePcm(samples) {
  const bytes = new Uint8Array(samples.buffer, samples.byteOffset, samples.byteLength);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 8192)
    binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  return btoa(binary);
}

async function closeCapture(old) {
  old.stopping = true;
  old.stream.getTracks().forEach(track => track.stop());
  old.worklet.port.onmessage = null;
  try { await old.context.close(); } catch {}
}

function matches(value, tabId, captureId) {
  return value && (tabId == null || value.tabId === tabId) &&
    (captureId == null || value.captureId === captureId);
}

async function stop(tabId, captureId) {
  if (matches(pending, tabId, captureId)) pending = null;
  if (!matches(capture, tabId, captureId)) return;
  const old = capture;
  capture = null;
  await closeCapture(old);
}

async function start(tabId, streamId, captureId) {
  const token = ++generation;
  pending = {tabId, captureId, token};
  const old = capture;
  capture = null;
  if (old) await closeCapture(old);
  let stream, context;
  try {
    if (pending?.token !== token) throw new Error("音频采集已取消");
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {mandatory: {chromeMediaSource:"tab", chromeMediaSourceId:streamId}},
      video: false,
    });
    if (pending?.token !== token) throw new Error("音频采集已取消");
    context = new AudioContext();
    await context.audioWorklet.addModule("audio-worklet.js");
    if (pending?.token !== token) throw new Error("音频采集已取消");
    const source = context.createMediaStreamSource(stream);
    const worklet = new AudioWorkletNode(context, "r2t2-capture");
    // Tab capture suppresses normal tab playback. Restore it explicitly.
    source.connect(context.destination);
    source.connect(worklet);
    worklet.connect(context.destination);
    await context.resume();
    if (pending?.token !== token) throw new Error("音频采集已取消");
    const current = {
      tabId, captureId, stream, context, worklet, ready:false, frames:[], buffered:0,
      queued:0, sending:Promise.resolve(), stopping:false,
    };
    capture = current;
    pending = null;
    stream.getAudioTracks()[0]?.addEventListener("ended", () => {
      if (capture !== current || current.stopping) return;
      stop(tabId,captureId).then(() => chrome.runtime.sendMessage({type:"VIDEO_CAPTURE_ENDED",tabId,captureId}).catch(() => {}));
    });
    worklet.port.onmessage = event => {
      if (capture !== current || !current.ready || event.data?.type !== "pcm") return;
      current.frames.push(event.data.samples);
      current.buffered += event.data.samples.length;
      if (current.buffered < 10240) return;
      const joined = new Float32Array(current.buffered);
      let offset = 0;
      for (const frame of current.frames) { joined.set(frame,offset); offset += frame.length; }
      current.frames = [];
      current.buffered = 0;
      current.queued += joined.length;
      if (current.queued > 3 * 16000) {
        stop(tabId,captureId).then(() => chrome.runtime.sendMessage({type:"VIDEO_CAPTURE_ENDED",tabId,captureId,
          reason:"语音识别处理速度跟不上视频播放，请稍后重试"}).catch(() => {}));
        return;
      }
      const pcm_f32le_b64 = encodePcm(joined);
      current.sending = current.sending.then(async () => {
        if (capture !== current) return;
        const reply = await chrome.runtime.sendMessage({
          type:"VIDEO_PCM",tabId,captureId,samples:joined.length,pcm_f32le_b64,
        });
        if (!reply?.ok) throw new Error(reply?.error || "本地语音识别失败");
        current.queued -= joined.length;
      }).catch(error => {
        if (capture === current)
          stop(tabId,captureId).then(() => chrome.runtime.sendMessage({type:"VIDEO_CAPTURE_ENDED",tabId,captureId,
            reason:error.message}).catch(() => {}));
      });
    };
  } catch (error) {
    if (pending?.token === token) pending = null;
    stream?.getTracks().forEach(track => track.stop());
    if (context) try { await context.close(); } catch {}
    throw error;
  }
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id) return;
  if (message.type === "OFFSCREEN_CAPTURE") {
    start(message.tabId,message.streamId,message.captureId || `legacy:${message.tabId}`).then(
      () => respond({ok:true}), error => respond({ok:false,error:error.message}));
    return true;
  }
  if (message.type === "OFFSCREEN_READY") {
    if (matches(capture,message.tabId,message.captureId)) capture.ready = true;
    respond({ok:true});
  }
  if (message.type === "OFFSCREEN_STOP") {
    stop(message.tabId,message.captureId).then(() => respond({ok:true}));
    return true;
  }
});
