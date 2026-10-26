"""Command line: lead-scrub leads.csv [--out DIR] [--region US] [--ai]."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import phonenumbers

from . import __version__
from .pipeline import load_rows, run
from .report import summary_lines, write_outputs

OUT_NAMES = ("clean.csv", "changes.csv", "review.csv", "report.md")


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="lead-scrub", description="Clean and de-duplicate a lead/CRM CSV.")
    ap.add_argument("csv", type=Path, help="input CSV (never modified)")
    ap.add_argument("--out", type=Path, default=None, help="output folder (default: ./lead-scrub-out)")
    ap.add_argument("--region", default="US", help="default country for phone numbers, ISO code (default US)")
    ap.add_argument("--ai", action="store_true", help="let the local Claude CLI judge borderline pairs")
    ap.add_argument("--version", action="version", version=f"lead-scrub {__version__}")
    args = ap.parse_args(argv)

    src = args.csv
    if not src.is_file():
        print(f"lead-scrub: no such file: {src}", file=sys.stderr)
        return 2
    out_dir = args.out or Path("lead-scrub-out")
    if out_dir.exists() and not out_dir.is_dir():
        print(f"lead-scrub: --out must be a folder: {out_dir}", file=sys.stderr)
        return 2
    for n in OUT_NAMES:  # never clobber the input
        target = out_dir / n
        if target.exists() and target.samefile(src):
            print(f"lead-scrub: refusing to overwrite the input file {src}", file=sys.stderr)
            return 2
    region = args.region.upper()
    if region not in phonenumbers.SUPPORTED_REGIONS:
        print(f"lead-scrub: unknown --region {args.region!r}", file=sys.stderr)
        return 2
    try:
        header, rows = load_rows(src)
    except (ValueError, OSError) as e:
        print(f"lead-scrub: cannot read {src}: {e}", file=sys.stderr)
        return 2
    judge = None
    if args.ai:
        from .ai import claude_judge, find_claude
        if find_claude():
            judge = claude_judge
        else:
            print("lead-scrub: --ai given but the claude CLI was not found; continuing without it.",
                  file=sys.stderr)
    res = run(header, rows, region, judge)
    files = write_outputs(res, out_dir, src.name)
    print(f"lead-scrub {__version__}  {src.name}")
    print("-" * 40)
    for line in summary_lines(res):
        print(line)
    print("-" * 40)
    for f in files:
        print(f"wrote {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
