// Exact translation cache and reference-counted in-flight video requests.
// Dependencies are passed in so the same lifecycle can be tested without Chrome.
export function createVideoJobs({api, backend, cache, cacheKey, sha, verifyResults, preferences}) {
  const inflight = new Map(), consumers = new Map(), generations = new Map(), stopped = new Set();
  const consumerKey = (tab, token) => `${tab}:${token}`;
  const generation = tab => generations.get(tab) || 0;
  const validToken = token => typeof token === 'string' && /^[a-zA-Z0-9_-]{8,100}$/.test(token);
  async function cancelEntry(entry) {
    if (entry.cancelled || entry.consumers.size) return;
    entry.cancelled = true;
    if (inflight.get(entry.key) === entry) inflight.delete(entry.key);
    try {
      if (entry.status.capabilities?.includes('cancel_by_request_v1'))
        await api(entry.cfg, `/api/requests/${encodeURIComponent(entry.requestId)}`, 'DELETE');
      else if (entry.jobId) await api(entry.cfg, `/api/jobs/${entry.jobId}`, 'DELETE');
    } catch {}
  }
  async function cancel(tab, tokens = null) {
    if (tokens !== null && (!Array.isArray(tokens) || tokens.length > 32 || tokens.some(t => !validToken(t))))
      throw new Error('无效的视频取消请求');
    if (tokens === null) generations.set(tab, generation(tab) + 1);
    const keys = tokens === null ? [...consumers.keys()].filter(k => k.startsWith(`${tab}:`))
      : tokens.map(t => consumerKey(tab, t));
    for (const key of keys) {
      stopped.add(key);
      const entry = consumers.get(key);
      consumers.delete(key);
      if (entry) { entry.consumers.delete(key); await cancelEntry(entry); }
    }
    while (stopped.size > 512) stopped.delete(stopped.values().next().value);
    return {cancelled: true};
  }
  async function translate(cfg, value, tab, token, prefetch = false) {
    if (!validToken(token)) throw new Error('无效的视频请求编号');
    const text = String(value || '').trim();
    if (!text || text.length > 1200) throw new Error('视频字幕长度无效');
    const stamp = generation(tab), consumer = consumerKey(tab, token);
    const alive = () => generation(tab) === stamp && !stopped.has(consumer);
    const status = await backend(cfg);
    if (!alive()) throw new Error('视频翻译已取消');
    const paragraph = {id: 'cue', text, source_hash: await sha(text)};
    const key = await cacheKey(cfg, status, paragraph);
    if (!alive()) throw new Error('视频翻译已取消');
    let hit;
    try { hit = await cache.get(key); } catch {}
    if (!alive()) throw new Error('视频翻译已取消');
    if (hit?.model_revision === status.revision && hit.prompt_version === status.prompt_version) {
      try {
        verifyResults({status:'completed',done:1,total:1,results:[{...hit,id:'cue',source_hash:paragraph.source_hash}]}, [paragraph]);
        return {text:hit.text,cached:true};
      } catch {} // A corrupt cache entry is never rendered.
    }
    let entry = inflight.get(key);
    if (!entry) {
      entry = {key,cfg,status,requestId:crypto.randomUUID(),consumers:new Set(),cancelled:false,prefetch};
      inflight.set(key, entry);
    }
    entry.consumers.add(consumer);
    consumers.set(consumer, entry);
    if (!prefetch && entry.prefetch) {
      entry.prefetch = false;
      if (entry.jobId && status.capabilities?.includes('video_priority_v1')) {
        try { await api(cfg, `/api/jobs/${entry.jobId}/priority`, 'POST', {}); } catch {}
      }
    }
    if (!entry.promise) entry.promise = (async () => {
      let completed = false;
      try {
        const submitted = await api(cfg, '/api/jobs', 'POST', {
          request_id:entry.requestId,expected_identity:cfg.identity,
          page_epoch:prefetch ? 'video-prefetch' : 'video',...preferences(cfg),output_budget:512,
          paragraphs:[paragraph],
        },30000);
        entry.jobId = submitted.id;
        if (prefetch && !entry.prefetch && status.capabilities?.includes('video_priority_v1')) {
          try { await api(cfg, `/api/jobs/${entry.jobId}/priority`, 'POST', {}); } catch {}
        }
        if (entry.cancelled) {
          try { await api(cfg, `/api/jobs/${entry.jobId}`, 'DELETE'); } catch {}
          throw new Error('视频翻译已取消');
        }
        const deadline = Date.now() + 120000;
        const wait = status.capabilities?.includes('job_wait_v1');
        let result = submitted;
        while (Date.now() < deadline) {
          if (entry.cancelled) throw new Error('视频翻译已取消');
          if (result.identity !== cfg.identity || result.model_revision !== status.revision ||
              result.prompt_version !== status.prompt_version) throw new Error('模型或服务版本已变化，请重试');
          if (result.status === 'completed') {
            verifyResults(result, [paragraph]);
            const translated = result.results[0];
            if (translated.model_revision !== status.revision || translated.prompt_version !== status.prompt_version)
              throw new Error('译文模型版本不匹配');
            completed = true;
            try { await cache.put(key, translated); } catch {}
            return {text:translated.text,cached:false};
          }
          if (['failed','cancelled'].includes(result.status)) throw new Error(result.error || '视频字幕翻译失败');
          if (!wait) await new Promise(resolve => setTimeout(resolve,100));
          result = await api(cfg, `/api/jobs/${submitted.id}${wait ? '?wait_ms=10000' : ''}`, 'GET', undefined,12000);
        }
        throw new Error('视频字幕翻译等待超时');
      } finally {
        if (!completed) {
          entry.consumers.clear();
          await cancelEntry(entry);
        }
        if (inflight.get(key) === entry) inflight.delete(key);
      }
    })();
    try {
      const result = await entry.promise;
      if (!alive()) throw new Error('视频翻译已取消');
      return result;
    } finally {
      if (consumers.get(consumer) === entry) consumers.delete(consumer);
      entry.consumers.delete(consumer);
    }
  }
  return {translate,cancel};
}
