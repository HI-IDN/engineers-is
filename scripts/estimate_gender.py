"""Estimate the gender of every licence holder's name into data/licences.sqlite.

    python scripts/estimate_gender.py

Uses kyngreinir (https://github.com/HI-IDN/kyngreinir): -dóttir, then the given names in
Þjóðskrá's name register, then -son. Replaces the table `name_gender` whole, one row per
distinct name in `licence`, so a re-run after a new fetch or a new kyngreinir is a re-run.

  name_gender   name, gender ('female' | 'male' | null), rule ('dottir' | 'given_name' | 'son'),
                the given name that decided, the agreement of the evidence ('unanimous' |
                'conflicting' | 'none'), how many parts say female and male, and the kyngreinir
                version that made the estimate.
  licence_gender (view)  licence_current with its name's estimate.

This is an estimate from naming convention only, not anyone's sex or gender, and is reported in
aggregate with the unknowns.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

import kyngreinir

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "licences.sqlite"

SCHEMA = """
drop table if exists name_gender;
create table name_gender (
    name        text primary key,
    gender      text,
    rule        text,
    given_name  text,
    agreement   text not null,
    n_female    integer not null,
    n_male      integer not null,
    kyngreinir  text not null
);
drop view if exists licence_gender;
create view licence_gender as
select l.*, g.gender, g.rule as gender_rule, g.agreement as gender_agreement
from licence_current l
left join name_gender g using (name);
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    names = [r[0] for r in con.execute("select distinct name from licence order by name")]
    rows = []
    for name in names:
        ex = kyngreinir.explain(name)
        rows.append((name, ex.estimate.gender, ex.estimate.rule, ex.estimate.given_name,
                     ex.agreement, ex.female, ex.male, kyngreinir.__version__))
    with con:
        con.executescript(SCHEMA)
        con.executemany("insert into name_gender values (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    for rule, gender, n in con.execute(
            "select gender_rule, gender, count(*) from licence_gender group by 1, 2 order by 3 desc"):
        print(f"  {gender or 'unknown':7} {rule or '-':11} {n}", file=sys.stderr)
    con.close()
    print(f"{len(rows)} names estimated with kyngreinir {kyngreinir.__version__}", file=sys.stderr)


if __name__ == "__main__":
    main()
