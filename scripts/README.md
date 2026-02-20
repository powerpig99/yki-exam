# YKI Karaoke Video Pipeline

Generate personalized YKI keskitaso practice videos from `fi_en_package.md` dialogue files.

## Pipeline

```
fi_en_package.md (Finnish dialogue + English translation)
  → generate_dialog_tts_google.py  (per-turn TTS audio → merged.mp3 + manifest.json)
  → render_dialog_karaoke.py       (karaoke video with dual subtitles → .mp4)
```

## Scripts

| Script | Purpose |
|--------|---------|
| `generate_dialog_tts_google.py` | Google Chirp 3 HD TTS with voice rotation |
| `render_dialog_karaoke.py` | 9:16 vertical video with Finnish karaoke + English subtitles |
| `batch_generate.sh` | Generic batch launcher — auto-discovers IDs, retry logic |
| `validate_packages.py` | Validates FI/EN sentence count parity across all packages |

## Requirements

- Python 3.12+ with venv (`.venv/`)
- `ffmpeg` and `ffprobe`
- `GOOGLE_API_KEY` environment variable (Google Cloud TTS)
- Packages: `pip install python-dotenv google-cloud-texttospeech`

## Usage

### Single dialogue
```bash
.venv/bin/python3 scripts/generate_dialog_tts_google.py --only li_dia_01 --force
.venv/bin/python3 scripts/render_dialog_karaoke.py --only li_dia_01 --force
```

### With learner gender (personalized voice assignment)
```bash
.venv/bin/python3 scripts/generate_dialog_tts_google.py --only li_dia_01 --force --learner-gender female
```

### Batch all dialogues for a learner
```bash
scripts/batch_generate.sh linh female ~/delivery/YKI_linh
```

### Re-pick a single speaker's voice
```bash
.venv/bin/python3 scripts/generate_dialog_tts_google.py --only li_dia_01 --force --repick B
```

## Input format

Each dialogue directory must contain `fi_en_package.md` with:
- Header: title, learner role (A or B), FI/EN context
- `**FI Koko mallidialogi:**` section with `- **A**:` / `- **B**:` turns
- `**EN Full sample dialogue:**` section with matching turns

Each FI turn and its EN translation must have the same number of sentences (split on `.!?`).

## Output

- `audio/merged.mp3` — concatenated TTS audio with narrator intro
- `audio/manifest.json` — per-segment timing data
- `video/dialogue.karaoke.mp4` — 1080x1920 vertical video, H.264
