# What "Mesh" is, and what the tick box on your hub actually does

## Mesh, properly

**A mesh network is several wifi access points behaving as one.** Same network
name, same password, and your devices move between them as you walk around,
without the connection dropping.

Without mesh, two access points are two separate networks, and a laptop clings
to the first one until the signal dies completely. With mesh, the infrastructure
actively tells the client *"move to the other one, it is better"*, through three
standards:

```
802.11k   the hub gives the client a list of neighbouring access points
802.11v   the hub suggests the client moves to a specific one
802.11r   the move happens fast, without a full re-authentication
```

That is real and useful, in a house where one access point cannot cover
everything.

## But the tick box usually does not do that

On consumer hubs, "Enable Mesh" is frequently **not a feature that adds
something. It is a constraint that locks other settings.**

Verbatim from one hub's own firmware (EE Smart 5G Hub 7 Plus, read 2026-10-13):

> *"If enabled, then only WiFi settings compatible with Mesh WiFi will be
> available to be selected on these pages."*

It does not create a mesh. It forces you to use only settings that would work
with one, so that the vendor's own satellites will pair without support calls.

The error strings say exactly what it costs:

```
"Please set the 802.11 mode to 802.11be before enabling Mesh"
"Only WPA2/WPA3 and WPA3 modes are compatible with Mesh settings"
"Auto mode is not compatible with Mesh settings"
```

So: Wi-Fi 7 mode forced, modern security forced, no automatic encryption, and
one network name shared across both bands.

## Why it is ticked out of the box

Commercial, not technical. **The vendor sells mesh satellites.** If the hub
ships already compatible, a customer buys a satellite, plugs it in, and it
works. If it shipped unticked, a share of customers would ring support because
their new satellite will not pair.

The cost falls on everyone who never buys one: settings locked for a
compatibility they do not use.

## What that means for an older device

One network name across both bands means **band steering**: the hub decides
which band each device gets. If your wifi card is 2.4 GHz only, the hub tries to
push you to 5 GHz on every connection and has to give up. On some firmware that
shows as slow association, or a device that keeps dropping off.

Many hubs offer a **"Compatibility SSID"**, which creates a separate, plain,
2.4 GHz-only network with no steering negotiation. That is what it is for. It
makes an old device's connection more *reliable*. It does not make it faster,
because the card remains the ceiling.

## If you have no satellites

Unticking Mesh costs you nothing and gives back control of the 802.11 mode, the
security settings and the band configuration.

If you do have satellites, leave it ticked. The restrictions are the price of
them working.
