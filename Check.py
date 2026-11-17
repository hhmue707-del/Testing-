import os
import re
import html
import logging
import sqlite3
import datetime as dt
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO)

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, filters,
)

# ============ CONFIG ============
# Token ကို environment variable (BOT_TOKEN) ထဲမှာပဲ ထည့်ပါ။ Code ထဲ တိုက်ရိုက်မရေးပါနဲ့
TOKEN = os.getenv("BOT_TOKEN", "9075322512:AAERcZfpKkBsfleRyvUwFafWCbJbtddHAyU")
TZ = ZoneInfo("Asia/Yangon")          # pip install tzdata
GROUP_ID = -1002386132084
REPORT_TO = 5641996451                # 24h report လက်ခံမယ့်သူ
CMD_USER = 5641996451                 # /SendNow သုံးခွင့်ရှိသူ
ADD_USER = 8837432063                 # /Add, /Remove_admin သုံးခွင့်ရှိသူ
DASH_USERS = {CMD_USER, ADD_USER}     # /Dashboard သုံးခွင့်ရှိသူများ
REPORT_HOUR = 3                       # နေ့စဉ် မနက် 3:00 မှာ report ပို့ပြီး reset
REPORT_MINUTE = 0
DASH_FILE = "dashboard.txt"           # တစ်နေ့တာဇယားတွေ ရက်စွဲနဲ့ သိမ်းမယ့်ဖိုင်
TASK_TARGET = 250                     # Admin တစ်ယောက်ချင်းစီ တစ်နေ့ ပို့ရမယ့် Message ပေါင်း (/Task နဲ့ ပြောင်းလို့ရ)
TASK_MODE = "total"                   # "total" = အတွင်း+အပြင် ပေါင်း / "in" = အလုပ်ချိန်အတွင်းစာပဲ

# shifts: [("HH:MM", "HH:MM"), ...]  (late တွက်မယ့် အချိန်)
#         "24/7"  = တစ်နေကုန်အား
#         None    = အားလပ်ချိန်မသတ်မှတ်ရသေး
ADMINS = {
    8290643353: {
        "name": "Jue Jue Thitsar",
        "shifts": [("13:00", "22:00")],
        "display": ["1:00 PM - 10:00 PM"],
        "hours": "9 Hours",
    },
    8718899309: {
        "name": "Strawberry",
        "shifts": [("20:00", "00:00")],
        "display": ["8:00 PM - 12:00 AM"],
        "hours": "4 Hours",
    },
    9176787837: {
        "name": "Bby Mo",
        "shifts": [("12:00", "15:00"), ("22:30", "01:00")],
        "display": ["12:00 PM - 3:00 PM", "10:30 PM - 1:00 AM"],
        "hours": "5.5 Hours Total",
    },
    8924897122: {
        "name": "Hancock",
        "shifts": [("16:00", "00:00")],
        "display": ["4:00 PM - 12:00 AM"],
        "hours": "8 Hours",
    },
    6723891950: {
        "name": "Mizukiel",
        "shifts": [("20:00", "01:00")],
        "display": ["8:00 PM - 1:00 AM"],
        "hours": "5 Hours",
    },
    8858244973: {
        "name": "K",
        "shifts": [("00:00", "02:00")],
        "display": ["12:00 AM - 2:00 AM"],
        "hours": "2 Hours",
    },
}
# လူသစ်တွေကို /Add နဲ့ ထည့်ရင် database ထဲမှာ အမြဲတမ်းသိမ်းထားမယ် (reset မလုပ်ပါ)
# /Remove_admin နဲ့ ထုတ်ရင် removed_admins ထဲမှတ်ထားလို့ restart လုပ်လည်း ပြန်မပေါ်ပါ
# =================================

db = sqlite3.connect("bot.db", check_same_thread=False)
db.execute("CREATE TABLE IF NOT EXISTS msgs(day TEXT, uid INT, n_in INT, n_out INT, PRIMARY KEY(day, uid))")
db.execute(
    "CREATE TABLE IF NOT EXISTS firsts(day TEXT, uid INT, idx INT, first_ts TEXT, late INT, "
    "PRIMARY KEY(day, uid, idx))"
)
db.execute("CREATE TABLE IF NOT EXISTS outs(day TEXT, uid INT, first_ts TEXT, PRIMARY KEY(day, uid))")
db.execute("CREATE TABLE IF NOT EXISTS extra_admins(uid INT PRIMARY KEY, name TEXT, s TEXT, e TEXT)")
db.execute("CREATE TABLE IF NOT EXISTS removed_admins(uid INT PRIMARY KEY)")
db.execute("CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT)")
db.commit()


# ---------- time helpers ----------
def hm(s):
    h, m = map(int, s.split(":"))
    return h * 60 + m


def fmt_hm(s):
    h, m = map(int, s.split(":"))
    return dt.time(h, m).strftime("%I:%M %p").lstrip("0")


def t12(d):
    return d.strftime("%I:%M %p").lstrip("0")


def calc_hours(s, e):
    diff = (hm(e) - hm(s)) % 1440 or 1440
    h = diff / 60
    return f"{int(h)} Hours" if h == int(h) else f"{h:.1f} Hours"


def parse_time(text):
    """2:00pm -> 14:00 / 2:00am -> 02:00 / 10:00pm -> 22:00 / 14:00 -> 14:00"""
    t = text.strip().lower().replace(" ", "")
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?([ap])\.?m\.?", t)
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if not (1 <= h <= 12 and 0 <= mi < 60):
            return None
        if ap == "p" and h != 12:
            h += 12
        if ap == "a" and h == 12:
            h = 0
        return f"{h:02d}:{mi:02d}"
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", t)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h < 24 and 0 <= mi < 60:
            return f"{h:02d}:{mi:02d}"
    return None


def report_day(now):
    """REPORT_HOUR:REPORT_MINUTE မတိုင်သေးရင် ယမန်နေ့အဖြစ်သတ်မှတ်"""
    return (now - dt.timedelta(hours=REPORT_HOUR, minutes=REPORT_MINUTE)).date()


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


# ---------- admins (database) ----------
def make_admin(name, s, e):
    return {
        "name": name,
        "shifts": [(s, e)],
        "display": [f"{fmt_hm(s)} - {fmt_hm(e)}"],
        "hours": calc_hours(s, e),
    }


def load_extra():
    for uid, name, s, e in db.execute("SELECT uid, name, s, e FROM extra_admins").fetchall():
        ADMINS[uid] = make_admin(name, s, e)
    # /Remove_admin နဲ့ ထုတ်ထားတဲ့သူတွေ (code ထဲက ပုံသေစာရင်းပါ ပြန်မပေါ်အောင်)
    for (uid,) in db.execute("SELECT uid FROM removed_admins").fetchall():
        ADMINS.pop(uid, None)


def load_settings():
    global TASK_TARGET
    r = db.execute("SELECT v FROM settings WHERE k='task_target'").fetchone()
    if r:
        TASK_TARGET = int(r[0])


# ---------- record messages ----------
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not update.effective_user or update.effective_chat.id != GROUP_ID:
        return
    uid = update.effective_user.id
    if uid not in ADMINS:
        return
    now = dt.datetime.now(TZ)
    day = str(report_day(now))
    shifts = ADMINS[uid]["shifts"]
    always = shifts is None or shifts == "24/7"

    found = None
    if not always:
        found = find_shift(now, shifts)
    in_shift = always or found is not None

    col = "n_in" if in_shift else "n_out"
    db.execute(
        f"INSERT INTO msgs(day, uid, n_in, n_out) VALUES(?,?,?,?) "
        f"ON CONFLICT(day, uid) DO UPDATE SET {col} = {col} + 1",
        (day, uid, 1 if in_shift else 0, 0 if in_shift else 1),
    )

    if always:
        db.execute(
            "INSERT OR IGNORE INTO firsts(day, uid, idx, first_ts, late) VALUES(?,?,?,?,?)",
            (day, uid, 0, t12(now), -1),
        )
    elif found:
        idx, start = found
        late = max(0, int((now - start).total_seconds() // 60))
        db.execute(
            "INSERT OR IGNORE INTO firsts(day, uid, idx, first_ts, late) VALUES(?,?,?,?,?)",
            (day, uid, idx, t12(now), late),
        )
    else:
        db.execute(
            "INSERT OR IGNORE INTO outs(day, uid, first_ts) VALUES(?,?,?)",
            (day, uid, t12(now)),
        )
    db.commit()


# ---------- report ----------
def build_report(day, now, title, final=False):
    """day = datetime.date / final=True = ၂၄ နာရီပြည့်ပြီ (Task ပြည့်/မပြည့် ဆုံးဖြတ်)"""
    dkey = str(day)
    lines = [title, f"Date: {dkey}", ""]
    task_rows = []
    for n, (uid, a) in enumerate(ADMINS.items(), start=1):
        row = db.execute("SELECT n_in, n_out FROM msgs WHERE day=? AND uid=?", (dkey, uid)).fetchone()
        n_in, n_out = row if row else (0, 0)
        task_rows.append((a["name"], n_in, n_out))
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
            lines.append(f"လိုင်းတက်ချိန်: {r[0]}" if r else "လိုင်းတက်ချိန်: မတက်သေးပါ / မတက်ခဲ့ပါ")
            lines.append(f"Message Count: {n_in}")
        else:
            lines.append("[အလုပ်ချိန်အတွင်း]")
            for i, (s, e) in enumerate(shifts):
                r = db.execute(
                    "SELECT first_ts, late FROM firsts WHERE day=? AND uid=? AND idx=?",
                    (dkey, uid, i),
                ).fetchone()
                sm = hm(s)
                start_dt = dt.datetime.combine(day, dt.time(sm // 60, sm % 60), TZ)
                if sm < REPORT_HOUR * 60 + REPORT_MINUTE:  # ညသန်းခေါင်ကျော်မှ စတဲ့ Shift (ဥပမာ 12:00 AM)
                    start_dt += dt.timedelta(days=1)
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
            lines.append(f"Message Count: {n_in}")
            lines.append("[အလုပ်ချိန်အပြင်]")
            r = db.execute("SELECT first_ts FROM outs WHERE day=? AND uid=?", (dkey, uid)).fetchone()
            lines.append(f"လိုင်းတက်ချိန်: {r[0]}" if r else "လိုင်းတက်ချိန်: မရှိပါ")
            lines.append(f"Message Count: {n_out}")
        lines.append("")

    # ---------- Task ----------
    lines.append(f"Task {TASK_TARGET} Msg")
    done = 0
    for n, (name, n_in, n_out) in enumerate(task_rows, start=1):
        cnt = n_in + n_out if TASK_MODE == "total" else n_in
        lines.append(f"{n:02d}. {name}")
        if final:
            if cnt >= TASK_TARGET:
                done += 1
                lines.append(f"Target Msg: {cnt} / {TASK_TARGET} (ပြည့်ပြီ)")
            else:
                lines.append(f"Target Msg: {cnt} / {TASK_TARGET} (မပြည့်ပါ - {TASK_TARGET - cnt} လိုသေး)")
        else:
            lines.append(f"Target Msg: {cnt} / {TASK_TARGET}")
    if final:
        lines.append("")
        lines.append(f"Task ပြည့်သူ: {done} / {len(task_rows)}")
    return "\n".join(lines)


def pre(t):
    return "<pre>" + html.escape(t) + "</pre>"


def chunks(text, limit=3500):
    out, cur = [], ""
    for block in text.split("\n\n"):
        if cur and len(cur) + len(block) + 2 > limit:
            out.append(cur)
            cur = block
        else:
            cur = f"{cur}\n\n{block}" if cur else block
    if cur:
        out.append(cur)
    return out


# ---------- dashboard txt ----------
def load_dash():
    blocks, cur = {}, None
    if os.path.exists(DASH_FILE):
        with open(DASH_FILE, encoding="utf-8") as f:
            for line in f.read().split("\n"):
                if line.startswith("===== ") and line.endswith(" ====="):
                    cur = line[6:-6]
                    blocks[cur] = []
                elif cur is not None:
                    blocks[cur].append(line)
    return {k: "\n".join(v).strip("\n") for k, v in blocks.items()}


def write_dash(blocks):
    with open(DASH_FILE, "w", encoding="utf-8") as f:
        for k in sorted(blocks):
            f.write(f"===== {k} =====\n{blocks[k]}\n\n")


def save_day(day, text):
    blocks = load_dash()
    blocks[str(day)] = text
    write_dash(blocks)


def sync_dashboard(now):
    """data ရှိပြီး ဖိုင်ထဲမရောက်သေးတဲ့ ပြီးခဲ့တဲ့ရက်တွေကို ထပ်ဖြည့်"""
    blocks = load_dash()
    today = str(report_day(now))
    days = [r[0] for r in db.execute("SELECT DISTINCT day FROM msgs ORDER BY day")]
    changed = False
    for d in days:
        if d < today and d not in blocks:
            blocks[d] = build_report(dt.date.fromisoformat(d), now, "၂၄ နာရီ Admin Report", final=True)
            changed = True
    if changed:
        write_dash(blocks)
    return blocks


def rebuild_dashboard(now):
    """Admin ဖယ်ပြီးနောက် dashboard.txt ထဲက ရက်တိုင်းကို database ကနေ ပြန်တည်ဆောက်"""
    blocks = load_dash()
    if not blocks:
        return
    days = {r[0] for r in db.execute("SELECT DISTINCT day FROM msgs")}
    new = {}
    for d in blocks:
        if d in days:
            new[d] = build_report(dt.date.fromisoformat(d), now, "၂၄ နာရီ Admin Report", final=True)
    write_dash(new)


async def dashboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in DASH_USERS:
        return
    now = dt.datetime.now(TZ)
    blocks = sync_dashboard(now)
    if not blocks:
        await update.effective_message.reply_text("သိမ်းထားတဲ့ ဇယား မရှိသေးပါ")
        return
    days = sorted(blocks)
    caption = f"Dashboard\n{days[0]} to {days[-1]} ({len(days)} days)"
    with open(DASH_FILE, "rb") as f:
        await update.effective_message.reply_document(
            document=f, filename="dashboard.txt", caption=caption
        )


# ---------- commands ----------
async def send_now(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != CMD_USER:
        return
    now = dt.datetime.now(TZ)
    text = build_report(report_day(now), now, "လက်ရှိ Status")
    for part in chunks(text):
        await update.effective_message.reply_text(pre(part), parse_mode="HTML")


async def send_report(ctx: ContextTypes.DEFAULT_TYPE, save=True):
    now = dt.datetime.now(TZ)
    day = report_day(now - dt.timedelta(minutes=1))  # ပြီးခဲ့တဲ့ ၂၄နာရီ
    text = build_report(day, now, "၂၄ နာရီ Admin Report", final=True)
    if save:
        save_day(day, text)       # ပို့ခင် txt ထဲ ကြိုသိမ်း
        sync_dashboard(now)
    for part in chunks(text):
        await ctx.bot.send_message(REPORT_TO, pre(part), parse_mode="HTML")
    # Reset: day key ပြောင်းသွားလို့ အသစ်ကနေ အလိုအလျောက်ပြန်စမယ် (Admin စာရင်းမပျက်)


async def daily_report(ctx: ContextTypes.DEFAULT_TYPE):
    await send_report(ctx, save=True)


async def test_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != CMD_USER:
        return
    try:
        await send_report(ctx, save=False)
        await update.effective_message.reply_text("ပို့ပြီးပါပြီ")
    except Exception as e:
        await update.effective_message.reply_text(f"Error: {e}")


# ---------- /Task (ဘယ်သူမဆို သုံးလို့ရ) ----------
async def task_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/Task -> လက်ရှိ target ကြည့် / /Task 300 -> target ပြောင်း"""
    global TASK_TARGET
    msg = update.effective_message
    if not ctx.args:
        await msg.reply_text(
            f"လက်ရှိ Task Target: {TASK_TARGET} Msg\n"
            "ပြောင်းချင်ရင် /Task 300 လို့ ရိုက်ပါ"
        )
        return
    t = ctx.args[0].strip()
    if not t.isdigit() or not (1 <= int(t) <= 100000):
        await msg.reply_text("ဂဏန်းပဲ ထည့်ပါ (1 - 100000)။ ဥပမာ /Task 300")
        return
    old = TASK_TARGET
    TASK_TARGET = int(t)
    db.execute("INSERT OR REPLACE INTO settings(k, v) VALUES('task_target', ?)", (str(TASK_TARGET),))
    db.commit()
    await msg.reply_text(f"Task Target ပြောင်းပြီးပါပြီ: {old} → {TASK_TARGET} Msg")


# ---------- /Remove_admin ----------
async def remove_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADD_USER:
        return
    msg = update.effective_message
    if not ctx.args or not ctx.args[0].isdigit():
        await msg.reply_text("သုံးပုံ: /Remove_admin 123456789")
        return
    uid = int(ctx.args[0])
    a = ADMINS.pop(uid, None)
    if a is None:
        await msg.reply_text(f"ID {uid} ကို Admin စာရင်းထဲမှာ မတွေ့ပါ")
        return
    for tbl in ("msgs", "firsts", "outs", "extra_admins"):
        db.execute(f"DELETE FROM {tbl} WHERE uid=?", (uid,))
    db.execute("INSERT OR IGNORE INTO removed_admins(uid) VALUES(?)", (uid,))
    db.commit()
    rebuild_dashboard(dt.datetime.now(TZ))
    await msg.reply_text(
        f"ဖယ်ရှားပြီးပါပြီ\nName: {a['name']}\nID: {uid}\n"
        "(Message count၊ လိုင်းတက်ချိန်၊ Dashboard မှတ်တမ်းတွေ အားလုံး ဖျက်ပြီး)"
    )


# ---------- /Add conversation ----------
A_NAME, A_UID, A_START, A_END = range(4)


async def add_begin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["new"] = {}
    await update.message.reply_text("နာမည် ထည့်ပါ")
    return A_NAME


async def add_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["new"]["name"] = update.message.text.strip()
    await update.message.reply_text("Tg Acc ID ထည့်ပါ")
    return A_UID


async def add_uid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.strip()
    if not t.isdigit() or len(t) < 5:
        await update.message.reply_text("ID က ဂဏန်းပဲ ဖြစ်ရပါမယ်။ ပြန်ထည့်ပါ")
        return A_UID
    ctx.user_data["new"]["uid"] = int(t)
    await update.message.reply_text(
        "စတင်မယ့်အချိန် ထည့်ပါ\nဥပမာ 2:00pm = ညနေ 2 နာရီ၊ 2:00am = မနက် 2 နာရီ"
    )
    return A_START


async def add_start_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    v = parse_time(update.message.text)
    if not v:
        await update.message.reply_text("အချိန်ပုံစံမှား။ ဥပမာ 2:00pm သို့ 2:00am ပြန်ထည့်ပါ")
        return A_START
    ctx.user_data["new"]["s"] = v
    await update.message.reply_text(
        "ပြီးဆုံးမယ့်အချိန် ထည့်ပါ\nဥပမာ 10:00pm = ည 10 နာရီ၊ 10:00am = မနက် 10 နာရီ"
    )
    return A_END


async def add_end_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    v = parse_time(update.message.text)
    if not v:
        await update.message.reply_text("အချိန်ပုံစံမှား။ ဥပမာ 10:00pm သို့ 10:00am ပြန်ထည့်ပါ")
        return A_END
    n = ctx.user_data.pop("new")
    n["e"] = v
    db.execute(
        "INSERT OR REPLACE INTO extra_admins(uid, name, s, e) VALUES(?,?,?,?)",
        (n["uid"], n["name"], n["s"], n["e"]),
    )
    db.execute("DELETE FROM removed_admins WHERE uid=?", (n["uid"],))  # ပြန်ထည့်ရင် ဖယ်ထားတဲ့စာရင်းက ဖြုတ်
    db.commit()
    ADMINS[n["uid"]] = make_admin(n["name"], n["s"], n["e"])
    a = ADMINS[n["uid"]]
    await update.message.reply_text(
        "ထည့်ပြီးပါပြီ (အမြဲတမ်းသိမ်းထားပါမယ်)\n"
        f"Name: {a['name']}\nID: {n['uid']}\nTime: {a['display'][0]}\nTotal: {a['hours']}"
    )
    return ConversationHandler.END


async def add_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.pop("new", None)
    await update.message.reply_text("ပယ်ဖျက်လိုက်ပါပြီ")
    return ConversationHandler.END


def main():
    load_extra()
    load_settings()
    app = ApplicationBuilder().token(TOKEN).build()

    only_add = filters.User(ADD_USER) & filters.ChatType.PRIVATE
    text_in = filters.TEXT & ~filters.COMMAND & only_add
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add", add_begin, filters=only_add)],
        states={
            A_NAME: [MessageHandler(text_in, add_name)],
            A_UID: [MessageHandler(text_in, add_uid)],
            A_START: [MessageHandler(text_in, add_start_time)],
            A_END: [MessageHandler(text_in, add_end_time)],
        },
        fallbacks=[CommandHandler("cancel", add_cancel, filters=only_add)],
    ))

    app.add_handler(CommandHandler("sendnow", send_now))              # /SendNow
    app.add_handler(CommandHandler("dashboard", dashboard))           # /Dashboard
    app.add_handler(CommandHandler("testreport", test_report))        # /testreport
    app.add_handler(CommandHandler("task", task_cmd))                 # /Task [number]  (ဘယ်သူမဆို)
   
