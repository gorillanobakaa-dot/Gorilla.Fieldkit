// Version: 1.0.0 · updated 26-10-16-00-40
// What can be installed into the INSTALLED build, asked of the browser itself (D-157-05 add-ons, D-158-13 search,
// D-158-17 policies). Born 2026-10-16 after the install surface was closed: uBlock Origin must still be active,
// only Mozilla's built-in system add-ons beside it, Google the only engine, and an engine add refused.
//   fieldkit build-harness probe <task> js=install-surface
// ADDON|<id>|<type>|active=<bool>|location=<name>|appDisabled=<bool>, then SEARCH|<engine names>, then POLICIES|<status>
const { AddonManager } = ChromeUtils.importESModule("resource://gre/modules/AddonManager.sys.mjs");
const { SearchService } = ChromeUtils.importESModule("moz-src:///toolkit/components/search/SearchService.sys.mjs");
for (const a of await AddonManager.getAddonsByTypes(["extension", "theme"])) {
  say(`ADDON|${a.id}|${a.type}|active=${a.isActive}|location=${a.location?.name}|appDisabled=${a.appDisabled}`);
}
await SearchService.init();
say(`SEARCH|${(await SearchService.getVisibleEngines()).map(e => e.name).join(",")}`);
try { await SearchService.addOpenSearchEngine("https://example.org/os.xml", null, {}); say("SEARCH-ADD|allowed"); }
catch (e) { say(`SEARCH-ADD|refused|${String(e).slice(0, 90)}`); }
say(`POLICIES|${Services.policies.status}|${JSON.stringify(Object.keys(Services.policies.getActivePolicies() || {}))}`);
