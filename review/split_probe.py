"""Run inside the web container (as its uid): what a compromised web process sees."""
import os, socket, sys, time
print("uid", os.getuid(), "| files: /run/parlornet:", os.listdir("/run/parlornet"), "| /data:", os.listdir("/data") if os.access("/data", os.R_OK) else "not readable")
for port in (6590, 6599, 6501):
    s = socket.create_connection(("parlord", port), 3); print("parlord:%d reachable" % port); s.close()
for name in ("pnrt-telnet", "pnrt-admin"):
    try:
        socket.create_connection((name, 6510 if "admin" in name else 6502), 3).close(); print(name, "reachable")
    except OSError as e:
        print(name, type(e).__name__)
if sys.argv[1:] == ["hold"]:
    held = [socket.create_connection(("parlord", 6590), 3) for _ in range(32)]
    print("holding 32 idle connections to parlord:6590", flush=True)
    time.sleep(8)
