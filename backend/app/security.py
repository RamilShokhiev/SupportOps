import hashlib
import hmac
import secrets


def hash_password(password):
    salt = secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 260000)
    return f'{salt}:{derived.hex()}'


def check_password(password, hashed):
    salt, expected = hashed.split(':')
    actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 260000).hex()
    return hmac.compare_digest(actual, expected)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def action_hash(title, body, repository):
    import json
    return hashlib.sha256(json.dumps([title, body, repository], ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
