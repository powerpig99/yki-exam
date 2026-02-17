#!/usr/bin/env python3
"""Generate per-turn TTS audio for YKI dialogues using ElevenLabs API.

Reads fi_en_package.md for Finnish text, generates audio per turn with
3 distinct voices (narrator, Speaker A, Speaker B) that rotate across
dialogues, concatenates into merged.mp3, and writes manifest.json.

Requires: ELEVENLABS_API_KEY in environment (or ~/.zshrc).
Run with: .venv_mlx_audio/bin/python3
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import random

from dotenv import load_dotenv

load_dotenv()

# Load API key from env or parse from ~/.zshrc
def get_api_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        zshrc = Path.home() / ".zshrc"
        if zshrc.exists():
            for line in zshrc.read_text().splitlines():
                if line.strip().startswith("ELEVENLABS_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY not found in environment or ~/.zshrc")
    return key


MODEL_ID = "eleven_multilingual_v2"

# Pre-downloaded voice list (run once to populate)
VOICES_JSON = Path(__file__).parent / "elevenlabs_voices.json"


def load_voice_pool() -> list[dict]:
    """Load voice pool from cached JSON file."""
    if not VOICES_JSON.exists():
        raise FileNotFoundError(
            f"Voice list not found: {VOICES_JSON}\n"
            "Download it first with the ElevenLabs API."
        )
    return json.loads(VOICES_JSON.read_text(encoding="utf-8"))


def pick_voices(dialogue_id: str, pool: list[dict]) -> tuple[dict, dict, dict]:
    """Randomly pick 3 distinct voices, seeded by dialogue ID for reproducibility."""
    rng = random.Random(dialogue_id)
    chosen = rng.sample(pool, 3)
    return chosen[0], chosen[1], chosen[2]


# --- Regexes for parsing fi_en_package.md ---
CONTEXT_RE = re.compile(r"^\*\*FI Konteksti:\*\*\s*(.+)$")
FI_DIALOG_HEADER_RE = re.compile(r"^\*\*FI Koko mallidialogi:\*\*")
TURN_RE = re.compile(r"^-\s+\*\*([AB])\*\*:\s*(.+)$")


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


def parse_fi_en_package(path: Path) -> tuple[str, list[tuple[str, str]]]:
    """Parse fi_en_package.md returning (context, [(speaker, text), ...])."""
    lines = path.read_text(encoding="utf-8").splitlines()
    context = ""
    turns: list[tuple[str, str]] = []
    in_fi_dialog = False

    for line in lines:
        stripped = line.strip()
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

    return context, turns


def concat_audio_files(files: list[Path], out_file: Path) -> None:
    """Concatenate audio files with ffmpeg."""
    ffmpeg = ffmpeg_binary()
    with tempfile.TemporaryDirectory(prefix="tts_concat_") as td:
        list_file = Path(td) / "concat_list.txt"
        with list_file.open("w", encoding="utf-8") as f:
            for p in files:
                p_escaped = str(p).replace("'", "'\\''")
                f.write(f"file '{p_escaped}'\n")

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


def elevenlabs_tts(client, text: str, voice_id: str, out_path: Path) -> None:
    """Generate TTS audio via ElevenLabs API and save as WAV.

    Uses pcm_24000 format (raw 16-bit mono PCM) for frame-accurate timing.
    """
    audio = client.text_to_speech.convert(
        voice_id=voice_id,
        text=text,
        model_id=MODEL_ID,
        output_format="pcm_24000",
    )
    # pcm_24000 returns raw signed 16-bit little-endian mono PCM
    pcm_path = out_path.with_suffix(".pcm")
    with open(pcm_path, "wb") as f:
        for chunk in audio:
            f.write(chunk)
    # Wrap raw PCM in WAV container
    ffmpeg = ffmpeg_binary()
    cmd = [
        ffmpeg, "-y",
        "-f", "s16le", "-ar", "24000", "-ac", "1",
        "-i", str(pcm_path),
        "-c:a", "pcm_s16le",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pcm_path.unlink()


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

    context, turns = parse_fi_en_package(pkg_path)
    if not turns:
        raise ValueError(f"No turns found in {pkg_path}")

    audio_dir = dialogue_dir / "audio"
    # Clean stale files from previous TTS runs (Chatterbox WAVs, old silence, old merged)
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
    elevenlabs_tts(client, narrator_text, voice_narrator["id"], narrator_file)
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
        elevenlabs_tts(client, text, voice["id"], out_file)
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
        "voice_narrator_id": voice_narrator["id"],
        "voice_a_id": voice_a["id"],
        "voice_b_id": voice_b["id"],
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


def main():
    parser = argparse.ArgumentParser(
        description="Generate per-turn TTS audio for YKI dialogues using ElevenLabs"
    )
    parser.add_argument("--dialogue-dir", type=Path, help="Single dialogue directory")
    parser.add_argument(
        "--dialogues-root", type=Path,
        default=Path("/Users/jingliang/Documents/YKI_exam/dialog_practice/dialogues"),
    )
    parser.add_argument("--only", type=str, default=None, help="Comma-separated dialogue IDs")
    parser.add_argument("--pause-narrator", type=float, default=1.0)
    parser.add_argument("--pause-turns", type=float, default=0.6)
    parser.add_argument("--force", action="store_true", help="Regenerate even if manifest exists")
    args = parser.parse_args()

    api_key = get_api_key()

    # Import here to avoid top-level warning noise
    from elevenlabs.client import ElevenLabs
    client = ElevenLabs(api_key=api_key)

    # Determine which dialogues to process
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
                if d.is_dir() and (d / "fi_en_package.md").exists()
            )

    if not dirs:
        print("No dialogue directories found.")
        return

    voice_pool = load_voice_pool()
    print(f"ElevenLabs model: {MODEL_ID}")
    print(f"Voice pool: {len(voice_pool)} voices")
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

        # Pick rotating voices for this dialogue
        v_narrator, v_a, v_b = pick_voices(dia_id, voice_pool)

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
