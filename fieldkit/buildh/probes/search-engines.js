// Version: 1.0.0 · updated 26-10-18-23-50
// The search engines the browser really offers (D-158-13: Google only). Born 2026-10-18: build 158 offered Perplexity,
// Bing, DuckDuckGo, Wikipedia and eBay in a profile carried over from 157, while every check had run on a fresh
// profile and passed. Run it on a COPY of a real profile (probe.run profile=...), never only on a fresh one.
// Output: SEARCH|engine|<id>|<name>|<default 0/1>   SEARCH|done|<count>
// the autoconfig sandbox has no Services.search: import the service itself (as install-surface.js does)
const { SearchService: SS } = ChromeUtils.importESModule("moz-src:///toolkit/components/search/SearchService.sys.mjs");
await SS.init();
const def = await SS.getDefault();
const engines = await SS.getVisibleEngines();
for (const e of engines) {
  say(`SEARCH|engine|${e.id}|${e.name}|${def && def.id == e.id ? 1 : 0}`);
}
say(`SEARCH|done|${engines.length}`);
