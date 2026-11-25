import os, stat
for pid in sorted((p for p in os.listdir("/proc") if p.isdigit()), key=int):
    try:
        cmd = open("/proc/%s/cmdline" % pid, "rb").read().replace(b"\0", b" ").decode()[:70]
        uid = [l.split()[1] for l in open("/proc/%s/status" % pid) if l.startswith("Uid:")][0]
        cap = [l.split()[1] for l in open("/proc/%s/status" % pid) if l.startswith("CapEff:")][0]
        print(pid, "uid", uid, "capeff", cap, cmd)
    except OSError:
        pass
for p in ["/data", "/data/frontends"] + sorted("/data/" + f for f in os.listdir("/data")) + sorted("/data/frontends/" + f for f in os.listdir("/data/frontends")):
    st = os.stat(p)
    print(oct(st.st_mode & 0o777), st.st_uid, st.st_gid, p)
