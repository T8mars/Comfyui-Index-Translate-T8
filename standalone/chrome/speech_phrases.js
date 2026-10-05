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
const abbreviations = new Set(['dr','mr','mrs','ms','prof','sr','jr','st','vs','etc','e.g','i.e']);
export function speechPhrase(stable, committed = '', final = false) {
  if (!stable.startsWith(committed)) throw new Error('语音识别已修改确认文本，请重新启动');
  const pending = stable.slice(committed.length);
  let end = final ? pending.length : 0;
  if (!final) {
    for (const match of pending.matchAll(/[。！？!?；;]|\.(?=\s|$)/g)) {
      const before = pending.slice(0,match.index);
      const word = before.match(/([A-Za-z]+(?:\.[A-Za-z]+)*)$/)?.[1] || '';
      if (match[0] === '.' && (abbreviations.has(word.toLowerCase()) || /^(?:[A-Z]\.)*[A-Z]$/.test(word))) continue;
      end = match.index + match[0].length;
    }
    if (!end && (pending.trim().split(/\s+/).length >= 12 || /[\u3400-\u9fff]{24}/.test(pending))) {
      const limited = pending.slice(0,120);
      const boundary = Math.max(limited.lastIndexOf(' '), limited.lastIndexOf('，'), limited.lastIndexOf(','));
      if (boundary >= 20) end = boundary + 1;
      else if (/[\u3400-\u9fff]{24}/.test(pending)) {
        // Stable Chinese often has no spaces or punctuation until the segment
        // finishes. Commit a bounded prefix so live subtitles can start early.
        end = limited.length;
        if (/^[\uD800-\uDBFF]$/.test(limited[end - 1]) && /^[\uDC00-\uDFFF]$/.test(pending[end])) --end;
      }
    }
  }
  return {text: pending.slice(0,end).trim(), committed: committed + pending.slice(0,end)};
}
