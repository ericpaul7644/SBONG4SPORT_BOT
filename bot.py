"""Football Digest bot: /start -> welcome + instructions. Buttons: Scores, Table, News.
Telegram Bot API + ESPN public site endpoints (no API key). Long polling, no database."""
import html
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import requests

TOKEN = os.environ["BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"
SITE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
STANDINGS = "https://site.api.espn.com/apis/v2/sports/soccer/{}/standings"
TZ_HOURS = int(os.getenv("TZ_OFFSET_HOURS", "1"))     # 1 = Nigeria (WAT)
TZ_LABEL = os.getenv("TZ_LABEL", "WAT")
CACHE_SECONDS = 60

LEAGUES = {  # code -> (button label, full name)
    "eng.1": ("Premier League", "Premier League"),
    "esp.1": ("La Liga", "La Liga"),
    "ita.1": ("Serie A", "Serie A"),
    "ger.1": ("Bundesliga", "Bundesliga"),
    "fra.1": ("Ligue 1", "Ligue 1"),
    "uefa.champions": ("Champions League", "Champions League"),
}
BTN_SCORES, BTN_TABLE, BTN_NEWS = "⚽ Scores", "🏆 Table", "📰 News"
KIND_TITLES = {"s": "Scores", "t": "Table", "n": "News"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("football-bot")
_cache = {}

WELCOME = (
    "👋 <b>Welcome to Football Digest!</b>\n\n"
    "Your quick football snapshot, right in Telegram.\n\n"
    "<b>How to use it</b>\n"
    f"1️⃣ Tap <b>{BTN_SCORES}</b> for live, recent and upcoming matches\n"
    f"2️⃣ Tap <b>{BTN_TABLE}</b> for the league standings\n"
    f"3️⃣ Tap <b>{BTN_NEWS}</b> for the latest headlines\n"
    "4️⃣ Pick a league with the buttons under each result "
    "(Premier League, La Liga, Serie A, Bundesliga, Ligue 1, Champions League)\n\n"
    f"<i>Match times are shown in {TZ_LABEL}.</i>"
)


def api(method, **params):
    try:
        data = requests.post(f"{API}/{method}", json=params, timeout=65).json()
    except Exception as e:
        log.warning("API %s failed: %s", method, e)
        return {"ok": False}
    if not data.get("ok"):
        log.warning("API %s error: %s", method, data)
    return data


def fetch(url, params=None):
    """GET JSON with a short cache; serves stale data if ESPN fails; None if nothing."""
    key = (url, tuple(sorted((params or {}).items())))
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        r = requests.get(url, params=params, timeout=15,
                         headers={"User-Agent": "football-digest-bot"})
        r.raise_for_status()
        data = r.json()
        _cache[key] = (now, data)
        return data
    except Exception as e:
        log.warning("fetch failed %s: %s", url, e)
        return hit[1] if hit else None


def esc(s):
    return html.escape(str(s), quote=False)


def local_time(iso):
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")) + timedelta(hours=TZ_HOURS)
    return dt.strftime("%a %d %b %H:%M")


# ---------- digests ----------
def scores_text(code):
    today = datetime.now(timezone.utc).date()
    rng = f"{today - timedelta(days=2):%Y%m%d}-{today + timedelta(days=3):%Y%m%d}"
    data = fetch(f"{SITE}/{code}/scoreboard", {"dates": rng})
    title = f"⚽ <b>{LEAGUES[code][1]} — Scores</b>"
    if data is None:
        return title + "\n\n⚠️ Couldn't load data right now. Try again in a minute."
    live, done, soon = [], [], []
    for ev in sorted(data.get("events", []), key=lambda e: e.get("date", "")):
        try:
            comp = ev["competitions"][0]["competitors"]
            home = next(c for c in comp if c["homeAway"] == "home")
            away = next(c for c in comp if c["homeAway"] == "away")
            name = lambda c: c["team"].get("shortDisplayName") or c["team"]["displayName"]
            st = ev["status"]["type"]
            state = st.get("state")
            if state == "in":
                live.append(f"🔴 {esc(st.get('shortDetail', 'LIVE'))}  <b>{esc(name(home))} "
                            f"{esc(home.get('score', 0))}-{esc(away.get('score', 0))} {esc(name(away))}</b>")
            elif state == "post":
                done.append(f"✅ {esc(name(home))} <b>{esc(home.get('score', 0))}-"
                            f"{esc(away.get('score', 0))}</b> {esc(name(away))}  <i>({local_time(ev['date'])[:10]})</i>")
            else:
                soon.append(f"🕒 {local_time(ev['date'])}  {esc(name(home))} vs {esc(name(away))}")
        except Exception:
            continue
    out = [title]
    if live:
        out += ["", "<b>Live now</b>"] + live
    if done:
        out += ["", "<b>Recent results</b>"] + done[-8:]
    if soon:
        out += ["", f"<b>Coming up ({TZ_LABEL})</b>"] + soon[:8]
    if not (live or done or soon):
        out += ["", "No matches in the last 2 / next 3 days."]
    return "\n".join(out)


def table_text(code):
    title = f"🏆 <b>{LEAGUES[code][1]} — Table</b>"
    if code == "uefa.champions":
        return title + "\n\nTable view isn't available for the Champions League. Try Scores or News."
    data = fetch(STANDINGS.format(code))
    try:
        entries = data["children"][0]["standings"]["entries"]
        rows = []
        for e in entries:
            st = {s["name"]: s.get("value") for s in e["stats"]}
            g = lambda k: int(st.get(k) or 0)
            rows.append((g("rank"), e["team"]["displayName"], g("gamesPlayed"), g("wins"),
                         g("ties"), g("losses"), g("pointDifferential"), g("points")))
        rows.sort(key=lambda r: (r[0] or 999, -r[7], -r[6]))
    except Exception:
        return title + "\n\n⚠️ Couldn't load the table right now. Try again in a minute."
    lines = [" #  Team           P  W  D  L   GD Pts"]
    for i, (rk, team, p, w, d, l, gd, pts) in enumerate(rows, 1):
        lines.append(f"{i:>2}  {esc(team[:13]):<13} {p:>2} {w:>2} {d:>2} {l:>2} {gd:>+4} {pts:>3}")
    return title + "\n\n<pre>" + "\n".join(lines) + "</pre>"


def news_text(code):
    title = f"📰 <b>{LEAGUES[code][1]} — News</b>"
    data = fetch(f"{SITE}/{code}/news")
    if data is None:
        return title + "\n\n⚠️ Couldn't load news right now. Try again in a minute."
    items = []
    for a in data.get("articles", [])[:6]:
        url = (a.get("links", {}).get("web", {}) or {}).get("href")
        head = a.get("headline")
        if head:
            items.append(f"• <a href=\"{html.escape(url, quote=True)}\">{esc(head)}</a>" if url else f"• {esc(head)}")
    return title + "\n\n" + ("\n\n".join(items) if items else "No headlines found right now.")


BUILDERS = {"s": scores_text, "t": table_text, "n": news_text}


def keyboard(kind, current):
    btns = [{"text": ("• " if c == current else "") + LEAGUES[c][0], "callback_data": f"{kind}:{c}"}
            for c in LEAGUES]
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    rows.append([{"text": "🔄 Refresh", "callback_data": f"{kind}:{current}"}])
    return {"inline_keyboard": rows}


MAIN_KB = {"keyboard": [[{"text": BTN_SCORES}, {"text": BTN_TABLE}, {"text": BTN_NEWS}]],
           "resize_keyboard": True}
TEXT_TO_KIND = {BTN_SCORES: "s", BTN_TABLE: "t", BTN_NEWS: "n",
                "/scores": "s", "/table": "t", "/news": "n"}


# ---------- handlers ----------
def handle_message(msg):
    if msg["chat"]["type"] != "private":
        return
    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else text
    if cmd in ("/start", "/help"):
        api("sendMessage", chat_id=chat_id, text=WELCOME, parse_mode="HTML", reply_markup=MAIN_KB)
    elif cmd in TEXT_TO_KIND:
        kind, code = TEXT_TO_KIND[cmd], "eng.1"
        api("sendMessage", chat_id=chat_id, text=BUILDERS[kind](code), parse_mode="HTML",
            reply_markup=keyboard(kind, code), disable_web_page_preview=True)
    elif text:
        api("sendMessage", chat_id=chat_id, text="Tap ⚽ Scores, 🏆 Table or 📰 News, or send /help.",
            reply_markup=MAIN_KB)


def handle_callback(cb):
    msg, data = cb.get("message"), cb.get("data", "")
    kind, _, code = data.partition(":")
    if not msg or kind not in BUILDERS or code not in LEAGUES:
        return api("answerCallbackQuery", callback_query_id=cb["id"])
    res = api("editMessageText", chat_id=msg["chat"]["id"], message_id=msg["message_id"],
              text=BUILDERS[kind](code), parse_mode="HTML", reply_markup=keyboard(kind, code),
              disable_web_page_preview=True)
    api("answerCallbackQuery", callback_query_id=cb["id"],
        text="Updated ✅" if res.get("ok") else "Already up to date")


def main():
    api("deleteWebhook")
    api("setMyCommands", commands=[
        {"command": "scores", "description": "Live, recent and upcoming matches"},
        {"command": "table", "description": "League standings"},
        {"command": "news", "description": "Latest football headlines"},
        {"command": "help", "description": "How to use the bot"}])
    api("setMyShortDescription", short_description="Live football scores, league tables and news on a button. ⚽")
    api("setMyDescription", description=(
        "Your quick football snapshot ⚽\n\n"
        "Tap START, then use the buttons to see:\n"
        "⚽ Live, recent and upcoming matches\n🏆 League tables\n📰 Latest headlines\n\n"
        "Premier League, La Liga, Serie A, Bundesliga, Ligue 1 and Champions League."))
    log.info("Football bot running")
    offset = None
    while True:
        params = {"timeout": 50, "allowed_updates": ["message", "callback_query"]}
        if offset:
            params["offset"] = offset
        res = api("getUpdates", **params)
        if not res.get("ok"):
            time.sleep(5)
            continue
        for upd in res["result"]:
            offset = upd["update_id"] + 1
            try:
                if "message" in upd:
                    handle_message(upd["message"])
                elif "callback_query" in upd:
                    handle_callback(upd["callback_query"])
            except Exception:
                log.exception("update failed: %s", upd)


if __name__ == "__main__":
    main()
