"""Estimate the gender of every licence holder's name into data/licences.sqlite.

    python scripts/estimate_gender.py

Uses icename-gender (https://github.com/HI-IDN/icename-gender): -dóttir, then the given names in
Þjóðskrá's name register, then -son. Replaces the table `name_gender` whole, one row per
distinct name in `licence`, so a re-run after a new fetch or a new icename-gender is a re-run.

  name_gender   name, gender ('female' | 'male' | null), rule ('dottir' | 'given_name' | 'son'),
                the given name that decided and its position among the given names (1 = the
                first; icename-gender passes over unisex, middle and unregistered names), the
                agreement of the evidence ('unanimous' | 'conflicting' | 'none') and, when it
                conflicts, how ('given_names': two given names disagree; 'suffix': -dóttir/-son
                disagrees with the estimate; 'both'), how many parts say female and male, and
                the icename-gender version that made the estimate.
  licence_gender (view)  licence_current with its name's estimate.

This is an estimate from naming convention only, not anyone's sex or gender, and is reported in
aggregate with the unknowns.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

import icename_gender
from icename_gender.classify import tokens

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "licences.sqlite"

SCHEMA = """
drop table if exists name_gender;
create table name_gender (
    name        text primary key,
    gender      text,
    rule        text,
    given_name  text,
    given_position integer,
    agreement   text not null,
    conflict    text,
    n_female    integer not null,
    n_male      integer not null,
    icename_gender text not null
);
drop view if exists licence_gender;
create view licence_gender as
select l.*, g.gender, g.rule as gender_rule, g.given_position, g.agreement as gender_agreement,
       g.conflict as gender_conflict
from licence_current l
left join name_gender g using (name);
"""


def given_position(name, est):
    """1-based position of the given name that decided, or None if no given name did."""
    if est.rule != "given_name":
        return None
    given = tokens(name)[:-1] or tokens(name)
    for i, g in enumerate(given, 1):
        if est.given_name in (g, *g.split("-")):
            return i
    return None


def conflict(ex):
    """How conflicting evidence conflicts: two given names disagree, the suffix disagrees with
    the estimate, or both. None unless the agreement is 'conflicting'."""
    if ex.agreement != "conflicting":
        return None
    said = {e.says for e in ex.evidence if e.source == "register"} & {"female", "male"}
    names = len(said) == 2
    suffix = any(e.source == "suffix" and e.says != ex.estimate.gender for e in ex.evidence)
    return "both" if names and suffix else "given_names" if names else "suffix"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    names = [r[0] for r in con.execute("select distinct name from licence order by name")]
    rows = []
    for name in names:
        ex = icename_gender.explain(name)
        est = ex.estimate
        rows.append((name, est.gender, est.rule, est.given_name, given_position(name, est),
                     ex.agreement, conflict(ex), ex.female, ex.male, icename_gender.__version__))
    with con:
        con.executescript(SCHEMA)
        con.executemany("insert into name_gender values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    for rule, gender, n in con.execute(
            "select gender_rule, gender, count(*) from licence_gender group by 1, 2 order by 3 desc"):
        print(f"  {gender or 'unknown':7} {rule or '-':11} {n}", file=sys.stderr)
    con.close()
    print(f"{len(rows)} names estimated with icename-gender {icename_gender.__version__}", file=sys.stderr)


if __name__ == "__main__":
    main()
