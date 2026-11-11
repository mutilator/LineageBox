// Light or dark, as chosen in this browser (More → Colours, app.js), put on the page before it is drawn, so it never
// shows the other colours first. Loaded in the <head> of every page, before the stylesheet: the site's pages have no
// inline scripts (the Content-Security-Policy in web/common.py allows only the site's own files). Nothing kept, or the
// browser keeping nothing, means "as the computer or phone is set": the stylesheet follows prefers-color-scheme.
try {
  const theme = localStorage.getItem("lineagebox-theme");
  if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
} catch {}

// Photo tiles (More → Photo tiles, app.js), the same way: Whole photos rather than squares, and Small or Large rather
// than Medium. app.css sizes and lays out the grids by them.
try {
  const root = document.documentElement;
  if (localStorage.getItem("lineagebox-tiles") === "whole") root.dataset.tiles = "whole";
  const size = localStorage.getItem("lineagebox-tile-size");
  if (size === "s" || size === "l") root.dataset.tileSize = size;
} catch {}
