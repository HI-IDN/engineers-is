#set page(
  header: context {
    grid(
      columns: (1fr, auto),
      [#text(size: 9pt)[Ingimundardóttir]],
      [#align(right)[#text("Icelandic Journal of Engineering // Verktækni (" + str(datetime.today().year()) + ")", size: 9pt)]],
    )
  },
  footer: context {
    grid(
      columns: (1fr, auto),
      [#text("Verkfræðingafélag Íslands // Association of Chartered Engineers in Iceland - https://www.ije.is", size: 7pt)],
      [#align(right)[#counter(page).display()]],
    )
  },
)
