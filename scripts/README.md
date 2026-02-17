# SoundCloud to Karaoke Scripts

This folder contains 3 scripts:

- `soundcloud_url_to_karaoke.sh` (recommended): one URL in, final `.mp3` + `.mp4` out.
- `soundcloud_playlist_clean_merge.sh`: download SoundCloud playlist and merge to cleaned MP3.
- `transcribe_karaoke_video.py`: transcribe MP3 and render vertical karaoke video.

## Requirements

- `python3`
- `yt-dlp`
- `curl`
- `ffmpeg` and `ffprobe`
- `OPENAI_API_KEY` environment variable for transcription/translation

If installed, the scripts prefer `/opt/homebrew/opt/ffmpeg-full/bin`.

## Quick Start (One Command)

From project root:

```bash
./scripts/soundcloud_url_to_karaoke.sh "https://soundcloud.com/gi-mara/sets/sisunautti-yhteiskunta-dialogit" .
```

Output files:

- `./sisunautti-yhteiskunta-dialogit.mp3`
- `./sisunautti-yhteiskunta-dialogit.mp4`

Notes:

- The output base name is auto-derived from the SoundCloud set slug.
- Intermediate files are cleaned automatically.

## Default Audio Cleaning Parameters

`soundcloud_playlist_clean_merge.sh` uses:

- `SILENCE_THRESH_DB=-50`
- `MIN_SILENCE_SEC=1.5`
- `KEEP_SILENCE_SEC=0.30`

You can override per run, for example:

```bash
SILENCE_THRESH_DB=-48 MIN_SILENCE_SEC=1.2 ./scripts/soundcloud_url_to_karaoke.sh "<url>" .
```

## Advanced: Run Steps Separately

Step 1: Merge playlist to MP3

```bash
./scripts/soundcloud_playlist_clean_merge.sh "<soundcloud set url>" "my_output.mp3"
```

Step 2: Create karaoke MP4 from MP3

```bash
./scripts/transcribe_karaoke_video.py "/absolute/path/to/my_output.mp3" --english-translation
```

Useful optional flags:

- `--force-transcribe`
- `--force-translate`
- `--split-mode semantic|timing`
- `--translation-batch-size 1` (best sync fidelity)

## If Script Is Not Executable

```bash
chmod +x ./scripts/soundcloud_url_to_karaoke.sh ./scripts/soundcloud_playlist_clean_merge.sh ./scripts/transcribe_karaoke_video.py
```
