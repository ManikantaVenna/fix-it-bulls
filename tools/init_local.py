"""Create local settings once without printing secrets or replacing settings."""

import secrets
from pathlib import Path


def create_local_env(root):
    root = Path(root)
    destination = root / ".env"
    content = (root / ".env.example").read_text(encoding="utf-8")
    content = content.replace("SECRET_KEY=", "SECRET_KEY=" + secrets.token_urlsafe(48), 1)
    try:
        with destination.open("x", encoding="utf-8") as output:
            output.write(content)
    except FileExistsError:
        return False
    return True


if __name__ == "__main__":
    created = create_local_env(Path(__file__).resolve().parents[1])
    print("Created private .env; keep it out of Git." if created else "Existing .env preserved.")
