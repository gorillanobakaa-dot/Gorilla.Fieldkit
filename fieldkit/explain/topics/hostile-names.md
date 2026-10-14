# Why a network called `:(` breaks automated tools

## The short version

A wifi network name is **text chosen by a stranger** that lands in the middle of
your scripts. Most tools treat it as if it were a safe word like `HomeWiFi`. It
is not. It is whatever the person who set it up decided to type.

The name `:(` is three characters, and each one attacks a different layer.

## `:` the colon, which separates fields almost everywhere

In Unix, the colon is the default way of saying "this field ends here".

```
root:x:0:0:root:/root:/bin/bash        a line from /etc/passwd
/usr/bin:/bin:/usr/local/bin           a PATH
192.168.1.1:8080                       host and port
fe80::1                                an IPv6 address
```

Network tools do it too. `nmcli -t` prints one network per line, fields split by
colons:

```
HomeWiFi:6:2437 MHz:71:WPA2
   name  chan  freq   signal  security
```

A program reads that by splitting on colons and taking field 2 as the channel.
Now put a colon **inside the name**:

```
:(:13:2472 MHz:100:WPA2
```

Splitting left to right gives an empty first field, then `(`, then `13`. **Every
field has shifted by one.** The program now reads the channel from where the
frequency should be, calls `int("2472 MHz")`, and crashes. Or worse, it does not
crash and quietly reports the wrong numbers.

`nmcli` knows this is dangerous, which is why it **escapes** the colon as `\:`
when it prints:

```
\:(:13:2472 MHz:100
```

That escaping is a warning. A program that does not unescape it will show your
network as `\:(` forever, and a program that splits naively is already wrong.

**The fix:** split from the RIGHT, so the name absorbs any colons it contains,
and unescape afterwards.

## `(` and `)` the brackets, which are commands

These are far more dangerous, because in two different languages they are not
text at all, they are instructions.

**In the shell, brackets start a sub-command.** This is a syntax error:

```sh
echo :(          # bash: syntax error near unexpected token `('
```

So a script that builds a command by gluing the name into a string simply dies.
Quoting fixes it, and most scripts forget:

```sh
fieldkit wifi watch --ssid ':('     # correct
fieldkit wifi watch --ssid :(       # dead
```

**In a regular expression, brackets mean "group".** Searching for the literal
text `:(` with a pattern search does not look for your network, it opens a group
and never closes it:

```
grep -E ':('     # unterminated subpattern: error
grep -F ':('     # -F means "plain text, not a pattern": works
```

So any tool that filters networks by a pattern breaks or matches the wrong thing.

## Why this is a genuine security class, not just an annoyance

Imagine a script that logs your connection:

```sh
echo "connected to $SSID" >> log.txt      # unquoted
```

Someone names their network:

```
test; rm -rf ~; echo
```

The semicolon ends the first command, and the next one runs **as you**. This is
**command injection**, and the network name is attacker-supplied data that a
careless script executed.

The pipe character `|` is the same family. Unquoted, `|` splits one command into
two and feeds the first into the second. A name containing `|` turns a log line
into a pipeline.

**So `:(` is a free, harmless test for a real vulnerability.** If a tool handles
it correctly, it probably escapes properly. If it crashes, prints `\:(`, or
reports the wrong channel, it is parsing names unsafely, and the same weakness
would have executed something nastier.

## The rule, for anyone writing tools

**Never build a command out of a name. Never split a name by position.**

1. Pass arguments as a **list**, not as one glued string, so the shell is never
   involved and brackets and semicolons stay harmless text.
2. Quote anything that does reach a shell.
3. Split field-separated output **from the right**, and unescape afterwards.
4. Search for names as **fixed text**, never as a pattern.
5. Treat the name as data, always. It belongs to someone else.

A tool that survives `:(` will survive the ones that were not meant kindly.
