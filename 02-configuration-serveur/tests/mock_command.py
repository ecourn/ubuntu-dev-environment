#!/usr/bin/env python3
"""Isolated command doubles for configuration-locale.sh integration tests."""
import json
import os
import sys
from pathlib import Path

state_path = Path(os.environ["NTP_TEST_STATE"])
state = json.loads(state_path.read_text())
name = Path(sys.argv[0]).name
args = sys.argv[1:]
state.setdefault("calls", []).append([name, *args])


def save():
    state_path.write_text(json.dumps(state))


def out(value):
    print(value)
    save()
    sys.exit(0)


def fail():
    save()
    sys.exit(1)


if name == "sudo":
    save()
    os.execvp(args[0], args)
if name == "apt-get":
    if "-s" in args:
        removed = state.get("apt_removes")
        out(f"Remv {removed} [1]" if removed else "0 upgraded, 0 removed")
    if "install" in args and "chrony" in args:
        state["chrony"] = True
    out("")
if name == "dpkg-query":
    out("install ok installed") if state.get(args[-1]) else fail()
if name == "systemctl":
    unit = args[-1].removesuffix(".service")
    if args[0] == "show":
        out("loaded" if state.get(unit) else "not-found")
    if args[0] == "is-active":
        out("") if state.get(unit + "_active") else fail()
    if args[0] == "is-enabled":
        out("") if state.get(unit + "_enabled") else fail()
    if args[0] == "disable":
        state[unit + "_active"] = False
        state[unit + "_enabled"] = False
        out("")
    if args[0] == "enable":
        if state.get("start_fails"):
            fail()
        state[unit + "_active"] = True
        state[unit + "_enabled"] = True
        out("")
if name == "chronyc":
    tries = int(args[1])
    state["sync_checks"] = state.get("sync_checks", 0) + tries
    out("") if state["sync_checks"] > state.get("sync_after", 0) else fail()
if name == "timedatectl":
    if args[0] == "list-timezones":
        out("Europe/Paris\nUTC")
    if args[0] == "set-timezone":
        state["timezone"] = args[1]
        out("")
    if "Timezone" in args:
        out(state.get("timezone", "UTC"))
    if "NTPSynchronized" in args:
        state["sync_checks"] = state.get("sync_checks", 0) + 1
        out("yes" if state["sync_checks"] > state.get("sync_after", 0) else "no")
if name == "locale-gen":
    out("") if args[0] == "fr_FR.UTF-8" else fail()
if name == "locale":
    out("fr_FR.utf8")
if name == "localectl":
    if args[0] == "set-locale":
        state["locale"] = args[1].split("=", 1)[1]
        out("")
    out("System Locale: LANG=" + state.get("locale", "C"))
if name == "date":
    if args == ["+%s"]:
        out(str(state.get("clock", 0)))
    out("mardi 29 septembre 2026 12:00:00 CEST +0200")
if name == "sleep":
    state["clock"] = state.get("clock", 0) + int(args[0])
    out("")
fail()
