#!/bin/sh
# MACHINE PREREQUISITE link-rescue-firewall (Linux, 2026-10-26). Run as root only, through the mandate's
# prerequisites (one pkexec prompt at a session start), never by a session on its own.
#   link-rescue-firewall.sh apply|verify|undo PEER ME PORT
# Lets TCP PORT (the rescue door, the link port + 1) in from PEER to ME (the two cable addresses) and nothing else,
# in whichever firewall is active (ufw or firewalld). No active firewall: nothing filters, nothing to open.
# Who can act through it: the cable peer, and only with the household key (the door checks every request).
set -eu
action=${1:-verify}; peer=${2:?peer}; me=${3:?me}; port=${4:?port}
say() { echo "LINK-RESCUE-FIREWALL: $*"; }
case "$peer$me$port" in *[!0-9.]*) say "FAILED: addresses and port must be digits and dots"; exit 2;; esac
fw=""
if command -v ufw >/dev/null 2>&1; then
  if s=$(ufw status 2>/dev/null); then
    if echo "$s" | grep -q "Status: active"; then fw=ufw; fi
  else
    fw=unknown          # ufw is there but its state needs root: never guess "nothing filters"
  fi
fi
if [ -z "$fw" ] && command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then fw=firewalld; fi
if [ "$fw" = unknown ]; then say "UNKNOWN: ufw is installed and its state is readable by root only"; exit 4; fi
rule="rule family=ipv4 source address=$peer/32 destination address=$me/32 port port=$port protocol=tcp accept"
present() {
  case "$fw" in
    ufw) ufw status | grep -q "^$me $port/tcp .*ALLOW IN *$peer" ;;
    firewalld) firewall-cmd --permanent --query-rich-rule="$rule" >/dev/null 2>&1 ;;
    *) return 0 ;;
  esac
}
if [ "$action" != verify ] && [ "$(id -u)" != 0 ]; then say "FAILED: not root"; exit 3; fi
if [ "$action" = apply ] && ! present; then
  case "$fw" in
    ufw) ufw allow in from "$peer" to "$me" port "$port" proto tcp ;;
    firewalld) firewall-cmd --permanent --add-rich-rule="$rule" && firewall-cmd --reload ;;
  esac
fi
if [ "$action" = undo ] && [ -n "$fw" ] && present; then
  case "$fw" in
    ufw) ufw delete allow in from "$peer" to "$me" port "$port" proto tcp ;;
    firewalld) firewall-cmd --permanent --remove-rich-rule="$rule" && firewall-cmd --reload ;;
  esac
fi
if present; then p=yes; else p=no; fi
say "OK $action: firewall ${fw:-none active}, rule for $peer -> $me:$port present = $p"
