import telebot
import json
import os
import threading
from datetime import datetime, timedelta

TOKEN = "YOUR_BOT_TOKEN"
ADMIN_ID = 7919870032  # Your Telegram ID here

bot = telebot.TeleBot(TOKEN)

DATA_DIR = "data"
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# Files per day: questions_YYYYMMDD.json, scores_YYYYMMDD.json
def questions_file(date_str):
    return os.path.join(DATA_DIR, f"questions_{date_str}.json")

def scores_file(date_str):
    return os.path.join(DATA_DIR, f"scores_{date_str}.json")

# Load JSON safely
def load_json(path):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {}

# Save JSON safely
def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

# Active quiz sessions: user_id -> dict with question index and timer thread
active_sessions = {}

# Admin command to add question:
# Format:
# Question description | Question text | Option1 | Option2 | Option3 | Option4 | correct_option_number (1-4)
@bot.message_handler(commands=['addquestion'])
def add_question_start(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ You are not authorized.")
        return
    bot.reply_to(message, "Send question in this format:\n"
                          "`description | question text | option1 | option2 | option3 | option4 | correct_option_number`",
                          parse_mode="Markdown")
    bot.register_next_step_handler(message, process_add_question)

def process_add_question(message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        parts = [p.strip() for p in message.text.split("|")]
        if len(parts) != 7:
            raise ValueError("Wrong number of fields")
        description, question_text = parts[0], parts[1]
        options = parts[2:6]
        correct_idx = int(parts[6]) - 1
        if correct_idx not in range(4):
            raise ValueError("Correct option number must be 1-4")
        
        # Use today's date for exam questions
        date_str = datetime.now().strftime("%Y%m%d")
        qfile = questions_file(date_str)
        questions = load_json(qfile)
        if not isinstance(questions, list):
            questions = []
        
        questions.append({
            "description": description,
            "question": question_text,
            "options": options,
            "answer": correct_idx
        })
        save_json(qfile, questions)
        bot.reply_to(message, f"✅ Question added for exam {date_str}!\n"
                              f"Total questions now: {len(questions)}")
    except Exception as e:
        bot.reply_to(message, f"⚠️ Error: {e}\nMake sure format is correct.")

# User command to join today's exam
@bot.message_handler(commands=['join'])
def join_exam(message):
    user_id = str(message.from_user.id)
    date_str = datetime.now().strftime("%Y%m%d")

    scores = load_json(scores_file(date_str))
    if user_id in scores:
        bot.reply_to(message, f"ℹ️ You have already joined today's exam ({date_str}).")
        return

    questions = load_json(questions_file(date_str))
    if not questions:
        bot.reply_to(message, f"❌ No exam questions are available today ({date_str}).")
        return

    # Set how many questions appear in this exam (e.g., 20)
    max_questions = 20
    if len(questions) > max_questions:
        questions = questions[:max_questions]

    # Save initial score and index
    scores[user_id] = {
        "score": 0,
        "index": 0,
        "joined": datetime.now().isoformat()
    }
    save_json(scores_file(date_str), scores)
    bot.reply_to(message, f"✅ You joined the exam for {date_str}!\n"
                          f"There are {len(questions)} questions. Good luck!")
    # Start quiz
    active_sessions[user_id] = {"date": date_str, "index": 0}
    send_question(message.chat.id, user_id)

def send_question(chat_id, user_id):
    date_str = active_sessions[user_id]["date"]
    idx = active_sessions[user_id]["index"]
    questions = load_json(questions_file(date_str))

    if idx >= len(questions):
        finish_exam(chat_id, user_id)
        return

    q = questions[idx]
    text = f"📋 *Question {idx+1}:*\n" \
           f"{q['description']}\n\n" \
           f"{q['question']}"
    markup = telebot.types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    for opt in q["options"]:
        markup.add(opt)
    bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

    # Start timer thread
    timer = threading.Timer(15, timeout_answer, args=(chat_id, user_id))
    timer.start()
    active_sessions[user_id]["timer"] = timer

def timeout_answer(chat_id, user_id):
    # Called when user fails to answer in time
    if user_id not in active_sessions:
        return
    idx = active_sessions[user_id]["index"]
    date_str = active_sessions[user_id]["date"]

    bot.send_message(chat_id, f"⏰ Time's up for question {idx+1}!")

    # Move to next question without incrementing score
    active_sessions[user_id]["index"] += 1
    send_question(chat_id, user_id)

@bot.message_handler(func=lambda message: True)
def handle_answer(message):
    user_id = str(message.from_user.id)
    if user_id not in active_sessions:
        return  # no active exam

    session = active_sessions[user_id]
    date_str = session["date"]
    idx = session["index"]
    questions = load_json(questions_file(date_str))
    scores = load_json(scores_file(date_str))

    if idx >= len(questions):
        bot.reply_to(message, "ℹ️ You have finished the exam.")
        return

    # Cancel timer on answer
    timer = session.get("timer")
    if timer:
        timer.cancel()

    user_answer = message.text.strip()
    correct_idx = questions[idx]["answer"]
    correct_answer = questions[idx]["options"][correct_idx]

    if user_answer.lower() == correct_answer.lower():
        scores[user_id]["score"] += 1
        bot.reply_to(message, "✅ Correct!")
    else:
        bot.reply_to(message, f"❌ Wrong! Correct answer was: {correct_answer}")

    # Move to next question
    session["index"] += 1
    scores[user_id]["index"] = session["index"]
    save_json(scores_file(date_str), scores)

    # Send next question or finish
    if session["index"] < len(questions):
        send_question(message.chat.id, user_id)
    else:
        finish_exam(message.chat.id, user_id)

def finish_exam(chat_id, user_id):
    date_str = active_sessions[user_id]["date"]
    scores = load_json(scores_file(date_str))
    score = scores[user_id]["score"]
    total = len(load_json(questions_file(date_str)))
    bot.send_message(chat_id, f"🏁 Exam finished!\n"
                              f"Your score: {score} / {total}")
    del active_sessions[user_id]

# Command to see today's exam description count (admin only)
@bot.message_handler(commands=['examinfo'])
def exam_info(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ Not authorized.")
        return
    date_str = datetime.now().strftime("%Y%m%d")
    questions = load_json(questions_file(date_str))
    bot.reply_to(message, f"Exam {date_str} has {len(questions)} questions.")

# Run bot
bot.polling()
