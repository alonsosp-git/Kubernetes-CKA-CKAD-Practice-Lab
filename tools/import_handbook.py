#!/usr/bin/env python3
"""Import the pages of a PDF you own into `handbook/`.

Why this exists
---------------
The lab can show the page of a book beside the exercise you are practising.
Those page images are somebody else's copyrighted work, so they are *not*
distributed with this project -- you point this script at your own copy and it
extracts the pages locally, into two directories that `.gitignore` keeps out of
version control.

    python tools/import_handbook.py "~/books/Kubernetes Handbook.pdf"
    python tools/import_handbook.py book.pdf --dpi 200 --pages 1-54

Nothing is uploaded anywhere; this runs entirely on your machine.

Everything works without it. Each of the 50 topics ships with an original
vector diagram drawn by this project (`k8slab/diagrams.py`), and the notes,
commands and gotchas are written for this lab. The imported pages are an extra.

Requires one of: pdftoppm (poppler-utils), pdftocairo, mutool, or PyMuPDF.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES_DIR = os.path.join(ROOT, "handbook", "pages")
TEXT_DIR = os.path.join(ROOT, "handbook", "text")


def parse_range(text: str) -> tuple:
    match = re.fullmatch(r"(\d+)(?:-(\d+))?", text.strip())
    if not match:
        raise argparse.ArgumentTypeError("use e.g. 1-54 or 7")
    first = int(match.group(1))
    return first, int(match.group(2) or first)


def have(program: str) -> bool:
    return shutil.which(program) is not None


def render_with_poppler(pdf: str, first: int, last: int, dpi: int) -> int:
    program = "pdftoppm" if have("pdftoppm") else "pdftocairo"
    prefix = os.path.join(PAGES_DIR, "page")
    argv = [program, "-png", "-r", str(dpi),
            "-f", str(first), "-l", str(last), pdf, prefix]
    subprocess.run(argv, check=True)
    # poppler writes page-1.png / page-01.png depending on the page count;
    # normalise to the page_NN.png the app looks for.
    renamed = 0
    for name in sorted(os.listdir(PAGES_DIR)):
        match = re.fullmatch(r"page-(\d+)\.png", name)
        if match:
            target = f"page_{int(match.group(1)):02d}.png"
            os.replace(os.path.join(PAGES_DIR, name),
                       os.path.join(PAGES_DIR, target))
            renamed += 1
    return renamed


def render_with_pymupdf(pdf: str, first: int, last: int, dpi: int) -> int:
    import fitz                                   # PyMuPDF

    document = fitz.open(pdf)
    count = 0
    zoom = dpi / 72.0
    for number in range(first, min(last, document.page_count) + 1):
        page = document.load_page(number - 1)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        pixmap.save(os.path.join(PAGES_DIR, f"page_{number:02d}.png"))
        text = page.get_text()
        if text.strip():
            with open(os.path.join(TEXT_DIR, f"page_{number:02d}.txt"),
                      "w", encoding="utf-8") as handle:
                handle.write(text)
        count += 1
    return count


def extract_text(pdf: str, first: int, last: int) -> int:
    if not have("pdftotext"):
        return 0
    written = 0
    for number in range(first, last + 1):
        target = os.path.join(TEXT_DIR, f"page_{number:02d}.txt")
        result = subprocess.run(
            ["pdftotext", "-f", str(number), "-l", str(number), pdf, target],
            capture_output=True)
        if result.returncode == 0 and os.path.exists(target):
            written += 1
    return written


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("Why this exists")[0].strip(),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf", help="path to a PDF you own")
    parser.add_argument("--dpi", type=int, default=150,
                        help="render resolution (default 150; 200+ is sharper "
                             "and much larger)")
    parser.add_argument("--pages", type=parse_range, default=(1, 999),
                        metavar="FIRST-LAST", help="page range, e.g. 1-54")
    parser.add_argument("--clear", action="store_true",
                        help="delete previously imported pages first")
    args = parser.parse_args(argv)

    pdf = os.path.abspath(os.path.expanduser(args.pdf))
    if not os.path.isfile(pdf):
        print(f"no such file: {pdf}", file=sys.stderr)
        return 2
    if not pdf.lower().endswith(".pdf"):
        print("that does not look like a PDF", file=sys.stderr)
        return 2
    if not 40 <= args.dpi <= 600:
        print("--dpi must be between 40 and 600", file=sys.stderr)
        return 2

    os.makedirs(PAGES_DIR, exist_ok=True)
    os.makedirs(TEXT_DIR, exist_ok=True)
    if args.clear:
        for directory in (PAGES_DIR, TEXT_DIR):
            for name in os.listdir(directory):
                if name != ".gitkeep":
                    os.remove(os.path.join(directory, name))

    first, last = args.pages
    print(f"reading {os.path.basename(pdf)} (pages {first}-{last}) at {args.dpi} dpi")

    try:
        if have("pdftoppm") or have("pdftocairo"):
            pages = render_with_poppler(pdf, first, last, args.dpi)
            words = extract_text(pdf, first, min(last, pages or last))
        else:
            try:
                import fitz                                     # noqa: F401
            except ImportError:
                print("\nNo PDF renderer found. Install one of:\n"
                      "  macOS         brew install poppler\n"
                      "  Debian/Ubuntu sudo apt install poppler-utils\n"
                      "  Fedora        sudo dnf install poppler-utils\n"
                      "  Windows       winget install oschwartz10612.Poppler\n"
                      "  any platform  pip install PyMuPDF\n", file=sys.stderr)
                return 3
            pages = render_with_pymupdf(pdf, first, last, args.dpi)
            words = pages
    except subprocess.CalledProcessError as exc:
        print(f"the renderer failed: {exc}", file=sys.stderr)
        return 4

    print(f"\n  {pages} page images -> handbook/pages/")
    print(f"  {words} text files  -> handbook/text/")
    print("\nBoth directories are in .gitignore: these pages are your copy of\n"
          "someone else's book, and must not be committed or redistributed.\n"
          "Start the lab and open any topic's Page tab to see them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
