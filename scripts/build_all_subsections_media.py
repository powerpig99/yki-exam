#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


PACKAGE_RE = re.compile(r"^(\d{2}_[a-z0-9_]+)_complete_package\.fi_en\.md$")


@dataclass
class Section:
    key: str
    fi_en_package: Path
    fi_listening_compact: Path


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def discover_sections(study_packages_dir: Path) -> list[Section]:
    out: list[Section] = []
    for p in sorted(study_packages_dir.glob("*_complete_package.fi_en.md")):
        m = PACKAGE_RE.match(p.name)
        if not m:
            continue
        key = m.group(1)
        out.append(
            Section(
                key=key,
                fi_en_package=p.resolve(),
                fi_listening_compact=(study_packages_dir / f"{key}_listening_compact.fi.md").resolve(),
            )
        )
    return out


def filter_sections(sections: list[Section], selector_csv: str) -> list[Section]:
    raw = [x.strip() for x in selector_csv.split(",") if x.strip()]
    if not raw:
        return sections
    chosen: list[Section] = []
    for sec in sections:
        sid = sec.key.split("_", 1)[0]
        if sec.key in raw or sid in raw:
            chosen.append(sec)
    return chosen


def builder_paths(scripts_dir: Path) -> dict[str, Path]:
    return {
        "reagointi": (scripts_dir / "build_reagointi_dialog_media.py").resolve(),
        "kertominen": (scripts_dir / "build_kertominen_dialog_media.py").resolve(),
        "mielipide": (scripts_dir / "build_mielipide_dialog_media.py").resolve(),
    }


def subsection_output(subsections_dir: Path, section_key: str, name: str) -> tuple[Path, Path]:
    out_dir = (subsections_dir / f"{section_key}_{name}_dialog").resolve()
    mp4 = (out_dir / f"{name}.karaoke.mp4").resolve()
    return out_dir, mp4


def main() -> int:
    p = argparse.ArgumentParser(description="Batch-build subsection media (Reagointi/Kertominen/Mielipide).")
    p.add_argument("--project-root", default=".")
    p.add_argument(
        "--sections",
        default="",
        help="Comma-separated section keys or IDs, e.g. 02,03,07 or 03_luonto_ja_ymparisto",
    )
    p.add_argument("--force", action="store_true", help="Regenerate even if final subsection mp4 exists.")
    p.add_argument("--tts-backend", choices=("openai", "edge_tts", "say", "google_chirp"), default="google_chirp")
    p.add_argument("--edge-voice", default="fi-FI-NooraNeural")
    p.add_argument("--say-voice", default="")
    p.add_argument("--google-api-key-env", default="GOOGLE_API_KEY")
    p.add_argument("--google-language-code", default="fi-FI")
    p.add_argument("--google-voice", default="")
    p.add_argument(
        "--dialog-context-backend",
        choices=("auto", "openai", "edge_tts", "say", "google_chirp"),
        default="google_chirp",
    )
    p.add_argument("--dialog-context-voice", default="")
    p.add_argument("--random-dialog-google-voices", action="store_true")
    p.add_argument("--self-cue-hz", type=int, default=0)
    p.add_argument("--friend-cue-hz", type=int, default=0)
    args = p.parse_args()

    root = Path(args.project_root).expanduser().resolve()
    scripts_dir = (root / "scripts").resolve()
    study_packages_dir = (root / "study_packages").resolve()
    subsections_dir = (root / "media" / "subsections").resolve()
    subsections_dir.mkdir(parents=True, exist_ok=True)

    builders = builder_paths(scripts_dir)
    for name, path in builders.items():
        if not path.exists():
            raise FileNotFoundError(f"Missing builder for {name}: {path}")

    sections = discover_sections(study_packages_dir)
    sections = filter_sections(sections, args.sections)
    if not sections:
        raise RuntimeError("No matching section packages found.")

    ok: list[str] = []
    fail: list[str] = []
    skip: list[str] = []

    for sec in sections:
        print(f"\n=== Section {sec.key} ===", flush=True)

        jobs: list[tuple[str, list[str]]] = []

        out_rea, mp4_rea = subsection_output(subsections_dir, sec.key, "reagointi")
        if not args.force and mp4_rea.exists():
            skip.append(f"{sec.key}: reagointi")
        else:
            jobs.append(
                (
                    "reagointi",
                    [
                        "python3",
                        str(builders["reagointi"]),
                        "--fi-en-package",
                        str(sec.fi_en_package),
                        "--out-dir",
                        str(out_rea),
                        "--title-fi",
                        "Reagointi",
                        "--title-en",
                        "Reaction exercises",
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
                        "--dialog-context-backend",
                        args.dialog_context_backend,
                        "--dialog-context-voice",
                        args.dialog_context_voice,
                        "--self-cue-hz",
                        str(args.self_cue_hz),
                        "--friend-cue-hz",
                        str(args.friend_cue_hz),
                        "--clean",
                        *(["--random-dialog-google-voices"] if args.random_dialog_google_voices else []),
                    ],
                )
            )

        out_ker, mp4_ker = subsection_output(subsections_dir, sec.key, "kertominen")
        if not args.force and mp4_ker.exists():
            skip.append(f"{sec.key}: kertominen")
        else:
            jobs.append(
                (
                    "kertominen",
                    [
                        "python3",
                        str(builders["kertominen"]),
                        "--fi-en-package",
                        str(sec.fi_en_package),
                        "--fi-listening-compact",
                        str(sec.fi_listening_compact),
                        "--out-dir",
                        str(out_ker),
                        "--title-fi",
                        "Kertominen",
                        "--title-en",
                        "Storytelling",
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
                        "--dialog-context-backend",
                        args.dialog_context_backend,
                        "--dialog-context-voice",
                        args.dialog_context_voice,
                        "--self-cue-hz",
                        str(args.self_cue_hz),
                        "--friend-cue-hz",
                        str(args.friend_cue_hz),
                        "--clean",
                        *(["--random-dialog-google-voices"] if args.random_dialog_google_voices else []),
                    ],
                )
            )

        out_mie, mp4_mie = subsection_output(subsections_dir, sec.key, "mielipide")
        if not args.force and mp4_mie.exists():
            skip.append(f"{sec.key}: mielipide")
        else:
            jobs.append(
                (
                    "mielipide",
                    [
                        "python3",
                        str(builders["mielipide"]),
                        "--fi-en-package",
                        str(sec.fi_en_package),
                        "--out-dir",
                        str(out_mie),
                        "--title-fi",
                        "Mielipide",
                        "--title-en",
                        "Opinion task",
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
                        "--dialog-context-backend",
                        args.dialog_context_backend,
                        "--dialog-context-voice",
                        args.dialog_context_voice,
                        "--self-cue-hz",
                        str(args.self_cue_hz),
                        "--friend-cue-hz",
                        str(args.friend_cue_hz),
                        "--clean",
                        *(["--random-dialog-google-voices"] if args.random_dialog_google_voices else []),
                    ],
                )
            )

        for name, cmd in jobs:
            try:
                run(cmd)
                ok.append(f"{sec.key}: {name}")
            except subprocess.CalledProcessError as exc:
                fail.append(f"{sec.key}: {name} (exit={exc.returncode})")

    print("\n=== Summary ===", flush=True)
    print(f"ok: {len(ok)}", flush=True)
    print(f"skip: {len(skip)}", flush=True)
    print(f"fail: {len(fail)}", flush=True)
    if ok:
        for item in ok:
            print(f"  OK   {item}", flush=True)
    if skip:
        for item in skip:
            print(f"  SKIP {item}", flush=True)
    if fail:
        for item in fail:
            print(f"  FAIL {item}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
