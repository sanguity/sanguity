// The theme, applied before the page draws (loaded in <head>, so there's no flash of the other theme): this browser's
// own choice (the light/dark toggle, "ht-theme") if it has one, else the account's default theme (#45) as last seen here
// ("ht-theme-default", kept by applyTheme in util.js), else the system's. And the person's In-range results, when Green
// (#1108, "ht-inrange", kept there too).
try { const t = localStorage.getItem("ht-theme") || localStorage.getItem("ht-theme-default"); if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
  if (localStorage.getItem("ht-inrange") === "green") document.documentElement.dataset.inrange = "green"; } catch (e) {}
