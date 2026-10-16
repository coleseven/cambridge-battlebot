"""Download CAMBC platform replay bundles (public API, no auth) with a sidecar of official metadata.

Usage:
  python tools/replay/fetch.py match <matchId>...            [--out replays/platform]
  python tools/replay/fetch.py group <groupId>...
  python tools/replay/fetch.py team [<teamId>|us]... --latest 10
  python tools/replay/fetch.py ladder --top 10 --latest 2     # latest N matches of each top-N team

Each match is saved as <out>/<matchId>.zip (the platform bundle; decode.mjs reads it) plus
<out>/<matchId>.meta.json: {matchId, groupId, map, rounds, createdAt, teams: [A, B], teamIds,
scores, results, statistics} taken from the match group. decode.mjs embeds that sidecar as
meta.platform, and events.py checks gems/kills/deaths against its official statistics.
Existing files are skipped. Requests are spaced by --delay seconds (default 1.0).
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://2026a.battlecode.co.uk/api/v1"
OUR_TEAM = "1158c8e5-3ab7-47da-9a7a-344e175243d0"
UA = {"User-Agent": "cambridge-battlebot replay toolkit (tools/replay/fetch.py)"}


class Client:
    def __init__(self, delay):
        self.delay, self.last = delay, 0.0

    def _get(self, path):
        for attempt in range(4):
            wait = self.last + self.delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.last = time.monotonic()
            try:
                with urllib.request.urlopen(urllib.request.Request(BASE + path, headers=UA), timeout=60) as r:
                    return r.read()
            except Exception as e:  # noqa: BLE001
                if attempt == 3 or getattr(e, "code", 500) in (400, 401, 403, 404):
                    raise
                time.sleep(2 * (attempt + 1))

    def json(self, path):
        return json.loads(self._get(path).decode("utf-8"))

    def bytes(self, path):
        return self._get(path)


def team_groups(c, team, want):
    """Finished match groups for a team, newest first, until `want` matches are covered."""
    groups, before, n = [], None, 0
    while n < want:
        q = {"scope": "all", "team": team, **({"before": before} if before else {})}
        d = c.json("/match-groups?" + urllib.parse.urlencode(q))
        page = d.get("groups", [])
        for g in page:
            if g.get("status") == "finished":
                groups.append(g["id"])
                n += max(1, len(g.get("matches") or []) or 1)
        if not d.get("more") or not page:
            break
        before = f"{page[-1]['createdAt']}_{page[-1]['id']}"
    return groups


def sidecar(group, m):
    sides = m.get("sides") or []
    return {"matchId": m["id"], "groupId": group.get("id"), "map": m.get("map"), "rounds": m.get("rounds"),
            "createdAt": m.get("createdAt") or group.get("createdAt"), "status": m.get("status"),
            "teams": [s.get("teamName") for s in sides], "teamIds": [s.get("teamId") for s in sides],
            "scores": [s.get("score") for s in sides], "results": [s.get("result") for s in sides],
            "statistics": m.get("statistics")}


def save_match(c, out, mid, meta=None):
    z, js = out / f"{mid}.zip", out / f"{mid}.meta.json"
    if meta is not None and not js.exists():
        js.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    if z.exists():
        print(f"skip {mid} (have it)")
        return z
    data = c.bytes(f"/matches/{mid}/bundle")
    tmp = z.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(z)
    print(f"got  {mid} {len(data) // 1024} KB" + (f"  {meta['teams'][0]} vs {meta['teams'][1]} on {meta['map']}" if meta else ""))
    return z


def fetch_group(c, out, gid, limit=None):
    g = c.json(f"/match-groups/{gid}")
    n = 0
    for m in g.get("matches", []):
        if m.get("status") != "finished":
            continue
        if limit is not None and n >= limit:
            break
        save_match(c, out, m["id"], sidecar(g, m))
        n += 1
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["match", "group", "team", "ladder"])
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--out", default="replays/platform")
    ap.add_argument("--latest", type=int, default=10, help="matches per team (team/ladder)")
    ap.add_argument("--top", type=int, default=10, help="ladder teams (ladder)")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    c = Client(a.delay)
    if a.cmd == "match":
        for mid in a.ids:
            save_match(c, out, mid)
        print("note: match ids alone carry no official statistics; use group/team to get the .meta.json sidecar", file=sys.stderr)
    elif a.cmd == "group":
        for gid in a.ids:
            fetch_group(c, out, gid)
    else:
        teams = ([OUR_TEAM if t == "us" else t for t in a.ids] or [OUR_TEAM]) if a.cmd == "team" else \
            [r["teamId"] for r in c.json(f"/ladder?page=1&pageSize={a.top}")["teams"][: a.top]]
        for t in teams:
            left = a.latest
            for gid in team_groups(c, t, a.latest):
                if left <= 0:
                    break
                left -= fetch_group(c, out, gid, left)


if __name__ == "__main__":
    main()
