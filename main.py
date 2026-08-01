#!/usr/bin/env python3
# coding: utf-8
from __future__ import annotations

import os
import re
import shutil
import subprocess as sp
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TaskID, TextColumn, TimeElapsedColumn

# Codec/bitrate args per output format. Formats not listed fall back to
# letting ffmpeg pick a default codec for "-f <format>".
FORMAT_CODEC_ARGS = {
    "mp3": ["-codec:a", "libmp3lame", "-b:a", "192k"],
    "m4a": ["-codec:a", "aac", "-b:a", "192k"],
    "aac": ["-codec:a", "aac", "-b:a", "192k"],
    "flac": ["-codec:a", "flac"],
    "wav": ["-codec:a", "pcm_s16le"],
    "ogg": ["-codec:a", "libvorbis", "-b:a", "192k"],
}

CHAPTER_RE = re.compile(r".*Chapter #(\d+):(\d+): start (\d+\.\d+), end (\d+\.\d+).*")

console = Console()


def find_ffmpeg(explicit_path: str | None) -> str:
    """Resolve the ffmpeg executable: explicit override > PATH."""
    if explicit_path:
        path = Path(explicit_path)
        if not path.exists():
            raise FileNotFoundError(f"ffmpeg not found at explicit path: {explicit_path}")
        return str(path)

    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path

    raise FileNotFoundError(
        "ffmpeg was not found on PATH. Install ffmpeg (e.g. 'winget install ffmpeg', "
        "'brew install ffmpeg', or 'apt install ffmpeg') or pass --ffmpeg with an explicit path."
    )


def parse_chapters(ffmpeg_path: str, filename: Path) -> tuple[list[dict], int]:
    command = [ffmpeg_path, "-i", str(filename)]
    try:
        output = sp.check_output(command, stderr=sp.STDOUT, universal_newlines=True)
    except sp.CalledProcessError as e:
        output = e.output

    chapters = []
    invalid = 0
    for line in output.splitlines():
        m = CHAPTER_RE.match(line)
        if m:
            cd, chapter_index, start_ts, end_ts = m.groups()
            start_ts_val, end_ts_val = float(start_ts), float(end_ts)
            if start_ts_val < 0 or end_ts_val <= start_ts_val:
                invalid += 1
                console.print(
                    f"[yellow]Skipping invalid chapter cd={int(cd) + 1} chapter_index={int(chapter_index) + 1} "
                    f"in {filename.name}: start={start_ts}, end={end_ts}[/yellow]"
                )
                continue
            chapters.append(
                {
                    "cd": int(cd) + 1,
                    "chapter_index": int(chapter_index) + 1,
                    "start_ts": start_ts,
                    "end_ts": end_ts,
                }
            )
    return chapters, invalid


def get_chapters(ffmpeg_path: str, filename: Path) -> tuple[list[dict], int]:
    book_name = filename.stem
    chapters, invalid = parse_chapters(ffmpeg_path, filename)
    for chap in chapters:
        chap["book_name"] = book_name
        chap["orgFile"] = str(filename)
    return chapters, invalid


def chapter_filename(
    cd: int, chapter_index: int, book_name: str, chapter_width: int, cd_width: int, out_format: str
) -> str:
    return f"{cd:0{cd_width}d}-{chapter_index:0{chapter_width}d}-{book_name}.{out_format}"


def compute_padding(chapters: list[dict]) -> tuple[int, int]:
    chapter_width = max(3, len(str(len(chapters))))
    cd_width = max(2, len(str(max(c["cd"] for c in chapters))))
    return chapter_width, cd_width


def convert_chapter(
    ffmpeg_path: str,
    org_file: str,
    cd: int,
    chapter_index: int,
    book_name: str,
    start_ts: str,
    end_ts: str,
    out_dir: Path,
    activation_bytes: str,
    out_format: str,
    chapter_width: int,
    cd_width: int,
) -> tuple[str, bool, str | None]:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_name = chapter_filename(cd, chapter_index, book_name, chapter_width, cd_width, out_format)
    out_path = out_dir / out_name

    if out_path.exists():
        return out_name, True, "skipped (already exists)"

    codec_args = FORMAT_CODEC_ARGS.get(out_format, ["-f", out_format])
    command = [
        ffmpeg_path,
        "-activation_bytes", activation_bytes,
        "-vn", "-vsync", "2",
        "-i", org_file,
        "-ss", start_ts,
        "-to", end_ts,
        "-ar", "44100", "-ac", "2",
        *codec_args,
        str(out_path),
    ]
    try:
        sp.check_output(command, stderr=sp.STDOUT, universal_newlines=True)
        return out_name, True, None
    except sp.CalledProcessError as e:
        out_path.unlink(missing_ok=True)
        return out_name, False, e.output


def convert_book(
    ffmpeg_path: str, org_file: str, book_name: str, output_dir: Path, activation_bytes: str, out_format: str
) -> tuple[str, bool, str | None]:
    """Remux a whole book into a single file, stream-copying audio so embedded chapters survive intact."""
    output_dir.mkdir(parents=True, exist_ok=True)
    out_name = f"{book_name}.{out_format}"
    out_path = output_dir / out_name

    if out_path.exists():
        return out_name, True, "skipped (already exists)"

    command = [
        ffmpeg_path,
        "-activation_bytes", activation_bytes,
        "-vn",
        "-i", org_file,
        "-c:a", "copy",
        str(out_path),
    ]
    try:
        sp.check_output(command, stderr=sp.STDOUT, universal_newlines=True)
        return out_name, True, None
    except sp.CalledProcessError as e:
        out_path.unlink(missing_ok=True)
        return out_name, False, e.output


def process_book(
    ffmpeg_path: str,
    book_name: str,
    chapters: list[dict],
    output_dir: Path,
    activation_bytes: str,
    out_format: str,
    chapter_workers: int,
    progress: Progress,
    books_task: TaskID,
) -> tuple[str, list[tuple[str, str]]]:
    if out_format == "m4b":
        # A single stream-copied file per book, keeping its original embedded
        # chapters, instead of splitting into one file per chapter.
        book_task = progress.add_task(f"[cyan]{book_name}", total=1)
        results = [convert_book(ffmpeg_path, chapters[0]["orgFile"], book_name, output_dir, activation_bytes, out_format)]
        progress.advance(book_task)
    else:
        chapter_width, cd_width = compute_padding(chapters)
        out_dir = output_dir / book_name
        book_task = progress.add_task(f"[cyan]{book_name}", total=len(chapters))
        results = []
        with ThreadPoolExecutor(max_workers=chapter_workers) as pool:
            futures = [
                pool.submit(
                    convert_chapter, ffmpeg_path, c["orgFile"], c["cd"], c["chapter_index"], book_name,
                    c["start_ts"], c["end_ts"], out_dir, activation_bytes, out_format, chapter_width, cd_width,
                )
                for c in chapters
            ]
            for future in as_completed(futures):
                results.append(future.result())
                progress.advance(book_task)

    failures = []
    for out_name, ok, message in results:
        if not ok:
            failures.append((out_name, message or ""))
            last_line = (message or "").strip().splitlines()[-1] if message else ""
            console.print(f"[red]FAILED[/red] {book_name}/{out_name}: {last_line}")

    progress.remove_task(book_task)
    progress.advance(books_task)
    status = "[green]done[/green]" if not failures else f"[yellow]done with {len(failures)} failure(s)[/yellow]"
    unit = "file" if out_format == "m4b" else "chapters"
    count = len(results) if out_format == "m4b" else len(chapters)
    console.print(f"{status}: {book_name} ({count} {unit})")
    return book_name, failures


def discover_books(input_dir: Path) -> list[Path]:
    return sorted(p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() == ".aax")


def print_dry_run_report(
    books: dict[str, list[dict]], invalid_counts: dict[str, int], output_dir: Path, out_format: str
) -> None:
    console.print("[bold]Dry run[/bold] — no files will be converted.\n")

    if out_format == "m4b":
        existing = 0
        for book_name in books:
            out_path = output_dir / f"{book_name}.m4b"
            already_exists = out_path.exists()
            marker = "[yellow](exists)[/yellow]" if already_exists else "[green](will create)[/green]"
            console.print(f"[cyan]{book_name}.m4b[/cyan] -> {out_path}  {marker}")
            if already_exists:
                existing += 1
        console.print(
            f"\n[bold]Summary:[/bold] {len(books)} book(s) — "
            f"{existing} already exist, {len(books) - existing} would be created."
        )
        return

    total_chapters = 0
    total_existing = 0
    total_invalid = 0
    for book_name, chapters in books.items():
        chapter_width, cd_width = compute_padding(chapters)
        out_dir = output_dir / book_name
        invalid = invalid_counts.get(book_name, 0)

        existing = 0
        console.print(f"[cyan]{book_name}[/cyan] ({len(chapters)} chapters -> {out_dir})")
        for chapter in chapters:
            out_name = chapter_filename(
                chapter["cd"], chapter["chapter_index"], book_name, chapter_width, cd_width, out_format
            )
            already_exists = (out_dir / out_name).exists()
            if already_exists:
                existing += 1
                console.print(f"  {out_name}  [yellow](exists)[/yellow]")
            else:
                console.print(f"  {out_name}  [green](will create)[/green]")

        summary = f"  -> {existing} already exist, {len(chapters) - existing} to create"
        if invalid:
            summary += f", [red]{invalid} invalid chapter(s) skipped[/red]"
        console.print(summary + "\n")

        total_chapters += len(chapters)
        total_existing += existing
        total_invalid += invalid

    summary = (
        f"[bold]Summary:[/bold] {len(books)} book(s), {total_chapters} chapter(s) total — "
        f"{total_existing} already exist, {total_chapters - total_existing} would be created"
    )
    if total_invalid:
        summary += f", {total_invalid} invalid chapter(s) skipped"
    console.print(summary + ".")


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "-i", "--input-dir", "input_dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    default=Path.cwd(), show_default="current directory",
    help="Directory containing .aax audiobook files.",
)
@click.option(
    "-o", "--output-dir", "output_dir",
    type=click.Path(file_okay=False, dir_okay=True, path_type=Path),
    default=Path("converted"), show_default=True,
    help="Directory to write converted books into.",
)
@click.option(
    "-a", "--activation-bytes", "activation_bytes",
    default=lambda: os.environ.get("AUDIBLE_ACTIVATION_BYTES"),
    help="Audible activation bytes (or set the AUDIBLE_ACTIVATION_BYTES environment variable).",
)
@click.option(
    "-f", "--format", "out_format",
    default="mp3", show_default=True,
    help="Output audio format/extension, e.g. mp3, m4a, flac, wav, ogg.",
)
@click.option(
    "--book-workers", "book_workers", type=int, default=2, show_default=True,
    help="Number of audiobooks to convert simultaneously.",
)
@click.option(
    "--chapter-workers", "chapter_workers", type=int, default=4, show_default=True,
    help="Number of chapters to convert simultaneously per book.",
)
@click.option(
    "--ffmpeg", "ffmpeg_override", default=None,
    help="Explicit path to an ffmpeg executable (overrides PATH lookup).",
)
@click.option(
    "--dry-run", "dry_run", is_flag=True, default=False,
    help="List what would be converted, and which output files already exist, without converting anything.",
)
def main(
    input_dir: Path,
    output_dir: Path,
    activation_bytes: str | None,
    out_format: str,
    book_workers: int,
    chapter_workers: int,
    ffmpeg_override: str | None,
    dry_run: bool,
) -> None:
    """Convert Audible AAX audiobooks into per-chapter audio files."""
    try:
        ffmpeg_path = find_ffmpeg(ffmpeg_override)
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)

    if not dry_run and not activation_bytes:
        console.print(
            "[red]No Audible activation bytes provided. "
            "Pass --activation-bytes or set the AUDIBLE_ACTIVATION_BYTES environment variable.[/red]"
        )
        sys.exit(1)

    out_format = out_format.lower().lstrip(".")

    book_files = discover_books(input_dir)
    if not book_files:
        console.print(f"[yellow]No .aax files found in {input_dir}[/yellow]")
        sys.exit(0)

    console.print(f"Using ffmpeg: [bold]{ffmpeg_path}[/bold]")
    console.print(f"Found {len(book_files)} audiobook(s) in {input_dir}")
    console.print("Reading chapter information...")

    books: dict[str, list[dict]] = {}
    invalid_counts: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=book_workers) as pool:
        futures = {pool.submit(get_chapters, ffmpeg_path, f): f for f in book_files}
        for future in as_completed(futures):
            f = futures[future]
            chapters, invalid = future.result()
            book_name = f.stem
            if invalid:
                invalid_counts[book_name] = invalid
            if not chapters:
                console.print(f"[yellow]No chapters found, skipping: {f.name}[/yellow]")
                continue
            books[book_name] = chapters

    if not books:
        console.print("[yellow]No books with chapter information to convert.[/yellow]")
        sys.exit(0)

    if dry_run:
        print_dry_run_report(books, invalid_counts, output_dir, out_format)
        return

    assert activation_bytes is not None

    output_dir.mkdir(parents=True, exist_ok=True)

    progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    )

    all_failures: dict[str, list[tuple[str, str]]] = {}
    with progress:
        books_task = progress.add_task("[bold]Books[/bold]", total=len(books))
        with ThreadPoolExecutor(max_workers=book_workers) as pool:
            futures = [
                pool.submit(
                    process_book, ffmpeg_path, name, chapters, output_dir,
                    activation_bytes, out_format, chapter_workers, progress, books_task,
                )
                for name, chapters in books.items()
            ]
            for future in as_completed(futures):
                name, failures = future.result()
                if failures:
                    all_failures[name] = failures

    console.print()
    if all_failures:
        console.print(f"[yellow]Finished with failures in {len(all_failures)} book(s):[/yellow]")
        for name, failures in all_failures.items():
            console.print(f"  {name}: {len(failures)} chapter(s) failed")
        sys.exit(1)
    else:
        console.print("[green]All books converted successfully.[/green]")


if __name__ == "__main__":
    main()
