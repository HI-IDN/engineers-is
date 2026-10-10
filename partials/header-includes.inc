<script>
  // Browser-tab title: the book, then the page ("Verkfræðingar á Íslandi – Gögn og uppruni").
  // Quarto's default puts the chapter first, with its number; the front page shows the book alone.
  (function () {
    var book = "Verkfræðingar á Íslandi";
    var fixTitle = function () {
      var h = document.querySelector("h1 .chapter-title") || document.querySelector("h1.title");
      var page = h ? h.textContent.trim() : "";
      document.title = page && page !== book ? book + " – " + page : book;
    };
    fixTitle();
    document.addEventListener("DOMContentLoaded", fixTitle);
  })();
</script>
