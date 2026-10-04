// Does the default search engine have its icon? (a placeholder icon means the built-in data was not used)
const { SearchService } = ChromeUtils.importESModule("moz-src:///toolkit/components/search/SearchService.sys.mjs");
const ss = SearchService.wrappedJSObject || SearchService;
await ss.init();
const e = await ss.getDefault();
say("default engine:", e.name);
const u = await race(e.getIconURL(16), 8000);
say("icon 16:", u.ok ? (u.value ? u.value.slice(0, 40) : "none (placeholder)") : u.error);
