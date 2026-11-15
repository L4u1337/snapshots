"""Polls a JSON endpoint and appends each response to a gzip'd JSONL file."""
from __future__ import annotations

import argparse
import gzip
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = "https://palermo-gw.prod.rushracing.app/"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
OUT = Path("out")
MIN_GAP = 1.0
_last = 0.0


def get(ses, path: str):
    global _last
    wait = MIN_GAP - (time.monotonic() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.monotonic()
    r = ses.get(BASE + path, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def save(ts, p, race, mtp, status, d) -> int:
    day = OUT / p["programDate"]
    day.mkdir(parents=True, exist_ok=True)
    with gzip.open(day / f"{p['programName']}.jsonl.gz", "at", encoding="utf-8") as f:
        f.write(json.dumps({"ts": ts, "plaza": p["caption"], "carrera": race, "mtp": mtp,
                            "estado": status, "d": d}) + "\n")
    return len(d.get("runnerPools", []))


def cycle(ses, names: set[str], ahead: int) -> tuple[int, float, int, int]:
    progs = [p for p in get(ses, "adw/programs") if p.get("caption") in names]
    live = done = 0
    pause = 300.0
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for p in progs:
        if p.get("currentRaceStatus") in ("OFFICIAL", "CANCELED") and \
                p.get("currentRace") == p.get("numberOfRaces"):
            done += 1
            continue
        cur = p.get("currentRace") or p.get("firstRace") or 1
        mtp = p.get("mtp")
        for k in range(ahead + 1):
            race = cur + k
            if race > p.get("numberOfRaces", 0):
                break
            try:
                d = get(ses, f"adw/races/{p['programDate']}/{p['programName']}/{race}"
                             "/pools-and-changes")
            except requests.RequestException as e:
                print(f"  ! {race}: {e}", flush=True)
                continue
            n = save(ts, p, race, mtp if k == 0 else None,
                     p.get("currentRaceStatus") if k == 0 else None, d)
            live += n > 0
        if mtp is not None:
            pause = min(pause, 20.0 if mtp <= 3 else 45.0 if mtp <= 10 else 120.0)
        else:
            pause = min(pause, 120.0)
    return live, (pause if progs else 600.0), len(progs), done


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--names", default="ARG - Palermo,ARG - San Isidro")
    ap.add_argument("--ahead", type=int, default=2)
    ap.add_argument("--hours", type=float, default=5.6)
    ap.add_argument("--give-up-at", type=int, default=None, metavar="HOUR",
                    help="exit if nothing has shown up by this local hour")
    args = ap.parse_args()
    names = {x.strip() for x in args.names.split(",")}
    ses = requests.Session()
    end = time.monotonic() + args.hours * 3600
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} start, up to {args.hours} h", flush=True)
    seen = False
    failures = 0
    while time.monotonic() < end:
        try:
            live, pause, listed, done = cycle(ses, names, args.ahead)
            failures = 0
            seen |= listed > 0
            if listed and done == listed:
                print(f"{datetime.now():%H:%M:%S} all done", flush=True)
                break
        except (requests.RequestException, ValueError) as e:
            print(f"  ! cycle: {e}", flush=True)
            failures += 1
            live, pause, listed = 0, 15.0, None
        # Only give up on a clean "nothing listed", never on a network error.
        if (not seen and listed == 0 and args.give_up_at is not None
                and datetime.now().hour >= args.give_up_at):
            print(f"{datetime.now():%H:%M:%S} nothing listed: exit", flush=True)
            break
        if failures >= 40:
            raise SystemExit("too many consecutive failures")
        print(f"{datetime.now():%H:%M:%S} live: {live}  next in {pause:.0f}s", flush=True)
        time.sleep(pause)


if __name__ == "__main__":
    main()
