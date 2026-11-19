/* Sanguity · the groups of the pages with a sidebar (#266), in one list (#273): each page's sidebar (sidebar.js) is
   drawn from its own (Sources, Sharing, Settings, Account, Admin), and the command palette (palette.js) searches all of
   them from every page. Loaded on every page with the top bar, after nav.js and before the page's own script.

   SG_PAGES[name] is {page: its file, title, icon, profile: true for a page about the profile chosen (its addresses
   carry ?profile=), admin: true for admins only, words (as a group's, below), groups}. Each group is what sideNav() takes, {id, title, icon, part,
   has: [the ids of its parts], …}, with what the page's own script reads (Sharing's needs and why, Account's hint),
   and for the palette:
   - words: what else someone might type for it ("password" for Sign-in and security), in lower case;
   - finds: its parts that are worth going to by name, [title as the part's heading says it, its id, words]. The
     palette goes to page#id, which opens the group and scrolls to the part.
   A group with rest: true (Settings' More: the sections no other group names) isn't searched.
   Plain script; all files share one scope. */
"use strict";
const SG_PAGES = {
  sources: {page: "sources.html", title: "Sources", icon: "database", profile: true, words: "import add records bring in", groups: [
    {id: "overview", title: "Overview", icon: "layout-grid", has: ["srcOverview"], words: "sources connections where records come from"},
    {id: "portals", title: "Portals and health plans", icon: "plug", part: "Connected", has: ["mine"],
     words: "connect portal patient health plan insurance fhir sync"},
    {id: "phone", title: "Phone and watch", icon: "watch", part: "Connected", has: ["watch", "cycle", "viewOnly"],   // cycle tracking (#183)
     words: "health connect android watch phone steps sleep", finds: [["Cycle tracking", "cycle", "period menstrual"]]},
    {id: "upload", title: "Upload files", icon: "upload", part: "One-off", has: ["importer", "viewOnly"], words: "import zip folder files export takeout"},
    {id: "manual", title: "Type records in", icon: "keyboard", part: "One-off", has: ["typeIn", "viewOnly"],
     words: "manual enter lab result home reading blood pressure weight payment cost"},
    {id: "link", title: "Paste a Health Link", icon: "link", part: "One-off", has: ["healthLink", "viewOnly"], words: "smart health link shl qr code"},
    {id: "history", title: "Import history", icon: "history", part: "History", has: ["history"], words: "imports undo remove"},
  ]},
  sharing: {page: "sharing.html", title: "Sharing", icon: "share-2", profile: true, words: "share who sees", groups: [
    {id: "people", title: "People", icon: "users", part: "Sharing", has: ["people"],
     words: "share who has access invite account owner editor viewer role leave", finds: [["Who has access", "people", "members"]]},
    {id: "links", title: "Share links", icon: "share-2", part: "Sharing", has: ["links"], needs: "owner", why: "Share links are made by a profile's owners.",
     words: "share link doctor clinician provider send revoke expire"},
    {id: "calendar", title: "Calendar feed", icon: "calendar", part: "Sharing", has: ["calendar"], words: "ics subscribe appointments calendar"},
    {id: "summary", title: "Visit summary", icon: "file-text", part: "Sharing", has: ["summaryIntro", "build", "preview"],
     words: "doctor appointment print pdf questions"},
    {id: "export", title: "Export everything", icon: "download", part: "Sharing", has: ["export"], needs: "editor", why: "A profile's owners and editors can export it.",
     words: "download backup zip copy"},
    {id: "activity", title: "Activity", icon: "history", part: "Sharing", has: ["activity"], needs: "owner", why: "A profile's activity is for its owners.",
     words: "log who opened viewed changes"},
    {id: "profile", title: "Profile", icon: "circle-user", part: "Profiles", has: ["profileCard", "newProfile"],
     words: "rename delete profile", finds: [["New profile", "newProfile", "create add child family someone else"]]},
  ]},
  settings: {page: "settings.html", title: "Settings", icon: "settings", profile: true, words: "preferences options", groups: [
    {id: "dashboard", title: "Dashboard", icon: "layout-grid", part: "This profile", has: ["sec-overview", "sec-tabs", "sec-trends", "sec-names", "sec-summary"],   // sec-summary: #186
     finds: [["Overview tiles", "sec-overview", "key labs home readings"], ["Tabs", "sec-tabs", "edit order hide show"],
       ["Lab charts", "sec-trends", "labs selected at first saved views"], ["Names", "sec-names", "nicknames rename lab names result sources"],
       ["Visit summary measures", "sec-summary", "ticked"]]},
    {id: "about", title: "About this person", icon: "user", part: "This profile", has: ["about", "targets"],   // #194, #234; on Sharing before #267
     finds: [["Birth date, sex and height", "about", "age birthday date of birth dob born child baby due date growth tall cm inches maximum heart rate"], ["Targets of your own", "targets", "goals"]]},
    {id: "labs", title: "Labs and ranges", icon: "test-tube-diagonal", part: "This profile", has: ["sec-ranges"], words: "reference ranges normal high low"},
    {id: "milestones", title: "Milestones", icon: "flag", part: "This profile", has: ["sec-milestones"], words: "diagnosis surgery treatment named dates"},
    {id: "activity", title: "Workouts", icon: "activity", part: "This profile", has: ["sec-health"], words: "visits exercise body and activity"},   // visits counted as workouts (#43)
    {id: "display", title: "Display", icon: "sun-moon", part: "You", has: ["sec-appearance", "sec-dates", "sec-units"],
      finds: [["Appearance", "sec-appearance", ""],   // (the theme: your menu's Dark or Light mode, #410)
        ["Dates and times", "sec-dates", "time zone date format"],
        ["Units", "sec-units", "metric imperial mmol mg kg lb pounds kilograms weight temperature celsius fahrenheit height cm inches"]]},
    {id: "more", title: "More", icon: "ellipsis", has: [], rest: true},   // a section no group names yet
  ]},
  account: {page: "account.html", title: "Account", icon: "circle-user", words: "sign-in security", groups: [
    {id: "security", title: "Sign-in and security", icon: "lock", hint: "How you sign in, and where you're signed in now.",
     has: ["password", "twofa", "passkeys", "sessions"],
     finds: [["Password", "password", "change passphrase"], ["Two-factor sign-in", "twofa", "2fa authenticator code totp recovery"],
       ["Passkeys", "passkeys", "security key fingerprint face"], ["Where you're signed in", "sessions", "sessions devices sign out log out"]]},
    {id: "device", title: "This device", icon: "smartphone", hint: "Sanguity on the device you're using now: as an app, and offline.",
     has: ["installApp", "offlineRecords"],
     finds: [["Install on this device", "installApp", "app home screen"], ["Offline records on this device", "offlineRecords", "offline without a connection"]]},
  ]},
  admin: {page: "admin.html", title: "Admin", icon: "wrench", admin: true, words: "server install", groups: [
    {id: "accounts", title: "Accounts", icon: "users", part: "People", has: ["users"], words: "users new account reset password"},
    {id: "activity", title: "Install activity", icon: "history", part: "People", has: ["installLog"], words: "log sign-ins"},
    {id: "backups", title: "Backups", icon: "archive", part: "Data safety", has: ["backups"], words: "backup restore"},   // #200
    {id: "apps", title: "Connected-source apps", icon: "plug", part: "Connections", has: ["admin", "sec-sync"],
     words: "set up a source client portal app", finds: [["Sync defaults", "sec-sync", "attachments"]]},
    {id: "directory", title: "Provider directory", icon: "book-user", part: "Connections", has: ["directory"], words: "providers"},
    {id: "outside", title: "Outside connections", icon: "globe", part: "Connections", has: ["outside"], words: "network internet"},
    {id: "labs", title: "Lab name matches", icon: "test-tube-diagonal", part: "Lab matching", has: ["labmaps"], words: "aliases mapping"},
  ]},
};
