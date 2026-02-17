#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


ENTRY_RE = re.compile(r"^####\s+((?:SPK|WRT)-[A-Z]+-\d{2}[A-Z]?)(?:\s*-\s*(.+?))?\s*$")
MIE_ID_RE = re.compile(r"^SPK-MIE-(\d+)([A-Z]?)$")


@dataclass
class Entry:
    id: str
    title: str
    lines: list[str]


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def clean_md(s: str) -> str:
    s = s.strip().replace("**", "")
    if s.startswith("- "):
        s = s[2:].strip()
    s = re.sub(r"^\d+\.\s*", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


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
        out.append(clean_md(inline))
    for line in lines[start + 1 :]:
        s = line.strip()
        if not s:
            continue
        if s.startswith("**FI ") or s.startswith("**EN ") or s.startswith("#### ") or s.startswith("---"):
            break
        out.append(clean_md(s))
    return [x for x in out if x]


def block_text(lines: list[str], label: str) -> str:
    return " ".join(extract_block_lines(lines, label)).strip()


def mie_sort_key(eid: str) -> tuple[int, str]:
    m = MIE_ID_RE.match(eid)
    if not m:
        return (10_000, "Z")
    return (int(m.group(1)), m.group(2) or "A")


def translate_single_line(
    *,
    line_fi: str,
    api_key: str,
    model: str,
    retries: int = 3,
) -> str:
    url = "https://api.openai.com/v1/chat/completions"
    payload: dict[str, object] = {
        "model": model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Translate Finnish to natural English. "
                    "Keep meaning faithful and keep it as one line. "
                    "Return only the translation text."
                ),
            },
            {"role": "user", "content": line_fi},
        ],
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_err: Exception | None = None
    for attempt in range(1, max(1, retries) + 1):
        req = urllib.request.Request(url=url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read().decode("utf-8")
            parsed = json.loads(raw)
            choices = parsed.get("choices", [])
            if isinstance(choices, list) and choices:
                msg = choices[0].get("message", {})
                if isinstance(msg, dict):
                    content = msg.get("content", "")
                    if isinstance(content, str) and content.strip():
                        return content.strip()
            raise RuntimeError("translation API response missing content")
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="ignore")
            except Exception:
                body = ""
            last_err = RuntimeError(f"HTTP {e.code}: {body[:500]}")
        except Exception as e:
            last_err = e
        if attempt < retries:
            time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"line translation failed: {last_err}")


def translate_single_line_google_web(line_fi: str) -> str:
    q = urllib.parse.quote(line_fi)
    url = (
        "https://translate.googleapis.com/translate_a/single"
        f"?client=gtx&sl=fi&tl=en&dt=t&q={q}"
    )
    req = urllib.request.Request(url=url, method="GET")
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, list) or not data:
        return line_fi
    parts = data[0]
    if not isinstance(parts, list):
        return line_fi
    out: list[str] = []
    for p in parts:
        if isinstance(p, list) and p and isinstance(p[0], str):
            out.append(p[0])
    text = "".join(out).strip()
    return text if text else line_fi


def translate_single_line_deepl(
    *,
    line_fi: str,
    context_fi: str,
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
            if context_fi.strip():
                form["context"] = context_fi.strip()
            data = urllib.parse.urlencode(form).encode("utf-8")
            req = urllib.request.Request(
                url=endpoint,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=45) as resp:
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


def build_mielipide_dialog_package(
    *,
    source_package: Path,
    listening_compact: Path,
    out_package: Path,
    title_fi: str,
    title_en: str,
    entry_id: str,
    translation_model: str,
    api_key_env: str,
    deepl_api_key_env: str,
) -> None:
    entries = split_entries(read_lines(source_package))
    mie_entries = sorted((e for e in entries if e.id.startswith("SPK-MIE-")), key=lambda e: mie_sort_key(e.id))
    if not mie_entries:
        raise RuntimeError(f"No SPK-MIE entries found in {source_package}")

    prompt_by_id: dict[str, str] = {}
    if listening_compact.exists():
        lc_entries = split_entries(read_lines(listening_compact))
        for e in lc_entries:
            if not e.id.startswith("SPK-MIE-"):
                continue
            prompt = block_text(e.lines, "Tehtävä ja kysymykset (FI)")
            if prompt:
                prompt_by_id[e.id] = prompt
    else:
        print(f"Warning: listening compact file not found: {listening_compact}", flush=True)

    api_key = os.environ.get(api_key_env, "").strip()
    deepl_api_key = os.environ.get(deepl_api_key_env, "").strip()
    if not api_key and not deepl_api_key:
        print(
            f"Warning: neither {deepl_api_key_env} nor {api_key_env} is set; "
            "using Google web translation fallback for missing Mielipide EN prompt lines.",
            flush=True,
        )
    if deepl_api_key:
        print(f"Using DeepL translation via env {deepl_api_key_env}.", flush=True)

    line_cache: dict[str, str] = {}

    def translate_line(fi_text: str, context_fi: str) -> str:
        if not fi_text:
            return ""
        cache_key = f"{fi_text}\n@@CTX@@\n{context_fi}"
        if cache_key in line_cache:
            return line_cache[cache_key]
        en = ""
        if deepl_api_key:
            try:
                en = translate_single_line_deepl(
                    line_fi=fi_text,
                    context_fi=context_fi,
                    deepl_api_key=deepl_api_key,
                ).strip()
            except Exception as exc:
                print(
                    f"Warning: DeepL line translation failed; falling back ({exc})",
                    flush=True,
                )
        if not en and api_key:
            try:
                en = translate_single_line(
                    line_fi=fi_text,
                    api_key=api_key,
                    model=translation_model,
                ).strip()
            except Exception as exc:
                print(
                    f"Warning: OpenAI line translation failed; using Google fallback ({exc})",
                    flush=True,
                )
        if not en:
            en = translate_single_line_google_web(fi_text).strip()
        if not en:
            en = fi_text
        line_cache[cache_key] = en
        return en

    fi_pairs: list[tuple[str, str]] = []
    en_pairs: list[tuple[str, str]] = []
    for e in mie_entries:
        fi_title = clean_md(e.title)
        fi_ctx = block_text(e.lines, "FI Konteksti")
        fi_ans = block_text(e.lines, "FI Mallivastaus")
        en_ctx = block_text(e.lines, "EN Context")
        en_ans = block_text(e.lines, "EN Sample answer")

        fi_a = prompt_by_id.get(e.id, "").strip()
        if not fi_a:
            fi_a = " ".join(part for part in [fi_title, fi_ctx] if part).strip()
        if not fi_a or not fi_ans:
            continue

        en_a = en_ctx.strip() if en_ctx.strip() else translate_line(fi_a, fi_ans)
        en_b = en_ans.strip() if en_ans.strip() else translate_line(fi_ans, fi_a)

        fi_pairs.append((fi_a, fi_ans))
        en_pairs.append((en_a, en_b))

    if not fi_pairs:
        raise RuntimeError("No FI context/answer pairs found for SPK-MIE entries.")

    title_fi_ctx = title_fi.strip()
    if title_fi_ctx and title_fi_ctx[-1] not in ".!?":
        title_fi_ctx += "."
    title_en_ctx = title_en.strip()
    if title_en_ctx and title_en_ctx[-1] not in ".!?":
        title_en_ctx += "."

    lines: list[str] = []
    lines.append(f"#### {entry_id} - {title_fi}")
    lines.append(f"**FI Konteksti:** {title_fi_ctx}")
    lines.append(f"**EN Context:** {title_en_ctx}")
    lines.append("")
    lines.append("**FI Koko mallidialogi (täysi):**")
    for q, a in fi_pairs:
        lines.append(f"- **A**: {q}")
        lines.append(f"- **B**: {a}")
    lines.append("")
    lines.append("**EN Full sample dialogue:**")
    for q, a in en_pairs:
        lines.append(f"- **A**: {q}")
        lines.append(f"- **B**: {a}")
    lines.append("")
    lines.append("**FI Kuunteluteksti:**")
    lines.append(title_fi_ctx)
    for q, a in fi_pairs:
        lines.append(f"A: {q}")
        lines.append(f"B: {a}")
    lines.append("")

    out_package.parent.mkdir(parents=True, exist_ok=True)
    out_package.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"Wrote: {out_package}")


def main() -> int:
    p = argparse.ArgumentParser(description="Build subsection Mielipide as one big dialog with dual subtitles.")
    p.add_argument("--fi-en-package", required=True, help="Section complete package (.fi_en.md)")
    p.add_argument("--fi-listening-compact", default="", help="Section listening compact FI package.")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--entry-id", default="SPK-DIA-97")
    p.add_argument("--title-fi", default="Mielipide")
    p.add_argument("--title-en", default="Opinion task")
    p.add_argument("--translation-model", default="gpt-4o-mini")
    p.add_argument("--api-key-env", default="OPENAI_API_KEY")
    p.add_argument("--deepl-api-key-env", default="DEEPL_API_KEY")
    p.add_argument("--tts-backend", choices=("openai", "edge_tts", "say", "google_chirp"), default="google_chirp")
    p.add_argument("--edge-voice", default="fi-FI-NooraNeural")
    p.add_argument("--say-voice", default="")
    p.add_argument("--google-api-key-env", default="GOOGLE_API_KEY")
    p.add_argument("--google-language-code", default="fi-FI")
    p.add_argument("--google-voice", default="")
    p.add_argument("--dialog-voice-a", default="")
    p.add_argument("--dialog-voice-b", default="")
    p.add_argument(
        "--dialog-context-backend",
        choices=("auto", "openai", "edge_tts", "say", "google_chirp"),
        default="google_chirp",
    )
    p.add_argument("--dialog-context-voice", default="")
    p.add_argument("--random-dialog-google-voices", action="store_true")
    p.add_argument("--self-cue-hz", type=int, default=0)
    p.add_argument("--friend-cue-hz", type=int, default=0)
    p.add_argument("--clean", action="store_true")
    args = p.parse_args()

    root = Path("/Users/jingliang/Documents/YKI_exam")
    scripts = root / "scripts"

    src_package = Path(args.fi_en_package).expanduser().resolve()
    if args.fi_listening_compact:
        listening_compact = Path(args.fi_listening_compact).expanduser().resolve()
    else:
        auto_name = src_package.name.replace("_complete_package.fi_en.md", "_listening_compact.fi.md")
        listening_compact = src_package.parent / auto_name

    out_dir = Path(args.out_dir).expanduser().resolve()
    if args.clean and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    dialog_package = out_dir / f"{args.entry_id}.fi_en.md"
    build_mielipide_dialog_package(
        source_package=src_package,
        listening_compact=listening_compact,
        out_package=dialog_package,
        title_fi=args.title_fi,
        title_en=args.title_en,
        entry_id=args.entry_id,
        translation_model=args.translation_model,
        api_key_env=args.api_key_env,
        deepl_api_key_env=args.deepl_api_key_env,
    )

    audio_dir = out_dir / "audio"
    manifest = audio_dir / "manifest.json"
    audio_dir.mkdir(parents=True, exist_ok=True)
    run(
        [
            "python3",
            str(scripts / "generate_fi_tts_from_package.py"),
            "--input",
            str(dialog_package),
            "--out-dir",
            str(audio_dir),
            "--manifest",
            str(manifest),
            "--tts-backend",
            args.tts_backend,
            "--edge-voice",
            args.edge_voice,
            "--say-voice",
            args.say_voice,
            "--google-api-key-env",
            args.google_api_key_env,
            "--google-language-code",
            args.google_language_code,
            "--google-voice",
            args.google_voice,
            "--dialog-voice-a",
            args.dialog_voice_a,
            "--dialog-voice-b",
            args.dialog_voice_b,
            "--dialog-context-backend",
            args.dialog_context_backend,
            "--dialog-context-voice",
            args.dialog_context_voice,
            "--dialog-role-cues",
            "--self-cue-hz",
            str(args.self_cue_hz),
            "--friend-cue-hz",
            str(args.friend_cue_hz),
            "--language",
            "fi",
            "--instructions",
            "Puhu luonnollista suomea. Kayta vain suomea. Lue numerot, paivamaarat, kellonajat ja lyhenteet suomeksi luonnollisessa muodossa.",
            "--format",
            "mp3",
            "--force",
            "--sleep-ms",
            "0",
            *(["--random-dialog-google-voices"] if args.random_dialog_google_voices else []),
        ]
    )

    audio_src = audio_dir / f"{args.entry_id}.mp3"
    audio_out = out_dir / "mielipide.mp3"
    shutil.copy2(audio_src, audio_out)

    video_prefix = out_dir / "mielipide"
    run(
        [
            "python3",
            str(scripts / "render_dual_karaoke_from_package.py"),
            "--audio",
            str(audio_out),
            "--output-prefix",
            str(video_prefix),
            "--fi-en-package",
            str(dialog_package),
            "--section-manifest",
            str(manifest),
            "--language",
            "fi",
            "--split-mode",
            "semantic",
            "--timing-source",
            "tts_text",
            "--cue-anchor",
            "manifest",
        ]
    )

    print("\nDone")
    print(f"Dialog package: {dialog_package}")
    print(f"Final MP3: {audio_out}")
    print(f"Final MP4: {video_prefix.with_suffix('.karaoke.mp4')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
