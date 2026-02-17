#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from pathlib import Path


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=str(cwd) if cwd else None)


def write_concat_list(manifest_path: Path, concat_list_path: Path) -> None:
    import json

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = data.get("items", [])
    concat_list_path.parent.mkdir(parents=True, exist_ok=True)
    with concat_list_path.open("w", encoding="utf-8") as f:
        for item in items:
            p = str(item["audio_file"]).replace("'", "'\\''")
            f.write(f"file '{p}'\n")


def cleanup_intermediates(out_base: Path) -> None:
    # Keep .words.json and .translation.en.json caches so rerenders can skip
    # retranscription/retranslation and translations remain editable.
    for suffix in (
        ".transcript.txt",
        ".karaoke.ass",
    ):
        p = out_base.with_suffix(suffix)
        if p.exists():
            p.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build final section mp3+mp4 from FI-EN package.")
    parser.add_argument("--section-id", required=True, help="e.g. 03")
    parser.add_argument("--section-slug", required=True, help="ascii slug e.g. luonto_ja_ymparisto")
    parser.add_argument("--section-title", required=True, help="exact source section title e.g. Luonto ja ympäristö")
    parser.add_argument("--fi-en-package", required=True, help="Path to section .fi_en.md package")
    parser.add_argument(
        "--source-md",
        default="yki_highway_to_hill.md",
        help="Source markdown path",
    )
    parser.add_argument(
        "--project-root",
        default=".",
        help="Project root",
    )
    parser.add_argument(
        "--translation-batch-size",
        type=int,
        default=1,
        help="Kept for compatibility; package-translation renderer does not use this.",
    )
    parser.add_argument("--force-audio", action="store_true", help="Regenerate per-ID TTS clips and merged mp3")
    parser.add_argument("--force-video", action="store_true", help="Force retranscribe/retranslate video render")
    parser.add_argument(
        "--tts-backend",
        choices=("openai", "say", "edge_tts", "google_chirp"),
        default="openai",
        help="TTS backend for FI audio generation.",
    )
    parser.add_argument(
        "--edge-voice",
        default="fi-FI-NooraNeural",
        help="Voice for edge_tts backend.",
    )
    parser.add_argument(
        "--say-voice",
        default="",
        help="Optional macOS say voice name.",
    )
    parser.add_argument("--google-api-key-env", default="GOOGLE_API_KEY", help="Google API key env var.")
    parser.add_argument("--google-language-code", default="fi-FI", help="Google TTS language code.")
    parser.add_argument("--google-voice", default="", help="Google default voice name.")
    parser.add_argument(
        "--dialog-voice-a",
        default="",
        help="Optional dialog speaker A voice (backend-specific).",
    )
    parser.add_argument(
        "--dialog-voice-b",
        default="",
        help="Optional dialog speaker B voice (backend-specific).",
    )
    parser.add_argument(
        "--dialog-context-backend",
        choices=("auto", "openai", "edge_tts", "say"),
        default="auto",
        help="Backend for dialog context sentence.",
    )
    parser.add_argument(
        "--dialog-context-voice",
        default="",
        help="Voice for dialog context sentence (backend-specific).",
    )
    parser.add_argument(
        "--random-dialog-google-voices",
        action="store_true",
        help="Randomly pick 3 Google Chirp voices for dialog C/A/B.",
    )
    parser.add_argument(
        "--force-transcribe",
        action="store_true",
        help="When rendering video, force retranscription instead of reusing cached words.",
    )
    parser.add_argument(
        "--disable-api-fallback",
        action="store_true",
        help="Disable API fallback translation for cues missing from the FI-EN package.",
    )
    parser.add_argument(
        "--cue-anchor",
        choices=("manifest", "asr"),
        default="manifest",
        help="Subtitle cue anchoring mode (manifest is recommended baseline).",
    )
    parser.add_argument(
        "--timing-source",
        choices=("tts_text", "asr"),
        default="tts_text",
        help="Cue timing source (tts_text is recommended baseline).",
    )
    parser.add_argument(
        "--clean-run",
        action="store_true",
        help="Delete existing section outputs/caches first, then regenerate from scratch.",
    )
    parser.add_argument("--keep-intermediate", action="store_true", help="Keep transcript/json/ass intermediates")
    args = parser.parse_args()

    root = Path(args.project_root).expanduser().resolve()
    scripts = root / "scripts"
    study_packages = root / "study_packages"
    media = root / "media"

    section_key = f"{args.section_id}_{args.section_slug}"
    fi_en_package = Path(args.fi_en_package).expanduser().resolve()
    if not fi_en_package.exists():
        raise FileNotFoundError(f"fi_en package not found: {fi_en_package}")

    section_media_root = media / section_key
    listening_compact_md = study_packages / f"{section_key}_listening_compact.fi.md"
    audio_dir = section_media_root / "audio_fi_listening_compact"
    manifest_path = audio_dir / "manifest.json"
    concat_list = audio_dir / "concat_list.txt"
    merged_mp3 = section_media_root / f"{section_key}_listening_compact_fi.mp3"

    final_dir = media / "final_sections" / section_key
    final_base = final_dir / section_key
    final_mp3 = final_base.with_suffix(".mp3")
    final_mp4 = final_base.with_suffix(".karaoke.mp4")

    if args.clean_run:
        print(f"Clean run: removing old artifacts for {section_key}")
        if audio_dir.exists():
            shutil.rmtree(audio_dir)
        if final_dir.exists():
            shutil.rmtree(final_dir)
        for path in (merged_mp3, listening_compact_md):
            if path.exists():
                path.unlink()

    section_media_root.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)

    # 1) Build compact listening package (FI)
    run(
        [
            "python3",
            str(scripts / "build_fi_listening_package.py"),
            "--source-md",
            str(Path(args.source_md).expanduser().resolve()),
            "--section",
            args.section_title,
            "--fi-en-package",
            str(fi_en_package),
            "--out",
            str(listening_compact_md),
        ]
    )

    # 2) Build TTS clips manifest
    tts_cmd = [
        "python3",
        str(scripts / "generate_fi_tts_from_package.py"),
        "--input",
        str(listening_compact_md),
        "--out-dir",
        str(audio_dir),
        "--manifest",
        str(manifest_path),
        "--model",
        "gpt-4o-mini-tts",
        "--voice",
        "alloy",
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
        "--language",
        "fi",
        "--instructions",
        "Puhu luonnollista suomea. Kayta vain suomea. Lue numerot, paivamaarat, kellonajat ja lyhenteet suomeksi luonnollisessa muodossa.",
        "--format",
        "mp3",
        "--sleep-ms",
        "120",
        "--dialog-role-cues",
        *(["--random-dialog-google-voices"] if args.random_dialog_google_voices else []),
    ]
    # Backward-compatible env override.
    env_backend = str(os.environ.get("YKI_TTS_BACKEND", "")).strip().lower()
    if env_backend in {"openai", "say", "edge_tts"}:
        i = tts_cmd.index("--tts-backend")
        tts_cmd[i + 1] = env_backend
    elif str(os.environ.get("YKI_TTS_LOCAL_ONLY", "")).strip().lower() in {"1", "true", "yes", "on"}:
        i = tts_cmd.index("--tts-backend")
        tts_cmd[i + 1] = "say"
    if args.force_audio:
        tts_cmd.append("--force")
    run(tts_cmd)

    # 3) Merge section mp3 from manifest order
    if args.force_audio or not merged_mp3.exists():
        write_concat_list(manifest_path, concat_list)
        run(
            [
                "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-fflags",
                "+genpts",
                "-i",
                str(concat_list),
                "-ar",
                "24000",
                "-ac",
                "1",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "160k",
                str(merged_mp3),
            ]
        )

    # 4) Final mp3 copy
    shutil.copy2(merged_mp3, final_mp3)

    # 5) Render dual-language karaoke video from package translations
    # (no re-translation) (skip if already present unless forced)
    if args.force_video or not final_mp4.exists():
        karaoke_cmd = [
            "python3",
            str(scripts / "render_dual_karaoke_from_package.py"),
            "--audio",
            str(final_mp3),
            "--output-prefix",
            str(final_base),
            "--fi-en-package",
            str(fi_en_package),
            "--section-manifest",
            str(manifest_path),
            "--language",
            "fi",
            "--split-mode",
            "semantic",
            "--timing-source",
            args.timing_source,
            "--cue-anchor",
            args.cue_anchor,
        ]
        if args.force_transcribe:
            karaoke_cmd.extend(["--force-transcribe"])
        if not args.disable_api_fallback:
            karaoke_cmd.extend(["--allow-api-fallback"])
        run(karaoke_cmd)
    else:
        print(f"Skip video render (already exists): {final_mp4}")

    if not args.keep_intermediate:
        cleanup_intermediates(final_base)

    print("\nDone")
    print("Final MP3:", final_mp3)
    print("Final MP4:", final_mp4)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
