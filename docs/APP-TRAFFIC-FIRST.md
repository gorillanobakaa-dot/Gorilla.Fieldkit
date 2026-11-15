# Look at the real app first

## In plain words

When a program talks to a service and you do not know what it sends, do not guess. Run the real program with a
recorder in front of it, read what it really sent, and copy that. A guess that "looks right" cost a whole day: a
model made up model names, added fake entries to a list, typed in a version number and rebuilt the program twice.
One recording of the real app showed the true names in minutes.

What the tools do for you, the same way every time:

- `fieldkit appcapture start APP` runs the app behind a local recorder and restarts it by its process number.
- `fieldkit appcapture show APP --bodies` lists where it connected and prints what it sent, with passwords and
  tokens blanked out. The raw recording stays in a private temporary folder and never goes into a repository.
- `fieldkit modelcatalog verify` compares your client with the recording and fails if a name was invented, a
  version was typed in, or a probe got "not found".
- `fieldkit appcapture stop APP --recycle` stops the recorder by process number and moves the recording to the trash.

## Developer notes

Environment for the proxied app and its children: `HTTPS_PROXY`, `SSL_CERT_FILE` (system CAs plus the proxy CA),
`NODE_EXTRA_CA_CERTS`, Electron `--proxy-server`. Restart by the recorded PID and creation time, never by name.
The proxy runs on its own venv interpreter with `-I`. Printed output goes through `fieldkit/appcapture/redact.py`
(`ya29.`, `1//`, `AIza`, JWT, Bearer, Authorization, cookies, token fields). Never open or copy the raw flow file.

The 7 DO NOTs (each one a check in `modelcatalog verify`, or a rule in this procedure):

1. Do not guess model ids; take them from the backend's reply.
2. Do not inject entries into a catalogue the backend did not report.
3. Do not hardcode a client version or user agent; read it from the installed app.
4. Do not probe with a hand-made request when a real one can be captured.
5. Do not treat "not found" or 404 on a probe as an answer about the id; compare with the real call.
6. Do not recompile or rebuild to test a guess; capture first.
7. Do not store or print tokens; redact, keep raw flows private, recycle them after use.

Winning path: observe the real client, write down the real request, make your client send the same, verify.
