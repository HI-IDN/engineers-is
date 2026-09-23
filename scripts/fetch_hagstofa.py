"""Fetch Hagstofa Íslands' population and graduation tables into data/hagstofa.sqlite.

    python scripts/fetch_hagstofa.py

Reads the PX-Web API at px.hagstofa.is. Every run adds a snapshot, as fetch_licences.py does:

  fetch        one row per query: table id and title, URL, the query sent, the run it belongs
               to and its fetch time (UTC), SHA-256 of the response, row count, and the
               response itself (gzip). A table fetched in several queries has one row each.
  population   MAN08000, mean population (meðalmannfjöldi) by year, sex and single year of age.
               age is null for all ages; sex is 'total', 'male', 'female' or 'other'.
  graduation   SKO04205c, graduations (brautskráningar) by academic year, programme, degree
               and sex. `year` is the academic year's second calendar year: 1995-1996 -> 1996.
               A graduation is a degree awarded, not a person; one person can count twice.
  enrolment    SKO04103, students (nemendur) at university level 1975-2006 by field, sex and
               where they studied: 'iceland' or 'abroad' ('total' for both). A count of
               enrolled students, not of graduations; the only field-level series before 1996.
  programme_group  config/programme_groups.csv: which programmes count as verkfræði and which
               as tæknifræði. Replaced whole each run; the CSV is the source.
  programme_field  config/programme_fields.csv: every programme's HÍ school (svið) -- 'FVS',
               'HVS', 'HUG', 'MVS', 'VON' or 'HOL' (Háskóli Íslands á Hólum). Replaced whole
               each run.
  population_current, graduation_current (views)  the newest fetch of each table.

These are aggregate public statistics; nothing here concerns individuals.
"""
import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "hagstofa.sqlite"
PROGRAMME_GROUPS = ROOT / "config" / "programme_groups.csv"
PROGRAMME_FIELDS = ROOT / "config" / "programme_fields.csv"
API = "https://px.hagstofa.is/pxis/api/v1/is/"
POPULATION = "Ibuar/mannfjoldi/1_yfirlit/Yfirlit_mannfjolda/MAN08000.px"
GRADUATION = "Samfelag/skolamal/4_haskolastig/1_hsProf/SKO04205c.px"
ENROLMENT = "Samfelag/skolamal/4_haskolastig/3_hseldra/SKO04103.px"
PLACE = {"Alls": "total", "2": "iceland", "3": "abroad"}
SEX = {"Total": "total", "Alls": "total", "1": "male", "2": "female", "3": "other"}
# How PX-Web marks a cell with no value.
MISSING = {"..", ".", "-", ""}

SCHEMA = """
create table if not exists fetch (
    fetch_id     integer primary key,
    table_id     text not null,        -- e.g. 'MAN08000'
    title        text not null,
    url          text not null,
    query        text not null,        -- the JSON query sent
    run_at       text not null,        -- when this run started (ISO 8601, UTC): one per run
    fetched_at   text not null,        -- when this query was answered (ISO 8601, UTC)
    sha256       text not null,        -- of the response body
    n_rows       integer not null,
    response     blob not null         -- gzip of the response body
);
create table if not exists population (
    fetch_id     integer not null references fetch(fetch_id),
    year         integer not null,
    sex          text not null,
    age          integer,              -- null: all ages
    n            real not null
);
create table if not exists graduation (
    fetch_id       integer not null references fetch(fetch_id),
    academic_year  text not null,      -- as published, '1995-1996'
    year           integer not null,   -- its second calendar year
    programme_code text not null,      -- 'Alls' for all programmes
    programme      text not null,
    degree_code    text not null,      -- 'Alls', or '1'..'5' (ISCED 5B, first degree, ...)
    degree         text not null,
    sex            text not null,
    n              integer not null
);
create table if not exists enrolment (
    fetch_id     integer not null references fetch(fetch_id),
    year         integer not null,
    place        text not null,        -- 'total', 'iceland' or 'abroad'
    field_code   text not null,        -- 'Alls', or '0'..'9'; '7' is Tæknigreinar, verkfræði
    field        text not null,
    sex          text not null,
    n            integer not null
);
drop table if exists programme_group;
create table programme_group (
    programme_code  text primary key,
    programme       text not null,
    programme_group text not null,     -- 'verkfraedi' | 'taeknifraedi'
    note            text
);
drop table if exists programme_field;
create table programme_field (
    programme_code  text primary key,
    programme       text not null,
    svid            text not null,     -- HÍ school: FVS, HVS, HUG, MVS, VON, HOL
    note            text               -- why, when it differs from the ISCED field
);
create view if not exists population_current as
select * from population where fetch_id in (
    select fetch_id from fetch where table_id = 'MAN08000' and run_at = (
        select max(run_at) from fetch where table_id = 'MAN08000'));
create view if not exists enrolment_current as
select * from enrolment where fetch_id in (
    select fetch_id from fetch where table_id = 'SKO04103' and run_at = (
        select max(run_at) from fetch where table_id = 'SKO04103'));
create view if not exists graduation_current as
select * from graduation where fetch_id in (
    select fetch_id from fetch where table_id = 'SKO04205c' and run_at = (
        select max(run_at) from fetch where table_id = 'SKO04205c'));
"""


def metadata(path):
    with urllib.request.urlopen(API + path, timeout=60) as r:
        return json.load(r)


def query(path, selection):
    """POST a PX-Web query; returns (the response body, parsed)."""
    q = {"query": selection, "response": {"format": "json"}}
    req = urllib.request.Request(API + path, data=json.dumps(q).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        body = r.read()
    return q, body, json.loads(body)


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


RUN_AT = now()


def record_fetch(con, table_id, title, path, q, body, n_rows):
    cur = con.execute(
        "insert into fetch (table_id, title, url, query, run_at, fetched_at, sha256, n_rows, "
        "response) values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (table_id, title, API + path, json.dumps(q, ensure_ascii=False), RUN_AT, now(),
         hashlib.sha256(body).hexdigest(), n_rows, gzip.compress(body)))
    return cur.lastrowid


def fetch_population(con):
    meta = metadata(POPULATION)
    q, body, data = query(POPULATION, [])
    rows = []
    for d in data["data"]:
        sex, age, year = d["key"]
        if d["values"][0] in MISSING:  # e.g. 'Kynsegin/annað' before it was recorded
            continue
        rows.append((int(year), SEX[sex], None if age == "-1" else int(age), float(d["values"][0])))
    with con:
        fid = record_fetch(con, "MAN08000", meta["title"], POPULATION, q, body, len(rows))
        con.executemany("insert into population values (?, ?, ?, ?, ?)",
                        [(fid, *r) for r in rows])
    return len(rows)


def fetch_graduation(con):
    """One query per degree: the whole table at once is refused as too large."""
    meta = metadata(GRADUATION)
    names = {v["code"]: dict(zip(v["values"], v["valueTexts"])) for v in meta["variables"]}
    total = 0
    for degree in names["Prófgráða"]:
        selection = [{"code": "Prófgráða", "selection": {"filter": "item", "values": [degree]}}]
        q, body, data = query(GRADUATION, selection)
        rows = []
        for d in data["data"]:
            programme, deg, academic_year, sex = d["key"]
            value = d["values"][0]
            if value in MISSING:
                continue
            rows.append((academic_year, int(academic_year[-4:]), programme,
                         names["Námsbraut"][programme].strip(), deg,
                         names["Prófgráða"][deg].strip(), SEX[sex], int(float(value))))
        with con:
            fid = record_fetch(con, "SKO04205c", meta["title"], GRADUATION, q, body, len(rows))
            con.executemany("insert into graduation values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            [(fid, *r) for r in rows])
        total += len(rows)
    return total


def fetch_enrolment(con):
    meta = metadata(ENROLMENT)
    names = {v["code"]: dict(zip(v["values"], v["valueTexts"])) for v in meta["variables"]}
    q, body, data = query(ENROLMENT, [])
    rows = []
    for d in data["data"]:
        place, field, sex, year = d["key"]
        if d["values"][0] in MISSING:
            continue
        rows.append((int(year), PLACE[place], field, names["námsbraut"][field].strip(), SEX[sex],
                     int(float(d["values"][0]))))
    with con:
        fid = record_fetch(con, "SKO04103", " ".join(meta["title"].split()), ENROLMENT, q, body,
                           len(rows))
        con.executemany("insert into enrolment values (?, ?, ?, ?, ?, ?, ?)",
                        [(fid, *r) for r in rows])
    return len(rows)


def load_programme_groups(con):
    with PROGRAMME_GROUPS.open(encoding="utf-8") as f:
        rows = [(r["programme_code"], r["programme"], r["programme_group"], r["note"] or None)
                for r in csv.DictReader(f)]
    with con:
        con.executemany("insert into programme_group values (?, ?, ?, ?)", rows)
    return len(rows)


def load_programme_fields(con):
    with PROGRAMME_FIELDS.open(encoding="utf-8") as f:
        rows = [(r["programme_code"], r["programme"], r["svid"], r["note"] or None)
                for r in csv.DictReader(f)]
    with con:
        con.executemany("insert into programme_field values (?, ?, ?, ?)", rows)
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = ap.parse_args()
    args.db.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(args.db)
    con.executescript(SCHEMA)
    print(f"population: {fetch_population(con)} rows", file=sys.stderr)
    print(f"graduation: {fetch_graduation(con)} rows", file=sys.stderr)
    print(f"enrolment: {fetch_enrolment(con)} rows", file=sys.stderr)
    print(f"programme_group: {load_programme_groups(con)} programmes", file=sys.stderr)
    print(f"programme_field: {load_programme_fields(con)} programmes", file=sys.stderr)
    con.close()
    print(f"written to {args.db}", file=sys.stderr)


if __name__ == "__main__":
    main()
