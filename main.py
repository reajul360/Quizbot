import logging
import json
import os
from datetime import datetime, timedelta, timezone

from telegram import Update, ReplyKeyboardRemove, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from telegram.constants import ParseMode

# --- Configuration ---
# WARNING: Do not share your bot token publicly.
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

# --- Conversation Handler States for User Quiz ---
GETTING_NAME, GETTING_ANSWERS = range(2)

# --- Data Management ---
def load_data():
    """Loads data from the JSON file."""
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"quizzes": {}, "active_quiz_id": None, "user_scores": {}}

def save_data(data):
    """Saves data to the JSON file."""
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

def cleanup_old_scores():
    """Removes scores older than SCORE_EXPIRATION_DAYS."""
    data = load_data()
    now = datetime.now(timezone.utc)
    # Use list() to create a copy of keys, as we'll be modifying the dictionary
    for user_id in list(data.get("user_scores", {}).keys()):
        for quiz_id in list(data["user_scores"][user_id].keys()):
            timestamp_str = data["user_scores"][user_id][quiz_id].get("timestamp")
            if timestamp_str:
                timestamp = datetime.fromisoformat(timestamp_str)
                if now - timestamp > timedelta(days=SCORE_EXPIRATION_DAYS):
                    del data["user_scores"][user_id][quiz_id]
                    logger.info(f"Expired score for user {user_id} on quiz {quiz_id} deleted.")
    save_data(data)

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

Use the following commands to manage the quizzes:

1️⃣ `/addquiz <Title>; <TimeLimitMinutes>`
Reply to a message containing the questions to add a new quiz.
*Example:* `/addquiz English Test 1; 20`

2️⃣ `/setactive <QuizID>`
Sets a quiz as the one users can take.
*Example:* `/setactive english_test_1`

3️⃣ `/updateversion <QuizID>`
Updates a quiz version, allowing all users to retake it.
*Example:* `/updateversion english_test_1`

4️⃣ `/listquizzes`
Shows all available quizzes and their IDs.

5️⃣ `/viewscores <QuizID>`
Shows all scores for a specific quiz.
*Example:* `/viewscores english_test_1`

6️⃣ `/deletequiz <QuizID>`
Permanently deletes a quiz.
*Example:* `/deletequiz english_test_1`
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
        if time_limit_minutes <= 0: raise ValueError
    except (ValueError, IndexError):
        await update.message.reply_text("❌ **Invalid Format!**\nUse the command like this:\n`/addquiz <Title>; <TimeLimitMinutes>`\n\n*Example:* `/addquiz English Test 1; 20`", parse_mode=ParseMode.MARKDOWN)
        return

    questions_text = update.message.reply_to_message.text
    lines = questions_text.strip().split('\n')
    new_questions = []
    
    for i, line in enumerate(lines):
        try:
            parts = line.split('+')
            question_text = parts[0].strip()
            options_part = parts[1].strip()
            correct_option_num = int(parts[2].strip())

            options = [opt.strip() for opt in options_part.split(',')]
            if not (1 <= correct_option_num <= len(options)):
                await update.message.reply_text(f"Error in question {i+1}: Correct answer number is out of range.")
                return

            new_questions.append({
                "question": question_text,
                "options": options,
                "correct_option_index": correct_option_num - 1 # Store as 0-based index
            })
        except (IndexError, ValueError):
            await update.message.reply_text(f"❌ Error parsing question on line {i+1}.\nPlease check the format: `Question+Opt1,Opt2,Opt3+CorrectNum`")
            return

    if not new_questions:
        await update.message.reply_text("No valid questions were found. Quiz not created.")
        return

    quiz_id = title.lower().replace(' ', '_')
    data = load_data()
    data["quizzes"][quiz_id] = {
        "title": title,
        "time_limit_minutes": time_limit_minutes,
        "questions": new_questions,
        "version": 1
    }
    save_data(data)
    
    await update.message.reply_text(f"✅ Quiz '{title}' created successfully with ID `{quiz_id}`.\nIt has {len(new_questions)} questions and a {time_limit_minutes}-minute time limit.", parse_mode=ParseMode.MARKDOWN)

@admin_only
async def list_quizzes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists all created quizzes."""
    data = load_data()
    quizzes = data.get("quizzes", {})
    if not quizzes:
        await update.message.reply_text("No quizzes have been created yet.")
        return

    active_quiz_id = data.get("active_quiz_id")
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
        if quiz_id not in data["quizzes"]:
            await update.message.reply_text("❌ Quiz ID not found.")
            return
        data["active_quiz_id"] = quiz_id
        save_data(data)
        await update.message.reply_text(f"✅ *{data['quizzes'][quiz_id]['title']}* is now the active quiz.")
    except IndexError:
        await update.message.reply_text("Usage: `/setactive <QuizID>`")

@admin_only
async def update_version_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Increments a quiz version, allowing users to retake it."""
    try:
        quiz_id = context.args[0]
        data = load_data()
        if quiz_id not in data["quizzes"]:
            await update.message.reply_text("❌ Quiz ID not found.")
            return
        
        current_version = data["quizzes"][quiz_id].get("version", 1)
        new_version = current_version + 1
        data["quizzes"][quiz_id]["version"] = new_version
        save_data(data)
        
        await update.message.reply_text(f"✅ Version for *{data['quizzes'][quiz_id]['title']}* updated to v{new_version}. Users can now retake it.")
    except IndexError:
        await update.message.reply_text("Usage: `/updateversion <QuizID>`")

@admin_only
async def view_scores_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Views scores for a specific quiz."""
    cleanup_old_scores() # Clean expired scores before viewing
    try:
        quiz_id = context.args[0]
        data = load_data()
        if quiz_id not in data["quizzes"]:
            await update.message.reply_text("❌ Quiz ID not found.")
            return

        scores_text = f"📊 *Scores for {data['quizzes'][quiz_id]['title']}*:\n\n"
        found_scores = False
        user_scores = data.get("user_scores", {})
        
        for user_id, attempts in user_scores.items():
            if quiz_id in attempts:
                attempt = attempts[quiz_id]
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
        if quiz_id not in data["quizzes"]:
            await update.message.reply_text("❌ Quiz ID not found.")
            return
        
        title = data["quizzes"][quiz_id]["title"]
        del data["quizzes"][quiz_id]
        
        # Also remove it as active if it was
        if data.get("active_quiz_id") == quiz_id:
            data["active_quiz_id"] = None
            
        save_data(data)
        await update.message.reply_text(f"🗑 Quiz '{title}' has been permanently deleted.")
    except IndexError:
        await update.message.reply_text("Usage: `/deletequiz <QuizID>`")


# --- User Quiz Conversation ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the quiz process for a user."""
    data = load_data()
    active_quiz_id = data.get("active_quiz_id")

    if not active_quiz_id or active_quiz_id not in data["quizzes"]:
        await update.message.reply_text("There is no active quiz at the moment. Please check back later!")
        return ConversationHandler.END

    quiz = data["quizzes"][active_quiz_id]
    user_id = str(update.effective_user.id)

    # Check if user has already taken this version
    user_attempts = data.get("user_scores", {}).get(user_id, {})
    if active_quiz_id in user_attempts and user_attempts[active_quiz_id].get("version") == quiz.get("version", 1):
        await update.message.reply_text(f"You have already completed the '{quiz['title']}' quiz. You can only take it again if the admin updates it.")
        return ConversationHandler.END

    context.user_data["quiz_id"] = active_quiz_id
    
    reply_keyboard = [["Yes, let's start!"], ["No, cancel."]]
    await update.message.reply_text(
        f"👋 Welcome!\n\nReady to take the *{quiz['title']}* quiz?\n"
        f"You will have *{quiz['time_limit_minutes']} minutes* to answer all questions.",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True),
        parse_mode=ParseMode.MARKDOWN
    )
    
    return GETTING_NAME

async def ask_for_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Asks the user for their name."""
    await update.message.reply_text("Great! First, please tell me your full name.", reply_markup=ReplyKeyboardRemove())
    return GETTING_ANSWERS

async def start_quiz_proper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives name, sends questions, and starts the timer."""
    user_name = update.message.text
    context.user_data["user_name"] = user_name
    quiz_id = context.user_data["quiz_id"]
    
    data = load_data()
    quiz = data["quizzes"][quiz_id]
    
    questions_text = f"Here are your questions, {user_name}. Good luck!\n\n"
    for i, q in enumerate(quiz["questions"]):
        options_str = "\n".join([f"  {chr(65+j)}. {opt}" for j, opt in enumerate(q["options"])])
        questions_text += f"*{i+1}. {q['question']}*\n{options_str}\n\n"
    
    questions_text += f"⏳ You have {quiz['time_limit_minutes']} minutes.\n\n"
    questions_text += "*Reply to this message* with your answers in the format: `1-A, 2-C, 3-B`"

    await update.message.reply_text(questions_text, parse_mode=ParseMode.MARKDOWN)

    # Start timer
    time_limit_seconds = quiz["time_limit_minutes"] * 60
    context.job_queue.run_once(quiz_timeout, time_limit_seconds, chat_id=update.effective_chat.id, name=f"quiz_{update.effective_chat.id}")

    return ConversationHandler.END # End conversation here, but wait for a reply message

async def quiz_timeout(context: ContextTypes.DEFAULT_TYPE):
    """Function called by the timer when time is up."""
    await context.bot.send_message(chat_id=context.job.chat_id, text="⌛️ *Time's up!* Your quiz has ended. If you sent your answers, they will be graded. Otherwise, you get a score of 0.")
    # We don't need to do much here, the answer handler will check if a submission was made.

async def process_answers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes the user's reply with their answers."""
    if not update.message.reply_to_message or "Reply to this message" not in update.message.reply_to_message.text:
        # This is a regular message, not an answer submission. Ignore it.
        return

    # A quiz was submitted, so we can remove the timer job
    jobs = context.job_queue.get_jobs_by_name(f"quiz_{update.effective_chat.id}")
    for job in jobs:
        job.schedule_removal()

    user_id = str(update.effective_user.id)
    data = load_data()
    active_quiz_id = data.get("active_quiz_id")
    
    if not active_quiz_id:
        await update.message.reply_text("Sorry, the quiz is no longer active.")
        return

    quiz = data["quizzes"][active_quiz_id]
    user_answers_raw = update.message.text.split(',')
    
    user_answers = {}
    for ans in user_answers_raw:
        try:
            q_num, ans_letter = [a.strip() for a in ans.split('-')]
            user_answers[int(q_num) - 1] = ord(ans_letter.upper()) - 65
        except (ValueError, IndexError):
            # Ignore malformed answer parts
            continue

    score = 0
    for i, question in enumerate(quiz["questions"]):
        if user_answers.get(i) == question["correct_option_index"]:
            score += 1
    
    # Save score
    if user_id not in data["user_scores"]:
        data["user_scores"][user_id] = {}
        
    user_name = context.user_data.get("user_name", update.effective_user.first_name)
    
    data["user_scores"][user_id][active_quiz_id] = {
        "score": score,
        "total": len(quiz["questions"]),
        "version": quiz.get("version", 1),
        "name": user_name,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    save_data(data)
    
    await update.message.reply_text(f"🎉 *Quiz Finished!* 🎉\n\nThanks, {user_name}!\nYour score is *{score}* out of *{len(quiz['questions'])}*.", parse_mode=ParseMode.MARKDOWN)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    await update.message.reply_text("Quiz cancelled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


def main() -> None:
    """Start the bot."""
    # Ensure data file exists
    if not os.path.exists(DATA_FILE):
        save_data({"quizzes": {}, "active_quiz_id": None, "user_scores": {}})

    application = Application.builder().token(BOT_TOKEN).build()

    # Conversation handler for the user taking a quiz
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start_command)],
        states={
            GETTING_NAME: [MessageHandler(filters.Regex("^Yes, let's start!$"), ask_for_name)],
            GETTING_ANSWERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, start_quiz_proper)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex("^No, cancel.$"), cancel)
        ],
    )
    
    # Add handlers
    application.add_handler(conv_handler)
    application.add_handler(MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, process_answers))
    
    # Admin handlers
    application.add_handler(CommandHandler("admin", admin_help_command))
    application.add_handler(CommandHandler("addquiz", add_quiz_command))
    application.add_handler(CommandHandler("listquizzes", list_quizzes_command))
    application.add_handler(CommandHandler("setactive", set_active_command))
    application.add_handler(CommandHandler("updateversion", update_version_command))
    application.add_handler(CommandHandler("viewscores", view_scores_command))
    application.add_handler(CommandHandler("deletequiz", delete_quiz_command))


    # Run the bot until the user presses Ctrl-C
    print("Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main(
