/* Sanguity · the groups of the pages with a sidebar (#266), in one list (#273): each page's sidebar (sidebar.js) is
   drawn from its own (Sources, Sharing, Settings), and the command palette (palette.js) searches all of them from every
   page. Loaded on every page with the top bar, before nav.js (#399) and the page's own script.

   SG_PAGES[name] is {page: its file, title, icon, profile: true for a page about the profile chosen (its addresses
   carry ?profile=), words (as a group's, below), groups}. Each group is what sideNav() takes, {id, title, icon, part,
   has: [the ids of its parts], …}, with what the page's own script reads (needs: the role on the profile shown it's
   for, on Sources and Sharing, and Sharing's and Settings' why; a hint, what the group is, said under the title after
   whose settings these are, "Just for you: …"; admin: true for admins only, Settings' Install, #991),
   and for the palette:
   - words: what else someone might type for it ("password" for Sign-in and security), in lower case;
   - place: words for where it is, shown nowhere, that a search takes as its place's name (#991: "account" for You's
     Sign-in and security, the Account page's before, so "account password" still finds Password first);
   - finds: its parts that are worth going to by name, [title as the part's heading says it, its id, words, and the
     role it needs, if its group doesn't: Tabs, #423]. The palette goes to page#id, which opens the group and scrolls
     to the part.
   A group with rest: true (Settings' More: the sections no other group names) isn't searched; one with cs: true is
   connected sources' (#1096), listed and searched only while they're on (sgConnected), as are the parts in a group's
   csHas (#1153: Install's Connected sources, whose switch is there either way).

   The icons (#399): one per place, the same wherever it's shown (the top bar and its menus, a phone's tab bar, the
   sidebars, the palette), and none for two places. A page's and a group's are theirs below; the records tabs' and the
   top bar's own are SG_ICON's, with a part's that's worth an icon of its own. sgIcon(key) gives any of them: "labs" (a
   tab), "add" (the bar's), "sources" (a page), "sources/upload" (a group, or a part: its own, else its group's).
   Loaded before nav.js, which draws the bar with them. Plain script; all files share one scope. */
"use strict";
/* connected sources (#1096): experimental, off unless an admin turns them on (Settings → Install → Connected sources,
   #1153). The one rule every page asks, from /api/me (its connected_sources, a plain true or false): off,
   nothing of theirs is offered (the cs groups, Add's Connect, the guide's way, the Overview's and Sources' notes) */
const sgConnected = me => !!(me && me.connected_sources);
const SG_ICON = {
  overview: "layout-grid", health: "heart-pulse", medications: "pill-bottle", timeline: "chart-gantt", labs: "test-tube-diagonal", costs: "banknote",
  more: "ellipsis",
  search: "search", add: "plus", you: "circle-user", guide: "compass", theme: "sun-moon", logout: "log-out",
  dose: "pill", medadd: "clipboard", journal: "notebook", reading: "gauge", fuadd: "list-todo", rqadd: "mail",   // Add's Today (#1092: Add a medication its own; #1138: a follow-up its own, check is a menu's tick)
  family: "users",   // the palette's Family history and Add a relative (#1134)
  "settings/directory": "book-user",   // Install's Provider directory, in Connected sources since #1153
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
     words: "sources connections where records come from waiting for you review fix sign in again"},   // (#528: a connection's)
    {id: "portals", title: "Portals and health plans", icon: "plug", part: "Connected", has: ["mine"], needs: "editor", cs: true,   // the ways in: owners' and editors' (#401)
     words: "connect portal patient health plan insurance fhir sync epic mychart oracle cerner find hospital clinic " +
       "sync now pull again sign in again reconnect disconnect what it pulls use server defaults fetch anyway name city state"},   // (#515: the vendors' and portals' names; #528: a connection's)
    {id: "phone", title: "Phone and watch", icon: "watch", part: "Connected", has: ["watch", "cycle"], needs: "editor",   // cycle tracking (#183)
     words: "health connect android watch phone steps sleep iphone apple health auto export upload address header key make new secret qr code",
     finds: [["Cycle tracking", "cycle", "period menstrual turn off stop delete data"]]},   // (#495: an iPhone's words; #499: its danger zone's)
    {id: "upload", title: "Upload files", icon: "upload", part: "One-off", has: ["importer"], needs: "editor", words: "import zip folder files export takeout pdf lab report quest labcorp"},
    {id: "manual", title: "Type records in", icon: "keyboard", part: "One-off", has: ["typeIn"], needs: "editor",
     words: "manual enter lab result home reading blood pressure weight payment cost"},
    {id: "link", title: "Paste a Health Link", icon: "link", part: "One-off", has: ["healthLink"], needs: "editor", words: "smart health link shl qr code experimental"},
    {id: "history", title: "Import history", icon: "history", part: "History", has: ["history"], words: "imports undo remove"},
  ]},
  sharing: {page: "sharing.html", title: "Sharing", icon: "share-2", profile: true, words: "share who sees", groups: [
    // no part labels: "Sharing" over all of them only said the page's title again (#402)
    {id: "people", title: "People", icon: "users", has: ["people"],
     words: "share who has access invite account owner editor viewer role leave profile",
     finds: [["Who has access", "people", "members remove person someone make change role editor viewer owner ownership transfer add give share with stop sharing"]]},   // (#510: what's done to one, on its own: in the group's, "remove account" and "make owner" found it before Admin's Accounts)
    {id: "links", title: "Share links", icon: "send", has: ["links"], needs: "owner", why: "Share links are made by a profile's owners.",
     words: "share link doctor clinician provider send revoke expire remove delete new create make qr code"},   // (#510: revoking one)
    {id: "calendar", title: "Calendar feed", icon: "calendar", has: ["calendar"], words: "ics subscribe appointments calendar create make turn off stop get new address what each event says minimal full"},
    {id: "summary", title: "Visit summary", icon: "file-text", has: ["summaryIntro", "build", "preview", "sumTicked"],   // sumTicked: from Settings → Dashboard (#402)
     words: "doctor appointment print pdf download questions changes since another lab trend", finds: [["Measures ticked at first", "sumTicked", "home readings watch"]]},
    {id: "emergency", title: "Emergency card", icon: "id-card", has: ["emergency"],   // #1126 (emergency.js)
     words: "emergency ice in case of emergency wallet card lock screen image phone allergies blood type contacts medications print printable pdf png download ambulance paramedic"},
    {id: "export", title: "Export everything", icon: "download", has: ["export"], needs: "editor", why: "A profile's owners and editors can export it.",
     words: "download backup zip copy"},
    {id: "activity", title: "Activity", icon: "scroll-text", has: ["activity"], needs: "owner", why: "A profile's activity is for its owners.",
     words: "log who opened viewed changes audit"},
  ]},
  /* Settings (#991): one page in three parts, This person (the profile shown), You (the Account page's two groups,
     and Display) and Install (the Admin page's groups, for admins only), General first (#998) */
  settings: {page: "settings.html", title: "Settings", icon: "settings", profile: true, words: "preferences options", groups: [
    // sec-summary (#186) is set on Sharing → Visit summary (#402): kept here out of sight, for Start from a condition.
    // (#1165) a section per tab it changes, named by it: Workouts (#43, its own group from #409) is Body and activity's
    {id: "dashboard", title: "Dashboard", icon: "layout-dashboard", part: "This person", has: ["sec-overview", "sec-trends", "sec-health", "sec-costs", "sec-tabs", "sec-summary"],
     finds: [["Overview tiles", "sec-overview", "key labs home readings start from a condition choose conditions preset use most measured add"], ["Tabs", "sec-tabs", "edit order hide show", "editor"],
       ["Labs", "sec-trends", "lab charts labs selected at first saved views add view no default"],
       ["Body and activity", "sec-health", "workouts visits exercise title contains count"], ["Costs", "sec-costs", "tax year month report"]]},
    {id: "about", title: "About this person", icon: "user", part: "This person", has: ["about", "targets"],   // #194, #234; on Sharing before #267
     finds: [["Birth date, sex and height", "about", "age birthday date of birth dob born child baby due date growth tall cm inches maximum heart rate measured bpm early add blood type group abo rh emergency contacts ice phone number relationship optional"], ["Targets of your own", "targets", "goals"]]},   // (an emergency contact's fields, #524)
    {id: "insurance", title: "Insurance cards", icon: "wallet-cards", part: "This person", has: ["insurance"],   // #1137
     words: "health plan insurer member id group number card photo front back phone add change take off",
     finds: [["Remove a plan", "insurance", "delete"]]},   // (its own: in the group's words, "remove member" found it before Who has access, #510)
    {id: "labs", title: "Labs and ranges", icon: "ruler", part: "This person", has: ["sec-names", "labmatches", "sec-ranges"], words: "reference ranges normal high low add",
     finds: [["Names", "sec-names", "nicknames rename lab names result sources records shown"],
       ["Lab name matches", "labmatches", "same test match matched charted with keep separate own test move results add change remove forget aliases import results " +
        "mapping server install admin"]]},   // Names on Dashboard before #402; matches #1097, with Install's for every profile (#1114)
    {id: "milestones", title: "Milestones", icon: "flag", part: "This person", has: ["sec-milestones"], words: "diagnosis surgery treatment named dates"},
    {id: "profile", title: "Rename or delete", icon: "user-pen", part: "This person", has: ["profileCard"], needs: "owner",   // on Sharing before #402
     why: "Only a profile's owners can rename or delete it.", words: "profile name rename change delete remove", finds: [["Delete this profile", "profileCard", "remove erase"]]},
    // You: the Account page's groups (#169: account.js, and pwa.js's two cards), then Display
    {id: "security", title: "Sign-in and security", icon: "lock", part: "You", hint: "how you sign in, and where you're signed in now.", place: "account",
     has: ["password", "twofa", "passkeys", "oidc", "sessions"],
     finds: [["Password", "password", "change new reset forgot passphrase current repeat"], ["Two-factor sign-in", "twofa", "2fa authenticator app code totp turn on off recovery make new disable enable reset lost phone qr scan"],
       ["Passkeys", "passkeys", "security key fingerprint face add rename remove delete"],
       ["Sign-in provider", "oidc", "oidc single sign-on sso link unlink identity authentik keycloak authelia google"], ["Where you're signed in", "sessions", "sessions devices sign out log out logout everywhere other"]]},   // (#502: what's done there; #519: logout)
    {id: "device", title: "This device", icon: "smartphone", part: "You", hint: "Sanguity on the device you're using now, as an app and offline.", place: "account",
     has: ["installApp", "offlineRecords"],
     finds: [["Install on this device", "installApp", "app home screen"], ["Offline records on this device", "offlineRecords", "offline without a connection"]]},
    // (#1130) shown once the server has a notification server: this person's, per profile they can see
    {id: "notifications", title: "Notifications", icon: "bell", part: "You", hint: "new results and dose reminders, sent to this server's own notification server.",
     has: ["notify"], words: "notify push ntfy alert new results dose reminders medication turn on off topic subscribe make a new test"},
    {id: "display", title: "Display", icon: "monitor", part: "You", has: ["sec-appearance", "sec-dates", "sec-units"],
      finds: [["Appearance", "sec-appearance", "default use browser in range results grey gray green"],   // (not "theme": the theme is your menu's Dark or Light mode, #410; In-range results, #1108)
        ["Dates and times", "sec-dates", "time zone date format"],
        ["Units", "sec-units", "metric imperial mmol mg kg lb pounds kilograms weight temperature celsius fahrenheit height cm inches lab tests"]]},
    // Install: the Admin page's groups (#167: admin.js, admin-sources.js, admin-backups.js, outside.js, activity.js),
    // admins only. General (#998) first: the server's own settings, each of its sections (#1000-#1005) a part in its has
    {id: "general", title: "General", icon: "wrench", part: "Install", admin: true, has: ["gen-host", "gen-phone", "gen-notify", "gen-restart", "gen-ocr", "gen-demo"], place: "admin", words: "server install configuration",
     finds: [["Host", "gen-host", "public addresses origins reverse proxy domain url passkey names published web port forwarded request"],
       ["Phone receiver", "gen-phone", "upload address watch scale health connect receiver port time zone unclaimed restore replaced"],
       ["Notifications", "gen-notify", "notification server ntfy topic push access token test send"],   // (#1130)
       ["Needs a restart", "gen-restart", "restart now upload limits file size files journal photo sync attachment pulls timeout health links password checks at once hash parallel trusted proxies forwarded time zone unclaimed restore replaced kept server-settings " +
        "ones most unpacked records fetches data wait vendor answer each request what brings between connection"],   // (#1121; its rows' labels, #524: "ones", so "one" is found but "one-time password" stays Accounts', #502)
       ["Text recognition", "gen-ocr", "ocr tesseract scanned documents scans searchable search text languages language file traineddata import download tessdata"],   // (#1129)
       ["Demo profile", "gen-demo", "demo sample example fictional made-up look around try add remove demo"]]},   // (#1101)
    // (#1119, the owner's decision) Accounts and sign-in: People, then Sign-in rules and Sign-in provider (General's
    // before), found by their words as there
    {id: "accounts", title: "Accounts and sign-in", icon: "user-cog", part: "Install", admin: true, has: ["users", "signin-rules", "signin-provider"], place: "admin",
     words: "users new create add account reset password one-time temporary remove delete disable enable make admin " +
       "owner ownerless profile without an turn off sign-in admin someone people",   // (#495, #499, #502: what's done to an account; #506: Make owner, under the list; #515: enable)
     finds: [   // (each of its rows' labels finds it, #524; "onetime", not "one-time": a word typed is found at a word's start, so it's
       // still found by "one-time", but not whole, so "one-time password" finds the group first, where one is made, #502)
       ["Sign-in rules", "signin-rules", "security sign-in lasts session length days hours with second factor without idle when unused sign out " +
        "who may create profiles admins only shortest password length characters two factor onetime temporary time works fresh " +
        "check minutes"],
       ["Wrong tries before a wait", "signin-rules", "password throttle attempts one account from place address any anywhere"],   // (an entry of its own: "create account" is People's)
       ["Require two-factor sign-in", "signin-rules", "2fa everyone"],   // (#1001; #528: "require 2fa")
       ["Sign-in provider", "signin-provider", "oidc openid connect single sign-on sso issuer client id secret redirect uri scopes acr " +
        "that counts as two factors button's name authentik keycloak authelia google test connection sign in with oidc only passwords off"]]},   // (#1109)
    // (#1153, the owner's decision) Backups: the backups themselves, then where they're kept and the key (General →
    // Backups and logging's before); Logs and activity: how long activity is kept and where the logs are read (the
    // same's), then Install activity; one Connected sources group, its switch first (General → Experimental's), the
    // rest of it (csHas) only while they're on; About last. Each found by its words as before
    {id: "backups", title: "Backups", icon: "archive", part: "Install", admin: true, has: ["backups", "bk-files"], place: "admin", words: "backup restore back up now make check show all fewer " +
      "schedule how often daily weekly off keep newest many oldest replaced delete now before-restore",   // #200 (#595: Restore's; #532: its schedule's; and each field's and segmented control's label finds its place)
     finds: [["Folder and key", "bk-files", "backups and logging folder encryption key file keys where kept mounted"]]},
    {id: "logs", title: "Logs and activity", icon: "logs", part: "Install", admin: true, has: ["logging", "installLog"], place: "admin",
     words: "install activity log sign-ins failed audit",   // (Install activity's, the group's own title before; its card's now)
     finds: [["Logging", "logging", "backups and logs log output docker security activity kept days how long delete older"]]},
    {id: "connected", title: "Connected sources (experimental)", icon: "app-window", part: "Install", admin: true, has: ["cs-switch", "admin", "sec-sync", "directory"],
     csHas: ["admin", "sec-sync", "directory"], place: "admin",
     finds: [["On or off", "cs-switch", "experimental connected sources portals health plans fhir epic mychart sync find your provider turn on off"],   // (#1096)
       ["Connected-source apps", "admin", "set up a source client portal app test connection change remove delete waiting for review done id secret issuer vendor epic mychart oracle cerner " +
        "new return address redirect uri sandbox use add another notes name people see fhir base how signs scopes ask"],   // (#506: a source's buttons; #510: its form's; #515: delete; #519: its return address)
       ["Sync defaults", "sec-sync", "attachments"],
       ["Provider directory", "directory", "providers epic oracle cerner hospitals lists add the update now download keep them up to date weekly"]]},
    {id: "outside", title: "Outside connections", icon: "globe", part: "Install", admin: true, has: ["outside"], place: "admin", words: "network internet"},
    {id: "install-about", title: "About", icon: "info", part: "Install", admin: true, has: ["install-about"], place: "admin",
     words: "version release releases what's new schema database deployed deployment truenas unraid casaos docker folders runs as user uid gid configuration limits upload size zip journal photo sync attachment timeout health links " +
        "server internal port bind secure session cookie"},   // (#1004; Host's and Security's before, #1119)
    {id: "more", title: "More", icon: "folder", has: [], rest: true},   // a section no group names yet
  ]},
};
