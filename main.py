import telebot
from telebot import types
import teacher
import gsheet
from datetime import date
import psycopg2
import gspread
from datetime import datetime

bot = telebot.TeleBot('6704758617:AAH7EKkgkfvbTUr3IdCAWdWEVg-S9jsWEgQ')
# PostgreSQL connection setup
conn = psycopg2.connect(
    dbname='tg_users',
    user='alisherma',
    host='localhost',
    port='5432'
)

sa = gspread.service_account(filename="service_account.json")
sh = sa.open("students_list")
wrs = sh.worksheet("Sheet1")


# Function to save user information into PostgreSQL
def save_user(user_type, user_name, email, telegram_user_id):
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO users (user_type, user_name, email, telegram_user_id ) VALUES (%s, %s, %s, %s) ON CONFLICT (email) DO NOTHING",
            (user_type, user_name, email, telegram_user_id)
        )
        conn.commit()
        cursor.close()
    except psycopg2.Error as e:
        print(f"Error saving user: {e}")
        conn.rollback()  # Rollback the transaction in case of error

# Function to check email existence in PostgreSQL
def check_email(email):
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT student_name FROM students WHERE student_email = %s", (email,))
        student = cursor.fetchone()
        
        cursor.execute("SELECT teacher_name FROM teachers WHERE teacher_email = %s", (email,))
        teacher = cursor.fetchone()
        
        cursor.close()
        
        if student:
            return 'student', student[0]
        elif teacher:
            return 'teacher', teacher[0]
        else:
            return None, None
    except psycopg2.Error as e:
        print(f"Error checking email: {e}")

# Function to handle attendance code verification
def handle_attendance_code(message):
    try:
        chat_id = message.chat.id
        attendance_code = message.text
        
        cursor = conn.cursor()
        cursor.execute("SELECT attendance_id, course_id FROM attendance WHERE session_code = %s", (attendance_code,))
        result = cursor.fetchone()
        
        if result:
            attendance_id, course_id = result
            cursor.execute("SELECT DISTINCT s.student_name,s.student_id FROM students s JOIN users u ON s.student_name = u.user_name JOIN enrollments e ON s.student_id = e.student_id WHERE u.telegram_user_id = %s AND e.course_id = %s", (message.from_user.id, course_id))
            student = cursor.fetchone()
            if student:
                student_name, student_id = student
                cursor.execute("UPDATE attendance SET attendance_date = %s, student_id = %s WHERE attendance_id = %s", (date.today(), student_id, attendance_id))
                conn.commit()
                print(student)
                update_google_sheet(student_name, get_course_name(course_id))
                bot.send_message(chat_id, 'Attendance marked successfully.')
            else:
                bot.send_message(chat_id, 'You are not enrolled in this course.')
        else:
            bot.send_message(chat_id, 'Invalid attendance code.')
        
        cursor.close()
    except psycopg2.Error as e:
        print(f"Error handling attendance code: {e}")

# Function to update Google Sheet with attendance information
def update_google_sheet(student_name, course_name):
    try:
        wrs = sh.worksheet(course_name)
        cell = wrs.find(student_name)
        now = datetime.now().strftime('%m-%d %H:%M')
        if cell:
            row = cell.row
            next_col = len(wrs.row_values(row)) + 1
            wrs.update_cell(row, next_col, now)
        else:
            # If the student's name is not found, add a new row with the student's name and the date
            wrs.append_row([student_name, now])
    
    except Exception as e:
        print(f"Error updating Google Sheet: {e}")


def save_attendance_session(teacher_name, course_name, session_code):
    cursor = conn.cursor()
    cursor.execute("SELECT course_id FROM courses WHERE course_name = %s", (course_name,))
    course_id = cursor.fetchone()[0]


    cursor.execute("SELECT teacher_id FROM teachers WHERE teacher_name = %s", (teacher_name,))
    teacher_id = cursor.fetchone()[0]


    # Insert attendance session
    cursor.execute(
        "INSERT INTO attendance (course_id, teacher_id, session_code) VALUES (%s, %s, %s)",
        (course_id, teacher_id, session_code)
    )
    conn.commit()
    cursor.close()


# Error handler for notifying students
def notify_students(course_name, session_code):
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT s.student_id, s.student_name, s.student_email, u.telegram_user_id  
            FROM students s
            JOIN users u ON s.student_name = u.user_name
            JOIN enrollments e ON s.student_id = e.student_id
            JOIN courses c ON e.course_id = c.course_id
            WHERE c.course_name = %s
        """, (course_name,))
        
        students = cursor.fetchall()
        cursor.close()
        
        for student in students:
            telegram_user_id = student[3]
            try:
                # bot.send_message(telegram_user_id, f'Attendance code for {course_name}: {session_code}. Please reply with the code to mark your attendance.')
                bot.send_message(telegram_user_id, f'Please reply with the code that the teacher will send you to mark your attendance in class.')
            except Exception as e:
                print(f"Failed to send message to {telegram_user_id}: {e}")
    except psycopg2.Error as e:
        print(f"Error notifying students: {e}")

# Command handler for starting interaction
@bot.message_handler(commands=['start'])
def main(message):
    bot.send_message(message.chat.id, 'Hello! Please, enter your email:')
    bot.register_next_step_handler(message, process_email)

# Handler for processing email input
def process_email(message):
    email = message.text
    user_type, user_name = check_email(email)
    telegram_user_id = message.from_user.id

    if user_type == 'teacher':
        save_user(user_type, user_name, email, telegram_user_id)
        teacher_courses = get_teacher_courses(user_name)
        bot.send_message(message.chat.id, 'Select a course:', reply_markup=generate_courses_keyboard(teacher_courses))
    elif user_type == 'student':
        save_user(user_type, user_name, email, telegram_user_id)
        bot.send_message(message.chat.id, 'Your username has been saved.')
    else:
        bot.send_message(message.chat.id, 'Email not found in our records. Please contact support.')

#teacher_selects_course
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    telegram_user_id  = message.from_user.id
    chat_id = message.chat.id
    # Retrieve user info based on telegram_username
    cursor = conn.cursor()
    cursor.execute("SELECT user_type, user_name FROM users WHERE telegram_user_id  = %s", (telegram_user_id ,))
    user_info = cursor.fetchone()
    cursor.close()
    user_type, user_name = user_info
    if not user_info:
        bot.send_message(chat_id, 'Please use /start to register first.')
        return
    if user_type == 'teacher':
        if message.text in get_teacher_courses(user_name):
            course_name = message.text
            session_code = teacher.generate_random_password()
            save_attendance_session(user_name, course_name, session_code)
            bot.send_message(chat_id, f'Attendance code for {course_name}: {session_code}')
            notify_students(course_name, session_code)
        else:
            bot.send_message(chat_id, 'Please select a valid course.')
    elif user_type == 'student':
        handle_attendance_code(message)
    else:
        bot.send_message(chat_id, 'Unknown user type.')

# Function to generate keyboard markup for course selection
def generate_courses_keyboard(courses):
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    for course in courses:
        markup.add(types.KeyboardButton(course))
    return markup

# Function to retrieve teacher's courses from the database
def get_teacher_courses(teacher_name):
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT course_name FROM courses WHERE course_id IN (SELECT course_id FROM teachers WHERE teacher_name = %s)", (teacher_name,))
        courses = cursor.fetchall()
        cursor.close()
        return [course[0] for course in courses]
    except psycopg2.Error as e:
        print(f"Error fetching teacher courses: {e}")

# Function to get course name by course ID
def get_course_name(course_id):
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT course_name FROM courses WHERE course_id = %s", (course_id,))
        course_name = cursor.fetchone()[0]
        cursor.close()
        return course_name
    except psycopg2.Error as e:
        print(f"Error fetching course name: {e}")


# Start the bot polling process
if __name__ == '__main__':
    bot.polling(none_stop=True)