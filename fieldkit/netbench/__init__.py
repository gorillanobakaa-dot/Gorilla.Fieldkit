"""Network benches B1-B5 of the Gorilla network and satellite study, run against local servers only.

    fieldkit build-harness netbench [TASK] [--bench B1,...] [--profile normal|satellite|slow] [--install-dir D]
                                           [--label NAME] [--links broadband,...] [--repeat N] [--out DIR]
    fieldkit build-harness netbench compare A B

Everything runs on 127.0.0.1: a generated page set (links.py, fixtures.py), a TLS origin server speaking HTTP/1.1
and HTTP/2 and an HTTP/3 server for B3 (servers.py), and an HTTP proxy that emulates a link (delay, rate, loss) in
front of the origin (relay.py). The browser is a headless throwaway copy of the install with a throwaway profile
(browser.py); only the processes this run started are stopped, by PID. The benches (benches.py) report a number only
when they measured it: anything else is UNMEASURED with the reason (report.py).
"""
