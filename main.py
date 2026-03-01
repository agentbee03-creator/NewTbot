import os
import requests
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler

# ========== ПРОСТОЙ HTTP СЕРВЕР ДЛЯ RAILWAY HEALTHCHECK ==========
# Этот сервер гарантированно работает без сложных зависимостей
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'OK')
    
    def log_message(self, format, *args):
        pass  # Не пишем логи на каждый запрос

def run_health_server():
    port = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    print(f"✅ Healthcheck server running on port {port}")
    server.serve_forever()

# Запускаем сервер в фоновом потоке
health_thread = threading.Thread(target=run_health_server, daemon=True)
health_thread.start()
# ==================================================================

# --- Состояния для разговора ---
WALLET1, WALLET2 = range(2)

# --- Получаем ключи из переменных окружения ---
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TONAPI_KEY = os.environ.get('TONAPI_KEY')

print(f"🚀 Запуск бота...")
print(f"🔑 TONAPI_KEY загружен: {'✅' if TONAPI_KEY else '❌'}")

# --- Функция для теста API ---
def test_api():
    """Тестирует подключение к API"""
    test_wallet = "EQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqB2N"
    url = f"https://tonapi.io/v2/accounts/{test_wallet}/events"
    
    headers = {}
    if TONAPI_KEY:
        headers['Authorization'] = f'Bearer {TONAPI_KEY}'
    
    try:
        response = requests.get(url, headers=headers, params={'limit': 1}, timeout=5)
        print(f"📡 Тест API: статус {response.status_code}")
        if response.status_code == 200:
            print("✅ API работает")
            return True
        else:
            print(f"❌ API ошибка: {response.text[:100]}")
            return False
    except Exception as e:
        print(f"❌ Исключение при тесте API: {e}")
        return False

# Вызываем тест при старте
test_api()

# --- Функция для получения транзакций ---
def get_all_transactions(wallet_address, limit=50):
    """Получает все транзакции кошелька"""
    url = f"https://tonapi.io/v2/accounts/{wallet_address}/events"
    
    headers = {}
    if TONAPI_KEY:
        headers['Authorization'] = f'Bearer {TONAPI_KEY}'
    
    print(f"🔍 Запрашиваю транзакции для {wallet_address[:6]}...")
    
    try:
        response = requests.get(url, headers=headers, params={'limit': limit}, timeout=10)
        print(f"📡 Статус ответа: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            events = data.get('events', [])
            print(f"✅ Получено событий: {len(events)}")
            return events
        else:
            print(f"❌ Ошибка API: {response.status_code} - {response.text[:200]}")
            return []
    except Exception as e:
        print(f"❌ Исключение при запросе: {e}")
        return []

def calculate_transfers(wallet_a, wallet_b):
    """Считает переводы между двумя кошельками"""
    print(f"\n🧮 Анализирую транзакции между {wallet_a[:6]}... и {wallet_b[:6]}...")
    
    events = get_all_transactions(wallet_a, limit=100)
    
    sent = 0.0
    received = 0.0
    tx_found = 0
    
    wallet_a_lower = wallet_a.lower()
    wallet_b_lower = wallet_b.lower()
    
    for event in events:
        try:
            actions = event.get('actions', [])
            
            for action in actions:
                action_type = action.get('type')
                
                # --- Обычные TON переводы ---
                if action_type == 'TonTransfer':
                    transfer = action.get('TonTransfer', {})
                    sender = transfer.get('sender', {}).get('address', '').lower()
                    recipient = transfer.get('recipient', {}).get('address', '').lower()
                    amount = int(transfer.get('amount', '0')) / 1_000_000_000
                    
                    if sender == wallet_a_lower and recipient == wallet_b_lower:
                        sent += amount
                        tx_found += 1
                        print(f"    ✓ ОТПРАВЛЕНО {amount} TON")
                    elif sender == wallet_b_lower and recipient == wallet_a_lower:
                        received += amount
                        tx_found += 1
                        print(f"    ✓ ПОЛУЧЕНО {amount} TON")
                
                # --- Jetton переводы (токены) ---
                elif action_type == 'JettonTransfer':
                    transfer = action.get('JettonTransfer', {})
                    sender = transfer.get('sender', {}).get('address', '').lower()
                    recipient = transfer.get('recipient', {}).get('address', '').lower()
                    amount = int(transfer.get('amount', '0')) / 1_000_000_000
                    symbol = transfer.get('jetton', {}).get('symbol', 'Unknown')
                    
                    if sender == wallet_a_lower and recipient == wallet_b_lower:
                        sent += amount
                        tx_found += 1
                        print(f"    ✓ ОТПРАВЛЕНО {amount} {symbol}")
                    elif sender == wallet_b_lower and recipient == wallet_a_lower:
                        received += amount
                        tx_found += 1
                        print(f"    ✓ ПОЛУЧЕНО {amount} {symbol}")
        
        except Exception as e:
            print(f"  ⚠️ Ошибка при обработке события: {e}")
            continue
    
    print(f"📊 ИТОГО: найдено {tx_found} транзакций")
    print(f"📤 Всего отправлено: {sent}")
    print(f"📥 Всего получено: {received}")
    
    return sent, received

# --- Команда /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я помогу посчитать взаиморасчеты между TON кошельками.\n\n"
        "Просто отправь команду /calc и следуй инструкциям."
    )

# --- Команда /calc (начало расчета) ---
async def calc_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔹 Введите **адрес первого кошелька** (ваш):\n"
        "(начинается с EQ или UQ)"
    )
    return WALLET1

# --- Получаем первый кошелек ---
async def get_wallet1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wallet = update.message.text.strip()
    
    if not (wallet.startswith('EQ') or wallet.startswith('UQ')):
        await update.message.reply_text("❌ Это не TON кошелек. Попробуйте еще раз:")
        return WALLET1
    
    context.user_data['wallet1'] = wallet
    await update.message.reply_text("✅ Первый кошелек сохранен.\n\n🔹 Теперь введите **второй кошелек**:")
    return WALLET2

# --- Получаем второй кошелек и считаем ---
async def get_wallet2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wallet2 = update.message.text.strip()
    wallet1 = context.user_data.get('wallet1')
    
    if not (wallet2.startswith('EQ') or wallet2.startswith('UQ')):
        await update.message.reply_text("❌ Это не TON кошелек. Попробуйте еще раз:")
        return WALLET2
    
    status_msg = await update.message.reply_text("🔄 Считаю транзакции... Это займет несколько секунд.")
    
    try:
        sent, received = calculate_transfers(wallet1, wallet2)
        diff = received - sent
        
        report = (
            f"📊 **Отчет по взаиморасчетам**\n\n"
            f"💰 **Отправлено** на кошелек 2: `{sent:.2f}` TON\n"
            f"💰 **Получено** с кошелька 2: `{received:.2f}` TON\n"
            f"📈 **Разница**: `{diff:+.2f}` TON\n\n"
            f"*Если разница положительная — вы должны получить, отрицательная — вы должны отправить*"
        )
        
        await status_msg.delete()
        await update.message.reply_text(report, parse_mode='Markdown')
        
    except Exception as e:
        await status_msg.delete()
        await update.message.reply_text(f"❌ Ошибка при расчете: {str(e)[:200]}")
    
    context.user_data.clear()
    return ConversationHandler.END

# --- Отмена ---
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Операция отменена. Для нового расчета отправьте /calc")
    context.user_data.clear()
    return ConversationHandler.END

# --- Запуск бота ---
def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('calc', calc_start)],
        states={
            WALLET1: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_wallet1)],
            WALLET2: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_wallet2)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)
    
    print("✅ Бот запущен и готов к работе!")
    app.run_polling()

if __name__ == '__main__':
    main()
