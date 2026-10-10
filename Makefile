# Render the research book and the pages published beside it under site/.
# The book renders first: it rebuilds site/, and the article and slides render into it.

.PHONY: all book grein kynning preview

all: book grein kynning

book:
	quarto render --no-execute-daemon

grein:
	quarto render article

kynning:
	quarto render kynning

preview: kynning
	quarto preview kynning
