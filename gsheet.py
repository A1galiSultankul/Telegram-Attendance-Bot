import gspread
import pandas as pd
import psycopg2
import schedule
import time
import threading
import os
from dotenv import load_dotenv



# sa = gspread.service_account(filename="service-account.json")
# sh = sa.open("students_list")
# students_wrs = sh.worksheet("Sheet1")

# Google Sheets setup
load_dotenv()

# Google Sheets setup
sa = gspread.service_account(filename=os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))
sh = sa.open_by_key(os.getenv("SHEET_KEY"))
worksheets = [ws.title for ws in sh.worksheets()]
print("Available worksheets:", worksheets)

students_wrs = sh.worksheet("Sheet1")  # Лист с данными учеников
teachers_wrs = sh.worksheet("Sheet2")  

# PostgreSQL connection details
database = os.getenv("DATABASE_NAME")
username = os.getenv("DATABASE_USER")
hostname = os.getenv("DATABASE_HOST")
port_id = os.getenv("DATABASE_PORT")
password = os.getenv("DATABASE_PASSWORD")

# Подключение к базе данных PostgreSQL
conn = psycopg2.connect(
    dbname=database,
    user=username,
    host=hostname,
    port=port_id,
    password=password
)


def update_database():
    # Connect to PostgreSQL
    conn = psycopg2.connect(
        dbname=database,
        user=username,
        host=hostname,
        port=port_id,
        password=password 
    )

    # Get all values in the sheet
    students_data = students_wrs.get_all_values()
    df_students = pd.DataFrame(students_data[1:], columns=students_data[0]).applymap(str.strip)
    df_students.columns = df_students.columns.str.lower()

    teachers_data = teachers_wrs.get_all_values()
    df_teachers = pd.DataFrame(teachers_data[1:], columns=teachers_data[0]).applymap(str.strip)
    df_teachers.columns = df_teachers.columns.str.lower()
    # print("Columns in Google Sheet:", df.columns)
    # if 'course' not in df.columns:
    #     print("Error: 'course' column not found in the Google Sheet.")
    #     return
    # Function to create tables if they do not exist
    def create_table(conn, create_table_query, table_name):
        try:
            cursor = conn.cursor()
            cursor.execute(create_table_query)
            conn.commit()
            cursor.close()
            print(f"Table '{table_name}' checked/created successfully.")
        except psycopg2.Error as e:
            print(f"Error creating table '{table_name}': {e}")
            conn.rollback()

    def create_all_tables(conn):
        create_courses_table_query = """
        CREATE TABLE IF NOT EXISTS courses (
            course_id serial PRIMARY KEY,
            course_name character varying(100) NOT NULL UNIQUE
        );
        """
    
        create_users_table_query = """
        CREATE TABLE IF NOT EXISTS users (
            user_id serial PRIMARY KEY,
            user_type character varying(10) NOT NULL,
            user_name character varying(100) NOT NULL,
            email character varying(100) NOT NULL UNIQUE,
            telegram_user_id bigint NOT NULL UNIQUE
        );
        """
        
        create_students_table_query = """
        CREATE TABLE IF NOT EXISTS students (
            student_id serial PRIMARY KEY,
            student_name character varying(100) NOT NULL,
            student_email character varying(100) NOT NULL UNIQUE,
            phone_number character varying(20)
        );

        """

        create_teacher_courses_table = """
        CREATE TABLE IF NOT EXISTS teacher_courses (
        teacher_id INT REFERENCES teachers(teacher_id) ON DELETE CASCADE,
        course_id INT REFERENCES courses(course_id) ON DELETE CASCADE,
        PRIMARY KEY (teacher_id, course_id)
    );
        """     
        create_teachers_table_query = """
        CREATE TABLE IF NOT EXISTS teachers (
            teacher_id serial PRIMARY KEY,
            teacher_name character varying(100) NOT NULL,
            teacher_email character varying(100) NOT NULL UNIQUE
        );
        """

        
        create_enrollments_table_query = """
        CREATE TABLE IF NOT EXISTS enrollments (
            enrollment_id serial PRIMARY KEY,
            student_id integer,
            course_id integer,
            CONSTRAINT enrollments_course_unique UNIQUE (student_id, course_id),
            CONSTRAINT enrollments_course_id_fkey FOREIGN KEY (course_id)
                REFERENCES public.courses (course_id) MATCH SIMPLE
                ON UPDATE NO ACTION
                ON DELETE NO ACTION,
            CONSTRAINT enrollments_student_id_fkey FOREIGN KEY (student_id)
                REFERENCES public.students (student_id) MATCH SIMPLE
                ON UPDATE NO ACTION
                ON DELETE NO ACTION
        );

        """
        
        create_attendance_table_query = """
        CREATE TABLE IF NOT EXISTS attendance (
            attendance_id serial PRIMARY KEY,
            student_id integer,
            course_id integer,
            teacher_id integer,
            session_code character varying(10) NOT NULL,
            attendance_date date NOT NULL DEFAULT CURRENT_DATE,
            created_at timestamp,
            CONSTRAINT attendance_course_id_fkey FOREIGN KEY (course_id)
                REFERENCES public.courses (course_id) MATCH SIMPLE
                ON UPDATE NO ACTION
                ON DELETE CASCADE,
            CONSTRAINT attendance_student_id_fkey FOREIGN KEY (student_id)
                REFERENCES public.students (student_id) MATCH SIMPLE
                ON UPDATE NO ACTION
                ON DELETE CASCADE,
            CONSTRAINT attendance_teacher_id_fkey FOREIGN KEY (teacher_id)
                REFERENCES public.teachers (teacher_id) MATCH SIMPLE
                ON UPDATE NO ACTION
                ON DELETE CASCADE
        );
        """
        
        create_table(conn, create_courses_table_query, 'courses')
        create_table(conn, create_users_table_query, 'users')
        create_table(conn, create_students_table_query, 'students')
        create_table(conn, create_teachers_table_query, 'teachers')
        create_table(conn, create_enrollments_table_query, 'enrollments')
        create_table(conn, create_attendance_table_query, 'attendance')
        create_table(conn, create_teacher_courses_table, 'teacher_courses')

    # Functions to insert data into tables
    def insert_courses(conn, courses):
        cursor = conn.cursor()
        course_prefix = "NUET"
        course_suffixes = ["Math", "Crit"]

        for course in courses:
            if course == '':
                continue
            for suffix in course_suffixes:
                # Формируем полное название курса с префиксом и суффиксом
                course_name = f"{course_prefix} {course} {suffix}"
                cursor.execute(
                    """
                    INSERT INTO courses (course_name) 
                    VALUES (%s) 
                    ON CONFLICT (course_name) DO NOTHING
                    """,
                    (course_name,)
                )
        conn.commit()
        cursor.close()


    def insert_teachers(conn, teachers):
        cursor = conn.cursor()
        for teacher_name, teacher_email in teachers:
            cursor.execute(
                """
                INSERT INTO teachers (teacher_name, teacher_email) 
                VALUES (%s, %s) 
                ON CONFLICT (teacher_email) DO NOTHING
                """,
                (teacher_name, teacher_email)
            )
        conn.commit()
        cursor.close()

    def insert_students(conn, students):
        cursor = conn.cursor()
        course_prefix = "NUET"
        course_suffixes = ["Math", "Crit"]

        for student_name, student_email, course, package, phone in students:
            if package.upper() == 'EXPLORER' or student_name == '':
                continue
            
            # Проверка существования студента по email
            cursor.execute("SELECT student_id FROM students WHERE student_email = %s", (student_email,))
            student_id = cursor.fetchone()
            
            if not student_id:
                cursor.execute(
                    """
                    INSERT INTO students (student_name, student_email, phone_number)
                    VALUES (%s, %s, %s) RETURNING student_id
                    """,
                    (student_name, student_email, phone)
                )
                student_id = cursor.fetchone()[0]
            else:
                student_id = student_id[0]
            
            # Добавление записей в enrollments для каждого курса Math и Crit для данного потока
            for suffix in course_suffixes:
                full_course_name = f"{course_prefix} {course} {suffix}"
                
                # Получение course_id для соответствующего курса
                cursor.execute("SELECT course_id FROM courses WHERE course_name = %s", (full_course_name,))
                course_id = cursor.fetchone()
                
                if not course_id:
                    print(f"Course '{full_course_name}' not found in courses table.")
                    continue
                course_id = course_id[0]
                
                # Проверка наличия записи в enrollments перед вставкой
                cursor.execute(
                    """
                    SELECT 1 FROM enrollments 
                    WHERE student_id = %s AND course_id = %s
                    """,
                    (student_id, course_id)
                )
                enrollment_exists = cursor.fetchone()

                if not enrollment_exists:
                    # Вставка записи в enrollments, если записи еще нет
                    cursor.execute(
                        """
                        INSERT INTO enrollments (student_id, course_id)
                        VALUES (%s, %s)
                        """,
                        (student_id, course_id)
                    )

        conn.commit()
        cursor.close()


    if conn is not None:
        # Создаем таблицы
        create_all_tables(conn)
        
        # Вставляем курсы, полученные из "Поток" студентов
        courses = df_students['поток'].unique()
        insert_courses(conn, courses)
        
        # Вставляем преподавателей из второго листа
        teachers = df_teachers[['name', 'email']].drop_duplicates().values
        insert_teachers(conn, teachers)
        
        # Вставляем студентов с фильтрацией пакета "EXPLORER"
        students = df_students[['фио', 'почта', 'поток', 'пакет', 'номер']].values
        insert_students(conn, students)

        conn.close()
    else:
        print("Failed to connect to the database.")

# def check_registration_status():
#     # Подключаемся к PostgreSQL
#     conn = psycopg2.connect(
#         dbname=database,
#         user=username,
#         host=hostname,
#         port=port_id,
#         password=password
#     )

#     # Извлекаем всех зарегистрированных пользователей из таблицы users
#     cursor = conn.cursor()
#     cursor.execute("SELECT user_name FROM users")
#     registered_users = set([row[0].strip().lower() for row in cursor.fetchall()])

#     # Получаем все значения из Google Sheet (Sheet1 - студенты)
#     data = students_wrs.get_all_values()
#     df_students = pd.DataFrame(data[1:], columns=data[0])
#     df_students.columns = df_students.columns.str.strip().str.lower()  # Убедимся, что названия колонок приведены к нижнему регистру

#     # Подготовим список обновлений для Google Sheets
#     updates = []

#     # Проверка статуса регистрации для каждого студента
#     for index, row in df_students.iterrows():
#         student_name = row['фио'].strip().lower()  # Извлекаем имя студента из столбца "ФИО"
#         if student_name in registered_users:
#             updates.append({"range": f"F{index + 2}", "values": [["registered"]]})  # Обновляем статус регистрации
#         else:
#             updates.append({"range": f"F{index + 2}", "values": [[""]]})  # Оставляем пустым, если не зарегистрирован

#     # Выполняем пакетное обновление значений в Google Sheets
#     body = {"valueInputOption": "RAW", "data": updates}
#     students_wrs.batch_update(body)

#     cursor.close()
#     conn.close()




schedule.every(24).hours.do(update_database)

    # schedule.every().minutes.do(check_registration_status)


# Run the scheduler in a separate thread
def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(1)


# Start the scheduler thread
scheduler_thread = threading.Thread(target=run_scheduler)
scheduler_thread.start()
