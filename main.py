async def get_wallet2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wallet2 = update.message.text.strip()
    wallet1 = context.user_data.get('wallet1')
    
    status_msg = await update.message.reply_text("🔄 Анализирую блокчейн... Это может занять до минуты ⏳")
    
    try:
        sent, received = await calculate_flow(wallet1, wallet2)
        diff = received - sent
        
        # Короткие адреса для отображения
        a_short = wallet1[:6] + "…" + wallet1[-4:] if len(wallet1) > 20 else wallet1
        b_short = wallet2[:6] + "…" + wallet2[-4:] if len(wallet2) > 20 else wallet2
        
        if sent == 0 and received == 0:
            text = f"📭 Транзакций между кошельками не найдено\n\n`{a_short}` ↔ `{b_short}`"
        else:
            sign = "➕" if diff > 0 else "➖" if diff < 0 else "⚖️"
            
            text = (
                f"📊 **Взаиморасчеты**\n\n"
                f"A: `{a_short}`\n"
                f"B: `{b_short}`\n\n"
                f"📥 A отправил на B: `{sent:.4f}` TON\n"
                f"📤 A получил от B: `{received:.4f}` TON\n\n"
                f"⚖️ **{sign} {abs(diff):.4f} TON**"
            )
        
        await status_msg.delete()
        await update.message.reply_text(text, parse_mode='Markdown')
        
    except Exception as e:
        await status_msg.delete()
        await update.message.reply_text(f"❌ Ошибка при расчете: {str(e)[:200]}")
    
    context.user_data.clear()
    return ConversationHandler.END
