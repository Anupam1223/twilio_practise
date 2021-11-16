from cryptography.fernet import Fernet


key = Fernet.generate_key()
fernet = Fernet(key)


def encrypt_token(token):
    return fernet.encrypt(token.encode()).decode()


def decrypt_token(token):
    data = bytes(token, "utf-8")
    return fernet.decrypt(data)
