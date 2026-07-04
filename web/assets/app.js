// Edge Card — progressive enhancement. Content is pre-rendered; JS only
// enhances (theme, and later: search / date switch / copy-for-AI).
(function () {
  // --- theme toggle (persisted) ---
  var KEY = "ec-theme";
  var saved = null;
  try { saved = localStorage.getItem(KEY); } catch (e) {}
  if (saved) document.documentElement.setAttribute("data-theme", saved);

  document.addEventListener("click", function (e) {
    var t = e.target.closest("[data-theme-toggle]");
    if (!t) return;
    var cur = document.documentElement.getAttribute("data-theme") || "dark";
    var next = cur === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem(KEY, next); } catch (e) {}
  });

  // --- Copy-for-AI: copies the sheet's embedded markdown to the clipboard ---
  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-copy-ai]");
    if (!b) return;
    var src = document.getElementById("ai-markdown");
    if (!src) return;
    var md = src.textContent || "";
    navigator.clipboard.writeText(md).then(function () {
      var old = b.textContent; b.textContent = "✓ Copied";
      setTimeout(function () { b.textContent = old; }, 1600);
    });
  });
})();
