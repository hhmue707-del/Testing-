import os
import html
import sqlite3
import datetime as dt
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters,
)

# ============ CONFIG ============
TOKEN = os.getenv("BOT_TOKEN", "9075322512:AAEzBbY9liRE1QdB2b_ppIxOvMfkW9H78CU")
TZ = ZoneInfo("Asia/Yangon")          # pip install tzdata
GROUP_ID = -1002386132084 
REPORT_TO = 5641996451                # 24h report လက်ခံမယ့်သူ
CMD_USER = 5641996451                 # /SendNow သုံးခွင့်ရှိသူ
REPORT_HOUR = 3                       # နေ့စဉ် မနက် 3:00 မှာ report ပို့ပြီး reset

# shifts: [("HH:MM", "HH:MM"), ...]  (late တွက်မယ့် အချိန်)
#         "24/7"  = တစ်နေကုန်အား (၂၄ နာရီအတွင်း လိုင်းတက်တဲ့ပထမဆုံးအချိန်ကိုမှတ်)
#         None    = အားလပ်ချိန်မသတ်မှတ်ရသေး (လိုင်းတက်ချိန်နဲ့ count ပဲမှတ်)
# display: report မှာပြမယ့် စာသား / hours: စုစုပေါင်းနာရီ
ADMINS = {
    8924897122: {
        "name": "Hancock",
        "shifts": [("09:00", "23:00")],
        "display": ["9:00 AM - 11:00 PM"],
        "hours": "14 Hours",
    },
    6723891950: {
        "name": "Mizukiel",
        "shifts": [("20:30", "02:00")],
        "display": ["8:30 PM - 1:00 AM / 2:00 AM"],
        "hours": "4.5-5.5 Hours",
    },
    9176787837: {
        "name": "A Hnin (Bby_Mo)",
        "shifts": [("12:00", "16:00"), ("22:30", "01:00")],
        "display": ["12:00 PM - 4:00 PM", "10:30 PM - 1:00 AM"],
        "hours": "6.5 Hours Total",
    },
    8858244973: {
        "name": "Daria",
        "shifts": [("23:00", "01:00")],
        "display": ["11:00 PM - 1:00 AM"],
        "hours": "2 Hours",
    },
    8657301648: {
        "name": "Shar Ya Ti Mg",
        "shifts": "24/7",
        "display": ["( 24/7 )"],
        "hours": "24 Hours",
    },
}
# Pam ရဲ့ Telegram ID သိရင် ဒီလိုထည့်ပါ:
# ADMINS[123456789] = {
#     "name": "Pam",
#     "shifts": [("23:00", "01:00")],
#     "display": ["11:00 PM - 1:00 AM"],
#     "hours": "2 Hours",
# }
# =================================

db = sqlite3.connect("bot.db", check_same_thread=False)
db.execute("CREATE TABLE IF NOT EXISTS counts(day TEXT, uid INT, n INT, PRIMARY KEY(day, uid))")
db.execute(
    "CREATE TABLE IF NOT EXISTS firsts(day TEXT, uid INT, idx INT, first_ts TEXT, late INT, "
    "PRIMARY KEY(day, uid, idx))"
)
db.commit()


def hm(s):
    h, m = map(int, s.split(":"))
    return h * 60 + m


def t12(d):
    return d.strftime("%I:%M %p").lstrip("0")


def report_day(now):
    """REPORT_HOUR မတိုင်သေးရင် ယမန်နေ့အဖြစ်သတ်မှတ်"""
    return (now - dt.timedelta(hours=REPORT_HOUR)).date()


def find_shift(now, shifts):
    """now က ဘယ် shift ထဲရောက်လဲ -> (idx, start_datetime)"""
    t = now.hour * 60 + now.minute
    for i, (s, e) in enumerate(shifts):
        sm, em = hm(s), hm(e)
        st = dt.time(sm // 60, sm % 60)
        if sm < em:
            if sm <= t < em:
                return i, dt.datetime.combine(now.date(), st, TZ)
        else:  # ညကျော်
            if t >= sm:
                return i, dt.datetime.combine(now.date(), st, TZ)
            if t < em:
                d = now.date() - dt.timedelta(days=1)
                return i, dt.datetime.combine(d, st, TZ)
    return None


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not update.effective_user or update.effective_chat.id != GROUP_ID:
        return
    uid = update.effective_user.id
    if uid not in ADMINS:
        return
    now = dt.datetime.now(TZ)
    day = str(report_day(now))

    db.execute(
        "INSERT INTO counts(day, uid, n) VALUES(?,?,1) "
        "ON CONFLICT(day, uid) DO UPDATE SET n = n + 1", (day, uid)
    )

    shifts = ADMINS[uid]["shifts"]
    if shifts is None or shifts == "24/7":
        # late မတွက်ဘူး၊ ၂၄နာရီအတွင်း ပထမဆုံး လိုင်းတက်ချိန်ကိုပဲမှတ်
        db.execute(
            "INSERT OR IGNORE INTO firsts(day, uid, idx, first_ts, late) VALUES(?,?,?,?,?)",
            (day, uid, 0, t12(now), -1),
        )
    else:
        found = find_shift(now, shifts)
        if found:
            idx, start = found
            late = max(0, int((now - start).total_seconds() // 60))
            db.execute(
                "INSERT OR IGNORE INTO firsts(day, uid, idx, first_ts, late) VALUES(?,?,?,?,?)",
                (day, uid, idx, t12(now), late),
            )
    db.commit()


def build_report(day, now, title):
    """day = datetime.date"""
    dkey = str(day)
    lines = [title, f"Date: {dkey}", ""]
    for n, (uid, a) in enumerate(ADMINS.items(), start=1):
        row = db.execute("SELECT n FROM counts WHERE day=? AND uid=?", (dkey, uid)).fetchone()
        count = row[0] if row else 0
        lines.append(f"{n:02d}. {a['name']} ({uid})")
        for d in a["display"]:
            lines.append(f"Time: {d}")
        if a["hours"]:
            lines.append(f"Total: {a['hours']}")

        shifts = a["shifts"]
        if shifts is None or shifts == "24/7":
            r = db.execute(
                "SELECT first_ts FROM firsts WHERE day=? AND uid=? AND idx=0", (dkey, uid)
            ).fetchone()
            if r:
                lines.append(f"လိုင်းတက်ချိန်: {r[0]}")
            else:
                lines.append("လိုင်းတက်ချိန်: ယနေ့ မတက်သေးပါ / မတက်ခဲ့ပါ")
        else:
            for i, (s, e) in enumerate(shifts):
                r = db.execute(
                    "SELECT first_ts, late FROM firsts WHERE day=? AND uid=? AND idx=?",
                    (dkey, uid, i),
                ).fetchone()
                sm = hm(s)
                start_dt = dt.datetime.combine(day, dt.time(sm // 60, sm % 60), TZ)
                label = f"Shift {i + 1} " if len(shifts) > 1 else ""
                if r:
                    ts, late = r
                    if late == 0:
                        lines.append(f"{label}လိုင်းတက်ချိန်: {ts} (အချိန်မှန်)")
                    else:
                        lines.append(f"{label}လိုင်းတက်ချိန်: {ts} ({late}min Late)")
                elif now >= start_dt:
                    lines.append(f"{label}လိုင်းတက်ချိန်: မတက်ခဲ့ပါ")
                else:
                    lines.append(f"{label}လိုင်းတက်ချိန်: အချိန်မစသေးပါ")
        lines.append(f"Message Count: {count}")
        lines.append("")
    return "\n".join(lines)


async def send_now(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != CMD_USER:
        return
    now = dt.datetime.now(TZ)
    text = build_report(report_day(now), now, "လက်ရှိ Status")
    await update.effective_message.reply_text(
        "<pre>" + html.escape(text) + "</pre>", parse_mode="HTML"
    )


async def daily_report(ctx: ContextTypes.DEFAULT_TYPE):
    now = dt.datetime.now(TZ)
    day = report_day(now - dt.timedelta(minutes=1))  # ပြီးခဲ့တဲ့ ၂၄နာရီ
    text = build_report(day, now, "၂၄ နာရီ Admin Report")
    await ctx.bot.send_message(
        REPORT_TO, "<pre>" + html.escape(text) + "</pre>", parse_mode="HTML"
    )
    # Reset: day key ပြောင်းသွားလို့ အသစ်ကနေ အလိုအလျောက်ပြန်စမယ်


def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("sendnow", send_now))  # /SendNow
    app.add_handler(MessageHandler(filters.Chat(GROUP_ID) & ~filters.COMMAND, on_message))
    app.job_queue.run_daily(daily_report, time=dt.time(REPORT_HOUR, 0, tzinfo=TZ))
    app.run_polling()


if __name__ == "__main__":
    main()
