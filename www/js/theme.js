// The saved light/dark choice, applied before the page draws (loaded in <head>, so there's no flash of the other theme).
try { const t = localStorage.getItem("ht-theme"); if (t) document.documentElement.dataset.theme = t; } catch (e) {}
