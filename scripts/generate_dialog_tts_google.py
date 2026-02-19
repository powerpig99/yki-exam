#!/usr/bin/env python3
"""Generate per-turn TTS audio for YKI dialogues using Google Chirp 3 HD.

Reads fi_en_package.md for Finnish text, generates audio per turn with
3 distinct voices (narrator, Speaker A, Speaker B) that rotate across
dialogues, concatenates into merged.mp3, and writes manifest.json.

Requires: GOOGLE_API_KEY in environment.
Run with: .venv/bin/python3 scripts/generate_dialog_tts_google.py --only <id> --force
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

MODEL_ID = "Chirp3-HD"

# Voice pool: all 30 fi-FI Chirp3-HD voices
# Gender: 1 = MALE, 2 = FEMALE
VOICE_POOL = [
    {"name": "Achernar", "voice_id": "fi-FI-Chirp3-HD-Achernar", "gender": "female"},
    {"name": "Achird", "voice_id": "fi-FI-Chirp3-HD-Achird", "gender": "male"},
    {"name": "Algenib", "voice_id": "fi-FI-Chirp3-HD-Algenib", "gender": "male"},
    # {"name": "Algieba", "voice_id": "fi-FI-Chirp3-HD-Algieba", "gender": "male"},  # removed: glitchy audio
    {"name": "Alnilam", "voice_id": "fi-FI-Chirp3-HD-Alnilam", "gender": "male"},
    {"name": "Aoede", "voice_id": "fi-FI-Chirp3-HD-Aoede", "gender": "female"},
    {"name": "Autonoe", "voice_id": "fi-FI-Chirp3-HD-Autonoe", "gender": "female"},
    {"name": "Callirrhoe", "voice_id": "fi-FI-Chirp3-HD-Callirrhoe", "gender": "female"},
    {"name": "Charon", "voice_id": "fi-FI-Chirp3-HD-Charon", "gender": "male"},
    {"name": "Despina", "voice_id": "fi-FI-Chirp3-HD-Despina", "gender": "female"},
    # {"name": "Enceladus", "voice_id": "fi-FI-Chirp3-HD-Enceladus", "gender": "male"},  # removed: glitchy audio
    {"name": "Erinome", "voice_id": "fi-FI-Chirp3-HD-Erinome", "gender": "female"},
    {"name": "Fenrir", "voice_id": "fi-FI-Chirp3-HD-Fenrir", "gender": "male"},
    {"name": "Gacrux", "voice_id": "fi-FI-Chirp3-HD-Gacrux", "gender": "female"},
    {"name": "Iapetus", "voice_id": "fi-FI-Chirp3-HD-Iapetus", "gender": "male"},
    {"name": "Kore", "voice_id": "fi-FI-Chirp3-HD-Kore", "gender": "female"},
    {"name": "Laomedeia", "voice_id": "fi-FI-Chirp3-HD-Laomedeia", "gender": "female"},
    {"name": "Leda", "voice_id": "fi-FI-Chirp3-HD-Leda", "gender": "female"},
    {"name": "Orus", "voice_id": "fi-FI-Chirp3-HD-Orus", "gender": "male"},
    {"name": "Puck", "voice_id": "fi-FI-Chirp3-HD-Puck", "gender": "male"},
    {"name": "Pulcherrima", "voice_id": "fi-FI-Chirp3-HD-Pulcherrima", "gender": "female"},
    {"name": "Rasalgethi", "voice_id": "fi-FI-Chirp3-HD-Rasalgethi", "gender": "male"},
    {"name": "Sadachbia", "voice_id": "fi-FI-Chirp3-HD-Sadachbia", "gender": "male"},
    # {"name": "Sadaltager", "voice_id": "fi-FI-Chirp3-HD-Sadaltager", "gender": "male"},  # removed: glitchy audio
    {"name": "Schedar", "voice_id": "fi-FI-Chirp3-HD-Schedar", "gender": "male"},
    {"name": "Sulafat", "voice_id": "fi-FI-Chirp3-HD-Sulafat", "gender": "female"},
    {"name": "Umbriel", "voice_id": "fi-FI-Chirp3-HD-Umbriel", "gender": "male"},
    {"name": "Vindemiatrix", "voice_id": "fi-FI-Chirp3-HD-Vindemiatrix", "gender": "female"},
    {"name": "Zephyr", "voice_id": "fi-FI-Chirp3-HD-Zephyr", "gender": "female"},
    {"name": "Zubenelgenubi", "voice_id": "fi-FI-Chirp3-HD-Zubenelgenubi", "gender": "male"},
]


FEMALE_VOICES = [v for v in VOICE_POOL if v["gender"] == "female"]
MALE_VOICES = [v for v in VOICE_POOL if v["gender"] == "male"]


def pick_voices(
    dialogue_id: str,
    repick_role: str | None = None,
    learner_gender: str | None = None,
    learner_role: str = "B",
) -> tuple[dict, dict, dict]:
    """Randomly pick 3 distinct voices, seeded by dialogue ID for reproducibility.

    repick_role: "A", "B", or "narrator" — re-pick only that speaker's voice
    using a shifted seed, keeping the other two unchanged.

    learner_gender: "female", "male", or None — if set, the learner_role speaker
    gets a voice of this gender, the other speaker gets the opposite gender.
    Narrator stays random.

    learner_role: "A" or "B" — which speaker is the learner (default "B").
    Read from **Learner role:** in fi_en_package.md.
    """
    rng = random.Random(dialogue_id)

    if learner_gender:
        opposite = "male" if learner_gender == "female" else "female"
        learner_pool = FEMALE_VOICES if learner_gender == "female" else MALE_VOICES
        other_pool = MALE_VOICES if learner_gender == "female" else FEMALE_VOICES

        learner_voice = rng.choice(learner_pool)
        other_voice = rng.choice([v for v in other_pool if v["name"] != learner_voice["name"]])
        narrator = rng.choice([v for v in VOICE_POOL if v["name"] not in {learner_voice["name"], other_voice["name"]}])

        if learner_role.upper() == "A":
            va, vb = learner_voice, other_voice
        else:
            va, vb = other_voice, learner_voice
    else:
        chosen = rng.sample(VOICE_POOL, 3)
        narrator, va, vb = chosen[0], chosen[1], chosen[2]

    if repick_role:
        # Pick a replacement from the remaining pool (exclude already-chosen voices)
        used_names = {narrator["name"], va["name"], vb["name"]}
        remaining = [v for v in VOICE_POOL if v["name"] not in used_names]
        alt_rng = random.Random(f"{dialogue_id}_repick_{repick_role}")
        replacement = alt_rng.choice(remaining)
        if repick_role.upper() == "A":
            va = replacement
        elif repick_role.upper() == "B":
            vb = replacement
        elif repick_role.lower() == "narrator":
            narrator = replacement

    return narrator, va, vb


# --- Regexes for parsing fi_en_package.md ---
CONTEXT_RE = re.compile(r"^\*\*FI Konteksti:\*\*\s*(.+)$")
FI_DIALOG_HEADER_RE = re.compile(r"^\*\*FI Koko mallidialogi:\*\*")
TURN_RE = re.compile(r"^-\s+\*\*([AB])\*\*:\s*(.+)$")
LEARNER_ROLE_RE = re.compile(r"^\*\*Learner role:\*\*\s*([ABab])\s*$")


def ffmpeg_binary() -> str:
    preferred = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
    if preferred.exists():
        return str(preferred)
    return shutil.which("ffmpeg") or ""


def ffprobe_duration_seconds(path: Path) -> float:
    preferred = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffprobe")
    ffprobe = str(preferred) if preferred.exists() else (shutil.which("ffprobe") or "")
    if not ffprobe:
        return 0.0
    cmd = [
        ffprobe, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        out = subprocess.check_output(cmd, text=True).strip()
        return round(float(out), 3) if out else 0.0
    except Exception:
        return 0.0


def generate_silence(out_path: Path, duration_sec: float, sample_rate: int = 24000):
    """Generate a silent WAV file."""
    ffmpeg = ffmpeg_binary()
    cmd = [
        ffmpeg, "-y",
        "-f", "lavfi",
        "-i", f"anullsrc=r={sample_rate}:cl=mono",
        "-t", str(duration_sec),
        "-c:a", "pcm_s16le",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


EN_DIALOG_HEADER_RE = re.compile(r"^\*\*EN Full sample dialogue:\*\*")


def parse_fi_en_package(path: Path) -> tuple[str, list[tuple[str, str]], str]:
    """Parse fi_en_package.md returning (context, [(speaker, text), ...], learner_role).

    learner_role is "A" or "B" (default "B" if not specified in file).
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    context = ""
    turns: list[tuple[str, str]] = []
    learner_role = "B"  # default
    in_fi_dialog = False

    for line in lines:
        stripped = line.strip()
        m = LEARNER_ROLE_RE.match(stripped)
        if m:
            learner_role = m.group(1).upper()
            continue
        m = CONTEXT_RE.match(stripped)
        if m:
            context = m.group(1).strip()
            continue
        if FI_DIALOG_HEADER_RE.match(stripped):
            in_fi_dialog = True
            continue
        if in_fi_dialog and (stripped.startswith("**EN ") or stripped.startswith("####")):
            in_fi_dialog = False
            continue
        if in_fi_dialog:
            m = TURN_RE.match(stripped)
            if m:
                turns.append((m.group(1), m.group(2).strip()))

    return context, turns, learner_role


def _count_sentences(text: str) -> int:
    """Count sentences by splitting on terminal punctuation followed by whitespace.

    Matches the renderer's split_sentences logic so decimals like 0.375 or 4.2
    are not falsely counted as sentence boundaries.
    """
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return len([p for p in parts if p.strip()])


def validate_sentence_counts(path: Path) -> None:
    """Verify FI and EN turns have matching sentence counts.

    Mismatched counts cause karaoke EN subtitles to shift/disappear.
    Raises ValueError with details on which turns mismatch.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    fi_turns: list[tuple[str, str]] = []
    en_turns: list[tuple[str, str]] = []
    section = None  # "fi" or "en"

    for line in lines:
        stripped = line.strip()
        if FI_DIALOG_HEADER_RE.match(stripped):
            section = "fi"
            continue
        if EN_DIALOG_HEADER_RE.match(stripped):
            section = "en"
            continue
        if section and (stripped.startswith("####") or stripped == ""):
            if stripped.startswith("####"):
                section = None
            continue
        if section:
            m = TURN_RE.match(stripped)
            if m:
                target = fi_turns if section == "fi" else en_turns
                target.append((m.group(1), m.group(2).strip()))

    if len(fi_turns) != len(en_turns):
        raise ValueError(
            f"{path.name}: FI has {len(fi_turns)} turns, EN has {len(en_turns)}"
        )

    errors = []
    for i, ((fi_spk, fi_text), (en_spk, en_text)) in enumerate(zip(fi_turns, en_turns)):
        fi_count = _count_sentences(fi_text)
        en_count = _count_sentences(en_text)
        if fi_count != en_count:
            errors.append(
                f"  Turn {i+1} ({fi_spk}): FI={fi_count} EN={en_count}\n"
                f"    FI: {fi_text}\n"
                f"    EN: {en_text}"
            )
    if errors:
        raise ValueError(
            f"{path.name}: Sentence count mismatches:\n" + "\n".join(errors)
        )


def concat_audio_files(files: list[Path], out_file: Path) -> None:
    """Concatenate audio files with ffmpeg."""
    ffmpeg = ffmpeg_binary()
    with tempfile.TemporaryDirectory(prefix="tts_concat_") as td:
        list_file = Path(td) / "concat_list.txt"
        with list_file.open("w", encoding="utf-8") as f:
            for p in files:
                p_abs = str(p.resolve()).replace("'", "'\\''")
                f.write(f"file '{p_abs}'\n")

        out_ext = out_file.suffix.lower().lstrip(".")
        cmd = [
            ffmpeg, "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-ar", "24000", "-ac", "1",
        ]
        if out_ext in ("wav", "wave"):
            cmd.extend(["-c:a", "pcm_s16le"])
        else:
            cmd.extend(["-c:a", "libmp3lame", "-b:a", "192k"])
        cmd.append(str(out_file))
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {(proc.stderr or '')[-500:]}")


def google_tts(client, text: str, voice_id: str, out_path: Path) -> None:
    """Generate TTS audio via Google Chirp 3 HD and save as WAV.

    Chirp 3 HD exaggerates tone on exclamation marks, so we replace '!' with
    '.' before sending to the API. The original text in fi_en_package.md is
    kept intact for use with other TTS engines that handle '!' better.
    """
    from google.cloud import texttospeech

    # Flatten exclamations for Chirp — it over-emotes on '!'
    tts_text = text.replace("!", ".")

    synth_input = texttospeech.SynthesisInput(text=tts_text)

    resp = client.synthesize_speech(
        input=synth_input,
        voice=texttospeech.VoiceSelectionParams(
            language_code="fi-FI",
            name=voice_id,
        ),
        audio_config=texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            sample_rate_hertz=24000,
        ),
    )
    with open(out_path, "wb") as f:
        f.write(resp.audio_content)


def generate_dialogue_audio(
    dialogue_dir: Path,
    client,
    voice_narrator: dict,
    voice_a: dict,
    voice_b: dict,
    pause_after_narrator: float = 1.0,
    pause_between_turns: float = 0.6,
) -> dict:
    """Generate TTS for one dialogue directory. Returns manifest dict."""
    pkg_path = dialogue_dir / "fi_en_package.md"
    if not pkg_path.exists():
        raise FileNotFoundError(f"No fi_en_package.md in {dialogue_dir}")

    # Validate FI/EN sentence counts before generating audio
    validate_sentence_counts(pkg_path)

    context, turns, _learner_role = parse_fi_en_package(pkg_path)
    if not turns:
        raise ValueError(f"No turns found in {pkg_path}")

    audio_dir = dialogue_dir / "audio"
    if audio_dir.exists():
        for old in audio_dir.iterdir():
            if old.name == "manifest.json":
                continue
            old.unlink()
    audio_dir.mkdir(parents=True, exist_ok=True)

    dia_id = dialogue_dir.name
    segments: list[dict] = []
    turn_idx = 0

    print(f"  Voices: narrator={voice_narrator['name']}, A={voice_a['name']}, B={voice_b['name']}")

    # --- Narrator: context ---
    narrator_text = context
    narrator_file = audio_dir / f"turn_{turn_idx:03d}_narrator.wav"
    print(f"  [narrator] {narrator_text[:70]}...")
    t0 = time.time()
    google_tts(client, narrator_text, voice_narrator["voice_id"], narrator_file)
    elapsed = time.time() - t0
    narrator_dur = ffprobe_duration_seconds(narrator_file)
    print(f"    {elapsed:.1f}s api → {narrator_dur:.1f}s audio")

    segments.append({
        "type": "speech",
        "speaker": "narrator",
        "text_fi": narrator_text,
        "file": narrator_file.name,
        "duration_sec": narrator_dur,
    })
    segments.append({
        "type": "pause",
        "speaker": "",
        "text_fi": "",
        "file": "",
        "duration_sec": pause_after_narrator,
    })
    turn_idx += 1

    # --- Dialogue turns ---
    for speaker, text in turns:
        voice = voice_a if speaker == "A" else voice_b
        out_file = audio_dir / f"turn_{turn_idx:03d}_{speaker}.wav"
        label = f"[{speaker}:{voice['name']}]"
        print(f"  {label} {text[:60]}{'...' if len(text) > 60 else ''}")
        t0 = time.time()
        google_tts(client, text, voice["voice_id"], out_file)
        elapsed = time.time() - t0
        dur = ffprobe_duration_seconds(out_file)
        print(f"    {elapsed:.1f}s api → {dur:.1f}s audio")

        segments.append({
            "type": "speech",
            "speaker": speaker,
            "text_fi": text,
            "file": out_file.name,
            "duration_sec": dur,
        })
        segments.append({
            "type": "pause",
            "speaker": "",
            "text_fi": "",
            "file": "",
            "duration_sec": pause_between_turns,
        })
        turn_idx += 1

    # Remove trailing pause
    if segments and segments[-1]["type"] == "pause":
        segments.pop()

    # --- Generate silence files for pauses ---
    silence_cache: dict[str, Path] = {}
    for seg in segments:
        if seg["type"] != "pause":
            continue
        dur_key = f"{seg['duration_sec']:.1f}"
        if dur_key not in silence_cache:
            sil_path = audio_dir / f"silence_{dur_key}s.wav"
            if not sil_path.exists():
                generate_silence(sil_path, seg["duration_sec"])
            silence_cache[dur_key] = sil_path
        seg["file"] = silence_cache[dur_key].name

    # --- Concatenate all segments ---
    concat_files = [audio_dir / seg["file"] for seg in segments]
    merged_path = audio_dir / "merged.mp3"
    print(f"  Concatenating {len(concat_files)} segments...")
    concat_audio_files(concat_files, merged_path)
    total_dur = ffprobe_duration_seconds(merged_path)
    print(f"  → merged.mp3 ({total_dur:.1f}s)")

    # --- Compute absolute timing ---
    t = 0.0
    for seg in segments:
        seg["start_sec"] = round(t, 3)
        seg["end_sec"] = round(t + seg["duration_sec"], 3)
        t += seg["duration_sec"]

    manifest = {
        "dialogue_id": dia_id,
        "model": MODEL_ID,
        "voice_narrator": voice_narrator["name"],
        "voice_a": voice_a["name"],
        "voice_b": voice_b["name"],
        "voice_narrator_id": voice_narrator["voice_id"],
        "voice_a_id": voice_a["voice_id"],
        "voice_b_id": voice_b["voice_id"],
        "segments": segments,
        "total_duration_sec": round(total_dur, 3),
        "merged_file": "merged.mp3",
    }

    manifest_path = audio_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"  Manifest written: {manifest_path.name}")
    return manifest


DIALOGUE_SEARCH_ROOTS = [
    Path("dialog_practice/dialogues"),
    Path("learners"),
]


def _find_all(dialogue_id: str) -> list[Path]:
    """Find all directories matching dialogue_id across known locations."""
    results = []
    for search_root in DIALOGUE_SEARCH_ROOTS:
        if not search_root.exists():
            continue
        for match in search_root.rglob(dialogue_id):
            if match.is_dir():
                results.append(match)
    return results


def _infer_home(dialogue_id: str) -> Path:
    """Infer the home directory for a dialogue ID based on where siblings live.

    Looks for existing directories with the same prefix (e.g. 'xr' from 'xr_dia_01')
    to determine which dialogues root they belong to. Falls back to dialog_practice/.
    """
    prefix = dialogue_id.split("_")[0]  # e.g. "xr" from "xr_dia_01"

    # Check learner directories for matching prefix
    learners_dir = Path("learners")
    if learners_dir.exists():
        for learner_dir in sorted(learners_dir.iterdir()):
            dialogues_dir = learner_dir / "dialogues"
            if not dialogues_dir.is_dir():
                continue
            for existing in dialogues_dir.iterdir():
                if existing.is_dir() and existing.name.split("_")[0] == prefix:
                    return dialogues_dir

    # Check dialog_practice for matching prefix
    dp_dir = Path("dialog_practice/dialogues")
    if dp_dir.exists():
        for existing in dp_dir.iterdir():
            if existing.is_dir() and existing.name.split("_")[0] == prefix:
                return dp_dir

    # Default fallback
    return dp_dir


def resolve_dialogue_dir(dialogue_id: str, explicit_root: Path | None = None) -> Path:
    """Find, move, or create a dialogue directory in the right location.

    1. Search everywhere for the ID
    2. Determine "home" (from explicit_root, or inferred from sibling prefix)
    3. If found in wrong place → move it
    4. If not found → create it
    """
    home_root = explicit_root if explicit_root else _infer_home(dialogue_id)
    home = home_root / dialogue_id
    found = _find_all(dialogue_id)

    if found:
        existing = found[0]
        if existing.resolve() == home.resolve():
            return home  # already in right place
        # Found in wrong place — move it
        home.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(existing), str(home))
        print(f"  Moved {existing} → {home}")
        return home

    # Not found — create
    home.mkdir(parents=True, exist_ok=True)
    print(f"  Created {home}")
    return home


def resolve_dialogue_dirs(ids: list[str], explicit_root: Path | None = None) -> list[Path]:
    """Resolve a list of dialogue IDs to their directories."""
    return [resolve_dialogue_dir(did, explicit_root) for did in ids]


def main():
    parser = argparse.ArgumentParser(
        description="Generate per-turn TTS audio for YKI dialogues using Google Chirp 3 HD"
    )
    parser.add_argument("--dialogue-dir", type=Path, help="Single dialogue directory")
    parser.add_argument(
        "--dialogues-root", type=Path, default=None,
        help="Explicit root directory (auto-discovers if omitted)",
    )
    parser.add_argument("--only", type=str, default=None, help="Comma-separated dialogue IDs")
    parser.add_argument("--pause-narrator", type=float, default=1.0)
    parser.add_argument("--pause-turns", type=float, default=0.6)
    parser.add_argument("--force", action="store_true", help="Regenerate even if manifest exists")
    parser.add_argument(
        "--repick", type=str, default=None,
        help="Re-pick voice for a speaker: A, B, or narrator (e.g. --repick A)",
    )
    parser.add_argument(
        "--learner-gender", type=str, default=None, choices=["female", "male"],
        help="Assign gendered voice to learner role (read from **Learner role:** in fi_en_package.md, default B)",
    )
    args = parser.parse_args()

    api_key = os.environ.get("GOOGLE_API_KEY", "")
    if not api_key:
        raise RuntimeError("GOOGLE_API_KEY not found in environment")

    from google.cloud import texttospeech
    client = texttospeech.TextToSpeechClient(
        client_options={"api_key": api_key}
    )

    if args.dialogue_dir:
        dirs = [args.dialogue_dir]
    elif args.only:
        ids = [x.strip() for x in args.only.split(",")]
        dirs = resolve_dialogue_dirs(ids, args.dialogues_root)
    elif args.dialogues_root:
        dirs = sorted(
            d for d in args.dialogues_root.iterdir()
            if d.is_dir() and (d / "fi_en_package.md").exists()
        )
    else:
        print("Specify --only <id>, --dialogues-root, or --dialogue-dir")
        return

    if not dirs:
        print("No dialogue directories found.")
        return

    print(f"Google Chirp 3 HD | Voice pool: {len(VOICE_POOL)} voices")
    print(f"Dialogues to process: {len(dirs)}\n")

    total = len(dirs)
    done = 0
    skipped = 0
    errors = 0

    for i, d in enumerate(dirs, 1):
        dia_id = d.name
        manifest_path = d / "audio" / "manifest.json"
        if manifest_path.exists() and not args.force:
            print(f"[{i}/{total}] {dia_id} — skipping (manifest exists)")
            skipped += 1
            continue

        # Parse learner role from fi_en_package.md for gender-aware voice selection
        learner_role = "B"  # default
        pkg_file = d / "fi_en_package.md"
        if pkg_file.exists() and args.learner_gender:
            _ctx, _turns, learner_role = parse_fi_en_package(pkg_file)

        v_narrator, v_a, v_b = pick_voices(
            dia_id,
            repick_role=args.repick,
            learner_gender=args.learner_gender,
            learner_role=learner_role,
        )

        print(f"\n[{i}/{total}] {dia_id}")
        print(f"{'─' * 50}")
        try:
            generate_dialogue_audio(
                dialogue_dir=d,
                client=client,
                voice_narrator=v_narrator,
                voice_a=v_a,
                voice_b=v_b,
                pause_after_narrator=args.pause_narrator,
                pause_between_turns=args.pause_turns,
            )
            done += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()
            errors += 1

    print(f"\n{'=' * 50}")
    print(f"Done: {done} generated, {skipped} skipped, {errors} errors (of {total} total)")


if __name__ == "__main__":
    main()
