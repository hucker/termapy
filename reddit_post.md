# Termapy — a serial terminal (as window/mac/linux terminal) that gets out of your way...but offers a clickable, scrollable, interface.

Docs: https://hucker.github.io/termapy/
GitHub: https://github.com/hucker/termapy

I built a serial terminal in Python for people who spend their days talking to embedded devices, possible on different OS (me PC/Mac) and needs it to work with git. I use it every day, so it's optimized for the workflows I actually need and works on Mac and PC (my work flow...), embedded C with a lot of Python experience.  

## Try it right now (no hardware needed)

First, install the python package manager [uv](https://docs.astral.sh/uv/) if you don't have it (one time):

    powershell -c "irm https://astral.sh/uv/install.ps1 | iex"   # Windows
    curl -LsSf https://astral.sh/uv/install.sh | sh                # Mac/Linux

Then run the demo, no install, no serial port required:

    uvx --from git+https://github.com/hucker/termapy@v0.43.0 termapy --demo

That connects to a simulated device. Click the **?** button for built-in help, hover over any button for a tooltip, or just start typing commands. /help is a good start. 

## Hooking up your device...

If you decide you want to talk to your device press the Config button at the top of the screen.  This will let you pick the basics, port/baud, and name.  If you have special needs like setting line endings, echo, auto-connect, retries etc. press the Advanced button to edit the full JSON configuration.

## What makes it different

- **Quick setup** — pick a port, pick a baud rate, connect. One dialog.
- **Visual TUI** — toolbar buttons for config, scripts, protocol tests, screenshots, and captures. Hover over any widget for context help. Click or type — your choice.
- **Per-device profiles** — each port that you set up gets its own config folder, cfg file, scripts. Switch between devices setups with a couple of clicks.
- **Git and team friendly** — configs are JSON files in a per-device folder structure. Check them into your repo and share with your team. Nothing is binary and  there is clear separation between team files and my files.
- **Two levels of scripting** — save `.run` files for simple command sequences, or write Python plugins for real logic. Every command in termapy is a Python plugin..Claude writes plugins in one shot often times.
- **CLI mode** — same tool works headless for CI, scripting, and SSH sessions...from the TUI type in /cli and it switches to a straight terminal window...type /tui to get back.

## When you need to go deeper

- Send raw hex with inline timing delays: `/proto.send 00 ~25ms "AT\r"`
- 62 built-in CRC algorithms with auto-append and code generation for C, Python, and Rust, yeah I was bored.  I have always hated doing CRC ports and now nobody will ever need to do it again. 
- Binary protocol test scripts with send/expect and pass/fail
- Packet format specs that decode bytes into named fields
- More stuff that I made at 2AM

## Install permanently

    uv tool install --python 3.14 git+https://github.com/hucker/termapy@v0.43.0

Built with Textual + pyserial. 1142 tests. Windows and macOS run identically. Linux *should* work but isn't tested as heavily (e.g., once...a while ago). MIT license.

I'd love feedback beyond a Claude saying "It's no longer embarrassing, stop adding dumb features."
