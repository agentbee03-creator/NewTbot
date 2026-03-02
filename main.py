import os
import aiohttp
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
import base64
import hashlib

# ========== HTTP СЕРВЕР ДЛЯ RAILWAY HEALTHCHECK ==========
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'OK')
    
    def log_message(self, format, *args):
        pass

def run_health_server():
    port = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    print(f"✅ Healthcheck server running on port {port}")
    server.serve_forever()

health_thread = threading.Thread(target=run_health_server, daemon=True)
health_thread.start()
# =========================================================

# --- Состояния для разговора ---
WALLET1, WALLET2 = range(2)

# --- Получаем ключи из переменных окружения ---
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TONCENTER_API_KEY = os.environ.get('TONAPI_KEY')  # TonCenter принимает API ключ

print(f"🚀 Запуск бота...")
print(f"🔑 TONCENTER_API_KEY загружен: {'✅' if TONCENTER_API_KEY else '❌'}")

# --- HTTP сессия для запросов ---
http_session = None

async def get_http_session():
    global http_session
    if http_session is None:
        timeout = aiohttp.ClientTimeout(total=30)
        http_session = aiohttp.ClientSession(timeout=timeout)
    return http_session

# --- Нормализация адресов ---
def normalize_address(addr: str) -> str:
    """Приводит адрес к формату 0:..."""
    addr = addr.strip()
    if addr.startswith('0:'):
        return addr
    if addr.startswith(('EQ', 'UQ')):
        # Декодируем base64 адрес в raw формат
        try:
            # Добавляем padding если нужно
            addr_base64 = addr.replace('-', '+').replace('_', '/')
            if len(addr_base64) % 4:
                addr_base64 += '=' * (4 - len(addr_base64) % 4)
            
            # Декодируем
            decoded = base64.b64decode(addr_base64)
            # Первый байт - флаг, остальные 32 байта - адрес
            if len(decoded) == 33:
                return '0:' + decoded[1:].hex()
        except:
            pass
    return addr

def eq_to_raw(eq_address: str) -> str:
    """Конвертирует EQ/UQ адрес в raw формат 0:..."""
    normalized = normalize_address(eq_address)
    if normalized.startswith('0:'):
        return normalized
    return eq_address

# --- Разрешение .ton доменов ---
async def resolve_domain(domain: str) -> str:
    """Получает адрес кошелька по .ton домену"""
    if not domain.endswith('.ton'):
        return domain
    
    # Используем DNS для разрешения .ton доменов
    import dns.resolver
    try:
        answers = dns.resolver.resolve(f'_tonconnect.{domain}', 'TXT')
        for rdata in answers:
            txt = rdata.strings[0].decode()
            if txt.startswith('addr='):
                return txt[5:]
    except:
        pass
    return domain

# --- Получение транзакций через TonCenter API ---
async def get_transactions_page(address: str, limit: int = 100, lt: int = None, hash: str = None):
    """Получает одну страницу транзакций с использованием API ключа"""
    session = await get_http_session()
    
    params = {
        'address': address,
        'limit': limit
    }
    if lt and hash:
        params['lt'] = lt
        params['hash'] = hash
    
    headers = {}
    if TONCENTER_API_KEY:
        headers['X-API-Key'] = TONCENTER_API_KEY
        print(f"🔑 Использую API ключ для запроса")
    
    try:
        async with session.get('https://toncenter.com/api/v2/getTransactions', 
                               params=params, headers=headers) as resp:
            print(f"📡 Статус ответа: {resp.status}")
            
            if resp.status == 200:
                data = await resp.json()
                if data.get('ok'):
                    result = data.get('result', [])
                    print(f"✅ Получено {len(result)} транзакций")
                    return result
                else:
                    print(f"❌ API вернул ошибку: {data.get('error')}")
                    return []
            elif resp.status == 429:
                print("⚠️ Слишком много запросов (429). Увеличиваю паузу...")
                await asyncio.sleep(2)
                return []  # вернем пустой список, чтобы цикл повторился
            else:
                print(f"❌ Ошибка HTTP: {resp.status}")
                return []
    except Exception as e:
        print(f"❌ Ошибка получения транзакций: {e}")
        return []

async def get_all_transactions(address: str, max_txs: int = 1000) -> list:
    """Загружает ВСЕ транзакции кошелька с пагинацией"""
    raw_addr = eq_to_raw(address)
    all_txs = []
    lt = None
    tx_hash = None
    
    print(f"🔍 Загружаю транзакции для {address[:10]}...")
    
    while len(all_txs) < max_txs:
        page = await get_transactions_page(raw_addr, 100, lt, tx_hash)
        if not page:
            break
        
        all_txs.extend(page)
        print(f"✅ Загружено {len(all_txs)} транзакций")
        
        # Получаем параметры для следующей страницы
        if page and len(page) > 0:
            last_tx = page[-1]
            lt = last_tx.get('transaction_id', {}).get('lt')
            tx_hash = last_tx.get('transaction_id', {}).get('hash')
        else:
            break
        
        # Небольшая пауза между запросами
        await asyncio.sleep(0.3)
    
    print(f"✅ Всего загружено {len(all_txs)} транзакций")
    return all_txs

async def calculate_flow(wallet_a: str, wallet_b: str):
    """Считает взаиморасчеты между двумя кошельками"""
    
    # Разрешаем домены если нужно
    for i, w in enumerate([wallet_a, wallet_b]):
        if w.endswith('.ton'):
            resolved = await resolve_domain(w)
            if resolved and resolved != w:
                print(f"✅ Домен {w} -> {resolved[:10]}...")
                if i == 0:
                    wallet_a = resolved
                else:
                    wallet_b = resolved
    
    # Нормализуем адреса
    a_raw = normalize_address(wallet_a)
    b_raw = normalize_address(wallet_b)
    
    print(f"🔄 Анализ {a_raw[:10]}... <-> {b_raw[:10]}...")
    
    # Загружаем все транзакции первого кошелька
    txs = await get_all_transactions(a_raw)
    
    sent_nano = 0
    received_nano = 0
    
    for tx in txs:
        try:
            # Входящие (A получил от B)
            in_msg = tx.get('in_msg')
            if in_msg and in_msg.get('source'):
                src = normalize_address(in_msg['source'])
                if src == b_raw:
                    val = int(in_msg.get('value', 0))
                    received_nano += val
            
            # Исходящие (A отправил B)
            for out_msg in tx.get('out_msgs', []):
                dst = normalize_address(out_msg.get('destination', ''))
                if dst == b_raw:
                    val = int(out_msg.get('value', 0))
                    sent_nano += val
        except Exception as e:
            print(f"⚠️ Ошибка обработки транзакции: {e}")
            continue
    
    # Конвертируем из нано в TON
    sent = sent_nano / 1_000_000_000
    received = received_nano / 1_000_000_000
    
    print(f"📊 ИТОГ: Получено={received:.4f}TON, Отправлено={sent:.4f}TON")
    
    return sent, received

# --- Команда /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я помогу посчитать взаиморасчеты между TON кошельками.\n\n"
        "Просто отправь команду /calc и следуй инструкциям.\n"
        "Поддерживаю .ton домены!"
    )

# --- Команда /calc ---
async def calc_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔹 Введите **адрес первого кошелька** (ваш):\n"
        "(начинается с EQ, UQ или .ton домен)"
    )
    return WALLET1

async def get_wallet1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wallet = update.message.text.strip()
    context.user_data['wallet1'] = wallet
    await update.message.reply_text("✅ Первый кошелек сохранен.\n\n🔹 Теперь введите **второй кошелек**:")
    return WALLET2

async def get_wallet2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wallet2 = update.message.text.strip()
    wallet1 = context.user_data.get('wallet1')
    
    status_msg = await update.message.reply_text("🔄 Анализирую блокчейн... Это займет некоторое время ⏳")
    
    try:
        sent, received = await calculate_flow(wallet1, wallet2)
        diff = received - sent
        
        # Короткие адреса для отображения
        a_short = wallet1[:10] + "…" + wallet1[-6:] if len(wallet1) > 20 else wallet1
        b_short = wallet2[:10] + "…" + wallet2[-6:] if len(wallet2) > 20 else wallet2
        
        if sent == 0 and received == 0:
            text = f"📭 Транзакций между кошельками не найдено\n\n`{a_short}` ↔ `{b_short}`"
        else:
            sign = "➕" if diff > 0 else "➖" if diff < 0 else "⚖️"
            owes = "B должен A" if diff > 0 else "A должен B" if diff < 0 else "В расчете"
            
            text = (
                f"📊 **Взаиморасчеты**\n\n"
                f"A: `{a_short}`\n"
                f"B: `{b_short}`\n\n"
                f"📥 A получил от B: `{received:.4f}` TON\n"
                f"📤 A отправил B: `{sent:.4f}` TON\n\n"
                f"⚖️ **{sign} {abs(diff):.4f} TON**\n"
                f"_{owes}_"
            )
        
        await status_msg.delete()
        await update.message.reply_text(text, parse_mode='Markdown')
        
    except Exception as e:
        await status_msg.delete()
        await update.message.reply_text(f"❌ Ошибка при расчете: {str(e)[:200]}")
    
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Операция отменена")
    context.user_data.clear()
    return ConversationHandler.END

# --- Запуск ---
async def post_init(application: Application):
    """Действия после инициализации"""
    global http_session
    http_session = await get_http_session()

async def shutdown():
    """Закрытие сессии при остановке"""
    global http_session
    if http_session:
        await http_session.close()

def main():
    # Создаем цикл событий
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Создаем приложение
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    
    # Обработчик диалога
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
    
    # Сбрасываем вебхук
    loop.run_until_complete(app.bot.delete_webhook(drop_pending_updates=True))
    
    print("✅ Бот запущен и готов к работе!")
    
    # Запускаем
    try:
        app.run_polling()
    finally:
        loop.run_until_complete(shutdown())

if __name__ == '__main__':
    main()
