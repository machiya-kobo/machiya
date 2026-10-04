"""`hister create-user` needs a real TTY (its prompt refuses piped input): this drives it in a pseudo-terminal, typing
the password from a file. The password is never printed; the transcript is filtered to the result line.
Usage: python3 tty_create_user.py PASSWORD_FILE podman run --rm -it ... create-user NAME --admin"""
import fcntl
import os
import pty
import re
import select
import struct
import sys
import termios
import time


def main():
    with open(sys.argv[1]) as f:
        password = f.read().strip()
    cmd = sys.argv[2:]
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(cmd[0], cmd)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 100, 0, 0))     # a 0x0 terminal confuses the TUI
    out, typed, deadline = b"", 0, time.time() + 40
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.5)
        if r:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
        plain = re.sub(rb"\x1b\[[0-9;?]*[A-Za-z]", b"", out)
        prompts = plain.count(b"assword:")
        if prompts > typed and typed < 2:
            time.sleep(0.3)
            os.write(fd, password.encode() + b"\r")
            typed += 1
    if os.environ.get("TTY_DEBUG"):
        with open(os.environ["TTY_DEBUG"], "wb") as f:
            f.write(out.replace(password.encode(), b"<password>"))
    if time.time() >= deadline:
        os.kill(pid, 15)
    _, status = os.waitpid(pid, 0)
    plain = re.sub(rb"\x1b\[[0-9;?]*[A-Za-z]", b"", out).decode(errors="replace")
    result = [line.strip() for line in plain.splitlines() if "User created" in line or "Failed" in line
              or "rror" in line]
    print("\n".join(result) or "(no result line)")
    code = os.waitstatus_to_exitcode(status)
    print("exit", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
