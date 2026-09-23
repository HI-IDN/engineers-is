# Shared data for the book's data appendix and results chapters.
#
# Sourced from a chapter (working directory docs/), it reads the local databases -- when they
# exist -- into `leyfi` (licence lists), `kyn` (name-based gender estimate) and `hag`
# (Hagstofa), and defines the small helpers the chapters use inline. Each is NULL when its
# database is absent, and every chunk that uses it then renders nothing: the frozen output
# is served instead.
#
#   setwd("docs"); source("../R/gogn.R")

# --- Licence lists ----------------------------------------------------------------------
# Read once for the whole section; `leyfi` is NULL when the local database is absent, and
# every chunk below then renders nothing (the frozen output is served instead).
db <- file.path("..", "data", "licences.sqlite")
leyfi <- NULL
if (file.exists(db)) {
  con <- DBI::dbConnect(RSQLite::SQLite(), db, flags = RSQLite::SQLITE_RO)
  leyfi <- list(
    sokn = DBI::dbGetQuery(con, "
      select f.list, f.fetched_at, f.n_entries, f.n_unparsed, f.n_no_date, f.n_not_person,
             min(l.licence_year) as fyrst, max(l.licence_year) as sidast
      from fetch f join licence l using (fetch_id)
      where f.fetch_id in (select max(fetch_id) from fetch group by list)
      group by f.fetch_id order by f.list desc"),
    snid = DBI::dbGetQuery(con, "
      select list, id_format, count(*) as n, sum(century_corrected) as old
      from licence_current group by list, id_format"),
    alls = DBI::dbGetQuery(con, "
      select count(*) as n, sum(century_corrected) as old,
             sum(implausible_age) as otrulegt,
             sum(not_collected) as ekki_sott, sum(date_is_upper_bound) as fyrir
      from licence_current")
  )
  DBI::dbDisconnect(con)
  leyfi$olaesilegt <- sum(leyfi$sokn$n_unparsed)
  leyfi$ekki_einstaklingur <- sum(leyfi$sokn$n_not_person)
  leyfi$sokn$n_not_person <- NULL  # counted in prose, not a table column
  leyfi$kt <- function(fmt) sum(leyfi$snid$n[leyfi$snid$id_format %in% fmt])
}
# "1 færslu" but "2 færslur": the count with the right form of the noun (accusative).
faerslur <- function(n) {
  if (n == 1) "eina færslu" else paste(n, "færslur")
}

# --- Gender estimate --------------------------------------------------------------------
kyn <- NULL
if (file.exists(db)) {
  con <- DBI::dbConnect(RSQLite::SQLite(), db, flags = RSQLite::SQLITE_RO)
  if (DBI::dbExistsTable(con, "name_gender")) {
    kyn <- list(
      d = DBI::dbGetQuery(con, "
        select list, gender, gender_rule, given_position, gender_agreement, gender_conflict
        from licence_gender"),
      utgafa = DBI::dbGetQuery(con, "select max(kyngreinir) as v from name_gender")$v
    )
  }
  DBI::dbDisconnect(con)
}

# One table for both lists: rows are the rule that decided, columns the estimate. Below the
# total, as a caveat, the estimates whose evidence conflicts ("þar af"), by how.
kyn_tafla <- function(d = kyn$d) {
  regla <- ifelse(is.na(d$gender_rule), "Ekkert sker úr",
           ifelse(d$gender_rule == "dottir", "-dóttir",
           ifelse(d$gender_rule == "son", "-son",
           ifelse(d$given_position == 1, "Fyrsta eiginnafn", "Síðara eiginnafn"))))
  radir <- c("-dóttir", "Fyrsta eiginnafn", "Síðara eiginnafn", "-son", "Ekkert sker úr")
  kyn_d <- factor(ifelse(is.na(d$gender), "Óþekkt",
                  ifelse(d$gender == "female", "Konur", "Karlar")),
                  levels = c("Konur", "Karlar", "Óþekkt"))
  t <- as.data.frame.matrix(table(factor(regla, levels = radir), kyn_d))
  t <- rbind(t, Samtals = colSums(t))
  osamraemi <- c(given_names = "þar af stangast á: tvö eiginnöfn",
                 suffix      = "þar af stangast á: eiginnafn og -son",
                 both        = "þar af stangast á: hvort tveggja")
  ag <- as.data.frame.matrix(table(factor(d$gender_conflict, levels = names(osamraemi)), kyn_d))
  rownames(ag) <- osamraemi
  ag <- ag[rowSums(ag) > 0, , drop = FALSE]
  t <- rbind(t, ag)
  t$Alls <- rowSums(t)
  t <- cbind(Regla = rownames(t), t)
  t[] <- lapply(t, as.character)
  sam <- which(t$Regla == "Samtals")
  t[sam, ] <- paste0("**", t[sam, ], "**")
  if (nrow(t) > sam) t$Regla[(sam + 1):nrow(t)] <- paste0("*", t$Regla[(sam + 1):nrow(t)], "*")
  knitr::kable(t, row.names = FALSE, align = "lrrrr",
               caption = "Kynjamat verkfræðinga og tæknifræðinga eftir því hvaða regla réð.")
}
if (!is.null(kyn)) {
  kyn$othekkt <- sum(is.na(kyn$d$gender))
  kyn$othekkt_hlutfall <- sub(".", ",", sprintf("%.1f", 100 * kyn$othekkt / nrow(kyn$d)),
                              fixed = TRUE)
}

# --- Hagstofa ---------------------------------------------------------------------------
hdb <- file.path("..", "data", "hagstofa.sqlite")
hag <- NULL
if (file.exists(hdb)) {
  con <- DBI::dbConnect(RSQLite::SQLite(), hdb, flags = RSQLite::SQLITE_RO)
  hag <- list(
    sott = DBI::dbGetQuery(con, "select substr(max(run_at), 1, 10) as d from fetch")$d,
    mf = DBI::dbGetQuery(con, "
      select min(year) as fra, max(year) as til from population_current
      where sex = 'total' and age is null"),
    br = DBI::dbGetQuery(con, "
      select min(year) as fra, max(year) as til, min(academic_year) as fyrsta,
             max(academic_year) as sidasta from graduation_current"),
    flokkun = DBI::dbGetQuery(con, "
      select p.programme_group, p.programme, p.programme_code, p.note,
             coalesce(sum(case when g.degree_code = '2' then g.n end), 0) as bs,
             coalesce(sum(case when g.degree_code = '4' then g.n end), 0) as ms,
             coalesce(sum(case when g.degree_code = '5' then g.n end), 0) as phd
      from programme_group p
      left join graduation_current g on g.programme_code = p.programme_code and g.sex = 'total'
      group by 1, 2, 3, 4 order by 1 desc, bs + ms + phd desc"),
    # Every programme and degree, with Hagstofa's own total ('Alls'), so shares of the whole
    # are exact rather than a sum of what we happened to fetch.
    hlutfall = DBI::dbGetQuery(con, "
      select g.year, g.degree_code,
             sum(case when g.programme_code = 'Alls' then g.n end) as alls,
             sum(case when p.programme_group is not null then g.n end) as vt
      from graduation_current g
      left join programme_group p using (programme_code)
      where g.sex = 'total'
      group by 1, 2"),
    # Hagstofa's own fields: the programme code follows ÍSNÁM2008 (ISCED 97), so its first
    # digit is the broad field (almennt svið) and its first two the narrow field.
    svid = DBI::dbGetQuery(con, "
      select g.year, substr(g.programme_code, 1, 1) as svid, p.programme_group, sum(g.n) as n
      from graduation_current g
      left join programme_group p using (programme_code)
      where g.sex = 'total' and g.degree_code = 'Alls' and g.programme_code <> 'Alls'
      group by 1, 2, 3"),
    # Field against field, at master's level (degree 4), since the engineer's title
    # requires a master's degree: Hagstofa's narrow fields as they are, and verkfræði by
    # our own classification of programmes.
    greinar = DBI::dbGetQuery(con, "
      select g.year,
             case when p.programme_group = 'verkfraedi' then 'Verkfræði'
                  when p.programme_group = 'taeknifraedi' then 'Tæknifræði'
                  else substr(g.programme_code, 1, 2)
             end as grein,
             sum(g.n) as n
      from graduation_current g
      left join programme_group p using (programme_code)
      where g.sex = 'total' and g.degree_code = '4' and g.programme_code <> 'Alls'
      group by 1, 2"),
    # Students (a count in the year) and graduations (degrees that year) in our
    # verkfræði programmes, by level. The two tables code degrees differently: students
    # '1' first degree and '2' master's; graduations '2' and '4'.
    flaedi = if (DBI::dbExistsTable(con, "student")) DBI::dbGetQuery(con, "
      select 'nemendur' as maeling, s.year,
             case s.degree_code when '1' then 'grunn' when '2' then 'meistara' end as stig,
             sum(s.n) as n
      from student_current s join programme_group p using (programme_code)
      where p.programme_group = 'verkfraedi' and s.sex = 'total'
        and s.degree_code in ('1', '2')
      group by 1, 2, 3
      union all
      select 'brautskraningar', g.year,
             case g.degree_code when '2' then 'grunn' when '4' then 'meistara' end,
             sum(g.n)
      from graduation_current g join programme_group p using (programme_code)
      where p.programme_group = 'verkfraedi' and g.sex = 'total'
        and g.degree_code in ('2', '4')
      group by 1, 2, 3"),
    argangar = if (DBI::dbExistsTable(con, "cohort")) DBI::dbGetQuery(con, "
      select entry_year, outcome, measure, field, value from cohort_current
      where sex = 'total'
        and field in ('Alls', 'Verkfræði, framleiðsla og mannvirkjagerð')"),
    stada = if (DBI::dbExistsTable(con, "cohort_status")) DBI::dbGetQuery(con, "
      select table_id, grp, entry_year, years_after, outcome, value
      from cohort_status_current where measure = 'percent' and sex = 'total'"),
    nemar = if (DBI::dbExistsTable(con, "enrolment")) DBI::dbGetQuery(con, "
      select year, place, n from enrolment_current
      where field_code = '7' and sex = 'total' and place in ('iceland', 'abroad')
        and year >= 1977"),
    utan = DBI::dbGetQuery(con, "
      select programme, programme_code,
             coalesce(sum(case when degree_code = '2' then n end), 0) as bs,
             coalesce(sum(case when degree_code = '4' then n end), 0) as ms
      from graduation_current
      where sex = 'total' and programme_code in ('522j', '422a', '481c')
      group by 1, 2")
  )
  DBI::dbDisconnect(con)
}
utan <- function(kodi, dalkur) if (is.null(hag)) NA else
  hag$utan[[dalkur]][hag$utan$programme_code == kodi]
# All graduations in a year and degree ('Alls' for every degree), Hagstofa's own total.
heild <- function(ar, gr = "Alls") if (is.null(hag)) NA else
  hag$hlutfall$alls[hag$hlutfall$degree_code == gr & hag$hlutfall$year == ar]
# How many of Hagstofa's narrow fields have any master's degrees (verkfræði, which we
# count by programme, aside).
fjoldi_svida <- function() if (is.null(hag)) NA else
  length(unique(hag$greinar$grein[hag$greinar$n > 0 & grepl("^[0-9]{2}$", hag$greinar$grein)]))
