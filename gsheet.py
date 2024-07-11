import gspread
import pandas as pd
import psycopg2

# Google Sheets setup
sa = gspread.service_account(filename="service_account.json")
sh = sa.open("students_list")
wrs = sh.worksheet("Sheet1")

# PostgreSQL connection details
hostname = 'localhost'
database = 'tg_users'
username = 'alisherma'
port_id = 5432

# Get all values in the sheet
data = wrs.get_all_values()

# Convert to a DataFrame
df = pd.DataFrame(data[1:], columns=data[0])
print(df.columns)
# Print column names to verify
print("Columns in DataFrame:", df.columns)

# Function to connect to PostgreSQL
def connect_to_postgresql(dbname, user, host, port):
    try:
        conn = psycopg2.connect(
            dbname=dbname,
            user=user,
            host=host,
            port=port
        )
        return conn
    except psycopg2.Error as e:
        print(f"Error connecting to PostgreSQL: {e}")
        return None

# Functions to create tables if they do not exist
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
    create_attendance_table_query = """
    CREATE TABLE IF NOT EXISTS attendance (
        attendance_id serial PRIMARY KEY,
        student_id integer,
        course_id integer,
        teacher_id integer,
        session_code character varying(10) NOT NULL,
        attendance_date date NOT NULL DEFAULT CURRENT_DATE,
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
        student_email character varying(100) NOT NULL UNIQUE
    );
    """
    create_teachers_table_query = """
    CREATE TABLE IF NOT EXISTS teachers (
        teacher_id serial PRIMARY KEY,
        teacher_name character varying(100) NOT NULL,
        course_id integer,
        teacher_email character varying(100) NOT NULL,
        CONSTRAINT teachers_course_id_fkey FOREIGN KEY (course_id)
            REFERENCES public.courses (course_id) MATCH SIMPLE
            ON UPDATE NO ACTION
            ON DELETE CASCADE
    );
    """
    create_enrollments_table_query = """
    CREATE TABLE IF NOT EXISTS enrollments (
        enrollment_id serial PRIMARY KEY,
        student_id integer,
        course_id integer,
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

    create_table(conn, create_attendance_table_query, 'attendance')
    create_table(conn, create_courses_table_query, 'courses')
    create_table(conn, create_users_table_query, 'users')
    create_table(conn, create_students_table_query, 'students')
    create_table(conn, create_teachers_table_query, 'teachers')
    create_table(conn, create_enrollments_table_query, 'enrollments')

# Functions to insert data into tables
def insert_courses(conn, courses):
    cursor = conn.cursor()
    for course in courses:
        cursor.execute("INSERT INTO courses (course_name) VALUES (%s) ON CONFLICT (course_name) DO NOTHING RETURNING course_id", (course,))
    conn.commit()
    cursor.close()

def insert_teachers(conn, teachers):
    cursor = conn.cursor()
    for teacher_name, course_name, teacher_email in teachers:
        cursor.execute("SELECT course_id FROM courses WHERE course_name = %s", (course_name,))
        course_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO teachers (teacher_name, course_id, teacher_email) VALUES (%s, %s, %s) RETURNING teacher_id", 
                       (teacher_name, course_id, teacher_email))
    conn.commit()
    cursor.close()

def insert_students(conn, students):
    cursor = conn.cursor()
    for student_name, student_email, course_name, teacher_name in students:
        cursor.execute("SELECT course_id FROM courses WHERE course_name = %s", (course_name,))
        course_id = cursor.fetchone()
        
        if not course_id:
            continue
        
        course_id = course_id[0]
        
        cursor.execute("INSERT INTO students (student_name, student_email) VALUES (%s, %s) ON CONFLICT (student_email) DO NOTHING RETURNING student_id", 
                       (student_name, student_email))
        student_record = cursor.fetchone()
        
        if student_record:
            student_id = student_record[0]
        else:
            cursor.execute("SELECT student_id FROM students WHERE student_email = %s", (student_email,))
            existing_student_id = cursor.fetchone()
            
            if existing_student_id:
                student_id = existing_student_id[0]
            else:
                continue
        
        cursor.execute("INSERT INTO enrollments (student_id, course_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (student_id, course_id))
    
    conn.commit()
    cursor.close()

# Connect to PostgreSQL
conn = connect_to_postgresql(database, username, hostname, port_id)

if conn is not None:
    # Ensure all tables exist
    create_all_tables(conn)
    
    # Insert courses
    courses = df['course'].unique()
    insert_courses(conn, courses)
    
    # Insert teachers
    teachers = df[['teacher', 'course', 'teacher_email']].drop_duplicates().values
    insert_teachers(conn, teachers)
    
    # Insert students
    students = df[['student name', 'email', 'course', 'teacher']].values
    insert_students(conn, students)
    
    # Close the connection
    conn.close()
else:
    print("Failed to connect to the database.")
