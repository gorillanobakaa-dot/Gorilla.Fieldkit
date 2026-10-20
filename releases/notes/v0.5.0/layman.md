# Fieldkit v0.5.0, in plain language

## A tool for the other system never starts

Every Fieldkit tool says which computers it works on: Windows, Linux or both. Until now nothing checked that before a
tool ran, so a Windows tool started on Linux simply crashed. Now, before anything starts, Fieldkit compares the tool
with the computer it is on. If they do not match, it stops, says why, and names what to use on this computer instead.
A model looking for a tool is only shown the tools that work here.

The picture below is a real terminal on a Linux laptop: two Windows tools are refused with the reason, and the
cooling monitor starts.

[![A real terminal on the Linux VAIO: fieldkit hdmi status and fieldkit build-harness build-run are refused as Windows-only with exit 2 and the Linux alternative; fieldkit thermal monitor finds the coretemp sensor and the intel_pstate cap](https://raw.githubusercontent.com/gorillanobakaa-dot/Gorilla.Fieldkit/v0.5.0/docs/screenshots/platform-gate-and-linux-thermal-monitor.png)](https://raw.githubusercontent.com/gorillanobakaa-dot/Gorilla.Fieldkit/v0.5.0/docs/screenshots/platform-gate-and-linux-thermal-monitor.png)

The laptop was busy when this was taken (Fieldkit's own test suite was running), so its idle reading was already
69 C. Because of that the sensor rose less than 10 C under load and was graded "surface", although coretemp is the
processor itself; that grading rule was written for Windows sensors (see the developer notes).

## The cooling monitor works on Linux

`fieldkit thermal monitor` watches the processor's temperature while something heavy runs, such as a browser build,
and slows the processor a little when it gets too hot. It worked only on Windows. On Linux it now reads the
processor's own temperature sensor and uses Linux's own speed limit. When it is not allowed to change the speed, it
says so plainly instead of pretending. It can stop by itself when the build it watches ends.

## A small model finds the right tool

Fieldkit has more than a thousand tool descriptions. A small model is now shown the best few for what it asked, in
the same order Gorilla OpenCode uses, with tools that work offline first. "Recipes" (how something was done before)
are found the same way, to read, never to run.

## Releasing the browser without staying up all night

The Firefox release path is now a few commands that check themselves: preparing the release, packing it overnight,
and testing the Windows installer. While the leak gate measures what the browser sends over the network, nothing
else is allowed to go online, so the measurement stays clean.

## Nothing private leaves the computer

A push to GitHub is checked commit by commit for private files, names and passwords before it leaves. The checks now
cover exactly what git would publish, and a scan finds programs that send usage data on by default.

## Download it as a stranger would

`fieldkit stranger` downloads a project the way someone else would, into an empty folder, and checks that it works
there. Several things that only worked on the owner's computer were found and fixed this way.
