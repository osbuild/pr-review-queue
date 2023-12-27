"""Encrypt / Decrypt Slack Nicks Helper

Helper script to enrypt/decrypt the files
slack_nicks.yaml/slack_nicks_encrypted.yaml vice versa

Usage:
  encrypt_slack_nicks.py [--decrypt | --generate_new_key]

Options:
  -d --decrypt           Decrypt slack_nicks_encrypted.yaml to slack_nicks.yaml
                         (default: encrypt slack_nicks.yaml to slack_nicks_encrypted.yaml)
  -g --generate_new_key  Just print a new key to be used for encryption/decryption

"""
import os
import yaml
from cryptography.fernet import Fernet
from docopt import docopt


def encrypt_values(data, key):
    cipher_suite = Fernet(key)
    encrypted_data = {}

    for k, v in data.items():
        encrypted_value = cipher_suite.encrypt(f"{v}".encode()).decode('utf-8')
        encrypted_data[k] = encrypted_value

    return encrypted_data

def decrypt_values(data, key):
    cipher_suite = Fernet(key)
    decrypted_data = {}

    for k, v in data.items():
        decrypted_value = cipher_suite.decrypt(f"{v}".encode()).decode('utf-8')
        decrypted_data[k] = decrypted_value

    return decrypted_data

def encrypt_yaml(file_path, key):
    with open(file_path, 'r') as file:
        original_data = yaml.safe_load(file)

    encrypted_values = encrypt_values(original_data, key)

    encrypted_file_path = file_path.replace('.yaml', '_encrypted.yaml')

    with open(encrypted_file_path, 'w') as encrypted_file:
        yaml.dump(encrypted_values, encrypted_file, default_flow_style=False)

    print(f"Encryption complete. Encrypted YAML file saved at: {encrypted_file_path}")

def decrypt_yaml(file_path, key):
    with open(file_path, 'r') as file:
        original_data = yaml.safe_load(file)

    decrypted_values = decrypt_values(original_data, key)

    decrypted_file_path = file_path.replace('_encrypted.yaml', '.yaml')

    with open(decrypted_file_path, 'w') as decrypted_file:
        yaml.dump(decrypted_values, decrypted_file, default_flow_style=False)

    print(f"Decryption complete. Decrypted YAML file saved at: {decrypted_file_path}")

def main():
    arguments = docopt(__doc__, version='0.1')
    if arguments["--generate_new_key"]:
        key = Fernet.generate_key()

        print("SECRET KEY - DO NOT SHARE!")
        print(f"export SLACK_NICKS_KEY={key.decode()}")
        return
    if arguments["--decrypt"]:
        encrypted_file = "slack_nicks_encrypted.yaml"
        key = os.getenv('SLACK_NICKS_KEY')
        decrypt_yaml(encrypted_file, key)
    else:
        decrypted_file = "slack_nicks.yaml"
        key = os.getenv('SLACK_NICKS_KEY')
        encrypt_yaml(decrypted_file, key)

if __name__ == "__main__":
    main()
