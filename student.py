import gsheet
import teacher


def list_size():
    list = gsheet.get_data()
    return len(list)

def password_checker(name):
    return input("Enter the password ,{name}:")