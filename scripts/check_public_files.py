"""Check tracked and publishable untracked files; never read local .env or runtime databases."""

import re
import subprocess
from pathlib import Path

PATTERNS = [
    re.compile(rb"\b\d{8,12}:[A-Za-z0-9_-]{35}\b"),  # Telegram bot tokens
    re.compile(rb"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
]


def main() -> None:
    tracked = (
        subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"]
        )
        .decode()
        .split("\0")
    )
    failures = []
    for name in filter(None, tracked):
        path = Path(name)
        if path.name.startswith(".env") and path.name != ".env.example":
            failures.append(name)
            continue
        if path.suffix in {".db", ".sqlite", ".sqlite3"}:
            failures.append(name)
            continue
        if path.is_file() and any(pattern.search(path.read_bytes()) for pattern in PATTERNS):
            failures.append(name)
    if failures:
        raise SystemExit("Potential private data in tracked files: " + ", ".join(failures))
    print("Tracked-file secret check passed.")


if __name__ == "__main__":
    main()
