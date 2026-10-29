#!/usr/bin/env bash
# Génère les deux versions de la présentation à partir de la source unique slides.md :
#   index.html  -> reveal.js   (touche S : vue présentateur, ?print-pdf : export PDF)
#   remark.html -> remark      (touche P : mode présentateur, C : cloner la fenêtre)
#
# Conventions de slides.md, compréhensibles par les deux moteurs :
#   ---            séparateur de slides
#   ???            début des notes de l'orateur
#   class: nom     en première ligne d'une slide : classe CSS de la slide (title, demo)
#
# Usage (Git Bash sous Windows, ou tout shell POSIX) :  ./build.sh
set -euo pipefail
cd "$(dirname "$0")"

# Le contenu est placé dans un <textarea> : on échappe & et < ; les deux moteurs retrouvent le texte d'origine.
escape() { sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g'; }

# ------------------------------------------------------------------ reveal.js
{
cat <<'HTML'
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vector Databases — pgvector vs ClickHouse</title>
<link rel="stylesheet" href="lib/reveal/reveal.min.css">
<link rel="stylesheet" href="lib/reveal/github.min.css">
<link rel="stylesheet" href="theme.css">
</head>
<body>
<div class="reveal"><div class="slides">
<section data-markdown data-separator="^---$" data-separator-notes="^\?\?\?$">
<textarea data-template>
HTML
sed -E 's/^class: *([A-Za-z0-9_-]+) *$/<!-- .slide: class="\1" -->/' slides.md | escape
cat <<'HTML'
</textarea>
</section>
</div></div>
<script src="lib/reveal/reveal.min.js"></script>
<script src="lib/reveal/plugin/markdown.min.js"></script>
<script src="lib/reveal/plugin/highlight.min.js"></script>
<script src="lib/reveal/plugin/notes.min.js"></script>
<script>
  Reveal.initialize({
    width: 1280, height: 720, margin: 0.04, center: false,
    hash: true, slideNumber: "c/t", transition: "fade", transitionSpeed: "fast",
    pdfSeparateFragments: false,
    plugins: [RevealMarkdown, RevealHighlight, RevealNotes]
  });
</script>
</body>
</html>
HTML
} > index.html

# ------------------------------------------------------------------ remark
{
cat <<'HTML'
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vector Databases — pgvector vs ClickHouse (remark)</title>
<link rel="stylesheet" href="theme.css">
<style>
  body { margin: 0; }
  .remark-slide-container { background: #e9ecf2; }
  .remark-slide-scaler { box-shadow: 0 8px 30px rgba(20, 30, 60, .18); }
  .remark-code-line-highlighted { background: rgba(57, 73, 171, .12); }
</style>
</head>
<body>
<textarea id="source">
HTML
escape < slides.md
cat <<'HTML'
</textarea>
<script src="lib/remark/remark.min.js"></script>
<script>
  remark.create({
    ratio: "16:9",
    highlightLanguage: "sql",
    highlightStyle: "github",
    countIncrementalSlides: false,
    slideNumberFormat: "%current% / %total%"
  });
</script>
</body>
</html>
HTML
} > remark.html

echo "OK : index.html (reveal.js) et remark.html (remark) générés depuis slides.md ($(grep -c '^---$' slides.md) séparateurs)."
