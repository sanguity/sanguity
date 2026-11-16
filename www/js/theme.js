// The theme, applied before the page draws (loaded in <head>, so there's no flash of the other theme): this browser's
// own choice (the ◐ toggle, "ht-theme") if it has one, else the account's default theme (#45) as last seen here
// ("ht-theme-default", kept by applyTheme in util.js), else the system's.
try { const t = localStorage.getItem("ht-theme") || localStorage.getItem("ht-theme-default"); if (t === "light" || t === "dark") document.documentElement.dataset.theme = t; } catch (e) {}
