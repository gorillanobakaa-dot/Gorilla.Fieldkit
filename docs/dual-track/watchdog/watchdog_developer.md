# fieldkit watchdog: developer track

## What it is

`fieldkit/watchdog/` is the online watchdog, previously a Windows-only loose
script at `Documents/Scripts/OnlineWatchdog/online_watchdog.py`, now one
cross-platform module inside the harness.

```
fieldkit watchdog status             online, ssid, paused, log path
fieldkit watchdog run                the loop, in this terminal
fieldkit watchdog test               speak the alarm once
fieldkit watchdog pause MINUTES      suppress alarms
fieldkit watchdog install [--apply]  systemd user unit (Linux only)
fieldkit watchdog uninstall          remove it
```

## Origin

From the original docstring, 2026-09-23: while testing the Portable Network Hub,
an AI session repeatedly took the laptop off its only internet (the owner's phone
hotspot) by switching the wifi radio off to measure the hub in no-internet
conditions. The owner had no indication the laptop needed help, and the agent
could not report it, because the channel it would have reported through was the
one it had broken.

It is therefore deliberately not agentic: a socket, a timer, and two actions.

## Platform split

| | Windows | Linux |
|---|---|---|
| state dir | `%LOCALAPPDATA%\PortableNetworkHub` | `$XDG_STATE_HOME/gorilla-watchdog` |
| current network | `netsh wlan show interfaces` | `nmcli -t -f ACTIVE,SSID dev wifi` |
| radio on | PowerShell `Windows.Devices.Radios` | `nmcli radio wifi on` |
| rejoin | `netsh wlan connect name=SSID` | `nmcli connection up SSID` |
| autostart | existing scheduled task, untouched | systemd user unit |
| speech | herald (System.Speech) | herald (speech-dispatcher) |

**Windows behaviour is byte-for-byte what it was.** The PowerShell radio
incantation is kept verbatim and marked do-not-tidy. Three tests pin the exact
calls so a future Linux change cannot silently alter them:
`test_windows_still_asks_netsh_for_the_network`,
`test_windows_reconnect_uses_the_radio_incantation_then_netsh`,
`test_windows_autostart_is_not_touched`.

## Two things worth knowing

**The Linux SSID query asks for the ACTIVE connection, not the first one.**
`nmcli -t -f SSID dev wifi` lists every network in range. Taking the first would
cheerfully attempt to join a neighbour's. Hence `ACTIVE,SSID` and a filter on
`yes`. Pinned by `test_linux_asks_nmcli_for_the_ACTIVE_network_only`.

**`online()` is a socket, not an HTTP request.** It proves the route works
without fetching anything, so nothing is sent anywhere and no page is read. That
is a deliberate property given the owner's egress rules, and
`test_online_is_a_socket_not_a_web_request` asserts every probe address is tried
before giving up.

## Speech now defers to the herald

The original inlined a `System.Speech` PowerShell call. It now calls
`herald.say()`, which:

- on Windows takes the same System.Speech path as before
- on Linux goes through `spd-say`, and therefore through whatever voice the
  machine is configured to use

So when `fieldkit/voice` installs a neural voice, the watchdog's alarm improves
with no change here. That is the payoff of fixing speech at the
speech-dispatcher layer rather than per tool.

`speak()` catches every exception and returns `not-spoken` rather than raising:
a machine with no audio device must not crash the watchdog, because its primary
job is to repair the connection and only its secondary job is to shout.
Asserted by `test_a_mute_machine_does_not_stop_the_loop`.

## systemd unit

User-level, no root:

```
~/.config/systemd/user/gorilla-watchdog.service
systemctl --user enable --now gorilla-watchdog
```

`apply()` verifies the artefact rather than the exit code: after enabling it
asks `systemctl --user is-active` and reports `running` or
`enabled-not-running`, rather than trusting the return code of the command that
started it.

`uninstall()` exists because a tool that cannot be removed is a tool nobody will
try. It leaves the log and pause file alone: they are a record.

## Defaults

```
interval          5 s
reconnect-after  20 s
alarm-after      90 s
repeat           60 s
probes           1.1.1.1:443, 8.8.8.8:53, 9.9.9.9:443, 208.67.222.222:443
```

## Not done

Never run on Linux against a real disconnection. The logic is unit-tested with a
fake clock and fake runners, but nobody has pulled the cable and listened. That
is the one test that matters and it needs a person.
