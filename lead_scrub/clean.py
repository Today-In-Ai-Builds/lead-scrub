"""Field-level cleanup: names, emails, phones, companies. Pure functions."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

import phonenumbers
from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

TITLES = {"mr", "mrs", "ms", "miss", "dr", "prof", "sir", "mx"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "md", "phd", "esq", "dds", "cpa"}
ACRONYMS = {"hvac", "cpa", "llc", "llp", "pllc", "usa", "ai", "tv", "hr", "it", "pc", "uk", "dj", "md", "dds"}
PARTICLES = {"van", "von", "der", "de", "da", "del", "di", "du", "la", "le", "bin"}
NICKNAMES = {
    "bob": "robert", "rob": "robert", "bobby": "robert", "bill": "william", "will": "william",
    "billy": "william", "mike": "michael", "jim": "james", "jimmy": "james", "liz": "elizabeth",
    "beth": "elizabeth", "betty": "elizabeth", "kate": "katherine", "kathy": "katherine",
    "katie": "katherine", "tom": "thomas", "tommy": "thomas", "dave": "david", "steve": "steven",
    "chris": "christopher", "jen": "jennifer", "jenny": "jennifer", "matt": "matthew",
    "joe": "joseph", "tony": "anthony", "dan": "daniel", "danny": "daniel", "rick": "richard",
    "rich": "richard", "dick": "richard", "pat": "patricia", "sue": "susan", "peggy": "margaret",
    "meg": "margaret", "alex": "alexander", "andy": "andrew", "nick": "nicholas", "ed": "edward",
    "ted": "edward", "jeff": "jeffrey", "greg": "gregory", "sam": "samuel", "ben": "benjamin",
}
ROLE_LOCALS = {
    "info", "sales", "admin", "support", "contact", "hello", "office", "team", "billing",
    "accounts", "accounting", "service", "help", "mail", "enquiries", "inquiries", "orders",
    "noreply", "no-reply", "webmaster", "marketing", "hr", "jobs",
}
FREE_DOMAINS = {
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com", "aol.com", "live.com",
    "msn.com", "comcast.net", "me.com", "mail.com", "proton.me", "protonmail.com", "ymail.com",
    "gmx.com", "zoho.com", "att.net", "verizon.net", "sbcglobal.net",
}
COMPANY_NOISE = {"inc", "llc", "corp", "corporation", "co", "company", "ltd", "limited", "llp",
                 "lp", "plc", "the", "incorporated", "pllc", "pc"}
_SUFFIX_FIX = {"llc": "LLC", "l.l.c.": "LLC", "l.l.c": "LLC", "inc": "Inc.", "inc.": "Inc.",
               "incorporated": "Inc.", "corp": "Corp.", "corp.": "Corp.", "corporation": "Corp.",
               "ltd": "Ltd.", "ltd.": "Ltd.", "llp": "LLP", "pllc": "PLLC"}

EMAIL_RE = re.compile(
    r"^[a-z0-9._%+'\-]+@[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9\-]*[a-z0-9])?)*\.[a-z]{2,}$"
)
_BAD_TLDS = {"con": "com", "cmo": "com", "ocm": "com", "vom": "com", "comm": "com",
             "nett": "net", "ogr": "org"}
_INVISIBLE = "\u200b\u200c\u200d\ufeff"


def ws(s: str) -> str:
    """Drop zero-width characters and collapse all whitespace to single spaces."""
    s = "".join(c for c in (s or "") if c not in _INVISIBLE)
    return re.sub(r"\s+", " ", s).strip()


def fold(s: str) -> str:
    """Casefold and strip accents."""
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).casefold()


# ---------------------------------------------------------------- names
def _cap_token(tok: str, first: bool) -> str:
    low = tok.lower()
    if not first and low in PARTICLES:
        return low
    out = []
    for part in re.split(r"([\-'])", low):
        if part in ("-", "'"):
            out.append(part)
            continue
        part = part.capitalize()
        if part.startswith("Mc") and len(part) > 2:
            part = "Mc" + part[2:].capitalize()
        out.append(part)
    return "".join(out)


def clean_name(raw: str) -> str:
    s = ws(raw)
    if not s:
        return ""
    # "Last, First" -> "First Last" (only when the tail is not just a suffix like Jr.)
    if s.count(",") == 1:
        head, tail = [p.strip() for p in s.split(",")]
        if head and tail and fold(tail).strip(".") not in SUFFIXES:
            s = f"{tail} {head}"
    s = ws(s.replace(",", " "))
    letters = [c for c in s if c.isalpha()]
    if letters and (all(c.isupper() for c in letters) or all(c.islower() for c in letters)):
        s = " ".join(_cap_token(t, i == 0) for i, t in enumerate(s.split(" ")))
    else:  # mixed case is trusted, except SHOUTED words ("Jo-Ann O'NEILL") and lowercase stragglers
        s = " ".join(_cap_token(t, i == 0) if _shouted(t) or _lowered(t) else t for i, t in enumerate(s.split(" ")))
    return re.sub(r"\b(Ii|Iii|Iv)\b", lambda m: m.group(1).upper(), s)


def _lowered(tok: str) -> bool:
    return tok.isalpha() and tok.islower() and tok not in PARTICLES


def _shouted(tok: str) -> bool:
    letters = [c for c in tok if c.isalpha()]
    return len(letters) >= 3 and all(c.isupper() for c in letters) and tok not in {"III"}


@lru_cache(maxsize=None)
def name_key(name: str) -> str:
    """Comparison key: folded, no titles/suffixes/punctuation, nickname-normalised first name."""
    toks = [t for t in re.split(r"[^a-z0-9]+", fold(name)) if t]
    toks = [t for t in toks if t not in TITLES and t not in SUFFIXES]
    if toks:
        toks[0] = NICKNAMES.get(toks[0], toks[0])
    return " ".join(toks)


def name_similarity(a: str, b: str) -> float:
    """0-100. Handles reordered tokens, nicknames and a first-name initial."""
    ka, kb = name_key(a), name_key(b)
    if not ka or not kb:
        return 0.0
    ta, tb = ka.split(), kb.split()
    if len(ta) >= 2 and len(tb) >= 2 and ta[-1] == tb[-1]:
        fa, fb = ta[0], tb[0]
        if fa == fb:
            return 100.0
        if (len(fa) == 1 and fb.startswith(fa)) or (len(fb) == 1 and fa.startswith(fb)):
            return 92.0
    return float(fuzz.token_sort_ratio(ka, kb))


# ---------------------------------------------------------------- email
def split_multi(raw: str) -> list[str]:
    return [p for p in re.split(r"[;,\s|]+", ws(raw)) if p]


def clean_email(raw: str) -> tuple[str, str | None]:
    """Return (cleaned_or_empty, note). Note is None when valid and not altered by a typo fix."""
    e = ws(raw).strip("<>\"'.,;:() ").lower()
    e = re.sub(r"^mailto:", "", e)
    if not e:
        return "", None
    if not EMAIL_RE.match(e) or ".." in e:
        return "", f"invalid email '{ws(raw)}'"
    local, domain = e.rsplit("@", 1)
    fixed = fix_domain(domain)
    if fixed != domain:
        return f"{local}@{fixed}", f"domain typo '{domain}' -> '{fixed}'"
    return e, None


def fix_domain(domain: str) -> str:
    """Repair near-misses of well-known mailbox providers (gmial.com -> gmail.com)."""
    if domain in FREE_DOMAINS:
        return domain
    head, _, tld = domain.rpartition(".")
    if tld in _BAD_TLDS:
        domain = f"{head}.{_BAD_TLDS[tld]}"
        if domain in FREE_DOMAINS:
            return domain
    best = None
    for known in FREE_DOMAINS:
        if len(known) < 9:  # too short to trust a 1-edit match (e.g. aol.com, me.com)
            continue
        d = Levenshtein.distance(domain, known)
        if d <= 2 and (best is None or d < best[0]):
            best = (d, known)
    return best[1] if best else domain


def is_role_email(email: str) -> bool:
    return email.split("@", 1)[0] in ROLE_LOCALS


def email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1]


# ---------------------------------------------------------------- phone
def clean_phone(raw: str, region: str = "US") -> tuple[str, str | None]:
    s = ws(raw)
    if not s:
        return "", None
    try:
        num = phonenumbers.parse(s, region)
    except phonenumbers.NumberParseException:
        return "", f"invalid phone '{s}'"
    if not phonenumbers.is_valid_number(num):
        return "", f"invalid phone '{s}'"
    out = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
    if num.extension:
        out += f" x{num.extension}"
    return out, None


def phone_id(phone: str) -> str:
    """Identity of a phone for matching: E.164 without extension."""
    return phone.split(" ")[0]


# ---------------------------------------------------------------- company
def clean_company(raw: str) -> str:
    s = ws(raw)
    if not s:
        return ""
    letters = [c for c in s if c.isalpha()]
    if letters and (all(c.isupper() for c in letters) or all(c.islower() for c in letters)):
        s = " ".join(w.upper() if w.lower() in ACRONYMS else _cap_token(w, True) for w in s.split(" "))
    toks = s.split(" ")
    if len(toks) > 1:
        fixed = _SUFFIX_FIX.get(toks[-1].lower().rstrip(","))
        if fixed:
            toks[-1] = fixed
            toks[-2] = toks[-2].rstrip(",")
    return " ".join(toks)


@lru_cache(maxsize=None)
def company_key(company: str) -> str:
    s = fold(company).replace("&", " and ")
    return " ".join(t for t in re.split(r"[^a-z0-9]+", s) if t and t not in COMPANY_NOISE)


def company_similarity(a: str, b: str) -> float:
    ka, kb = company_key(a), company_key(b)
    if not ka or not kb:
        return 0.0
    return float(fuzz.ratio(ka, kb))
