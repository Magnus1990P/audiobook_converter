# audiobook_converter

Converts Audible `.aax` audiobooks into per-chapter audio files (MP3 by
default), with a live console progress display. Runs on Windows, Linux, and
macOS.

## Requirements

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/) available on your `PATH` (see below)
- Your Audible activation bytes

Install the Python dependencies:

```sh
pip install -r requirements.txt
```

### FFmpeg

This project does **not** bundle ffmpeg — it shells out to an ffmpeg binary
you install yourself, resolved in this order:

1. `--ffmpeg /path/to/ffmpeg` if you pass it explicitly
2. `ffmpeg` on your `PATH`

Install it with your platform's package manager, e.g.:

- Windows: `winget install ffmpeg` (or `choco install ffmpeg`)
- macOS: `brew install ffmpeg`
- Linux: `apt install ffmpeg` / `dnf install ffmpeg` / equivalent

FFmpeg is a separate project distributed under LGPL/GPL by its own authors,
depending on how your build is configured — see
[ffmpeg.org/legal.html](https://ffmpeg.org/legal.html). This project's own
[LICENSE](LICENSE) covers only the source code here; it does not redistribute
ffmpeg in any form.

## Usage

```sh
python main.py --input-dir "C:\Audiobooks" --activation-bytes DEADBEEF
```

Or set your activation bytes once as an environment variable:

```sh
export AUDIBLE_ACTIVATION_BYTES=deadbeef   # Windows: setx AUDIBLE_ACTIVATION_BYTES deadbeef
python main.py --input-dir "C:\Audiobooks"
```

The script scans `--input-dir` for `*.aax` files, reads each book's chapter
list, and converts every chapter in parallel. Output goes to
`.\converted\<book name>\` (relative to the current directory) by default, or
wherever `--output-dir` points.

### Options

| Option | Default | Description |
| --- | --- | --- |
| `-i, --input-dir` | current directory | Directory containing `.aax` files |
| `-o, --output-dir` | `.\converted` | Where converted books are written |
| `-a, --activation-bytes` | `$AUDIBLE_ACTIVATION_BYTES` | Your Audible activation bytes |
| `-f, --format` | `mp3` | Output format/extension: `mp3`, `m4a`, `aac`, `flac`, `wav`, `ogg`, ... |
| `--book-workers` | `2` | Audiobooks converted simultaneously |
| `--chapter-workers` | `4` | Chapters converted simultaneously per book |
| `--ffmpeg` | — | Explicit path to an ffmpeg executable, bypassing `PATH` lookup |

Run `python main.py --help` for the full list.

### Output naming

Each chapter is written as `<disc>-<chapter>-<book name>.<ext>`, with both
numbers zero-padded (chapter numbers pad to at least 3 digits, so listings
sort correctly even for books with 100+ chapters):

```
converted/
  My Great Book/
    01-001-My Great Book.mp3
    01-002-My Great Book.mp3
    ...
    01-137-My Great Book.mp3
```

Chapters that already exist in the output directory are skipped, so a
canceled/interrupted run can simply be re-run to pick up where it left off.

### Concurrency

Up to `--book-workers` books are processed at once, and within each book, up
to `--chapter-workers` chapters convert concurrently — 2×4 = 8 simultaneous
`ffmpeg` processes by default. The console shows overall book progress plus a
live chapter-progress bar for each book currently being converted.
