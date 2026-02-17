#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


ENTRY_RE = re.compile(r"^####\s+((?:SPK|WRT)-[A-Z]+-\d{2}[A-Z]?)(?:\s*-\s*(.+?))?\s*$")
REA_ID_RE = re.compile(r"^SPK-REA-(\d+)$")


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


def rea_sort_key(eid: str) -> int:
    m = REA_ID_RE.match(eid)
    return int(m.group(1)) if m else 10_000


def build_reagointi_dialog_package(
    *,
    source_package: Path,
    out_package: Path,
    title_fi: str,
    title_en: str,
    entry_id: str,
) -> None:
    entries = split_entries(read_lines(source_package))
    rea_entries = sorted((e for e in entries if e.id.startswith("SPK-REA-")), key=lambda e: rea_sort_key(e.id))
    if not rea_entries:
        raise RuntimeError(f"No SPK-REA entries found in {source_package}")

    fi_pairs: list[tuple[str, str]] = []
    en_pairs: list[tuple[str, str]] = []
    for e in rea_entries:
        fi_q = block_text(e.lines, "FI Konteksti")
        fi_a = block_text(e.lines, "FI Mallivastaus")
        en_q = block_text(e.lines, "EN Context")
        en_a = block_text(e.lines, "EN Sample answer")
        if fi_q and fi_a:
            fi_pairs.append((fi_q, fi_a))
        if en_q and en_a:
            en_pairs.append((en_q, en_a))

    if not fi_pairs:
        raise RuntimeError("No FI question/answer pairs found for SPK-REA entries.")

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
    if en_pairs:
        for q, a in en_pairs:
            lines.append(f"- **A**: {q}")
            lines.append(f"- **B**: {a}")
    else:
        lines.append("- **Question**: Reagointi")
        lines.append("- **Answer**: Reagointi")
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
    p = argparse.ArgumentParser(description="Build subsection Reagointi as one big dialog with dual subtitles.")
    p.add_argument("--fi-en-package", required=True, help="Section complete package (.fi_en.md)")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--entry-id", default="SPK-DIA-99")
    p.add_argument("--title-fi", default="Reagointi")
    p.add_argument("--title-en", default="Reaction exercises")
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

    root = Path(".")
    scripts = root / "scripts"

    src_package = Path(args.fi_en_package).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    if args.clean and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    dialog_package = out_dir / f"{args.entry_id}.fi_en.md"
    build_reagointi_dialog_package(
        source_package=src_package,
        out_package=dialog_package,
        title_fi=args.title_fi,
        title_en=args.title_en,
        entry_id=args.entry_id,
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
    audio_out = out_dir / "reagointi.mp3"
    shutil.copy2(audio_src, audio_out)

    video_prefix = out_dir / "reagointi"
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
