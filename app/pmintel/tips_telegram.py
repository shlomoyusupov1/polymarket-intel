"""Read-only Telegram listener for the tip tracker (uses the owner's own account via Telethon).

  docker compose run --rm tips python -m pmintel.tips_telegram --login        # one time: phone + code
  docker compose run --rm tips python -m pmintel.tips_telegram --list-chats   # find group names
  docker compose run --rm tips python -m pmintel.tips_telegram --history 30   # import the last 30 days
  (service `tips` then runs continuously: new messages + daily outcome updates)

Only groups listed in TG_CHATS (comma-separated titles or numeric ids) are read. Nothing is ever
sent. Only symbols, an optional price, the group title, message id and time are stored.
Without TG_API_ID/TG_API_HASH the service only updates outcomes of manually added tips.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from . import db, tips

log = logging.getLogger("tips.telegram")
SESSION = os.environ.get("TG_SESSION", "/data/telegram")  # docker volume, never in git


def chats_wanted() -> list[str]:
    return [c.strip() for c in os.environ.get("TG_CHATS", "").split(",") if c.strip()]


def matches(dialog_id, title: str, wanted: list[str]) -> bool:
    """TG_CHATS holds the ids printed by --list-chats/--scan (marked form, e.g. -1001234567890)
    or titles. Production bug 2026-10-17: comparing the entity's bare id matched nothing."""
    return str(dialog_id) in wanted or (title or "").strip() in wanted


def handle(conn, title: str, chat_id, msg_id, date: datetime, text: str) -> int:
    symbols, price = tips.extract(text or "")
    n = 0
    for sym in symbols:
        try:
            if tips.record(conn, title, sym, date, price if len(symbols) == 1 else None, kind="telegram",
                           chat_id=str(chat_id), message_id=str(msg_id)):
                n += 1
        except Exception as e:  # noqa: BLE001 - e.g. unique-index duplicate on re-import
            log.debug("skip %s/%s: %s", msg_id, sym, e)
    return n


def client():
    from telethon import TelegramClient
    api_id, api_hash = os.environ.get("TG_API_ID"), os.environ.get("TG_API_HASH")
    if not api_id or not api_hash:
        return None
    return TelegramClient(SESSION, int(api_id), api_hash)


async def outcome_loop(conn):
    """Every 6 h: new SEC insider purchases (last 5 days), then outcomes for all recent tips."""
    from . import sec_insider
    while True:
        from . import sec_events
        for name, fn in (("sec", lambda: sec_insider.run(conn, sec_insider.Sec(), 5)),
                         ("sec_events", lambda: sec_events.run(conn, sec_insider.Sec(), 5)),
                         ("outcomes", lambda: tips.update_outcomes(conn))):
            try:
                res = await asyncio.to_thread(fn)
                log.info("%s: %s", name, res if not isinstance(res, list) else f"{len(res)} days")
            except Exception:
                log.exception("%s failed", name)
        await asyncio.sleep(6 * 3600)


async def run(args):
    conn = db.connect()
    db.init_schema(conn)
    cl = client()
    if cl is None:
        if args.login or args.list_chats or args.history or args.scan:
            raise SystemExit("TG_API_ID / TG_API_HASH are not set in .env")
        log.info("Telegram not configured; updating outcomes only")
        await outcome_loop(conn)
        return
    if args.login:
        await cl.start()  # asks for phone number and the code Telegram sends
        me = await cl.get_me()
        os.chmod(SESSION + ".session", 0o600)
        print(f"logged in as {me.first_name}. Session saved (chmod 600).")
        return
    await cl.connect()
    if not await cl.is_user_authorized():
        raise SystemExit("not logged in: run with --login first")
    if args.scan:
        # count messages that name a stock symbol per group; nothing is stored
        since = datetime.now(timezone.utc) - timedelta(days=args.scan)
        stats = []
        async for d in cl.iter_dialogs():
            if not (d.is_group or d.is_channel):
                continue
            n_msgs = n_tips = 0
            syms: set[str] = set()
            async for m in cl.iter_messages(d.entity, limit=1500):
                if m.date < since:
                    break
                n_msgs += 1
                found, _ = tips.extract(m.message or "")
                if found:
                    n_tips += 1
                    syms.update(found)
            if n_tips:
                stats.append((n_tips, n_msgs, len(syms), d.id, d.name))
        stats.sort(reverse=True)
        print(f"groups with stock symbols in the last {args.scan} days (tip msgs / all msgs / distinct symbols):")
        for n_tips, n_msgs, n_syms, cid, name in stats[:40]:
            print(f"{n_tips:5d} {n_msgs:6d} {n_syms:4d}  {cid}\t{name}")
        return
    if args.list_chats:
        async for d in cl.iter_dialogs():
            if d.is_group or d.is_channel:
                print(f"{d.id}\t{d.name}")
        return
    wanted = chats_wanted()
    if not wanted:
        log.warning("TG_CHATS is empty: no groups will be read")
    dialogs = [d async for d in cl.iter_dialogs() if (d.is_group or d.is_channel) and matches(d.id, d.name, wanted)]
    log.info("reading %d groups: %s", len(dialogs), [d.name for d in dialogs])
    print(f"reading {len(dialogs)} of {len(wanted)} configured groups")
    if args.history:
        since = datetime.now(timezone.utc) - timedelta(days=args.history)
        total = 0
        for d in dialogs:
            async for m in cl.iter_messages(d.entity, offset_date=None, reverse=False):
                if m.date < since:
                    break
                total += handle(conn, d.name, d.id, m.id, m.date, m.message)
        print(f"imported {total} tips from the last {args.history} days")
        print(f"outcomes computed: {tips.update_outcomes(conn)}")
        return
    from telethon import events
    ids = [d.id for d in dialogs]
    names = {d.id: d.name for d in dialogs}

    @cl.on(events.NewMessage(chats=ids))
    async def on_message(ev):  # noqa: ANN001
        n = handle(conn, names.get(ev.chat_id, str(ev.chat_id)), ev.chat_id, ev.id, ev.date, ev.raw_text)
        if n:
            log.info("recorded %d tip(s) from %s", n, names.get(ev.chat_id))

    asyncio.create_task(outcome_loop(conn))
    await cl.run_until_disconnected()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true")
    ap.add_argument("--list-chats", action="store_true")
    ap.add_argument("--scan", type=int, default=0, help="rank groups by stock-symbol messages in the last N days (stores nothing)")
    ap.add_argument("--history", type=int, default=0, help="import the last N days, then exit")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
