# Engineers in Iceland

`engineers-is` is a focused research project on the development and renewal of
the legally protected engineering and engineering-technologist professions in
Iceland. It is a spin-off from
[`HI-IDN/skemman-msc`](https://github.com/HI-IDN/skemman-msc) and reuses data
infrastructure from
[`HI-IDN/skemman-harvester`](https://github.com/HI-IDN/skemman-harvester).

The working research book is intended for publication at
<https://hi-idn.github.io/engineers-is/>. It is a public research log: methods,
source decisions, code and provisional results remain reviewable while the
paper develops.

## Research focus

The main question is:

> How has entry into the legally protected engineering and engineering-
> technologist professions in Iceland developed from 1962/1965 to 2026 in
> scale, age and estimated gender composition, and how is that development
> associated with demography, graduations, engineering education, institutions
> and regulation?

The project distinguishes between interest in engineering education,
graduations, entry into the register of protected professional titles and the
size of the active profession. Those are different phenomena and require
different evidence.

## Repository boundaries

- **`skemman-harvester`** owns collection, parsing, caching and the normalized
  data model.
- **`skemman-msc`** owns the broader longitudinal study of Icelandic MSc
  theses and engineering education.
- **`icename-gender`** owns the name-based gender estimate and its copy of
  Þjóðskrá's name register.
- **`engineers-is`** owns the longitudinal analysis of professional-title
  licences, population denominators, graduation flows and the institutional
  history needed to interpret them.

The Skemman author-to-licence linkage is a secondary cohort analysis here, not
the definition of the profession or the main outcome.

## Helper package

Gender is estimated from names with
[`icename-gender`](https://github.com/HI-IDN/icename-gender), a small Python package split out for
reuse: *-dóttir*, then the given names in Þjóðskrá's name register, then *-son*. It is an
estimate from naming convention only and is reported in aggregate.

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install git+https://github.com/HI-IDN/icename-gender.git

python scripts/fetch_licences.py     # the licence lists, into data/licences.sqlite
python scripts/estimate_gender.py    # name_gender, from icename-gender
python scripts/fetch_hagstofa.py     # population and graduations, into data/hagstofa.sqlite
```

## Working method

Development takes place on the `profession-history` branch. When the research
book reaches a coherent milestone, the branch can be squash-merged into
`main`. The documentation is a Quarto book; render it locally with:

```powershell
quarto render
```

The rendered site is written to `site/`. Large or sensitive source data remain
local and are not committed.

## License

MIT. See [LICENSE](LICENSE).
