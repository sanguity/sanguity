# Accessibility: WCAG 2.2 AA

Sanguity aims to meet [WCAG 2.2](https://www.w3.org/TR/WCAG22/) at level AA on every page: the records (Overview,
Body and activity, Medications, Timeline, Labs, Costs), the event drawer, Sources, Sharing, Settings, Account, Admin, the
first-run guide, the provider finder, sign-in, setup and a shared page (`share.html`). This page lists each
criterion, how it was checked, what was fixed in the review (#202), and what's still open.

Two kinds of check keep it that way. **Automated**, on every pull request (`tests/browser_check.py`, in CI):

- **axe-core** (Deque's engine, version 4.12.1, MPL-2.0) runs its WCAG 2.0, 2.1 and 2.2 A and AA rules on about a
  hundred pages and states in light and dark: each records tab (Labs as charts and as a heatmap, both also as
  tables), the drawer for a lab result, a claim, a medication, a letter and a visit, the app's own dialog (with a
  box to type in), the More… period menu, a kind's ⋯ menu, the top bar's two menus, every group of Sources,
  Sharing, Settings, Account and Admin, the guide, the provider finder and the sign-in page; and, under the real
  Content-Security-Policy, the shared page, setup, and a fresh install's empty tabs. A serious or critical finding
  fails the check; moderate and minor ones are listed under a passing one. axe comes from the
  [`axe-playwright-python`](https://pypi.org/project/axe-playwright-python/) package, pinned and hash-checked in
  `requirements-dev.txt`; only its bundled `axe.min.js` is used, put into the page by Playwright's `evaluate`, which
  the CSP doesn't apply to. The app never loads it, and its CSP is unchanged.
- **The keyboard**: Tab from the top and Shift+Tab from the bottom of each records tab and a group of each page, at
  375 px where the top bar is tallest: every stop must look different when it has focus, and none may be wholly
  under the sticky top bar or a sticky header.
- **Reflow and spacing**: every records tab and every group of every page at 320 px (1280 px at 400% zoom), and at
  375 px with WCAG's text spacing applied (line height 1.5, letter spacing 0.12em, word spacing 0.16em, paragraph
  spacing 2em): nothing scrolls sideways, and no control is cut off or overlaps another.
- **Forced colours** (Windows' high contrast): the filled buttons keep an edge.
- **Sign-in** fields carry `autocomplete` (username, current-password, one-time-code) so a password manager fills
  them, and pasting isn't stopped.

The earlier checks still run too: the skip link, page titles and the shared status line (#209), the app's own
dialog (#210), charts with tables and out-of-range marks that aren't colour alone (#60), every page at 375 px (#61).

**By hand**, in Chromium (Playwright, light, dark and forced colours, 1400, 375 and 320 px): the list below.
What can't be done here is a pass with a real screen reader: see [Still open](#still-open).

## The criteria

| Criterion | How it was checked | What the review changed |
| --- | --- | --- |
| 1.1.1 Non-text content | axe; read the code | Nothing: icons (Lucide's, from one SVG sprite since #257, in place of Unicode glyphs) are `aria-hidden` in a button or link that is named (the ✕ "Remove …", the ✎ "Rename …"), or labelled; ▲ and ▼ stay text, the out-of-range marks; charts have a label and a table (#60). A document shown in a frame is named by its title now. An ECG recording's trace (#182) has no table of its 15,360 samples, which would say nothing a table can show: it takes focus with a summary of what it is (length, sample rate, lead, the device's classification), ← and → step through it saying the voltage, a table beside it sums it up, and Download as CSV gives every sample. |
| 1.3.1 Info and relationships | axe; read the code | Claims, paid amounts, prescriptions, a medication's listings and fills, and a result's components each open from a button in the row (below). A Costs column's sort is said with `aria-sort`. A field marked wrong when typing lab results in points to the message saying why (`aria-describedby`). |
| 1.3.2 Meaningful sequence, 1.3.3 Sensory characteristics | read the pages | Nothing found. |
| 1.3.4 Orientation | 375 and 320 px, a phone on its side | Nothing found: both orientations work. |
| 1.3.5 Identify input purpose | read the forms | Nothing found: sign-in, setup and the account page's fields have `autocomplete` (username, current-password, new-password, one-time-code). |
| 1.4.1 Use of colour | #60, #193; looked again; #250 | **Fixed** (#250): out of range is ▲/▼ as well as red, and a lab day on the Timeline with a result out of range has a red ▲ over it (it was a 2 px red dot). On the Labs charts, procedures and imaging were ▼ like a result below range, told apart only by colour: a procedure is a ● now and imaging a ■, in the legend too. On/off chips differ by a tick and tint. |
| 1.4.3 Contrast (minimum) | axe on every state, both themes | **Fixed.** The muted grey was 3.4:1 on the light theme (`--muted`, 5.2:1 on the page since #254); filled blue buttons with white text were 4.4:1 in light and 3.6:1 in dark (an `--accent-fill` token, navy since #254: 8.2:1 and 6.4:1); blue text on dark cards and red text in dark were under 4.5:1 (lighter there). The tokens are in `www/css/tokens.css`, one copy that every page loads, sign-in and setup included (#251), each a light and dark pair since #254, measured below ([The colours](#the-colours)). The Costs running totals' end labels were in their lines' orange and green (about 3.1:1 and 2.7:1 in light); they're in ink now, each after a short line in its colour (#250). |
| 1.4.4 Resize text | 200% zoom (700 px wide) | Nothing found beyond reflow, below. |
| 1.4.5 Images of text | read the pages | None used. |
| 1.4.10 Reflow | 320 px, every page and group | **Fixed.** The Overview's columns and the Costs charts were 9–23 px too wide at 320 px. The top bar stops sticking when the window is under 500 px tall (zoomed a long way in, or a phone on its side), so it doesn't take half the screen. |
| 1.4.11 Non-text contrast | measured the tokens (`tools/contrast.py`) | **Fixed.** A text field's edge was about 1.3:1 against the page; it's 3:1 now (`--field-line`), keeping red for a field marked wrong. Focus rings are the accent, over 3:1 in both themes ([The colours](#the-colours)). |
| 1.4.12 Text spacing | the WCAG spacing at 375 px, every page | Nothing found. |
| 1.4.13 Content on hover or focus | read the code | **Fixed.** A chart's hover readout goes with Escape too, the pointer left where it is. It never covers what it's about, and moving onto it keeps it (it lets the pointer through). **Since #261** the app's own tooltips (`data-tip`, `util.js`) take the place of every `title` that said something the screen doesn't: shown on hover and on keyboard focus, above or below their element and never over it, kept while the pointer is on them, put away by Escape with the pointer and focus left where they are, and no timer; the browser check hovers, moves onto, escapes and tabs to one. A `title` stays only where it repeats what's said, on a disabled control and on a frame. **Since #358** a tip on something that can't take focus has its words where the keyboard and a screen reader get them as well (on the screen, in the element's name, or as a focusable control's tip and description), and a tip is no description where the name already says it; the browser check reads them in Chromium's accessibility tree. |
| 2.1.1 Keyboard | Tab through every page; read every click handler | **Fixed.** The Overview's out-of-range and recent rows, the Costs bar lists, the Timeline's events, claims, paid amounts, prescriptions, a medication's listings and fills, a result's components and the Costs column headers opened or sorted by mouse only, or took focus without saying what they were. Each is a button now (`pressable()` in `util.js` for a row, `.cell-btn` in a table's cell). |
| 2.1.2 No keyboard trap | Tab and Shift+Tab walks; the drawer, dialog, menus and rename box | Nothing found: Escape closes each, and focus goes back. Since #262 the dialog and the drawer are one `<dialog>` (`openDialog`, `util.js`): modal where it covers the page (the browser keeps focus in it and the page inert, no script of ours), beside the page otherwise; the shared status line moves into a modal dialog while it's open, so what's said is heard. |
| 2.1.4 Character key shortcuts | read the code | None: every shortcut is on a focused control (arrows, Home, End, Escape). **Since #273** "/" opens the command palette (`palette.js`) when focus isn't in a field you type in: a key on its own, so the switch at the palette's foot turns it off (this browser remembers), Ctrl+K or ⌘K still opening it; the browser check turns it off and on. |
| 2.2.1 Timing adjustable | read the code | Nothing timed while someone works; a sign-in lasts 12 hours, or 30 days with a second factor. A toast with something to do (an Undo) stays until it's done or put away (#362: on a touch screen nothing could keep it before); one that only says something (#263) stays at least 10 seconds, waits while the pointer is on it or focus is in it, and has a ✕. |
| 2.2.2 Pause, stop, hide; 2.3.1 Three flashes | read the code | Nothing moves by itself; less motion when asked (#209). |
| 2.4.1 Bypass blocks, 2.4.2 Page titled | #209 | Nothing new. |
| 2.4.3 Focus order | Tab walks | Nothing found. |
| 2.4.4 Link purpose, 2.4.6 Headings and labels | read the pages; each page's heading outline | The two search boxes that had only a placeholder are named ("Search events", "Search claims"); the Costs type-of-care list is named. **Fixed** (#331): every page has one h1, its own title (the brand in the top bar is a paragraph, not a heading), the Overview's cards are h3s under a section's h2, and no level is skipped; the browser check lists each page's outline and runs axe's heading rules. |
| 2.4.5 Multiple ways | — | The tabs, the top bar's menus, the sidebars and each view's own address. |
| 2.4.7 Focus visible | Tab walks, every stop compared focused and not | **Fixed.** A menu's item with focus was only tinted 4%; it's ringed. A date field reached by Shift+Tab (its calendar button has focus) showed nothing; it's ringed too. One rule in `base.css` (the base layer, #251), which every page loads, sign-in and setup too (#310), rings everything that takes focus by keyboard in the accent, 2 px out; a component sets its own only to differ (inset in a segmented control or a menu, an SVG mark's thicker stroke). |
| 2.4.11 Focus not obscured (minimum) | Tab and Shift+Tab walks at 375 px | **Fixed.** What gets focus is scrolled clear of the sticky top bar (its height kept as `--bar-h` by `nav.js`), a scrolling table's header row, a Settings group's sticky Save bar, the lab list's own sticky header, and the toasts (#362: the page scrolls by their height, `--toasts-h`, and its foot can scroll up above them; in the drawer they're in its flow, under its content, over nothing in it, taking at most a third of it and no more than leaves its body half, scrolling within themselves with the newest in view (#369); a question, `ask()`, leaves them behind it). |
| 2.5.1 Pointer gestures, 2.5.7 Dragging movements | read the code | Nothing to fix: a chart's drag and pinch have the period buttons and dates, ← → keys, and a table; the key-lab tiles and tabs reorder with ↑ ↓ buttons and a ⋯ menu as well as by drag. The drawer as a phone's sheet (#262) closes when swiped down, and by its ✕ or Escape. |
| 2.5.2 Pointer cancellation, 2.5.4 Motion actuation | read the code | Nothing found. |
| 2.5.3 Label in name | read the labels | Nothing found: labels start with the visible words. |
| 2.5.8 Target size (minimum) | axe's target-size rule | **Fixed.** The ✎ (rename) is a 24 px target, and a button in a table's cell is 24 px tall, without changing the rows' height. |
| 3.1.1 Language of page | axe | Nothing found. |
| 3.2.1 On focus, 3.2.2 On input | read the code | Nothing new: picking a profile in the profile menu switches whose records are shown, which is what it's for and what its group says (#264). |
| 3.2.3, 3.2.4 Consistent navigation and identification | looked at every page | The same top bar on every page: the profile menu, the records tabs, Add and your menu (#264). |
| 3.2.6 Consistent help | looked at every page | Getting started is in the same menu on every page; a card's background is behind the ⓘ beside its heading, the same button in the same place on every page (#280). |
| 3.3.1 Error identification, 3.3.3 Error suggestion | read the forms | Errors are said in words beside the form and through the shared alert line (#209). Typed-in lab rows link the message to the field (above). |
| 3.3.2 Labels or instructions | axe | See 2.4.6. |
| 3.3.4 Error prevention | read the code | Removing, revoking and undoing ask first (#210); imports are previewed and can be undone. |
| 3.3.7 Redundant entry | read the forms | Nothing asked twice, except a new password, typed again to confirm it (allowed for security). |
| 3.3.8 Accessible authentication (minimum) | automated (above) | Nothing to fix: password managers can fill the sign-in, paste works in every field, and passkeys need no typing at all. The setup code can be pasted. |
| 4.1.2 Name, role, value | axe; read the code | **Fixed.** Rows that opened something on click are buttons (2.1.1). A paid amount's row said `aria-expanded` on a table row, which a row can't have; its date is the button that says it. The heatmap's row names were focusable inside a picture, so screen readers couldn't reach them; they're clicked there, and renamed by keyboard from the ✎ in the heatmap's table (or a chart). A chart's table that scrolls takes focus, named by its columns, so it can be scrolled by keyboard. |
| 4.1.3 Status messages | #209 | **Since #263** a save, an import or a removal that can be undone is said by a toast, in one polite live region inside a landmark named Notifications on every page with app.css, said once (the page's own line beside the control shows it without saying it again). Alt+N takes focus to the newest toast (not while typing in a field, where Option+N types ñ on a Mac, #361) and Escape gives it back; no toast takes focus by itself. Focus that went into a toast goes back where it came from when the toast goes, however it goes, and stays on a button while its action runs (#361). While a modal dialog is open, the status line is inside it, so it's heard, and so are the toasts while the drawer is open (a modal sheet on a phone), so they can be used; a question, `ask()`, leaves the toasts where they were, inert behind it, so one asked from a toast's Undo doesn't show that toast under its buttons and Tab from them doesn't reach it (#369). |

## The colours

The colours (#254, Direction A of #243) are warm neutrals with a navy accent for controls, links and the focus ring
only (the data's blue is `--c-lab`), red for what's out of range or wrong, and green for what's in range or done.
Nothing a chart draws is in the accent (#334): a milestone's rule and label are `--milestone`, a near-black (near-white
in dark) neutral, and a chart's cursor `--cursor`, a grey, since every hue already names a kind of record. Each
is one token in `www/css/tokens.css`, declared once as a `light-dark()` pair: the system's theme picks the colour
unless the person chose one (`data-theme` on `<html>`), and `tests/browser_check.py` checks that both ways give the
same colours. A card is white on the warm page in light; in dark each step up (page, card, a field or menu on a card)
is lighter.

This table is measured, not estimated: `python3 tools/contrast.py` resolves the tokens in Chromium in the system's
light and dark and in a chosen light and dark theme, lays a translucent token over the surface it's on, and prints
the WCAG contrast ratio of each pair, failing if one is under what it needs (4.5:1 for text, 3:1 for an edge or a
focus ring) or if the system's and the chosen theme disagree. The last four rows are decorative steps, shown for
reference: a card stands off the page by its white and its edge, not by contrast.

| Foreground | Background | What it is | Light | Dark | Needs |
|---|---|---|---:|---:|---:|
| `--ink` | `--page` | text | 16.12 (#1a1915 on #f6f5f1) | 15.97 (#eeece6 on #121110) | 4.5:1 |
| `--ink` | `--surface` | text | 17.59 (#1a1915 on #ffffff) | 14.87 (#eeece6 on #1a1917) | 4.5:1 |
| `--ink` | `--raised` | text | 17.59 (#1a1915 on #ffffff) | 13.63 (#eeece6 on #22211e) | 4.5:1 |
| `--ink` | `--hover` on `--surface` | text | 14.89 (#1a1915 on #eeece6) | 12.51 (#eeece6 on #292825) | 4.5:1 |
| `--ink` | `--hover` on `--raised` | text | 14.89 (#1a1915 on #eeece6) | 11.30 (#eeece6 on #302f2c) | 4.5:1 |
| `--ink2` | `--page` | secondary text | 8.49 (#4a4740 on #f6f5f1) | 10.39 (#c4c0b6 on #121110) | 4.5:1 |
| `--ink2` | `--surface` | secondary text | 9.27 (#4a4740 on #ffffff) | 9.67 (#c4c0b6 on #1a1917) | 4.5:1 |
| `--ink2` | `--raised` | secondary text | 9.27 (#4a4740 on #ffffff) | 8.86 (#c4c0b6 on #22211e) | 4.5:1 |
| `--ink2` | `--hover` on `--surface` | secondary text | 7.84 (#4a4740 on #eeece6) | 8.13 (#c4c0b6 on #292825) | 4.5:1 |
| `--ink2` | `--hover` on `--raised` | secondary text | 7.84 (#4a4740 on #eeece6) | 7.35 (#c4c0b6 on #302f2c) | 4.5:1 |
| `--muted` | `--page` | muted text | 5.17 (#6b675e on #f6f5f1) | 6.63 (#9d998f on #121110) | 4.5:1 |
| `--muted` | `--surface` | muted text | 5.63 (#6b675e on #ffffff) | 6.18 (#9d998f on #1a1917) | 4.5:1 |
| `--muted` | `--raised` | muted text | 5.63 (#6b675e on #ffffff) | 5.66 (#9d998f on #22211e) | 4.5:1 |
| `--muted` | `--hover` on `--surface` | muted text | 4.77 (#6b675e on #eeece6) | 5.20 (#9d998f on #292825) | 4.5:1 |
| `--muted` | `--hover` on `--raised` | muted text | 4.77 (#6b675e on #eeece6) | 4.69 (#9d998f on #302f2c) | 4.5:1 |
| `--accent` | `--page` | a link, a selected control's text | 7.48 (#1f4f8f on #f6f5f1) | 8.89 (#8db4ee on #121110) | 4.5:1 |
| `--accent` | `--surface` | a link, a selected control's text | 8.16 (#1f4f8f on #ffffff) | 8.28 (#8db4ee on #1a1917) | 4.5:1 |
| `--accent` | `--raised` | a link, a selected control's text | 8.16 (#1f4f8f on #ffffff) | 7.59 (#8db4ee on #22211e) | 4.5:1 |
| `--accent` | `--hover` on `--surface` | a link, a selected control's text | 6.91 (#1f4f8f on #eeece6) | 6.96 (#8db4ee on #292825) | 4.5:1 |
| `--accent` | `--hover` on `--raised` | a link, a selected control's text | 6.91 (#1f4f8f on #eeece6) | 6.29 (#8db4ee on #302f2c) | 4.5:1 |
| `--crit` | `--page` | out of range, an error | 5.99 (#b3261e on #f6f5f1) | 7.66 (#f2877c on #121110) | 4.5:1 |
| `--crit` | `--surface` | out of range, an error | 6.54 (#b3261e on #ffffff) | 7.14 (#f2877c on #1a1917) | 4.5:1 |
| `--crit` | `--raised` | out of range, an error | 6.54 (#b3261e on #ffffff) | 6.54 (#f2877c on #22211e) | 4.5:1 |
| `--crit` | `--hover` on `--surface` | out of range, an error | 5.53 (#b3261e on #eeece6) | 6.00 (#f2877c on #292825) | 4.5:1 |
| `--crit` | `--hover` on `--raised` | out of range, an error | 5.53 (#b3261e on #eeece6) | 5.42 (#f2877c on #302f2c) | 4.5:1 |
| `--good-text` | `--page` | in range, done | 6.00 (#1e6b34 on #f6f5f1) | 8.56 (#6bc08a on #121110) | 4.5:1 |
| `--good-text` | `--surface` | in range, done | 6.55 (#1e6b34 on #ffffff) | 7.97 (#6bc08a on #1a1917) | 4.5:1 |
| `--good-text` | `--raised` | in range, done | 6.55 (#1e6b34 on #ffffff) | 7.31 (#6bc08a on #22211e) | 4.5:1 |
| `--good-text` | `--hover` on `--surface` | in range, done | 5.54 (#1e6b34 on #eeece6) | 6.71 (#6bc08a on #292825) | 4.5:1 |
| `--good-text` | `--hover` on `--raised` | in range, done | 5.54 (#1e6b34 on #eeece6) | 6.06 (#6bc08a on #302f2c) | 4.5:1 |
| `--on-accent` | `--accent-fill` | a filled button's text | 8.16 (#ffffff on #1f4f8f) | 6.39 (#ffffff on #2f5fa3) | 4.5:1 |
| `--on-accent` | `--crit-fill` | a filled button's text, for something that can't be undone | 6.54 (#ffffff on #b3261e) | 6.54 (#ffffff on #b3261e) | 4.5:1 |
| `--field-line` | `--raised` | a field's edge, against its inside | 3.63 (#8a867c on #ffffff) | 3.41 (#77736a on #22211e) | 3:1 |
| `--field-line` | `--surface` | a field's edge, against the card | 3.63 (#8a867c on #ffffff) | 3.72 (#77736a on #1a1917) | 3:1 |
| `--field-line` | `--page` | a field's edge, against the page (sign-in, setup) | 3.33 (#8a867c on #f6f5f1) | 3.99 (#77736a on #121110) | 3:1 |
| `--accent` | `--page` | the focus ring | 7.48 (#1f4f8f on #f6f5f1) | 8.89 (#8db4ee on #121110) | 3:1 |
| `--accent` | `--surface` | the focus ring | 8.16 (#1f4f8f on #ffffff) | 8.28 (#8db4ee on #1a1917) | 3:1 |
| `--accent` | `--raised` | the focus ring | 8.16 (#1f4f8f on #ffffff) | 7.59 (#8db4ee on #22211e) | 3:1 |
| `--crit` | `--raised` | a field marked wrong | 6.54 (#b3261e on #ffffff) | 6.54 (#f2877c on #22211e) | 3:1 |
| `--milestone` | `--surface` | a milestone's label on a chart (its dashed rule needs 3) | 14.10 (#2e2b26 on #ffffff) | 12.35 (#dcd8ce on #1a1917) | 4.5:1 |
| `--cursor` | `--surface` | a chart's cursor, a crosshair or an ECG trace's | 5.63 (#6b675e on #ffffff) | 6.18 (#9d998f on #1a1917) | 3:1 |
| `--surface` | `--page` | a card against the page | 1.09 (#ffffff on #f6f5f1) | 1.07 (#1a1917 on #121110) | – |
| `--ring` | `--page` | a card's edge against the page | 1.21 (#e3e0d8 on #f6f5f1) | 1.34 (#2c2b2a on #121110) | – |
| `--grid` | `--surface` | a divider, a chart's grid | 1.32 (#e3e0d8 on #ffffff) | 1.26 (#2e2c28 on #1a1917) | – |
| `--axis` | `--surface` | a chart's axis | 1.82 (#c4c0b6 on #ffffff) | 1.58 (#3e3b36 on #1a1917) | – |

The kinds of record keep their colours (`--c-*`, unchanged until Redesign 7): a mark in one is never the only way
to tell what it is, and text about it is in ink.

## Still open

- **A real screen-reader pass.** The review was done in Chromium with axe-core and by reading the code and the
  accessibility tree; it couldn't run VoiceOver, NVDA, JAWS or TalkBack. Worth doing on: the charts (role
  `application` with arrow keys, #60, and an ECG's trace, #182), the Timeline's picture and its list, the drawer, the period menu and the
  kinds' ⋯ menus, the Settings reorderable lists, and the import preview.
- **Other browsers.** Firefox and Safari weren't checked here (CI runs Chromium). Their focus rings and date
  fields differ.
- **Charts' own marks in forced colours.** In Chromium the charts' lines and points keep their own colours in high
  contrast rather than the person's theme's; the text, the controls and the tables beside each chart follow it.
- **Documents from a portal** (letters, reports, HTML pages shown in the drawer) are shown as the portal made
  them; their own accessibility isn't Sanguity's to fix.
- **Enhanced (AAA) criteria** weren't targeted: 1.4.6 contrast 7:1, 2.4.13 focus appearance, 2.5.5 44 px targets.
