# Accessibility: WCAG 2.2 AA

Sanguity aims to meet [WCAG 2.2](https://www.w3.org/TR/WCAG22/) at level AA on every page: the records (Overview,
Body and activity, Timeline, Labs, Costs), the event drawer, Sources, Sharing, Settings, Account, Admin, the
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
| 1.1.1 Non-text content | axe; read the code | Nothing: icons are `aria-hidden` or labelled (✕ "Remove …", ✎ "Rename …"), charts have a label and a table (#60). A document shown in a frame is named by its title now. An ECG recording's trace (#182) has no table of its 15,360 samples, which would say nothing a table can show: it takes focus with a summary of what it is (length, sample rate, lead, the device's classification), ← and → step through it saying the voltage, a table beside it sums it up, and Download as CSV gives every sample. |
| 1.3.1 Info and relationships | axe; read the code | Claims, paid amounts, prescriptions, a medication's listings and fills, and a result's components each open from a button in the row (below). A Costs column's sort is said with `aria-sort`. A field marked wrong when typing lab results in points to the message saying why (`aria-describedby`). |
| 1.3.2 Meaningful sequence, 1.3.3 Sensory characteristics | read the pages | Nothing found. |
| 1.3.4 Orientation | 375 and 320 px, a phone on its side | Nothing found: both orientations work. |
| 1.3.5 Identify input purpose | read the forms | Nothing found: sign-in, setup and the account page's fields have `autocomplete` (username, current-password, new-password, one-time-code). |
| 1.4.1 Use of colour | #60, #193; looked again; #250 | **Fixed** (#250): out of range is ▲/▼ as well as red, and a lab day on the Timeline with a result out of range has a red ▲ over it (it was a 2 px red dot). On the Labs charts, procedures and imaging were ▼ like a result below range, told apart only by colour: a procedure is a ● now and imaging a ■, in the legend too. On/off chips differ by a tick and tint. |
| 1.4.3 Contrast (minimum) | axe on every state, both themes | **Fixed.** The muted grey was 3.4:1 on the light theme (now `#6a6964`, 5.2:1); filled blue buttons with white text were 4.4:1 in light and 3.6:1 in dark (a `--accent-fill` token: 4.9:1 and 5.2:1); blue text on dark cards and red text in dark were under 4.5:1 (lighter there). Tokens in `www/css/app.css`, `login.html` and `setup.html`; the look is the same family of colours. The Costs running totals' end labels were in their lines' orange and green (about 3.1:1 and 2.7:1 in light); they're in ink now, each after a short line in its colour (#250). |
| 1.4.4 Resize text | 200% zoom (700 px wide) | Nothing found beyond reflow, below. |
| 1.4.5 Images of text | read the pages | None used. |
| 1.4.10 Reflow | 320 px, every page and group | **Fixed.** The Overview's columns and the Costs charts were 9–23 px too wide at 320 px. The top bar stops sticking when the window is under 500 px tall (zoomed a long way in, or a phone on its side), so it doesn't take half the screen. |
| 1.4.11 Non-text contrast | measured the tokens | **Fixed.** A text field's edge was about 1.3:1 against the page; it's 3:1 now (`--field-line`), keeping red for a field marked wrong. Focus rings are the accent, over 3:1 in both themes. |
| 1.4.12 Text spacing | the WCAG spacing at 375 px, every page | Nothing found. |
| 1.4.13 Content on hover or focus | read the code | **Fixed.** A chart's hover readout goes with Escape too, the pointer left where it is. It never covers what it's about, and moving onto it keeps it (it lets the pointer through). |
| 2.1.1 Keyboard | Tab through every page; read every click handler | **Fixed.** The Overview's out-of-range and recent rows, the Costs bar lists, the Timeline's events, claims, paid amounts, prescriptions, a medication's listings and fills, a result's components and the Costs column headers opened or sorted by mouse only, or took focus without saying what they were. Each is a button now (`pressable()` in `util.js` for a row, `.cell-btn` in a table's cell). |
| 2.1.2 No keyboard trap | Tab and Shift+Tab walks; the drawer, dialog, menus and rename box | Nothing found: Escape closes each, and focus goes back. |
| 2.1.4 Character key shortcuts | read the code | None: every shortcut is on a focused control (arrows, Home, End, Escape). |
| 2.2.1 Timing adjustable | read the code | Nothing timed while someone works; a sign-in lasts 12 hours, or 30 days with a second factor. |
| 2.2.2 Pause, stop, hide; 2.3.1 Three flashes | read the code | Nothing moves by itself; less motion when asked (#209). |
| 2.4.1 Bypass blocks, 2.4.2 Page titled | #209 | Nothing new. |
| 2.4.3 Focus order | Tab walks | Nothing found. |
| 2.4.4 Link purpose, 2.4.6 Headings and labels | read the pages | The two search boxes that had only a placeholder are named ("Search events", "Search claims"); the Costs type-of-care list is named. |
| 2.4.5 Multiple ways | — | The tabs, the top bar's menus, the sidebars and each view's own address. |
| 2.4.7 Focus visible | Tab walks, every stop compared focused and not | **Fixed.** A menu's item with focus was only tinted 4%; it's ringed. A date field reached by Shift+Tab (its calendar button has focus) showed nothing; it's ringed too. |
| 2.4.11 Focus not obscured (minimum) | Tab and Shift+Tab walks at 375 px | **Fixed.** What gets focus is scrolled clear of the sticky top bar (its height kept as `--bar-h` by `nav.js`), a scrolling table's header row, a Settings group's sticky Save bar, and the lab list's own sticky header. |
| 2.5.1 Pointer gestures, 2.5.7 Dragging movements | read the code | Nothing to fix: a chart's drag and pinch have the period buttons and dates, ← → keys, and a table; the key-lab tiles and tabs reorder with ↑ ↓ buttons and a ⋯ menu as well as by drag. |
| 2.5.2 Pointer cancellation, 2.5.4 Motion actuation | read the code | Nothing found. |
| 2.5.3 Label in name | read the labels | Nothing found: labels start with the visible words. |
| 2.5.8 Target size (minimum) | axe's target-size rule | **Fixed.** The ✎ (rename) is a 24 px target, and a button in a table's cell is 24 px tall, without changing the rows' height. |
| 3.1.1 Language of page | axe | Nothing found. |
| 3.2.1 On focus, 3.2.2 On input | read the code | Nothing new: the profile picker switches whose records are shown, which is what it's for and what its label says. |
| 3.2.3, 3.2.4 Consistent navigation and identification | looked at every page | The same top bar on every page. |
| 3.2.6 Consistent help | looked at every page | Getting started is in the same menu on every page; a card's help is under "More about this" in the same place (#176). |
| 3.3.1 Error identification, 3.3.3 Error suggestion | read the forms | Errors are said in words beside the form and through the shared alert line (#209). Typed-in lab rows link the message to the field (above). |
| 3.3.2 Labels or instructions | axe | See 2.4.6. |
| 3.3.4 Error prevention | read the code | Removing, revoking and undoing ask first (#210); imports are previewed and can be undone. |
| 3.3.7 Redundant entry | read the forms | Nothing asked twice, except a new password, typed again to confirm it (allowed for security). |
| 3.3.8 Accessible authentication (minimum) | automated (above) | Nothing to fix: password managers can fill the sign-in, paste works in every field, and passkeys need no typing at all. The setup code can be pasted. |
| 4.1.2 Name, role, value | axe; read the code | **Fixed.** Rows that opened something on click are buttons (2.1.1). A paid amount's row said `aria-expanded` on a table row, which a row can't have; its date is the button that says it. The heatmap's row names were focusable inside a picture, so screen readers couldn't reach them; they're clicked there, and renamed by keyboard from the ✎ in the heatmap's table (or a chart). A chart's table that scrolls takes focus, named by its columns, so it can be scrolled by keyboard. |
| 4.1.3 Status messages | #209 | Nothing new. |

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
