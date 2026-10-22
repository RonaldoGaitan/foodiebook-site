"""Builds the Foodie Book stats page from Sentry and PostHog.

Live:    SENTRY_TOKEN=... POSTHOG_KEY=... python3 dashboard/build.py stats/index.html
Offline: python3 dashboard/build.py out.html --data snapshot.json
"""
import argparse
import datetime as dt
from zoneinfo import ZoneInfo
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request

SENTRY = "https://us.sentry.io/api/0"
SENTRY_ORG = "foodie-book"
SENTRY_PROJECT = "foodie-book-ios"
SENTRY_PROJECT_ID = "4512178612338688"
SENTRY_WEB = "https://foodie-book.sentry.io"
POSTHOG = "https://us.posthog.com"
POSTHOG_PROJECT = "661336"
DAYS = 30
HERE = os.path.dirname(os.path.abspath(__file__))

EVENT_NAMES = {
    "recipe_saved": "Recipes saved",
    "meal_planned": "Meals planned",
    "cooking_mode_opened": "Cooking mode opened",
    "pro_screen_viewed": "Pro screen viewed",
    "recipe_import_started": "Imports started",
    "recipe_import_finished": "Imports that worked",
    "recipe_import_failed": "Imports that failed",
    "grocery_item_added": "Grocery items added",
    "shopping_trip_finished": "Shopping trips finished",
    "barcode_scanned": "Barcodes scanned",
    "receipt_scanned": "Receipts scanned",
    "pantry_item_added": "Pantry items added",
    "ai_ideas_requested": "AI recipe ideas asked for",
    "onboarding_finished": "Onboarding finished",
    "account_created": "Accounts created (email)",
    "signed_in_with_apple": "Sign in with Apple",
    "pro_purchase_started": "Pro purchase started",
    "pro_purchased": "Pro bought",
    "pro_purchase_cancelled": "Pro purchase cancelled",
}

# (pattern in Sentry title, plain-language name, what it means, how much it matters)
EXPLAIN = [
    (r"EntitlementError: notConfigured", "\u201cPro not set up\u201d",
     "The app couldn't load Pro because the RevenueCat key was missing from the build.",
     "Low if it has stopped. It was fixed once the key was added."),
    (r"PGRST102", "Settings didn't save",
     "Saving a setting (like home store and city) was rejected by Supabase as \u201cempty or invalid data\u201d, so the setting may not have saved.",
     "Medium. Users think it saved when it didn't."),
    (r"PGRST303", "Sign-in clock mismatch",
     "The phone's clock was ahead of the server, so a sign-in token looked like it came from the future.",
     "Low. It fixes itself after a retry."),
    (r"SpoonacularError", "Price/nutrition service off",
     "Spoonacular has no API key yet, so those estimates weren't available.",
     "Low. Expected until the key is added."),
    (r"App Hang Fully Blocked", "App froze (fully)",
     "The screen stopped responding completely for a few seconds.",
     "Medium. Users may think the app is broken."),
    (r"App Hang", "App froze briefly",
     "The app was slow to respond for a couple of seconds.",
     "Low to medium. Watch it if it grows."),
    (r"NSInternalInconsistencyException: Invalid update", "Crash: list changed mid-refresh",
     "A list on screen changed its number of items while it was redrawing, and the app crashed.",
     "High. It's a real crash, even if rare."),
    (r"WatchdogTermination", "iPhone closed the app",
     "iOS shut the app down, probably for using too much memory.",
     "Medium. It looks like a crash to the user."),
    (r"AuthenticationServices.AuthorizationError", "Sign in with Apple stopped",
     "Sign in with Apple was cancelled or failed (code 1000 usually means cancelled or not signed into iCloud).",
     "Low. Usually the person tapped cancel."),
    (r"OEmbedError: noCaption", "Video had no caption",
     "An imported video link had no caption to read.",
     "Low. Normal for some videos."),
    (r"permission denied", "Database permission blocked",
     "The database refused a request the app made.",
     "Medium. A feature may not load for some users."),
]


def explain(title):
    for pat, name, what, matters in EXPLAIN:
        if re.search(pat, title):
            return name, what, matters
    return title[:60], "Sentry recorded this error.", "Unknown. Open it in Sentry to see details."


def get(url, token, data=None):
    req = urllib.request.Request(url, data=data, headers={"Authorization": f"Bearer {token}"})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def fetch_sentry(token):
    q = urllib.parse.urlencode
    issues = get(f"{SENTRY}/projects/{SENTRY_ORG}/{SENTRY_PROJECT}/issues/?"
                 + q({"query": "is:unresolved", "sort": "freq", "statsPeriod": "", "limit": 25}), token)
    stats = get(f"{SENTRY}/organizations/{SENTRY_ORG}/events-stats/?"
                + q({"project": SENTRY_PROJECT_ID, "statsPeriod": f"{DAYS}d", "interval": "1d",
                     "yAxis": "count()", "dataset": "errors"}), token)
    builds = get(f"{SENTRY}/organizations/{SENTRY_ORG}/events/?"
                 + q([("project", SENTRY_PROJECT_ID), ("field", "release"), ("field", "count()"),
                      ("sort", "-count()"), ("statsPeriod", "7d"), ("dataset", "errors"),
                      ("per_page", "8")]), token)
    return {
        "daily": [[dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d"),
                   sum(v.get("count", 0) for v in vals)] for ts, vals in stats["data"]],
        "issues": [{
            "id": i["shortId"], "title": i["title"], "count": int(i["count"]),
            "first_seen": i["firstSeen"], "last_seen": i["lastSeen"],
            "url": i.get("permalink") or f"{SENTRY_WEB}/issues/{i['shortId']}",
        } for i in issues],
        "builds_7d": [[r["release"], r["count()"]] for r in builds["data"]],
    }


def hogql(token, sql):
    body = json.dumps({"query": {"kind": "HogQLQuery", "query": sql}}).encode()
    return get(f"{POSTHOG}/api/projects/{POSTHOG_PROJECT}/query/", token, body)["results"]


def fetch_posthog(token):
    names = ", ".join(f"'{e}'" for e in EVENT_NAMES)
    since = f"timestamp > now() - INTERVAL {DAYS} DAY"
    return {
        "events": hogql(token, f"SELECT event, count() FROM events WHERE {since} AND event IN ({names}) "
                               "GROUP BY event ORDER BY count() DESC"),
        "daily": hogql(token, f"SELECT toString(toDate(timestamp)) AS d, count(), count(DISTINCT distinct_id) "
                              f"FROM events WHERE {since} AND event IN ({names}) GROUP BY d ORDER BY d"),
        "devices": hogql(token, f"SELECT count(DISTINCT distinct_id) FROM events WHERE {since} "
                                f"AND event IN ({names})")[0][0],
        "screens": hogql(token, f"SELECT properties.$screen_name AS s, count(), count(DISTINCT distinct_id) "
                                f"FROM events WHERE {since} AND event = '$screen' AND s IS NOT NULL "
                                "GROUP BY s ORDER BY count() DESC LIMIT 15"),
        "import_fail": hogql(token, f"SELECT properties.reason AS r, count() FROM events WHERE {since} "
                                    "AND event = 'recipe_import_failed' GROUP BY r ORDER BY count() DESC"),
    }


def ago(iso, now):
    t = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    d = (now - t).days
    return "today" if d < 1 else "yesterday" if d < 2 else f"{d} days ago"


def short_day(day):
    return dt.date.fromisoformat(day).strftime("%b %-d")


def e(s):
    return html.escape(str(s))


def metric(label, value, note):
    return (f'<div class="a-metric"><div class="a-metric__label">{e(label)}</div>'
            f'<div class="a-metric__value">{e(value)}</div><div class="a-metric__delta">{e(note)}</div></div>')


def chart(kind, title, unit, head, rows, label):
    ths = "".join(f'<th scope="col"{" data-numeric" if i else ""}>{e(h)}</th>' for i, h in enumerate(head))
    trs = "".join("<tr><th scope=\"row\">" + e(r[0]) + "</th>"
                  + "".join(f"<td data-numeric>{c:,}</td>" for c in r[1:]) + "</tr>" for r in rows)
    return (f'<figure class="a-panel" data-a-chart="{kind}" data-a-chart-unit="{unit}" data-a-chart-label="{e(label)}">'
            f'<figcaption class="a-panel__title">{e(title)}</figcaption><div class="a-table-scroll">'
            f'<table class="a-table"><caption class="a-visually-hidden">{e(label)}</caption>'
            f'<thead><tr>{ths}</tr></thead><tbody>{trs}</tbody></table></div></figure>')


def render(data, now):
    s, p = data.get("sentry"), data.get("posthog")
    local = now.astimezone(ZoneInfo("America/Chicago"))
    out = [f'<p class="a-section__note">Updated {local.strftime("%b %-d, %Y %-I:%M %p")} Central. '
           f'Covers the last {DAYS} days.</p>']

    # Summary
    cards = []
    if s:
        last7 = sum(c for _, c in s["daily"][-7:])
        prev7 = sum(c for _, c in s["daily"][-14:-7])
        crashes = [i for i in s["issues"] if re.search(r"Exception|Watchdog|crash", i["title"], re.I)]
        recent = [i for i in s["issues"] if (now - dt.datetime.fromisoformat(i["last_seen"].replace("Z", "+00:00"))).days < 7]
        trend = "fewer" if last7 < prev7 else "more" if last7 > prev7 else "same as"
        cards += [metric("Errors, last 7 days", f"{last7:,}", f"{trend} than the week before ({prev7:,})"),
                  metric("Open problems", len(s["issues"]), f"{len(recent)} happened this week"),
                  metric("Crash types open", len(crashes), "app actually closed")]
    if p:
        total = sum(n for _, n in p["events"])
        cards += [metric("Phones using tracked features", f"{p['devices']:,}", f"last {DAYS} days"),
                  metric("Tracked actions", f"{total:,}", "saves, plans, cooking, Pro views")]
    out.append(f'<section class="a-section"><h2 class="a-section__title">At a glance</h2>'
               f'<div class="a-grid">{"".join(cards)}</div></section>')

    if s:
        todo = []
        for i in s["issues"]:
            name, what, matters = explain(i["title"])
            if (now - dt.datetime.fromisoformat(i["last_seen"].replace("Z", "+00:00"))).days < 7 \
                    and matters.startswith(("High", "Medium")):
                todo.append(f'<li><a href="{e(i["url"])}">{e(name)}</a>: {e(matters)} '
                            f'({i["count"]:,} times, last {ago(i["last_seen"], now)}).</li>')
        body = (f'<ul class="a-prose">{"".join(todo)}</ul>' if todo
                else '<p class="a-prose">Nothing urgent this week.</p>')
        out.append('<section class="a-section a-callout a-callout--risk"><h2 class="a-section__title">'
                   f'Worth fixing (happened this week)</h2>{body}</section>')

    # Usage
    out.append('<section class="a-section"><h2 class="a-section__title">How people use the app (PostHog)</h2>')
    if p is None:
        out.append('<div class="a-error"><p class="a-error__title">Couldn\u2019t load PostHog</p>'
                   '<p>Check the POSTHOG_KEY secret on GitHub.</p></div>')
    elif not p["events"]:
        out.append('<div class="a-empty"><p><strong>No usage data yet.</strong> The app only sends 4 anonymous '
                   'actions: saving a recipe, planning a meal, opening cooking mode and opening the Pro screen. '
                   'Data starts with the first TestFlight build that has the PostHog key (pushed Oct 21).</p></div>')
    else:
        rows = [[EVENT_NAMES.get(ev, ev), n] for ev, n in p["events"]]
        out.append(chart("bar", "What people did", "", ["Action", "Times"], rows,
                         f"Tracked actions in the last {DAYS} days"))
        drows = [[short_day(d), n, u] for d, n, u in p["daily"]]
        out.append(chart("line", "Activity per day", "", ["Day", "Actions", "Phones"], drows,
                         "Tracked actions and active phones per day"))
        if p.get("screens"):
            out.append(chart("bar", "Most-opened screens", "", ["Screen", "Opens", "Phones"],
                             [[str(n), c, u] for n, c, u in p["screens"]], f"Screen opens in the last {DAYS} days"))
        ev = dict(p["events"])
        ok, bad = ev.get("recipe_import_finished", 0), ev.get("recipe_import_failed", 0)
        if ok + bad:
            why = {"timeout": "took too long", "no_recipe": "no recipe found in the post", "save_failed": "couldn't save (offline?)"}
            reasons = ", ".join(f"{why.get(r, r)}: {n}" for r, n in p.get("import_fail") or [])
            out.append(f'<p class="a-prose">Link imports worked <strong>{round(100 * ok / (ok + bad))}%</strong> of the time '
                       f'({ok:,} worked, {bad:,} failed{"; " + e(reasons) if reasons else ""}).</p>')
        pro = ev.get("pro_screen_viewed", 0)
        out.append(f'<p class="a-prose">The Pro screen was opened <strong>{pro:,}</strong> times. '
                   'Compare that with RevenueCat to see how many turned into trials.</p>')
    out.append(f'<p class="a-prose"><a href="{POSTHOG}/project/{POSTHOG_PROJECT}">Open PostHog</a></p></section>')

    # Errors
    out.append('<section class="a-section"><h2 class="a-section__title">Problems in the app (Sentry)</h2>')
    if s is None:
        out.append('<div class="a-error"><p class="a-error__title">Couldn\u2019t load Sentry</p>'
                   '<p>Check the SENTRY_TOKEN secret on GitHub.</p></div>')
    else:
        rows = [[short_day(d), c] for d, c in s["daily"]]
        out.append(chart("bar", "Errors per day", "", ["Day", "Errors"], rows,
                         f"Sentry error events per day, last {DAYS} days"))
        out.append('<p class="a-section__note">Most problems first. Tap a name to open it in Sentry.</p>')
        if not s["issues"]:
            out.append('<div class="a-empty"><p>No open problems. Nice.</p></div>')
        for i in s["issues"]:
            name, what, matters = explain(i["title"])
            out.append(
                f'<article class="a-panel"><div class="a-panel__head"><h3 class="a-panel__title">'
                f'<a href="{e(i["url"])}">{e(name)}</a></h3><span class="a-label">{i["count"]:,} times</span></div>'
                f'<p class="a-prose">{e(what)}</p><dl class="a-meta-list">'
                f'<div><dt>Does it matter?</dt><dd>{e(matters)}</dd></div>'
                f'<div><dt>Last happened</dt><dd>{ago(i["last_seen"], now)}</dd></div>'
                f'<div><dt>First seen</dt><dd>{ago(i["first_seen"], now)}</dd></div>'
                f'<div><dt>Sentry ID</dt><dd>{e(i["id"])}</dd></div></dl>'
                f'<details class="a-disclosure"><summary>Technical message</summary>'
                f'<p class="a-prose"><code>{e(i["title"][:300])}</code></p></details></article>')
        if s.get("builds_7d"):
            brow = sorted(([r.split("+")[-1], c] for r, c in s["builds_7d"]),
                          key=lambda r: int(r[0]) if r[0].isdigit() else 0)
            out.append(chart("bar", "Errors by build, last 7 days", "", ["Build", "Errors"], brow,
                             "Sentry errors by TestFlight build number, last 7 days"))
            out.append('<p class="a-section__note">Old builds still show errors until testers update.</p>')
    out.append(f'<p class="a-prose"><a href="{SENTRY_WEB}/issues/?project={SENTRY_PROJECT_ID}'
               f'&amp;query=is%3Aunresolved">Open Sentry</a></p></section>')
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("output")
    ap.add_argument("--data", help="read data from a JSON file instead of the APIs")
    a = ap.parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    if a.data:
        data = json.load(open(a.data))
    else:
        data = {}
        for key, env, fn in (("sentry", "SENTRY_TOKEN", fetch_sentry), ("posthog", "POSTHOG_KEY", fetch_posthog)):
            try:
                data[key] = fn(os.environ[env])
            except Exception as ex:  # keep building so one broken source doesn't blank the page
                print(f"{key} failed: {ex}", file=sys.stderr)
                data[key] = None
    template = open(os.path.join(HERE, "template.html")).read()
    os.makedirs(os.path.dirname(os.path.abspath(a.output)), exist_ok=True)
    open(a.output, "w").write(template.replace("<!--DASHBOARD-->", render(data, now)))
    if data.get("sentry") is None and data.get("posthog") is None:
        sys.exit("both sources failed")


if __name__ == "__main__":
    main()
