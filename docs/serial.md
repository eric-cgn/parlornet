# Serial connections

Many period computers have a serial port and no network card. They can still reach a parlor, as long as whatever sits at the other end of the serial line can make a TCP connection for them. This page describes the usual ways to do that, from a WiFi modem to a login on a Linux machine, says which port each kind of program should reach, and ends with notes on the cable.

## Which port to reach

A parlor serves each kind of program on a port of its own, so the port depends on the program you run, not on how the serial line gets out.

| Program | Port |
|---|---|
| ParlorNet for the Apple IIe ([apple2.md](apple2.md)), and any program that speaks ParlorNet's own protocol, PARLORNET/1 | 6501 |
| A terminal program, for the telnet service's menus and boards | 6502 |
| Worthy Opponent ([its page](clients/worthy-opponent.md)) | 6519 |
| GEnie's RSCARDS front ends ([their page](clients/rscards.md)); a terminal that reaches it by mistake is offered the telnet service's games there | 6520 |

On the RSCARDS port, 6520, a terminal that is no RSCARDS program gets a short guide after about 12 seconds, 40 columns wide: start the RSCARDS program now, or press T for the text games, which then run on the same connection as on 6502. So a C64 or a VT100 that dialed 6520 need not dial again.

The other protocols and their ports are in [protocols.md](protocols.md). Most of them belong to programs that open TCP connections of their own, so they seldom come over a serial line.

On the native port, 6501, the parlor waits 20 seconds for the program's first line and then hangs up. A program that dials, such as ParlorNet for the Apple IIe, sends it right after CONNECT; when you make the connection by hand, start the program's side straight away.

The telnet service, 6502, opens by asking the terminal for its type and window size in telnet's own codes. A WiFi modem in telnet mode or the `telnet` program in a shell answers them. Over a plain connection, such as nc gives, they show as a few odd characters at the top of the first screen, and the parlor, which then cannot tell what the terminal is, starts in the `pc` display. Its menu shows a sample of each display, so pick the one that looks right. From a Unix shell, `telnet parlornet.org 6502` is the better way to the telnet service.

## A WiFi modem

A WiFi modem plugs into the serial port and behaves like a Hayes modem whose phone numbers are host names. Zimodem and the many other modems built on the ESP8266 or ESP32 work this way. Dial the parlor's name and port, such as `ATDT parlornet.org:6501`, and the program carries on after CONNECT as it would over a phone line.

Some of these modems can also treat the connection as telnet; on a Zimodem, the T in ATDT turns that on. The native port needs every byte passed as it is, so dial it in the modem's plain mode, which on a Zimodem is `ATD"parlornet.org:6501"`. Zimodem's telnet handling follows its register S62: with `ATS62=0` an ATDT dial is plain. Its dial command also reads letters after ATD as options, so put the host in quotes. ParlorNet's Apple II and Commodore 64 programs dial `ATDT` and the number in their settings (`ATDP` with Pulse dial on), and a number that starts with AT is sent as the whole dial command. So on a Zimodem, set the number to `ATD"parlornet.org:6501"`. Another way is the init string `ATS62=0` (or `ATS62=0&W` saved in the modem once) with the number `"parlornet.org:6501"` in quotes. Either way leave Pulse dial off: on a Zimodem, ATDP means PETSCII translation, not pulse dialing. The RSCARDS and Worthy Opponent services work either way.

## A login on a Unix machine

If the serial line runs to a Linux machine, such as a Raspberry Pi or an old PC with a USB serial adapter, and that machine offers a login on the port, the period computer's terminal program can log in to a shell there. The shell then makes the connection. On most Linux systems agetty runs the login: `agetty -L ttyUSB0 9600 vt100` answers on the first USB serial adapter at 9600 bit/s and ignores the modem control lines, and systemd's `serial-getty@ttyUSB0` service runs agetty for you. Other Unix systems have their own getty.

### By hand, with nc

Log in and type:

```
stty raw -echo; nc parlornet.org 6501; stty sane
```

`stty raw -echo` passes every byte through unchanged and stops the shell from echoing what the program sends, nc connects to the parlor, and `stty sane` puts the terminal back when the connection ends. Then start the program's side at once. On the Apple IIe, press Open-Apple-P instead of Return, as [apple2.md](apple2.md#through-a-unix-shell) explains.

The versions of nc differ in one way that matters here. The traditional nc (Debian's netcat-traditional), BusyBox's nc, GNU netcat and the nc of macOS end as soon as the parlor hangs up. OpenBSD's nc (netcat-openbsd, the usual one on Debian and Ubuntu) and Nmap's ncat do not notice the hang-up until the program sends something more: the next key or two you press ends them, and those keys go nowhere. OpenBSD nc's `-N` and `-q` options are about the end of your own input, which never comes from a terminal, so they do not change this. If it bothers you, use `busybox nc` or install the traditional nc.

### pndial, for programs that dial

Many period programs expect a modem: they send AT commands, dial a number and wait for CONNECT. pndial is a short shell script that plays that modem in the shell, so such a program can dial the parlor through a Unix login. It is [tools/pndial](../tools/pndial) in ParlorNet's source. Copy it to the Unix machine, for example with `curl -O https://raw.githubusercontent.com/eric-cgn/parlornet/main/tools/pndial`, and make it executable with `chmod +x pndial`. It needs only sh, stty, tr and nc, and it runs under dash, bash, BusyBox sh and the sh of macOS, with any of the versions of nc above.

Log in from the program's terminal, run pndial, then let the program dial:

```
pndial [-z] [host [port]]
```

The host and port are where a dial goes when the number does not name a parlor itself. They default to parlornet.org and 6501, so for a program that does not speak PARLORNET/1, give the port it needs from the table above. The number dialed chooses the parlor like this:

- `ATDT host:port` dials that host and port, such as `ATDT parlornet.org:6520`.
- `ATDT host` dials that host on the port given to pndial. The name must have a dot in it, such as `ATDT myparlor.example.org`.
- Any other number, such as `ATDT5551212` or `ATDT parlornet`, dials the host and port given to pndial. A program that can only dial digits, or that still holds an old phone number, works unchanged this way.

#### pndial as ATZ

A program that dials usually resets its modem first with `ATZ`. Installed under that name, pndial lets the program start it, so a plain shell login answers like a modem with no one typing at the shell. Make a link named ATZ to pndial in a directory on your PATH:

```
ln -s pndial ~/.local/bin/ATZ
```

(with pndial in `~/.local/bin` too), or add an alias to the shell's startup file, such as `~/.bashrc`, which suits a system where links are awkward:

```
alias ATZ='pndial -z'
```

The program sends `ATZ` and a carriage return to the shell prompt. The shell echoes the line, as a modem echoes a command, and runs pndial, which skips its banner and answers just OK, as the modem's reply to the reset. It then waits for commands as usual: any further command gets OK, and the dial gets CONNECT. A lowercase `atz` link works too, and `-z` gives the same start from the command line. Host and port arguments can follow, in the alias or after `-z`, for numbers that do not name a parlor.

Some examples:

- For ParlorNet for the Apple IIe, install pndial as ATZ, log the line in to the shell once from the program's console (C), then press Open-Apple-Q for the start screen and P. The program sends ATZ, pndial answers OK, the program dials `ATDTparlornet.org:6501` (the number in its settings), and on CONNECT it signs in at once, so there is no race with the parlor's 20 seconds. When you close the connection with Open-Apple-Q, the parlor hangs up; OpenBSD's nc notices when the program sends `+++` after it, and pndial says NO CARRIER and waits for the next dial, which P makes. Control-C in the console ends pndial and brings back the shell prompt. Without the ATZ name, run `pndial` in the console, then press Open-Apple-Q and P.
- For RSCARDS on a Mac, run `pndial parlornet.org 6520` and leave the front end's dial string as it is. It still holds GEnie's number, which now dials the parlor. Command-D dials.
- For RSCARDS on an IBM PC, run `pndial parlornet.org 6520`, dial from DOS with `echo ATDT5551212 > COM1`, then start `RSCARDS 1`.
- For Worthy Opponent, run `pndial parlornet.org 6519` and dial any entry of the phone book, or put `ATDT parlornet.org:6519` in it as its page says.

pndial echoes the commands it is sent, as a modem does, until `ATE0` turns that off. It answers every other AT command with OK, and a line that does not start with AT gets no answer. On a dial it says CONNECT with the line's speed (`CONNECT 9600`), joins the line to the parlor with nc and passes every byte both ways unchanged and unechoed. When either side hangs up it says NO CARRIER, puts the line back into command mode and waits for the next dial, as a modem would, so a program can dial again without anyone typing at the shell. Control-C or Control-D while it waits puts the terminal back as it was and ends pndial.

It is a few dozen lines on purpose, and not a full modem. It says CONNECT before the connection is made, so a parlor that cannot be reached gives CONNECT and then NO CARRIER. During a connection every key goes to the parlor, Control-C included, so hang up from the program by signing off or closing its connection; there is no `+++` escape. It has no numeric result codes, no S-registers and no answering of calls. With OpenBSD's nc or ncat, NO CARRIER waits for the program's next key or two, as described above; run it as `NC='busybox nc' pndial` to use BusyBox's nc instead. For more than this, use tcpser or a WiFi modem.

## tcpser

tcpser, by Jim Brain, emulates a whole Hayes modem on a computer's serial port and dials TCP hosts. `tcpser -d /dev/ttyUSB0 -s 9600` answers on that port at 9600 bit/s, and `ATDT parlornet.org:6501` dials as on a WiFi modem. Its phone book maps a number to a parlor, so `-n5551212=parlornet.org:6520` lets a program that dials only digits reach RSCARDS. Emulators often have such a modem built in: DOSBox's `serial1=modem`, for one, dials `ATDT host:port`.

## A direct bridge

When the program does not dial at all, a bridge can join the serial port straight to the parlor's port, with no modem in between. With socat, on the machine the period computer's cable runs to:

```
socat /dev/ttyUSB0,b9600,raw,echo=0 TCP:parlornet.org:6501
```

A serial port server such as ser2net can do the same job; its own documentation covers connecting out. The bridge connects as soon as it starts, so for the native port start it just before the program connects, within the parlor's 20 seconds. ParlorNet for the Apple IIe then connects with P.

## Dialing in by phone

A dial-up service for real modems is planned: a phone number that a period modem can call. A program that sends PARLORNET/1 within a few seconds of CONNECT will reach the native protocol, and anything else, such as a terminal program, will reach the telnet service.

## The cable

- Most computers' serial ports are wired the same way, as terminals, so a cable between two computers needs its send and receive lines crossed: a null-modem cable, or a null-modem adapter on a straight one. A modem, WiFi or not, takes a straight cable. Some serial cards, such as the Apple's Super Serial Card, have a jumper block that sets them for a modem or a terminal and so can do the crossing themselves.
- Both ends must use the same speed and format. Use 8N1: 8 data bits, no parity and 1 stop bit. 9600 or 19200 bit/s suits the Apple IIe program, RSCARDS runs at 1200 or 2400 and Worthy Opponent at 1200. On Linux the getty sets the line's speed, or `stty -F /dev/ttyUSB0 9600` for a bridge.
- For flow control use RTS/CTS, with a cable that carries those lines and both ends set for it (`stty crtscts` on Linux), or none. XON/XOFF does not survive a raw line: once the terminal is raw, as with nc, pndial or socat, the XON and XOFF characters go to the parlor as data.
- With `agetty -L` the Linux side ignores carrier detect. Without `-L` the login waits for carrier detect, which a null-modem cable usually takes from the period computer's DTR line.
