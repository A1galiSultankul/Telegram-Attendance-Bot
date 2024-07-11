import random
import string

def generate_random_password(length=6):
    # Define the characters to choose from
    characters = string.ascii_letters + string.digits
    
    # Generate a random password
    password = ''.join(random.choice(characters) for i in range(length))
    
    return password

def cur_password(new_pwd=None):
    global password
    if new_pwd:
        password = new_pwd
    return password


