#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


ENTRY_RE = re.compile(r"^####\s+((?:SPK|WRT)-[A-Z]+-\d{2}[A-Z]?)(?:\s*-\s*(.+?))?\s*$")
ROLE_LABEL_LINE_RE = re.compile(r"^[A-ZÅÄÖ][A-Za-zÅÄÖåäö '\-]{0,24}:\s+.+$")
ROLE_LABEL_PREFIX_RE = re.compile(r"^[A-ZÅÄÖ][A-Za-zÅÄÖåäö '\-]{0,24}:\s*")


@dataclass
class Entry:
    id: str
    title: str
    lines: list[str]


@dataclass
class ManifestInterval:
    id: str
    start: float
    end: float
    text_fi: str
    spoken_segments: list[dict]


def load_tkv_module(script_path: Path):
    spec = importlib.util.spec_from_file_location("tkv", str(script_path))
    if not spec or not spec.loader:
        raise RuntimeError(f"Could not load module from {script_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def split_entries(lines: list[str]) -> list[Entry]:
    out: list[Entry] = []
    cur_id = None
    cur_title = None
    cur_lines: list[str] = []
    for line in lines:
        m = ENTRY_RE.match(line)
        if m:
            if cur_id:
                out.append(Entry(cur_id, cur_title or "", cur_lines))
            cur_id = m.group(1)
            cur_title = (m.group(2) or "").strip()
            cur_lines = []
            continue
        if cur_id:
            cur_lines.append(line)
    if cur_id:
        out.append(Entry(cur_id, cur_title or "", cur_lines))
    return out


def extract_block_lines(lines: list[str], label: str) -> list[str]:
    prefix = f"**{label}:**"
    start = None
    inline = ""
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            start = i
            inline = line[len(prefix) :].strip()
            break
    if start is None:
        return []

    out: list[str] = []
    if inline:
        out.append(inline)
    for line in lines[start + 1 :]:
        s = line.strip()
        if not s:
            continue
        if s.startswith("**FI ") or s.startswith("**EN ") or s.startswith("#### ") or s.startswith("---"):
            break
        out.append(s)
    return out


def clean_text(s: str) -> str:
    s = s.replace("**", "").strip()
    if s.startswith("- "):
        s = s[2:].strip()
    s = re.sub(r"\s+", " ", s)
    return s


def en_text_for_entry(e: Entry) -> str:
    lines = e.lines
    eid = e.id
    if eid.startswith("SPK-DIA-"):
        context = extract_block_lines(lines, "EN Context")
        full = extract_block_lines(lines, "EN Full sample dialogue")
        your = extract_block_lines(lines, "EN Your lines (full sample answers)")
        parts = []
        if context:
            parts.append(" ".join(clean_text(x) for x in context))
        if full:
            parts.append(" ".join(clean_text(x) for x in full))
        elif your:
            parts.append(" ".join(clean_text(x) for x in your))
        return clean_text(" ".join(parts))

    if eid.startswith("SPK-KER-"):
        sp = extract_block_lines(lines, "EN Sample speech")
        return clean_text(" ".join(sp))

    if eid.startswith("SPK-REA-") or eid.startswith("SPK-MIE-"):
        ctx = extract_block_lines(lines, "EN Context")
        ans = extract_block_lines(lines, "EN Sample answer")
        parts = []
        if ctx:
            parts.append(" ".join(clean_text(x) for x in ctx))
        if ans:
            parts.append(" ".join(clean_text(x) for x in ans))
        return clean_text(" ".join(parts))

    if eid.startswith("WRT-"):
        txt = extract_block_lines(lines, "EN Sample text")
        return clean_text(" ".join(txt))

    # Fallback
    for label in ("EN Sample answer", "EN Sample speech", "EN Sample text", "EN Full sample dialogue", "EN Context"):
        b = extract_block_lines(lines, label)
        if b:
            return clean_text(" ".join(b))
    return ""


def fi_text_for_entry(e: Entry) -> str:
    lines = e.lines
    eid = e.id
    if eid.startswith("SPK-DIA-"):
        context = extract_block_lines(lines, "FI Konteksti")
        full = extract_block_lines(lines, "FI Koko mallidialogi (täysi)")
        your = extract_block_lines(lines, "FI Sinun repliikit (täydet mallivastaukset)")
        parts = []
        if context:
            parts.append(" ".join(clean_text(x) for x in context))
        if full:
            parts.append(" ".join(clean_text(x) for x in full))
        elif your:
            parts.append(" ".join(clean_text(x) for x in your))
        return clean_text(" ".join(parts))

    if eid.startswith("SPK-KER-"):
        sp = extract_block_lines(lines, "FI Mallipuhe")
        return clean_text(" ".join(sp))

    if eid.startswith("SPK-REA-") or eid.startswith("SPK-MIE-"):
        ctx = extract_block_lines(lines, "FI Konteksti")
        ans = extract_block_lines(lines, "FI Mallivastaus")
        parts = []
        if ctx:
            parts.append(" ".join(clean_text(x) for x in ctx))
        if ans:
            parts.append(" ".join(clean_text(x) for x in ans))
        return clean_text(" ".join(parts))

    if eid.startswith("WRT-"):
        txt = extract_block_lines(lines, "FI Malliteksti")
        return clean_text(" ".join(txt))

    for label in ("FI Mallivastaus", "FI Mallipuhe", "FI Malliteksti", "FI Koko mallidialogi (täysi)", "FI Konteksti"):
        b = extract_block_lines(lines, label)
        if b:
            return clean_text(" ".join(b))
    return ""


def parse_role_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        s = clean_text(line)
        # Accept both FI and EN role labels (e.g., "Ystävä:", "Sinä:", "Friend:", "You:", "Officer:")
        if ROLE_LABEL_LINE_RE.match(s):
            out.append(s)
    return out


def dialog_segments_for_entry(e: Entry) -> list[tuple[str, str]]:
    fi_ctx = " ".join(clean_text(x) for x in extract_block_lines(e.lines, "FI Konteksti")).strip()
    en_ctx = " ".join(clean_text(x) for x in extract_block_lines(e.lines, "EN Context")).strip()
    fi_lines = parse_role_lines(extract_block_lines(e.lines, "FI Koko mallidialogi (täysi)"))
    en_lines = parse_role_lines(extract_block_lines(e.lines, "EN Full sample dialogue"))
    segs: list[tuple[str, str]] = []
    if fi_ctx:
        segs.append((fi_ctx, en_ctx))
    if fi_lines and en_lines:
        m = min(len(fi_lines), len(en_lines))
        for i in range(m):
            segs.append((fi_lines[i], en_lines[i]))
    return segs


def load_manifest_intervals(manifest_path: Path) -> list[ManifestInterval]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = data.get("items", [])
    intervals: list[ManifestInterval] = []
    t = 0.0
    for item in items:
        eid = str(item["id"])
        text_fi = clean_text(str(item.get("text_fi", "")))
        spoken_raw = item.get("spoken_segments", [])
        spoken_segments: list[dict] = []
        if isinstance(spoken_raw, list):
            for seg in spoken_raw:
                if not isinstance(seg, dict):
                    continue
                txt = clean_text(str(seg.get("text_fi", "")))
                try:
                    s = float(seg.get("start_sec", 0.0))
                    e = float(seg.get("end_sec", 0.0))
                except (TypeError, ValueError):
                    continue
                if not txt:
                    continue
                if e <= s:
                    continue
                spoken_segments.append(
                    {
                        "text_fi": txt,
                        "role": str(seg.get("role", "")),
                        "start_sec": s,
                        "end_sec": e,
                        "duration_sec": max(0.0, e - s),
                    }
                )
        d = float(item.get("duration_sec", 0.0))
        start = t
        end = t + max(0.0, d)
        intervals.append(
            ManifestInterval(
                id=eid,
                start=start,
                end=end,
                text_fi=text_fi,
                spoken_segments=spoken_segments,
            )
        )
        t = end
    return intervals


def cue_id_assignments(cues: list[list[dict]], intervals: list[ManifestInterval]) -> list[str]:
    out: list[str] = []
    if not intervals:
        return out
    j = 0
    for cue in cues:
        s = float(cue[0]["start"])
        while j + 1 < len(intervals) and s >= intervals[j].end - 1e-6:
            j += 1
        out.append(intervals[j].id)
    return out


def words_for_interval(words: list[dict], start: float, end: float) -> list[dict]:
    if end <= start:
        return []

    out: list[dict] = []
    for w in words:
        ws = float(w["start"])
        we = float(w["end"])
        mid = (ws + we) * 0.5
        if mid < start or mid >= end:
            continue
        cs = max(start, ws)
        ce = min(end, we)
        if ce <= cs:
            ce = min(end, cs + 0.05)
        if ce <= cs:
            continue
        out.append({"word": str(w["word"]), "start": cs, "end": ce})

    if out:
        return out

    # Fallback for heavy timestamp drift: keep any overlapping words.
    for w in words:
        ws = float(w["start"])
        we = float(w["end"])
        if we <= start or ws >= end:
            continue
        cs = max(start, ws)
        ce = min(end, we)
        if ce <= cs:
            ce = min(end, cs + 0.05)
        if ce <= cs:
            continue
        out.append({"word": str(w["word"]), "start": cs, "end": ce})
    return out


def clamp_cue_to_interval(cue: list[dict], start: float, end: float) -> list[dict]:
    if end <= start:
        return []
    cleaned: list[dict] = []
    prev_end = start
    for w in cue:
        ws = max(start, float(w["start"]))
        we = min(end, float(w["end"]))
        ws = max(ws, prev_end)
        if we <= ws:
            we = min(end, ws + 0.05)
        if we <= ws:
            continue
        cleaned.append({"word": str(w["word"]), "start": ws, "end": we})
        prev_end = we
    return cleaned


def build_manifest_anchored_cues(
    tkv,
    words: list[dict],
    intervals: list[ManifestInterval],
    *,
    mode: str,
) -> tuple[list[list[dict]], list[str]]:
    cues: list[list[dict]] = []
    cue_ids: list[str] = []
    for iv in intervals:
        iv_words = words_for_interval(words, iv.start, iv.end)
        if not iv_words:
            continue
        iv_cues = tkv.build_cues(iv_words, mode=mode)
        for cue in iv_cues:
            clamped = clamp_cue_to_interval(cue, iv.start, iv.end)
            if not clamped:
                continue
            cues.append(clamped)
            cue_ids.append(iv.id)
    return cues, cue_ids


def is_punct_token(tok: str) -> bool:
    return bool(re.fullmatch(r"[.,!?;:)]", tok))


def smart_tokens(text: str) -> list[str]:
    cleaned = clean_text(text)
    if not cleaned:
        return []
    return re.findall(r"[^\W\d_]+(?:[’'\-][^\W\d_]+)*|\d+|[.,!?;:)]", cleaned, flags=re.UNICODE)


def join_tokens(tokens: list[str]) -> str:
    out = ""
    for i, tok in enumerate(tokens):
        if i > 0 and not is_punct_token(tok):
            out += " "
        out += tok
    return out.strip()


def count_word_tokens(tokens: list[str]) -> int:
    return sum(1 for t in tokens if not is_punct_token(t))


def split_tokens_max_words(tokens: list[str], *, max_words: int) -> list[list[str]]:
    if not tokens:
        return []
    out: list[list[str]] = []
    cur: list[str] = []
    words = 0
    for tok in tokens:
        if is_punct_token(tok):
            if cur:
                cur.append(tok)
            continue
        if cur and words >= max_words:
            out.append(cur)
            cur = []
            words = 0
        cur.append(tok)
        words += 1
    if cur:
        out.append(cur)

    # Avoid a tiny trailing cue if possible.
    if len(out) >= 2 and count_word_tokens(out[-1]) <= 2:
        merged = out[-2] + out[-1]
        if count_word_tokens(merged) <= max_words + 3:
            out[-2] = merged
            out.pop()
    return out


def split_text_to_fi_cues(text: str, *, max_words: int = 10) -> list[str]:
    tokens = smart_tokens(text)
    if not tokens:
        return []
    chunks = split_tokens_max_words(tokens, max_words=max_words)
    return [join_tokens(c) for c in chunks if c]


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def split_text_preserve_words(text: str, *, max_words: int = 10) -> list[str]:
    src = normalize_spaces(text)
    if not src:
        return []
    words = src.split(" ")
    out: list[list[str]] = []
    for i in range(0, len(words), max_words):
        out.append(words[i : i + max_words])

    if len(out) >= 2 and len(out[-1]) <= 2 and len(out[-2]) + len(out[-1]) <= max_words + 3:
        out[-2].extend(out[-1])
        out.pop()
    return [" ".join(chunk) for chunk in out if chunk]


def split_text_to_sentence_cues(text: str, *, max_words: int = 12) -> list[str]:
    src = normalize_spaces(text)
    if not src:
        return []
    sent_parts = [s.strip() for s in re.split(r"(?<=[.!?])\s+", src) if s.strip()]
    if not sent_parts:
        sent_parts = [src]

    out: list[str] = []
    for sent in sent_parts:
        words = sent.split(" ")
        if len(words) <= max_words:
            out.append(sent)
            continue
        out.extend(split_text_preserve_words(sent, max_words=max_words))
    return out


def split_tts_sentences(text: str) -> list[str]:
    src = normalize_spaces(text)
    if not src:
        return []
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", src) if p.strip()]
    return parts or [src]


def translation_key(text: str) -> str:
    s = clean_text(text)
    s = s.replace("’", "'").replace("`", "'")
    s = s.replace("…", " ")
    s = s.replace("—", "-").replace("–", "-")
    s = s.replace("/", " ")
    s = s.replace("\\", " ")
    s = s.lower()
    s = re.sub(r"[\"“”]", "", s)
    s = re.sub(r"[.,!?;:()\[\]{}]", " ", s)
    s = s.replace("-", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def add_translation_pair(store: dict[str, str], fi_text: str, en_text: str) -> None:
    fi = normalize_spaces(fi_text)
    en = normalize_spaces(en_text)
    if not fi or not en:
        return
    key = translation_key(fi)
    if not key or key in store:
        return
    store[key] = en


def add_sentence_pairs_if_aligned(store: dict[str, str], fi_text: str, en_text: str) -> None:
    fi_sents = split_tts_sentences(fi_text)
    en_sents = split_tts_sentences(en_text)
    if len(fi_sents) != len(en_sents):
        return
    for fi, en in zip(fi_sents, en_sents):
        add_translation_pair(store, fi, en)


def split_text_into_weighted_chunks(text: str, count: int, weights: list[int]) -> list[str]:
    src = normalize_spaces(text)
    if count <= 0:
        return []
    if not src:
        return ["" for _ in range(count)]
    if count == 1:
        return [src]

    words = src.split(" ")
    n = len(words)
    if n == 0:
        return ["" for _ in range(count)]

    norm_w = [max(1, int(w)) for w in weights[:count]]
    if len(norm_w) < count:
        norm_w.extend([1] * (count - len(norm_w)))
    total_w = sum(norm_w) or count

    out: list[str] = []
    start = 0
    consumed_w = 0
    for i in range(count):
        if i == count - 1:
            end = n
        else:
            consumed_w += norm_w[i]
            target = round((consumed_w / total_w) * n)
            # Keep one token for each remaining chunk.
            min_end = start + 1
            max_end = n - (count - i - 1)
            if max_end < min_end:
                min_end = max_end
            end = max(min_end, min(max_end, target))
        chunk = " ".join(words[start:end]).strip()
        out.append(chunk)
        start = end

    # Ensure no empty English chunks for cue contract.
    for i, chunk in enumerate(out):
        if chunk:
            continue
        if i > 0 and out[i - 1]:
            out[i] = out[i - 1].split(" ")[-1]
        elif i + 1 < len(out) and out[i + 1]:
            out[i] = out[i + 1].split(" ")[0]
        else:
            out[i] = src
    return out


def add_sentence_pairs_partitioned(store: dict[str, str], fi_text: str, en_text: str) -> None:
    fi_sents = split_tts_sentences(fi_text)
    if not fi_sents:
        return
    en_sents = split_tts_sentences(en_text)
    if len(fi_sents) == len(en_sents):
        for fi, en in zip(fi_sents, en_sents):
            add_translation_pair(store, fi, en)
        return

    weights = [max(1, count_word_tokens(smart_tokens(s))) for s in fi_sents]
    en_chunks = split_text_into_weighted_chunks(en_text, len(fi_sents), weights)
    for fi, en in zip(fi_sents, en_chunks):
        add_translation_pair(store, fi, en)


def build_translation_lookups(fi_en_package: Path) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    entries = split_entries(read_lines(fi_en_package))
    by_id: dict[str, dict[str, str]] = {}
    global_map: dict[str, str] = {}

    for e in entries:
        entry_map: dict[str, str] = {}

        fi_full = fi_text_for_entry(e)
        en_full = en_text_for_entry(e)
        add_translation_pair(entry_map, fi_full, en_full)
        # For dialogues, use per-turn alignment below to avoid noisy pairs from
        # the whole-dialogue blob.
        if not e.id.startswith("SPK-DIA-"):
            add_sentence_pairs_partitioned(entry_map, fi_full, en_full)

        if e.id.startswith("SPK-DIA-"):
            for fi_seg, en_seg in dialog_segments_for_entry(e):
                fi_line = strip_leading_role_label(fi_seg)
                en_line = strip_leading_role_label(en_seg)
                add_translation_pair(entry_map, fi_line, en_line)
                add_sentence_pairs_partitioned(entry_map, fi_line, en_line)

        by_id[e.id] = entry_map
        for k, v in entry_map.items():
            if k not in global_map:
                global_map[k] = v

    return by_id, global_map


def build_karaoke_words_for_text(text: str, start: float, end: float) -> list[dict]:
    tokens = smart_tokens(text)
    if not tokens or end <= start:
        return []

    weights: list[float] = []
    for tok in tokens:
        if is_punct_token(tok):
            weights.append(0.25)
        else:
            weights.append(max(1.0, len(tok) / 4.0))
    total = sum(weights) or 1.0
    dur = end - start

    out: list[dict] = []
    t = start
    for i, tok in enumerate(tokens):
        if i == len(tokens) - 1:
            t_next = end
        else:
            t_next = t + dur * (weights[i] / total)
        if t_next <= t:
            t_next = min(end, t + 0.05)
        if t_next <= t:
            continue
        out.append({"word": tok, "start": t, "end": t_next})
        t = t_next
    return out


def proportional_spans_for_interval(iv: ManifestInterval, cue_texts: list[str]) -> list[tuple[float, float]]:
    if not cue_texts:
        return []
    weights = [max(1, count_word_tokens(smart_tokens(t))) for t in cue_texts]
    total_w = sum(weights) or 1
    span = iv.end - iv.start
    out: list[tuple[float, float]] = []
    acc = 0
    for idx, w in enumerate(weights):
        s = iv.start + span * (acc / total_w)
        acc += w
        e = iv.start + span * (acc / total_w)
        if idx == len(weights) - 1:
            e = iv.end
        out.append((s, e))
    return out


def strip_leading_role_label(text: str) -> str:
    s = normalize_spaces(text)
    return ROLE_LABEL_PREFIX_RE.sub("", s).strip()


def manifest_spoken_spans(iv: ManifestInterval) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for seg in iv.spoken_segments:
        try:
            rs = float(seg.get("start_sec", 0.0))
            re_ = float(seg.get("end_sec", 0.0))
        except (TypeError, ValueError):
            continue
        if re_ <= rs:
            continue
        s = iv.start + rs
        e = iv.start + re_
        s = max(iv.start, min(iv.end, s))
        e = max(iv.start, min(iv.end, e))
        if e <= s:
            continue
        out.append((s, e))
    return out


def build_text_timed_cues_from_manifest(
    intervals: list[ManifestInterval],
) -> tuple[list[list[dict]], list[str]]:
    cues: list[list[dict]] = []
    cue_ids: list[str] = []

    for iv in intervals:
        if iv.end <= iv.start:
            continue

        fi_segs = [normalize_spaces(str(seg.get("text_fi", ""))) for seg in iv.spoken_segments]
        fi_segs = [s for s in fi_segs if s]
        if not fi_segs:
            fi_text = normalize_spaces(iv.text_fi)
            if fi_text:
                fi_segs = split_text_to_sentence_cues(fi_text, max_words=12)
            if not fi_segs and fi_text:
                fi_segs = [fi_text]
        if not fi_segs:
            continue

        spans = manifest_spoken_spans(iv)
        if len(spans) != len(fi_segs):
            spans = proportional_spans_for_interval(iv, fi_segs)

        for idx, fi_text in enumerate(fi_segs):
            start, end = spans[idx]
            words = build_karaoke_words_for_text(fi_text, start, end)
            if not words:
                continue
            cues.append(words)
            cue_ids.append(iv.id)

    return cues, cue_ids


def translate_single_line_deepl(
    *,
    line_fi: str,
    deepl_api_key: str,
    retries: int = 3,
) -> str:
    endpoints = ["https://api-free.deepl.com/v2/translate", "https://api.deepl.com/v2/translate"]
    last_err: Exception | None = None

    for endpoint in endpoints:
        for attempt in range(1, max(1, retries) + 1):
            form: dict[str, str] = {
                "auth_key": deepl_api_key,
                "text": line_fi,
                "source_lang": "FI",
                "target_lang": "EN",
            }
            data = urllib.parse.urlencode(form).encode("utf-8")
            req = urllib.request.Request(
                url=endpoint,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read().decode("utf-8")
                parsed = json.loads(raw)
                items = parsed.get("translations", [])
                if isinstance(items, list) and items and isinstance(items[0], dict):
                    text = str(items[0].get("text", "")).strip()
                    if text:
                        return text
                raise RuntimeError("DeepL response missing translations[0].text")
            except Exception as exc:
                last_err = exc
                if attempt < retries:
                    time.sleep(min(2 ** attempt, 8))
        # Try next endpoint if this one fails.
    raise RuntimeError(f"DeepL translation failed: {last_err}")


def build_strict_english_translations(
    tkv,
    *,
    cues: list[list[dict]],
    cue_ids: list[str],
    translations_by_id: dict[str, dict[str, str]],
    global_translations: dict[str, str],
    api_key: str | None,
    deepl_api_key: str | None,
    model: str,
    allow_api_fallback: bool = True,
) -> list[str]:
    if not cues:
        return []

    out: list[str] = []
    cache: dict[str, str] = {}
    warned_missing_key = False

    for i, cue in enumerate(cues):
        fi_line = cue_plain_text(cue).strip()
        if not fi_line:
            out.append("")
            continue

        eid = cue_ids[i] if i < len(cue_ids) else ""
        key = translation_key(fi_line)

        en_line = ""
        if key and eid:
            en_line = translations_by_id.get(eid, {}).get(key, "")
        if not en_line and key:
            en_line = global_translations.get(key, "")

        if not en_line:
            if key and key in cache:
                en_line = cache[key]
            elif allow_api_fallback and (deepl_api_key or api_key):
                if deepl_api_key:
                    try:
                        en_line = translate_single_line_deepl(
                            line_fi=fi_line,
                            deepl_api_key=deepl_api_key,
                        )
                    except Exception as exc:
                        print(f"Warning: DeepL could not translate cue {i+1}: {exc}")
                        en_line = ""
                if not en_line and api_key:
                    try:
                        en_line = tkv.translate_single_line_to_english(
                            line=fi_line,
                            api_key=api_key,
                            model=model,
                        )
                    except Exception as exc:
                        print(f"Warning: OpenAI could not translate cue {i+1}: {exc}")
                        en_line = ""
                if key:
                    cache[key] = en_line
            elif not warned_missing_key:
                if allow_api_fallback:
                    print(
                        "Warning: neither DEEPL_API_KEY nor OPENAI_API_KEY is set; "
                        "unmatched cues will have empty English subtitles."
                    )
                else:
                    print("Warning: API fallback translation is disabled; unmatched cues will be empty.")
                warned_missing_key = True

        out.append(clean_text(en_line))

    return out


def expected_tts_text_cue_count(intervals: list[ManifestInterval]) -> int:
    total = 0
    for iv in intervals:
        fi_segs = [normalize_spaces(str(seg.get("text_fi", ""))) for seg in iv.spoken_segments]
        fi_segs = [s for s in fi_segs if s]
        if fi_segs:
            total += len(fi_segs)
            continue
        fi_text = normalize_spaces(iv.text_fi)
        if not fi_text:
            continue
        split = split_text_to_sentence_cues(fi_text, max_words=12)
        total += len(split) if split else 1
    return total


def assert_cue_contract(
    cues: list[list[dict]],
    cue_ids: list[str],
    en_translations: list[str],
    *,
    timing_source: str,
    intervals: list[ManifestInterval],
) -> None:
    if len(cues) != len(cue_ids):
        raise RuntimeError(
            f"cue contract violation: cues ({len(cues)}) != cue_ids ({len(cue_ids)})"
        )
    if len(cues) != len(en_translations):
        raise RuntimeError(
            f"cue contract violation: cues ({len(cues)}) != translations ({len(en_translations)})"
        )

    for i, cue in enumerate(cues):
        if not cue:
            raise RuntimeError(f"cue contract violation: empty cue at index {i}")
        start = float(cue[0]["start"])
        end = float(cue[-1]["end"])
        if end <= start:
            raise RuntimeError(
                f"cue contract violation: non-positive cue duration at index {i} ({start}..{end})"
            )

    if timing_source == "tts_text":
        expected = expected_tts_text_cue_count(intervals)
        if len(cues) != expected:
            raise RuntimeError(
                "cue contract violation: tts_text cue count mismatch "
                f"(expected {expected}, got {len(cues)})"
            )


def assert_translation_coverage(
    cues: list[list[dict]],
    cue_ids: list[str],
    en_translations: list[str],
) -> None:
    missing: list[tuple[int, str, str]] = []
    for i, cue in enumerate(cues):
        en = en_translations[i].strip() if i < len(en_translations) else ""
        if en:
            continue
        fi = cue_plain_text(cue).strip()
        eid = cue_ids[i] if i < len(cue_ids) else ""
        missing.append((i, eid, fi))

    if not missing:
        return

    samples = "\n".join(
        f"  - cue#{idx + 1} [{eid}] FI='{fi}'" for idx, eid, fi in missing[:12]
    )
    raise RuntimeError(
        "translation coverage failure: missing English subtitle lines for "
        f"{len(missing)}/{len(cues)} cues.\n"
        "Examples:\n"
        f"{samples}"
    )


def cue_plain_text(cue: list[dict]) -> str:
    text = ""
    for i, w in enumerate(cue):
        token = str(w["word"])
        if i > 0 and not re.fullmatch(r"[.,!?;:)]", token):
            text += " "
        text += token
    return text.strip()


def setup_ffmpeg_bins(tkv) -> None:
    ffmpeg_full = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
    ffprobe_full = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffprobe")
    if ffmpeg_full.exists() and ffprobe_full.exists():
        tkv.FFMPEG_BIN = ffmpeg_full
        tkv.FFPROBE_BIN = ffprobe_full
    else:
        tkv.require_cmd("ffmpeg")
        tkv.require_cmd("ffprobe")
        tkv.FFMPEG_BIN = Path(shutil.which("ffmpeg") or "ffmpeg")
        tkv.FFPROBE_BIN = Path(shutil.which("ffprobe") or "ffprobe")


def transcribe_or_load_words(
    tkv,
    *,
    input_audio: Path,
    words_json_path: Path,
    api_key: str | None,
    model: str,
    language: str | None,
    chunk_seconds: int,
    force_transcribe: bool,
) -> list[dict]:
    cached_words = None if force_transcribe else tkv.load_cached_words(words_json_path)
    if cached_words:
        print(f"Using cached transcript words: {words_json_path}")
        return cached_words

    tkv.require_cmd("curl")
    if not api_key:
        tkv.fail("OPENAI_API_KEY is not set (or provide cached *.words.json)")
    tkv.ensure_openai_host_resolves()

    print(f"Transcription language: {language if language else 'auto'}")
    print("Splitting audio...")
    all_words: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="karaoke-work-") as tmp:
        tmp_dir = Path(tmp)
        chunks = tkv.split_audio(input_audio, tmp_dir / "chunks", chunk_seconds)
        total = len(chunks)
        for idx, (chunk, offset) in enumerate(chunks, start=1):
            print(f"Transcribing chunk {idx}/{total}: {chunk.name}")
            words = tkv.transcribe_chunk(
                chunk_path=chunk,
                offset=offset,
                api_key=api_key,
                model=model,
                language=language,
            )
            all_words.extend(words)
    if not all_words:
        tkv.fail("no words returned from transcription")
    all_words.sort(key=lambda w: float(w["start"]))
    return all_words


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render dual-language karaoke video using package EN translations (no re-translation)."
    )
    parser.add_argument("--audio", required=True)
    parser.add_argument("--output-prefix", required=True)
    parser.add_argument("--fi-en-package", required=True)
    parser.add_argument("--section-manifest", required=True, help="manifest.json from per-ID section compact audio")
    parser.add_argument("--language", default="fi")
    parser.add_argument("--model", default="whisper-1")
    parser.add_argument("--translation-model", default="gpt-4o-mini")
    parser.add_argument("--split-mode", choices=("semantic", "timing"), default="semantic")
    parser.add_argument(
        "--timing-source",
        choices=("tts_text", "asr"),
        default="tts_text",
        help="tts_text: cues and timing from TTS text/manifest segments. asr: cues from ASR words.",
    )
    parser.add_argument(
        "--cue-anchor",
        choices=("manifest", "asr"),
        default="manifest",
        help="Used when timing-source=asr. manifest keeps cues inside per-ID TTS windows.",
    )
    parser.add_argument("--chunk-seconds", type=int, default=540)
    parser.add_argument("--force-transcribe", action="store_true")
    parser.add_argument(
        "--allow-api-fallback",
        action="store_true",
        help="Allow translating unmatched cues via API (disabled by default).",
    )
    parser.add_argument(
        "--allow-missing-english",
        action="store_true",
        help="Allow empty English subtitle lines (disabled by default).",
    )
    args = parser.parse_args()

    script_path = Path("scripts/transcribe_karaoke_video.py")
    tkv = load_tkv_module(script_path)
    setup_ffmpeg_bins(tkv)

    input_audio = Path(args.audio).expanduser().resolve()
    out_base = Path(args.output_prefix).expanduser().resolve()
    fi_en_package = Path(args.fi_en_package).expanduser().resolve()
    section_manifest = Path(args.section_manifest).expanduser().resolve()

    transcript_path = out_base.with_suffix(".transcript.txt")
    words_json_path = out_base.with_suffix(".words.json")
    translation_path = out_base.with_suffix(".translation.en.json")
    ass_path = out_base.with_suffix(".karaoke.ass")
    mp4_path = out_base.with_suffix(".karaoke.mp4")

    print(f"Input: {input_audio}")
    api_key = os.environ.get("OPENAI_API_KEY")
    deepl_api_key = os.environ.get("DEEPL_API_KEY")
    allow_api_fallback = bool(args.allow_api_fallback)
    language = args.language.strip().lower()
    if language in {"", "auto", "none", "detect"}:
        language = None

    translations_by_id, global_translations = build_translation_lookups(fi_en_package)
    intervals = load_manifest_intervals(section_manifest)
    if args.timing_source == "tts_text":
        cues, cue_ids = build_text_timed_cues_from_manifest(intervals)
        all_words = [w for cue in cues for w in cue]
    else:
        all_words = transcribe_or_load_words(
            tkv,
            input_audio=input_audio,
            words_json_path=words_json_path,
            api_key=api_key,
            model=args.model,
            language=language,
            chunk_seconds=args.chunk_seconds,
            force_transcribe=args.force_transcribe,
        )
        if args.cue_anchor == "manifest":
            cues, cue_ids = build_manifest_anchored_cues(
                tkv,
                all_words,
                intervals,
                mode=args.split_mode,
            )
        else:
            cues = tkv.build_cues(all_words, mode=args.split_mode)
            cue_ids = cue_id_assignments(cues, intervals)

    en_translations = build_strict_english_translations(
        tkv,
        cues=cues,
        cue_ids=cue_ids,
        translations_by_id=translations_by_id,
        global_translations=global_translations,
        api_key=api_key,
        deepl_api_key=deepl_api_key,
        model=args.translation_model,
        allow_api_fallback=allow_api_fallback,
    )

    if not cues:
        tkv.fail("no cues generated")

    assert_cue_contract(
        cues,
        cue_ids,
        en_translations,
        timing_source=args.timing_source,
        intervals=intervals,
    )
    if not args.allow_missing_english:
        assert_translation_coverage(cues, cue_ids, en_translations)

    tkv.write_transcript(all_words, transcript_path)
    words_json_path.write_text(json.dumps(all_words, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    cue_texts = [tkv.cue_plain_text(c) for c in cues]

    translation_path.write_text(
        json.dumps(
            {
                "mode": "package_alignment_text_timing" if args.timing_source == "tts_text" else "package_alignment",
                "timing_source": args.timing_source,
                "cue_anchor": args.cue_anchor,
                "fi_en_package": str(fi_en_package),
                "section_manifest": str(section_manifest),
                "sources": cue_texts,
                "cue_ids": cue_ids,
                "translations": en_translations,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    tkv.write_ass(cues, ass_path, english_translations=en_translations)
    duration = tkv.probe_duration(input_audio)
    print("Rendering vertical video...")
    tkv.render_video(input_audio, ass_path, mp4_path, duration)

    print("Done")
    print(f"Transcript: {transcript_path}")
    print(f"Words JSON: {words_json_path}")
    print(f"English translation cache: {translation_path}")
    print(f"ASS subtitles: {ass_path}")
    print(f"Video: {mp4_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
