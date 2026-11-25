"""R9: deleting an account frees the name but tables keep seats and owners by
name. Whoever signs up with the freed name next plays, resigns and owns the
deleted player's tables.

    python3.13 review/r9_deleted_name_reuse.py
"""
import asyncio
import os
import re
import time

from harness import Client, Stack


async def main():
    s = Stack("password", admin=True).start()
    try:
        a = await Client.connect(s.core)
        for l in ("HELO alice", "AUT password alicepass1", "AUT confirm alicepass1"):
            await a.req(l, show=False)
        b = await Client.connect(s.core)
        for l in ("HELO bob", "AUT password bobpass11", "AUT confirm bobpass11"):
            await b.req(l, show=False)
        tid = (await a.req("NEW reversi/1 standard vis=private"))[-1].split()[1]
        await a.req("JON %s" % tid)
        await b.req("INV %s bob" % tid, show=False)
        await a.req("INV %s bob" % tid)
        await b.req("JON %s" % tid)
        a.close()

        # the operator deletes alice through the admin socket (admin-role key, setup password from the log)
        from adapters.admin import AdminCore
        time.sleep(0.5)
        setup = re.search(r"setup password:\s+(\S+)", s.core_output()).group(1)
        core = await AdminCore.connect("127.0.0.1", s.admin_port, os.path.join(s.keys, "admin.key"))
        print("admin login:", await core.req("login", password=setup))
        print("admin set_password:", await core.req("set_password", password="adminpass1"))
        print("admin delete alice:", await core.req("delete", name="alice"))
        core.close()

        m = await Client.connect(s.core)
        print("\n-- someone else signs up as alice --")
        for l in ("HELO alice", "AUT password mallory99", "AUT confirm mallory99"):
            await m.req(l, show=False)
        await m.req("LST")
        await m.req("VUE %s" % tid)
        await m.req("RES %s" % tid)
        m.close()
        b.close()
    finally:
        s.stop()


asyncio.run(main())
