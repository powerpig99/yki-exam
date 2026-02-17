#!/usr/bin/env python3
"""Render karaoke video for YKI dialogues.

Reads manifest.json + fi_en_package.md, generates ASS subtitles with
karaoke word timing and English translations, renders 9:16 vertical
MP4 (1080x1920) with dark background and speaker color-coding.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path


# --- ASS helpers ---

def format_ass_time(seconds: float) -> str:
    """Format seconds to ASS time: h:mm:ss.cs"""
    s = max(0.0, seconds)
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    cs = int(round((sec - int(sec)) * 100))
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{int(sec):02d}.{cs:02d}"


def escape_ass(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


# --- Parsing fi_en_package.md ---

CONTEXT_FI_RE = re.compile(r"^\*\*FI Konteksti:\*\*\s*(.+)$")
CONTEXT_EN_RE = re.compile(r"^\*\*EN Context:\*\*\s*(.+)$")
FI_DIALOG_RE = re.compile(r"^\*\*FI Koko mallidialogi:\*\*")
EN_DIALOG_RE = re.compile(r"^\*\*EN Full (?:sample )?dialogue:\*\*")
TURN_RE = re.compile(r"^-\s+\*\*([AB])\*\*:\s*(.+)$")


def parse_fi_en_package(path: Path) -> dict:
    """Parse fi_en_package.md → {context_fi, context_en, fi_turns, en_turns}."""
    lines = path.read_text(encoding="utf-8").splitlines()

    context_fi = ""
    context_en = ""
    fi_turns: list[tuple[str, str]] = []
    en_turns: list[tuple[str, str]] = []
    section = None

    for line in lines:
        stripped = line.strip()

        m = CONTEXT_FI_RE.match(stripped)
        if m:
            context_fi = m.group(1).strip()
            continue
        m = CONTEXT_EN_RE.match(stripped)
        if m:
            context_en = m.group(1).strip()
            continue

        if FI_DIALOG_RE.match(stripped):
            section = "fi"
            continue
        if EN_DIALOG_RE.match(stripped):
            section = "en"
            continue
        if stripped.startswith("####"):
            section = None
            continue

        m = TURN_RE.match(stripped)
        if m:
            if section == "fi":
                fi_turns.append((m.group(1), m.group(2).strip()))
            elif section == "en":
                en_turns.append((m.group(1), m.group(2).strip()))

    return {
        "context_fi": context_fi,
        "context_en": context_en,
        "fi_turns": fi_turns,
        "en_turns": en_turns,
    }


# --- Karaoke word timing ---

def build_karaoke_words(text: str, start: float, end: float) -> list[dict]:
    """Distribute time across words proportionally by character length."""
    tokens = text.split()
    if not tokens or end <= start:
        return []

    weights = []
    for tok in tokens:
        if all(c in ".,!?;:-…\"'" for c in tok):
            weights.append(0.25)
        else:
            weights.append(max(1.0, len(tok) / 3.0))

    total_weight = sum(weights) or 1.0
    dur = end - start

    out = []
    t = start
    for i, tok in enumerate(tokens):
        if i == len(tokens) - 1:
            t_next = end
        else:
            t_next = t + dur * (weights[i] / total_weight)
        if t_next <= t:
            t_next = min(end, t + 0.05)
        out.append({"word": tok, "start": t, "end": t_next})
        t = t_next
    return out


def karaoke_ass_text(words: list[dict], max_chars: int = 32) -> str:
    r"""Build karaoke ASS text with \k tags and line breaks for long text."""
    if not words:
        return ""

    parts = []
    line_len = 0

    for i, w in enumerate(words):
        dur_cs = max(1, int(round((w["end"] - w["start"]) * 100)))
        word = escape_ass(w["word"])
        word_len = len(w["word"])

        if i == 0:
            parts.append(f"{{\\kf{dur_cs}}}{word}")
            line_len = word_len
        else:
            new_len = line_len + 1 + word_len
            if new_len > max_chars:
                parts.append(f"\\N{{\\kf{dur_cs}}}{word}")
                line_len = word_len
            else:
                parts.append(f"{{\\kf{dur_cs}}} {word}")
                line_len = new_len

    return "".join(parts)


# --- Translation text wrapping ---

def wrap_for_ass(text: str, max_chars: int = 42) -> str:
    """Wrap EN text into multiple lines for ASS, returns escaped text with \\N."""
    words = text.split()
    lines: list[list[str]] = [[]]
    line_len = 0

    for word in words:
        new_len = line_len + len(word) + (1 if lines[-1] else 0)
        if new_len > max_chars and lines[-1]:
            lines.append([word])
            line_len = len(word)
        else:
            lines[-1].append(word)
            line_len = new_len

    escaped_lines = [escape_ass(" ".join(line)) for line in lines if line]
    return r"\N".join(escaped_lines)


# --- ASS generation ---

# Colors in ASS BGR format: &HAABBGGRR
# KNarrator: white highlight, dim grey unhighlighted
# KSpeakerA: gold/yellow highlight
# KSpeakerB: cyan/aqua highlight
ASS_HEADER = r"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: KNarrator,Arial,66,&H00FFFFFF,&H00666666,&H00202020,&H80000000,0,1,0,0,100,100,0,0,1,3,0,5,60,60,0,1
Style: KSpeakerA,Arial,78,&H0000CCFF,&H00444466,&H00101010,&H80000000,1,0,0,0,100,100,0,0,1,4,0,5,60,60,0,1
Style: KSpeakerB,Arial,78,&H00FFAA44,&H00444455,&H00101010,&H80000000,1,0,0,0,100,100,0,0,1,4,0,5,60,60,0,1
Style: Translation,Arial,48,&H00DDDDDD,&H00DDDDDD,&H00101010,&H64000000,0,0,0,0,100,100,0,0,1,3,0,2,60,60,80,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""

# Layout positions for 1080x1920
KARAOKE_Y = 850
TRANS_Y = 1550


def split_sentences(text: str) -> list[str]:
    """Split text into sentences on . ! ? followed by space or end."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def allocate_times(items: list[str], start: float, end: float) -> list[tuple[float, float]]:
    """Distribute a time window across items proportionally by character length."""
    if not items:
        return []
    weights = [max(1.0, len(s)) for s in items]
    total = sum(weights)
    dur = end - start
    times = []
    t = start
    for i, w in enumerate(weights):
        t_next = end if i == len(weights) - 1 else t + dur * (w / total)
        times.append((round(t, 3), round(t_next, 3)))
        t = t_next
    return times


def pair_sentences(fi_sents: list[str], en_text: str) -> list[str]:
    """Split EN text into sentences and pair with FI sentences by index.

    If counts differ, remaining EN sentences are merged into the last slot.
    """
    en_sents = split_sentences(en_text) if en_text else []
    if not en_sents:
        return [""] * len(fi_sents)

    n_fi = len(fi_sents)
    paired = [""] * n_fi

    for i in range(n_fi):
        if i < n_fi - 1 and i < len(en_sents):
            paired[i] = en_sents[i]
        else:
            # Last FI sentence gets all remaining EN sentences
            paired[i] = " ".join(en_sents[i:])
            break

    return paired


def build_events(segments: list[dict], en_map: dict[int, str], context_en: str) -> str:
    """Build ASS Dialogue lines — FI and EN synced sentence by sentence."""
    events = []
    speech_idx = 0

    for seg in segments:
        if seg["type"] != "speech":
            continue

        speaker = seg["speaker"]
        text_fi = seg["text_fi"]
        seg_start = seg["start_sec"]
        seg_end = seg["end_sec"]

        # Style
        if speaker == "narrator":
            style = "KNarrator"
        elif speaker == "A":
            style = "KSpeakerA"
        else:
            style = "KSpeakerB"

        # Split FI into sentences, allocate time
        fi_sents = split_sentences(text_fi)
        if not fi_sents:
            fi_sents = [text_fi]
        sent_times = allocate_times(fi_sents, seg_start, seg_end)

        # Pair EN sentences with FI sentences
        en_text = context_en if speaker == "narrator" else en_map.get(speech_idx, "")
        en_paired = pair_sentences(fi_sents, en_text)

        # One karaoke + translation event per sentence
        for j, (sent, (s_start, s_end)) in enumerate(zip(fi_sents, sent_times)):
            start_t = format_ass_time(s_start)
            end_t = format_ass_time(s_end)

            # FI karaoke
            words = build_karaoke_words(sent, s_start, s_end)
            k_text = karaoke_ass_text(words, max_chars=20)
            pos_k = f"\\an5\\pos(540,{KARAOKE_Y})"
            events.append(
                f"Dialogue: 0,{start_t},{end_t},{style},,0,0,0,,{{{pos_k}}}{k_text}"
            )

            # EN translation for this sentence
            en_sent = en_paired[j] if j < len(en_paired) else ""
            if en_sent:
                wrapped = wrap_for_ass(en_sent, max_chars=42)
                pos_t = f"\\an5\\pos(540,{TRANS_Y})"
                events.append(
                    f"Dialogue: 1,{start_t},{end_t},Translation,,0,0,0,,{{{pos_t}}}{wrapped}"
                )

        speech_idx += 1

    return "\n".join(events)


# --- Video rendering ---

def ffmpeg_binary() -> str:
    preferred = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
    if preferred.exists():
        return str(preferred)
    return shutil.which("ffmpeg") or ""


def render_video(audio_path: Path, ass_path: Path, out_path: Path, duration: float) -> None:
    ffmpeg = ffmpeg_binary()
    escaped_ass = str(ass_path).replace("\\", "\\\\").replace(":", "\\:").replace("'", r"\'")

    cmd = [
        ffmpeg,
        "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi",
        "-i", f"color=c=0x0f172a:s=1080x1920:r=30:d={duration:.3f}",
        "-i", str(audio_path),
        "-vf", f"ass='{escaped_ass}'",
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "160k",
        "-movflags", "+faststart",
        "-shortest",
        str(out_path),
    ]
    proc = subprocess.run(cmd, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg render failed: {(proc.stderr or '')[-800:]}")


# --- Main pipeline ---

def render_dialogue(dialogue_dir: Path) -> Path:
    """Render karaoke video for one dialogue. Returns output path."""
    manifest_path = dialogue_dir / "audio" / "manifest.json"
    pkg_path = dialogue_dir / "fi_en_package.md"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pkg = parse_fi_en_package(pkg_path)

    # Build EN translation map: speech_index → EN text
    # Speech segments in order: narrator, A, B, A, B, ...
    # EN turns from package: A, B, A, B, ... (same order)
    en_map: dict[int, str] = {}
    speech_idx = 0
    en_idx = 0
    for seg in manifest["segments"]:
        if seg["type"] != "speech":
            continue
        if seg["speaker"] != "narrator":
            if en_idx < len(pkg["en_turns"]):
                en_map[speech_idx] = pkg["en_turns"][en_idx][1]
                en_idx += 1
        speech_idx += 1

    # Generate ASS
    events = build_events(manifest["segments"], en_map, pkg["context_en"])
    video_dir = dialogue_dir / "video"
    video_dir.mkdir(parents=True, exist_ok=True)
    ass_path = video_dir / "dialogue.karaoke.ass"
    ass_path.write_text(ASS_HEADER + events + "\n", encoding="utf-8")
    print(f"  ASS: {ass_path.name}")

    # Render video
    audio_path = dialogue_dir / "audio" / manifest["merged_file"]
    out_path = video_dir / "dialogue.karaoke.mp4"
    duration = manifest["total_duration_sec"] + 1.0
    print(f"  Rendering {out_path.name} ({duration:.1f}s)...")
    render_video(audio_path, ass_path, out_path, duration)

    return out_path


def main():
    parser = argparse.ArgumentParser(description="Render karaoke videos for YKI dialogues")
    parser.add_argument("--dialogue-dir", type=Path, help="Single dialogue dir")
    parser.add_argument(
        "--dialogues-root", type=Path,
        default=Path("dialog_practice/dialogues"),
    )
    parser.add_argument("--only", type=str, default=None, help="Comma-separated IDs")
    parser.add_argument("--force", action="store_true", help="Regenerate videos")
    args = parser.parse_args()

    if args.dialogue_dir:
        dirs = [args.dialogue_dir]
    else:
        root = args.dialogues_root
        if args.only:
            ids = [x.strip() for x in args.only.split(",")]
            dirs = [root / i for i in ids]
        else:
            dirs = sorted(
                d for d in root.iterdir()
                if d.is_dir() and (d / "audio" / "manifest.json").exists()
            )

    total = len(dirs)
    done = 0
    skipped = 0
    errors = 0

    for i, d in enumerate(dirs, 1):
        dia_id = d.name
        video_path = d / "video" / "dialogue.karaoke.mp4"
        if video_path.exists() and not args.force:
            print(f"[{i}/{total}] {dia_id} — skipping (video exists)")
            skipped += 1
            continue

        print(f"\n[{i}/{total}] {dia_id}")
        try:
            out = render_dialogue(d)
            size_mb = out.stat().st_size / (1024 * 1024)
            print(f"  Done: {size_mb:.1f} MB")
            done += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            errors += 1

    print(f"\n{'=' * 50}")
    print(f"Done: {done} rendered, {skipped} skipped, {errors} errors (of {total})")


if __name__ == "__main__":
    main()
