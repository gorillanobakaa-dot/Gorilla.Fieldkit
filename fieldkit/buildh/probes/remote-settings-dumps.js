// Is the data built into the binary used? (2026-10-04: a locked server URL silently disabled every dump)
const { Utils } = ChromeUtils.importESModule("resource://services-settings/Utils.sys.mjs");
say("Utils.LOAD_DUMPS:", Utils.LOAD_DUMPS, "SERVER_URL:", Utils.SERVER_URL, "skip remote:", Utils.shouldSkipRemoteActivity);
const { RemoteSettings } = ChromeUtils.importESModule("resource://services-settings/remote-settings.sys.mjs");
for (const name of ["search-config-icons", "url-classifier-skip-urls", "password-rules", "anti-tracking-url-decoration"]) {
  const r = await race(RemoteSettings(name).get(), 8000);
  say(name, r.ok ? r.value.length + " record(s)" : r.error);
}
const icons = RemoteSettings("search-config-icons");
const list = await icons.get();
if (list.length) {
  const a = await race(icons.attachments.get(list[0]), 8000);
  say("first search icon attachment:", a.ok && a.value ? a.value.buffer.byteLength + " bytes from " + a.value._source : (a.error || "none"));
}
