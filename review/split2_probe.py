"""Run inside a split-mode extension container: what can a compromised extension reach?
argv: list of host:port to try; 'flood HOST PORT SECS' opens idle sockets for SECS."""
import os, socket, sys, time
if sys.argv[1] == "flood":
    host, port, secs = sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
    end, n, held = time.time() + secs, 0, []
    while time.time() < end:
        try:
            held.append(socket.create_connection((host, port), 1)); n += 1
        except OSError:
            time.sleep(0.01)
        if len(held) > 500:
            for s in held[:250]: s.close()
            del held[:250]
    print("flooded %s:%d with %d idle sockets" % (host, port, n))
    sys.exit()
print("uid", os.getuid())
for t in sys.argv[1:]:
    h, p = t.rsplit(":", 1)
    try:
        socket.create_connection((h, int(p)), 2).close(); print("  reach  ", t)
    except OSError as e:
        print("  blocked", t, type(e).__name__)
