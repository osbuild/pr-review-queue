import os
import yaml
from cryptography.fernet import Fernet


def encrypt_values(data, key):
    cipher_suite = Fernet(key)
    encrypted_data = {}

    for k, v in data.items():
        encrypted_value = cipher_suite.encrypt(v.encode()).decode('utf-8')
        encrypted_data[k] = encrypted_value

    return encrypted_data

def encrypt_yaml(file_path, key):
    with open(file_path, 'r') as file:
        original_data = yaml.safe_load(file)

    encrypted_values = encrypt_values(original_data, key)

    encrypted_file_path = file_path.replace('.yaml', '_encrypted.yaml')

    with open(encrypted_file_path, 'w') as encrypted_file:
        yaml.dump(encrypted_values, encrypted_file, default_flow_style=False)

    print(f"Encryption complete. Encrypted YAML file saved at: {encrypted_file_path}")


if __name__ == "__main__":
    decrypted_file = "slack_nicks.yaml"
    key = os.getenv('SLACK_NICKS_KEY')
    encrypt_yaml(decrypted_file, key)
