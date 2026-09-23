"""Fetch the government lists of licensed engineers (verkfræðingar) and technologists
(tæknifræðingar) into a small local SQLite database, one snapshot per fetch.

    python scripts/fetch_licences.py              # fetch both lists now
    python scripts/fetch_licences.py --db other.sqlite

Writes data/licences.sqlite (gitignored). Every run adds a snapshot; nothing is overwritten,
so a change on the live page can be traced by comparing two fetches:

  fetch     one row per list per run: URL, fetch time (UTC), HTTP status, SHA-256 of the page
            as served, parse counts, and a copy of the page with every kennitala redacted.
  licence   the parsed entries of that fetch: name, birth year, licence year and date, list,
            and two flags read off the date text (see licence_date()).
  licence_current (view)  the entries of the newest fetch of each list.

PRIVACY: each entry on the source page carries the person's kennitala. It is parsed in memory
only to get the birth year and is never written out -- not to `licence`, not to the stored page
copy, not to any log. The SHA-256 is of the original page, so an identical re-fetch can be
recognised without keeping the identifiers.

Sources (one <p> per person, "Name, kennitala|f. dd.mm.yy, title. Fær leyfi <date>."):
  https://www.stjornarradid.is/verkefni/atvinnuvegir/starfsrettindi/verkfraedingar/
  https://www.stjornarradid.is/verkefni/atvinnuvegir/starfsrettindi/taeknifraedingar/

The parsing is carried over from HI-IDN/skemman-msc scripts/fetch_engineer_licences.py.
"""
import argparse
import datetime as dt
import gzip
import hashlib
import html
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "licences.sqlite"
BASE = "https://www.stjornarradid.is/verkefni/atvinnuvegir/starfsrettindi/"
LISTS = {"verkfraedingur": BASE + "verkfraedingar/", "taeknifraedingur": BASE + "taeknifraedingar/"}
UA = "Mozilla/5.0 (research; engineers-is; contact via repo owner)"
MONTHS = {"janúar": 1, "febrúar": 2, "mars": 3, "apríl": 4, "maí": 5, "júní": 6, "júlí": 7,
          "ágúst": 8, "september": 9, "október": 10, "nóvember": 11, "desember": 12}
FIRST_LICENCE_YEAR = 1937
# Age at licensing that a birth year must give; outside it the century digit is a typo.
PLAUSIBLE_AGE = (18, 90)

# The source is hand-typed: separators, "kt.", "f." and title spellings vary. Find the id
# (kennitala or old-style birth date) anywhere; the name is whatever precedes it.
#   kennitala:  ddmmyy-nnnn, and the variants typed on the page: dd.mm.yy-nnnn, ddmmyy/nnnn,
#               ddmmyy--nnnn, ddmmyy- nnnn, ddmmyy nnnn, ddmmyynnnn
#   birth date: the older form, "f. dd.mm.yy" -- "f." optional ("f", or none at all), spaces
#               allowed, year of two or four digits; the last two digits are the birth year
# Only the text before "Fær leyfi" is searched, so the licence date is never taken for one.
ID = re.compile(
    r"(?<!\d)(\d{2}\.?\d{2}\.?\d{2}\s*(?:--?|/|\.)?\s*\d{4})(?!\d)"
    r"|(?<!\w)f\.?\s*(\d{1,2}\.\s*\d{1,2}\.\s*\d{2,4})(?!\d)"
    r"|(?<![\d.])(\d{1,2}\.\d{1,2}\.(?:\d{4}|\d{2}))(?![\d.])"
)
# A mistyped kennitala -- five or seven digits before the separator, a comma inside, or five
# after it ("#####-####", "#######-####", "###,##-####", "######-#####"). The two digits just
# before the separator are still the birth year, so the birth year is read from those
# (id_format 'kennitala_typo'). With four digits after, the last is the century; with five
# it is unclear which digit is extra, so 1900s is assumed and PLAUSIBLE_AGE corrects it.
ID_TYPO = re.compile(r"(?<!\d)(\d[\d.,]{3,8}\d)\s*(?:--?|/)\s*(\d{4,5})(?!\d)")
DATE = re.compile(r"Fær leyfi\s*(.*?)\.?\s*$")
# Anything shaped like a kennitala. Some entries repeat it after "Fær leyfi" instead of a
# date (a typo on the government page), so raw_date and the page copy are both scrubbed.
# Looser than ID on purpose -- it also catches the mistyped ones with seven digits before the
# separator or five after, which ID does not accept but which must not be stored either.
ID_SHAPED = re.compile(r"(?<!\d)\d{2}\.?\d{2}\.?\d{2,3}\s*(?:--?|/|\.)?\s*\d{4,5}(?!\d)")

SCHEMA = """
create table if not exists fetch (
    fetch_id      integer primary key,
    list          text not null,           -- 'verkfraedingur' | 'taeknifraedingur'
    url           text not null,
    fetched_at    text not null,           -- ISO 8601, UTC
    http_status   integer not null,
    sha256        text not null,           -- of the page as served, before redaction
    n_entries     integer not null,        -- rows written to licence
    n_unparsed    integer not null,        -- "Fær leyfi" paragraphs that could not be read
    n_no_date     integer not null,        -- identified, but no licence date could be read
    page_redacted blob not null            -- gzip of the page with kennitölur replaced by <kt>
);
create table if not exists licence (
    fetch_id      integer not null references fetch(fetch_id),
    list          text not null,
    name          text not null,
    birth_year    integer not null,
    id_format     text not null,           -- how the identifier was printed, see id_format()
    century_corrected integer not null,    -- 1: century digit gave an implausible age, fixed
    implausible_age integer not null,      -- 1: age at licensing outside PLAUSIBLE_AGE and no
                                           --    century fixes it; likely a typo, still counted
    licence_year  integer not null,
    licence_date  text,                    -- ISO date, null when the source gives only a year
    date_is_upper_bound integer not null,  -- 1: "fyrir dd.mm.yy", granted before that date
    not_collected integer not null,        -- 1: "dd.mm.yy hefur ekki sótt leyfi", see below
    raw_date      text not null            -- the date text as printed, kennitala-shaped redacted
);
create index if not exists licence_fetch on licence(fetch_id);
create view if not exists licence_current as
select l.*
from licence l
join (select list, max(fetch_id) as fetch_id from fetch group by list) f using (list, fetch_id);
"""


def id_format(printed):
    """How the identifier was printed -- stored instead of the identifier itself.

    kennitala          ddmmyy-nnnn, as it should be
    kennitala_variant  ten digits, another separator: dd.mm.yy-nnnn, ddmmyy/nnnn, ddmmyy--nnnn, ...
    kennitala_typo     five or seven digits before the separator, or a comma inside
    birth_date         the older "f. dd.mm.yy", "f." optional, year of two or four digits
    """
    if printed is None:
        return "kennitala_typo"
    if printed.startswith("f."):
        return "birth_date"
    return "kennitala" if re.fullmatch(r"\d{6}-\d{4}", printed) else "kennitala_variant"


def is_person_kennitala(digits):
    """A person's kennitala starts with a real day and month. A company's has the day plus 40
    (41-71), so it gives no birth year and the entry is left unread."""
    return 1 <= int(digits[:2]) <= 31 and 1 <= int(digits[2:4]) <= 12


def parse_entry(text):
    dm = DATE.search(text)
    if not dm:
        return None
    head = text[:dm.start()]
    idm = ID.search(head)
    if idm and not idm.group(1):
        # Birth date, with or without "f.": keep only the last two digits of the year.
        d, mo, y = re.sub(r"\s", "", idm.group(2) or idm.group(3)).split(".")
        ident, fmt = f"f. {d}.{mo}.{y[-2:]}", id_format("f.")
    elif idm:
        fmt = id_format(idm.group(1))
        ident = idm.group(1)
        if idm.group(1):
            digits = re.sub(r"\D", "", idm.group(1))
            if not is_person_kennitala(digits):
                return None
            ident = digits[:6] + "-" + digits[6:]
    else:
        idm = ID_TYPO.search(head)
        digits = re.sub(r"\D", "", idm.group(1)) if idm else ""
        tail = idm.group(2) if idm else ""
        if not 5 <= len(digits) <= 7 or (len(digits) == 6 and len(tail) == 4):
            return None
        century = tail[-1] if len(tail) == 4 else "9"
        ident, fmt = digits[-2:].rjust(6, "0") + "-000" + century, id_format(None)
    name = re.sub(r"[\s,.]*(?:f\.|kt\.?)?[\s,.]*$", "", head[:idm.start()]).strip()
    return {"name": name, "id": ident, "date": dm.group(1), "fmt": fmt} if name else None


def year2(yy):
    """Two-digit year -> full year; licences start in 1937, so 27..99 -> 19xx else 20xx."""
    return 1900 + yy if yy > 26 else 2000 + yy


def birth_year(ident):
    if "-" in ident:  # ddmmyy-xxxx, last digit is the century (9 -> 1900s, 0 -> 2000s, 8 -> 1800s)
        yy, c = int(ident[4:6]), ident[-1]
        return {"9": 1900, "0": 2000, "8": 1800}.get(c, 1900) + yy
    m = re.search(r"\.(\d{2,4})$", ident)
    y = int(m.group(1))
    return y if y > 999 else 1900 + y


# A licence date is the first date in the text after "Fær leyfi". What follows it varies and
# is kept in raw_date: "Endurútgefið dd.mm.yy", "og dd.mm.yy", "Látinn", and the note
# "dd.mm.yy hefur ekki sótt leyfi" -- from a one-time cleanup of the lists (dated 1999): the
# licence was granted, but the holder never collected the document. It is still a licence.
# A three-digit year ("30.05.022") is a typo; only the year matters, so its last two digits
# are read as a two-digit year.
DATE_NUM = re.compile(r"(?<!\d)(\d{1,2})\s*[.,]+\s*(\d{1,2})\s*[.,]*\s*(\d{2,4})(?!\d)")
DATE_MONTH = re.compile(r"(?<!\d)(\d{1,2})\.?\s*([a-záéíóúýþæö]+)\.?\s*(\d{2}|\d{4})(?!\d)", re.I)
YEAR_ONLY = re.compile(r"(?<!\d)(1[89]\d\d|20\d\d)(?!\d)")


def licence_date(raw, this_year):
    """(year, ISO date or None, date_is_upper_bound, not_collected) from the text after
    "Fær leyfi". Tolerates 06.07.18, 12,05.82, 12.05 82, 1.júní 2026, 8.maí.25, 1975."""
    raw = ID_SHAPED.sub("", raw)  # a kennitala typed where the date belongs is not a date
    found = []
    for m in DATE_NUM.finditer(raw):
        d, mo = int(m[1]), int(m[2])
        if 1 <= d <= 31 and 1 <= mo <= 12:
            found.append((m.start(), d, mo, m[3]))
            break
    for m in DATE_MONTH.finditer(raw):
        if m[2].lower() in MONTHS:
            found.append((m.start(), int(m[1]), MONTHS[m[2].lower()], m[3]))
            break
    upper = False
    if found:
        start, d, mo, y = min(found)
        y = int(y) if len(y) == 4 else year2(int(y[-2:]))
        iso = f"{y:04d}-{mo:02d}-{d:02d}"
        upper = "fyrir" in raw[:start].lower()
    else:
        m = YEAR_ONLY.search(raw)
        y, iso = (int(m[1]), None) if m else (None, None)
    if y is not None and not FIRST_LICENCE_YEAR <= y <= this_year:  # typos in the source
        y, iso = None, None
    return y, iso, upper, "hefur ekki sótt" in raw.lower()


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, r.read()


def parse_page(page, lst, this_year):
    """Entries of one page, plus counts of the paragraphs that were skipped."""
    rows, unparsed, no_date = [], 0, 0
    for p in re.findall(r"<p>(.*?)</p>", page, re.S):
        text = html.unescape(re.sub(r"<[^>]+>", "", p)).replace("\xa0", " ").strip()
        if "Fær leyfi" not in text:
            continue
        m = parse_entry(re.sub(r"\s+", " ", text))
        if not m:
            unparsed += 1
            continue
        year, iso, upper, not_collected = licence_date(m["date"], this_year)
        if year is None:
            no_date += 1
            continue
        if m["fmt"] == "birth_date" and iso:
            # The licence date typed again where the birth date belongs is not a birth date. Day,
            # month and year must all match: a birthday can share the licence's day and month.
            d, mo, yy = m["id"][3:].split(".")
            if (int(d), int(mo), yy) == (int(iso[8:10]), int(iso[5:7]), iso[2:4]):
                unparsed += 1
                continue
        born, corrected, implausible = birth_year(m["id"]), False, False
        if not PLAUSIBLE_AGE[0] <= year - born <= PLAUSIBLE_AGE[1]:
            # A mistyped century digit (e.g. 0 for 9: born "2070", licensed 2007). Take the
            # century that gives a plausible age at licensing, if exactly one does. Otherwise
            # the typo is elsewhere (licence date, birth year digits); the licence still counts,
            # with the birth year as printed.
            fits = [c + born % 100 for c in (1800, 1900, 2000)
                    if PLAUSIBLE_AGE[0] <= year - (c + born % 100) <= PLAUSIBLE_AGE[1]]
            if len(fits) == 1:
                born, corrected = fits[0], True
            else:
                implausible = True
        rows.append((lst, m["name"].strip(), born, m["fmt"], int(corrected), int(implausible),
                     year, iso, int(upper),
                     int(not_collected), ID_SHAPED.sub("<kt>", m["date"].strip())))
    # The source prints a few people twice, once with the date spelled out and once numeric:
    # the same grant, so it counts once.
    seen, unique = set(), []
    for r in rows:
        key = (r[0], r[1], r[2], r[6], r[7])  # list, name, birth year, licence year and date
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique, unparsed, no_date


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = ap.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(args.db)
    con.executescript(SCHEMA)
    for lst, url in LISTS.items():
        fetched_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
        status, raw = fetch(url)
        page = raw.decode("utf-8", "replace")
        rows, unparsed, no_date = parse_page(page, lst, fetched_at.year)
        redacted = gzip.compress(ID_SHAPED.sub("<kt>", page).encode("utf-8"))
        with con:
            cur = con.execute(
                "insert into fetch (list, url, fetched_at, http_status, sha256, n_entries, "
                "n_unparsed, n_no_date, page_redacted) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (lst, url, fetched_at.isoformat(), status, hashlib.sha256(raw).hexdigest(),
                 len(rows), unparsed, no_date, redacted))
            con.executemany(
                "insert into licence (fetch_id, list, name, birth_year, id_format, century_corrected, "
                "implausible_age, licence_year, licence_date, date_is_upper_bound, not_collected, "
                "raw_date) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(cur.lastrowid, *r) for r in rows])
        print(f"{lst}: {len(rows)} entries, {unparsed} unparsed, "
              f"{no_date} without a readable licence date", file=sys.stderr)
    con.close()
    print(f"written to {args.db}", file=sys.stderr)


if __name__ == "__main__":
    main()
