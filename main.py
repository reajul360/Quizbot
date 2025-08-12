import logging
import json
import os
from datetime import timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Poll
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    PollAnswerHandler,
)
from telegram.constants import ParseMode

# --- Configuration ---
# WARNING: Do not share your bot token publicly.
# It's best to use environment variables for security.
BOT_TOKEN = "8281350439:AAH61nGOiyiaOvmWZ_yKroVeTCavv6BEAA8" # Replace with your bot token
OWNER_ID = 7919870032  # Replace with your Telegram User ID
DATA_FILE = "quiz_data.json"

# --- Logging Setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- Conversation Handler States ---
# For the admin question setup
(
    SELECTING_ACTION,
    ADD_QUIZ_TITLE,
    ADD_QUESTION,
    ADD_OPTIONS,
    ADD_CORRECT_ANSWER,
    ADD_TIME_LIMIT,
    SET_ACTIVE_QUIZ,
    UPDATE_QUIZ_VERSION,
) = range(8)

# --- Data Management ---
def load_data():
    """Loads data from the JSON file."""
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # Return a default structure if file doesn't exist or is empty
        return {
            "quizzes": {},
            "active_quiz_id": None,
            "user_data": {},
        }

def save_data(data):
    """Saves data to the JSON file."""
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

# --- Admin Decorator ---
def admin_only(func):
    """Decorator to restrict access to the bot owner."""
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id != OWNER_ID:
            await update.message.reply_text("Sorry, this is an admin-only command.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

# --- Admin Panel Commands ---
@admin_only
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Displays the main admin panel."""
    keyboard = [
        [InlineKeyboardButton("➕ Add/Edit Quiz", callback_data="admin_add_quiz")],
        [InlineKeyboardButton("🚀 Set Active Quiz", callback_data="admin_set_active")],
        [InlineKeyboardButton("🔄 Update Quiz Version", callback_data="admin_update_version")],
        [InlineKeyboardButton("📊 View Scores", callback_data="admin_view_scores")],
        [InlineKeyboardButton("❌ Close Panel", callback_data="admin_close")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("👑 *Admin Panel*\n\nWelcome, owner! What would you like to do?", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    return SELECTING_ACTION

# --- User Commands ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /start command for users."""
    user = update.effective_user
    data = load_data()
    active_quiz_id = data.get("active_quiz_id")

    await update.message.reply_text(
        f"👋 Welcome, {user.first_name}!\n\nI am your friendly Quiz Bot."
    )

    if not active_quiz_id or active_quiz_id not in data["quizzes"]:
        await update.message.reply_text("There is no active quiz at the moment. Please check back later!")
        return

    quiz = data["quizzes"][active_quiz_id]
    quiz_title = quiz["title"]
    time_limit = quiz["time_limit_minutes"]
    num_questions = len(quiz["questions"])
    
    # Check if user has already taken this version of the quiz
    user_id_str = str(user.id)
    if user_id_str in data["user_data"]:
        user_attempts = data["user_data"][user_id_str].get("quiz_attempts", {})
        if active_quiz_id in user_attempts and user_attempts[active_quiz_id].get("version") == quiz.get("version", 1):
             await update.message.reply_text(f"You have already completed the '{quiz_title}' quiz. You can only take it again if the admin updates it.")
             return

    keyboard = [[InlineKeyboardButton("✅ Start Quiz", callback_data=f"start_quiz_{active_quiz_id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"Ready for a challenge?\n\n🎓 *Quiz:* {quiz_title}\n"
        f"❓ *Questions:* {num_questions}\n"
        f"⏳ *Time Limit:* {time_limit} minutes\n\n"
        "Click the button below to begin!",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN
    )

# --- Quiz Logic ---
async def start_quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback to start the quiz for a user."""
    query = update.callback_query
    await query.answer()

    quiz_id = query.data.split("_")[-1]
    user_id = str(update.effective_user.id)
    
    data = load_data()
    
    if user_id not in data["user_data"]:
        data["user_data"][user_id] = {"quiz_attempts": {}, "current_quiz": {}}

    # Initialize quiz state for the user
    data["user_data"][user_id]["current_quiz"] = {
        "quiz_id": quiz_id,
        "current_question_index": 0,
        "score": 0,
        "answers": []
    }
    
    quiz = data["quizzes"][quiz_id]
    time_limit_seconds = quiz["time_limit_minutes"] * 60
    
    # Schedule the timer to end the quiz
    job = context.job_queue.run_once(end_quiz_timer, when=time_limit_seconds, data={"user_id": user_id, "chat_id": query.message.chat_id}, name=f"quiz_timer_{user_id}")
    data["user_data"][user_id]["current_quiz"]["timer_job_id"] = job.id

    save_data(data)
    
    await query.edit_message_text("The quiz has started! Good luck. The first question is below.")
    await send_question(update.effective_chat.id, user_id, context)


async def send_question(chat_id: int, user_id: str, context: ContextTypes.DEFAULT_TYPE):
    """Sends the current question to the user as a poll."""
    data = load_data()
    user_quiz_state = data["user_data"][user_id]["current_quiz"]
    quiz_id = user_quiz_state["quiz_id"]
    question_index = user_quiz_state["current_question_index"]
    quiz = data["quizzes"][quiz_id]

    if question_index >= len(quiz["questions"]):
        # This case should be handled by the answer handler, but as a fallback
        await end_quiz(user_id, chat_id, context)
        return

    question_data = quiz["questions"][question_index]
    
    message = await context.bot.send_poll(
        chat_id=chat_id,
        question=f"Question {question_index + 1}/{len(quiz['questions'])}:\n\n{question_data['question']}",
        options=question_data["options"],
        type=Poll.QUIZ,
        correct_option_id=question_data["correct_option_id"],
        is_anonymous=False, # Important for tracking answers
        explanation=f"Let's see how you did!",
        explanation_parse_mode=ParseMode.MARKDOWN
    )

    # Store poll ID to link answer to the user
    context.bot_data.setdefault("poll_data", {})[message.poll.id] = {
        "user_id": user_id,
        "chat_id": chat_id,
        "message_id": message.message_id
    }


async def receive_poll_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles a user's answer to a quiz poll."""
    answer = update.poll_answer
    poll_id = answer.poll_id
    
    poll_data_map = context.bot_data.get("poll_data", {})
    if poll_id not in poll_data_map:
        return

    poll_info = poll_data_map[poll_id]
    user_id = str(poll_info["user_id"])
    chat_id = poll_info["chat_id"]
    
    data = load_data()
    user_quiz_state = data["user_data"][user_id]["current_quiz"]
    quiz_id = user_quiz_state["quiz_id"]
    question_index = user_quiz_state["current_question_index"]
    quiz = data["quizzes"][quiz_id]
    
    correct_option = quiz["questions"][question_index]["correct_option_id"]
    
    # Check if the answer is correct
    if answer.option_ids and answer.option_ids[0] == correct_option:
        user_quiz_state["score"] += 1
        
    user_quiz_state["current_question_index"] += 1
    save_data(data)
    
    # Check if quiz is over
    if user_quiz_state["current_question_index"] >= len(quiz["questions"]):
        await end_quiz(user_id, chat_id, context)
    else:
        # Send the next question
        await send_question(chat_id, user_id, context)

async def end_quiz(user_id: str, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Finalizes the quiz, calculates score, and saves it."""
    data = load_data()
    
    if "current_quiz" not in data["user_data"][user_id] or not data["user_data"][user_id]["current_quiz"]:
        # Quiz already ended, possibly by timer
        return

    # Remove timer job if it exists
    job_id = data["user_data"][user_id]["current_quiz"].get("timer_job_id")
    if job_id:
        current_jobs = context.job_queue.get_jobs_by_name(job_id)
        for job in current_jobs:
            job.schedule_removal()

    user_quiz_state = data["user_data"][user_id]["current_quiz"]
    quiz_id = user_quiz_state["quiz_id"]
    score = user_quiz_state["score"]
    quiz = data["quizzes"][quiz_id]
    total_questions = len(quiz["questions"])
    
    # Save the final attempt
    data["user_data"][user_id]["quiz_attempts"][quiz_id] = {
        "score": score,
        "total": total_questions,
        "version": quiz.get("version", 1)
    }
    
    # Clear the current quiz state
    data["user_data"][user_id]["current_quiz"] = {}
    save_data(data)
    
    await context.bot.send_message(
        chat_id,
        f"🎉 *Quiz Finished!* 🎉\n\n"
        f"You scored *{score}* out of *{total_questions}*.\n\n"
        "Thanks for participating!",
        parse_mode=ParseMode.MARKDOWN
    )

async def end_quiz_timer(context: ContextTypes.DEFAULT_TYPE):
    """Job function called by the timer to end the quiz."""
    job_data = context.job.data
    user_id = str(job_data["user_id"])
    chat_id = job_data["chat_id"]
    
    await context.bot.send_message(chat_id, "⌛️ *Time's up!* The quiz has ended.", parse_mode=ParseMode.MARKDOWN)
    await end_quiz(user_id, chat_id, context)

# --- Admin Conversation Handler Functions ---
async def admin_add_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Asks for the new quiz title."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Please send me the title for the new quiz (e.g., 'English Grammar Test 1'). Or send /cancel to stop.")
    context.user_data['new_quiz'] = {'questions': []}
    return ADD_QUIZ_TITLE

async def received_quiz_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives quiz title and asks for the first question."""
    title = update.message.text
    context.user_data['new_quiz']['title'] = title
    await update.message.reply_text(f"Great! Quiz title is '{title}'. Now, please send the first question. Send /done when you have added all questions.")
    return ADD_QUESTION

async def received_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives a question and asks for options."""
    question_text = update.message.text
    context.user_data['current_question'] = {'question': question_text}
    await update.message.reply_text("Question received. Now send the options, separated by commas.\n\n*Example:* Option A, Option B, Option C, Option D")
    return ADD_OPTIONS

async def received_options(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives options and asks for the correct answer index."""
    options = [opt.strip() for opt in update.message.text.split(',')]
    if len(options) < 2:
        await update.message.reply_text("Please provide at least two options, separated by commas. Try again.")
        return ADD_OPTIONS
        
    context.user_data['current_question']['options'] = options
    await update.message.reply_text(f"Options received: {', '.join(options)}. Now, please send the number of the *correct* option (e.g., 1 for the first option, 2 for the second, etc.).")
    return ADD_CORRECT_ANSWER

async def received_correct_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives correct answer, saves the question, and asks for the next one."""
    try:
        # User sends 1-based index, we need 0-based for the poll
        correct_index = int(update.message.text) - 1
        options_len = len(context.user_data['current_question']['options'])
        if not 0 <= correct_index < options_len:
            raise ValueError
    except (ValueError, TypeError):
        await update.message.reply_text("Invalid input. Please send a number corresponding to the correct option. Try again.")
        return ADD_CORRECT_ANSWER

    context.user_data['current_question']['correct_option_id'] = correct_index
    context.user_data['new_quiz']['questions'].append(context.user_data['current_question'])
    context.user_data.pop('current_question', None)
    
    await update.message.reply_text("Question saved! Send the next question, or type /done to finish.")
    return ADD_QUESTION

async def done_adding_questions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Finishes adding questions and asks for time limit."""
    if not context.user_data.get('new_quiz', {}).get('questions'):
        await update.message.reply_text("You haven't added any questions! Please add at least one question or /cancel.")
        return ADD_QUESTION
        
    await update.message.reply_text("All questions added. Now, please enter the time limit for the quiz in minutes (e.g., 20).")
    return ADD_TIME_LIMIT

async def received_time_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives time limit, saves the quiz, and ends conversation."""
    try:
        time_limit = int(update.message.text)
        if time_limit <= 0: raise ValueError
    except (ValueError, TypeError):
        await update.message.reply_text("Invalid input. Please send a positive number for the minutes. Try again.")
        return ADD_TIME_LIMIT

    new_quiz = context.user_data['new_quiz']
    new_quiz['time_limit_minutes'] = time_limit
    new_quiz['version'] = 1 # Initial version
    
    # Generate a unique ID for the quiz
    quiz_id = new_quiz['title'].lower().replace(' ', '_')
    
    data = load_data()
    data['quizzes'][quiz_id] = new_quiz
    save_data(data)
    
    await update.message.reply_text(f"✅ Quiz '{new_quiz['title']}' has been created successfully with {len(new_quiz['questions'])} questions and a {time_limit}-minute time limit.")
    
    context.user_data.clear()
    await admin_command(update, context) # Show admin panel again
    return ConversationHandler.END

async def admin_set_active(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Shows a list of quizzes to set as active."""
    query = update.callback_query
    await query.answer()
    data = load_data()
    quizzes = data.get("quizzes", {})
    if not quizzes:
        await query.edit_message_text("No quizzes found. Please add a quiz first.")
        return SELECTING_ACTION

    keyboard = [[InlineKeyboardButton(q_data["title"], callback_data=f"setactive_{q_id}")] for q_id, q_data in quizzes.items()]
    keyboard.append([InlineKeyboardButton("« Back", callback_data="admin_back")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Select a quiz to make active:", reply_markup=reply_markup)
    return SET_ACTIVE_QUIZ

async def set_active_quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Callback to set the chosen quiz as active."""
    query = update.callback_query
    await query.answer()
    quiz_id = query.data.split("_")[-1]
    
    data = load_data()
    data["active_quiz_id"] = quiz_id
    save_data(data)
    
    await query.edit_message_text(f"✅ *{data['quizzes'][quiz_id]['title']}* is now the active quiz!")
    # Show admin panel again after a delay
    await admin_command(query, context)
    return SELECTING_ACTION

async def admin_update_version(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Shows a list of quizzes to update their version."""
    query = update.callback_query
    await query.answer()
    data = load_data()
    quizzes = data.get("quizzes", {})
    if not quizzes:
        await query.edit_message_text("No quizzes found.")
        return SELECTING_ACTION

    keyboard = [[InlineKeyboardButton(f"{q_data['title']} (v{q_data.get('version', 1)})", callback_data=f"updver_{q_id}")] for q_id, q_data in quizzes.items()]
    keyboard.append([InlineKeyboardButton("« Back", callback_data="admin_back")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Select a quiz to update its version (this will allow users to retake it):", reply_markup=reply_markup)
    return UPDATE_QUIZ_VERSION

async def update_version_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Callback to increment the version of a quiz."""
    query = update.callback_query
    await query.answer()
    quiz_id = query.data.split("_")[-1]
    
    data = load_data()
    current_version = data["quizzes"][quiz_id].get("version", 1)
    data["quizzes"][quiz_id]["version"] = current_version + 1
    save_data(data)
    
    await query.edit_message_text(f"✅ Version for *{data['quizzes'][quiz_id]['title']}* updated to v{current_version + 1}. Users can now retake this quiz.")
    await admin_command(query, context)
    return SELECTING_ACTION


async def admin_view_scores(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Displays scores for the active quiz."""
    query = update.callback_query
    await query.answer()
    data = load_data()
    active_quiz_id = data.get("active_quiz_id")

    if not active_quiz_id:
        await query.edit_message_text("No quiz is currently active.")
        return SELECTING_ACTION

    scores_text = f"Scores for *{data['quizzes'][active_quiz_id]['title']}*:\n\n"
    found_scores = False
    for user_id, user_data in data["user_data"].items():
        if active_quiz_id in user_data.get("quiz_attempts", {}):
            attempt = user_data["quiz_attempts"][active_quiz_id]
            scores_text += f"User ID: `{user_id}` - Score: {attempt['score']}/{attempt['total']}\n"
            found_scores = True
    
    if not found_scores:
        scores_text += "No scores recorded for this quiz yet."
        
    keyboard = [[InlineKeyboardButton("« Back", callback_data="admin_back")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(scores_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    return SELECTING_ACTION

async def admin_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Returns to the main admin menu."""
    query = update.callback_query
    await query.answer()
    # Re-display the admin panel by calling the initial command function logic
    keyboard = [
        [InlineKeyboardButton("➕ Add/Edit Quiz", callback_data="admin_add_quiz")],
        [InlineKeyboardButton("🚀 Set Active Quiz", callback_data="admin_set_active")],
        [InlineKeyboardButton("🔄 Update Quiz Version", callback_data="admin_update_version")],
        [InlineKeyboardButton("📊 View Scores", callback_data="admin_view_scores")],
        [InlineKeyboardButton("❌ Close Panel", callback_data="admin_close")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("👑 *Admin Panel*\n\nWelcome, owner! What would you like to do?", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    return SELECTING_ACTION

async def admin_close_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Closes the admin panel."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Admin panel closed.")
    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the current conversation."""
    await update.message.reply_text("Operation cancelled.")
    context.user_data.clear()
    await admin_command(update, context) # Show admin panel again
    return ConversationHandler.END

def main() -> None:
    """Start the bot."""
    # Ensure data file exists
    if not os.path.exists(DATA_FILE):
        save_data({
            "quizzes": {},
            "active_quiz_id": None,
            "user_data": {},
        })

    application = Application.builder().token(BOT_TOKEN).build()

    # Conversation handler for the admin panel
    admin_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("admin", admin_command)],
        states={
            SELECTING_ACTION: [
                CallbackQueryHandler(admin_add_quiz, pattern="^admin_add_quiz$"),
                CallbackQueryHandler(admin_set_active, pattern="^admin_set_active$"),
                CallbackQueryHandler(admin_update_version, pattern="^admin_update_version$"),
                CallbackQueryHandler(admin_view_scores, pattern="^admin_view_scores$"),
                CallbackQueryHandler(admin_close_panel, pattern="^admin_close$"),
            ],
            ADD_QUIZ_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_quiz_title)],
            ADD_QUESTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_question),
                CommandHandler("done", done_adding_questions)
            ],
            ADD_OPTIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_options)],
            ADD_CORRECT_ANSWER: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_correct_answer)],
            ADD_TIME_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_time_limit)],
            SET_ACTIVE_QUIZ: [
                CallbackQueryHandler(set_active_quiz_callback, pattern="^setactive_"),
                CallbackQueryHandler(admin_back_callback, pattern="^admin_back$")
            ],
            UPDATE_QUIZ_VERSION: [
                CallbackQueryHandler(update_version_callback, pattern="^updver_"),
                CallbackQueryHandler(admin_back_callback, pattern="^admin_back$")
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_conversation),
            CallbackQueryHandler(admin_back_callback, pattern="^admin_back$"),
            CallbackQueryHandler(admin_close_panel, pattern="^admin_close$"),
        ],
        per_user=True,
        per_chat=True,
    )

    application.add_handler(admin_conv_handler)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CallbackQueryHandler(start_quiz_callback, pattern="^start_quiz_"))
    application.add_handler(PollAnswerHandler(receive_poll_answer))

    # Run the bot until the user presses Ctrl-C
    print("Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

