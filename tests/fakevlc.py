#!/usr/bin/env python3
"""A stand-in for VLC used by the tests: speaks the rc interface on --rc-host and 'plays' for a few seconds.

  --fake-run=N     play for N seconds, then exit 0          --fake-exit=C   exit at once with code C
  --fake-no-rc     do not open the remote-control socket    --start-time=S  begin at S seconds
"""
import socket
import sys
import threading
import time

args = sys.argv[1:]
host = port = None
start = 0.0
run_for = 3.0
exit_code = None
no_rc = False
for a in args:
    if a.startswith("--rc-host="):
        host, port = a.split("=", 1)[1].rsplit(":", 1)
        port = int(port)
    elif a.startswith("--start-time="):
        start = float(a.split("=", 1)[1])
    elif a.startswith("--fake-run="):
        run_for = float(a.split("=", 1)[1])
    elif a.startswith("--fake-exit="):
        exit_code = int(a.split("=", 1)[1])
    elif a == "--fake-no-rc":
        no_rc = True
if exit_code is not None:
    sys.exit(exit_code)
t0 = time.time()
paused = [False]
offset = [start]
stop = threading.Event()


def pos():
    return int(offset[0] + ((time.time() - t0) if not paused[0] else 0))


def serve(srv):
    conn, _ = srv.accept()
    conn.sendall(b"VLC media player 3.0.20 Vetinari\r\nCommand Line Interface initialized. Type `help' for help.\r\n> ")
    buf = b""
    while not stop.is_set():
        try:
            d = conn.recv(1024)
        except OSError:
            break
        if not d:
            break
        buf += d
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            cmd = line.strip().decode()
            if cmd == "get_time":
                conn.sendall(f"{pos()}\r\n> ".encode())
            elif cmd == "get_length":
                conn.sendall(b"600\r\n> ")
            elif cmd == "pause":
                if not paused[0]:
                    offset[0] = pos()
                else:
                    t0_shift = time.time()
                    globals()["t0"] = t0_shift
                paused[0] = not paused[0]
                conn.sendall(b"> ")
            elif cmd.startswith("seek"):
                offset[0] = float(cmd.split()[1])
                globals()["t0"] = time.time()
                conn.sendall(b"> ")
            elif cmd == "quit":
                stop.set()
                conn.sendall(b"> ")
                break
            else:
                conn.sendall(b"> ")


if not no_rc and host:
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(1)
    threading.Thread(target=serve, args=(server,), daemon=True).start()
while not stop.is_set() and time.time() - t0 < run_for:
    time.sleep(0.05)
sys.exit(0)
