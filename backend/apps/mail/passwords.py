import secrets

from argon2 import PasswordHasher

# libsodium (яким Dovecot перевіряє ARGON2ID) підтримує лише parallelism=1
_hasher = PasswordHasher(time_cost=3, memory_cost=32768, parallelism=1, hash_len=32, salt_len=16)

ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_client_password() -> str:
    groups = ["".join(secrets.choice(ALPHABET) for _ in range(5)) for _ in range(5)]
    return "-".join(groups)  # ~140 біт ентропії


def dovecot_hash(password: str) -> str:
    return "{ARGON2ID}" + _hasher.hash(password)
