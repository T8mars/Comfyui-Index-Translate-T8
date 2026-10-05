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
  old.drain?.reject(new Error('声音采集已结束'));
  old.stream.getTracks().forEach(track => track.stop());
  old.worklet.port.onmessage = null;
  try { await old.context.close(); } catch {}
}

function matches(value, tabId, captureId) {
  return value && (tabId == null || value.tabId === tabId) &&
    (captureId == null || value.captureId === captureId);
}

function sendBuffered(current) {
  if (!current.buffered) return;
  const joined = new Float32Array(current.buffered);
  let offset = 0;
  for (const frame of current.frames) { joined.set(frame,offset); offset += frame.length; }
  current.frames = [];
  current.buffered = 0;
  current.queued += joined.length;
  const cycle = current.cycle, frameCaptureId = current.captureId;
  if (current.queued > 3 * 16000) {
    stop(current.tabId,frameCaptureId).then(() => chrome.runtime.sendMessage({type:'VIDEO_CAPTURE_ENDED',tabId:current.tabId,captureId:frameCaptureId,
      reason:'语音识别处理速度跟不上视频播放，请稍后重试'}).catch(() => {}));
    return;
  }
  const pcm_f32le_b64 = encodePcm(joined);
  current.sending = current.sending.then(async () => {
    if (capture !== current || current.cycle !== cycle) return;
    const reply = await chrome.runtime.sendMessage({type:'VIDEO_PCM',tabId:current.tabId,captureId:frameCaptureId,samples:joined.length,pcm_f32le_b64});
    if (capture !== current || current.cycle !== cycle) return;
    if (!reply?.ok) throw new Error(reply?.error || '本地语音识别失败');
    current.queued -= joined.length;
  }).catch(error => {
    if (capture === current && current.cycle === cycle)
      stop(current.tabId,frameCaptureId).then(() => chrome.runtime.sendMessage({type:'VIDEO_CAPTURE_ENDED',tabId:current.tabId,captureId:frameCaptureId,
        reason:error.message}).catch(() => {}));
  });
}

async function drain(tabId, captureId) {
  const current = capture;
  if (!matches(current,tabId,captureId)) throw new Error('原有声音采集已结束');
  if (current.drain) return current.drain.promise;
  const cycle = current.cycle;
  let resolve, reject;
  const signal = new Promise((yes,no) => {resolve=yes;reject=no;});
  const timeout = setTimeout(() => reject(new Error('声音收尾等待超时')),5000);
  current.drain = {resolve,reject,promise:null};
  const promise = (async () => {
    try {
      current.worklet.port.postMessage({type:'drain',cycle});
      await signal;
      if (capture !== current || current.cycle !== cycle) throw new Error('声音采集已切换');
      sendBuffered(current);
      current.ready = false;
      current.paused = true;
      await current.sending;
      if (capture !== current || current.cycle !== cycle) throw new Error('声音采集已切换');
    } finally {
      clearTimeout(timeout);
      if (current.cycle === cycle) current.drain = null;
    }
  })();
  if (current.drain) current.drain.promise = promise;
  return promise;
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
    const worklet = new AudioWorkletNode(context, "r2t2-capture",{processorOptions:{paused:true}});
    // Tab capture suppresses normal tab playback. Restore it explicitly.
    source.connect(context.destination);
    source.connect(worklet);
    worklet.connect(context.destination);
    await context.resume();
    if (pending?.token !== token) throw new Error("音频采集已取消");
    const current = {
      tabId, captureId, stream, context, worklet, ready:false, frames:[], buffered:0,
      queued:0, sending:Promise.resolve(), stopping:false, cycle:0,paused:true,
    };
    capture = current;
    pending = null;
    stream.getAudioTracks()[0]?.addEventListener("ended", () => {
      if (capture !== current || current.stopping) return;
      const endedId = current.captureId;
      stop(tabId,endedId).then(() => chrome.runtime.sendMessage({type:"VIDEO_CAPTURE_ENDED",tabId,captureId:endedId}).catch(() => {}));
    });
    worklet.port.onmessage = event => {
      if (event.data?.cycle != null && event.data.cycle !== current.cycle) return;
      if (capture === current && event.data?.type === 'paused') {
        if (event.data.paused === current.paused) current.ready = !current.paused;
        return;
      }
      if (capture === current && event.data?.type === 'drained') {
        current.drain?.resolve(); return;
      }
      if (capture !== current || (!current.ready && !current.drain) || event.data?.type !== "pcm") return;
      current.frames.push(event.data.samples);
      current.buffered += event.data.samples.length;
      if (current.buffered < 5120) return;
      sendBuffered(current);
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
    if (matches(capture,message.tabId,message.captureId)) {
      capture.ready = !message.paused;
      capture.paused = !!message.paused;
      capture.worklet.port.postMessage({type:'pause',paused:!!message.paused,cycle:capture.cycle});
    }
    respond({ok:true});
  }
  if (message.type === 'OFFSCREEN_PAUSE') {
    if (matches(capture,message.tabId,message.captureId)) {
      capture.paused = !!message.paused;
      // Keep accepting previously posted PCM until the DSP pause ACK, which
      // follows those frames on the same port. Resuming can open immediately.
      if (!message.paused) capture.ready = true;
      capture.worklet.port.postMessage({type:'pause',paused:!!message.paused,cycle:capture.cycle});
    }
    respond({ok:true});
  }
  if (message.type === 'OFFSCREEN_DRAIN') {
    drain(message.tabId,message.captureId).then(() => respond({ok:true}),error => respond({ok:false,error:error.message}));
    return true;
  }
  if (message.type === 'OFFSCREEN_RESET') {
    if (typeof message.nextCaptureId !== 'string' || !message.nextCaptureId ||
        !matches(capture,message.tabId,message.captureId)) {
      respond({ok:false,error:'原有声音采集已结束'}); return;
    }
    capture.drain?.reject(new Error('声音采集已切换'));
    capture.drain = null;
    capture.ready = false;
    capture.paused = true;
    capture.captureId = message.nextCaptureId;
    ++capture.cycle;
    capture.frames = []; capture.buffered = capture.queued = 0;
    capture.sending = Promise.resolve();
    capture.worklet.port.postMessage({type:'reset',cycle:capture.cycle});
    respond({ok:true});
  }
  if (message.type === "OFFSCREEN_STOP") {
    stop(message.tabId,message.captureId).then(() => respond({ok:true}));
    return true;
  }
});
