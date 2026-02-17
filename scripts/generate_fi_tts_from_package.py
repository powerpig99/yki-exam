#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ID_HEADING_RE = re.compile(
    r"^####\s+((?:SPK|WRT)-[A-Z]+-\d{2}[A-Z]?)(?:\s*-\s*(.+?))?\s*$"
)
ROLE_LABEL_RE = re.compile(r"\b([A-ZÅÄÖ][A-Za-zÅÄÖåäö '\-]{0,24}:)\s*")
ROLE_LINE_RE = re.compile(r"^\s*([A-ZÅÄÖ][A-Za-zÅÄÖåäö '\-]{0,24})\s*:\s*(.*)$")
GOOGLE_VOICE_NAME_CACHE: dict[str, list[str]] = {}
GOOGLE_VOICE_CACHE_PATH = Path.home() / ".cache" / "yki_exam_google_voices.json"
DEFAULT_GOOGLE_VOICE_CACHE_TTL_SEC = 30 * 24 * 3600
GOOGLE_VOICE_CACHE_TTL_SEC = DEFAULT_GOOGLE_VOICE_CACHE_TTL_SEC
GOOGLE_VOICE_CACHE_FORCE_REFRESH = False


@dataclass
class Entry:
    id: str
    title: str
    block_lines: list[str]


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def split_entries(lines: Iterable[str]) -> list[Entry]:
    entries: list[Entry] = []
    current_id: str | None = None
    current_title: str | None = None
    current_block: list[str] = []

    for raw in lines:
        line = raw.rstrip("\n")
        m = ID_HEADING_RE.match(line)
        if m:
            if current_id is not None and current_title is not None:
                entries.append(Entry(current_id, current_title, current_block))
            current_id = m.group(1)
            current_title = (m.group(2) or "").strip()
            current_block = []
            continue
        if current_id is not None:
            current_block.append(line)

    if current_id is not None and current_title is not None:
        entries.append(Entry(current_id, current_title, current_block))
    return entries


def clean_markdown_line(line: str) -> str:
    s = line.strip()
    if not s:
        return ""
    s = s.replace("**", "")
    if s.startswith("- "):
        s = s[2:].strip()
    s = re.sub(r"^\d+\.\s*", "", s)
    s = s.replace("  ", " ")
    return s.strip()


def extract_block_lines_by_label(block_lines: list[str], label: str) -> list[str]:
    # Example label: "FI Mallivastaus"
    start_idx: int | None = None
    start_inline: str = ""
    prefix = f"**{label}:**"

    for i, line in enumerate(block_lines):
        if line.startswith(prefix):
            start_idx = i
            start_inline = line[len(prefix) :].strip()
            break
    if start_idx is None:
        return ""

    out_lines: list[str] = []
    if start_inline:
        out_lines.append(start_inline)

    for line in block_lines[start_idx + 1 :]:
        stripped = line.strip()
        if not stripped:
            # Keep paragraph boundaries as a sentence break.
            out_lines.append("")
            continue
        if (
            stripped.startswith("**FI ")
            or stripped.startswith("**EN ")
            or stripped.startswith("#### ")
            or stripped.startswith("---")
        ):
            break
        out_lines.append(stripped)

    cleaned: list[str] = []
    for line in out_lines:
        c = clean_markdown_line(line)
        if c:
            cleaned.append(c)

    return cleaned


def extract_block_by_label(block_lines: list[str], label: str) -> str:
    return " ".join(extract_block_lines_by_label(block_lines, label)).strip()


def fi_text_for_entry(entry: Entry) -> str:
    eid = entry.id
    lines = entry.block_lines
    if eid.startswith("SPK-DIA-"):
        listen_lines = extract_block_lines_by_label(lines, "FI Kuunteluteksti")
        if listen_lines:
            # Preserve line boundaries so role parsing stays stable.
            return "\n".join(listen_lines).strip()
    else:
        text = extract_block_by_label(lines, "FI Kuunteluteksti")
        if text:
            return text

    if eid.startswith("SPK-DIA-"):
        text = extract_block_by_label(lines, "FI Sinun repliikit (täydet mallivastaukset)")
        if text:
            return text
        # Fallback to full dialogue if needed.
        text = extract_block_by_label(lines, "FI Koko mallidialogi (täysi)")
        if text:
            return text
    elif eid.startswith("SPK-REA-") or eid.startswith("SPK-MIE-"):
        text = extract_block_by_label(lines, "FI Mallivastaus")
        if text:
            return text
    elif eid.startswith("SPK-KER-"):
        text = extract_block_by_label(lines, "FI Mallipuhe")
        if text:
            return text
    elif eid.startswith("WRT-"):
        text = extract_block_by_label(lines, "FI Malliteksti")
        if text:
            return text

    # Generic fallback order.
    for label in (
        "FI Mallivastaus",
        "FI Mallipuhe",
        "FI Malliteksti",
        "FI Sinun repliikit (täydet mallivastaukset)",
    ):
        text = extract_block_by_label(lines, label)
        if text:
            return text
    return ""


def section_type_from_id(eid: str) -> str:
    if eid.startswith("SPK-DIA-"):
        return "dialogi"
    if eid.startswith("SPK-REA-"):
        return "reagointi"
    if eid.startswith("SPK-KER-"):
        return "kertominen"
    if eid.startswith("SPK-MIE-"):
        return "mielipide"
    if eid.startswith("WRT-"):
        return "kirjoittaminen"
    return "unknown"


def ffprobe_duration_seconds(path: Path) -> float:
    preferred = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffprobe")
    if preferred.exists():
        ffprobe = str(preferred)
    else:
        ffprobe = shutil.which("ffprobe") or ""
    if not ffprobe:
        return 0.0

    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        out = subprocess.check_output(cmd, text=True).strip()
        return round(float(out), 3) if out else 0.0
    except Exception:
        return 0.0


def ffmpeg_binary() -> str:
    preferred = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
    if preferred.exists():
        return str(preferred)
    return shutil.which("ffmpeg") or ""


def say_binary() -> str:
    return shutil.which("say") or ""


def pick_finnish_say_voice(preferred_voice: str = "") -> str:
    say = say_binary()
    if not say:
        return ""
    try:
        out = subprocess.check_output(
            [say, "-v", "?"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return ""

    voices: list[str] = []
    for line in out.splitlines():
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^([A-Za-z0-9._-]+)\s+([A-Za-z0-9._-]+)\s+#", s)
        if not m:
            continue
        name, lang = m.group(1), m.group(2).lower()
        if lang.startswith("fi"):
            voices.append(name)

    if preferred_voice and preferred_voice in voices:
        return preferred_voice
    for preferred in ("Satu", "Oiva"):
        if preferred in voices:
            return preferred
    return voices[0] if voices else ""


def load_google_voice_cache() -> dict:
    try:
        if not GOOGLE_VOICE_CACHE_PATH.exists():
            return {}
        data = json.loads(GOOGLE_VOICE_CACHE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_google_voice_cache(data: dict) -> None:
    try:
        GOOGLE_VOICE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOOGLE_VOICE_CACHE_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        # Cache write errors are non-fatal.
        return


def synthesize_with_macos_say(
    *,
    text: str,
    out_file: Path,
    response_format: str,
    language: str | None,
    voice_override: str = "",
) -> None:
    say = say_binary()
    if not say:
        raise RuntimeError("macOS 'say' command not found.")
    ffmpeg = ffmpeg_binary()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found for local TTS conversion.")

    voice = ""
    if voice_override:
        voice = voice_override.strip()
    elif (language or "").lower().startswith("fi"):
        voice = pick_finnish_say_voice()
        if not voice:
            raise RuntimeError("No Finnish voice found for macOS 'say'.")

    with tempfile.TemporaryDirectory(prefix="say_tts_") as td:
        tdir = Path(td)
        aiff_path = tdir / "tts.aiff"
        cmd = [say]
        if voice:
            cmd.extend(["-v", voice])
        cmd.extend(["-o", str(aiff_path), text])
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        fmt = response_format.lower()
        ff_cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(aiff_path),
            "-ar",
            "24000",
            "-ac",
            "1",
        ]
        if fmt == "mp3":
            ff_cmd.extend(["-c:a", "libmp3lame", "-b:a", "128k"])
        elif fmt in ("wav", "wave"):
            ff_cmd.extend(["-c:a", "pcm_s16le"])
        ff_cmd.append(str(out_file))
        subprocess.run(ff_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def synthesize_with_edge_tts(
    *,
    text: str,
    out_file: Path,
    response_format: str,
    voice: str,
) -> None:
    edge_tts = shutil.which("edge-tts") or ""
    if not edge_tts:
        raise RuntimeError("edge-tts not found. Install with: pip install edge-tts")
    ffmpeg = ffmpeg_binary()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found for edge-tts conversion.")

    with tempfile.TemporaryDirectory(prefix="edge_tts_") as td:
        tdir = Path(td)
        src_mp3 = tdir / "tts.mp3"
        cmd = [
            edge_tts,
            "--voice",
            voice,
            "--text",
            text,
            "--write-media",
            str(src_mp3),
        ]
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            tail = (proc.stderr or "").strip()[-1200:]
            raise RuntimeError(f"edge-tts failed ({proc.returncode}): {tail}")

        fmt = response_format.lower()
        ff_cmd = [
            ffmpeg,
            "-y",
            "-i",
            str(src_mp3),
            "-ar",
            "24000",
            "-ac",
            "1",
        ]
        if fmt == "mp3":
            ff_cmd.extend(["-c:a", "libmp3lame", "-b:a", "128k"])
        elif fmt in ("wav", "wave"):
            ff_cmd.extend(["-c:a", "pcm_s16le"])
        ff_cmd.append(str(out_file))
        subprocess.run(ff_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def list_google_tts_voice_names(
    *,
    api_key: str,
    language_code: str,
    timeout_sec: int = 30,
    retries: int = 3,
) -> list[str]:
    cache_key = language_code.strip()
    cached = None if GOOGLE_VOICE_CACHE_FORCE_REFRESH else GOOGLE_VOICE_NAME_CACHE.get(cache_key)
    if cached:
        return list(cached)

    if not GOOGLE_VOICE_CACHE_FORCE_REFRESH:
        disk = load_google_voice_cache()
        entry = disk.get(cache_key, {})
        if isinstance(entry, dict):
            fetched_at = float(entry.get("fetched_at", 0.0) or 0.0)
            names = entry.get("names", [])
            if (
                isinstance(names, list)
                and names
                and (time.time() - fetched_at) <= max(3600, GOOGLE_VOICE_CACHE_TTL_SEC)
            ):
                clean_names = [str(x).strip() for x in names if str(x).strip()]
                if clean_names:
                    GOOGLE_VOICE_NAME_CACHE[cache_key] = clean_names
                    print(
                        f"    using cached Google voices for {language_code} ({len(clean_names)})",
                        flush=True,
                    )
                    return list(clean_names)

    url = (
        "https://texttospeech.googleapis.com/v1/voices"
        f"?languageCode={urllib.parse.quote(language_code)}"
        f"&key={urllib.parse.quote(api_key)}"
    )
    data: dict = {}
    last_err: Exception | None = None
    for attempt in range(1, max(1, retries) + 1):
        req = urllib.request.Request(url=url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                break
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="ignore")
            except Exception:
                body = ""
            last_err = RuntimeError(f"Google TTS voices list failed (HTTP {e.code}): {body[:800]}")
            retryable = e.code in (429, 500, 502, 503, 504)
            if not retryable or attempt >= retries:
                raise last_err
            time.sleep(min(2 ** attempt, 8))
        except Exception as e:
            last_err = RuntimeError(f"Google TTS voices list failed: {e}")
            if attempt >= retries:
                raise last_err
            time.sleep(min(2 ** attempt, 8))
    if not data:
        raise RuntimeError(f"Google TTS voices list failed after retries: {last_err}")

    names: list[str] = []
    for v in data.get("voices", []):
        if not isinstance(v, dict):
            continue
        n = str(v.get("name", "")).strip()
        if not n:
            continue
        langs = [str(x) for x in v.get("languageCodes", []) if isinstance(x, str)]
        if language_code in langs or n.startswith(f"{language_code}-"):
            names.append(n)
    GOOGLE_VOICE_NAME_CACHE[cache_key] = list(names)
    disk = load_google_voice_cache()
    disk[cache_key] = {"fetched_at": time.time(), "names": list(names)}
    save_google_voice_cache(disk)
    print(f"    fetched Google voices for {language_code} ({len(names)})", flush=True)
    return names


def pick_google_chirp_dialog_voices(
    *,
    api_key: str,
    language_code: str,
    count: int = 3,
) -> list[str]:
    names = list_google_tts_voice_names(api_key=api_key, language_code=language_code)
    chirp = [n for n in names if "Chirp3-HD" in n]
    pool = chirp if len(chirp) >= count else names
    if not pool:
        raise RuntimeError(
            f"No Google TTS voices found for language {language_code}. "
            "Verify Cloud Text-to-Speech API access and enabled voices."
        )
    rng = random.SystemRandom()
    if len(pool) >= count:
        return rng.sample(pool, count)
    out = pool[:]
    while len(out) < count:
        out.append(rng.choice(pool))
    return out


def synthesize_with_google_chirp(
    *,
    text: str,
    out_file: Path,
    response_format: str,
    api_key: str,
    voice_name: str,
    language_code: str,
) -> None:
    url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={urllib.parse.quote(api_key)}"
    payload = {
        "input": {"text": text},
        "voice": {"languageCode": language_code, "name": voice_name},
        "audioConfig": {"audioEncoding": "MP3"},
    }
    req = urllib.request.Request(
        url=url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="ignore")
        except Exception:
            body = ""
        raise RuntimeError(f"Google TTS synth failed (HTTP {e.code}): {body[:1000]}")
    except Exception as e:
        raise RuntimeError(f"Google TTS synth failed: {e}")

    b64 = str(data.get("audioContent", "")).strip()
    if not b64:
        raise RuntimeError("Google TTS synth failed: missing audioContent in response")

    mp3_bytes = base64.b64decode(b64)
    fmt = response_format.lower()
    if fmt == "mp3":
        out_file.write_bytes(mp3_bytes)
        return

    ffmpeg = ffmpeg_binary()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found for Google TTS conversion.")
    with tempfile.TemporaryDirectory(prefix="google_tts_") as td:
        tdir = Path(td)
        src_mp3 = tdir / "tts.mp3"
        src_mp3.write_bytes(mp3_bytes)
        ff_cmd = [ffmpeg, "-y", "-i", str(src_mp3), "-ar", "24000", "-ac", "1"]
        if fmt in ("wav", "wave"):
            ff_cmd.extend(["-c:a", "pcm_s16le"])
        else:
            ff_cmd.extend(["-c:a", "libmp3lame", "-b:a", "128k"])
        ff_cmd.append(str(out_file))
        subprocess.run(ff_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def build_tone(path: Path, *, freq_hz: int, duration_sec: float = 0.12) -> None:
    if freq_hz <= 0:
        return
    ffmpeg = ffmpeg_binary()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found for cue tone generation.")
    cmd = [
        ffmpeg,
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={freq_hz}:duration={duration_sec}",
        "-ar",
        "24000",
        "-ac",
        "1",
        "-b:a",
        "128k",
        str(path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def concat_audio_files(files: list[Path], out_file: Path) -> list[float]:
    ffmpeg = ffmpeg_binary()
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found for concat.")
    durations = [ffprobe_duration_seconds(p) for p in files]
    with tempfile.TemporaryDirectory(prefix="tts_concat_") as td:
        tdir = Path(td)
        list_file = tdir / "concat_list.txt"
        with list_file.open("w", encoding="utf-8") as f:
            for p in files:
                p_escaped = str(p).replace("'", "'\\''")
                f.write(f"file '{p_escaped}'\n")
        out_ext = out_file.suffix.lower().lstrip(".")
        cmd = [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-ar",
            "24000",
            "-ac",
            "1",
        ]
        if out_ext in ("wav", "wave"):
            cmd.extend(["-c:a", "pcm_s16le"])
        else:
            cmd.extend(["-c:a", "libmp3lame", "-b:a", "128k"])
        cmd.append(str(out_file))
        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc.returncode != 0:
            tail = (proc.stderr or "").strip()[-1200:]
            raise RuntimeError(f"ffmpeg concat failed ({proc.returncode}): {tail}")
        return durations


def parse_dialog_turns(text: str) -> tuple[str, list[tuple[str, str]]]:
    # Returns (context_before_turns, [(role, utterance), ...]).
    # First try strict line-by-line parsing so speaker order matches source
    # exactly and we never emit an empty first turn.
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    context_lines: list[str] = []
    turns: list[tuple[str, str]] = []
    cur_role: str | None = None
    cur_parts: list[str] = []

    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        m = ROLE_LINE_RE.match(s)
        if m:
            if cur_role is not None:
                utter = normalize_spaces(" ".join(cur_parts))
                if utter:
                    turns.append((cur_role, utter))
            cur_role = m.group(1).strip()
            first = m.group(2).strip()
            cur_parts = [first] if first else []
            continue
        if cur_role is None:
            context_lines.append(s)
        else:
            cur_parts.append(s)

    if cur_role is not None:
        utter = normalize_spaces(" ".join(cur_parts))
        if utter:
            turns.append((cur_role, utter))

    if turns:
        return normalize_spaces(" ".join(context_lines)), turns

    # Fallback for compact one-line dialog strings.
    # Keep role labels strict: no sentence punctuation in label. This avoids
    # accidental matches like "keli ulkona. Sinä:" that truncate dialogue.
    matches = list(ROLE_LABEL_RE.finditer(text))
    if not matches:
        return text.strip(), []
    context = text[: matches[0].start()].strip()
    for i, m in enumerate(matches):
        role = m.group(1).replace(":", "").strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        utter = normalize_spaces(text[start:end].strip())
        if utter:
            turns.append((role, utter))
    return context, turns


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def split_tts_sentences(text: str) -> list[str]:
    src = normalize_spaces(text)
    if not src:
        return []
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", src) if p.strip()]
    return parts or [src]


def timeline_from_parts(parts: list[dict], durations: list[float] | None = None) -> tuple[list[dict], float]:
    t = 0.0
    spoken: list[dict] = []
    for i, p in enumerate(parts):
        path = p["path"]
        if durations is not None and i < len(durations):
            dur = max(0.0, float(durations[i]))
        else:
            dur = ffprobe_duration_seconds(path)
        start = t
        end = t + max(0.0, dur)
        if p.get("kind") == "speech":
            spoken.append(
                {
                    "text_fi": p.get("text", ""),
                    "role": p.get("role", ""),
                    "start_sec": round(start, 3),
                    "end_sec": round(end, 3),
                    "duration_sec": round(max(0.0, dur), 3),
                }
            )
        t = end
    return spoken, round(t, 3)


def synthesize_text_by_sentences(
    *,
    api_key: str,
    model: str,
    voice: str,
    language: str | None,
    instructions: str | None,
    text: str,
    out_file: Path,
    response_format: str,
    backend: str,
    edge_voice: str,
    say_voice: str = "",
    google_api_key: str = "",
    google_language_code: str = "fi-FI",
    google_voice: str = "",
) -> list[dict]:
    sentences = split_tts_sentences(text)
    if not sentences:
        return []

    with tempfile.TemporaryDirectory(prefix="tts_text_") as td:
        tdir = Path(td)
        parts: list[dict] = []
        total_sentences = len(sentences)
        for i, sent in enumerate(sentences):
            p = tdir / f"seg_{i:03d}.{response_format}"
            call_tts(
                api_key=api_key,
                model=model,
                voice=voice,
                language=language,
                instructions=instructions,
                text=sent,
                out_file=p,
                response_format=response_format,
                backend=backend,
                edge_voice=edge_voice,
                say_voice=say_voice,
                google_api_key=google_api_key,
                google_language_code=google_language_code,
                google_voice=google_voice,
            )
            parts.append({"kind": "speech", "text": sent, "role": "", "path": p})
            done = i + 1
            if done == 1 or done % 12 == 0 or done == total_sentences:
                print(f"    text segment {done}/{total_sentences}", flush=True)

        durations = concat_audio_files([x["path"] for x in parts], out_file)
        spoken, _ = timeline_from_parts(parts, durations)
        return spoken


def synthesize_dialog_with_role_cues(
    *,
    api_key: str,
    model: str,
    voice: str,
    language: str | None,
    instructions: str | None,
    text: str,
    out_file: Path,
    response_format: str,
    friend_freq_hz: int,
    self_freq_hz: int,
    backend: str,
    edge_voice: str,
    say_voice: str,
    dialog_voice_a: str,
    dialog_voice_b: str,
    dialog_context_backend: str,
    dialog_context_voice: str,
    google_api_key: str,
    google_language_code: str,
    google_voice: str,
) -> list[dict]:
    context, turns = parse_dialog_turns(text)
    if not turns:
        return synthesize_text_by_sentences(
            api_key=api_key,
            model=model,
            voice=voice,
            language=language,
            instructions=instructions,
            text=text,
            out_file=out_file,
            response_format=response_format,
            backend=backend,
            edge_voice=edge_voice,
            say_voice=say_voice,
            google_api_key=google_api_key,
            google_language_code=google_language_code,
            google_voice=google_voice,
        )

    with tempfile.TemporaryDirectory(prefix="tts_dialog_") as td:
        tdir = Path(td)
        cue_friend = tdir / f"cue_friend.{response_format}"
        cue_self = tdir / f"cue_self.{response_format}"
        has_friend_cue = friend_freq_hz > 0
        has_self_cue = self_freq_hz > 0
        if has_friend_cue:
            build_tone(cue_friend, freq_hz=friend_freq_hz)
        if has_self_cue:
            build_tone(cue_self, freq_hz=self_freq_hz)

        parts: list[dict] = []
        seg_i = 0
        voice_a = dialog_voice_a.strip() or voice
        voice_b = dialog_voice_b.strip() or voice
        total_sentences = (len(split_tts_sentences(context)) if context else 0) + sum(
            len(split_tts_sentences(utter)) for _, utter in turns
        )
        done_sentences = 0

        if context:
            context_backend = dialog_context_backend.strip() or backend
            context_voice = dialog_context_voice.strip()
            context_openai_voice = context_voice or voice
            context_edge_voice = context_voice or edge_voice
            context_say_voice = context_voice or say_voice
            for sent in split_tts_sentences(context):
                p = tdir / f"seg_{seg_i:03d}.{response_format}"
                call_tts(
                    api_key=api_key,
                    model=model,
                    voice=context_openai_voice,
                    language=language,
                    instructions=instructions,
                    text=sent,
                    out_file=p,
                    response_format=response_format,
                    backend=context_backend,
                    edge_voice=context_edge_voice,
                    say_voice=context_say_voice,
                    google_api_key=google_api_key,
                    google_language_code=google_language_code,
                    google_voice=context_openai_voice,
                )
                parts.append({"kind": "speech", "text": sent, "role": "C", "path": p})
                seg_i += 1
                done_sentences += 1
                if (
                    done_sentences == 1
                    or done_sentences % 12 == 0
                    or done_sentences == total_sentences
                ):
                    print(f"    dialog segment {done_sentences}/{total_sentences} (C)", flush=True)

        role_to_alias: dict[str, str] = {}
        for role_raw, utter in turns:
            # Canonical rule: preserve speaker order from text.
            # First distinct speaker -> A, second distinct speaker -> B.
            role_key = role_raw.strip().casefold()
            alias = role_to_alias.get(role_key)
            if alias is None:
                alias = "A" if not role_to_alias else "B"
                role_to_alias[role_key] = alias
            cue_path: Path | None = cue_self if alias == "A" else cue_friend
            if alias == "A" and not has_self_cue:
                cue_path = None
            if alias == "B" and not has_friend_cue:
                cue_path = None
            if cue_path is not None:
                parts.append(
                    {
                        "kind": "tone",
                        "text": "",
                        "role": alias,
                        "path": cue_path,
                    }
                )
            turn_voice = voice_a if alias == "A" else voice_b
            for sent in split_tts_sentences(utter):
                p = tdir / f"seg_{seg_i:03d}.{response_format}"
                call_tts(
                    api_key=api_key,
                    model=model,
                    voice=turn_voice,
                    language=language,
                    instructions=instructions,
                    text=sent,
                    out_file=p,
                    response_format=response_format,
                    backend=backend,
                    edge_voice=edge_voice,
                    say_voice=say_voice,
                    google_api_key=google_api_key,
                    google_language_code=google_language_code,
                    google_voice=turn_voice,
                )
                parts.append(
                    {
                        "kind": "speech",
                        "text": sent,
                        "role": alias,
                        "path": p,
                    }
                )
                seg_i += 1
                done_sentences += 1
                if (
                    done_sentences == 1
                    or done_sentences % 12 == 0
                    or done_sentences == total_sentences
                ):
                    print(
                        f"    dialog segment {done_sentences}/{total_sentences} ({alias})",
                        flush=True,
                    )

        if not any(p.get("kind") == "speech" for p in parts):
            return synthesize_text_by_sentences(
                api_key=api_key,
                model=model,
                voice=voice,
                language=language,
                instructions=instructions,
                text=text,
                out_file=out_file,
                response_format=response_format,
                backend=backend,
                edge_voice=edge_voice,
                say_voice=say_voice,
                google_api_key=google_api_key,
                google_language_code=google_language_code,
                google_voice=google_voice,
            )

        durations = concat_audio_files([x["path"] for x in parts], out_file)
        spoken, _ = timeline_from_parts(parts, durations)
        return spoken


def call_tts(
    *,
    api_key: str,
    model: str,
    voice: str,
    language: str | None,
    instructions: str | None,
    text: str,
    out_file: Path,
    response_format: str,
    backend: str,
    edge_voice: str,
    say_voice: str,
    google_api_key: str,
    google_language_code: str,
    google_voice: str,
    timeout_sec: int = 120,
    retries: int = 4,
) -> None:
    if backend == "say":
        synthesize_with_macos_say(
            text=text,
            out_file=out_file,
            response_format=response_format,
            language=language,
            voice_override=say_voice,
        )
        return
    if backend == "edge_tts":
        edge_voice_name = voice.strip() or edge_voice
        synthesize_with_edge_tts(
            text=text,
            out_file=out_file,
            response_format=response_format,
            voice=edge_voice_name,
        )
        return
    if backend == "google_chirp":
        candidate = voice.strip() or google_voice.strip()
        selected_voice = ""
        if candidate and candidate.startswith(f"{google_language_code}-"):
            selected_voice = candidate
        if not selected_voice:
            voices = list_google_tts_voice_names(
                api_key=google_api_key,
                language_code=google_language_code,
            )
            chirp = [v for v in voices if "Chirp3-HD" in v]
            pool = chirp if chirp else voices
            if not pool:
                raise RuntimeError(
                    f"No Google TTS voices found for {google_language_code}."
                )
            selected_voice = pool[0]
        last_err: Exception | None = None
        for attempt in range(1, 4):
            try:
                synthesize_with_google_chirp(
                    text=text,
                    out_file=out_file,
                    response_format=response_format,
                    api_key=google_api_key,
                    voice_name=selected_voice,
                    language_code=google_language_code,
                )
                return
            except Exception as e:
                last_err = e
                if attempt >= 3:
                    break
                time.sleep(min(2 ** attempt, 8))
        raise RuntimeError(f"Google TTS synth failed after retries: {last_err}")
    if backend != "openai":
        raise RuntimeError(f"Unsupported TTS backend: {backend}")

    url = "https://api.openai.com/v1/audio/speech"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payloads: list[dict] = []
    seen: set[str] = set()
    for fmt_key in ("response_format", "format"):
        for include_lang, include_instr in (
            (True, True),
            (True, False),
            (False, True),
            (False, False),
        ):
            payload = {
                "model": model,
                "voice": voice,
                "input": text,
                fmt_key: response_format,
            }
            if include_lang and language:
                payload["language"] = language
            if include_instr and instructions:
                payload["instructions"] = instructions
            key = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            payloads.append(payload)

    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        for payload in payloads:
            req = urllib.request.Request(
                url=url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                    audio_bytes = resp.read()
                    out_file.write_bytes(audio_bytes)
                    return
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="ignore")
                except Exception:
                    body = ""
                last_err = RuntimeError(f"HTTP {e.code}: {body[:500]}")
                body_l = body.lower()
                if e.code == 429 and (
                    "insufficient_quota" in body_l
                    or "rate_limit" in body_l
                    or "rate limit" in body_l
                ):
                    break
                # 4xx except rate limit typically won't recover by retrying other payload quickly,
                # but we still attempt both payload styles each attempt.
                continue
            except Exception as e:
                last_err = e
                continue
        sleep_s = min(2 ** attempt, 10)
        time.sleep(sleep_s)

    raise RuntimeError(f"TTS request failed after retries: {last_err}")


def build_entries(input_md: Path) -> list[dict]:
    lines = read_lines(input_md)
    entries = split_entries(lines)
    out: list[dict] = []
    for e in entries:
        fi_text = fi_text_for_entry(e)
        out.append(
            {
                "id": e.id,
                "title": e.title,
                "type": section_type_from_id(e.id),
                "text_fi": fi_text,
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate Finnish TTS clips per ID from FI-EN package and write manifest."
    )
    parser.add_argument("--input", required=True, help="Path to .fi_en.md package")
    parser.add_argument("--out-dir", required=True, help="Directory for output audio files")
    parser.add_argument("--manifest", default="", help="Manifest JSON output path")
    parser.add_argument("--model", default="gpt-4o-mini-tts", help="TTS model")
    parser.add_argument("--voice", default="alloy", help="TTS voice")
    parser.add_argument(
        "--tts-backend",
        choices=("openai", "say", "edge_tts", "google_chirp"),
        default="openai",
        help="TTS backend (openai, google_chirp, edge_tts, say).",
    )
    parser.add_argument(
        "--edge-voice",
        default="fi-FI-NooraNeural",
        help="Voice name when --tts-backend edge_tts is used.",
    )
    parser.add_argument(
        "--say-voice",
        default="",
        help="Optional macOS say voice name (used when --tts-backend say).",
    )
    parser.add_argument(
        "--google-api-key-env",
        default="GOOGLE_API_KEY",
        help="Environment variable for Google API key (google_chirp backend).",
    )
    parser.add_argument(
        "--google-language-code",
        default="fi-FI",
        help="Google TTS language code for google_chirp backend.",
    )
    parser.add_argument(
        "--google-voice",
        default="",
        help="Default Google voice name for google_chirp backend (e.g. fi-FI-Chirp3-HD-...).",
    )
    parser.add_argument(
        "--google-voices-cache-ttl-hours",
        type=int,
        default=720,
        help="Reuse cached Google voice list for this many hours (default: 720 = 30 days).",
    )
    parser.add_argument(
        "--refresh-google-voices-cache",
        action="store_true",
        help="Force refresh Google voice list cache.",
    )
    parser.add_argument(
        "--language",
        default="fi",
        help="Language code hint for TTS (default: fi).",
    )
    parser.add_argument(
        "--instructions",
        default=(
            "Puhu luonnollista suomea. Kayta vain suomea. "
            "Lue numerot, paivamaarat, kellonajat ja lyhenteet suomeksi luonnollisessa muodossa."
        ),
        help="Optional TTS speaking instructions.",
    )
    parser.add_argument("--format", default="mp3", help="Audio format (mp3, wav, etc.)")
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Deprecated alias for --tts-backend say.",
    )
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY", help="Environment variable name for API key")
    parser.add_argument("--force", action="store_true", help="Regenerate files even if they already exist")
    parser.add_argument("--dry-run", action="store_true", help="Parse and manifest only, no API calls")
    parser.add_argument("--sleep-ms", type=int, default=250, help="Sleep between requests")
    parser.add_argument(
        "--dialog-role-cues",
        action="store_true",
        help="For SPK-DIA IDs, add two short role cue tones and synthesize by turns.",
    )
    parser.add_argument(
        "--friend-cue-hz",
        type=int,
        default=660,
        help="Cue tone frequency for role B/other (set 0 to disable tones).",
    )
    parser.add_argument(
        "--self-cue-hz",
        type=int,
        default=440,
        help="Cue tone frequency for role A/Sinä (set 0 to disable tones).",
    )
    parser.add_argument(
        "--dialog-voice-a",
        default="",
        help="Optional speaker A voice for dialog turns (falls back to --voice).",
    )
    parser.add_argument(
        "--dialog-voice-b",
        default="",
        help="Optional speaker B voice for dialog turns (falls back to --voice).",
    )
    parser.add_argument(
        "--dialog-context-backend",
        choices=("auto", "openai", "edge_tts", "say", "google_chirp"),
        default="auto",
        help="Backend for dialog context sentence. auto uses --tts-backend.",
    )
    parser.add_argument(
        "--dialog-context-voice",
        default="",
        help="Voice for dialog context sentence (backend-specific).",
    )
    parser.add_argument(
        "--random-dialog-google-voices",
        action="store_true",
        help="For dialogs on google_chirp, randomly pick 3 voices for C/A/B.",
    )
    args = parser.parse_args()

    global GOOGLE_VOICE_CACHE_TTL_SEC, GOOGLE_VOICE_CACHE_FORCE_REFRESH
    GOOGLE_VOICE_CACHE_TTL_SEC = max(3600, int(args.google_voices_cache_ttl_hours) * 3600)
    GOOGLE_VOICE_CACHE_FORCE_REFRESH = bool(args.refresh_google_voices_cache)

    input_md = Path(args.input).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = (
        Path(args.manifest).expanduser().resolve()
        if args.manifest
        else (out_dir / "manifest.json")
    )

    existing_text_by_id: dict[str, str] = {}
    existing_segments_by_id: dict[str, list[dict]] = {}
    if manifest_path.exists():
        try:
            prev = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(prev, dict):
                for item in prev.get("items", []):
                    if not isinstance(item, dict):
                        continue
                    eid = str(item.get("id", "")).strip()
                    txt = str(item.get("text_fi", ""))
                    if eid:
                        existing_text_by_id[eid] = txt
                        segs = item.get("spoken_segments")
                        if isinstance(segs, list):
                            existing_segments_by_id[eid] = [x for x in segs if isinstance(x, dict)]
        except Exception:
            existing_text_by_id = {}
            existing_segments_by_id = {}

    entries = build_entries(input_md)
    if not entries:
        print("No entries found.", file=sys.stderr)
        return 1

    missing_fi = [e["id"] for e in entries if not e["text_fi"]]
    if missing_fi:
        print("Missing Finnish text for IDs:", ", ".join(missing_fi), file=sys.stderr)
        return 2

    backend = args.tts_backend
    if args.local_only:
        backend = "say"
    context_backend = backend if args.dialog_context_backend == "auto" else args.dialog_context_backend

    api_key = os.environ.get(args.api_key_env, "").strip()
    google_api_key = os.environ.get(args.google_api_key_env, "").strip()
    google_language_code = args.google_language_code.strip() or "fi-FI"

    needs_openai = backend == "openai" or context_backend == "openai"
    if not args.dry_run and needs_openai and not api_key:
        print(f"Missing API key env: {args.api_key_env}", file=sys.stderr)
        return 3
    needs_google = backend == "google_chirp" or context_backend == "google_chirp"
    if not args.dry_run and needs_google and not google_api_key:
        print(f"Missing Google API key env: {args.google_api_key_env}", file=sys.stderr)
        return 4

    language = args.language.strip().lower()
    if language in {"", "auto", "none", "null"}:
        language = None
    instructions = args.instructions.strip() or None

    output_rows: list[dict] = []
    total = len(entries)

    for idx, e in enumerate(entries, start=1):
        eid = e["id"]
        title = e["title"]
        text_fi = e["text_fi"]
        ext = args.format.lower()
        audio_file = out_dir / f"{eid}.{ext}"

        can_reuse = audio_file.exists()
        if can_reuse and not args.force:
            prev_text = existing_text_by_id.get(eid)
            # Reuse only when we can verify this audio file was synthesized
            # from the same FI text.
            prev_segments = existing_segments_by_id.get(eid, [])
            can_reuse = prev_text is not None and prev_text == text_fi and bool(prev_segments)

        status = "skipped_existing"
        spoken_segments: list[dict] = []
        if args.force or not can_reuse:
            if args.dry_run:
                status = "dry_run"
            else:
                print(f"[{idx}/{total}] TTS {eid} ...")
                if args.dialog_role_cues and eid.startswith("SPK-DIA-"):
                    dialog_voice_a = args.dialog_voice_a
                    dialog_voice_b = args.dialog_voice_b
                    dialog_context_voice = args.dialog_context_voice
                    if args.random_dialog_google_voices and (
                        backend == "google_chirp" or context_backend == "google_chirp"
                    ):
                        picked = pick_google_chirp_dialog_voices(
                            api_key=google_api_key,
                            language_code=google_language_code,
                            count=3,
                        )
                        print(
                            "    random dialog voices: "
                            f"C={picked[0]} A={picked[1]} B={picked[2]}",
                            flush=True,
                        )
                        if context_backend == "google_chirp" and not dialog_context_voice:
                            dialog_context_voice = picked[0]
                        if backend == "google_chirp":
                            if not dialog_voice_a:
                                dialog_voice_a = picked[1]
                            if not dialog_voice_b:
                                dialog_voice_b = picked[2]

                    spoken_segments = synthesize_dialog_with_role_cues(
                        api_key=api_key,
                        model=args.model,
                        voice=args.voice,
                        language=language,
                        instructions=instructions,
                        text=text_fi,
                        out_file=audio_file,
                        response_format=args.format,
                        friend_freq_hz=args.friend_cue_hz,
                        self_freq_hz=args.self_cue_hz,
                        backend=backend,
                        edge_voice=args.edge_voice,
                        say_voice=args.say_voice,
                        dialog_voice_a=dialog_voice_a,
                        dialog_voice_b=dialog_voice_b,
                        dialog_context_backend=context_backend,
                        dialog_context_voice=dialog_context_voice,
                        google_api_key=google_api_key,
                        google_language_code=google_language_code,
                        google_voice=args.google_voice,
                    )
                else:
                    spoken_segments = synthesize_text_by_sentences(
                        api_key=api_key,
                        model=args.model,
                        voice=args.voice,
                        language=language,
                        instructions=instructions,
                        text=text_fi,
                        out_file=audio_file,
                        response_format=args.format,
                        backend=backend,
                        edge_voice=args.edge_voice,
                        say_voice=args.say_voice,
                        google_api_key=google_api_key,
                        google_language_code=google_language_code,
                        google_voice=args.google_voice,
                    )
                status = "generated"
                time.sleep(max(args.sleep_ms, 0) / 1000.0)
        else:
            spoken_segments = existing_segments_by_id.get(eid, [])

        duration = ffprobe_duration_seconds(audio_file) if audio_file.exists() else 0.0
        if not spoken_segments and text_fi and duration > 0:
            spoken_segments = [
                {
                    "text_fi": text_fi,
                    "role": "",
                    "start_sec": 0.0,
                    "end_sec": round(duration, 3),
                    "duration_sec": round(duration, 3),
                }
            ]
        output_rows.append(
            {
                "id": eid,
                "title": title,
                "type": e["type"],
                "text_fi": text_fi,
                "spoken_segments": spoken_segments,
                "audio_file": str(audio_file),
                "duration_sec": duration,
                "status": status,
            }
        )

    manifest = {
        "source_package": str(input_md),
        "model": args.model,
        "voice": args.voice,
        "tts_backend": backend,
        "edge_voice": args.edge_voice if backend == "edge_tts" else "",
        "say_voice": args.say_voice if backend == "say" else "",
        "google_language_code": google_language_code if backend == "google_chirp" else "",
        "google_voice": args.google_voice if backend == "google_chirp" else "",
        "dialog_context_backend": context_backend,
        "dialog_context_voice": args.dialog_context_voice,
        "language": language or "",
        "instructions": instructions or "",
        "format": args.format,
        "count": len(output_rows),
        "items": output_rows,
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote manifest: {manifest_path}")
    print(f"Items: {len(output_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
