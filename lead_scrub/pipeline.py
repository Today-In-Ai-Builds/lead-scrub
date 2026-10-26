"""Load, clean, match and merge lead rows."""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import clean as C

ALIASES = {
    "first": {"firstname", "first", "givenname", "fname", "forename"},
    "last": {"lastname", "last", "surname", "lname", "familyname"},
    "name": {"name", "fullname", "contact", "contactname", "customername", "leadname"},
    "email": {"email", "emailaddress", "mail", "eemail", "emailaddr", "workemail"},
    "phone": {"phone", "phonenumber", "mobile", "tel", "telephone", "cell", "mobilephone",
              "workphone", "cellphone"},
    "company": {"company", "companyname", "organization", "organisation", "org", "business",
                "account", "employer"},
}
AUTO_NAME = 80.0       # shared email/phone + name at least this similar -> same person
REVIEW_NAME = 55.0     # shared email + name this similar -> ask a human
NAME_ONLY_AUTO = 93.0  # no shared identifier: name and company must both be very close
NAME_ONLY_REVIEW = 85.0
COMPANY_MATCH = 85.0
MAX_FUZZY_BLOCK = 150  # name/company/domain blocks bigger than this are skipped (email/phone always compared)


@dataclass
class Rec:
    rows: list[int]
    name: str = ""
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    company: str = ""
    extras: dict[str, str] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)

    def completeness(self) -> int:
        return sum(bool(x) for x in (self.name, self.emails, self.phones, self.company)) * 10 + len(
            [v for v in self.extras.values() if v])


@dataclass
class Result:
    records: list[Rec]
    headers_extra: list[str]
    changes: list[dict]
    groups: list[dict]          # merged groups: rows, reasons, result name
    review: list[dict]          # unresolved pairs for a human
    stats: dict


# ------------------------------------------------------------------ loading
def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def load_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    text = read_text(path)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text, newline=""), dialect)
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError("the CSV is empty")
    header = [C.ws(h) for h in header]
    if not any(header):
        raise ValueError("the first row has no column names")
    rows = []
    for cells in reader:
        if not any(C.ws(c) for c in cells):
            continue  # skip blank lines but keep numbering honest below
        rows.append((reader.line_num, cells))
    out = []
    for lineno, cells in rows:
        d = {h: (cells[i] if i < len(cells) else "") for i, h in enumerate(header) if h}
        d["__row"] = str(lineno)
        out.append(d)
    return header, out


def map_columns(header: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for h in header:
        key = re.sub(r"[^a-z0-9]", "", h.lower())
        for role, names in ALIASES.items():
            if key in names and role not in mapping:
                mapping[role] = h
    return mapping


# ------------------------------------------------------------------ cleaning
def build_records(header, rows, region: str, changes: list[dict]) -> tuple[list[Rec], list[str]]:
    cols = map_columns(header)
    known = set(cols.values())
    extra_cols = [h for h in header if h and h not in known]
    recs = []

    def log(row, fld, before, after, why):
        changes.append({"row": row, "field": fld, "before": before, "after": after, "reason": why})

    for d in rows:
        row = int(d["__row"])
        rec = Rec(rows=[row])
        # name
        if "first" in cols or "last" in cols:
            raw_name = " ".join(x for x in (d.get(cols.get("first", ""), ""), d.get(cols.get("last", ""), "")) if x.strip())
            if "name" in cols and not C.ws(raw_name):
                raw_name = d.get(cols["name"], "")
        else:
            raw_name = d.get(cols.get("name", ""), "")
        rec.name = C.clean_name(raw_name)
        if C.ws(raw_name) != rec.name:
            log(row, "name", C.ws(raw_name), rec.name, "name formatting")
        # emails
        for part in C.split_multi(d.get(cols.get("email", ""), "")):
            e, note = C.clean_email(part)
            if not e:
                rec.flags.append(note or f"invalid email '{part}'")
                log(row, "email", part, "", note or "invalid email")
                continue
            if note:
                rec.flags.append(f"email fixed: {note}")
                log(row, "email", part, e, note)
            elif part != e:
                log(row, "email", part, e, "email lowercased/trimmed")
            if e not in rec.emails:
                rec.emails.append(e)
        # phones
        for part in re.split(r"[;|/]+", d.get(cols.get("phone", ""), "")):
            if not C.ws(part):
                continue
            p, note = C.clean_phone(part, region)
            if not p:
                rec.flags.append(note or f"invalid phone '{C.ws(part)}'")
                log(row, "phone", C.ws(part), "", note or "invalid phone")
                continue
            if C.ws(part) != p:
                log(row, "phone", C.ws(part), p, f"phone normalised to E.164 ({region} default)")
            if p not in rec.phones and C.phone_id(p) not in [C.phone_id(x) for x in rec.phones]:
                rec.phones.append(p)
        # company
        raw_co = d.get(cols.get("company", ""), "")
        rec.company = C.clean_company(raw_co)
        if C.ws(raw_co) != rec.company:
            log(row, "company", C.ws(raw_co), rec.company, "company formatting")
        for h in extra_cols:
            rec.extras[h] = C.ws(d.get(h, ""))
        recs.append(rec)
    return recs, extra_cols


# ------------------------------------------------------------------ matching
def classify(a: Rec, b: Rec) -> tuple[str | None, str]:
    """Return ('auto' | 'review' | None, reason)."""
    ns = C.name_similarity(a.name, b.name)
    both_names = bool(a.name and b.name)
    shared_email = [e for e in a.emails if e in b.emails]
    pa, pb = {C.phone_id(p) for p in a.phones}, {C.phone_id(p) for p in b.phones}
    shared_phone = sorted(pa & pb)
    if shared_email:
        e = shared_email[0]
        if C.is_role_email(e):
            if both_names and ns >= 85:
                return "auto", f"same email {e} and same name"
        elif not both_names or ns >= AUTO_NAME:
            return "auto", f"same email {e}"
        elif ns >= REVIEW_NAME or _last(a.name) == _last(b.name):
            return "review", f"same email {e} but names differ ({ns:.0f}% alike)"
    if shared_phone:
        p = shared_phone[0]
        if not both_names or ns >= AUTO_NAME:
            return "auto", f"same phone {p}"
        if ns >= REVIEW_NAME + 15:
            return "review", f"same phone {p} but names differ ({ns:.0f}% alike)"
    if both_names:
        cs = C.company_similarity(a.company, b.company)
        same_dom = bool({C.email_domain(e) for e in a.emails} & {C.email_domain(e) for e in b.emails}
                        - C.FREE_DOMAINS)
        company_ok = cs >= COMPANY_MATCH or same_dom
        if ns >= NAME_ONLY_AUTO and company_ok:
            return "auto", f"name {ns:.0f}% alike at the same company"
        if ns >= NAME_ONLY_REVIEW and company_ok:
            return "review", f"name {ns:.0f}% alike at the same company"
    return None, ""


def _last(name: str) -> str:
    toks = C.name_key(name).split()
    return toks[-1] if len(toks) > 1 else ""


def candidate_pairs(recs: list[Rec]) -> set[tuple[int, int]]:
    blocks: dict[str, list[int]] = {}

    def add(key, i):
        if key:
            blocks.setdefault(key, []).append(i)

    for i, r in enumerate(recs):
        for e in r.emails:
            add("e:" + e, i)
        for p in r.phones:
            add("p:" + C.phone_id(p), i)
        toks = C.name_key(r.name).split()
        if toks:
            add("n:" + toks[-1][:3] + toks[0][:1], i)
        add("c:" + C.company_key(r.company)[:5], i)
        for e in r.emails:
            d = C.email_domain(e)
            if d not in C.FREE_DOMAINS:
                add("d:" + d, i)
    pairs = set()
    for key, idx in blocks.items():
        if len(idx) > MAX_FUZZY_BLOCK and key[0] in "ncd":
            continue  # a "block" this big is a big company or a common surname: too vague to compare
        for x in range(len(idx)):
            for y in range(x + 1, len(idx)):
                a, b = idx[x], idx[y]
                if a != b:
                    pairs.add((min(a, b), max(a, b)))
    return pairs


class DSU:
    def __init__(self, n):
        self.p = list(range(n))
        self.mem = {i: [i] for i in range(n)}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            lo, hi = min(ra, rb), max(ra, rb)
            self.p[hi] = lo
            self.mem[lo].extend(self.mem.pop(hi))

    def members(self, x):
        return self.mem[self.find(x)]


def _would_join_different_people(recs, dsu, a, b) -> bool:
    if dsu.find(a) == dsu.find(b):
        return False
    left = [recs[i] for i in dsu.members(a) if recs[i].name]
    right = [recs[i] for i in dsu.members(b) if recs[i].name]
    return any(C.name_similarity(x.name, y.name) < REVIEW_NAME for x in left for y in right)


def merge(group: list[Rec]) -> Rec:
    group = sorted(group, key=lambda r: (-r.completeness(), r.rows[0]))
    out = Rec(rows=sorted(x for r in group for x in r.rows))
    out.name = max((r.name for r in group),
                   key=lambda n: (len(C.name_key(n).split()), len(n), sum(c.isupper() for c in n)), default="")
    out.company = next((r.company for r in group if r.company), "")
    for r in group:
        for e in r.emails:
            if e not in out.emails:
                out.emails.append(e)
        for p in r.phones:
            if C.phone_id(p) not in [C.phone_id(x) for x in out.phones]:
                out.phones.append(p)
        for f in r.flags:
            if f not in out.flags:
                out.flags.append(f)
        for k, v in r.extras.items():
            if v and not out.extras.get(k):
                out.extras[k] = v
            out.extras.setdefault(k, "")
    return out


def run(header, rows, region: str = "US", judge=None) -> Result:
    changes: list[dict] = []
    recs, extra_cols = build_records(header, rows, region, changes)
    dsu = DSU(len(recs))
    edge_reasons: dict[int, list[tuple[int, int, str]]] = {}
    review = []
    for a, b in sorted(candidate_pairs(recs)):
        verdict, why = classify(recs[a], recs[b])
        if verdict == "auto":
            if _would_join_different_people(recs, dsu, a, b):
                # A nameless row (or a shared mailbox) must not glue two different people together.
                review.append({"a": a, "b": b, "reason": why + ", but that would join two different names"})
                continue
            dsu.union(a, b)
            edge_reasons.setdefault(a, []).append((a, b, why))
        elif verdict == "review":
            review.append({"a": a, "b": b, "reason": why})
    ai_used = 0
    if judge and review:
        pairs = [{"id": i, "a": _brief(recs[p["a"]]), "b": _brief(recs[p["b"]])} for i, p in enumerate(review)]
        answers = judge(pairs) or {}
        for i, p in enumerate(review):
            ans = answers.get(i)
            if ans in ("same", "different"):
                p["ai"] = ans
                ai_used += 1
                if ans == "same":
                    dsu.union(p["a"], p["b"])
                    edge_reasons.setdefault(p["a"], []).append((p["a"], p["b"], p["reason"] + "; Claude judged same person"))
    clusters: dict[int, list[int]] = {}
    for i in range(len(recs)):
        clusters.setdefault(dsu.find(i), []).append(i)
    reasons_by_root: dict[int, list[str]] = {}
    for edges in edge_reasons.values():
        for a, b, why in edges:
            reasons_by_root.setdefault(dsu.find(a), []).append(f"rows {recs[a].rows[0]} + {recs[b].rows[0]}: {why}")
    out, groups = [], []
    for root in sorted(clusters):
        members = [recs[i] for i in clusters[root]]
        if len(members) == 1:
            out.append(members[0])
            continue
        m = merge(members)
        out.append(m)
        groups.append({"rows": m.rows, "result": m.name or (m.emails[0] if m.emails else ""),
                       "reasons": reasons_by_root.get(root, [])})
    out.sort(key=lambda r: r.rows[0])
    remaining = []
    for p in review:
        if dsu.find(p["a"]) == dsu.find(p["b"]) or p.get("ai") == "different":
            continue
        remaining.append({"rows": (recs[p["a"]].rows[0], recs[p["b"]].rows[0]),
                          "a": _brief(recs[p["a"]]), "b": _brief(recs[p["b"]]), "reason": p["reason"]})
    stats = {
        "rows_in": len(recs), "rows_out": len(out), "duplicates_merged": len(recs) - len(out),
        "groups": len(groups), "field_fixes": len([c for c in changes if c["after"] != ""]),
        "invalid_dropped": len([c for c in changes if c["after"] == "" and "invalid" in c["reason"]]),
        "needs_review": len(remaining), "ai_judged": ai_used,
        "with_flags": len([r for r in out if r.flags]),
    }
    return Result(out, extra_cols, changes, groups, remaining, stats)


def _brief(r: Rec) -> dict:
    return {"name": r.name, "email": ", ".join(r.emails), "phone": ", ".join(r.phones),
            "company": r.company}
