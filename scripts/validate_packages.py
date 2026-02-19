#!/usr/bin/env python3
"""Validate fi_en_package.md files for sentence count alignment.

Uses the SAME sentence splitting logic as the karaoke renderer
(render_dialog_karaoke.py:split_sentences) to avoid false positives.

Usage:
    python scripts/validate_packages.py learners/linh          # all files for a learner
    python scripts/validate_packages.py learners/linh/dialogues/li_dia_04  # single dialogue
    python scripts/validate_packages.py --all                  # all learners
"""

import argparse
import re
import sys
from pathlib import Path


def split_sentences(text: str) -> list[str]:
    """Split text into sentences on . ! ? followed by space or end.

    This is the canonical logic from render_dialog_karaoke.py:202.
    DO NOT modify without also updating the renderer.
    """
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


TURN_RE = re.compile(r"^- \*\*([AB])\*\*:\s*(.*)")
# Match only the dialogue section headers, NOT context/konteksti lines
FI_HEADER_RE = re.compile(r"^\*\*FI Koko mallidialogi")
EN_HEADER_RE = re.compile(r"^\*\*EN Full")


def extract_turns(lines: list[str], start_re: re.Pattern) -> list[tuple[str, str]]:
    """Extract speaker turns from a section starting with the given header pattern."""
    turns = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if start_re.match(stripped):
            in_section = True
            continue
        if in_section:
            m = TURN_RE.match(stripped)
            if m:
                turns.append((m.group(1), m.group(2).strip()))
            elif stripped.startswith("**") and turns:
                # Hit next section header after collecting turns
                break
    return turns


def validate_file(path: Path) -> list[str]:
    """Validate a single fi_en_package.md file. Returns list of error strings."""
    errors = []
    lines = path.read_text(encoding="utf-8").splitlines()

    fi_turns = extract_turns(lines, FI_HEADER_RE)
    en_turns = extract_turns(lines, EN_HEADER_RE)

    if not fi_turns:
        errors.append(f"  No FI turns found")
        return errors
    if not en_turns:
        errors.append(f"  No EN turns found")
        return errors

    if len(fi_turns) != len(en_turns):
        errors.append(f"  Turn count mismatch: FI={len(fi_turns)}, EN={len(en_turns)}")
        return errors

    for i, ((fi_spk, fi_text), (en_spk, en_text)) in enumerate(zip(fi_turns, en_turns)):
        fi_sents = split_sentences(fi_text)
        en_sents = split_sentences(en_text)
        if len(fi_sents) != len(en_sents):
            errors.append(
                f"  Turn {i+1} ({fi_spk}): FI={len(fi_sents)} sentences, EN={len(en_sents)}"
            )

    return errors


def find_packages(root: Path) -> list[Path]:
    """Find all fi_en_package.md files under root."""
    if root.is_file() and root.name == "fi_en_package.md":
        return [root]
    return sorted(root.rglob("fi_en_package.md"))


def main():
    parser = argparse.ArgumentParser(description="Validate fi_en_package.md sentence counts")
    parser.add_argument("path", nargs="?", help="Learner dir, dialogue dir, or --all")
    parser.add_argument("--all", action="store_true", help="Validate all learners")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent

    if args.all:
        search_root = project_root / "learners"
    elif args.path:
        search_root = Path(args.path)
        if not search_root.is_absolute():
            search_root = project_root / search_root
    else:
        parser.error("Provide a path or --all")
        return

    packages = find_packages(search_root)
    if not packages:
        print(f"No fi_en_package.md files found under {search_root}")
        sys.exit(1)

    total_errors = 0
    for pkg in packages:
        dialogue_id = pkg.parent.name
        errors = validate_file(pkg)
        if errors:
            print(f"{dialogue_id}:")
            for e in errors:
                print(e)
            total_errors += len(errors)

    print(f"\nChecked {len(packages)} files: ", end="")
    if total_errors == 0:
        print("ALL PASS")
    else:
        print(f"{total_errors} issues found")
        sys.exit(1)


if __name__ == "__main__":
    main()
