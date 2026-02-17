#!/usr/bin/env python3
"""Parse YKI exam dialogues from markdown files into structured JSON.

Reads blockquote-formatted dialogues from 01-07 source markdown files,
outputs one directory per dialogue with dialogue.json and scaffold fi_en_package.md.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Section slug mapping (filename stem → short slug for directory naming)
SECTION_SLUGS = {
    "01-ihminen-ja-lahipiiri": "01",
    "02-arkielama": "02",
    "03-luonto-ja-ymparisto": "03",
    "04-terveys-ja-hyvinvointi": "04",
    "05-tyo-ja-koulutus": "05",
    "06-vapaa-aika": "06",
    "07-yhteiskunta": "07",
}

# Regex patterns
DIALOGUE_HEADING_RE = re.compile(r"^####\s+MALLI:\s*Dialogi\s+(\d+)\s*$")
TILANNE_RE = re.compile(r"^\*\*Tilanne:\*\*\s*(.+)$")
BLOCKQUOTE_RE = re.compile(r"^>\s*(.*)$")
SPEAKER_RE = re.compile(r"^\*\*(.+?):\*\*\s*(.*)$")
TIME_RE = re.compile(r"[—–-]\s*(\d+)\s*sek\s*$")


def parse_turn_text(raw: str) -> tuple[str, int | None]:
    """Extract the text/instruction and optional time limit from a turn."""
    m = TIME_RE.search(raw)
    if m:
        time_sek = int(m.group(1))
        text = raw[: m.start()].rstrip(" —–-").strip()
        return text, time_sek
    return raw.strip(), None


def is_instruction(text: str) -> bool:
    """Check if a Sinä line is an instruction (not actual speech)."""
    # Sinä lines are always instructions in the source — they contain
    # parenthesized directions or imperative Finnish like "Vastaa", "Kerro" etc.
    return True


def parse_dialogues_from_file(filepath: Path) -> list[dict]:
    """Parse all dialogues from one markdown file."""
    lines = filepath.read_text(encoding="utf-8").splitlines()
    stem = filepath.stem
    section = SECTION_SLUGS.get(stem, stem[:2])

    dialogues = []
    current_num = None
    current_tilanne = None
    current_turns: list[dict] = []
    in_blockquote = False

    def flush():
        nonlocal current_num, current_tilanne, current_turns
        if current_num is not None and current_turns:
            dia_id = f"{section}_dia_{current_num:02d}"
            dialogues.append(
                {
                    "id": dia_id,
                    "section": section,
                    "dialogue_num": current_num,
                    "tilanne": current_tilanne or "",
                    "turns": current_turns,
                }
            )
        current_num = None
        current_tilanne = None
        current_turns = []

    for line in lines:
        # Check for dialogue heading
        m = DIALOGUE_HEADING_RE.match(line.strip())
        if m:
            flush()
            current_num = int(m.group(1))
            in_blockquote = False
            continue

        if current_num is None:
            continue

        # Check for Tilanne line
        m = TILANNE_RE.match(line.strip())
        if m:
            current_tilanne = m.group(1).strip()
            continue

        # Parse blockquote lines
        m = BLOCKQUOTE_RE.match(line)
        if m:
            inner = m.group(1).strip()
            if not inner:
                # Empty blockquote line (separator)
                continue
            in_blockquote = True

            # Check if this is a speaker line
            sm = SPEAKER_RE.match(inner)
            if sm:
                speaker = sm.group(1).strip()
                raw_text = sm.group(2).strip()
                text, time_sek = parse_turn_text(raw_text)
                is_sina = speaker.lower() == "sinä"

                turn = {
                    "speaker": speaker,
                    "is_sina": is_sina,
                    "text": text,
                }
                if time_sek is not None:
                    turn["time_sek"] = time_sek
                if is_sina:
                    turn["instruction"] = text
                current_turns.append(turn)
            else:
                # Continuation of previous turn's text
                if current_turns:
                    prev = current_turns[-1]
                    text, time_sek = parse_turn_text(inner)
                    prev["text"] = (prev["text"] + " " + text).strip()
                    if time_sek is not None:
                        prev["time_sek"] = time_sek
        else:
            # Non-blockquote line after blockquote section
            if line.strip() == "---":
                in_blockquote = False

    flush()
    return dialogues


def write_dialogue_json(dialogue: dict, out_dir: Path) -> None:
    """Write dialogue.json for one dialogue."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "dialogue.json", "w", encoding="utf-8") as f:
        json.dump(dialogue, f, ensure_ascii=False, indent=2)


def write_scaffold(dialogue: dict, out_dir: Path) -> None:
    """Write scaffold fi_en_package.md with blank Sinä slots."""
    lines = []
    dia_id = dialogue["id"]
    tilanne = dialogue["tilanne"]

    lines.append(f"#### {dia_id}")
    lines.append(f"**FI Konteksti:** {tilanne}")
    lines.append(f"**EN Context:** <!-- TODO: translate -->")
    lines.append("")
    lines.append("**FI Koko mallidialogi:**")

    speaker_label = {}  # map speaker name → A or B
    label_counter = iter("AB")

    for turn in dialogue["turns"]:
        speaker = turn["speaker"]
        if speaker not in speaker_label:
            speaker_label[speaker] = next(label_counter, "?")
        label = speaker_label[speaker]

        if turn["is_sina"]:
            instruction = turn.get("instruction", "")
            time_sek = turn.get("time_sek", "")
            lines.append(
                f"- **{label}** (Sinä): <!-- TODO: sample answer --> "
                f"({instruction}) [{time_sek}s]"
            )
        else:
            lines.append(f"- **{label}**: {turn['text']}")

    lines.append("")
    lines.append("**EN Full dialogue:**")
    for turn in dialogue["turns"]:
        speaker = turn["speaker"]
        label = speaker_label[speaker]
        lines.append(f"- **{label}**: <!-- TODO: translate -->")

    lines.append("")

    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "fi_en_package.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description="Parse YKI dialogues from markdown")
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path("."),
        help="Directory containing 01-07 markdown files",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("dialog_practice/dialogues"),
        help="Output directory for parsed dialogues",
    )
    parser.add_argument(
        "--only",
        type=str,
        default=None,
        help="Comma-separated dialogue IDs to process (e.g. 01_dia_01,01_dia_02)",
    )
    args = parser.parse_args()

    source_files = sorted(args.source_dir.glob("[0-9][0-9]-*.md"))
    if not source_files:
        print(f"No source files found in {args.source_dir}", file=sys.stderr)
        sys.exit(1)

    only_ids = set(args.only.split(",")) if args.only else None

    all_dialogues = []
    for sf in source_files:
        dialogues = parse_dialogues_from_file(sf)
        all_dialogues.extend(dialogues)
        print(f"  {sf.name}: {len(dialogues)} dialogues")

    print(f"\nTotal: {len(all_dialogues)} dialogues parsed")

    written = 0
    for dia in all_dialogues:
        if only_ids and dia["id"] not in only_ids:
            continue
        out_dir = args.out_dir / dia["id"]
        write_dialogue_json(dia, out_dir)
        write_scaffold(dia, out_dir)
        written += 1

    print(f"Written: {written} dialogue directories to {args.out_dir}")


if __name__ == "__main__":
    main()
