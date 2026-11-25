import socket, time, sys
def fibs():
    s = socket.create_connection(("127.0.0.1", 6505), 5); s.settimeout(4)
    time.sleep(0.3); s.recv(4096); s.sendall(b"alice\r\n"); time.sleep(0.3); s.sendall(b"x\r\n")
    data = b""; end = time.time() + 4
    while time.time() < end:
        try:
            d = s.recv(4096)
            if not d: break
            data += d
            if b"(y/n)" in data or b"answering" in data: break
        except socket.timeout: break
    s.close()
    return b"(y/n)" in data
def admin():
    s = socket.create_connection(("127.0.0.1", 6510), 5); s.settimeout(5)
    data = b""; end = time.time() + 5
    while time.time() < end and b"Display" not in data and b"answering" not in data:
        try:
            d = s.recv(4096)
            if not d: break
            data += d
        except socket.timeout: break
    s.close()
    return b"Display" in data
n = int(sys.argv[1])
print(sys.argv[2], "FIBS sign-ups reaching the core: %d/%d" % (sum(fibs() for _ in range(n)), n),
      "| admin screens reaching the core: %d/3" % sum(admin() for _ in range(3)))
