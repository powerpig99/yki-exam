# YKI Keskitaso Exam Prep — Personalized Karaoke Videos

Automated pipeline for generating personalized Finnish language practice videos for the [YKI keskitaso (intermediate) exam](https://www.oph.fi/en/national-certificates-language-proficiency-yki). Each video is a karaoke-style dialogue with word-by-word Finnish highlighting and English subtitles, tailored to an individual learner's life context.

Includes a sample learner (Linh) with 56 complete dialogue packages for reference.

<video src="https://github.com/powerpig99/yki-exam/raw/main/sample.mp4" controls width="270"></video>

## How It Works

```
Learner Profile + Shared Templates
  → Claude Code agents fill in personalized dialogues (fi_en_package.md)
  → Google Chirp 3 HD TTS (27 Finnish voices, deterministic rotation)
  → ffmpeg karaoke renderer (9:16 vertical, dual subtitles)
  → .mp4 video per dialogue
```

Each learner gets **56 videos** covering all YKI keskitaso speaking tasks:

| Type | Count | Description |
|------|-------|-------------|
| `dia_` | 36 | Two-person dialogues (everyday situations) |
| `rea_` | 5 | Reactions and responses |
| `ker_` | 3 | Storytelling (kertominen) |
| `mie_` | 5 | Expressing opinions (mielipide) |
| `wri_` | 7 | Writing tasks (kirjoittaminen) |

## Project Structure

```
YKI_exam/
├── scripts/                    # Pipeline scripts (TTS, video, batch, validation)
├── learners/
│   ├── framework/              # Shared across all learners
│   │   ├── questionnaire.md    # Learner intake questionnaire
│   │   ├── process.md          # Production workflow
│   │   ├── yki_keskitaso_topics.md  # Topic research + vocabulary
│   │   └── templates/          # 56 universal dialogue templates
│   │       ├── dia_01/ … dia_36/
│   │       ├── rea_01/ … rea_05/
│   │       ├── ker_01/ … ker_03/
│   │       ├── mie_01/ … mie_05/
│   │       └── wri_01/ … wri_07/
│   └── linh/                   # Sample learner: profile, plan, dialogues/
└── .gitignore
```

## Pipeline Scripts

| Script | Purpose |
|--------|---------|
| `generate_dialog_tts_google.py` | Google Chirp 3 HD TTS — reads `fi_en_package.md`, generates per-turn audio with voice rotation |
| `render_dialog_karaoke.py` | Karaoke video renderer — ASS subtitles with word timing, 1080x1920 H.264 |
| `batch_generate.sh` | Generic batch launcher — auto-discovers IDs from learner directory, retry logic |
| `validate_packages.py` | Validates FI/EN sentence count parity across all packages |

See [scripts/README.md](scripts/README.md) for detailed usage.

## Quick Start

### Prerequisites

- Python 3.12+
- `ffmpeg` and `ffprobe`
- Google Cloud TTS API key (`GOOGLE_API_KEY` in `.env`)

### Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install python-dotenv google-cloud-texttospeech
```

### Generate a Single Video

```bash
# TTS
.venv/bin/python3 scripts/generate_dialog_tts_google.py \
  --only li_dia_01 --force --learner-gender female

# Video
.venv/bin/python3 scripts/render_dialog_karaoke.py --only li_dia_01 --force
```

### Batch Generate for a Learner

```bash
scripts/batch_generate.sh learners/linh female ~/delivery/YKI_linh/
```

## Content Architecture

The system uses a **two-step isolation architecture**:

1. **Template agents** generate 56 universal dialogue templates with `{NIMI}`/`{NAME}` placeholders and generic situations — no learner-specific information
2. **Fill-in agents** personalize templates using the learner's profile, assigning Finnish character names and adapting contexts

This separation means new learner sets can reuse existing templates (fast path) or generate fresh ones.

### Content Rules

- Finnish text spells out all numbers (TTS pronunciation accuracy)
- FI and EN sentence counts must match per turn (renderer splits on `.!?`)
- Character names must be Finnish (TTS constraint)
- Non-learner gender alternates across templates for voice diversity

## What's Not in Git

Media files, personal learner content, and copyrighted sources are gitignored:

- `learners/*` — personal learner content (only `framework/` and sample `linh/` are tracked)
- `audio/`, `video/` — excluded due to size; fully regenerable from the `fi_en_package.md` source files using the pipeline scripts
- `.pdf` files — copyrighted textbook scans (local reference only)
- `.env` — API keys

## License

[MIT](LICENSE) — pipeline scripts, templates, and sample imaginary learner content are all freely available.
