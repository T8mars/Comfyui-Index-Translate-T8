"""SRT structure is handled locally; only cue text is sent to translation."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import re
from typing import Callable

MAX_SRT_BYTES = 1024 * 1024
MAX_CUES = 5000
MAX_CUE_CHARS = 12000
_TIME = r"(\d{2,}):([0-5]\d):([0-5]\d),([0-9]{3})"
_TIMING = re.compile(r"^" + _TIME + r"\s+-->\s+" + _TIME + r"(?:\s+.*)?$")
_FORMAT = re.compile(r"</?(?:i|b|u|font)\b[^<>\r\n]*>|\{\\[^{}\r\n]+\}", re.I)
_PLACEHOLDER = re.compile(r"%%IT_[A-Za-z0-9_]+%%")


@dataclass(frozen=True)
class Cue:
    number: str
    timing: str
    text: str


def _milliseconds(groups):
    hours, minutes, seconds, millis = map(int, groups)
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def parse_srt(text: str) -> list[Cue]:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("请输入或上传包含时间轴的 SRT 字幕")
    if len(text.encode('utf-8')) > MAX_SRT_BYTES:
        raise ValueError("SRT 文件超过 1 MiB，请拆成多个字幕文件")
    if '\x00' in text:
        raise ValueError("字幕包含空字符，请另存为 UTF-8 SRT")
    normalized = text.removeprefix('\ufeff').replace('\r\n', '\n').replace('\r', '\n').strip()
    blocks = re.split(r"\n[ \t]*\n+", normalized)
    if len(blocks) > MAX_CUES:
        raise ValueError("一个 SRT 最多支持 5000 条字幕，请分段处理")
    cues = []
    for ordinal, block in enumerate(blocks, 1):
        lines = block.split('\n')
        if len(lines) < 3 or not re.fullmatch(r"[0-9]+", lines[0].strip()):
            raise ValueError(f"第 {ordinal} 条字幕缺少序号、时间轴或正文；纯文本不能确定真实时间轴")
        timing = lines[1].strip()
        match = _TIMING.fullmatch(timing)
        if not match:
            raise ValueError(f"第 {ordinal} 条字幕时间轴无效，请使用 HH:MM:SS,mmm --> HH:MM:SS,mmm")
        if _milliseconds(match.groups()[4:8]) <= _milliseconds(match.groups()[:4]):
            raise ValueError(f"第 {ordinal} 条字幕结束时间必须晚于开始时间")
        body = '\n'.join(lines[2:]).strip()
        if not body or len(body) > MAX_CUE_CHARS:
            raise ValueError(f"第 {ordinal} 条字幕为空或超过 12000 字符")
        cues.append(Cue(lines[0].strip(), timing, body))
    return cues


def decode_srt(data: bytes) -> str:
    if len(data) > MAX_SRT_BYTES:
        raise ValueError("SRT 文件超过 1 MiB，请分段处理")
    # A UTF-16 BOM is necessary to distinguish this from legacy Chinese bytes.
    for encoding in (('utf-16',) if data.startswith((b'\xff\xfe', b'\xfe\xff'))
                     else ('utf-8-sig', 'gb18030')):
        try:
            text = data.decode(encoding)
        except UnicodeError:
            continue
        if '\x00' in text:
            raise ValueError("字幕包含空字符，请另存为 UTF-8 SRT")
        parse_srt(text)
        return text
    raise ValueError("无法读取字幕编码，请另存为 UTF-8 SRT")


def protect_formatting(text: str) -> tuple[str, dict[str, str]]:
    nonce = hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]
    # Never overwrite a literal placeholder already present in the subtitle.
    while f'%%IT_srt_{nonce}_' in text:
        nonce += 'x'
    replacements = {}
    def replace(match):
        token = f'%%IT_srt_{nonce}_{len(replacements)}%%'
        replacements[token] = match.group()
        return token
    return _FORMAT.sub(replace, text), replacements


def restore_translation(cue: Cue, text: str) -> str:
    protected, replacements = protect_formatting(cue.text)
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"字幕 {cue.number} 的译文为空，未导出文件")
    if Counter(_PLACEHOLDER.findall(protected)) != Counter(_PLACEHOLDER.findall(text)):
        raise ValueError(f"字幕 {cue.number} 的格式标记丢失，请重试")
    for token, original in replacements.items():
        text = text.replace(token, original)
    # Empty lines would accidentally start a new SRT block.
    lines = [line.strip() for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n') if line.strip()]
    if not lines or any(_TIMING.fullmatch(line) for line in lines) or '\x00' in text:
        raise ValueError(f"字幕 {cue.number} 的译文包含无效字幕结构，请重试")
    return '\n'.join(lines)


def render_srt(cues: list[Cue], translations: list[str], bilingual: bool = False) -> str:
    if len(cues) != len(translations) or not cues:
        raise ValueError("字幕尚未全部翻译完成，不能导出完整 SRT")
    blocks = []
    for cue, translation in zip(cues, translations):
        body = restore_translation(cue, translation)
        if bilingual:
            body = cue.text + '\n' + body
        blocks.append(f'{cue.number}\n{cue.timing}\n{body}')
    result = '\n\n'.join(blocks) + '\n'
    # Do not apply the input byte/cue limits to expanded English output.
    return result.replace('\n', '\r\n')


def subtitle_request(text: str, options: dict) -> dict:
    cues = parse_srt(text)
    paragraphs = []
    for ordinal, cue in enumerate(cues):
        protected, _ = protect_formatting(cue.text)
        paragraphs.append({'id': str(ordinal), 'text': protected,
                           'source_hash': hashlib.sha256(protected.encode('utf-8')).hexdigest()})
    return {**options, 'page_epoch': 'subtitles', 'kind': 'srt', 'srt_text': text, 'paragraphs': paragraphs}


def translate_srt(text: str, translate: Callable[[str], dict], *, bilingual=False,
                  cancel_check=None, progress=None) -> tuple[str, dict]:
    def check_cancel():
        if cancel_check and cancel_check():
            from .inference import TranslationCancelled
            raise TranslationCancelled('字幕翻译已取消')
    cues = parse_srt(text)
    translations = []
    for ordinal, cue in enumerate(cues, 1):
        check_cancel()
        protected, _ = protect_formatting(cue.text)
        result = translate(protected)
        if result.get('status') != 'completed':
            raise ValueError(f'字幕 {cue.number} 的译文被截断，请提高每条输出预算')
        restore_translation(cue, result.get('text'))
        translations.append(result['text'])
        if progress:
            progress(ordinal, len(cues))
    check_cancel()
    return render_srt(cues, translations, bilingual), {'cues': len(cues), 'bilingual': bilingual,
                                                     'timing_preserved': True}
