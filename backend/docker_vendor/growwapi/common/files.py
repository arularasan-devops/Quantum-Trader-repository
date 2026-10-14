"""
The module provides utility functions for file operations.
"""

import os


def get_cwd(file: str = __file__) -> str:
    """
    Get the current working directory of the file.
    
    Args:
        file (str): The file path to get the directory of. Defaults to the current
            file.
    Returns:
        str: The current working directory
    """
    return os.path.dirname(os.path.abspath(file))

def generate_token_file(jwt_token):
    file_path = get_cwd() + '/a.creds'
    content = f"""{jwt_token}"""
    with open(file_path, 'w', encoding='UTF-8') as file:
        file.write(content)
    return file_path

def generate_seed_file(seed):
    file_path = get_cwd() + '/b.creds'
    content = f"""{seed}"""
    with open(file_path, 'w', encoding='UTF-8') as file:
        file.write(content)
    return file_path
