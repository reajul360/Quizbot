import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# Questions
questions = [
    {
        "question": "What is the capital of France?",
        "options": ["London", "Berlin", "Paris", "Rome"],
        "answer": 2
    },
    {
        "question": "2 + 2 = ?",
        "options": ["3", "4", "5", "6"],
        "answer": 1
    }
]

user_scores = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_scores[update.effective_user.id] = 0
    await ask_question(update.message, 0)

async def ask_question(message, q_index):
    q = questions[q_index]
    buttons = [
        [InlineKeyboardButton(opt, callback_data=f"{q_index}:{i}")]
        for i, opt in enumerate(q["options"])
    ]
    await message.reply_text(q["question"], reply_markup=InlineKeyboardMarkup(buttons))

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    q_index, choice = map(int, query.data.split(":"))
    if choice == questions[q_index]["answer"]:
        user_scores[query.from_user.id] += 1

    if q_index + 1 < len(questions):
        await query.message.delete()
        await ask_question(query.message, q_index + 1)
    else:
        score = user_scores[query.from_user.id]
        await query.message.delete()
        await query.message.reply_text(f"Quiz finished! Your score: {score}/{len(questions)}")

TOKEN = os.getenv("BOT_TOKEN")

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(button_click))

app.run_polling()
