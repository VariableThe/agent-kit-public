#!/usr/bin/env python3
"""Load only the P8 key into the child process. The env file is never shell code."""
import os
from pathlib import Path
import re
import sys

def main():
    split = sys.argv.index("--")
    command = sys.argv[1:split]
    if not command:
        raise ValueError("Supply the existing Pi executable before --")
    directory = Path(__file__).resolve().parent
    text = (directory / "key.env").read_text().strip()
    if not re.fullmatch(r"P8_ROUTER_API_KEY=[A-Za-z0-9_.-]+", text):
        raise ValueError("Invalid P8 key file")
    env = dict(os.environ)
    env["P8_ROUTER_API_KEY"] = text.split("=", 1)[1]
    env["PI_CODING_AGENT_DIR"] = str(directory)
    os.execvpe(command[0], command + sys.argv[split + 1:], env)

if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError):
        print("P8 launcher failed; check the private key file and Pi executable.", file=sys.stderr)
        sys.exit(1)
