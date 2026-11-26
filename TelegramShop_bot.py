# -*- coding: utf-8 -*-
"""
ربات فروش خدمات تلگرام
- استارز | گیفت/تدی | بوست | ری‌اکشن پرمیوم | شماره مجازی | سایر خدمات
- پرداخت: کارت + رسید (تومان) و TON
- تحویل: کاملاً دستی توسط ادمین
- قابلیت تغییر اسم دکمه‌ها و محصولات از پنل مدیریت
- aiogram 3.x + SQLite
"""

import asyncio
import html
import logging
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta

import aiohttp
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

# =========================================================
# تنظیمات — فقط همین بخش را ویرایش کن
# =========================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

TOKEN = os.getenv("BOT_TOKEN", "9080957558:AAHfbvdA7PumB7J5RnK_ND9r8eesh8p4azs")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "7790560491").split(",") if x.strip()]
OWNER_ID = int(os.getenv("OWNER_ID", "6915177269") or 0) or ADMIN_IDS[0]

MAIN_CHANNEL = os.getenv("MAIN_CHANNEL", "")
MAIN_CHANNEL_LINK = os.getenv("MAIN_CHANNEL_LINK", "")

DEFAULT_SETTINGS = {
    "card_number": "6037-9912-3956-6957",
    "card_holder": "ابوالفضل خشنود ",
    "min_charge": "50000",
    "crypto_enabled": "1",
    "ton_address": "UQCzp3NB0GKnATdLm9kVcI-GxIyuTldGYlUk0bGr3loLsLlU",
    "ton_rate": "0",              # 0 = خودکار از نوبیتکس
    "price_markup": "0",
    "referral_reward": "5000",
    "support_username": "",
    "shop_open": "1",
    "colored_buttons": "1",
    "glow_buttons": "1",
    "show_stars": "1",
    "show_gift": "1",
    "show_boost": "1",
    "show_reaction": "1",
    "show_number": "1",
    "show_other": "1",
    "welcome_text": (
        "👋 به فروشگاه خدمات تلگرام خوش آمدید!\n\n"
        "⭐ استارز  |  🧸 گیفت  |  🚀 بوست  |  ✨ ری‌اکشن  |  📱 شماره مجازی\n\n"
        "یکی از گزینه‌های زیر را انتخاب کنید 👇"
    ),
}

INVOICE_TTL = 900
INVOICE_GRACE = 300
WATCH_INTERVAL = 4
MIN_TON_ACCEPT = 0.05

DB_PATH = os.path.join(BASE_DIR, "shop.db")
LOG_PATH = os.path.join(BASE_DIR, "bot_errors.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_PATH, encoding="utf-8")],
)

# =========================================================
# Database
# =========================================================

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
db.execute("PRAGMA journal_mode=WAL")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    balance INTEGER NOT NULL DEFAULT 0,
    banned INTEGER NOT NULL DEFAULT 0,
    referred_by INTEGER,
    ref_paid INTEGER NOT NULL DEFAULT 0,
    discount_id INTEGER,
    joined_at TEXT
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    price INTEGER NOT NULL DEFAULT 0,
    qty INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    product_name TEXT NOT NULL,
    category TEXT NOT NULL,
    price INTEGER NOT NULL,
    orig_price INTEGER NOT NULL,
    target_id INTEGER,
    target_username TEXT,
    note TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT,
    done_at TEXT
);
CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    file_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    reason TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS discounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    percent INTEGER NOT NULL,
    usage_limit INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS discount_uses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    discount_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    text TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS crypto_invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    comment TEXT NOT NULL UNIQUE,
    created_ts INTEGER NOT NULL,
    expires_ts INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    chat_id INTEGER,
    message_id INTEGER
);
CREATE TABLE IF NOT EXISTS crypto_payments (
    tx_hash TEXT PRIMARY KEY,
    invoice_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    ton_amount REAL NOT NULL,
    rate INTEGER NOT NULL,
    toman INTEGER NOT NULL,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS button_labels (
    orig TEXT PRIMARY KEY,
    label TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS force_join (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    link TEXT NOT NULL
);
"""
db.executescript(SCHEMA)
db.commit()

# محصولات نمونه
SEED_PRODUCTS = [
    ("stars", "⭐ ۵۰ استارز", 0, 90000),
    ("stars", "⭐ ۱۰۰ استارز", 0, 175000),
    ("stars", "⭐ ۲۵۰ استارز", 0, 430000),
    ("stars", "⭐ ۵۰۰ استارز", 0, 850000),
    ("stars", "⭐ ۱۰۰۰ استارز", 0, 1700000),
    ("gift", "🧸 تدی (نمونه)", 0, 150000),
    ("gift", "🎁 گیفت استارزی ۵۰", 50, 120000),
    ("gift", "🎁 گیفت استارزی ۱۰۰", 100, 230000),
    ("boost", "🚀 بوست ۱ روزه", 1, 80000),
    ("boost", "🚀 بوست ۳ روزه", 3, 200000),
    ("boost", "🚀 بوست ۵ روزه", 5, 320000),
    ("boost", "🚀 بوست ۱ ماهه", 30, 900000),
    ("reaction", "✨ ری‌اکشن پرمیوم", 0, 120000),
    ("number", "📱 شماره مجازی (نمونه)", 0, 250000),
    ("other", "🎁 سرویس سفارشی", 0, 100000),
]
if db.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
    for cat, name, qty, price in SEED_PRODUCTS:
        db.execute(
            "INSERT INTO products (category, name, qty, price) VALUES (?,?,?,?)",
            (cat, name, qty, price),
        )
    db.commit()

CATEGORIES = {
    "stars": ("⭐", "استارز تلگرام"),
    "gift": ("🧸", "گیفت و تدی"),
    "boost": ("🚀", "بوست کانال/گروه"),
    "reaction": ("✨", "ری‌اکشن پرمیوم"),
    "number": ("📱", "شماره مجازی"),
    "other": ("🎁", "سایر خدمات"),
}

# ---------------- settings cache ----------------

_settings = {}
_btn_labels = {}


def _load_settings():
    _settings.clear()
    for row in db.execute("SELECT key, value FROM settings"):
        _settings[row["key"]] = row["value"]


def _load_btn_labels():
    _btn_labels.clear()
    for row in db.execute("SELECT orig, label FROM button_labels"):
        _btn_labels[row["orig"]] = row["label"]


_load_settings()
_load_btn_labels()


def get_setting(key):
    if key in _settings:
        return _settings[key]
    return DEFAULT_SETTINGS.get(key, "")


def set_setting(key, value):
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    db.commit()
    _settings[key] = str(value)


def flag(key):
    return get_setting(key) == "1"


def cat_visible(cat):
    return get_setting("show_" + cat) != "0"


# ---------------- helpers ----------------

DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def fmt(n):
    return f"{int(n):,}"


def esc(s):
    return html.escape(str(s or ""))


def to_int(text):
    t = (text or "").translate(DIGITS).replace(",", "").replace("٬", "").strip()
    return int(t) if t.isdigit() else None


def is_admin(user_id):
    return user_id in ADMIN_IDS


def is_owner(user_id):
    return user_id == OWNER_ID


def mention(user_id, name=None):
    return f'<a href="tg://user?id={user_id}">{esc(name or user_id)}</a>'


def user_row(user_id):
    return db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def user_label(row):
    if row is None:
        return "؟"
    name = row["first_name"] or str(row["user_id"])
    uname = f" (@{row['username']})" if row["username"] else ""
    return f"{mention(row['user_id'], name)}{uname} — <code>{row['user_id']}</code>"


def upsert_user(tg_user, ref=None):
    row = user_row(tg_user.id)
    if row is None:
        referred = ref if (ref and ref != tg_user.id and user_row(ref)) else None
        db.execute(
            "INSERT INTO users (user_id, username, first_name, referred_by, joined_at) "
            "VALUES (?,?,?,?,?)",
            (tg_user.id, tg_user.username, tg_user.first_name, referred, now()),
        )
    else:
        db.execute(
            "UPDATE users SET username=?, first_name=? WHERE user_id=?",
            (tg_user.username, tg_user.first_name, tg_user.id),
        )
    db.commit()


def add_balance(user_id, amount, reason):
    db.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
    db.execute(
        "INSERT INTO transactions (user_id, amount, reason, created_at) VALUES (?,?,?,?)",
        (user_id, amount, reason, now()),
    )
    db.commit()


def get_balance(user_id):
    row = user_row(user_id)
    return row["balance"] if row else 0


def active_discount(user_id):
    row = db.execute(
        """SELECT d.* FROM users u JOIN discounts d ON d.id = u.discount_id
           WHERE u.user_id=? AND d.active=1""",
        (user_id,),
    ).fetchone()
    if not row:
        return None
    if row["expires_at"] and row["expires_at"] < now():
        return None
    if row["usage_limit"]:
        used = db.execute(
            "SELECT COUNT(*) FROM discount_uses WHERE discount_id=?", (row["id"],)
        ).fetchone()[0]
        if used >= row["usage_limit"]:
            return None
    return row


def marked_up(base_price):
    try:
        pct = float(get_setting("price_markup") or 0)
    except ValueError:
        pct = 0
    if pct <= 0 or base_price <= 0:
        return base_price
    return max(1000, int(round(base_price * (100 + pct) / 100 / 1000) * 1000))


def price_for(user_id, base_price):
    base_price = marked_up(base_price)
    d = active_discount(user_id)
    if d:
        return base_price - base_price * d["percent"] // 100, d
    return base_price, None


def place_order(user_id, product, target_id, target_username):
    price, disc = price_for(user_id, product["price"])
    orig = marked_up(product["price"])
    cur = db.cursor()
    try:
        cur.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id=? AND balance >= ?",
            (price, user_id, price),
        )
        if cur.rowcount == 0:
            db.rollback()
            return "nofunds", None, price
        cur.execute(
            """INSERT INTO orders (user_id, product_id, product_name, category,
               price, orig_price, target_id, target_username, status, created_at)
               VALUES (?,?,?,?,?,?,?,?, 'pending', ?)""",
            (user_id, product["id"], product["name"], product["category"],
             price, orig, target_id, target_username, now()),
        )
        order_id = cur.lastrowid
        cur.execute(
            "INSERT INTO transactions (user_id, amount, reason, created_at) VALUES (?,?,?,?)",
            (user_id, -price, f"خرید سفارش #{order_id}", now()),
        )
        if disc:
            cur.execute(
                "INSERT INTO discount_uses (discount_id, user_id) VALUES (?,?)",
                (disc["id"], user_id),
            )
            cur.execute("UPDATE users SET discount_id=NULL WHERE user_id=?", (user_id,))
        db.commit()
        return "ok", order_id, price
    except Exception:
        db.rollback()
        raise


def get_order(order_id):
    return db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()


def finish_order(order_id):
    cur = db.execute(
        "UPDATE orders SET status='done', done_at=? WHERE id=? AND status='pending'",
        (now(), order_id),
    )
    db.commit()
    return cur.rowcount == 1


def reject_order(order_id):
    order = get_order(order_id)
    if not order or order["status"] != "pending":
        return False
    cur = db.cursor()
    try:
        cur.execute(
            "UPDATE orders SET status='rejected', done_at=? WHERE id=? AND status='pending'",
            (now(), order_id),
        )
        if cur.rowcount == 0:
            db.rollback()
            return False
        cur.execute("UPDATE users SET balance = balance + ? WHERE user_id=?",
                    (order["price"], order["user_id"]))
        cur.execute(
            "INSERT INTO transactions (user_id, amount, reason, created_at) VALUES (?,?,?,?)",
            (order["user_id"], order["price"], f"بازگشت وجه سفارش #{order_id}", now()),
        )
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise


STATUS_TEXT = {
    "pending": "⏳ در انتظار تحویل",
    "done": "✅ تحویل داده شد",
    "rejected": "❌ لغو شد (وجه برگشت داده شد)",
}
STATUS_ICON = {"pending": "⏳", "done": "✅", "rejected": "❌"}


def order_text(o, for_admin=False):
    cat_icon = CATEGORIES.get(o["category"], ("📦", ""))[0]
    target = "—"
    if o["category"] == "boost":
        target = esc(o["target_username"]) or "—"
    elif o["target_id"]:
        target = mention(o["target_id"], "پروفایل") + f" — <code>{o['target_id']}</code>"
        if o["target_username"]:
            target += f" @{esc(o['target_username'])}"
    elif o["target_username"]:
        target = f"@{esc(o['target_username'])}"
    lines = [
        f"🧾 <b>سفارش #{o['id']}</b>",
        "",
        f"{cat_icon} سرویس: <b>{esc(o['product_name'])}</b>",
        f"{'📣 کانال/گروه' if o['category'] == 'boost' else '🎯 گیرنده'}: {target}",
        f"💰 مبلغ: <b>{fmt(o['price'])}</b> تومان",
    ]
    if o["orig_price"] != o["price"]:
        lines.append(f"🏷 قیمت اصلی: <s>{fmt(o['orig_price'])}</s> تومان")
    lines.append(f"📌 وضعیت: {STATUS_TEXT.get(o['status'], o['status'])}")
    lines.append(f"🕐 ثبت: {o['created_at']}")
    if o["done_at"]:
        lines.append(f"🏁 پایان: {o['done_at']}")
    if o["note"]:
        lines.append(f"📝 یادداشت: {esc(o['note'])}")
    if for_admin:
        lines.insert(2, f"👤 خریدار: {user_label(user_row(o['user_id']))}")
    return "\n".join(lines)


# =========================================================
# Keyboards
# =========================================================

PRIMARY, SUCCESS, DANGER = "primary", "success", "danger"

_DANGER_WORDS = ("❌", "🚫", "🗑", "➖", "انصراف", "حذف", "لغو", "رد ", "کسر", "بن ")
_SUCCESS_WORDS = ("✅", "➕", "تایید", "تأیید", "تحویل", "شارژ", "خرید", "پرداخت", "ارسال", "بله", "ذخیره")
_NAV_WORDS = ("🔙", "🏠", "◀️", "▶️", "بازگشت", "منوی اصلی")
ALT = (PRIMARY, SUCCESS)


def auto_style(text):
    t = text or ""
    if any(w in t for w in _DANGER_WORDS):
        return DANGER
    if any(w in t for w in _SUCCESS_WORDS):
        return SUCCESS
    if any(w in t for w in _NAV_WORDS):
        return PRIMARY
    return ALT[hash(t) % 2]


def B(text, cb=None, style=None, url=None, raw=False):
    style = style or auto_style(text)
    if not raw:
        text = _btn_labels.get(text, text)
        if cb != "noop" and len(text) <= 40 and flag("glow_buttons"):
            text = f"✦ {text} ✦"
    kwargs = {"text": text}
    if url:
        kwargs["url"] = url
    else:
        kwargs["callback_data"] = cb
    if style and flag("colored_buttons"):
        kwargs["style"] = style
    return InlineKeyboardButton(**kwargs)


def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def home_btn():
    return [B("🏠 منوی اصلی", "home")]


def back_kb(to, label="🔙 بازگشت"):
    return kb([[B(label, to)]])


def home_kb(user_id):
    rows = []
    top = []
    if cat_visible("stars"):
        top.append(B("⭐ خرید استارز", "cat:stars", SUCCESS))
    if cat_visible("gift"):
        top.append(B("🧸 گیفت و تدی", "cat:gift", PRIMARY))
    if top:
        rows.append(top)
    mid = []
    if cat_visible("boost"):
        mid.append(B("🚀 بوست", "cat:boost", DANGER))
    if cat_visible("reaction"):
        mid.append(B("✨ ری‌اکشن پرمیوم", "cat:reaction", PRIMARY))
    if mid:
        rows.append(mid)
    if cat_visible("number"):
        rows.append([B("📱 شماره مجازی", "cat:number", SUCCESS)])
    if cat_visible("other"):
        rows.append([B("🎁 سایر خدمات", "cat:other", PRIMARY)])
    rows += [
        [B("💰 کیف پول", "wallet", SUCCESS), B("📦 سفارش‌های من", "orders", PRIMARY)],
        [B("🎟 کد تخفیف", "discount", DANGER), B("🎯 دعوت دوستان", "referral", SUCCESS)],
        [B("👨‍💻 پشتیبانی", "support", PRIMARY)],
    ]
    if is_admin(user_id):
        rows.append([B("👑 پنل مدیریت", "adm", DANGER)])
    return kb(rows)


def paginate(items, page, per=8):
    total = max(1, (len(items) + per - 1) // per)
    page = max(0, min(page, total - 1))
    return items[page * per:(page + 1) * per], page, total


def nav_row(prefix, page, total):
    row = []
    if page > 0:
        row.append(B("◀️ قبلی", f"{prefix}:{page - 1}"))
    if total > 1:
        row.append(B(f"{page + 1}/{total}", "noop"))
    if page < total - 1:
        row.append(B("بعدی ▶️", f"{prefix}:{page + 1}"))
    return row


def adm_back(to="adm", label="🔙 پنل مدیریت"):
    return kb([[B(label, to)]])


def cancel_adm(to="adm"):
    return kb([[B("❌ انصراف", to, DANGER)]])


# =========================================================
# Force join
# =========================================================

def required_channels():
    chans = []
    if MAIN_CHANNEL:
        chans.append((MAIN_CHANNEL, MAIN_CHANNEL_LINK or f"https://t.me/{MAIN_CHANNEL.lstrip('@')}"))
    for row in db.execute("SELECT chat_id, link FROM force_join"):
        chans.append((row["chat_id"], row["link"]))
    return chans


async def is_member(bot, user_id):
    if is_admin(user_id):
        return True
    for chat_id, _link in required_channels():
        try:
            m = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if m.status in ("left", "kicked"):
                return False
        except Exception as e:
            logging.warning("membership check failed %s/%s: %s", chat_id, user_id, e)
    return True


def join_kb():
    rows = []
    for i, (_cid, link) in enumerate(required_channels(), start=1):
        rows.append([B(f"📢 عضویت در کانال {i}", url=link)])
    rows.append([B("✅ عضو شدم، تایید", "check_join", SUCCESS)])
    return kb(rows)


JOIN_TEXT = (
    "⛔ برای استفاده از ربات ابتدا باید در کانال(های) ما عضو شوید.\n\n"
    "بعد از عضویت روی «✅ عضو شدم، تایید» بزنید."
)


# =========================================================
# Render helpers
# =========================================================

async def render(event, text, markup=None):
    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as e:
            if "not modified" in str(e):
                return
            try:
                await event.message.delete()
            except Exception:
                pass
            await event.message.answer(text, reply_markup=markup)
    else:
        await event.answer(text, reply_markup=markup)


async def safe_send(bot, chat_id, text, markup=None):
    try:
        await bot.send_message(chat_id, text, reply_markup=markup)
        return True
    except Exception as e:
        logging.warning("send_message to %s failed: %s", chat_id, e)
        return False


async def notify_admins(bot, text, markup=None):
    for admin_id in ADMIN_IDS:
        await safe_send(bot, admin_id, text, markup)


# =========================================================
# FSM States
# =========================================================

class BuyFlow(StatesGroup):
    target = State()
    channel = State()


class ChargeFlow(StatesGroup):
    amount = State()
    photo = State()


class RedeemFlow(StatesGroup):
    code = State()


class SupportFlow(StatesGroup):
    message = State()


class AddProduct(StatesGroup):
    name = State()
    qty = State()
    price = State()
    desc = State()


class EditPrice(StatesGroup):
    value = State()


class AdminMoney(StatesGroup):
    amount = State()


class AdminMsg(StatesGroup):
    text = State()


class AdminSearch(StatesGroup):
    query = State()


class AdminById(StatesGroup):
    uid = State()


class AddDiscount(StatesGroup):
    code = State()
    percent = State()
    limit = State()
    days = State()


class AddForceJoin(StatesGroup):
    chat = State()
    link = State()


class Broadcast(StatesGroup):
    content = State()


class SettingEdit(StatesGroup):
    value = State()


class ButtonRename(StatesGroup):
    value = State()


class EditProductName(StatesGroup):
    value = State()


# =========================================================
# Middleware
# =========================================================

class Gate(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is None or user.is_bot:
            return await handler(event, data)

        bot = data["bot"]
        ref = None
        if isinstance(event, Message) and event.text and event.text.startswith("/start"):
            parts = event.text.split(maxsplit=1)
            if len(parts) > 1 and parts[1].startswith("ref") and parts[1][3:].isdigit():
                ref = int(parts[1][3:])
        upsert_user(user, ref)

        if is_admin(user.id):
            return await handler(event, data)

        row = user_row(user.id)
        is_cb = isinstance(event, CallbackQuery)

        if row and row["banned"]:
            msg = "⛔ شما توسط مدیریت از استفاده از ربات مسدود شده‌اید."
            if is_cb:
                await event.answer(msg, show_alert=True)
            else:
                await event.answer(msg)
            return

        if is_cb and event.data == "check_join":
            return await handler(event, data)

        if not await is_member(bot, user.id):
            if is_cb:
                await event.answer("⛔ ابتدا عضو کانال شوید.", show_alert=True)
                await render(event, JOIN_TEXT, join_kb())
            else:
                await event.answer(JOIN_TEXT, reply_markup=join_kb())
            return

        return await handler(event, data)


# =========================================================
# Routers
# =========================================================

user_router = Router()
admin_router = Router()
admin_router.message.filter(F.from_user.id.in_(ADMIN_IDS))
admin_router.callback_query.filter(F.from_user.id.in_(ADMIN_IDS))


async def pay_referral(bot, user_id):
    row = user_row(user_id)
    if not row or not row["referred_by"] or row["ref_paid"]:
        return
    reward = to_int(get_setting("referral_reward")) or 0
    db.execute("UPDATE users SET ref_paid=1 WHERE user_id=?", (user_id,))
    db.commit()
    if reward > 0:
        add_balance(row["referred_by"], reward, f"پاداش دعوت کاربر {user_id}")
        await safe_send(
            bot,
            row["referred_by"],
            "🎉 یک نفر با لینک اختصاصی شما عضو ربات شد!\n\n"
            f"💰 مبلغ <b>{fmt(reward)}</b> تومان به کیف پول شما اضافه شد.",
        )


# =========================================================
# /start ، منوی اصلی
# =========================================================

@user_router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    await pay_referral(bot, message.from_user.id)
    await message.answer(get_setting("welcome_text"), reply_markup=home_kb(message.from_user.id))


@user_router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ عملیات لغو شد.", reply_markup=home_kb(message.from_user.id))


@user_router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery):
    await call.answer()


@user_router.callback_query(F.data == "check_join")
async def cb_check_join(call: CallbackQuery, state: FSMContext, bot: Bot):
    if await is_member(bot, call.from_user.id):
        await pay_referral(bot, call.from_user.id)
        await call.answer("✅ عضویت تایید شد.")
        await render(call, "✅ عضویت شما تایید شد!\n\n" + get_setting("welcome_text"),
                     home_kb(call.from_user.id))
    else:
        await call.answer("❌ هنوز عضو نشده‌اید!", show_alert=True)


@user_router.callback_query(F.data == "home")
async def cb_home(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    await render(call, get_setting("welcome_text"), home_kb(call.from_user.id))


# =========================================================
# خرید
# =========================================================

@user_router.callback_query(F.data.startswith("cat:"))
async def cb_category(call: CallbackQuery, state: FSMContext):
    await state.clear()
    cat = call.data.split(":", 1)[1]
    if cat not in CATEGORIES:
        await call.answer()
        return
    if not cat_visible(cat) and not is_admin(call.from_user.id):
        await call.answer("🔒 این بخش فعلاً در دسترس نیست.", show_alert=True)
        return
    icon, title = CATEGORIES[cat]
    products = db.execute(
        "SELECT * FROM products WHERE category=? AND active=1 ORDER BY price", (cat,)
    ).fetchall()
    await call.answer()
    if not products:
        await render(call, f"{icon} <b>{title}</b>\n\n❌ فعلاً محصولی موجود نیست.",
                     kb([home_btn()]))
        return
    disc = active_discount(call.from_user.id)
    rows = []
    for i, p in enumerate(products):
        price, _ = price_for(call.from_user.id, p["price"])
        rows.append([B(f"{p['name']}  |  {fmt(price)} تومان", f"prod:{p['id']}", ALT[i % 2])])
    rows.append(home_btn())
    note = f"\n\n🎉 کد تخفیف {disc['percent']}% روی قیمت‌ها اعمال شده." if disc else ""
    await render(call, f"{icon} <b>{title}</b>\n\nیکی از گزینه‌ها را انتخاب کنید:{note}", kb(rows))


def get_product(pid):
    return db.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()


@user_router.callback_query(F.data.startswith("prod:"))
async def cb_product(call: CallbackQuery, state: FSMContext):
    await state.clear()
    p = get_product(int(call.data.split(":", 1)[1]))
    if not p or not p["active"] or (not cat_visible(p["category"]) and not is_admin(call.from_user.id)):
        await call.answer("❌ این محصول در دسترس نیست.", show_alert=True)
        return
    await call.answer()
    price, disc = price_for(call.from_user.id, p["price"])
    price_line = (
        f"💰 قیمت: <s>{fmt(marked_up(p['price']))}</s> <b>{fmt(price)}</b> تومان (🎉 {disc['percent']}% تخفیف)"
        if disc else f"💰 قیمت: <b>{fmt(price)}</b> تومان"
    )
    delivery = "👨‍💻 تحویل: توسط ادمین (معمولاً چند دقیقه تا چند ساعت)"
    desc = f"\n📝 {esc(p['description'])}\n" if p["description"] else ""
    qty_line = ""
    if p["category"] == "boost" and p["qty"]:
        qty_line = f"\n🚀 مدت: <b>{p['qty']} روز</b>"
    elif p["category"] == "gift" and p["qty"]:
        qty_line = f"\n⭐ ارزش تقریبی: <b>{fmt(p['qty'])}</b> استارز"
    text = (
        f"{CATEGORIES[p['category']][0]} <b>{esc(p['name'])}</b>\n{desc}{qty_line}\n\n"
        f"{price_line}\n{delivery}\n\n"
    )
    if p["category"] == "boost":
        text += "ℹ️ بوست روی کانال یا گروه شما اعمال می‌شود.\n\nبرای ادامه لینک/یوزرنیم کانال را بفرستید."
        rows = [
            [B("🚀 ثبت سفارش", f"buy:{p['id']}:chan", SUCCESS)],
            [B("🔙 بازگشت", f"cat:{p['category']}")],
        ]
    else:
        text += "این سرویس را برای چه کسی می‌خواهید؟"
        rows = [
            [B("👤 برای خودم", f"buy:{p['id']}:self", SUCCESS)],
            [B("🎁 برای شخص دیگر", f"buy:{p['id']}:other", PRIMARY)],
            [B("🔙 بازگشت", f"cat:{p['category']}")],
        ]
    await render(call, text, kb(rows))


def parse_target(text):
    t = (text or "").strip()
    num = to_int(t)
    if num:
        return num, None
    m = re.search(r"(?:t\.me/|@)?([A-Za-z][A-Za-z0-9_]{3,31})$", t)
    if m:
        return None, m.group(1)
    return None, None


def parse_channel(text):
    t = (text or "").strip()
    if not t or len(t) > 200:
        return None
    m = re.fullmatch(r"@?([A-Za-z][A-Za-z0-9_]{4,31})", t)
    if m:
        return f"https://t.me/{m.group(1)}"
    m = re.fullmatch(r"(?:https?://)?(?:t\.me|telegram\.me)/(\+[\w-]{8,}|joinchat/[\w-]{8,})", t)
    if m:
        return f"https://t.me/{m.group(1)}"
    m = re.fullmatch(r"(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z][A-Za-z0-9_]{4,31})(?:/\d+)?/?", t)
    if m:
        return f"https://t.me/{m.group(1)}"
    return None


async def show_confirm(event, state, user_id):
    data = await state.get_data()
    p = get_product(data.get("pid", 0))
    if not p:
        await render(event, "❌ سفارش منقضی شده. دوباره شروع کنید.", kb([home_btn()]))
        return
    price, disc = price_for(user_id, p["price"])
    balance = get_balance(user_id)
    tid, tuser = data.get("tid"), data.get("tuser")
    target = []
    if p["category"] == "boost":
        target.append(esc(tuser))
    else:
        if tid:
            target.append(f"<code>{tid}</code>")
        if tuser:
            target.append(f"@{esc(tuser)}")
    label = "📣 کانال/گروه" if p["category"] == "boost" else "🎯 گیرنده"
    text = (
        "🧾 <b>تایید نهایی سفارش</b>\n\n"
        f"{CATEGORIES[p['category']][0]} سرویس: <b>{esc(p['name'])}</b>\n"
        f"{label}: {' / '.join(target) or '—'}\n"
        f"💰 مبلغ: <b>{fmt(price)}</b> تومان"
        + (f" (🎉 {disc['percent']}% تخفیف)" if disc else "")
        + f"\n💳 موجودی کیف پول: <b>{fmt(balance)}</b> تومان"
        + "\n\nبا تایید، مبلغ از کیف پول شما کسر می‌شود."
    )
    rows = []
    if balance < price:
        rows.append([B(f"💳 شارژ کیف پول (کمبود {fmt(price - balance)} تومان)", "wallet", PRIMARY)])
    rows.append([B("✅ تایید و پرداخت", "confirm", SUCCESS), B("❌ انصراف", "home", DANGER)])
    await render(event, text, kb(rows))


@user_router.callback_query(F.data.startswith("buy:"))
async def cb_buy(call: CallbackQuery, state: FSMContext):
    _, pid, who = call.data.split(":")
    p = get_product(int(pid))
    if not p or not p["active"]:
        await call.answer("❌ این محصول در دسترس نیست.", show_alert=True)
        return
    await call.answer()
    await state.clear()
    await state.update_data(pid=p["id"])
    if who == "self":
        await state.update_data(tid=call.from_user.id, tuser=call.from_user.username)
        await show_confirm(call, state, call.from_user.id)
        return
    if who == "chan":
        await state.set_state(BuyFlow.channel)
        await render(
            call,
            "📣 <b>کانال یا گروه را مشخص کنید</b>\n\n"
            "یوزرنیم (مثل <code>@mychannel</code>) یا لینک کانال/گروه را بفرستید.\n"
            "برای کانال خصوصی، لینک دعوت (<code>t.me/+...</code>) بفرستید.\n\n"
            "⚠️ لطفاً دقیق بفرستید؛ اشتباه در آدرس مسئولیتش با خود شماست.",
            kb([[B("❌ انصراف", f"prod:{p['id']}", DANGER)]]),
        )
        return
    await state.set_state(BuyFlow.target)
    await render(
        call,
        "🎁 <b>گیرنده را مشخص کنید</b>\n\n"
        "آیدی عددی یا یوزرنیم (مثل <code>@username</code>) گیرنده را بفرستید.",
        kb([[B("❌ انصراف", f"prod:{p['id']}", DANGER)]]),
    )


@user_router.message(BuyFlow.target)
async def msg_buy_target(message: Message, state: FSMContext):
    tid, tuser = parse_target(message.text)
    if not tid and not tuser:
        await message.answer("❌ ورودی نامعتبره. آیدی عددی یا @username بفرستید.")
        return
    await state.update_data(tid=tid, tuser=tuser)
    await state.set_state(None)
    await show_confirm(message, state, message.from_user.id)


@user_router.message(BuyFlow.channel)
async def msg_buy_channel(message: Message, state: FSMContext):
    link = parse_channel(message.text)
    if not link:
        await message.answer("❌ ورودی نامعتبره. یوزرنیم (@name) یا لینک t.me کانال/گروه را بفرستید.")
        return
    await state.update_data(tid=None, tuser=link)
    await state.set_state(None)
    await show_confirm(message, state, message.from_user.id)


_user_locks = {}


def user_lock(uid):
    lk = _user_locks.get(uid)
    if lk is None:
        lk = _user_locks[uid] = asyncio.Lock()
    return lk


@user_router.callback_query(F.data == "confirm")
async def cb_confirm(call: CallbackQuery, state: FSMContext, bot: Bot):
    uid = call.from_user.id
    async with user_lock(uid):
        data = await state.get_data()
        p = get_product(data.get("pid", 0))
        if not p or not p["active"] or (not cat_visible(p["category"]) and not is_admin(uid)):
            await call.answer("❌ سفارش منقضی شده. دوباره شروع کنید.", show_alert=True)
            return
        if not flag("shop_open") and not is_admin(uid):
            await call.answer("🔒 فروشگاه موقتاً بسته است.", show_alert=True)
            return
        await state.clear()
        status, order_id, price = place_order(uid, p, data.get("tid"), data.get("tuser"))
        if status == "nofunds":
            await state.set_data(data)
            msg = (f"❌ موجودی کافی نیست!\nموجودی: {fmt(get_balance(uid))} تومان\n"
                   f"قیمت: {fmt(price)} تومان")
            await call.answer(msg, show_alert=True)
            return

    await call.answer("✅ سفارش ثبت شد.")
    order = get_order(order_id)
    await render(
        call,
        "✅ <b>سفارش شما ثبت شد!</b>\n\n" + order_text(order) +
        "\n\n⏳ به‌محض تحویل، پیام اطلاع‌رسانی برای شما ارسال می‌شود.",
        kb([[B("📦 سفارش‌های من", "orders")], home_btn()]),
    )
    await notify_admins(
        bot, "🛒 <b>سفارش جدید (نیازمند تحویل دستی)</b>\n\n" + order_text(order, True),
        order_admin_kb(order),
    )


def order_admin_kb(o):
    rows = []
    if o["status"] == "pending":
        rows.append([B("✅ تحویل شد", f"adm_odone:{o['id']}", SUCCESS),
                     B("❌ رد و بازگشت وجه", f"adm_orej:{o['id']}", DANGER)])
    rows.append([B("🔙 سفارش‌ها", "adm_ord:0")])
    return kb(rows)


# =========================================================
# کیف پول + شارژ کارت + TON
# =========================================================

@user_router.callback_query(F.data == "wallet")
async def cb_wallet(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    bal = get_balance(call.from_user.id)
    text = (
        f"💰 <b>کیف پول شما</b>\n\n"
        f"موجودی: <b>{fmt(bal)}</b> تومان\n\n"
        "برای شارژ یکی از روش‌های زیر را انتخاب کنید:"
    )
    rows = [
        [B("💳 کارت به کارت (رسید)", "charge_card", SUCCESS)],
    ]
    if flag("crypto_enabled") and get_setting("ton_address"):
        rows.append([B("💎 شارژ با TON", "charge_ton", PRIMARY)])
    rows.append(home_btn())
    await render(call, text, kb(rows))


@user_router.callback_query(F.data == "charge_card")
async def cb_charge_card(call: CallbackQuery, state: FSMContext):
    await call.answer()
    min_c = to_int(get_setting("min_charge")) or 50000
    await state.set_state(ChargeFlow.amount)
    await render(
        call,
        f"💳 <b>شارژ با کارت به کارت</b>\n\n"
        f"حداقل مبلغ: <b>{fmt(min_c)}</b> تومان\n\n"
        "مبلغ مورد نظر را به تومان بفرستید:",
        kb([[B("❌ انصراف", "wallet", DANGER)]]),
    )


@user_router.message(ChargeFlow.amount)
async def msg_charge_amount(message: Message, state: FSMContext):
    amount = to_int(message.text)
    min_c = to_int(get_setting("min_charge")) or 50000
    if not amount or amount < min_c:
        await message.answer(f"❌ مبلغ باید حداقل {fmt(min_c)} تومان باشد.")
        return
    await state.update_data(amount=amount)
    await state.set_state(ChargeFlow.photo)
    card = get_setting("card_number")
    holder = get_setting("card_holder")
    await message.answer(
        f"💳 مبلغ <b>{fmt(amount)}</b> تومان را به کارت زیر واریز کنید:\n\n"
        f"<code>{esc(card)}</code>\n"
        f"👤 {esc(holder)}\n\n"
        "بعد از واریز، <b>عکس رسید</b> را همینجا بفرستید.",
        reply_markup=kb([[B("❌ انصراف", "wallet", DANGER)]]),
    )


@user_router.message(ChargeFlow.photo, F.photo)
async def msg_charge_photo(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    amount = data.get("amount")
    if not amount:
        await state.clear()
        await message.answer("❌ سفارش منقضی شده.", reply_markup=home_kb(message.from_user.id))
        return
    file_id = message.photo[-1].file_id
    cur = db.execute(
        "INSERT INTO receipts (user_id, amount, file_id, created_at) VALUES (?,?,?,?)",
        (message.from_user.id, amount, file_id, now()),
    )
    rid = cur.lastrowid
    db.commit()
    await state.clear()
    await message.answer(
        f"✅ رسید شما ثبت شد (#{rid}).\n"
        "بعد از بررسی توسط ادمین، کیف پول شارژ می‌شود.",
        reply_markup=home_kb(message.from_user.id),
    )
    caption = (
        f"🧾 <b>رسید جدید #{rid}</b>\n\n"
        f"👤 {user_label(user_row(message.from_user.id))}\n"
        f"💰 مبلغ: <b>{fmt(amount)}</b> تومان\n"
        f"🕐 {now()}"
    )
    markup = kb([
        [B("✅ تایید و شارژ", f"adm_rok:{rid}", SUCCESS),
         B("❌ رد", f"adm_rno:{rid}", DANGER)],
    ])
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_photo(admin_id, file_id, caption=caption, reply_markup=markup)
        except Exception as e:
            logging.warning("send receipt to admin failed: %s", e)


@user_router.message(ChargeFlow.photo)
async def msg_charge_photo_invalid(message: Message):
    await message.answer("❌ لطفاً فقط عکس رسید را بفرستید.")


# ---------------- TON ----------------

_ton_rate_cache = {"value": 0, "ts": 0.0}


async def fetch_ton_rate():
    manual = to_int(get_setting("ton_rate")) or 0
    if manual > 0:
        return manual
    if _ton_rate_cache["value"] and time.time() - _ton_rate_cache["ts"] < 60:
        return _ton_rate_cache["value"]
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get("https://api.nobitex.ir/market/stats",
                                params={"srcCurrency": "ton", "dstCurrency": "rls"}) as resp:
                data = await resp.json(content_type=None)
            rate = int(float(data["stats"]["ton-rls"]["latest"]) // 10)  # ریال → تومان
            if rate > 1000:
                _ton_rate_cache.update(value=rate, ts=time.time())
                return rate
    except Exception as e:
        logging.warning("ton rate failed: %s", e)
    return _ton_rate_cache["value"]


@user_router.callback_query(F.data == "charge_ton")
async def cb_charge_ton(call: CallbackQuery, state: FSMContext):
    if not flag("crypto_enabled") or not get_setting("ton_address"):
        await call.answer("❌ شارژ TON فعلاً فعال نیست.", show_alert=True)
        return
    await call.answer()
    rate = await fetch_ton_rate()
    if not rate:
        await render(call, "❌ نرخ TON در دسترس نیست. کمی بعد دوباره امتحان کنید.",
                     kb([[B("🔙 بازگشت", "wallet")]]))
        return
    comment = secrets.token_hex(4)
    created = int(time.time())
    expires = created + INVOICE_TTL
    cur = db.execute(
        "INSERT INTO crypto_invoices (user_id, comment, created_ts, expires_ts, chat_id) "
        "VALUES (?,?,?,?,?)",
        (call.from_user.id, comment, created, expires, call.message.chat.id),
    )
    inv_id = cur.lastrowid
    db.commit()
    addr = get_setting("ton_address")
    text = (
        f"💎 <b>شارژ با TON</b>\n\n"
        f"آدرس ولت:\n<code>{esc(addr)}</code>\n\n"
        f"💬 کامنت اجباری (حتماً بنویس):\n<code>{comment}</code>\n\n"
        f"📊 نرخ تقریبی: ۱ TON ≈ <b>{fmt(rate)}</b> تومان\n"
        f"⏱ اعتبار فاکتور: {INVOICE_TTL // 60} دقیقه\n\n"
        "⚠️ مبلغ را همراه با کامنت بالا بفرستید. بعد از تأیید شبکه، خودکار شارژ می‌شود."
    )
    await render(call, text, kb([
        [B("🔄 بررسی وضعیت", f"ton_check:{inv_id}", SUCCESS)],
        [B("❌ انصراف", "wallet", DANGER)],
    ]))


@user_router.callback_query(F.data.startswith("ton_check:"))
async def cb_ton_check(call: CallbackQuery):
    inv_id = int(call.data.split(":")[1])
    inv = db.execute("SELECT * FROM crypto_invoices WHERE id=?", (inv_id,)).fetchone()
    if not inv or inv["user_id"] != call.from_user.id:
        await call.answer("❌ فاکتور پیدا نشد.", show_alert=True)
        return
    if inv["status"] == "paid":
        await call.answer("✅ قبلاً پرداخت و شارژ شده.", show_alert=True)
        return
    if inv["status"] != "pending":
        await call.answer("این فاکتور منقضی یا لغو شده.", show_alert=True)
        return
    await call.answer("در حال بررسی بلاکچین... کمی صبر کنید.")


# TON watcher (ساده)
async def crypto_watcher(bot):
    await asyncio.sleep(10)
    while True:
        try:
            await _check_pending_invoices(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("crypto watcher failed")
        await asyncio.sleep(WATCH_INTERVAL)


async def _check_pending_invoices(bot):
    now_ts = int(time.time())
    invs = db.execute(
        "SELECT * FROM crypto_invoices WHERE status='pending' AND expires_ts + ? > ?",
        (INVOICE_GRACE, now_ts),
    ).fetchall()
    if not invs:
        return
    addr = get_setting("ton_address")
    if not addr:
        return
    rate = await fetch_ton_rate()
    if not rate:
        return
    # در نسخهٔ کامل می‌توان از toncenter API استفاده کرد.
    # اینجا فقط اسکلت نگه داشته شده تا بعداً با کلید API کامل شود.
    for inv in invs:
        if inv["expires_ts"] < now_ts and inv["status"] == "pending":
            db.execute("UPDATE crypto_invoices SET status='expired' WHERE id=?", (inv["id"],))
            db.commit()


# =========================================================
# سفارش‌های من ، کد تخفیف ، رفرال ، پشتیبانی
# =========================================================

@user_router.callback_query(F.data == "orders")
async def cb_orders(call: CallbackQuery):
    await call.answer()
    items = db.execute(
        "SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 20",
        (call.from_user.id,),
    ).fetchall()
    if not items:
        await render(call, "📦 هنوز سفارشی ثبت نکرده‌اید.", kb([home_btn()]))
        return
    rows = [[B(f"{STATUS_ICON[o['status']]} #{o['id']} | {o['product_name']}",
               f"myo:{o['id']}")] for o in items]
    rows.append(home_btn())
    await render(call, "📦 <b>سفارش‌های اخیر شما</b>", kb(rows))


@user_router.callback_query(F.data.startswith("myo:"))
async def cb_my_order(call: CallbackQuery):
    o = get_order(int(call.data.split(":")[1]))
    if not o or o["user_id"] != call.from_user.id:
        await call.answer("❌ پیدا نشد.", show_alert=True)
        return
    await call.answer()
    await render(call, order_text(o), kb([[B("🔙 سفارش‌ها", "orders")], home_btn()]))


@user_router.callback_query(F.data == "discount")
async def cb_discount(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(RedeemFlow.code)
    await render(call, "🎟 کد تخفیف را بفرستید:",
                 kb([[B("❌ انصراف", "home", DANGER)]]))


@user_router.message(RedeemFlow.code)
async def msg_redeem(message: Message, state: FSMContext):
    code = (message.text or "").strip().upper()
    row = db.execute("SELECT * FROM discounts WHERE code=? AND active=1", (code,)).fetchone()
    if not row:
        await message.answer("❌ کد معتبر نیست.")
        return
    if row["expires_at"] and row["expires_at"] < now():
        await message.answer("❌ این کد منقضی شده.")
        return
    if row["usage_limit"]:
        used = db.execute("SELECT COUNT(*) FROM discount_uses WHERE discount_id=?",
                          (row["id"],)).fetchone()[0]
        if used >= row["usage_limit"]:
            await message.answer("❌ ظرفیت استفاده از این کد پر شده.")
            return
    db.execute("UPDATE users SET discount_id=? WHERE user_id=?", (row["id"], message.from_user.id))
    db.commit()
    await state.clear()
    await message.answer(
        f"✅ کد <b>{esc(code)}</b> با <b>{row['percent']}%</b> تخفیف فعال شد.\n"
        "روی خرید بعدی اعمال می‌شود.",
        reply_markup=home_kb(message.from_user.id),
    )


@user_router.callback_query(F.data == "referral")
async def cb_referral(call: CallbackQuery, bot: Bot):
    await call.answer()
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref{call.from_user.id}"
    reward = to_int(get_setting("referral_reward")) or 0
    count = db.execute("SELECT COUNT(*) FROM users WHERE referred_by=?",
                       (call.from_user.id,)).fetchone()[0]
    text = (
        f"🎯 <b>دعوت دوستان</b>\n\n"
        f"لینک اختصاصی شما:\n<code>{link}</code>\n\n"
        f"با هر دعوت موفق <b>{fmt(reward)}</b> تومان جایزه می‌گیرید.\n"
        f"تعداد دعوت‌های شما: <b>{fmt(count)}</b>"
    )
    await render(call, text, kb([home_btn()]))


@user_router.callback_query(F.data == "support")
async def cb_support(call: CallbackQuery, state: FSMContext):
    await call.answer()
    uname = get_setting("support_username")
    if uname:
        await render(call, f"👨‍💻 پشتیبانی: @{esc(uname)}",
                     kb([[B("ارسال پیام به پشتیبانی", url=f"https://t.me/{uname}")], home_btn()]))
        return
    await state.set_state(SupportFlow.message)
    await render(call, "👨‍💻 پیام خود را برای پشتیبانی بفرستید:",
                 kb([[B("❌ انصراف", "home", DANGER)]]))


@user_router.message(SupportFlow.message)
async def msg_support(message: Message, state: FSMContext, bot: Bot):
    text = (message.text or "").strip()
    if not text:
        await message.answer("❌ متن پیام را بفرستید.")
        return
    db.execute("INSERT INTO tickets (user_id, text, created_at) VALUES (?,?,?)",
               (message.from_user.id, text, now()))
    db.commit()
    await state.clear()
    await message.answer("✅ پیام شما ثبت شد. به‌زودی پاسخ می‌دهیم.",
                         reply_markup=home_kb(message.from_user.id))
    await notify_admins(
        bot,
        f"🎫 <b>تیکت جدید</b>\n\n👤 {user_label(user_row(message.from_user.id))}\n\n{esc(text)}",
    )


# =========================================================
# پنل مدیریت
# =========================================================

def pending_counts():
    orders = db.execute("SELECT COUNT(*) FROM orders WHERE status='pending'").fetchone()[0]
    receipts = db.execute("SELECT COUNT(*) FROM receipts WHERE status='pending'").fetchone()[0]
    tickets = db.execute("SELECT COUNT(*) FROM tickets WHERE status='open'").fetchone()[0]
    return orders, receipts, tickets


@admin_router.callback_query(F.data == "adm")
async def cb_adm(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    o, r, t = pending_counts()
    text = (
        "👑 <b>پنل مدیریت</b>\n\n"
        f"🛒 سفارش در انتظار: <b>{o}</b>\n"
        f"🧾 رسید در انتظار: <b>{r}</b>\n"
        f"🎫 تیکت باز: <b>{t}</b>"
    )
    rows = [
        [B(f"🛒 سفارش‌ها ({o})", "adm_ord:0", SUCCESS), B(f"🧾 رسیدها ({r})", "adm_rcp:0", PRIMARY)],
        [B("👥 کاربران", "adm_users:0"), B("📦 محصولات", "adm_prod:0")],
        [B("💲 قیمت‌ها", "adm_pr:0"), B("🎟 کدهای تخفیف", "adm_disc:0")],
        [B("✏️ تغییر اسم دکمه‌ها", "adm_bl:u:0"), B("🔘 دکمه‌های فروشگاه", "adm_shopbtn")],
        [B("🔗 جوین اجباری", "adm_fj"), B("📢 پیام همگانی", "adm_bc")],
        [B("⚙️ تنظیمات", "adm_set"), B("➕ شارژ کاربر", "adm_byid:charge")],
        [B("➖ کسر حساب", "adm_byid:deduct"), B("✉️ پیام به کاربر", "adm_byid:msg")],
        [B("🏠 منوی اصلی", "home")],
    ]
    await render(call, text, kb(rows))


# ---------------- سفارش‌ها ----------------

@admin_router.callback_query(F.data.startswith("adm_ord:"))
async def cb_adm_orders(call: CallbackQuery):
    await call.answer()
    items = db.execute("SELECT * FROM orders WHERE status='pending' ORDER BY id").fetchall()
    if not items:
        await render(call, "🛒 <b>سفارش‌ها</b>\n\n✅ سفارش در انتظاری نداریم.",
                     kb([[B("📜 همه‌ی سفارش‌ها", "adm_ordall:0")], [B("🔙 پنل مدیریت", "adm")]]))
        return
    chunk, page, total = paginate(items, int(call.data.split(":")[1]), 8)
    rows = [[B(f"⏳ #{o['id']} | {o['product_name']} | {fmt(o['price'])}",
               f"adm_o:{o['id']}", SUCCESS)] for o in chunk]
    nav = nav_row("adm_ord", page, total)
    if nav:
        rows.append(nav)
    rows.append([B("📜 همه‌ی سفارش‌ها", "adm_ordall:0")])
    rows.append([B("🔙 پنل مدیریت", "adm")])
    await render(call, f"🛒 <b>سفارش‌های در انتظار ({len(items)})</b>", kb(rows))


@admin_router.callback_query(F.data.startswith("adm_ordall:"))
async def cb_adm_orders_all(call: CallbackQuery):
    await call.answer()
    items = db.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
    chunk, page, total = paginate(items, int(call.data.split(":")[1]), 8)
    rows = [[B(f"{STATUS_ICON[o['status']]} #{o['id']} | {o['product_name']} | {fmt(o['price'])}",
               f"adm_o:{o['id']}")] for o in chunk]
    nav = nav_row("adm_ordall", page, total)
    if nav:
        rows.append(nav)
    rows.append([B("🔙 سفارش‌ها", "adm_ord:0")])
    await render(call, "📜 <b>همه‌ی سفارش‌ها</b>", kb(rows))


@admin_router.callback_query(F.data.startswith("adm_o:"))
async def cb_adm_order(call: CallbackQuery):
    o = get_order(int(call.data.split(":")[1]))
    if not o:
        await call.answer("❌ پیدا نشد.", show_alert=True)
        return
    await call.answer()
    await render(call, order_text(o, True), order_admin_kb(o))


@admin_router.callback_query(F.data.startswith("adm_odone:"))
async def cb_adm_odone(call: CallbackQuery, bot: Bot):
    oid = int(call.data.split(":")[1])
    if not finish_order(oid):
        await call.answer("⚠️ این سفارش قبلاً رسیدگی شده.", show_alert=True)
        return
    o = get_order(oid)
    await call.answer("✅ تحویل ثبت شد.")
    await safe_send(bot, o["user_id"],
                    f"✅ <b>سفارش #{oid} تحویل داده شد!</b>\n\n"
                    f"📦 {esc(o['product_name'])}\n\nممنون از خرید شما 🌹",
                    kb([home_btn()]))
    await render(call, order_text(o, True), order_admin_kb(o))


@admin_router.callback_query(F.data.startswith("adm_orej:"))
async def cb_adm_orej(call: CallbackQuery, bot: Bot):
    oid = int(call.data.split(":")[1])
    if not reject_order(oid):
        await call.answer("⚠️ این سفارش قبلاً رسیدگی شده.", show_alert=True)
        return
    o = get_order(oid)
    await call.answer("❌ سفارش رد و وجه برگشت داده شد.")
    await safe_send(bot, o["user_id"],
                    f"❌ <b>سفارش #{oid} لغو شد</b>\n\n"
                    f"💰 مبلغ {fmt(o['price'])} تومان به کیف پول شما برگشت داده شد.",
                    kb([home_btn()]))
    await render(call, order_text(o, True), order_admin_kb(o))


# ---------------- رسیدها ----------------

@admin_router.callback_query(F.data.startswith("adm_rcp:"))
async def cb_adm_receipts(call: CallbackQuery):
    await call.answer()
    items = db.execute("SELECT * FROM receipts WHERE status='pending' ORDER BY id").fetchall()
    if not items:
        await render(call, "🧾 <b>رسیدها</b>\n\n✅ رسید در انتظاری نداریم.", adm_back())
        return
    chunk, page, total = paginate(items, int(call.data.split(":")[1]), 8)
    rows = [[B(f"🧾 #{r['id']} | {fmt(r['amount'])} تومان | {r['user_id']}",
               f"adm_rv:{r['id']}", SUCCESS)] for r in chunk]
    nav = nav_row("adm_rcp", page, total)
    if nav:
        rows.append(nav)
    rows.append([B("🔙 پنل مدیریت", "adm")])
    await render(call, f"🧾 <b>رسیدهای در انتظار ({len(items)})</b>", kb(rows))


@admin_router.callback_query(F.data.startswith("adm_rv:"))
async def cb_adm_receipt_view(call: CallbackQuery):
    r = db.execute("SELECT * FROM receipts WHERE id=?", (int(call.data.split(":")[1]),)).fetchone()
    if not r:
        await call.answer("❌ پیدا نشد.", show_alert=True)
        return
    await call.answer()
    caption = (f"🧾 <b>رسید #{r['id']}</b>\n\n👤 {user_label(user_row(r['user_id']))}\n"
               f"💰 مبلغ اعلامی: <b>{fmt(r['amount'])}</b> تومان\n🕐 {r['created_at']}")
    markup = kb([[B("✅ تایید و شارژ", f"adm_rok:{r['id']}", SUCCESS),
                  B("❌ رد", f"adm_rno:{r['id']}", DANGER)]]) if r["status"] == "pending" else None
    await call.message.answer_photo(r["file_id"], caption=caption, reply_markup=markup)


@admin_router.callback_query(F.data.startswith("adm_rok:"))
async def cb_adm_receipt_ok(call: CallbackQuery, bot: Bot):
    rid = int(call.data.split(":")[1])
    r = db.execute("SELECT * FROM receipts WHERE id=?", (rid,)).fetchone()
    if not r:
        await call.answer("❌ پیدا نشد.", show_alert=True)
        return
    cur = db.cursor()
    try:
        cur.execute("UPDATE receipts SET status='approved' WHERE id=? AND status='pending'", (rid,))
        if cur.rowcount == 0:
            db.rollback()
            await call.answer("⚠️ این رسید قبلاً بررسی شده.", show_alert=True)
            return
        cur.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (r["amount"], r["user_id"]))
        cur.execute(
            "INSERT INTO transactions (user_id, amount, reason, created_at) VALUES (?,?,?,?)",
            (r["user_id"], r["amount"], f"شارژ کیف پول (رسید #{rid})", now()),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    await call.answer("✅ کیف پول شارژ شد.")
    try:
        await call.message.edit_caption(
            caption=(call.message.caption or "") + f"\n\n✅ تایید و شارژ شد ({fmt(r['amount'])} تومان)",
            reply_markup=None,
        )
    except Exception:
        pass
    await safe_send(bot, r["user_id"],
                    f"✅ <b>کیف پول شما شارژ شد!</b>\n\n💰 مبلغ: {fmt(r['amount'])} تومان\n"
                    f"💵 موجودی فعلی: {fmt(get_balance(r['user_id']))} تومان",
                    kb([home_btn()]))


@admin_router.callback_query(F.data.startswith("adm_rno:"))
async def cb_adm_receipt_no(call: CallbackQuery, bot: Bot):
    rid = int(call.data.split(":")[1])
    cur = db.execute("UPDATE receipts SET status='rejected' WHERE id=? AND status='pending'", (rid,))
    db.commit()
    if cur.rowcount == 0:
        await call.answer("⚠️ این رسید قبلاً بررسی شده.", show_alert=True)
        return
    r = db.execute("SELECT * FROM receipts WHERE id=?", (rid,)).fetchone()
    await call.answer("❌ رد شد.")
    try:
        await call.message.edit_caption(caption=(call.message.caption or "") + "\n\n❌ رد شد",
                                        reply_markup=None)
    except Exception:
        pass
    if r:
        await safe_send(bot, r["user_id"],
                        f"❌ رسید #{rid} شما تایید نشد.\nدر صورت اشتباه با پشتیبانی تماس بگیرید.",
                        kb([home_btn()]))


# ---------------- کاربران ----------------

@admin_router.callback_query(F.data.startswith("adm_users:"))
async def cb_adm_users(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    items = db.execute("SELECT * FROM users ORDER BY joined_at DESC").fetchall()
    chunk, page, total = paginate(items, int(call.data.split(":")[1]), 8)
    rows = [[B(f"{'🚫' if u['banned'] else '👤'} {u['first_name'] or u['user_id']} | {fmt(u['balance'])}",
               f"adm_u:{u['user_id']}")] for u in chunk]
    nav = nav_row("adm_users", page, total)
    if nav:
        rows.append(nav)
    rows.append([B("🔍 جستجوی کاربر", "adm_usearch", PRIMARY)])
    rows.append([B("🔙 پنل مدیریت", "adm")])
    await render(call, f"👥 <b>کاربران ({len(items)})</b>", kb(rows))


@admin_router.callback_query(F.data == "adm_usearch")
async def cb_adm_usearch(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(AdminSearch.query)
    await render(call, "🔍 آیدی عددی یا یوزرنیم کاربر را بفرستید:", cancel_adm("adm_users:0"))


@admin_router.message(AdminSearch.query)
async def msg_adm_search(message: Message, state: FSMContext):
    t = (message.text or "").strip()
    num = to_int(t)
    if num:
        row = user_row(num)
    else:
        row = db.execute("SELECT * FROM users WHERE LOWER(username)=?",
                         (t.lstrip("@").lower(),)).fetchone()
    if not row:
        await message.answer("❌ کاربری پیدا نشد. دوباره امتحان کنید یا /cancel بزنید.")
        return
    await state.clear()
    await message.answer(*user_card(row["user_id"]))


def user_card(uid):
    u = user_row(uid)
    orders = db.execute(
        "SELECT COUNT(*), COALESCE(SUM(price),0) FROM orders WHERE user_id=? AND status='done'",
        (uid,),
    ).fetchone()
    text = (
        f"👤 <b>اطلاعات کاربر</b>\n\n{user_label(u)}\n"
        f"💰 موجودی: <b>{fmt(u['balance'])}</b> تومان\n"
        f"🛒 خریدهای موفق: {orders[0]} (جمع {fmt(orders[1])} تومان)\n"
        f"📅 عضویت: {u['joined_at']}\n"
        f"وضعیت: {'🚫 بن‌شده' if u['banned'] else '✅ عادی'}"
    )
    markup = kb([
        [B("➕ شارژ", f"adm_ucharge:{uid}", SUCCESS), B("➖ کسر", f"adm_udeduct:{uid}", DANGER)],
        [B("✅ رفع بن" if u["banned"] else "🚫 بن کردن", f"adm_uban:{uid}",
           SUCCESS if u["banned"] else DANGER),
         B("📨 ارسال پیام", f"adm_umsg:{uid}", PRIMARY)],
        [B("🔙 کاربران", "adm_users:0")],
    ])
    return text, markup


@admin_router.callback_query(F.data.startswith("adm_u:"))
async def cb_adm_user(call: CallbackQuery, state: FSMContext):
    await state.clear()
    uid = int(call.data.split(":")[1])
    if not user_row(uid):
        await call.answer("❌ پیدا نشد.", show_alert=True)
        return
    await call.answer()
    await render(call, *user_card(uid))


@admin_router.callback_query(F.data.startswith("adm_uban:"))
async def cb_adm_uban(call: CallbackQuery, bot: Bot):
    uid = int(call.data.split(":")[1])
    if is_admin(uid):
        await call.answer("⛔ ادمین رو نمی‌شه بن کرد.", show_alert=True)
        return
    db.execute("UPDATE users SET banned = 1 - banned WHERE user_id=?", (uid,))
    db.commit()
    u = user_row(uid)
    await call.answer("🚫 بن شد." if u["banned"] else "✅ رفع بن شد.")
    if not u["banned"]:
        await safe_send(bot, uid, "✅ دسترسی شما به ربات دوباره فعال شد.")
    await render(call, *user_card(uid))


@admin_router.callback_query(F.data.startswith("adm_ucharge:") | F.data.startswith("adm_udeduct:"))
async def cb_adm_money(call: CallbackQuery, state: FSMContext):
    mode, uid = call.data.split(":")
    await call.answer()
    await state.set_state(AdminMoney.amount)
    await state.update_data(uid=int(uid), mode="charge" if mode == "adm_ucharge" else "deduct")
    await render(call, "💰 مبلغ را به تومان بفرستید:", cancel_adm(f"adm_u:{uid}"))


@admin_router.message(AdminMoney.amount)
async def msg_adm_money(message: Message, state: FSMContext, bot: Bot):
    amount = to_int(message.text)
    if not amount:
        await message.answer("❌ فقط عدد (بزرگ‌تر از صفر) بفرستید.")
        return
    d = await state.get_data()
    uid = d["uid"]
    if d["mode"] == "deduct":
        if get_balance(uid) < amount:
            await message.answer("❌ موجودی کاربر کمتر از این مبلغه.")
            return
        add_balance(uid, -amount, "کسر توسط مدیریت")
        await safe_send(bot, uid, f"➖ مبلغ {fmt(amount)} تومان توسط مدیریت از کیف پول شما کسر شد.")
    else:
        add_balance(uid, amount, "شارژ توسط مدیریت")
        await safe_send(bot, uid, f"➕ مبلغ {fmt(amount)} تومان توسط مدیریت به کیف پول شما اضافه شد.")
    await state.clear()
    await message.answer("✅ انجام شد.")
    await message.answer(*user_card(uid))


@admin_router.callback_query(F.data.startswith("adm_umsg:"))
async def cb_adm_umsg(call: CallbackQuery, state: FSMContext):
    uid = int(call.data.split(":")[1])
    await call.answer()
    await state.set_state(AdminMsg.text)
    await state.update_data(uid=uid)
    await render(call, "📨 پیام را بفرستید:", cancel_adm(f"adm_u:{uid}"))


@admin_router.message(AdminMsg.text)
async def msg_adm_umsg(message: Message, state: FSMContext, bot: Bot):
    uid = (await state.get_data())["uid"]
    try:
        await bot.copy_message(uid, message.chat.id, message.message_id)
        await message.answer("✅ ارسال شد.")
    except Exception as e:
        await message.answer(f"❌ ارسال نشد: {esc(str(e))[:200]}")
    await state.clear()


# شارژ / کسر / پیام با آیدی
BYID_TITLES = {
    "charge": "➕ <b>شارژ کاربر</b>",
    "deduct": "➖ <b>کسر حساب</b>",
    "msg": "✉️ <b>پیام به کاربر</b>",
}


def find_user(text):
    t = (text or "").strip()
    num = to_int(t)
    if num:
        return user_row(num)
    if t:
        return db.execute("SELECT * FROM users WHERE LOWER(username)=?",
                          (t.lstrip("@").lower(),)).fetchone()
    return None


@admin_router.callback_query(F.data.startswith("adm_byid:"))
async def cb_adm_byid(call: CallbackQuery, state: FSMContext):
    mode = call.data.split(":")[1]
    if mode not in BYID_TITLES:
        await call.answer()
        return
    await call.answer()
    await state.clear()
    await state.set_state(AdminById.uid)
    await state.update_data(mode=mode)
    await render(call, f"{BYID_TITLES[mode]}\n\n🆔 آیدی عددی کاربر را بفرستید:", cancel_adm("adm"))


@admin_router.message(AdminById.uid)
async def msg_adm_byid(message: Message, state: FSMContext):
    row = find_user(message.text)
    if not row:
        await message.answer(
            "❌ کاربری با این آیدی پیدا نشد.\n"
            "کاربر باید حداقل یک‌بار ربات را استارت کرده باشد."
        )
        return
    uid = row["user_id"]
    mode = (await state.get_data())["mode"]
    if mode == "charge":
        await state.set_state(AdminMoney.amount)
        await state.update_data(uid=uid, mode="charge")
        await message.answer(
            f"{user_label(row)}\n💰 موجودی فعلی: <b>{fmt(row['balance'])}</b> تومان\n\n"
            "💵 مبلغ شارژ را به تومان بفرستید:",
            reply_markup=cancel_adm("adm"),
        )
    elif mode == "msg":
        await state.set_state(AdminMsg.text)
        await state.update_data(uid=uid)
        await message.answer(f"{user_label(row)}\n\n📨 پیام را بفرستید:",
                             reply_markup=cancel_adm("adm"))
    else:
        await state.set_state(AdminMoney.amount)
        await state.update_data(uid=uid, mode="deduct")
        await message.answer(
            f"{user_label(row)}\n💰 موجودی فعلی: <b>{fmt(row['balance'])}</b> تومان\n\n"
            "💵 مبلغ کسر را به تومان بفرستید:",
            reply_markup=cancel_adm("adm"),
        )


# ---------------- محصولات ----------------

@admin_router.callback_query(F.data.startswith("adm_prod:"))
async def cb_adm_products(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    items = db.execute("SELECT * FROM products ORDER BY category, price").fetchall()
    chunk, page, total = paginate(items, int(call.data.split(":")[1]), 8)
    rows = [
        [B(f"{'🟢' if p['active'] else '🔴'} {p['name']} | {fmt(p['price'])}",
           f"adm_pp:{p['id']}:{page}")]
        for p in chunk
    ]
    nav = nav_row("adm_prod", page, total)
    if nav:
        rows.append(nav)
    rows.append([B("➕ افزودن محصول", "adm_pnew", SUCCESS)])
    rows.append([B("🔙 پنل مدیریت", "adm")])
    await render(call, "📦 <b>محصولات</b>\n\nروی محصول بزنید تا ویرایش/حذف کنید.", kb(rows))


@admin_router.callback_query(F.data.startswith("adm_pp:"))
async def cb_adm_product_detail(call: CallbackQuery, state: FSMContext):
    await state.clear()
    _, pid, page = call.data.split(":")
    p = get_product(int(pid))
    if not p:
        await call.answer("❌ پیدا نشد.", show_alert=True)
        return
    await call.answer()
    text = (
        f"📦 <b>{esc(p['name'])}</b>\n\n"
        f"دسته: {CATEGORIES.get(p['category'], ('', p['category']))[1]}\n"
        f"قیمت: <b>{fmt(p['price'])}</b> تومان\n"
        f"وضعیت: {'🟢 فعال' if p['active'] else '🔴 غیرفعال'}\n"
        f"توضیحات: {esc(p['description']) or '—'}"
    )
    rows = [
        [B("✏️ تغییر اسم", f"adm_pname:{p['id']}:{page}", PRIMARY),
         B("💲 تغییر قیمت", f"adm_pprice:{p['id']}:{page}", SUCCESS)],
        [B("🟢 فعال" if not p["active"] else "🔴 غیرفعال", f"adm_ptog:{p['id']}:{page}",
           SUCCESS if not p["active"] else DANGER)],
        [B("🗑 حذف", f"adm_pdel:{p['id']}:{page}", DANGER)],
        [B("🔙 محصولات", f"adm_prod:{page}")],
    ]
    await render(call, text, kb(rows))


@admin_router.callback_query(F.data.startswith("adm_ptog:"))
async def cb_adm_ptog(call: CallbackQuery, state: FSMContext):
    _, pid, page = call.data.split(":")
    db.execute("UPDATE products SET active = 1 - active WHERE id=?", (int(pid),))
    db.commit()
    await cb_adm_product_detail(call, state)


@admin_router.callback_query(F.data.startswith("adm_pdel:"))
async def cb_adm_pdel(call: CallbackQuery):
    _, pid, page = call.data.split(":")
    db.execute("DELETE FROM products WHERE id=?", (int(pid),))
    db.commit()
    await call.answer("🗑 حذف شد.")
    await cb_adm_products(call, FSMContext)  # type: ignore


@admin_router.callback_query(F.data.startswith("adm_pname:"))
async def cb_adm_pname(call: CallbackQuery, state: FSMContext):
    _, pid, page = call.data.split(":")
    await call.answer()
    await state.set_state(EditProductName.value)
    await state.update_data(pid=int(pid), page=int(page))
    await render(call, "✏️ اسم جدید محصول را بفرستید:", cancel_adm(f"adm_pp:{pid}:{page}"))


@admin_router.message(EditProductName.value)
async def msg_adm_pname(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name or len(name) > 80:
        await message.answer("❌ اسم باید بین ۱ تا ۸۰ کاراکتر باشد.")
        return
    data = await state.get_data()
    db.execute("UPDATE products SET name=? WHERE id=?", (name, data["pid"]))
    db.commit()
    await state.clear()
    await message.answer("✅ اسم محصول تغییر کرد.")
    p = get_product(data["pid"])
    if p:
        await message.answer(
            f"📦 <b>{esc(p['name'])}</b>\nقیمت: {fmt(p['price'])} تومان",
            reply_markup=adm_back(f"adm_prod:{data.get('page', 0)}", "🔙 محصولات"),
        )


@admin_router.callback_query(F.data.startswith("adm_pprice:"))
async def cb_adm_pprice(call: CallbackQuery, state: FSMContext):
    _, pid, page = call.data.split(":")
    await call.answer()
    await state.set_state(EditPrice.value)
    await state.update_data(pid=int(pid), page=int(page))
    await render(call, "💲 قیمت جدید را به تومان بفرستید:", cancel_adm(f"adm_pp:{pid}:{page}"))


@admin_router.message(EditPrice.value)
async def msg_adm_pprice(message: Message, state: FSMContext):
    price = to_int(message.text)
    if not price or price < 1000:
        await message.answer("❌ قیمت باید حداقل ۱۰۰۰ تومان باشد.")
        return
    data = await state.get_data()
    db.execute("UPDATE products SET price=? WHERE id=?", (price, data["pid"]))
    db.commit()
    await state.clear()
    await message.answer(f"✅ قیمت به {fmt(price)} تومان تغییر کرد.",
                         reply_markup=adm_back(f"adm_prod:{data.get('page', 0)}", "🔙 محصولات"))


@admin_router.callback_query(F.data == "adm_pnew")
async def cb_adm_pnew(call: CallbackQuery, state: FSMContext):
    await call.answer()
    rows = [[B(f"{icon} {title}", f"adm_pnewc:{cat}", PRIMARY)]
            for cat, (icon, title) in CATEGORIES.items()]
    rows.append([B("❌ انصراف", "adm_prod:0", DANGER)])
    await render(call, "➕ دسته محصول جدید را انتخاب کنید:", kb(rows))


@admin_router.callback_query(F.data.startswith("adm_pnewc:"))
async def cb_adm_pnewc(call: CallbackQuery, state: FSMContext):
    cat = call.data.split(":")[1]
    if cat not in CATEGORIES:
        await call.answer()
        return
    await call.answer()
    await state.set_state(AddProduct.name)
    await state.update_data(cat=cat)
    await render(call, "✏️ نام محصول را بفرستید:", cancel_adm("adm_prod:0"))


@admin_router.message(AddProduct.name)
async def msg_add_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name or len(name) > 80:
        await message.answer("❌ نام باید بین ۱ تا ۸۰ کاراکتر باشد.")
        return
    await state.update_data(name=name)
    await state.set_state(AddProduct.price)
    await message.answer("💲 قیمت را به تومان بفرستید:")


@admin_router.message(AddProduct.price)
async def msg_add_price(message: Message, state: FSMContext):
    price = to_int(message.text)
    if not price or price < 1000:
        await message.answer("❌ قیمت باید حداقل ۱۰۰۰ تومان باشد.")
        return
    data = await state.get_data()
    db.execute(
        "INSERT INTO products (category, name, price, active) VALUES (?,?,?,1)",
        (data["cat"], data["name"], price),
    )
    db.commit()
    await state.clear()
    await message.answer(
        f"✅ محصول «{esc(data['name'])}» با قیمت {fmt(price)} تومان اضافه شد.",
        reply_markup=adm_back("adm_prod:0", "🔙 محصولات"),
    )


# ---------------- قیمت‌ها (لیست سریع) ----------------

@admin_router.callback_query(F.data.startswith("adm_pr:"))
async def cb_adm_prices(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    items = db.execute("SELECT * FROM products ORDER BY category, price").fetchall()
    chunk, page, total = paginate(items, int(call.data.split(":")[1]), 8)
    rows = [
        [B(f"{p['name']} | {fmt(p['price'])}", f"adm_prp:{p['id']}:{page}")]
        for p in chunk
    ]
    nav = nav_row("adm_pr", page, total)
    if nav:
        rows.append(nav)
    rows.append([B("🔙 پنل مدیریت", "adm")])
    await render(call, "💲 <b>مدیریت قیمت‌ها</b>\n\nروی محصول بزنید تا قیمتش عوض شود.", kb(rows))


@admin_router.callback_query(F.data.startswith("adm_prp:"))
async def cb_adm_price_edit(call: CallbackQuery, state: FSMContext):
    _, pid, page = call.data.split(":")
    p = get_product(int(pid))
    if not p:
        await call.answer("❌ پیدا نشد.", show_alert=True)
        return
    await call.answer()
    await state.set_state(EditPrice.value)
    await state.update_data(pid=int(pid), page=int(page))
    await render(
        call,
        f"✏️ <b>{esc(p['name'])}</b>\n💰 قیمت فعلی: <b>{fmt(p['price'])}</b> تومان\n\nقیمت جدید را بفرستید:",
        cancel_adm(f"adm_pr:{page}"),
    )


# ---------------- دکمه‌های فروشگاه (نمایش/مخفی) ----------------

SHOP_BUTTONS = {
    "show_stars": "⭐ خرید استارز",
    "show_gift": "🧸 گیفت و تدی",
    "show_boost": "🚀 بوست",
    "show_reaction": "✨ ری‌اکشن پرمیوم",
    "show_number": "📱 شماره مجازی",
    "show_other": "🎁 سایر خدمات",
}


def shopbtn_view():
    rows = []
    for key, label in SHOP_BUTTONS.items():
        on = cat_visible(key[5:])
        rows.append([B(f"{label}: {'🟢 نمایش' if on else '🔴 مخفی'}",
                       f"adm_shopbtn_t:{key}", SUCCESS if on else DANGER)])
    rows.append([B("🔙 پنل مدیریت", "adm")])
    return ("🔘 <b>دکمه‌های فروشگاه</b>\n\n"
            "هر دکمه‌ای که مخفی کنید از منوی کاربران ناپدید می‌شود.", kb(rows))


@admin_router.callback_query(F.data == "adm_shopbtn")
async def cb_adm_shopbtn(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    await render(call, *shopbtn_view())


@admin_router.callback_query(F.data.startswith("adm_shopbtn_t:"))
async def cb_adm_shopbtn_t(call: CallbackQuery):
    key = call.data.split(":", 1)[1]
    if key not in SHOP_BUTTONS:
        await call.answer()
        return
    new = "0" if cat_visible(key[5:]) else "1"
    set_setting(key, new)
    await call.answer("🟢 نمایش" if new == "1" else "🔴 مخفی")
    await render(call, *shopbtn_view())


# ---------------- تغییر اسم دکمه‌ها ----------------

BTN_NAME_MAX = 60
BTN_PER_PAGE = 10

# لیست دکمه‌های ثابت (برای تغییر اسم)
USER_BUTTONS = [
    "⭐ خرید استارز", "🧸 گیفت و تدی", "🚀 بوست", "✨ ری‌اکشن پرمیوم",
    "📱 شماره مجازی", "🎁 سایر خدمات", "💰 کیف پول", "📦 سفارش‌های من",
    "🎟 کد تخفیف", "🎯 دعوت دوستان", "👨‍💻 پشتیبانی", "🏠 منوی اصلی",
    "🔙 بازگشت", "✅ تایید و پرداخت", "❌ انصراف", "👤 برای خودم",
    "🎁 برای شخص دیگر", "💳 کارت به کارت (رسید)", "💎 شارژ با TON",
]
ADMIN_BUTTONS = [
    "👑 پنل مدیریت", "🛒 سفارش‌ها", "🧾 رسیدها", "👥 کاربران",
    "📦 محصولات", "💲 قیمت‌ها", "🎟 کدهای تخفیف", "✏️ تغییر اسم دکمه‌ها",
    "🔘 دکمه‌های فروشگاه", "🔗 جوین اجباری", "📢 پیام همگانی", "⚙️ تنظیمات",
    "✅ تحویل شد", "❌ رد و بازگشت وجه", "➕ شارژ کاربر", "➖ کسر حساب",
]


def _btn_items(scope):
    return USER_BUTTONS if scope == "u" else ADMIN_BUTTONS


def btn_list_view(scope, page_no):
    items = _btn_items(scope)
    chunk, page, total = paginate(list(range(len(items))), page_no, BTN_PER_PAGE)
    rows = []
    for i in chunk:
        orig = items[i]
        cur = _btn_labels.get(orig)
        rows.append([B(f"{cur} ✏️" if cur else orig, f"adm_blp:{scope}:{i}:{page}", PRIMARY, raw=True)])
    nav = nav_row(f"adm_bl:{scope}", page, total)
    if nav:
        rows.append(nav)
    rows.append([
        B("👥 دکمه‌های کاربران" + (" ✅" if scope == "u" else ""), "adm_bl:u:0",
          SUCCESS if scope == "u" else PRIMARY),
        B("👑 دکمه‌های ادمین" + (" ✅" if scope == "a" else ""), "adm_bl:a:0",
          SUCCESS if scope == "a" else PRIMARY),
    ])
    rows.append([B("🔄 برگرداندن همه به پیش‌فرض", "adm_blrall", DANGER)])
    rows.append([B("🔙 پنل مدیریت", "adm")])
    text = (
        "✏️ <b>تغییر اسم دکمه‌ها</b>\n\n"
        f"{'👥 دکمه‌های کاربران' if scope == 'u' else '👑 دکمه‌های ادمین'}\n"
        "روی هر دکمه بزن تا اسمش را عوض کنی."
    )
    return text, kb(rows)


@admin_router.callback_query(F.data.startswith("adm_bl:"))
async def cb_adm_btn_list(call: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        _, scope, page = call.data.split(":")
        page = int(page)
    except ValueError:
        scope, page = "u", 0
    if scope not in ("u", "a"):
        scope = "u"
    await call.answer()
    await render(call, *btn_list_view(scope, page))


@admin_router.callback_query(F.data.startswith("adm_blp:"))
async def cb_adm_btn_detail(call: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        _, scope, idx, page = call.data.split(":")
        idx, page = int(idx), int(page)
        items = _btn_items(scope)
        orig = items[idx]
    except (ValueError, IndexError):
        await call.answer("❌ پیدا نشد.", show_alert=True)
        return
    await call.answer()
    cur = _btn_labels.get(orig)
    text = (
        "✏️ <b>تغییر اسم دکمه</b>\n\n"
        f"🔹 پیش‌فرض: <code>{esc(orig)}</code>\n"
        f"🔸 فعلی: <code>{esc(cur or orig)}</code>"
    )
    rows = [[B("✏️ تعیین اسم جدید", f"adm_ble:{scope}:{idx}:{page}", PRIMARY)]]
    if cur:
        rows.append([B("🔄 برگرداندن به پیش‌فرض", f"adm_blr:{scope}:{idx}:{page}", SUCCESS)])
    rows.append([B("🔙 لیست", f"adm_bl:{scope}:{page}")])
    await render(call, text, kb(rows))


@admin_router.callback_query(F.data.startswith("adm_ble:"))
async def cb_adm_btn_edit(call: CallbackQuery, state: FSMContext):
    try:
        _, scope, idx, page = call.data.split(":")
        idx, page = int(idx), int(page)
        items = _btn_items(scope)
        orig = items[idx]
    except (ValueError, IndexError):
        await call.answer("❌ پیدا نشد.", show_alert=True)
        return
    await call.answer()
    await state.set_state(ButtonRename.value)
    await state.update_data(scope=scope, idx=idx, page=page, orig=orig)
    await render(
        call,
        f"✏️ اسم جدید دکمه‌ی <code>{esc(orig)}</code> را بفرست.\n\nحداکثر {BTN_NAME_MAX} کاراکتر.",
        cancel_adm(f"adm_blp:{scope}:{idx}:{page}"),
    )


@admin_router.message(ButtonRename.value)
async def msg_adm_btn_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name or len(name) > BTN_NAME_MAX or "\n" in name:
        await message.answer(f"❌ اسم باید یک خط و بین ۱ تا {BTN_NAME_MAX} کاراکتر باشد.")
        return
    data = await state.get_data()
    orig = data["orig"]
    if name == orig:
        db.execute("DELETE FROM button_labels WHERE orig=?", (orig,))
    else:
        db.execute(
            "INSERT INTO button_labels (orig, label) VALUES (?,?) "
            "ON CONFLICT(orig) DO UPDATE SET label=excluded.label",
            (orig, name),
        )
    db.commit()
    _load_btn_labels()
    await state.clear()
    await message.answer("✅ اسم دکمه ذخیره شد.",
                         reply_markup=adm_back(f"adm_bl:{data['scope']}:{data.get('page', 0)}",
                                               "🔙 لیست دکمه‌ها"))


@admin_router.callback_query(F.data.startswith("adm_blr:"))
async def cb_adm_btn_reset(call: CallbackQuery, state: FSMContext):
    try:
        _, scope, idx, page = call.data.split(":")
        idx = int(idx)
        orig = _btn_items(scope)[idx]
    except (ValueError, IndexError):
        await call.answer("❌ پیدا نشد.", show_alert=True)
        return
    db.execute("DELETE FROM button_labels WHERE orig=?", (orig,))
    db.commit()
    _load_btn_labels()
    await call.answer("🔄 به پیش‌فرض برگشت.")
    await cb_adm_btn_detail(call, state)


@admin_router.callback_query(F.data == "adm_blrall")
async def cb_adm_btn_reset_all(call: CallbackQuery):
    await call.answer()
    await render(call, "⚠️ اسم همه‌ی دکمه‌ها به پیش‌فرض برمی‌گردد. مطمئنی؟",
                 kb([[B("✅ بله", "adm_blrallok", SUCCESS),
                      B("❌ انصراف", "adm_bl:u:0", DANGER)]]))


@admin_router.callback_query(F.data == "adm_blrallok")
async def cb_adm_btn_reset_all_ok(call: CallbackQuery):
    db.execute("DELETE FROM button_labels")
    db.commit()
    _load_btn_labels()
    await call.answer("✅ همه به پیش‌فرض برگشتند.", show_alert=True)
    await render(call, *btn_list_view("u", 0))


# ---------------- کد تخفیف ----------------

@admin_router.callback_query(F.data.startswith("adm_disc:"))
async def cb_adm_discounts(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    items = db.execute("SELECT * FROM discounts ORDER BY id DESC").fetchall()
    chunk, page, total = paginate(items, int(call.data.split(":")[1]), 8)
    rows = []
    for d in chunk:
        used = db.execute("SELECT COUNT(*) FROM discount_uses WHERE discount_id=?",
                          (d["id"],)).fetchone()[0]
        lim = d["usage_limit"] or "∞"
        rows.append([
            B(f"{'🟢' if d['active'] else '🔴'} {d['code']} | {d['percent']}% | {used}/{lim}",
              f"adm_dtog:{d['id']}"),
            B("🗑", f"adm_ddel:{d['id']}", DANGER),
        ])
    nav = nav_row("adm_disc", page, total)
    if nav:
        rows.append(nav)
    rows.append([B("➕ ساخت کد تخفیف", "adm_dnew", SUCCESS)])
    rows.append([B("🔙 پنل مدیریت", "adm")])
    await render(call, "🎟 <b>کدهای تخفیف</b>", kb(rows))


@admin_router.callback_query(F.data.startswith("adm_dtog:"))
async def cb_adm_dtog(call: CallbackQuery, state: FSMContext):
    db.execute("UPDATE discounts SET active = 1 - active WHERE id=?",
               (int(call.data.split(":")[1]),))
    db.commit()
    await call.answer()
    await cb_adm_discounts(call, state)


@admin_router.callback_query(F.data.startswith("adm_ddel:"))
async def cb_adm_ddel(call: CallbackQuery, state: FSMContext):
    did = int(call.data.split(":")[1])
    db.execute("DELETE FROM discounts WHERE id=?", (did,))
    db.execute("UPDATE users SET discount_id=NULL WHERE discount_id=?", (did,))
    db.commit()
    await call.answer()
    await cb_adm_discounts(call, state)


@admin_router.callback_query(F.data == "adm_dnew")
async def cb_adm_dnew(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(AddDiscount.code)
    await render(call, "🎟 متن کد تخفیف را بفرستید (مثلاً <code>OFF20</code>):",
                 cancel_adm("adm_disc:0"))


@admin_router.message(AddDiscount.code)
async def msg_ad_code(message: Message, state: FSMContext):
    code = (message.text or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9_\-]{2,30}", code):
        await message.answer("❌ فقط حروف انگلیسی/عدد (۲ تا ۳۰ کاراکتر).")
        return
    if db.execute("SELECT 1 FROM discounts WHERE code=?", (code,)).fetchone():
        await message.answer("❌ این کد قبلاً ساخته شده.")
        return
    await state.update_data(code=code)
    await state.set_state(AddDiscount.percent)
    await message.answer("🔢 درصد تخفیف را بفرستید (۱ تا ۱۰۰):")


@admin_router.message(AddDiscount.percent)
async def msg_ad_percent(message: Message, state: FSMContext):
    p = to_int(message.text)
    if not p or not 1 <= p <= 100:
        await message.answer("❌ عدد بین ۱ تا ۱۰۰ بفرستید.")
        return
    await state.update_data(percent=p)
    await state.set_state(AddDiscount.limit)
    await message.answer("👥 حداکثر تعداد استفاده؟ (برای نامحدود <code>0</code>)")


@admin_router.message(AddDiscount.limit)
async def msg_ad_limit(message: Message, state: FSMContext):
    n = to_int(message.text)
    if n is None:
        await message.answer("❌ فقط عدد بفرستید.")
        return
    await state.update_data(limit=n)
    await state.set_state(AddDiscount.days)
    await message.answer("📅 چند روز اعتبار داشته باشد؟ (برای بدون انقضا <code>0</code>)")


@admin_router.message(AddDiscount.days)
async def msg_ad_days(message: Message, state: FSMContext):
    days = to_int(message.text)
    if days is None:
        await message.answer("❌ فقط عدد بفرستید.")
        return
    d = await state.get_data()
    expires = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M") if days else None
    db.execute(
        "INSERT INTO discounts (code, percent, usage_limit, expires_at) VALUES (?,?,?,?)",
        (d["code"], d["percent"], d["limit"], expires),
    )
    db.commit()
    await state.clear()
    await message.answer(
        f"✅ کد <b>{d['code']}</b> ({d['percent']}%) ساخته شد.",
        reply_markup=adm_back("adm_disc:0", "🔙 کدهای تخفیف"),
    )


# ---------------- جوین اجباری ----------------

@admin_router.callback_query(F.data == "adm_fj")
async def cb_adm_fj(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    rows = [[B(f"🗑 {r['chat_id']}", f"adm_fjdel:{r['id']}", DANGER)]
            for r in db.execute("SELECT * FROM force_join")]
    rows.append([B("➕ افزودن کانال/گروه", "adm_fjadd", SUCCESS)])
    rows.append([B("🔙 پنل مدیریت", "adm")])
    main = f"\n\nکانال اصلی (در کد): {esc(MAIN_CHANNEL)}" if MAIN_CHANNEL else ""
    await render(
        call,
        "🔗 <b>جوین اجباری</b>\n\nربات باید در کانال/گروه <b>ادمین</b> باشد." + main,
        kb(rows),
    )


@admin_router.callback_query(F.data == "adm_fjadd")
async def cb_adm_fjadd(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(AddForceJoin.chat)
    await render(call, "یوزرنیم کانال را بفرستید (مثلاً <code>@my_channel</code>):",
                 cancel_adm("adm_fj"))


@admin_router.message(AddForceJoin.chat)
async def msg_fj_chat(message: Message, state: FSMContext):
    chat = (message.text or "").strip()
    if not chat:
        return
    await state.update_data(chat=chat)
    await state.set_state(AddForceJoin.link)
    await message.answer("🔗 لینک عضویت (https://t.me/...) را بفرستید:")


@admin_router.message(AddForceJoin.link)
async def msg_fj_link(message: Message, state: FSMContext):
    link = (message.text or "").strip()
    if not link.startswith("http"):
        await message.answer("❌ لینک باید با https:// شروع شود.")
        return
    chat = (await state.get_data())["chat"]
    db.execute("INSERT INTO force_join (chat_id, link) VALUES (?,?)", (chat, link))
    db.commit()
    await state.clear()
    await message.answer("✅ اضافه شد.", reply_markup=adm_back("adm_fj", "🔙 جوین اجباری"))


@admin_router.callback_query(F.data.startswith("adm_fjdel:"))
async def cb_adm_fjdel(call: CallbackQuery, state: FSMContext):
    db.execute("DELETE FROM force_join WHERE id=?", (int(call.data.split(":")[1]),))
    db.commit()
    await cb_adm_fj(call, state)


# ---------------- پیام همگانی ----------------

@admin_router.callback_query(F.data == "adm_bc")
async def cb_adm_bc(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(Broadcast.content)
    await render(call, "📢 <b>پیام همگانی</b>\n\nپیام را بفرستید (متن، عکس، ...):",
                 cancel_adm())


@admin_router.message(Broadcast.content)
async def msg_bc_content(message: Message, state: FSMContext):
    await state.update_data(chat_id=message.chat.id, msg_id=message.message_id)
    await state.set_state(None)
    n = db.execute("SELECT COUNT(*) FROM users WHERE banned=0").fetchone()[0]
    await message.answer(
        f"⚠️ این پیام برای <b>{fmt(n)}</b> کاربر ارسال می‌شود. تایید می‌کنید؟",
        reply_markup=kb([[B("✅ ارسال", "adm_bcok", SUCCESS), B("❌ انصراف", "adm", DANGER)]]),
    )


@admin_router.callback_query(F.data == "adm_bcok")
async def cb_adm_bcok(call: CallbackQuery, state: FSMContext, bot: Bot):
    d = await state.get_data()
    if "msg_id" not in d:
        await call.answer("❌ پیامی برای ارسال نیست.", show_alert=True)
        return
    await state.clear()
    await call.answer("⏳ شروع شد...")
    await render(call, "⏳ در حال ارسال پیام همگانی...")
    users = [r["user_id"] for r in db.execute("SELECT user_id FROM users WHERE banned=0")]
    ok = fail = 0
    for uid in users:
        try:
            await bot.copy_message(uid, d["chat_id"], d["msg_id"])
            ok += 1
        except TelegramForbiddenError:
            fail += 1
        except Exception as e:
            logging.warning("broadcast to %s failed: %s", uid, e)
            fail += 1
        await asyncio.sleep(0.05)
    await call.message.answer(f"✅ ارسال تموم شد.\n\n📨 موفق: {ok}\n❌ ناموفق: {fail}",
                              reply_markup=adm_back())


# ---------------- تنظیمات ----------------

SETTING_LABELS = {
    "card_number": "💳 شماره کارت",
    "card_holder": "👤 نام صاحب کارت",
    "min_charge": "⬇️ حداقل شارژ (تومان)",
    "ton_address": "💎 آدرس ولت TON",
    "ton_rate": "📊 نرخ TON به تومان (0 = خودکار)",
    "referral_reward": "🎯 پاداش رفرال (تومان)",
    "support_username": "👨‍💻 آیدی پشتیبانی",
    "price_markup": "📈 درصد افزایش روی قیمت‌ها",
    "welcome_text": "👋 متن خوش‌آمدگویی",
}
TOGGLES = {
    "shop_open": "🛍 فروشگاه",
    "crypto_enabled": "💎 شارژ ارزی (TON)",
    "colored_buttons": "🎨 دکمه‌های رنگی",
    "glow_buttons": "✨ افکت درخشش دکمه‌ها",
}


@admin_router.callback_query(F.data == "adm_set")
async def cb_adm_settings(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.answer()
    rows = []
    for key, label in TOGGLES.items():
        on = flag(key)
        rows.append([B(f"{label}: {'🟢 روشن' if on else '🔴 خاموش'}", f"adm_settog:{key}",
                       SUCCESS if on else DANGER)])
    for key, label in SETTING_LABELS.items():
        rows.append([B(f"✏️ {label}", f"adm_setedit:{key}")])
    rows.append([B("🔙 پنل مدیریت", "adm")])
    text = (
        "⚙️ <b>تنظیمات</b>\n\n"
        f"💳 کارت: <code>{esc(get_setting('card_number'))}</code> — {esc(get_setting('card_holder'))}\n"
        f"⬇️ حداقل شارژ: {fmt(get_setting('min_charge'))} تومان\n"
        f"💎 ولت TON: <code>{esc(get_setting('ton_address')) or '—'}</code>\n"
        f"🎯 پاداش رفرال: {fmt(get_setting('referral_reward'))} تومان\n"
        f"👨‍💻 پشتیبانی: {esc(get_setting('support_username')) or '—'}"
    )
    await render(call, text, kb(rows))


@admin_router.callback_query(F.data.startswith("adm_settog:"))
async def cb_adm_settog(call: CallbackQuery, state: FSMContext):
    key = call.data.split(":")[1]
    if key in TOGGLES:
        set_setting(key, "0" if flag(key) else "1")
    await cb_adm_settings(call, state)


@admin_router.callback_query(F.data.startswith("adm_setedit:"))
async def cb_adm_setedit(call: CallbackQuery, state: FSMContext):
    key = call.data.split(":")[1]
    if key not in SETTING_LABELS:
        await call.answer()
        return
    await call.answer()
    await state.set_state(SettingEdit.value)
    await state.update_data(key=key)
    await render(call, f"✏️ مقدار جدید «{SETTING_LABELS[key]}» را بفرستید:", cancel_adm("adm_set"))


@admin_router.message(SettingEdit.value)
async def msg_setting_value(message: Message, state: FSMContext):
    key = (await state.get_data())["key"]
    value = (message.text or "").strip()
    if key in ("min_charge", "referral_reward", "ton_rate"):
        n = to_int(value)
        if n is None:
            await message.answer("❌ فقط عدد بفرستید.")
            return
        value = str(n)
    if key == "support_username":
        value = value.lstrip("@")
    if key == "price_markup":
        try:
            float(value.replace(",", "."))
        except ValueError:
            await message.answer("❌ عدد معتبر بفرستید (مثلاً 10.5).")
            return
    set_setting(key, value)
    await state.clear()
    await message.answer("✅ ذخیره شد.", reply_markup=adm_back("adm_set", "🔙 تنظیمات"))


# =========================================================
# Fallback + Error + Run
# =========================================================

@user_router.message()
async def fallback(message: Message, state: FSMContext):
    if await state.get_state() is not None:
        return
    await message.answer("از دکمه‌های زیر استفاده کنید 👇", reply_markup=home_kb(message.from_user.id))


dp = Dispatcher(storage=MemoryStorage())
dp.message.outer_middleware(Gate())
dp.callback_query.outer_middleware(Gate())
dp.include_router(admin_router)
dp.include_router(user_router)


@dp.errors()
async def on_error(event):
    logging.exception("Unhandled error: %s", event.exception)
    try:
        if event.update.callback_query:
            await event.update.callback_query.answer("⚠️ خطایی رخ داد. دوباره امتحان کنید.", show_alert=True)
    except Exception:
        pass
    return True


async def main():
    if not TOKEN:
        raise SystemExit("❌ توکن تنظیم نشده. متغیر محیطی BOT_TOKEN را ست کنید.")
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.get_me()
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info("Bot @%s started. Owner=%s Admins=%s", me.username, OWNER_ID, ADMIN_IDS)
    watcher = asyncio.create_task(crypto_watcher(bot))
    try:
        await dp.start_polling(bot, handle_signals=False)
    finally:
        watcher.cancel()


if __name__ == "__main__":
    asyncio.run(main())
