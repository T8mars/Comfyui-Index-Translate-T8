// Feed replies contain a delta; finish replies contain the complete event history.
export function takeSpeechEvents(reply, previous = 0, snapshot = false) {
  const revision = reply.duplicate && !snapshot && Array.isArray(reply.events) && !reply.events.length
    ? previous : reply.revision;
  const events = reply.events;
  if (!Number.isSafeInteger(revision) || revision < previous || !Array.isArray(events) ||
      (snapshot ? events.length !== revision : events.length !== revision - previous))
    throw new Error('语音事件序列不完整，请重新启动');
  return {events: snapshot ? events.slice(previous) : events, revision};
}

// Translate only the ASR engine's append-only stable prefix, never preview drafts.
export function speechPhrase(stable, committed = '', final = false) {
  if (!stable.startsWith(committed)) throw new Error('语音识别已修改确认文本，请重新启动');
  const pending = stable.slice(committed.length);
  let end = final ? pending.length : 0;
  if (!final) {
    for (const match of pending.matchAll(/[。！？!?；;]|\.(?=\s|$)/g)) {
      const before = pending.slice(0,match.index);
      const word = before.match(/([A-Za-z]+)$/)?.[1] || '';
      if (match[0] === '.' && word && word.length <= 3) continue;
      end = match.index + match[0].length;
    }
    if (!end && (pending.trim().split(/\s+/).length >= 12 || /[\u3400-\u9fff]{24}/.test(pending))) {
      const limited = pending.slice(0,120);
      const boundary = Math.max(limited.lastIndexOf(' '), limited.lastIndexOf('，'), limited.lastIndexOf(','));
      if (boundary >= 20) end = boundary + 1;
    }
  }
  return {text: pending.slice(0,end).trim(), committed: committed + pending.slice(0,end)};
}
