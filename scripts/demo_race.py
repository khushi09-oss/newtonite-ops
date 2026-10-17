"""Show the three concurrency guarantees against a running stack. Standard library only.

    python scripts/demo_race.py                 # API on localhost:8000
    python scripts/demo_race.py --claimers 50   # more people racing
    python scripts/demo_race.py --base http://other-host:8000

1. Claim race: N people claim one new item at the same instant. Exactly one wins.
2. Retried request: the same comment is sent twice with one Idempotency-Key. It lands once.
3. Stale edit: an edit based on an old version is refused with what changed and who changed it.

Each run creates its own item on the Risk team, so it can be run any number of times.
"""

import argparse
import http.cookiejar
import json
import sys
import threading
import urllib.error
import urllib.request
import uuid
from collections import Counter

LEAD = "finn.okafor@newtonite.dev"  # seeded lead of the Risk team


class Session:
    """One signed-in user: a cookie jar and a JSON helper."""

    def __init__(self, base: str, email: str):
        self.base = base
        self.http = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )
        self.user = self.call("POST", "/auth/dev-login", {"email": email})[1]

    def call(self, method, path, body=None, key=None):
        req = urllib.request.Request(self.base + path, method=method)
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            req.add_header("content-type", "application/json")
        if key:
            req.add_header("Idempotency-Key", key)
        try:
            with self.http.open(req, data, timeout=30) as res:
                raw = res.read()
                return res.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as err:
            raw = err.read()
            return err.code, json.loads(raw) if raw else None


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--base", default="http://localhost:8000", help="API base URL")
    ap.add_argument("--claimers", type=int, default=20, help="how many people race for the claim")
    args = ap.parse_args()

    try:
        lead = Session(args.base, LEAD)
    except urllib.error.URLError as err:
        print(f"Cannot reach the API at {args.base}: {err.reason}. Is `docker compose up` running?")
        return 2
    risk = next(t for t in lead.call("GET", "/me")[1]["teams"] if t["name"] == "Risk")
    members = lead.call("GET", f"/teams/{risk['id']}/members")[1]["members"]
    # The shared demo people only, by their real addresses: anyone else on Risk (an invite made from
    # the Admin page, say) has no password-free sign-in, and is left out instead of breaking the race.
    people = [
        Session(args.base, m["email"])
        for m in members
        if m["role"] != "viewer" and m["email"].endswith("@newtonite.dev")
    ]

    status, item = lead.call(
        "POST",
        "/items",
        {
            "team_id": risk["id"],
            "kind": "incident",
            "title": "Demo: everyone wants this one",
            "priority": 0,
        },
        key=str(uuid.uuid4()),
    )
    assert status == 201, (status, item)
    print(f"Created item {item['id']} on {risk['name']} at version {item['version']}.\n")

    # 1. Claim race
    print(f"1. Claim race: {args.claimers} requests from {len(people)} people, released together.")
    gate = threading.Barrier(args.claimers)
    results = [None] * args.claimers

    def claim(n):
        who = people[n % len(people)]
        gate.wait()
        results[n] = (
            who.user["name"],
            *who.call("POST", f"/items/{item['id']}/claim", {"version": 1}),
        )

    threads = [threading.Thread(target=claim, args=(n,)) for n in range(args.claimers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    codes = Counter(code for _, code, _ in results)
    winner = next(name for name, code, _ in results if code == 200)
    told = {body["current"]["assignee_name"] for _, code, body in results if code == 409}
    events = lead.call("GET", f"/items/{item['id']}/events")[1]["events"]
    claims = sum(1 for e in events if e["kind"] == "claimed")
    print(f"   {codes[200]} succeeded, {codes[409]} were refused with 409.")
    print(f"   Winner: {winner}. Every refusal named: {', '.join(sorted(told))}.")
    print(f"   Claim events in the history: {claims}.")
    ok_race = (
        codes[200] == 1 and codes[409] == args.claimers - 1 and told == {winner} and claims == 1
    )

    # 2. Retried request
    print("\n2. Retried request: one comment, sent twice with the same Idempotency-Key.")
    key = str(uuid.uuid4())
    first = lead.call(
        "POST", f"/items/{item['id']}/comments", {"body": "Paging the on-call."}, key=key
    )
    again = lead.call(
        "POST", f"/items/{item['id']}/comments", {"body": "Paging the on-call."}, key=key
    )
    other = lead.call("POST", f"/items/{item['id']}/comments", {"body": "Something else."}, key=key)
    comments = [
        e
        for e in lead.call("GET", f"/items/{item['id']}/events")[1]["events"]
        if e["kind"] == "comment"
    ]
    print(f"   First: {first[0]}. Retry: {again[0]}, same response: {first[1] == again[1]}.")
    print(f"   Same key with a different body: {other[0]}. Comments stored: {len(comments)}.")
    ok_retry = (
        first[0] == again[0] == 201
        and first[1] == again[1]
        and other[0] == 422
        and len(comments) == 1
    )

    # 3. Stale edit
    print("\n3. Stale edit: the lead changes the priority, then an edit from an old copy arrives.")
    current = lead.call("GET", f"/items/{item['id']}")[1]
    stale_version = current["version"]
    lead.call("PATCH", f"/items/{item['id']}", {"version": stale_version, "priority": 1})
    status, body = lead.call(
        "PATCH",
        f"/items/{item['id']}",
        {"version": stale_version, "title": "Edited from an old tab"},
    )
    last = body["events"][0] if status == 409 else {}
    print(
        f"   Edit based on version {stale_version}: {status}. The item is now at version {body['current']['version'] if status == 409 else '?'}."
    )
    print(f"   Latest change in the response: {last.get('actor_name')} set {last.get('after')}.")
    title_now = lead.call("GET", f"/items/{item['id']}")[1]["title"]
    print(f"   Title is still: {title_now!r}.")
    ok_stale = status == 409 and title_now == item["title"]

    ok = ok_race and ok_retry and ok_stale
    print("\nAll three held." if ok else "\nSomething did not hold. See above.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
