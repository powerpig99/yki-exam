#!/usr/bin/env python3
"""Compare Chatterbox vs Qwen3-TTS on a single YKI dialogue.

Generates audio for each turn of a dialogue using both models,
outputting to dialog_practice/tts_comparison/ for manual evaluation.

Must run with: .venv_mlx_audio/bin/python3
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

# Suppress noisy logs before importing mlx_audio
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

from mlx_audio.tts.generate import generate_audio, load_model


# Test sentences: the non-Sinä turns from dialogue 01_dia_03
# (Phone call to congratulate friend on new job)
def load_dialogue(dialogue_dir: Path) -> dict:
    with open(dialogue_dir / "dialogue.json", encoding="utf-8") as f:
        return json.load(f)


def get_test_texts(dialogue: dict) -> list[dict]:
    """Extract non-Sinä turns for TTS testing (these have actual Finnish text)."""
    texts = []
    # Include tilanne as narrator line
    texts.append({
        "role": "narrator",
        "text": f"Tilanne: {dialogue['tilanne']}",
    })
    for i, turn in enumerate(dialogue["turns"]):
        if not turn["is_sina"]:
            texts.append({
                "role": turn["speaker"],
                "text": turn["text"],
                "turn_idx": i,
            })
    return texts


def run_chatterbox(texts: list[dict], out_dir: Path, ref_audio: str | None):
    """Generate audio using Chatterbox model."""
    model_id = "mlx-community/chatterbox-fp16"
    print(f"\n{'='*60}")
    print(f"Model: Chatterbox ({model_id})")
    print(f"{'='*60}")

    out_dir.mkdir(parents=True, exist_ok=True)
    model = load_model(model_id)

    for i, item in enumerate(texts):
        prefix = f"turn_{i:02d}_{item['role']}"
        print(f"\n  [{i+1}/{len(texts)}] {item['role']}: {item['text'][:60]}...")
        t0 = time.time()
        generate_audio(
            text=item["text"],
            model=model,
            lang_code="fi",
            output_path=str(out_dir),
            file_prefix=prefix,
            audio_format="wav",
            verbose=False,
            **({"ref_audio": ref_audio} if ref_audio else {}),
        )
        elapsed = time.time() - t0
        print(f"    Done in {elapsed:.1f}s")

    return model_id


def run_qwen3_tts(texts: list[dict], out_dir: Path, voice: str = "Ethan"):
    """Generate audio using Qwen3-TTS Base model."""
    model_id = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16"
    print(f"\n{'='*60}")
    print(f"Model: Qwen3-TTS ({model_id})")
    print(f"{'='*60}")

    out_dir.mkdir(parents=True, exist_ok=True)
    model = load_model(model_id)

    for i, item in enumerate(texts):
        prefix = f"turn_{i:02d}_{item['role']}"
        print(f"\n  [{i+1}/{len(texts)}] {item['role']}: {item['text'][:60]}...")
        t0 = time.time()
        generate_audio(
            text=item["text"],
            model=model,
            voice=voice,
            lang_code="fi",
            output_path=str(out_dir),
            file_prefix=prefix,
            audio_format="wav",
            verbose=False,
        )
        elapsed = time.time() - t0
        print(f"    Done in {elapsed:.1f}s")

    return model_id


def main():
    parser = argparse.ArgumentParser(description="Compare TTS models on a YKI dialogue")
    parser.add_argument(
        "--dialogue",
        type=Path,
        default=Path("dialog_practice/dialogues/01_dia_03"),
        help="Path to dialogue directory with dialogue.json",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("dialog_practice/tts_comparison"),
        help="Output directory for comparison audio",
    )
    parser.add_argument(
        "--ref-audio",
        type=str,
        default="media/_mlx_test/fi_auto_ryan_000.wav",
        help="Reference audio for voice cloning (Chatterbox)",
    )
    parser.add_argument(
        "--qwen-voice",
        type=str,
        default="Ethan",
        help="Qwen3-TTS voice name (e.g. Chelsie, Ethan, Vivian)",
    )
    parser.add_argument(
        "--only",
        type=str,
        default=None,
        help="Run only: chatterbox or qwen3",
    )
    args = parser.parse_args()

    dialogue = load_dialogue(args.dialogue)
    texts = get_test_texts(dialogue)
    dia_id = dialogue["id"]

    print(f"Dialogue: {dia_id} — {dialogue['tilanne'][:60]}")
    print(f"Test turns: {len(texts)} (narrator + {len(texts)-1} speaker turns)")

    if args.only != "qwen3":
        chatterbox_dir = args.out_dir / f"{dia_id}_chatterbox"
        run_chatterbox(texts, chatterbox_dir, args.ref_audio)

    if args.only != "chatterbox":
        qwen3_dir = args.out_dir / f"{dia_id}_qwen3"
        run_qwen3_tts(texts, qwen3_dir, args.qwen_voice)

    print(f"\n{'='*60}")
    print(f"Comparison audio saved to: {args.out_dir}")
    print(f"Listen to both directories and pick the winner:")
    print(f"  {args.out_dir / f'{dia_id}_chatterbox'}/")
    print(f"  {args.out_dir / f'{dia_id}_qwen3'}/")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
