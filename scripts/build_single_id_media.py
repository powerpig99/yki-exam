#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


ENTRY_PREFIX = "#### "


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def extract_entry(fi_en_package: Path, entry_id: str) -> str:
    lines = fi_en_package.read_text(encoding="utf-8").splitlines()
    start = None
    needle = f"#### {entry_id}"
    for i, line in enumerate(lines):
        if line.startswith(needle):
            start = i
            break
    if start is None:
        raise RuntimeError(f"Entry not found: {entry_id}")

    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith(ENTRY_PREFIX):
            end = i
            break
    return "\n".join(lines[start:end]).rstrip() + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description="Build one ID as FI audio + dual-sub karaoke video.")
    p.add_argument("--entry-id", required=True, help="e.g. SPK-DIA-02")
    p.add_argument("--fi-en-package", required=True)
    p.add_argument("--section-title", required=True, help="e.g. Ihminen ja lähipiiri")
    p.add_argument(
        "--source-md",
        default="yki_highway_to_hill.md",
    )
    p.add_argument(
        "--out-dir",
        default="media/single_id_test",
    )
    p.add_argument("--tts-backend", choices=("openai", "edge_tts", "say", "google_chirp"), default="edge_tts")
    p.add_argument("--edge-voice", default="fi-FI-NooraNeural")
    p.add_argument("--say-voice", default="")
    p.add_argument("--google-api-key-env", default="GOOGLE_API_KEY")
    p.add_argument("--google-language-code", default="fi-FI")
    p.add_argument("--google-voice", default="")
    p.add_argument("--dialog-voice-a", default="fi-FI-NooraNeural")
    p.add_argument("--dialog-voice-b", default="fi-FI-HarriNeural")
    p.add_argument("--dialog-context-backend", choices=("auto", "openai", "edge_tts", "say", "google_chirp"), default="say")
    p.add_argument("--dialog-context-voice", default="Satu")
    p.add_argument("--self-cue-hz", type=int, default=0)
    p.add_argument("--friend-cue-hz", type=int, default=0)
    p.add_argument("--random-dialog-google-voices", action="store_true")
    p.add_argument("--allow-api-fallback", action="store_true")
    p.add_argument("--allow-missing-english", action="store_true")
    p.add_argument("--clean", action="store_true")
    args = p.parse_args()

    fi_en_package = Path(args.fi_en_package).expanduser().resolve()
    source_md = Path(args.source_md).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()

    if args.clean and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    subset_fi_en = out_dir / f"{args.entry_id}.fi_en.md"
    subset_listening = out_dir / f"{args.entry_id}.listening.fi.md"
    audio_dir = out_dir / "audio"
    manifest = audio_dir / "manifest.json"
    out_base = out_dir / args.entry_id.lower()
    out_mp3 = out_base.with_suffix(".mp3")

    subset_fi_en.write_text(extract_entry(fi_en_package, args.entry_id), encoding="utf-8")

    run(
        [
            "python3",
            "scripts/build_fi_listening_package.py",
            "--source-md",
            str(source_md),
            "--section",
            args.section_title,
            "--fi-en-package",
            str(subset_fi_en),
            "--out",
            str(subset_listening),
        ]
    )

    audio_dir.mkdir(parents=True, exist_ok=True)
    run(
        [
            "python3",
            "scripts/generate_fi_tts_from_package.py",
            "--input",
            str(subset_listening),
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
            "--format",
            "mp3",
            "--force",
            "--sleep-ms",
            "0",
            *(["--random-dialog-google-voices"] if args.random_dialog_google_voices else []),
        ]
    )

    src_mp3 = audio_dir / f"{args.entry_id}.mp3"
    if not src_mp3.exists():
        raise RuntimeError(f"Expected MP3 not found: {src_mp3}")
    shutil.copy2(src_mp3, out_mp3)

    cmd = [
        "python3",
        "scripts/render_dual_karaoke_from_package.py",
        "--audio",
        str(out_mp3),
        "--output-prefix",
        str(out_base),
        "--fi-en-package",
        str(subset_fi_en),
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
    if args.allow_api_fallback:
        cmd.append("--allow-api-fallback")
    if args.allow_missing_english:
        cmd.append("--allow-missing-english")
    run(cmd)

    print("\nDone")
    print("FI-EN subset:", subset_fi_en)
    print("Listening input:", subset_listening)
    print("Final MP3:", out_mp3)
    print("Final MP4:", out_base.with_suffix(".karaoke.mp4"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
