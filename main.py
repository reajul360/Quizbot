import logging
import json
import os
import re
from datetime import datetime, timezone, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from telegram.constants import ParseMode

# --- Configuration ---
BOT_TOKEN = "8281350439:AAH61nGOiyiaOvmWZ_yKroVeTCavv6BEAA8" # Replace with your bot token
OWNER_ID = 7919870032  # Replace with your Telegram User ID
DATA_FILE = "quiz_data.json"
SCORE_EXPIRATION_DAYS = 2

# --- Logging Setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# --- Data Management ---
def load_data():
    """Loads data from the JSON file."""
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # Return a default structure if file doesn't exist or is empty
        return {"quizzes": {}, "active_quiz_id": None, "user_scores": {}}

def save_data(data):
    """Saves data to the JSON file."""
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)


# --- Admin Decorator ---
def admin_only(func):
    """Decorator to restrict access to the bot owner."""
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if update.effective_user.id != OWNER_ID:
            await update.message.reply_text("⛔️ Sorry, this is an admin-only command.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

# --- Admin Commands ---
@admin_only
async def admin_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the list of admin commands."""
    help_text = """
👑 *Admin Command Menu* 👑

1️⃣ `/addquiz <Title>; <TimeLimitMinutes>`
Reply to a message with questions to add a quiz.
*Format:* `Question+Opt1,Opt2+CorrectNum`

2️⃣ `/setactive <QuizID>`
Sets a quiz as the one users can take.

3️⃣ `/updateversion <QuizID>`
Updates a quiz version, allowing all users to retake it.

4️⃣ `/listquizzes`
Shows all available quizzes and their IDs.

5️⃣ `/viewscores <QuizID>`
Shows all scores for a specific quiz.

6️⃣ `/deletequiz <QuizID>`
Permanently deletes a quiz.
"""
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

@admin_only
async def add_quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Adds a new quiz from a formatted message."""
    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ Please use this command by replying to the message that contains your questions.")
        return
    try:
        args_text = " ".join(context.args)
        title, time_limit_str = [arg.strip() for arg in args_text.split(';')]
        time_limit_minutes = int(time_limit_str)
    except (ValueError, IndexError):
        await update.message.reply_text("❌ **Invalid Format!** Use: `/addquiz <Title>; <TimeLimitMinutes>`", parse_mode=ParseMode.MARKDOWN)
        return
    questions_text = update.message.reply_to_message.text
    lines = questions_text.strip().split(';')
    new_questions = []
    for i, line in enumerate(lines):
        try:
            parts = line.split('+')
            question_text = parts[0].strip()
            options_part = parts[1].strip()
            correct_option_num = int(parts[2].strip())
            options = [opt.strip() for opt in options_part.split(',')]
            if not (1 <= correct_option_num <= len(options)):
                raise ValueError("Correct answer number is out of range.")
            new_questions.append({
                "question": question_text,
                "options": options,
                "correct_option_index": correct_option_num - 1
            })
        except (IndexError, ValueError) as e:
            await update.message.reply_text(f"❌ Error on line {i+1}: {e}.\nFormat: `Question+Opt1,Opt2+CorrectNum`")
            return
    if not new_questions:
        await update.message.reply_text("No valid questions found.")
        return
    
    s = re.sub(r'[^\w\s-]', '', title.lower())
    quiz_id = re.sub(r'[-\s]+', '_', s).strip('_')

    data = load_data()
    data['quizzes'][quiz_id] = {"title": title, "time_limit_minutes": time_limit_minutes, "questions": new_questions, "version": 1}
    save_data(data)
    await update.message.reply_text(f"✅ Quiz '{title}' created with ID `{quiz_id}`.", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def list_quizzes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists all created quizzes."""
    data = load_data()
    quizzes = data.get('quizzes', {})
    if not quizzes:
        await update.message.reply_text("No quizzes have been created yet.")
        return
    active_quiz_id = data.get('active_quiz_id')
    message = "📋 *Available Quizzes:*\n\n"
    for q_id, q_data in quizzes.items():
        active_status = " (🚀 ACTIVE)" if q_id == active_quiz_id else ""
        message += f"- *Title:* {q_data['title']}\n  *ID:* `{q_id}`{active_status}\n"
    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

@admin_only
async def set_active_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sets a quiz to be the active one."""
    try:
        quiz_id = context.args[0]
        data = load_data()
        if quiz_id not in data['quizzes']:
            await update.message.reply_text("❌ Quiz ID not found.")
            return
        data['active_quiz_id'] = quiz_id
        save_data(data)
        await update.message.reply_text(f"✅ *{data['quizzes'][quiz_id]['title']}* is now the active quiz.")
    except IndexError:
        await update.message.reply_text("Usage: `/setactive <QuizID>`")

@admin_only
async def update_version_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Increments a quiz version."""
    try:
        quiz_id = context.args[0]
        data = load_data()
        if quiz_id not in data['quizzes']:
            await update.message.reply_text("❌ Quiz ID not found.")
            return
        new_version = data['quizzes'][quiz_id].get("version", 1) + 1
        data['quizzes'][quiz_id]["version"] = new_version
        save_data(data)
        await update.message.reply_text(f"✅ Version for *{data['quizzes'][quiz_id]['title']}* updated to v{new_version}.")
    except IndexError:
        await update.message.reply_text("Usage: `/updateversion <QuizID>`")

@admin_only
async def view_scores_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Views scores for a specific quiz after cleaning old ones."""
    try:
        quiz_id_to_view = context.args[0]
        data = load_data()
        
        # Cleanup old scores
        now = datetime.now(timezone.utc)
        user_scores = data.get('user_scores', {})
        for user_id in list(user_scores.keys()):
            for quiz_id in list(user_scores[user_id].keys()):
                timestamp_str = user_scores[user_id][quiz_id].get("timestamp")
                if timestamp_str:
                    timestamp = datetime.fromisoformat(timestamp_str)
                    if now - timestamp > timedelta(days=SCORE_EXPIRATION_DAYS):
                        del data['user_scores'][user_id][quiz_id]
        save_data(data)

        # Display scores
        scores_text = f"📊 *Scores for {quiz_id_to_view}*:\n\n"
        found_scores = False
        for user_id, attempts in data.get('user_scores', {}).items():
            if quiz_id_to_view in attempts:
                attempt = attempts[quiz_id_to_view]
                name = attempt.get('name', f'User ID: {user_id}')
                scores_text += f"👤 *{name}*: {attempt['score']}/{attempt['total']}\n"
                found_scores = True
        
        if not found_scores:
            scores_text += "No scores recorded for this quiz yet."
            
        await update.message.reply_text(scores_text, parse_mode=ParseMode.MARKDOWN)
    except IndexError:
        await update.message.reply_text("Usage: `/viewscores <QuizID>`")

@admin_only
async def delete_quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Deletes a quiz permanently."""
    try:
        quiz_id = context.args[0]
        data = load_data()
        if quiz_id not in data['quizzes']:
            await update.message.reply_text("❌ Quiz ID not found.")
            return
        title = data['quizzes'][quiz_id]["title"]
        del data['quizzes'][quiz_id]
        if data.get("active_quiz_id") == quiz_id:
            data['active_quiz_id'] = None
        save_data(data)
        await update.message.reply_text(f"🗑 Quiz '{title}' has been permanently deleted.")
    except IndexError:
        await update.message.reply_text("Usage: `/deletequiz <QuizID>`")

# --- Interactive Quiz Logic ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /start command, checking for an active quiz."""
    data = load_data()
    active_quiz_id = data.get('active_quiz_id')
    if not active_quiz_id:
        await update.message.reply_text("There is no active quiz at the moment.")
        return
    quiz = data['quizzes'][active_quiz_id]
    user_id = str(update.effective_user.id)
    user_scores = data.get('user_scores', {}).get(user_id, {})
    if active_quiz_id in user_scores and user_scores[active_quiz_id].get("version") == quiz.get("version", 1):
        await update.message.reply_text(f"You have already completed the '{quiz['title']}' quiz.")
        return
    keyboard = [[InlineKeyboardButton("✅ Start Quiz", callback_data=f"startquiz_{active_quiz_id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"👋 Welcome, {update.effective_user.first_name}!\n\n"
        f"Ready for the *{quiz['title']}* quiz?\n"
        f"You will have *{quiz['time_limit_minutes']} minutes* to answer {len(quiz['questions'])} questions.",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

async def start_quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initializes and starts the quiz."""
    query = update.callback_query
    await query.answer()
    quiz_id = query.data.split('_')[1]
    data = load_data()
    quiz_data = data['quizzes'][quiz_id]
    context.user_data.update({
        'quiz_id': quiz_id,
        'current_question': 0,
        'score': 0,
        'quiz_data': quiz_data,
        'chat_id': update.effective_chat.id,
        'user_id': str(update.effective_user.id),
        'name': update.effective_user.first_name
    })
    await query.edit_message_text(text="Quiz starting... Good luck!")
    time_limit_seconds = quiz_data["time_limit_minutes"] * 60
    chat_id = update.effective_chat.id
    context.job_queue.run_once(quiz_timeout, time_limit_seconds, chat_id=chat_id, name=f"quiz_timer_{chat_id}", data=context.user_data.copy())
    await send_question(context)

async def send_question(context: ContextTypes.DEFAULT_TYPE):
    """Sends the current question with interactive buttons."""
    user_data = context.user_data
    q_index = user_data['current_question']
    quiz_data = user_data['quiz_data']
    chat_id = user_data['chat_id']
    if q_index >= len(quiz_data['questions']):
        await end_quiz(context)
        return
    question_data = quiz_data['questions'][q_index]
    keyboard = []
    for i, option in enumerate(question_data['options']):
        callback_data = f"answer_{q_index}_{i}"
        keyboard.append([InlineKeyboardButton(option, callback_data=callback_data)])
    reply_markup = InlineKeyboardMarkup(keyboard)
    question_text = f"*{q_index + 1}. {question_data['question']}*"
    await context.bot.send_message(chat_id, question_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles user's answer from a button press."""
    query = update.callback_query
    await query.answer()
    if not context.user_data:
        await query.edit_message_text("This quiz has already ended.")
        return
    data = query.data.split('_')
    q_index = int(data[1])
    chosen_opt_index = int(data[2])
    user_data = context.user_data
    quiz_data = user_data['quiz_data']
    if q_index != user_data['current_question']:
        await query.edit_message_text("This is an old question.")
        return
    correct_opt_index = quiz_data['questions'][q_index]['correct_option_index']
    feedback_text = "✅ Correct!" if chosen_opt_index == correct_opt_index else f"❌ Wrong! The correct answer was: {quiz_data['questions'][q_index]['options'][correct_opt_index]}"
    if chosen_opt_index == correct_opt_index:
        user_data['score'] += 1
    await query.edit_message_text(f"{query.message.text}\n\n_{feedback_text}_", parse_mode=ParseMode.MARKDOWN)
    user_data['current_question'] += 1
    await send_question(context)

async def end_quiz(context: ContextTypes.DEFAULT_TYPE):
    """Ends the quiz, saves the score, and shows the result."""
    user_data = context.user_data
    if not user_data: return
    chat_id = user_data['chat_id']
    jobs = context.job_queue.get_jobs_by_name(f"quiz_timer_{chat_id}")
    for job in jobs:
        job.schedule_removal()
    
    quiz_id = user_data['quiz_id']
    quiz_data = user_data['quiz_data']
    score = user_data['score']
    total = len(quiz_data['questions'])
    user_id = user_data['user_id']
    name = user_data['name']

    data = load_data()
    if user_id not in data['user_scores']:
        data['user_scores'][user_id] = {}
    data['user_scores'][user_id][quiz_id] = {"score": score, "total": total, "version": quiz_data.get("version", 1), "name": name, "timestamp": datetime.now(timezone.utc).isoformat()}
    save_data(data)
    
    await context.bot.send_message(chat_id, f"🎉 *Quiz Finished!* 🎉\n\nThanks, {name}!\nYour final score is *{score}* out of *{total}*.", parse_mode=ParseMode.MARKDOWN)
    user_data.clear()

async def quiz_timeout(context: ContextTypes.DEFAULT_TYPE):
    """Function called by the timer when time is up."""
    job_data = context.job.data
    context.user_data.update(job_data)
    await context.bot.send_message(job_data['chat_id'], "⌛️ *Time's up!*", parse_mode=ParseMode.MARKDOWN)
    await end_quiz(context)

def main() -> None:
    """Initializes and runs the bot."""
    # Create the data file if it doesn't exist
    if not os.path.exists(DATA_FILE):
        save_data({"quizzes": {}, "active_quiz_id": None, "user_scores": {}})

    application = Application.builder().token(BOT_TOKEN).build()
    
    # User handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(start_quiz_callback, pattern="^startquiz_"))
    application.add_handler(CallbackQueryHandler(answer_handler, pattern="^answer_"))
    
    # Admin handlers
    application.add_handler(CommandHandler("admin", admin_help_command))
    application.add_handler(CommandHandler("addquiz", add_quiz_command))
    application.add_handler(CommandHandler("listquizzes", list_quizzes_command))
    application.add_handler(CommandHandler("setactive", set_active_command))
    application.add_handler(CommandHandler("updateversion", update_version_command))
    application.add_handler(CommandHandler("viewscores", view_scores_command))
    application.add_handler(CommandHandler("deletequiz", delete_quiz_command))

    print("Bot is running with local file storage...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
