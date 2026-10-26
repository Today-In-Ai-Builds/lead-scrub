"""Write clean.csv, review.csv, changes.csv and report.md. All CSV cells are formula-safe."""
from __future__ import annotations

import csv
import re
from pathlib import Path

from .pipeline import Result

_SAFE_NUMBERISH = re.compile(r"^[+-]?\d[\d .()-]*( x\d+)?$")


def safe_cell(v) -> str:
    """Neutralise spreadsheet formula injection (OWASP CSV injection), keeping real phone numbers."""
    s = "" if v is None else str(v)
    if s and s[0] in "=+-@\t\r" and not _SAFE_NUMBERISH.match(s):
        return "'" + s
    return s


def md(v) -> str:
    """Make untrusted text inert inside a Markdown table cell."""
    s = re.sub(r"\s+", " ", str(v))
    s = s.replace("\\", "\\\\").replace("|", "\\|").replace("`", "'").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([*_\[\]#@!])", r"\\\1", s)


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([safe_cell(h) for h in header])
        for r in rows:
            w.writerow([safe_cell(c) for c in r])


def write_outputs(res: Result, out_dir: Path, source_name: str, hours_per_1000: float = 3.0) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    header = ["name", "email", "other_emails", "phone", "other_phones", "company",
              *res.headers_extra, "flags", "source_rows"]
    rows = []
    for r in res.records:
        rows.append([r.name, r.emails[0] if r.emails else "", "; ".join(r.emails[1:]),
                     r.phones[0] if r.phones else "", "; ".join(r.phones[1:]), r.company,
                     *[r.extras.get(h, "") for h in res.headers_extra],
                     "; ".join(r.flags), ",".join(map(str, r.rows))])
    files = [out_dir / "clean.csv", out_dir / "changes.csv", out_dir / "review.csv", out_dir / "report.md"]
    write_csv(files[0], header, rows)
    write_csv(files[1], ["row", "field", "before", "after", "reason"],
              [[c["row"], c["field"], c["before"], c["after"], c["reason"]] for c in res.changes]
              + [[", ".join(map(str, g["rows"])), "merge", "", g["result"], "; ".join(g["reasons"])]
                 for g in res.groups])
    write_csv(files[2], ["row_a", "row_b", "name_a", "email_a", "phone_a", "company_a",
                         "name_b", "email_b", "phone_b", "company_b", "why_unsure"],
              [[p["rows"][0], p["rows"][1], p["a"]["name"], p["a"]["email"], p["a"]["phone"], p["a"]["company"],
                p["b"]["name"], p["b"]["email"], p["b"]["phone"], p["b"]["company"], p["reason"]]
               for p in res.review])
    files[3].write_text(render_report(res, source_name, hours_per_1000), encoding="utf-8")
    return files


def render_report(res: Result, source_name: str, hours_per_1000: float = 3.0) -> str:
    s = res.stats
    saved = s["rows_in"] / 1000 * hours_per_1000
    L = [f"# lead-scrub report for `{md(source_name)}`", "",
         f"- Rows in: **{s['rows_in']}**, rows out: **{s['rows_out']}** "
         f"(**{s['duplicates_merged']}** duplicate rows merged into {s['groups']} people)",
         f"- Field fixes applied: **{s['field_fixes']}**; invalid values set aside: **{s['invalid_dropped']}**",
         f"- Pairs left for a human to decide: **{s['needs_review']}** (see `review.csv`)",
         f"- Rows carrying a flag in `clean.csv`: **{s['with_flags']}**",
         f"- Estimated hand-cleaning time avoided: **~{saved:.2f} h** at {hours_per_1000:g} h per 1,000 rows", ""]
    if res.groups:
        L += ["## Merged duplicates", "", "| Source rows | Kept as | Why |", "| --- | --- | --- |"]
        for g in res.groups:
            L.append(f"| {', '.join(map(str, g['rows']))} | {md(g['result'])} | {md('; '.join(g['reasons']))} |")
        L.append("")
    if res.review:
        L += ["## Needs your decision (not merged)", "",
              "| Rows | Record A | Record B | Why unsure |", "| --- | --- | --- | --- |"]
        for p in res.review:
            a, b = p["a"], p["b"]
            L.append(f"| {p['rows'][0]} / {p['rows'][1]} | {md(a['name'])} {md(a['email'])} | "
                     f"{md(b['name'])} {md(b['email'])} | {md(p['reason'])} |")
        L.append("")
    flagged = [r for r in res.records if r.flags]
    if flagged:
        L += ["## Fix list: values that need a person", "", "| Row | Contact | Problem |", "| --- | --- | --- |"]
        for r in flagged:
            who = r.name or (r.emails[0] if r.emails else "(blank)")
            L.append(f"| {r.rows[0]} | {md(who)} | {md('; '.join(r.flags))} |")
        L.append("")
    L += ["## Every change", "", "See `changes.csv` for each before/after value with the reason.", ""]
    return "\n".join(L)


def summary_lines(res: Result) -> list[str]:
    s = res.stats
    ai = [f"judged by Claude  {s['ai_judged']}"] if s["ai_judged"] else []
    return ai + [
        f"rows in            {s['rows_in']}",
        f"rows out           {s['rows_out']}   ({s['duplicates_merged']} duplicates merged)",
        f"field fixes        {s['field_fixes']}",
        f"invalid set aside  {s['invalid_dropped']}",
        f"needs a human      {s['needs_review']}",
    ]
