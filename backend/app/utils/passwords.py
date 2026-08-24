import secrets
import string


def generate_password(length: int = 12) -> str:
    """
    Generates a random password guaranteed to include at least one lowercase,
    one uppercase, one digit, and one symbol — used when the operator chooses
    auto-generate instead of typing a password by hand.
    """
    alphabet = string.ascii_letters + string.digits + "!@#$%&*"
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in pwd) and any(c.isupper() for c in pwd)
                and any(c.isdigit() for c in pwd) and any(c in "!@#$%&*" for c in pwd)):
            return pwd
