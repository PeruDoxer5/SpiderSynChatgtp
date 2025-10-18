import asyncio
import json
import aiosqlite
import httpx
from datetime import datetime
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ---------------- CONFIG ----------------
with open("config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

TELEGRAM_TOKEN = config["TELEGRAM_TOKEN"]
OPENAI_API_KEY = config["OPENAI_API_KEY"]
MODEL = config.get("MODEL", "gpt-4o-mini")
ADMINS = set(config.get("ADMINS", []))
DB_FILE = "bans.db"
# ----------------------------------------

# ----------- DB FUNCTIONS ---------------
async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS banned (user_id INTEGER PRIMARY KEY, reason TEXT, banned_at TEXT)"
        )
        await db.commit()

async def is_banned(user_id: int) -> bool:
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT 1 FROM banned WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        await cur.close()
        return row is not None

async def ban_user(user_id: int, reason: str = ""):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR REPLACE INTO banned (user_id, reason, banned_at) VALUES (?, ?, ?)",
            (user_id, reason, datetime.utcnow().isoformat()),
        )
        await db.commit()

async def unban_user(user_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM banned WHERE user_id = ?", (user_id,))
        await db.commit()

async def list_banned():
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT user_id, reason, banned_at FROM banned")
        rows = await cur.fetchall()
        await cur.close()
        return rows
# ----------------------------------------

# ----------- OPENAI CHAT ----------------
async def openai_chat(prompt: str) -> str:
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    data = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "Eres SpiderSyn, una IA con estilo hacker, analítica, directa y útil. "
                                          "Habla con un toque sarcástico pero siempre con respeto."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 700,
        "temperature": 0.7,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, json=data, headers=headers)
        r.raise_for_status()
        res = r.json()
        return res["choices"][0]["message"]["content"].strip()
# ----------------------------------------

# ----------- DECORADOR ADMIN ------------
def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or user.id not in ADMINS:
            await update.message.reply_text("🕷️ Acceso denegado. Solo administradores pueden usar ese comando.")
            return
        return await func(update, context)
    return wrapper
# ----------------------------------------

# ----------- HANDLERS -------------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🕸️ *Bienvenido a SpiderSyn* 🧠\n\n"
        "Soy una IA basada en ChatGPT. Puedo responderte en chats privados o en grupos.\n\n"
        "👑 *Comandos de administrador:*\n"
        "• /ban `<user_id>` [razón]\n"
        "• /unban `<user_id>`\n"
        "• /banned — lista de baneados\n\n"
        "Habla conmigo, humano... veamos qué tan interesante eres.",
        parse_mode="Markdown"
    )

@admin_only
async def ban_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args and not update.message.reply_to_message:
        await update.message.reply_text("🕷️ Uso correcto: /ban <user_id> [razón] o responde al mensaje del usuario.")
        return

    target_id = None
    reason = ""

    if update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
    elif args:
        try:
            target_id = int(args[0])
            if len(args) > 1:
                reason = " ".join(args[1:])
        except ValueError:
            await update.message.reply_text("⚠️ El ID del usuario debe ser numérico.")
            return

    if target_id:
        await ban_user(target_id, reason)
        await update.message.reply_text(
            f"🕸️ *SpiderSyn* ha baneado al usuario `{target_id}`.\n"
            f"Motivo: {reason or 'Sin especificar.'}",
            parse_mode="Markdown"
        )

@admin_only
async def unban_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args and not update.message.reply_to_message:
        await update.message.reply_text("🕷️ Uso: /unban <user_id> o responde al mensaje del usuario.")
        return

    target_id = None

    if update.message.reply_to_message:
        target_id = update.message.reply_to_message.from_user.id
    elif args:
        try:
            target_id = int(args[0])
        except ValueError:
            await update.message.reply_text("⚠️ El ID del usuario debe ser numérico.")
            return

    if target_id:
        await unban_user(target_id)
        await update.message.reply_text(f"✅ Usuario `{target_id}` ha sido liberado de la red 🕸️.", parse_mode="Markdown")

@admin_only
async def banned_list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await list_banned()
    if not rows:
        await update.message.reply_text("🕸️ Nadie está atrapado en la red de SpiderSyn.")
    else:
        text = "📋 *Usuarios atrapados en la red:*\n\n" + "\n".join(
            f"• `{r[0]}` — {r[1] or 'Sin razón'} — {r[2][:19]}" for r in rows
        )
        await update.message.reply_text(text, parse_mode="Markdown")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user = update.effective_user
    if await is_banned(user.id):
        await update.message.reply_text("🚫 SpiderSyn te ha silenciado. Contacta con un administrador.")
        return

    text = update.message.text.strip()
    if text.startswith("/"):
        return  # ignorar comandos

    await update.message.chat.send_action("typing")
    try:
        resp = await openai_chat(text)
        await update.message.reply_text(resp)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error interno: {e}")
# ----------------------------------------

# ----------- MAIN LOOP ------------------
async def main():
    await init_db()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("ban", ban_handler))
    app.add_handler(CommandHandler("unban", unban_handler))
    app.add_handler(CommandHandler("banned", banned_list_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    print("🕷️ SpiderSyn está vivo y rastreando mensajes...")
    await app.run_polling()
# ----------------------------------------

if __name__ == "__main__":
    asyncio.run(main())
