"""Fetch the government lists of licensed engineers (verkfræðingar) and technologists
(tæknifræðingar) into a small local SQLite database, one snapshot per fetch.

    python scripts/fetch_licences.py              # fetch both lists now
    python scripts/fetch_licences.py --db other.sqlite

Writes data/licences.sqlite (gitignored). Every run adds a snapshot; nothing is overwritten,
so a change on the live page can be traced by comparing two fetches:

  fetch     one row per list per run: URL, fetch time (UTC), HTTP status, SHA-256 of the page
            as served, parse counts, and a copy of the page with every kennitala redacted.
  licence   the parsed entries of that fetch: name, birth year, licence year and date, list.
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

# The source is hand-typed: separators, "kt.", "f." and title spellings vary. Find the id
# (kennitala or old-style birth date) anywhere; the name is whatever precedes it.
#   kennitala:  ddmmyy-nnnn, and the variants typed on the page: dd.mm.yy-nnnn, ddmmyy/nnnn,
#               ddmmyy--nnnn, ddmmyy- nnnn, ddmmyy nnnn, ddmmyynnnn
#   birth date: "f. dd.mm.yy", also "f. dd. mm.yy" and "f dd.mm.yy"
ID = re.compile(
    r"(?<!\d)(\d{2}\.?\d{2}\.?\d{2}\s*(?:--?|/|\.)?\s*\d{4})(?!\d)"
    r"|(?<!\w)f\.?\s*(\d{1,2}\.\s*\d{1,2}\.\s*\d{2,4})(?!\d)"
)
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
    n_not_licensed integer not null,       -- listed, but the page says never applied
    page_redacted blob not null            -- gzip of the page with kennitölur replaced by <kt>
);
create table if not exists licence (
    fetch_id      integer not null references fetch(fetch_id),
    list          text not null,
    name          text not null,
    birth_year    integer not null,
    licence_year  integer not null,
    licence_date  text,                    -- ISO date, null when the source gives only a year
    raw_date      text not null            -- the date as printed, kennitala-shaped text redacted
);
create index if not exists licence_fetch on licence(fetch_id);
create view if not exists licence_current as
select l.*
from licence l
join (select list, max(fetch_id) as fetch_id from fetch group by list) f using (list, fetch_id);
"""


def parse_entry(text):
    idm, dm = ID.search(text), DATE.search(text)
    if not idm or not dm:
        return None
    ident = idm.group(1) or "f. " + re.sub(r"\s", "", idm.group(2))
    if idm.group(1):
        digits = re.sub(r"\D", "", idm.group(1))
        ident = digits[:6] + "-" + digits[6:]
    name = re.sub(r"[\s,.]*(?:f\.|kt\.?)?[\s,.]*$", "", text[:idm.start()]).strip()
    return {"name": name, "id": ident, "date": dm.group(1)} if name else None


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


def licence_date(raw):
    """Return (year, ISO date or None), tolerating 06.07.18 / 1.júní 2026 / 4.10.2001 / 1975."""
    raw = raw.strip().rstrip(".")
    m = re.match(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{2,4})$", raw)
    if m:
        d, mo, y = int(m[1]), int(m[2]), int(m[3])
        y = y if y > 999 else year2(y)
        return y, f"{y:04d}-{mo:02d}-{d:02d}"
    m = re.match(r"(\d{1,2})\.\s*([a-záéíóúýþæö]+)\s*(\d{4})$", raw, re.I)
    if m and m[2].lower() in MONTHS:
        return int(m[3]), f"{int(m[3]):04d}-{MONTHS[m[2].lower()]:02d}-{int(m[1]):02d}"
    m = re.search(r"(\d{4})", raw)
    return (int(m[1]), None) if m else (None, None)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, r.read()


def parse_page(page, lst, this_year):
    """Entries of one page, plus counts of the paragraphs that were skipped."""
    rows, unparsed, not_licensed = [], 0, 0
    for p in re.findall(r"<p>(.*?)</p>", page, re.S):
        text = html.unescape(re.sub(r"<[^>]+>", "", p)).replace("\xa0", " ").strip()
        if "Fær leyfi" not in text:
            continue
        m = parse_entry(re.sub(r"\s+", " ", text))
        if not m or not 1880 <= birth_year(m["id"]) <= this_year - 15:
            unparsed += 1
            continue
        year, iso = licence_date(m["date"])
        if year is not None and not FIRST_LICENCE_YEAR <= year <= this_year:  # typos in the source
            year, iso = None, None
        if year is None:
            # "... hefur ekki sótt leyfi": qualified and listed, but never licensed.
            not_licensed += 1
            continue
        rows.append((lst, m["name"].strip(), birth_year(m["id"]), year, iso,
                     ID_SHAPED.sub("<kt>", m["date"].strip())))
    # The source prints a few people twice, once with the date spelled out and once numeric:
    # the same grant, so it counts once.
    seen, unique = set(), []
    for r in rows:
        key = r[:5]
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique, unparsed, not_licensed


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
        rows, unparsed, not_licensed = parse_page(page, lst, fetched_at.year)
        redacted = gzip.compress(ID_SHAPED.sub("<kt>", page).encode("utf-8"))
        with con:
            cur = con.execute(
                "insert into fetch (list, url, fetched_at, http_status, sha256, n_entries, "
                "n_unparsed, n_not_licensed, page_redacted) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (lst, url, fetched_at.isoformat(), status, hashlib.sha256(raw).hexdigest(),
                 len(rows), unparsed, not_licensed, redacted))
            con.executemany(
                "insert into licence (fetch_id, list, name, birth_year, licence_year, "
                "licence_date, raw_date) values (?, ?, ?, ?, ?, ?, ?)",
                [(cur.lastrowid, *r) for r in rows])
        print(f"{lst}: {len(rows)} entries, {unparsed} unparsed, "
              f"{not_licensed} listed but not licensed", file=sys.stderr)
    con.close()
    print(f"written to {args.db}", file=sys.stderr)


if __name__ == "__main__":
    main()
