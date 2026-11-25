"""R13 (informational): a non-ASCII byte in an answer reached
hmac.compare_digest as U+FFFD and raised TypeError (ERR INTERNAL). Now it is a
wrong answer.

    python3.13 review/r13_nonascii.py
"""
import asyncio

from harness import Client, Stack


async def main():
    s = Stack("password", "email")
    s.seed("dave", password="Correct horse 1", email="dave@example.org")
    s.start()
    try:
        c = await Client.connect(s.core)
        await c.req("HELO dave")
        await c.req("AUT password reset")
        c.w.write(b"AUT password 12345\xff\r\n")
        print("> AUT password 12345\\xff\n<", await c.line())
        c.close()
        await asyncio.sleep(0.3)
        print("core log:", [l for l in s.core_output().splitlines() if "internal" in l])
    finally:
        s.stop()


asyncio.run(main())
