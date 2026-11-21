/* Sanguity · the groups of the pages with a sidebar (#266), in one list (#273): each page's sidebar (sidebar.js) is
   drawn from its own (Sources, Sharing, Settings, Account, Admin), and the command palette (palette.js) searches all of
   them from every page. Loaded on every page with the top bar, before nav.js (#399) and the page's own script.

   SG_PAGES[name] is {page: its file, title, icon, profile: true for a page about the profile chosen (its addresses
   carry ?profile=), admin: true for admins only, words (as a group's, below), groups}. Each group is what sideNav() takes, {id, title, icon, part,
   has: [the ids of its parts], …}, with what the page's own script reads (needs: the role on the profile shown it's
   for, on Sources and Sharing, and Sharing's and Settings' why; Account's hint),
   and for the palette:
   - words: what else someone might type for it ("password" for Sign-in and security), in lower case;
   - finds: its parts that are worth going to by name, [title as the part's heading says it, its id, words, and the
     role it needs, if its group doesn't: Tabs, #423]. The palette goes to page#id, which opens the group and scrolls
     to the part.
   A group with rest: true (Settings' More: the sections no other group names) isn't searched.

   The icons (#399): one per place, the same wherever it's shown (the top bar and its menus, a phone's tab bar, the
   sidebars, the palette), and none for two places. A page's and a group's are theirs below; the records tabs' and the
   top bar's own are SG_ICON's, with a part's that's worth an icon of its own. sgIcon(key) gives any of them: "labs" (a
   tab), "add" (the bar's), "sources" (a page), "sources/upload" (a group, or a part: its own, else its group's).
   Loaded before nav.js, which draws the bar with them. Plain script; all files share one scope. */
"use strict";
const SG_ICON = {
  overview: "layout-grid", health: "heart-pulse", medications: "pill-bottle", timeline: "chart-gantt", labs: "test-tube-diagonal", costs: "banknote",
  more: "ellipsis",
  search: "search", add: "plus", you: "circle-user", guide: "compass", theme: "sun-moon", logout: "log-out",
  dose: "pill", journal: "notebook", reading: "gauge",   // Add's Today
  "settings/sec-tabs": "pencil",   // Edit tabs… (the profile menu, More) and the palette's Tabs
  "sharing/newProfile": "user-plus",   // New profile
};
function sgIcon(key) {
  if (SG_ICON[key]) return SG_ICON[key];
  const [name, id] = key.split("/"), P = SG_PAGES[name];
  if (!P) return "file-text";
  const g = id && (P.groups.find(x => x.id === id) || P.groups.find(x => x.has.includes(id) || (x.finds || []).some(f => f[1] === id)));
  return (g || P).icon;
}
const SG_PAGES = {
  sources: {page: "sources.html", title: "Sources", icon: "database", profile: true, words: "import add records bring in", groups: [
    {id: "overview", title: "Overview", icon: "layout-list", has: ["waiting", "srcOverview"],   // #283
     words: "sources connections where records come from waiting for you review"},
    {id: "portals", title: "Portals and health plans", icon: "plug", part: "Connected", has: ["mine"], needs: "editor",   // the ways in: owners' and editors' (#401)
     words: "connect portal patient health plan insurance fhir sync epic mychart oracle cerner"},   // (#515: the vendors' and portals' names)
    {id: "phone", title: "Phone and watch", icon: "watch", part: "Connected", has: ["watch", "cycle"], needs: "editor",   // cycle tracking (#183)
     words: "health connect android watch phone steps sleep iphone apple health auto export", finds: [["Cycle tracking", "cycle", "period menstrual turn off stop delete data"]]},   // (#495: an iPhone's words; #499: its danger zone's)
    {id: "upload", title: "Upload files", icon: "upload", part: "One-off", has: ["importer"], needs: "editor", words: "import zip folder files export takeout"},
    {id: "manual", title: "Type records in", icon: "keyboard", part: "One-off", has: ["typeIn"], needs: "editor",
     words: "manual enter lab result home reading blood pressure weight payment cost"},
    {id: "link", title: "Paste a Health Link", icon: "link", part: "One-off", has: ["healthLink"], needs: "editor", words: "smart health link shl qr code"},
    {id: "history", title: "Import history", icon: "history", part: "History", has: ["history"], words: "imports undo remove"},
  ]},
  sharing: {page: "sharing.html", title: "Sharing", icon: "share-2", profile: true, words: "share who sees", groups: [
    // no part labels: "Sharing" over all of them only said the page's title again (#402)
    {id: "people", title: "People", icon: "users", has: ["people"],
     words: "share who has access invite account owner editor viewer role leave",
     finds: [["Who has access", "people", "members remove person make change role editor viewer"]]},   // (#510: what's done to one, on its own: in the group's, "remove account" and "make owner" found it before Admin's Accounts)
    {id: "links", title: "Share links", icon: "send", has: ["links"], needs: "owner", why: "Share links are made by a profile's owners.",
     words: "share link doctor clinician provider send revoke expire remove delete"},   // (#510: revoking one)
    {id: "calendar", title: "Calendar feed", icon: "calendar", has: ["calendar"], words: "ics subscribe appointments calendar"},
    {id: "summary", title: "Visit summary", icon: "file-text", has: ["summaryIntro", "build", "preview", "sumTicked"],   // sumTicked: from Settings → Dashboard (#402)
     words: "doctor appointment print pdf questions", finds: [["Measures ticked at first", "sumTicked", "home readings watch"]]},
    {id: "export", title: "Export everything", icon: "download", has: ["export"], needs: "editor", why: "A profile's owners and editors can export it.",
     words: "download backup zip copy"},
    {id: "activity", title: "Activity", icon: "scroll-text", has: ["activity"], needs: "owner", why: "A profile's activity is for its owners.",
     words: "log who opened viewed changes"},
  ]},
  settings: {page: "settings.html", title: "Settings", icon: "settings", profile: true, words: "preferences options", groups: [
    // sec-summary (#186) is set on Sharing → Visit summary (#402): kept here out of sight, for Start from a condition
    {id: "dashboard", title: "Dashboard", icon: "layout-dashboard", part: "This profile", has: ["sec-overview", "sec-tabs", "sec-trends", "sec-summary"],
     finds: [["Overview tiles", "sec-overview", "key labs home readings"], ["Tabs", "sec-tabs", "edit order hide show", "editor"],
       ["Lab charts", "sec-trends", "labs selected at first saved views"]]},
    {id: "about", title: "About this person", icon: "user", part: "This profile", has: ["about", "targets"],   // #194, #234; on Sharing before #267
     finds: [["Birth date, sex and height", "about", "age birthday date of birth dob born child baby due date growth tall cm inches maximum heart rate"], ["Targets of your own", "targets", "goals"]]},
    {id: "labs", title: "Labs and ranges", icon: "ruler", part: "This profile", has: ["sec-names", "sec-ranges"], words: "reference ranges normal high low",
     finds: [["Names", "sec-names", "nicknames rename lab names result sources"]]},   // Names on Dashboard before #402
    {id: "milestones", title: "Milestones", icon: "flag", part: "This profile", has: ["sec-milestones"], words: "diagnosis surgery treatment named dates"},
    {id: "activity", title: "Workouts", icon: "dumbbell", part: "This profile", has: ["sec-health"], words: "visits exercise body and activity"},   // visits counted as workouts (#43)
    {id: "profile", title: "Rename or delete", icon: "user-pen", part: "This profile", has: ["profileCard"], needs: "owner",   // on Sharing before #402
     why: "Only a profile's owners can rename or delete it.", words: "profile name rename delete remove", finds: [["Delete this profile", "profileCard", "remove erase"]]},
    {id: "display", title: "Display", icon: "monitor", part: "You", has: ["sec-appearance", "sec-dates", "sec-units"],
      finds: [["Appearance", "sec-appearance", ""],   // (the theme: your menu's Dark or Light mode, #410)
        ["Dates and times", "sec-dates", "time zone date format"],
        ["Units", "sec-units", "metric imperial mmol mg kg lb pounds kilograms weight temperature celsius fahrenheit height cm inches"]]},
    {id: "more", title: "More", icon: "folder", has: [], rest: true},   // a section no group names yet
  ]},
  account: {page: "account.html", title: "Account", icon: "key-round", words: "sign-in security", groups: [
    {id: "security", title: "Sign-in and security", icon: "lock", hint: "How you sign in, and where you're signed in now.",
     has: ["password", "twofa", "passkeys", "sessions"],
     finds: [["Password", "password", "change passphrase"], ["Two-factor sign-in", "twofa", "2fa authenticator code totp turn on off recovery make new"],
       ["Passkeys", "passkeys", "security key fingerprint face add remove"], ["Where you're signed in", "sessions", "sessions devices sign out log out everywhere other"]]},   // (#502: what's done there)
    {id: "device", title: "This device", icon: "smartphone", hint: "Sanguity on the device you're using now: as an app, and offline.",
     has: ["installApp", "offlineRecords"],
     finds: [["Install on this device", "installApp", "app home screen"], ["Offline records on this device", "offlineRecords", "offline without a connection"]]},
  ]},
  admin: {page: "admin.html", title: "Admin", icon: "wrench", admin: true, words: "server install", groups: [
    {id: "accounts", title: "Accounts", icon: "user-cog", part: "People", has: ["users"],
     words: "users new create add account reset password one-time temporary remove delete disable enable make admin require two-factor " +
       "owner ownerless profile without an"},   // (#495, #499, #502: what's done to an account; #506: Make owner, under the list; #515: enable)
    {id: "activity", title: "Install activity", icon: "logs", part: "People", has: ["installLog"], words: "log sign-ins"},
    {id: "backups", title: "Backups", icon: "archive", part: "Data safety", has: ["backups"], words: "backup restore"},   // #200
    {id: "apps", title: "Connected-source apps", icon: "app-window", part: "Connections", has: ["admin", "sec-sync"],
     words: "set up a source client portal app test connection change remove delete waiting for review done id secret issuer vendor epic mychart oracle cerner", finds: [["Sync defaults", "sec-sync", "attachments"]]},   // (#506: a source's buttons; #510: its form's; #515: delete)
    {id: "directory", title: "Provider directory", icon: "book-user", part: "Connections", has: ["directory"], words: "providers"},
    {id: "outside", title: "Outside connections", icon: "globe", part: "Connections", has: ["outside"], words: "network internet"},
    {id: "labs", title: "Lab name matches", icon: "arrow-right-left", part: "Lab matching", has: ["labmaps"], words: "aliases mapping"},
  ]},
};
