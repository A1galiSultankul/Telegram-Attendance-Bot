import telebot
from telebot import types
import teacher
import gsheet
from datetime import date
import psycopg2
import gspread
from datetime import datetime, timedelta, date, timezone
from apscheduler.schedulers.background import BackgroundScheduler
import threading
import os
from dotenv import load_dotenv


# Загружаем переменные окружения из файла .env
load_dotenv()

# Инициализируем токен и создаем объект бота
BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

# Настраиваем подключение к базе данных PostgreSQL
conn = psycopg2.connect(
    dbname=os.getenv("DATABASE_NAME"),
    user=os.getenv("DATABASE_USER"),
    host=os.getenv("DATABASE_HOST"),
    port=os.getenv("DATABASE_PORT"),
    password=os.getenv("DATABASE_PASSWORD")
)

# Настройка учетной записи Google Sheets
service_account_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
sa = gspread.service_account(filename=service_account_path)
sheet_key = os.getenv("SHEET_KEY")
sh = sa.open_by_key(sheet_key)
wrs = sh.worksheet("Sheet1")

def update_google_sheet_with_code(course_name, session_code):
    try:
        # Check if the worksheet already exists
        try:
            wrs = sh.worksheet(course_name)
        except gspread.exceptions.WorksheetNotFound:
            # Create a new worksheet with the course name if not found
            wrs = sh.add_worksheet(title=course_name, rows="120", cols="100")
            # Optionally, you can initialize the first row with headers
            wrs.append_row(["Attendance", "Student Name"])
        
        # Get the first row (headers)
        headers = wrs.row_values(1)

        # Find the first empty column in the first row
        col = len(headers) + 1

        # Update the first empty column with the session code and date
        wrs.update_cell(1, col, f"{session_code}")

    except Exception as e:
        print(f"Error updating Google Sheet: {e}")

# Function to save user information into PostgreSQL

def save_user(user_type, user_name, email, telegram_user_id):
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO users (user_type, user_name, email, telegram_user_id) VALUES (%s, %s, %s, %s) ON CONFLICT (email) DO NOTHING",
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


def save_attendance_session(teacher_name, course_name, session_code):
    try:
        cursor = conn.cursor()
        
        # Получаем teacher_id по имени учителя
        cursor.execute("SELECT teacher_id FROM teachers WHERE teacher_name = %s", (teacher_name,))
        teacher_result = cursor.fetchone()
        
        # Получаем course_id по названию курса
        cursor.execute("SELECT course_id FROM courses WHERE course_name = %s", (course_name,))
        course_result = cursor.fetchone()

        if teacher_result and course_result:
            teacher_id = teacher_result[0]
            course_id = course_result[0]

            # Сохраняем сессию посещаемости
            cursor.execute("""
                INSERT INTO attendance (teacher_id, course_id, session_code, attendance_date, created_at) 
                VALUES (%s, %s, %s, %s, %s)
            """, (teacher_id, course_id, session_code, datetime.today(), datetime.now()))
            
            conn.commit()
        cursor.close()
    except psycopg2.Error as e:
        print(f"Error saving attendance session: {e}")



def handle_attendance_code(message):
    try:
        chat_id = message.chat.id
        attendance_code = message.text

        cursor = conn.cursor()
        cursor.execute("SELECT attendance_id, course_id, created_at FROM attendance WHERE session_code = %s", (attendance_code,))
        result = cursor.fetchone()


        if result:
            attendance_id, course_id, created_at = result
            course_name = get_course_name(course_id)

            schedule_absence_check(attendance_code, course_name)

            time_diff = datetime.now() - created_at

            if time_diff.total_seconds() > 1800:  
                bot.send_message(chat_id, 'Код для отметки присутствия просрочен.')

                bot.send_message(chat_id, 'Отсутствие отмечено, так как присутствие не было зафиксировано вовремя.')
            else:
                cursor.execute("SELECT DISTINCT s.student_name, s.student_id FROM students s JOIN users u ON s.student_name = u.user_name JOIN enrollments e ON s.student_id = e.student_id WHERE u.telegram_user_id = %s AND e.course_id = %s", 
                               (message.from_user.id, course_id))
                student = cursor.fetchone()

                if student:
                    student_name, student_id = student
                    
                    cursor.execute("SELECT COUNT(*) FROM attendance WHERE attendance_id = %s AND student_id = %s", 
                                   (attendance_id, student_id))
                    already_marked = cursor.fetchone()[0]

                    if already_marked > 0:
                        bot.send_message(chat_id, 'Присутствие уже отмечено для этой сессии.')
                    else:
                        cursor.execute("UPDATE attendance SET attendance_date = %s, student_id = %s WHERE attendance_id = %s", 
                                       (date.today(), student_id, attendance_id))
                        conn.commit()
                        update_google_sheet(student_name, attendance_code, get_course_name(course_id))
                        bot.send_message(chat_id, 'Молодец! Присутствие успешно отмечено.')
                else:
                    bot.send_message(chat_id, 'Вы не зарегистрированы на этот курс.')
        else:
            bot.send_message(chat_id, 'Неверный код. Пожалуйста, проверьте и попробуйте снова.')

        cursor.close()
    except psycopg2.Error as e:
        print(f"Error handling attendance code: {e}")


def schedule_absence_check(attendance_code, course_name):
    
    timer = threading.Timer(1800, insert_absent_for_unmarked_attendance, [attendance_code, course_name])
    timer.start()

    timer = threading.Timer(1860, Attendance_percentage, [course_name])
    timer.start()



def Attendance_percentage(course_name):
    wrs = sh.worksheet(course_name)
    
    # Get all the values in column 1 and column 2 at once
    column_1_values = wrs.col_values(1)  # Fetch the entire first column
    second_column_values = wrs.col_values(2)  # Fetch the entire second column
    
    index_name = len(second_column_values) + 1  # Assuming this is the first empty row in column 2
    print(index_name)
    
    # Loop through the rows starting from row 2 up to index_name
    for i in range(2, index_name):
        # Check if the cell in column 1 of the current row (i) is empty
        cell_value = column_1_values[i - 1] if i - 1 < len(column_1_values) else ""  # Fetch the value locally
        
        if cell_value == "":  # If the cell is empty
            # Insert the attendance formula into the empty cell
            attendance_formula = f'=ОКРУГЛ((СЧЁТЕСЛИ($C${i}:$CV${i}; "Present")/(СЧЁТЕСЛИ($C${i}:$CV${i}; "Present") + СЧЁТЕСЛИ($C${i}:$CV${i}; "Absent")))*100; 0) & "%"'
            wrs.update_cell(i, 1, attendance_formula)  # Update the cell in column 1 with the formula


# Function to update Google Sheet with attendance information

def update_google_sheet(student_name, session_code, course_name):
    try:
        # Access the specific worksheet
        wrs = sh.worksheet(course_name)

        full_name = student_name.strip().lower()

        # Get the first row and first column values
        first_row = wrs.row_values(1)
        column_values = wrs.col_values(2)

        # Find the first empty cell in the second column
        second_column_values = wrs.col_values(2)
        index_name = len(second_column_values) + 1  # Assuming index_name as the first empty row in column 2

        # Check if the student's name is in the first column
        student_names_lower = [name.lower() for name in column_values]
        if full_name not in student_names_lower:
            # Add student name to the first empty cell in the first column
            wrs.update_cell(index_name, 2, student_name.strip())
            row_index = index_name
        else:
            # Find the row index for the student's full name
            row_index = student_names_lower.index(full_name) + 1

        # Find the column index for the session code
        if session_code in first_row:
            column_index = first_row.index(session_code) + 1
        else:
            raise ValueError(f"Session code {session_code} not found in the first row.")

        # Update the cell with 'Present'
        wrs.update_cell(row_index, column_index, 'Present')

        

    except Exception as e:
        print(f"An error occurred: {e}")
    

def insert_absent_for_unmarked_attendance(session_code, course_name):
    try:
        wrs = sh.worksheet(course_name)
        first_row = wrs.row_values(1)

        if session_code not in first_row:
            raise ValueError(f"Session code {session_code} not found.")

        column_index = first_row.index(session_code) + 1
        session_column_values = wrs.col_values(column_index)
        num_rows = len(wrs.col_values(2))

        # Prepare the "Absent" updates in a single pass
        absent_updates = [
            (row + 1, column_index, 'Absent')
            for row in range(num_rows)
            if row >= len(session_column_values) or not session_column_values[row].strip()
        ]

        if absent_updates:
            wrs.batch_update([{'range': f'R{row}C{col}', 'values': [[val]]} for row, col, val in absent_updates])

        print(f"Marked 'Absent' in column {column_index} for empty fields up to row {num_rows}.")
    except Exception as e:
        print(f"An error occurred: {e}")

# Error handler for notifying students
def notify_students(course_name, session_code):
    try:
        cursor = conn.cursor()
        
        # Находим course_id по названию курса
        cursor.execute("SELECT course_id FROM courses WHERE course_name = %s", (course_name,))
        course_result = cursor.fetchone()
        
        if course_result:
            course_id = course_result[0]
            
            # Находим всех студентов, записанных на данный курс
            cursor.execute("""
                SELECT DISTINCT s.student_id, s.student_name, s.student_email, u.telegram_user_id  
                FROM students s
                JOIN users u ON s.student_name = u.user_name
                JOIN enrollments e ON s.student_id = e.student_id
                WHERE e.course_id = %s
            """, (course_id,))
            
            students = cursor.fetchall()
            cursor.close()
            
            # Отправляем сообщение каждому студенту
            for student in students:
                telegram_user_id = student[3]
                try:
                    bot.send_message(telegram_user_id, f'Отправьте код от учителя, чтобы отметить ваше присутствие на занятии.')
                except Exception as e:
                    print(f"Failed to send message to {telegram_user_id}: {e}")
    except psycopg2.Error as e:
        print(f"Error notifying students: {e}")
attempts = {}  # Словарь для отслеживания попыток ввода email для каждого пользователя

@bot.message_handler(commands=['start'])
def main(message):
    bot.send_message(message.chat.id, 'Привет👋! Введите свой email, чтобы начать')
    bot.register_next_step_handler(message, process_email)

def process_email(message):
    chat_id = message.chat.id
    email = message.text
    telegram_user_id = message.from_user.id
    
    # Проверяем, существует ли пользователь с таким email
    if user_exists(email):
        bot.send_message(chat_id, 'Этот email уже зарегистрирован. Пожалуйста, используйте команду /start с другим email или обратитесь в поддержку, если возникли сложности. \n Поддержка: +77718342121')
        return

    user_type, user_name = check_email(email)
    
    # Если email не найден, увеличиваем счетчик попыток
    if user_type is None:
        attempts[chat_id] = attempts.get(chat_id, 0) + 1

        # Если попыток уже две, запрашиваем номер телефона
        if attempts[chat_id] >= 2:
            bot.send_message(chat_id, 'Email не найден. Пожалуйста, укажите ваш номер телефона:')
            bot.register_next_step_handler(message, process_phone)
        else:
            bot.send_message(chat_id, 'Email не найден в наших записях. Пожалуйста, введите email ещё раз или обратитесь в поддержку, если у вас возникли проблемы.')
            bot.register_next_step_handler(message, process_email)
        return

    # Если email найден в базе
    if user_type == 'teacher':
        save_user(user_type, user_name, email, telegram_user_id)
        selected_courses[chat_id] = {"courses": [], "is_selecting": True}
        bot.send_message(chat_id, 'Please select the courses you will be teaching. Press "Done" when finished.', reply_markup=create_updated_course_keyboard(chat_id))

    elif user_type == 'student':
        save_user(user_type, user_name, email, telegram_user_id)
        bot.send_message(chat_id, 'Ваше имя пользователя успешно сохранено.')


# Handler for processing email input
def process_phone(message):
    phone = message.text
    chat_id = message.chat.id

    # Поиск email по номеру телефона
    email = find_email_by_phone(phone)

    # Если email найден, предлагаем пользователю подтвердить
    if email:
        bot.send_message(chat_id, f'Это ваш email?: {email}? Ответьте «yes» для подтверждения или «no», чтобы связаться с поддержкой.')
        bot.register_next_step_handler(message, confirm_email, email)
    else:
        bot.send_message(chat_id, "Нам не удалось найти адрес электронной почты, связанный с этим номером телефона. Пожалуйста, свяжитесь со службой поддержки по телефону +77718342121.")

def confirm_email(message, email):
    chat_id = message.chat.id
    response = message.text.strip().lower()

    if response == "yes" or "да":
        # Завершаем регистрацию
        telegram_user_id = message.from_user.id
        user_type, user_name = check_email(email)  # Получаем тип и имя пользователя

        if user_type:
            save_user(user_type, user_name, email, telegram_user_id)
            bot.send_message(chat_id, 'Email подтвержден, регистрация завершена.')
    else:
        bot.send_message(chat_id, "Пожалуйста, свяжитесь со службой поддержки по телефону +77718342121.")

def user_exists(email):
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM users WHERE email = %s", (email,))
    result = cursor.fetchone()
    cursor.close()
    return result is not None

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

def find_email_by_phone(phone):
    cursor = conn.cursor()
    cursor.execute("SELECT student_email FROM students WHERE phone_number = %s", (phone,))
    result = cursor.fetchone()
    cursor.close()
    return result[0] if result else None



def create_course_selection_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    courses = get_all_courses()
    
    # Добавляем кнопки для каждого курса
    for course in courses:
        markup.add(types.InlineKeyboardButton(f" {course}", callback_data=f"select_{course}"))
    
    # Добавляем кнопку завершения выбора
    markup.add(types.InlineKeyboardButton("Done", callback_data="done"))
    return markup

selected_courses = {}
@bot.callback_query_handler(func=lambda call: call.data.startswith("select_") or call.data == "done")
def handle_course_selection(call):
    chat_id = call.message.chat.id
    course_name = call.data.replace("select_", "")

    if course_name == "done":
        # Проверяем, были ли выбраны курсы
        if not selected_courses.get(chat_id) or not selected_courses[chat_id]["courses"]:
            bot.send_message(chat_id, "No courses selected. Please select at least one course.")
            return
        
        # Получаем teacher_id по telegram_user_id (chat_id)
        teacher_id = get_teacher_id_by_telegram_id(chat_id)
        
        if not teacher_id:
            bot.send_message(chat_id, "Teacher not found in database.")
            return

        # Создаем курсор перед использованием в цикле
        cursor = conn.cursor()
        
        # Сохраняем каждый выбранный курс в таблицу teacher_courses
        for course in selected_courses[chat_id]["courses"]:
            # Получаем course_id для курса
            cursor.execute("SELECT course_id FROM courses WHERE course_name = %s", (course,))
            course_result = cursor.fetchone()
            
            if course_result:
                course_id = course_result[0]
                
                # Добавляем запись в teacher_courses для привязки учителя к курсу
                cursor.execute("""
                    INSERT INTO teacher_courses (teacher_id, course_id)
                    VALUES (%s, %s) ON CONFLICT (teacher_id, course_id) DO NOTHING
                """, (teacher_id, course_id))

        # Фиксируем изменения в базе данных
        conn.commit()
        cursor.close()

        # Подтверждаем успешное сохранение выбранных курсов и убираем клавиатуру
        bot.send_message(chat_id, "Your course selection has been saved successfully.", reply_markup=types.ReplyKeyboardRemove())
        
        # Выводим клавиатуру с выбранными курсами
        selected_courses_list = selected_courses[chat_id]["courses"]
        bot.send_message(chat_id, "Here are your selected courses:", reply_markup=generate_courses_keyboard(selected_courses_list))
        
        # Удаляем данные из selected_courses после завершения
        del selected_courses[chat_id]

    elif course_name in get_all_courses():
        # Добавляем выбранный курс в список, если его еще нет
        if chat_id not in selected_courses:
            selected_courses[chat_id] = {"courses": []}
        
        if course_name not in selected_courses[chat_id]["courses"]:
            selected_courses[chat_id]["courses"].append(course_name)
            bot.send_message(chat_id, f"Course '{course_name}' selected. Choose more or press 'Done'.")
        else:
            bot.send_message(chat_id, f"Course '{course_name}' is already selected. Choose more or press 'Done'.")
    else:
        bot.send_message(chat_id, "Invalid course selected. Please choose a valid course or press 'Done'.")

        
def create_updated_course_keyboard(chat_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    courses = get_all_courses()
    
    # Обновляем отображение кнопок в соответствии с текущим выбором
    for course in courses:
        if course in selected_courses[chat_id]["courses"]:
            markup.add(types.InlineKeyboardButton(f" {course}", callback_data=f"select_{course}"))
        else:
            markup.add(types.InlineKeyboardButton(f" {course}", callback_data=f"select_{course}"))
    
    # Добавляем кнопку завершения выбора
    markup.add(types.InlineKeyboardButton("Done", callback_data="done"))
    return markup

def get_teacher_id_by_telegram_id(telegram_user_id):
    try:
        cursor = conn.cursor()
        
        # Сначала находим имя учителя в таблице users
        cursor.execute("SELECT user_name FROM users WHERE telegram_user_id = %s", (telegram_user_id,))
        result = cursor.fetchone()
        
        if result:
            user_name = result[0]
            # Теперь ищем teacher_id в таблице teachers по user_name
            cursor.execute("SELECT teacher_id FROM teachers WHERE teacher_name = %s", (user_name,))
            teacher_id_result = cursor.fetchone()
            
            cursor.close()
            return teacher_id_result[0] if teacher_id_result else None
        else:
            cursor.close()
            return None
    except psycopg2.Error as e:
        print(f"Error fetching teacher ID: {e}")
        return None

# def get_teacher_id_by_email(teacher_email):
#     try:
#         cursor = conn.cursor()
        
#         # Получаем teacher_id по email учителя
#         cursor.execute("SELECT email FROM users WHERE telegram_user_id = %s", (teacher_email,))
#         teacher_id_result = cursor.fetchone()
        
#         cursor.close()
#         return teacher_id_result[0] if teacher_id_result else None
#     except psycopg2.Error as e:
#         print(f"Error fetching teacher ID by email: {e}")
#         return None


# def save_teacher_courses_by_email(teacher_email, courses):
#     try:
#         cursor = conn.cursor()
        
#         # Получаем данные учителя (teacher_id и teacher_name) по email, чтобы сохранить их
#         cursor.execute("SELECT teacher_name FROM teachers WHERE teacher_email = %s LIMIT 1", (teacher_email,))
#         teacher_info = cursor.fetchone()
        
#         if not teacher_info:
#             print(f"No teacher found with email {teacher_email}")
#             return
        
#         teacher_name = teacher_info

#         # # Удаляем существующие записи для учителя на основе email
#         # cursor.execute("DELETE FROM teachers WHERE teacher_email = %s", (teacher_email,))

#         # Добавляем новые курсы для учителя, сохраняя teacher_id и teacher_name
#         for course_name in courses:
#             # Получаем course_id по имени курса
#             cursor.execute("SELECT course_id FROM courses WHERE course_name = %s", (course_name,))
#             course_id_result = cursor.fetchone()
#             if course_id_result:
#                 course_id = course_id_result[0]
                
#                 # Вставляем новую запись, сохраняя teacher_id, teacher_name и email
#                 cursor.execute(
#                     "INSERT INTO teachers (teacher_id, teacher_name, course_id, teacher_email) VALUES (%s, %s, %s, %s)", 
#                     (teacher_name, course_id, teacher_email)
#                 )

#         conn.commit()
#         cursor.close()
#     except psycopg2.Error as e:
#         print(f"Error saving teacher courses by email: {e}")
#         conn.rollback()
selecting_course = {}
@bot.message_handler(func=lambda message: True)
def handle_message(message):
    chat_id = message.chat.id

    # Проверяем, находится ли пользователь в процессе выбора курсов
    if chat_id in selecting_course and selecting_course[chat_id].get("is_selecting"):
        if message.text == "Done":
            # Завершаем выбор курсов и создаем код для всех выбранных
            selected_courses = selecting_course[chat_id]["courses"]
            if selected_courses:
                session_code = teacher.generate_random_password()
                for course_name in selected_courses:
                    save_attendance_session(selecting_course[chat_id]["teacher_name"], course_name, session_code)
                    update_google_sheet_with_code(course_name, session_code)
                    bot.send_message(chat_id, f'Attendance code for {course_name}: {session_code}')
                    notify_students(course_name, session_code)
                
                # Получаем список всех курсов данного учителя из базы данных
                teacher_name = selecting_course[chat_id]["teacher_name"]
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT c.course_name 
                    FROM courses c
                    JOIN teacher_courses tc ON c.course_id = tc.course_id
                    JOIN teachers t ON tc.teacher_id = t.teacher_id
                    WHERE t.teacher_name = %s
                """, (teacher_name,))
                courses = [row[0] for row in cursor.fetchall()]
                cursor.close()

                # Выводим клавиатуру с курсами учителя
                bot.send_message(chat_id, "Here are all your courses:", reply_markup=generate_courses_keyboard(courses))
                
            else:
                bot.send_message(chat_id, "No courses were selected.")
            
            # Сброс состояния выбора курсов после завершения
            selecting_course.pop(chat_id)
            return

        # Если сообщение — это название курса, добавляем его в список выбранных курсов
        if message.text in get_all_courses():
            selecting_course[chat_id]["courses"].append(message.text)
            bot.send_message(chat_id, f'Course "{message.text}" selected. Press "Done" when finished.')
        else:
            bot.send_message(chat_id, "Please select courses by using the buttons or press 'Done' when finished.")
        return

    # Основная логика проверки пользователя и запроса на регистрацию
    telegram_user_id = message.from_user.id
    cursor = conn.cursor()
    cursor.execute("SELECT user_type, user_name FROM users WHERE telegram_user_id = %s", (telegram_user_id,))
    user_info = cursor.fetchone()
    cursor.close()

    if not user_info:
        bot.send_message(chat_id, 'Please use /start to register first.')
        return

    user_type, user_name = user_info

    if user_type == 'teacher':
        if message.text in get_teacher_courses(user_name):
            course_name = message.text
            session_code = teacher.generate_random_password()
            save_attendance_session(user_name, course_name, session_code)
            update_google_sheet_with_code(course_name, session_code)
            bot.send_message(chat_id, f'Attendance code for {course_name}: {session_code}')
            notify_students(course_name, session_code)
        
        elif message.text == "All courses":
            select_multiple_courses(chat_id, user_name)
        else:
            bot.send_message(chat_id, 'Please select a valid course.')
    
    elif user_type == 'student':
        handle_attendance_code(message)
    else:
        bot.send_message(chat_id, 'Unknown user type.')

def select_multiple_courses(chat_id, teacher_name):
    # Получаем список курсов учителя
    courses = get_all_courses()
    # Создаем меню с кнопками для выбора нескольких курсов
    markup = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    course_buttons = [types.KeyboardButton(course) for course in courses]
    done_button = types.KeyboardButton("Done")
    markup.add(*course_buttons, done_button)
    
    # Сохраняем выбранные курсы в словарь, привязанный к user_id
    selecting_course[chat_id] = {"courses": [], "is_selecting": True, "teacher_name": teacher_name}
    
    bot.send_message(chat_id, "Please select courses for attendance. Press 'Done' when finished.", reply_markup=markup)

# Словарь для хранения выбранных курсов



# Function to generate keyboard markup for course selection
def generate_courses_keyboard(courses):
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    for course in courses:
        markup.add(types.KeyboardButton(course))
        
    markup.add(types.KeyboardButton("All courses"))
    return markup

# Function to retrieve teacher's courses from the database
def get_teacher_courses(teacher_name):
    try:
        cursor = conn.cursor()
        
        # Получаем teacher_id по имени учителя
        cursor.execute("SELECT teacher_id FROM teachers WHERE teacher_name = %s", (teacher_name,))
        teacher_result = cursor.fetchone()
        
        if teacher_result:
            teacher_id = teacher_result[0]
            
            # Получаем все course_name, связанные с этим teacher_id через таблицу teacher_courses
            cursor.execute("""
                SELECT c.course_name 
                FROM courses c 
                JOIN teacher_courses tc ON c.course_id = tc.course_id
                WHERE tc.teacher_id = %s
            """, (teacher_id,))
            
            courses = cursor.fetchall()
            cursor.close()
            
            return [course[0] for course in courses]  # Возвращаем список имен курсов
        else:
            cursor.close()
            return []
    except psycopg2.Error as e:
        print(f"Error fetching teacher courses: {e}")
        return []
    
def get_all_courses():
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT course_name FROM courses")
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