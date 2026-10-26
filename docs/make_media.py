"""Make docs/media/demo.mp4 (about 30 s) and the frame PNGs from a REAL run of lead-scrub on demo/leads.csv.

Needs headless Chrome (or Edge) and ffmpeg on PATH. No extra Python packages.
    python docs/make_media.py
Set CHROME=/path/to/chrome if it is not in a standard place.
"""
from __future__ import annotations

import html
import io
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lead_scrub.cli import main as scrub_main  # noqa: E402
from lead_scrub.pipeline import load_rows, run  # noqa: E402

MEDIA = ROOT / "docs" / "media"
CHROMES = [
    os.environ.get("CHROME", ""),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

CSS = """
body{margin:0;background:#0d1117;color:#e6edf3;font:26px/1.45 Consolas,'DejaVu Sans Mono',monospace}
.win{margin:36px 48px;border:2px solid #30363d;border-radius:14px;overflow:hidden;background:#010409;height:648px}
.bar{background:#161b22;padding:10px 18px;font-size:20px;color:#8b949e}
.body{padding:22px 30px;white-space:pre}
.p{color:#7ee787}.d{color:#8b949e}.r{color:#ff7b72}.y{color:#f2cc60}.g{color:#7ee787}.b{color:#79c0ff}
h1{font:700 54px/1.2 'Segoe UI',Arial,sans-serif;margin:0 0 18px;white-space:normal}
.big{font:600 40px/1.4 'Segoe UI',Arial,sans-serif;white-space:normal}
"""


def page(title: str, body: str) -> str:
    return (f"<!doctype html><meta charset=utf-8><style>{CSS}</style><div class=win>"
            f"<div class=bar>{html.escape(title)}</div><div class=body>{body}</div></div>")


def esc(s: str, width: int) -> str:
    s = s if len(s) <= width else s[: width - 1] + "…"
    return html.escape(s.ljust(width))


def find_chrome() -> str:
    for c in CHROMES:
        if c and Path(c).exists():
            return c
    for n in ("chrome", "google-chrome", "chromium", "msedge"):
        if shutil.which(n):
            return shutil.which(n)
    raise SystemExit("no Chrome/Edge found; set CHROME=")


def shot(chrome: str, html_text: str, out: Path) -> None:
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "f.html"
        f.write_text(html_text, encoding="utf-8")
        subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        "--force-device-scale-factor=1", "--window-size=1280,720",
                        f"--screenshot={out}", f.as_uri()], check=True, capture_output=True, timeout=90)


def main() -> None:
    MEDIA.mkdir(parents=True, exist_ok=True)
    src = ROOT / "demo" / "leads.csv"
    header, rows = load_rows(src)
    res = run(header, rows)

    # 1. the messy input
    lines = [f'<span class=p>$</span> head -12 demo/leads.csv   <span class=d># what the CRM export looks like</span>', ""]
    lines.append("<span class=b>" + esc("Full Name", 17) + esc("Email", 36) + "Phone</span>")
    for r in rows[:11]:
        lines.append(esc(r["Full Name"], 17) + esc(r["Email"].strip(), 36) + html.escape(r["Phone"]))
    f1 = page("terminal", "\n".join(lines))

    # 2. the command + real summary
    buf = io.StringIO()
    with tempfile.TemporaryDirectory() as out, redirect_stdout(buf):
        scrub_main([str(src), "--out", out])
    summary = [ln for ln in buf.getvalue().splitlines() if not ln.startswith("wrote")]
    body = '<span class=p>$</span> python -m lead_scrub demo/leads.csv\n\n' + "\n".join(
        html.escape(ln) for ln in summary) + '\n\n<span class=g>wrote clean.csv  changes.csv  review.csv  report.md</span>'
    f2 = page("terminal", body)

    # 3. merge examples from the real result
    pick = [g for g in res.groups if g["rows"][0] in (2, 7, 13)]
    row_by_num = {int(r["__row"]): r for r in rows}
    parts = ["<span class=y>Duplicates merged, nothing lost</span>\n"]
    for g in pick:
        for n in g["rows"]:
            r = row_by_num[n]
            parts.append("<span class=r>- " + esc(f"row {n}", 7) + esc(r["Full Name"], 17) + html.escape(r["Email"].strip() or "(no email)") + "</span>")
        m = next(x for x in res.records if x.rows == g["rows"])
        parts.append("<span class=g>+ " + esc("kept", 7) + esc(m.name, 17) + html.escape(m.emails[0] + (f"  (+{len(m.emails) - 1} more kept)" if len(m.emails) > 1 else "")) + "</span>\n")
    f3 = page("report.md", "\n".join(parts))

    # 4. what still needs a person
    parts = ["<span class=y>Left for a human, never guessed</span>\n"]
    for p in res.review:
        parts.append(f"rows {p['rows'][0]} + {p['rows'][1]}: {html.escape(p['a']['name'])} / {html.escape(p['b']['name'])}")
        parts.append(f"<span class=d>  {html.escape(p['reason'])}</span>\n")
    parts.append("<span class=y>Bad values set aside with a fix list</span>\n")
    for r in res.records:
        for fl in r.flags:
            parts.append(f"row {r.rows[0]:<3} {html.escape(fl)}")
    f4 = page("report.md", "\n".join(parts))

    # 5. ROI card
    f5 = ('<!doctype html><meta charset=utf-8><style>' + CSS + '</style><div class=win><div class=body style="white-space:normal;padding:60px 70px">'
          '<h1>lead-scrub</h1><div class=big>Clean and de-duplicate a lead list in one command.<br><br>'
          '<span class=g>~3 hours/week saved</span><br>worth ~$90/week at $30/hour<br><br>'
          '<span class=d style="font-size:30px">Free, offline, MIT / Apache-2.0 parts. Claude optional.</span></div></div></div>')

    chrome = find_chrome()
    frames = [(f1, 6), (f2, 6), (f3, 8), (f4, 6), (f5, 5)]
    pngs = []
    for i, (h, _) in enumerate(frames, 1):
        out = MEDIA / f"frame{i}.png"
        shot(chrome, h, out)
        pngs.append(out)
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for (_, secs), png in zip(frames, pngs):
        cmd += ["-loop", "1", "-t", str(secs), "-i", str(png)]
    n = len(frames)
    cmd += ["-filter_complex", "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0,format=yuv420p[v]",
            "-map", "[v]", "-r", "30", str(MEDIA / "demo.mp4")]
    subprocess.run(cmd, check=True)
    print("wrote", MEDIA / "demo.mp4", "and", len(pngs), "frame PNGs")


if __name__ == "__main__":
    main()
