import os
import requests
from flask import Flask
import threading
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# --- Простой HTTP сервер для Railway ---
app = Flask(__name__)
@app.route('/')
@app.route('/health')
def health():
    return 'OK', 200

def run_http():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

thread = threading.Thread(target=run_http, daemon=True)
thread.start()
# --------------------------------------

TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TONAPI_KEY = os.environ.get('TONAPI_KEY')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот работает!")

def main():
    app_bot = Application.builder().token(TOKEN).build()
    app_bot.add_handler(CommandHandler("start", start))
    print("✅ Бот запущен")
    app_bot.run_polling()

if __name__ == '__main__':
    main()