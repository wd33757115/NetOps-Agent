"""Reject tracked files excluded by the repository distribution rules."""

import subprocess
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--ignored", "--exclude-standard", "-z"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
    )
    paths = [p for p in result.stdout.decode("utf-8").split("\0") if p]
    if paths:
        print("Files outside the source/manual/deployment scope are tracked:")
        for path in paths:
            print(f"  {path}")
        print("Remove them from the Git index with git rm --cached; keep local data on disk.")
        return 1
    print("Repository distribution scope: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
