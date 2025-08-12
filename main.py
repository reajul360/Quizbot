import logging
import json
import os
import re
from datetime import datetime, timezone, timedelta

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    PollAnswerHandler,
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
        return {"quizzes": {}, "active_quiz_id": None, "user_scores": {}, "user_states": {}}

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

# --- Admin Commands (No changes needed) ---
@admin_only
async def admin_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = "..." # Same as before
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

@admin_only
async def add_quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    try:
        quiz_id_to_view = context.args[0]
        data = load_data()
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

# --- Poll-Based Quiz Logic ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /start command and begins the quiz."""
    chat_id = update.effective_chat.id
    user_id = str(update.effective_user.id)
    data = load_data()
    active_quiz_id = data.get('active_quiz_id')

    if not active_quiz_id:
        await update.message.reply_text("There is no active quiz at the moment.")
        return

    # Check if user is already in a quiz
    if data.get('user_states', {}).get(user_id):
        await update.message.reply_text("You are already in the middle of a quiz!")
        return

    quiz = data['quizzes'][active_quiz_id]
    user_scores = data.get('user_scores', {}).get(user_id, {})
    if active_quiz_id in user_scores and user_scores[active_quiz_id].get("version") == quiz.get("version", 1):
        await update.message.reply_text(f"You have already completed the '{quiz['title']}' quiz.")
        return

    # Initialize user state
    user_state = {
        'quiz_id': active_quiz_id,
        'current_question': 0,
        'score': 0,
        'name': update.effective_user.first_name
    }
    if 'user_states' not in data:
        data['user_states'] = {}
    data['user_states'][user_id] = user_state
    save_data(data)

    await update.message.reply_text(f"👋 Welcome, {user_state['name']}!\n\nThe quiz '{quiz['title']}' is starting now. Good luck!")
    
    # Start timer
    time_limit_seconds = quiz["time_limit_minutes"] * 60
    context.job_queue.run_once(quiz_timeout, time_limit_seconds, chat_id=chat_id, user_id=user_id, name=f"quiz_timer_{user_id}")

    await send_poll_question(context, user_id=user_id, chat_id=chat_id)

async def send_poll_question(context: ContextTypes.DEFAULT_TYPE, user_id: str, chat_id: int):
    """Sends the current question as a poll."""
    data = load_data()
    user_state = data.get('user_states', {}).get(user_id)
    if not user_state:
        return # Quiz ended or state lost

    quiz_id = user_state['quiz_id']
    q_index = user_state['current_question']
    quiz = data['quizzes'][quiz_id]

    if q_index >= len(quiz['questions']):
        await end_quiz(context, user_id, chat_id)
        return

    question_data = quiz['questions'][q_index]
    
    message = await context.bot.send_poll(
        chat_id=chat_id,
        question=f"Question {q_index + 1}/{len(quiz['questions'])}: {question_data['question']}",
        options=question_data['options'],
        type="quiz",
        correct_option_id=question_data['correct_option_index'],
        is_anonymous=False, # Must be False for quiz mode
    )
    
    # Save poll_id to link it to the user
    context.bot_data.setdefault(message.poll.id, {})['user_id'] = user_id

async def receive_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles a user's answer to a poll."""
    poll_id = update.poll_answer.poll_id
    user_id = str(update.poll_answer.user.id)
    
    # Check if the poll belongs to an active quiz for this user
    bot_poll_data = context.bot_data.get(poll_id)
    if not bot_poll_data or bot_poll_data.get('user_id') != user_id:
        return

    data = load_data()
    user_state = data.get('user_states', {}).get(user_id)
    if not user_state:
        return
        
    quiz_id = user_state['quiz_id']
    q_index = user_state['current_question']
    quiz = data['quizzes'][quiz_id]
    
    correct_option_id = quiz['questions'][q_index]['correct_option_index']
    
    if update.poll_answer.option_ids and update.poll_answer.option_ids[0] == correct_option_id:
        user_state['score'] += 1

    user_state['current_question'] += 1
    save_data(data)
    
    await send_poll_question(context, user_id=user_id, chat_id=update.poll_answer.user.id)

async def end_quiz(context: ContextTypes.DEFAULT_TYPE, user_id: str, chat_id: int):
    """Ends the quiz, saves the score, and cleans up."""
    data = load_data()
    user_state = data.get('user_states', {}).get(user_id)
    if not user_state:
        return

    # Remove timer job
    jobs = context.job_queue.get_jobs_by_name(f"quiz_timer_{user_id}")
    for job in jobs:
        job.schedule_removal()
    
    quiz_id = user_state['quiz_id']
    quiz_data = data['quizzes'][quiz_id]
    score = user_state['score']
    total = len(quiz_data['questions'])
    name = user_state['name']

    # Save score
    if 'user_scores' not in data:
        data['user_scores'] = {}
    if user_id not in data['user_scores']:
        data['user_scores'][user_id] = {}
        
    data['user_scores'][user_id][quiz_id] = {
        "score": score,
        "total": total,
        "version": quiz_data.get("version", 1),
        "name": name,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    # Clean up user state
    del data['user_states'][user_id]
    save_data(data)

    await context.bot.send_message(chat_id, f"🎉 *Quiz Finished!* 🎉\n\nThanks, {name}!\nYour final score is *{score}* out of *{total}*.", parse_mode=ParseMode.MARKDOWN)

async def quiz_timeout(context: ContextTypes.DEFAULT_TYPE):
    """Function called by the timer when time is up."""
    user_id = str(context.job.user_id)
    chat_id = context.job.chat_id
    await context.bot.send_message(chat_id, "⌛️ *Time's up!*", parse_mode=ParseMode.MARKDOWN)
    await end_quiz(context, user_id, chat_id)

def main() -> None:
    """Initializes and runs the bot."""
    if not os.path.exists(DATA_FILE):
        save_data({"quizzes": {}, "active_quiz_id": None, "user_scores": {}, "user_states": {}})

    application = Application.builder().token(BOT_TOKEN).build()
    
    # User handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(PollAnswerHandler(receive_poll_answer))
    
    # Admin handlers (add them back)
    application.add_handler(CommandHandler("admin", admin_help_command))
    application.add_handler(CommandHandler("addquiz", add_quiz_command))
    application.add_handler(CommandHandler("listquizzes", list_quizzes_command))
    application.add_handler(CommandHandler("setactive", set_active_command))
    application.add_handler(CommandHandler("updateversion", update_version_command))
    application.add_handler(CommandHandler("viewscores", view_scores_command))
    application.add_handler(CommandHandler("deletequiz", delete_quiz_command))

    print("Bot is running with Telegram Poll quiz mode...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
