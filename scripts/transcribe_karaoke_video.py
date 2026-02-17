#!/usr/bin/env python3
"""
Transcribe an audio file with word timestamps and render a vertical karaoke-style video.

Requirements:
- OPENAI_API_KEY environment variable
- ffmpeg, ffprobe, curl, python3
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Iterable

FFMPEG_BIN = None
FFPROBE_BIN = None


def fail(msg: str) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def require_cmd(name: str) -> None:
    if shutil.which(name) is None:
        fail(f"missing required command: {name}")


def run(cmd: list[str], *, capture: bool = False) -> str:
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=True,
    )
    if proc.returncode != 0:
        if capture:
            err = proc.stderr.strip() or proc.stdout.strip() or "(no details)"
            fail(f"command failed: {' '.join(cmd)}\n{err}")
        fail(f"command failed: {' '.join(cmd)}")
    return proc.stdout if capture and proc.stdout is not None else ""


def probe_duration(path: Path) -> float:
    out = run(
        [
            str(FFPROBE_BIN),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nokey=1:noprint_wrappers=1",
            str(path),
        ],
        capture=True,
    ).strip()
    try:
        duration = float(out)
    except ValueError as exc:
        raise RuntimeError(f"could not parse duration for {path}: {out}") from exc
    return max(duration, 0.0)


def split_audio(input_audio: Path, chunks_dir: Path, chunk_seconds: int) -> list[tuple[Path, float]]:
    chunks_dir.mkdir(parents=True, exist_ok=True)
    pattern = chunks_dir / "chunk_%03d.mp3"
    run(
        [
            str(FFMPEG_BIN),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(input_audio),
            "-f",
            "segment",
            "-segment_time",
            str(chunk_seconds),
            "-vn",
            "-ac",
            "1",
            "-c:a",
            "libmp3lame",
            "-q:a",
            "4",
            str(pattern),
        ]
    )

    chunk_files = sorted(chunks_dir.glob("chunk_*.mp3"))
    if not chunk_files:
        fail("audio split produced zero chunks")

    result: list[tuple[Path, float]] = []
    offset = 0.0
    for chunk in chunk_files:
        result.append((chunk, offset))
        offset += probe_duration(chunk)
    return result


def transcribe_chunk(
    *,
    chunk_path: Path,
    offset: float,
    api_key: str,
    model: str,
    language: str | None,
) -> list[dict[str, float | str]]:
    cmd = [
        "curl",
        "-sS",
        "--retry",
        "5",
        "--retry-all-errors",
        "--retry-delay",
        "2",
        "--connect-timeout",
        "15",
        "--max-time",
        "600",
        "-X",
        "POST",
        "https://api.openai.com/v1/audio/transcriptions",
        "-H",
        f"Authorization: Bearer {api_key}",
        "-F",
        f"file=@{chunk_path}",
        "-F",
        f"model={model}",
        "-F",
        "response_format=verbose_json",
        "-F",
        "timestamp_granularities[]=word",
    ]
    if language:
        cmd += ["-F", f"language={language}"]

    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        if proc.returncode == 6 or "Could not resolve host" in err:
            fail(
                "Could not resolve api.openai.com (DNS/network issue). "
                "Check connection/VPN/DNS and retry."
            )
        fail(f"transcription request failed (curl exit {proc.returncode}): {err or '(no details)'}")

    raw = proc.stdout
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON from API for {chunk_path.name}") from exc

    if isinstance(data, dict) and "error" in data:
        err = data["error"]
        if isinstance(err, dict):
            message = err.get("message", str(err))
        else:
            message = str(err)
        raise RuntimeError(f"API error for {chunk_path.name}: {message}")

    words = data.get("words") if isinstance(data, dict) else None
    if not isinstance(words, list):
        raise RuntimeError(
            "API response did not include word timestamps. "
            "Use model whisper-1 with response_format=verbose_json."
        )

    out: list[dict[str, float | str]] = []
    for item in words:
        if not isinstance(item, dict):
            continue
        token = str(item.get("word", "")).strip()
        if not token:
            continue
        try:
            start = float(item["start"]) + offset
            end = float(item["end"]) + offset
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            end = start + 0.05
        out.append({"word": token, "start": start, "end": end})
    return out


def load_cached_words(words_json_path: Path) -> list[dict[str, float | str]] | None:
    if not words_json_path.exists():
        return None

    try:
        data = json.loads(words_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(data, list):
        return None

    words: list[dict[str, float | str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        token = str(item.get("word", "")).strip()
        if not token:
            continue
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            end = start + 0.05
        words.append({"word": token, "start": start, "end": end})

    if not words:
        return None

    words.sort(key=lambda w: float(w["start"]))
    return words


def format_ass_time(seconds: float) -> str:
    t = max(0.0, seconds)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    cs = int(round((t - math.floor(t)) * 100))
    if cs >= 100:
        cs = 0
        s += 1
    if s >= 60:
        s = 0
        m += 1
    if m >= 60:
        m = 0
        h += 1
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def escape_ass(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def is_punct(tok: str) -> bool:
    return bool(re.fullmatch(r"[.,!?;:)]", tok))


def ends_sentence(tok: str) -> bool:
    return bool(re.search(r"[.!?][\"')\]]*$", tok))


def normalize_token(tok: str) -> str:
    return re.sub(r"(^[^\w]+|[^\w]+$)", "", tok.lower())


SEMANTIC_LEADING_BREAK_WORDS = {
    # Finnish
    "mutta",
    "että",
    "koska",
    "joten",
    "jos",
    "kun",
    "vaikka",
    "siksi",
    # English
    "but",
    "because",
    "so",
    "if",
    "when",
    "although",
    "however",
    "therefore",
}


def cue_span_seconds(cue: list[dict[str, float | str]]) -> float:
    if not cue:
        return 0.0
    return float(cue[-1]["end"]) - float(cue[0]["start"])


def merge_short_cues(
    cues: list[list[dict[str, float | str]]],
    *,
    max_words: int,
    max_span: float,
    min_words: int = 3,
    min_span: float = 1.0,
) -> list[list[dict[str, float | str]]]:
    merged: list[list[dict[str, float | str]]] = []
    for cue in cues:
        span = cue_span_seconds(cue)
        if merged and (len(cue) < min_words or span < min_span):
            prev = merged[-1]
            combined = prev + cue
            if len(combined) <= max_words + 4 and cue_span_seconds(combined) <= max_span + 1.5:
                merged[-1] = combined
                continue
        merged.append(cue)
    return merged


def build_cues_timing(
    words: list[dict[str, float | str]],
    *,
    max_words: int = 12,
    max_span: float = 5.0,
    max_gap: float = 0.7,
) -> list[list[dict[str, float | str]]]:
    cues: list[list[dict[str, float | str]]] = []
    current: list[dict[str, float | str]] = []

    for w in words:
        if not current:
            current = [w]
            continue

        prev_token = str(current[-1]["word"])
        gap = float(w["start"]) - float(current[-1]["end"])
        span = float(w["end"]) - float(current[0]["start"])
        sentence_break = ends_sentence(prev_token) and len(current) >= 5 and span >= 2.0 and gap >= 0.35
        long_block_break = span > 4.2 and len(current) >= 9

        if sentence_break or long_block_break or len(current) >= max_words or span > max_span or gap > max_gap:
            cues.append(current)
            current = [w]
        else:
            current.append(w)

    if current:
        cues.append(current)

    return merge_short_cues(cues, max_words=max_words, max_span=max_span)


def build_cues_semantic(
    words: list[dict[str, float | str]],
    *,
    max_words: int = 14,
    max_span: float = 6.0,
    max_gap: float = 1.4,
) -> list[list[dict[str, float | str]]]:
    cues: list[list[dict[str, float | str]]] = []
    current: list[dict[str, float | str]] = []

    for w in words:
        tok = str(w["word"])
        tok_norm = normalize_token(tok)

        if not current:
            current = [w]
            continue

        span = float(current[-1]["end"]) - float(current[0]["start"])
        gap = float(w["start"]) - float(current[-1]["end"])

        # Break before strong clause starters if current block is already substantial.
        if (
            tok_norm in SEMANTIC_LEADING_BREAK_WORDS
            and len(current) >= 6
            and span >= 2.2
            and gap >= 0.08
        ):
            cues.append(current)
            current = [w]
            continue

        # Respect a larger speech pause as a cue boundary.
        if gap >= max_gap and (len(current) >= 3 or span >= 1.2):
            cues.append(current)
            current = [w]
            continue

        current.append(w)
        span = float(current[-1]["end"]) - float(current[0]["start"])
        n = len(current)

        tail = str(current[-1]["word"])
        comma_pause = tail.endswith(",") and n >= 6 and span >= 2.4
        sentence_break = ends_sentence(tail) and (n >= 5 or span >= 2.2)
        long_block_break = n >= 12 and span >= 5.8

        if (
            sentence_break
            or comma_pause
            or long_block_break
            or n >= max_words
            or span >= max_span
        ):
            cues.append(current)
            current = []

    if current:
        cues.append(current)

    return merge_short_cues(
        cues,
        max_words=max_words,
        max_span=max_span,
        min_words=4,
        min_span=1.2,
    )


def build_cues(
    words: list[dict[str, float | str]],
    *,
    mode: str,
) -> list[list[dict[str, float | str]]]:
    if mode == "timing":
        return build_cues_timing(words)
    return build_cues_semantic(words)


def balanced_break_indices(
    tokens: list[str],
    *,
    max_lines: int,
    target_chars_per_line: int,
    hard_max_chars_per_line: int,
) -> tuple[set[int], int, int]:
    if not tokens:
        return set(), 0, 0

    n = len(tokens)
    if n == 1:
        return set(), len(tokens[0]), 1

    token_lens = [len(t) for t in tokens]
    lead_space = [1 if i > 0 and not is_punct(tokens[i]) else 0 for i in range(n)]

    prefix = [0]
    for i in range(n):
        prefix.append(prefix[-1] + token_lens[i] + lead_space[i])

    def line_len(start: int, end: int) -> int:
        # end is exclusive
        return prefix[end] - prefix[start] - lead_space[start]

    total_chars = line_len(0, n)
    desired_lines = int(round(total_chars / max(1, target_chars_per_line)))
    desired_lines = max(1, min(max_lines, n, desired_lines))
    if total_chars > hard_max_chars_per_line and desired_lines == 1:
        desired_lines = 2

    best_score = float("inf")
    best_prev: list[list[int]] | None = None
    best_lines = 1

    min_candidate = max(1, desired_lines - 2)
    max_candidate = min(max_lines, n, desired_lines + 3)

    for line_count in range(min_candidate, max_candidate + 1):
        inf = float("inf")
        dp = [[inf] * (n + 1) for _ in range(line_count + 1)]
        prev = [[-1] * (n + 1) for _ in range(line_count + 1)]
        dp[0][0] = 0.0

        for lc in range(1, line_count + 1):
            for end in range(lc, n + 1):
                start_min = lc - 1
                for start in range(start_min, end):
                    if dp[lc - 1][start] == inf:
                        continue

                    length = line_len(start, end)
                    overflow = max(0, length - hard_max_chars_per_line)
                    shortfall = max(0, 6 - length)
                    line_tokens = end - start

                    cost = float((length - target_chars_per_line) ** 2)
                    cost += float(overflow * overflow * 500)
                    cost += float(shortfall * shortfall * 20)
                    if line_tokens == 1 and n > line_count:
                        cost += 20.0

                    cand = dp[lc - 1][start] + cost
                    if cand < dp[lc][end]:
                        dp[lc][end] = cand
                        prev[lc][end] = start

        final_cost = dp[line_count][n]
        if final_cost == inf:
            continue

        extra_line_penalty = max(0, line_count - desired_lines) * 14.0
        score = final_cost + abs(line_count - desired_lines) * 12.0 + extra_line_penalty
        if score < best_score:
            best_score = score
            best_prev = prev
            best_lines = line_count

    if best_prev is None:
        return set(), total_chars, 1

    breaks: set[int] = set()
    spans: list[tuple[int, int]] = []
    end = n
    for lc in range(best_lines, 0, -1):
        start = best_prev[lc][end]
        if start < 0:
            break
        spans.append((start, end))
        if start > 0:
            breaks.add(start)
        end = start

    max_line_chars = 0
    for start, end in spans:
        max_line_chars = max(max_line_chars, line_len(start, end))

    return breaks, max_line_chars, best_lines


def cue_to_karaoke_text(cue: list[dict[str, float | str]]) -> tuple[str, int]:
    if not cue:
        return "", 86

    tokens = [str(w["word"]) for w in cue]
    total_chars = 0
    for i, token in enumerate(tokens):
        if i > 0 and not is_punct(token):
            total_chars += 1
        total_chars += len(token)

    if total_chars <= 36:
        max_lines = 2
        target_chars_per_line = 18
        hard_max_chars_per_line = 24
    elif total_chars <= 60:
        max_lines = 3
        target_chars_per_line = 19
        hard_max_chars_per_line = 24
    elif total_chars <= 86:
        max_lines = 4
        target_chars_per_line = 20
        hard_max_chars_per_line = 24
    else:
        max_lines = 5
        target_chars_per_line = 21
        hard_max_chars_per_line = 25

    if len(tokens) >= 16:
        max_lines = min(6, max_lines + 1)

    break_points, max_line_chars, line_count = balanced_break_indices(
        tokens,
        max_lines=max_lines,
        target_chars_per_line=target_chars_per_line,
        hard_max_chars_per_line=hard_max_chars_per_line,
    )

    # Width-driven sizing from longest rendered line; line count is only a safety cap.
    if max_line_chars <= 10:
        width_font = 96
    elif max_line_chars <= 12:
        width_font = 92
    elif max_line_chars <= 14:
        width_font = 88
    elif max_line_chars <= 16:
        width_font = 84
    elif max_line_chars <= 18:
        width_font = 80
    elif max_line_chars <= 20:
        width_font = 76
    elif max_line_chars <= 22:
        width_font = 72
    elif max_line_chars <= 24:
        width_font = 68
    elif max_line_chars <= 26:
        width_font = 64
    elif max_line_chars <= 28:
        width_font = 60
    else:
        width_font = 56

    if line_count <= 3:
        line_cap = 96
    elif line_count == 4:
        line_cap = 90
    elif line_count == 5:
        line_cap = 84
    elif line_count == 6:
        line_cap = 78
    elif line_count == 7:
        line_cap = 72
    elif line_count == 8:
        line_cap = 70
    else:
        line_cap = 66

    font_size = max(48, min(width_font, line_cap))

    parts: list[str] = []

    for i, w in enumerate(cue):
        start = float(w["start"])
        nxt = float(cue[i + 1]["start"]) if i + 1 < len(cue) else float(cue[-1]["end"])
        dur_cs = max(1, int(round(max(0.05, nxt - start) * 100)))

        token = str(w["word"])
        prefix = ""
        if i in break_points:
            prefix += r"\N"
        if i > 0 and i not in break_points and not is_punct(token):
            prefix += " "

        parts.append(f"{{\\k{dur_cs}}}{prefix}{escape_ass(token)}")

    return "".join(parts), font_size


def write_ass(
    cues: list[list[dict[str, float | str]]],
    ass_path: Path,
    *,
    english_translations: list[str] | None = None,
) -> None:
    header = textwrap.dedent(
        """\
        [Script Info]
        ScriptType: v4.00+
        PlayResX: 1080
        PlayResY: 1920
        WrapStyle: 2
        ScaledBorderAndShadow: yes

        [V4+ Styles]
        Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
        Style: Karaoke,Arial,86,&H0000D7FF,&H00FFFFFF,&H00101010,&H70000000,1,0,0,0,100,100,0,0,1,4,0,5,90,90,0,1
        Style: Translation,Arial,52,&H00FFFFFF,&H00FFFFFF,&H00101010,&H64000000,0,0,0,0,100,100,0,0,1,3,0,2,90,90,120,1

        [Events]
        Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
        """
    )

    lines = [header]
    for idx, cue in enumerate(cues):
        start = float(cue[0]["start"])
        end = float(cue[-1]["end"]) + 0.05
        text, font_size = cue_to_karaoke_text(cue)
        text = f"{{\\an5\\pos(540,960)\\fs{font_size}}}{text}"
        lines.append(
            f"Dialogue: 0,{format_ass_time(start)},{format_ass_time(end)},Karaoke,,0,0,0,,{text}\n"
        )
        if english_translations and idx < len(english_translations):
            trans, trans_font, trans_lines = format_translation_text(english_translations[idx])
            if trans:
                if trans_lines <= 1:
                    trans_y = 1750
                elif trans_lines == 2:
                    trans_y = 1730
                else:
                    trans_y = 1695
                trans_text = f"{{\\an2\\pos(540,{trans_y})\\fs{trans_font}}}{trans}"
                lines.append(
                    f"Dialogue: 1,{format_ass_time(start)},{format_ass_time(end)},Translation,,0,0,0,,{trans_text}\n"
                )

    ass_path.write_text("".join(lines), encoding="utf-8")


def render_video(audio_path: Path, ass_path: Path, out_path: Path, duration: float) -> None:
    escaped_ass = str(ass_path).replace("\\", "\\\\").replace(":", "\\:").replace("'", r"\'")
    run(
        [
            str(FFMPEG_BIN),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x0f172a:s=1080x1920:r=30:d={duration:.3f}",
            "-i",
            str(audio_path),
            "-vf",
            f"ass='{escaped_ass}'",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            "-shortest",
            str(out_path),
        ]
    )


def write_transcript(words: Iterable[dict[str, float | str]], transcript_path: Path) -> None:
    text = ""
    for i, w in enumerate(words):
        token = str(w["word"])
        if i > 0 and not is_punct(token):
            text += " "
        text += token
    transcript_path.write_text(text.strip() + "\n", encoding="utf-8")


def cue_plain_text(cue: list[dict[str, float | str]]) -> str:
    text = ""
    for i, w in enumerate(cue):
        token = str(w["word"])
        if i > 0 and not is_punct(token):
            text += " "
        text += token
    return text.strip()


def parse_model_json(content: str) -> object:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for pattern in (r"\{.*\}", r"\[.*\]"):
            match = re.search(pattern, text, flags=re.DOTALL)
            if not match:
                continue
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                continue
    raise RuntimeError("model response did not contain valid JSON")


def parse_translation_list(content: str, *, expected: int) -> list[str]:
    data = parse_model_json(content)

    items: object
    if isinstance(data, dict):
        items = data.get("translations")
    else:
        items = data

    if not isinstance(items, list):
        raise RuntimeError("translation response JSON missing translations array")

    id_map: dict[int, str] = {}
    has_id_objects = False
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id")
        raw_text = item.get("translation", item.get("text"))
        try:
            idx = int(raw_id)
        except (TypeError, ValueError):
            continue
        if not (1 <= idx <= expected):
            continue
        value = str(raw_text).strip() if raw_text is not None else ""
        if not value:
            continue
        has_id_objects = True
        id_map[idx] = value

    if has_id_objects:
        if len(id_map) != expected:
            missing = [str(i) for i in range(1, expected + 1) if i not in id_map]
            raise RuntimeError(
                "translation id mismatch: expected all ids 1.."
                f"{expected}, missing {', '.join(missing[:8])}"
            )
        return [id_map[i] for i in range(1, expected + 1)]

    if expected > 1:
        raise RuntimeError("translation response missing id-tagged items for multi-line batch")

    if any(isinstance(item, dict) for item in items):
        raise RuntimeError("translation response used object items without valid id/translation fields")

    out = [str(x).strip() for x in items]
    if len(out) != expected:
        raise RuntimeError(f"translation count mismatch: expected {expected}, got {len(out)}")
    return out


def extract_chat_message_content(data: object) -> str:
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if not isinstance(message, dict):
        return ""
    content_obj = message.get("content", "")
    if isinstance(content_obj, str):
        return content_obj
    if isinstance(content_obj, list):
        parts: list[str] = []
        for part in content_obj:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "".join(parts)
    return str(content_obj)


def request_chat_completion_content(
    *,
    payload: dict[str, object],
    api_key: str,
    max_time_seconds: int,
    request_name: str,
) -> str:
    cmd = [
        "curl",
        "-sS",
        "--retry",
        "4",
        "--retry-all-errors",
        "--retry-delay",
        "2",
        "--connect-timeout",
        "15",
        "--max-time",
        str(max_time_seconds),
        "-X",
        "POST",
        "https://api.openai.com/v1/chat/completions",
        "-H",
        f"Authorization: Bearer {api_key}",
        "-H",
        "Content-Type: application/json",
        "-d",
        json.dumps(payload, ensure_ascii=False),
    ]

    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        if proc.returncode == 6 or "Could not resolve host" in err:
            fail(
                "Could not resolve api.openai.com (DNS/network issue). "
                "Check connection/VPN/DNS and retry."
            )
        fail(f"{request_name} request failed (curl exit {proc.returncode}): {err or '(no details)'}")

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON from {request_name} API") from exc

    if isinstance(data, dict) and "error" in data:
        err = data["error"]
        if isinstance(err, dict):
            message = str(err.get("message", err))
        else:
            message = str(err)
        raise RuntimeError(f"{request_name} API error: {message}")

    content = extract_chat_message_content(data)
    if not content:
        raise RuntimeError(f"{request_name} API response missing message content")
    return content


def translate_single_line_to_english(
    *,
    line: str,
    api_key: str,
    model: str,
) -> str:
    payload: dict[str, object] = {
        "model": model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Translate the given subtitle line to faithful natural English. "
                    "Treat this line independently. If it is a fragment, translate it as a fragment. "
                    "Do not summarize, omit, add, or complete it using external context. "
                    "Preserve all clauses and questions. "
                    'Return strict JSON object: {"translation":"..."}'
                ),
            },
            {
                "role": "user",
                "content": json.dumps({"line": line}, ensure_ascii=False),
            },
        ],
    }

    content = request_chat_completion_content(
        payload=payload,
        api_key=api_key,
        max_time_seconds=120,
        request_name="single-line translation",
    )

    try:
        data = parse_model_json(content)
    except RuntimeError:
        data = None

    if isinstance(data, dict):
        translation = data.get("translation", data.get("text"))
        if translation is not None:
            text = str(translation).strip()
            if text:
                return text
    if isinstance(data, list) and data:
        text = str(data[0]).strip()
        if text:
            return text
    text = content.strip()
    if text:
        return text
    return line


def translate_batch_to_english(
    *,
    lines: list[str],
    api_key: str,
    model: str,
) -> list[str]:
    indexed_lines = [{"id": i + 1, "text": line} for i, line in enumerate(lines)]

    errors: list[str] = []
    for attempt in range(1, 4):
        system_prompt = (
            "Translate subtitle lines to faithful natural English. "
            "Keep one output per input in the same order. "
            "Do not summarize, omit, add, merge, or split meaning across lines. "
            "Do not move content from one id to another. Preserve named entities, negation, and questions. "
            "Translate each id independently as written. If a line is a fragment, keep it a fragment. "
            "Never complete one id using words from another id. "
            "Return strict JSON object with every id exactly once: "
            '{"translations":[{"id":1,"translation":"..."}]}'
        )
        user_payload: dict[str, object] = {
            "count": len(lines),
            "lines": indexed_lines,
        }
        if attempt > 1:
            user_payload["validation_error"] = errors[-1]
            user_payload["must_follow"] = "Include each id 1..count exactly once."

        payload: dict[str, object] = {
            "model": model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
        }

        content = request_chat_completion_content(
            payload=payload,
            api_key=api_key,
            max_time_seconds=220,
            request_name="translation",
        )
        try:
            return parse_translation_list(content, expected=len(lines))
        except RuntimeError as exc:
            errors.append(str(exc))
            print(f"Translation batch parse retry {attempt}/3: {exc}")

    print("Falling back to single-line translation for this batch.")
    return [
        translate_single_line_to_english(line=line, api_key=api_key, model=model) for line in lines
    ]


def translate_cues_to_english(
    *,
    cue_texts: list[str],
    api_key: str,
    model: str,
    batch_size: int = 1,
) -> list[str]:
    out: list[str] = []
    total = len(cue_texts)
    for start in range(0, total, batch_size):
        end = min(total, start + batch_size)
        if batch_size == 1:
            print(f"Translating cue {start + 1}/{total}...")
        else:
            print(f"Translating cues {start + 1}-{end}/{total}...")
        out.extend(
            translate_batch_to_english(
                lines=cue_texts[start:end],
                api_key=api_key,
                model=model,
            )
        )
    return out


def format_translation_text(text: str) -> tuple[str, int, int]:
    tokens = text.strip().split()
    if not tokens:
        return "", 52, 0

    total_chars = sum(len(t) for t in tokens) + max(0, len(tokens) - 1)
    if total_chars <= 30:
        max_lines = 1
        target_chars_per_line = 24
    elif total_chars <= 60:
        max_lines = 2
        target_chars_per_line = 24
    else:
        max_lines = 3
        target_chars_per_line = 25

    break_points, max_line_chars, line_count = balanced_break_indices(
        tokens,
        max_lines=max_lines,
        target_chars_per_line=target_chars_per_line,
        hard_max_chars_per_line=30,
    )

    lines: list[str] = [tokens[0]]
    for i in range(1, len(tokens)):
        tok = tokens[i]
        if i in break_points:
            lines.append(tok)
        else:
            lines[-1] += f" {tok}"

    escaped = [escape_ass(x) for x in lines]
    out_text = r"\N".join(escaped)

    if max_line_chars <= 22:
        font_size = 52
    elif max_line_chars <= 25:
        font_size = 48
    elif max_line_chars <= 28:
        font_size = 44
    elif max_line_chars <= 30:
        font_size = 40
    else:
        font_size = 38

    return out_text, font_size, line_count


def load_cached_translations(
    translation_path: Path,
    *,
    cue_texts: list[str],
) -> list[str] | None:
    if not translation_path.exists():
        return None
    try:
        data = json.loads(translation_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    sources = data.get("sources")
    translations = data.get("translations")
    if not isinstance(sources, list) or not isinstance(translations, list):
        return None
    if len(sources) != len(cue_texts) or len(translations) != len(cue_texts):
        return None

    source_norm = [str(x).strip() for x in sources]
    if source_norm != cue_texts:
        return None
    return [str(x).strip() for x in translations]


def ensure_openai_host_resolves(*, retries: int = 3, sleep_seconds: float = 1.0) -> None:
    last_error: Exception | None = None
    for _ in range(retries):
        try:
            socket.getaddrinfo("api.openai.com", 443, type=socket.SOCK_STREAM)
            return
        except OSError as exc:
            last_error = exc
            time.sleep(sleep_seconds)
    if last_error is not None:
        fail(
            "DNS lookup failed for api.openai.com. "
            "Try switching network/VPN or DNS resolver, then rerun."
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transcribe MP3 and create vertical karaoke subtitle video."
    )
    parser.add_argument("input_audio", help="Path to input audio file (mp3/m4a/wav/...)" )
    parser.add_argument(
        "--output-prefix",
        default=None,
        help="Prefix for output files (defaults to input filename stem in same folder)",
    )
    parser.add_argument(
        "--language",
        default="auto",
        help="Language code (fi/en/...), or auto for mixed-language detection",
    )
    parser.add_argument("--model", default="whisper-1", help="Transcription model")
    parser.add_argument(
        "--split-mode",
        choices=("semantic", "timing"),
        default="semantic",
        help="Subtitle cue splitting mode",
    )
    parser.add_argument(
        "--force-transcribe",
        action="store_true",
        help="Ignore cached *.words.json and call transcription API again",
    )
    parser.add_argument(
        "--chunk-seconds",
        type=int,
        default=540,
        help="Split audio into chunks of this many seconds before transcription",
    )
    parser.add_argument(
        "--english-translation",
        action="store_true",
        help="Add English translation subtitles at the bottom of the video",
    )
    parser.add_argument(
        "--translation-model",
        default="gpt-4o-mini",
        help="Model for subtitle translation when --english-translation is enabled",
    )
    parser.add_argument(
        "--force-translate",
        action="store_true",
        help="Ignore cached *.translation.en.json and call translation API again",
    )
    parser.add_argument(
        "--translation-batch-size",
        type=int,
        default=1,
        help="Cues per translation API request (1 is slowest but best sync fidelity)",
    )

    args = parser.parse_args()
    if args.translation_batch_size < 1:
        fail("--translation-batch-size must be >= 1")

    global FFMPEG_BIN, FFPROBE_BIN

    ffmpeg_full = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
    ffprobe_full = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffprobe")

    if ffmpeg_full.exists() and ffprobe_full.exists():
        FFMPEG_BIN = ffmpeg_full
        FFPROBE_BIN = ffprobe_full
    else:
        require_cmd("ffmpeg")
        require_cmd("ffprobe")
        FFMPEG_BIN = Path(shutil.which("ffmpeg") or "ffmpeg")
        FFPROBE_BIN = Path(shutil.which("ffprobe") or "ffprobe")

    input_audio = Path(args.input_audio).expanduser().resolve()
    if not input_audio.exists():
        fail(f"input audio not found: {input_audio}")

    language_arg = args.language.strip().lower()
    language: str | None
    if language_arg in {"", "auto", "detect", "none"}:
        language = None
    else:
        language = language_arg

    out_prefix = args.output_prefix
    if out_prefix is None or not out_prefix.strip():
        out_base = input_audio.with_suffix("")
    else:
        out_base = Path(out_prefix).expanduser().resolve()

    transcript_path = out_base.with_suffix(".transcript.txt")
    words_json_path = out_base.with_suffix(".words.json")
    translation_path = out_base.with_suffix(".translation.en.json")
    ass_path = out_base.with_suffix(".karaoke.ass")
    mp4_path = out_base.with_suffix(".karaoke.mp4")

    print(f"Input: {input_audio}")
    cached_words = None if args.force_transcribe else load_cached_words(words_json_path)
    api_key = os.environ.get("OPENAI_API_KEY")
    dns_checked = False

    if cached_words:
        print(f"Using cached transcript words: {words_json_path}")
        all_words = cached_words
    else:
        require_cmd("curl")
        if not api_key:
            fail("OPENAI_API_KEY is not set (or provide cached *.words.json)")
        ensure_openai_host_resolves()
        dns_checked = True

        print(f"Transcription language: {language if language else 'auto'}")
        print("Splitting audio...")
        all_words = []
        with tempfile.TemporaryDirectory(prefix="karaoke-work-") as tmp:
            tmp_dir = Path(tmp)
            chunks = split_audio(input_audio, tmp_dir / "chunks", args.chunk_seconds)
            total = len(chunks)
            for idx, (chunk, offset) in enumerate(chunks, start=1):
                print(f"Transcribing chunk {idx}/{total}: {chunk.name}")
                words = transcribe_chunk(
                    chunk_path=chunk,
                    offset=offset,
                    api_key=api_key,
                    model=args.model,
                    language=language,
                )
                all_words.extend(words)

    if not all_words:
        fail("no words returned from transcription")

    all_words.sort(key=lambda w: float(w["start"]))
    cues = build_cues(all_words, mode=args.split_mode)
    english_translations: list[str] | None = None

    write_transcript(all_words, transcript_path)
    words_json_path.write_text(json.dumps(all_words, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.english_translation:
        cue_texts = [cue_plain_text(cue) for cue in cues]
        cached_translations = None
        if not args.force_translate:
            cached_translations = load_cached_translations(translation_path, cue_texts=cue_texts)

        if cached_translations:
            print(f"Using cached English translations: {translation_path}")
            english_translations = cached_translations
        else:
            require_cmd("curl")
            if not api_key:
                fail(
                    "OPENAI_API_KEY is not set (or provide cached *.translation.en.json) "
                    "for --english-translation"
                )
            if not dns_checked:
                ensure_openai_host_resolves()
                dns_checked = True

            print(f"Translation model: {args.translation_model}")
            english_translations = translate_cues_to_english(
                cue_texts=cue_texts,
                api_key=api_key,
                model=args.translation_model,
                batch_size=args.translation_batch_size,
            )
            translation_path.write_text(
                json.dumps(
                    {
                        "model": args.translation_model,
                        "sources": cue_texts,
                        "translations": english_translations,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

    write_ass(cues, ass_path, english_translations=english_translations)

    duration = probe_duration(input_audio)
    print("Rendering vertical video...")
    render_video(input_audio, ass_path, mp4_path, duration)

    print("Done")
    print(f"Transcript: {transcript_path}")
    print(f"Words JSON: {words_json_path}")
    if args.english_translation:
        print(f"English translation cache: {translation_path}")
    print(f"ASS subtitles: {ass_path}")
    print(f"Video: {mp4_path}")


if __name__ == "__main__":
    main()
