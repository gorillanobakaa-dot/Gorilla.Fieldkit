"""Time bombs (2026-10-09, D-157-42): every date compiled into the tree that switches a protection off is found and
judged; the built-in lists are refreshed only from pure-data files that expire later."""
import datetime

from fieldkit.buildh import timebombs as tb

D = datetime.date
CT_US = 1796155504000000            # 2026-12-01
PINS_US = 1798228253105000          # 2026-12-25
HSTS_US = 1800647445144000          # 2027-01-22


def _grep(lines):
    def run(args, **k):
        return type("R", (), {"stdout": "\n".join(lines), "returncode": 0})()
    return run


GOOD = [f"security/ct/CTKnownLogs.h:17:static const PRTime kCTExpirationTime = INT64_C({CT_US});",
        f"security/manager/ssl/StaticHPKPins.h:676:static const PRTime kPreloadPKPinsExpirationTime = INT64_C({PINS_US});",
        f"security/manager/ssl/nsSTSPreloadList.inc:11:const PRTime gPreloadListExpirationTime = INT64_C({HSTS_US});"]


def test_build_29_dates_pass_today_and_fail_close_to_the_first_one():
    rows = tb.rows("w", at=D(2026, 10, 9), run=_grep(GOOD))
    assert all(r["ok"] for r in rows) and "2026-12-01" in rows[0]["evidence"]
    rows = tb.rows("w", at=D(2026, 11, 1), run=_grep(GOOD))          # 30 days before CT switches off
    assert not rows[0]["ok"] and "lists-refresh" in rows[0]["evidence"] and rows[1]["ok"]


def test_an_unreviewed_or_vanished_expiry_fails():
    new = GOOD + ["netwerk/x/Y.h:3:static const PRTime kSomethingExpirationTime = INT64_C(1900000000000000);"]
    rows = tb.rows("w", at=D(2026, 10, 9), run=_grep(new))
    assert any(not r["ok"] and "kSomethingExpirationTime" in r["evidence"] for r in rows)
    rows = tb.rows("w", at=D(2026, 10, 9), run=_grep(GOOD[1:]))
    assert any(not r["ok"] and "kCTExpirationTime" in r["evidence"] and "not found" in r["evidence"] for r in rows)
    assert not tb.rows("w", at=D(2026, 10, 9), run=_grep([]))[-1]["ok"]          # a blind scan fails


INC = ("/* licence */\n#include <stdint.h>\nconst PRTime gPreloadListExpirationTime = INT64_C({e});\n%%\n"
       "a.com, 1\nb.org, 0\n%%\n")
HDR = ("#ifndef CTKnownLogs_h\n#include \"CTLog.h\"\nstatic const PRTime kCTExpirationTime = INT64_C({e});\n"
       "namespace mozilla::ct {{\nstruct CTLogInfo {{\n  int a;\n}};\nconst CTLogInfo kCTLogList[] = {{\n{rows}}};\n"
       "}}  // namespace mozilla::ct\n#endif\n")


def test_check_list_takes_data_and_refuses_structure_or_code():
    old = INC.format(e=HSTS_US)
    assert tb.check_list("x/nsSTSPreloadList.inc", old, INC.format(e=HSTS_US + 1).replace("b.org, 0", "c.net, 1\nb.org, 0"))[0]
    assert not tb.check_list("x/nsSTSPreloadList.inc", old, old.replace("%%\n", "%%\n#include <evil.h>\n", 1))[0]
    assert not tb.check_list("x/nsSTSPreloadList.inc", old, old.replace("/* licence */", "/* other */"))[0]
    h = HDR.format(e=CT_US, rows='  {"Argon", 1},\n')
    assert tb.check_list("security/ct/CTKnownLogs.h", h, HDR.format(e=CT_US + 5, rows='  {"Argon", 1},\n  {"Xenon", 2},\n'))[0]
    assert not tb.check_list("security/ct/CTKnownLogs.h", h, h.replace('#include "CTLog.h"', '#include "CTLog.h"\n#include "x.h"'))[0]
    assert not tb.check_list("security/ct/CTKnownLogs.h", h, h.replace("}  // namespace", "void f() {\n}\n}  // namespace"))[0]


def test_refresh_writes_only_later_pure_data_and_keeps_line_endings(tmp_path):
    w = tmp_path / "tree"
    for p, text in ((tb.LIST_FILES[0], HDR.format(e=CT_US, rows='  {"A", 1},\n')),
                    (tb.LIST_FILES[1], "#include <stdint.h>\nstatic const PRTime kPreloadPKPinsExpirationTime = "
                                       f"INT64_C({PINS_US});\n"),
                    (tb.LIST_FILES[2], INC.format(e=HSTS_US))):
        (w / p).parent.mkdir(parents=True, exist_ok=True)
        (w / p).write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    newer = {tb.LIST_FILES[0]: HDR.format(e=CT_US + 10 ** 12, rows='  {"A", 1},\n  {"B", 2},\n'),
             tb.LIST_FILES[1]: "#include <stdint.h>\nstatic const PRTime kPreloadPKPinsExpirationTime = "
                               f"INT64_C({PINS_US + 10 ** 12});\n",
             tb.LIST_FILES[2]: INC.format(e=HSTS_US + 10 ** 12)}
    res = tb.refresh(w, fetched=(newer, "abc123", "2026-10-09"), write=True)
    assert all(r["ok"] for r in res["rows"]) and sorted(res["written"]) == sorted(tb.LIST_FILES)
    assert b'{"B", 2},\r\n' in (w / tb.LIST_FILES[0]).read_bytes()
    older = dict(newer, **{tb.LIST_FILES[2]: INC.format(e=HSTS_US - 10 ** 12)})
    res = tb.refresh(w, fetched=(older, "abc123", "2026-10-09"), write=True)
    assert not res["rows"][2]["ok"] and res["written"] == []
