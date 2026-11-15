/* Sanguity · helpers shared by every page: $, el (build DOM), api (call the backend).
   Plain script, loaded first; all files share one global scope. */
"use strict";
const $ = s => document.querySelector(s);
const el = (tag, attrs = {}, ...kids) => {
  const e = tag.startsWith("svg:") ? document.createElementNS("http://www.w3.org/2000/svg", tag.slice(4)) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "text") e.textContent = v;
    else if (k === "class") e.setAttribute("class", v);
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(e.style, v);
    else e.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null) e.append(c.nodeType ? c : document.createTextNode(c));
  return e;
};
/* every backend call: same-origin cookie, never cached, and the X-Sanguity header the API requires on changes */
const api = (path, opts = {}) => fetch("api/" + path, {cache: "no-store", credentials: "same-origin", ...opts,
  headers: {...(opts.method && opts.method !== "GET" ? {"X-Sanguity": "1", "Content-Type": "application/json"} : {}), ...opts.headers}});
