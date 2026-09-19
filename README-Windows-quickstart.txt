k250-forge — Windows quickstart (portable ZIP)
===============================================

What you downloaded is the whole k250-forge toolchain as a folder. Nothing is
installed to your system: no admin, no PATH edits, no registry. Everything the
tools need lives in the folder it unzips into.

1. Unzip this file onto a folder YOU own. NOT C:\, NOT Program Files, NOT your
   Downloads folder. For example:
       C:\Users\YourName\k250-forge
   (The launcher builds a venv next to the code, so it has to be a folder you
   can write to. Unzip takes care of this if you pick a folder under your user.)

2. Double-click  Start-K250.cmd
   - First run: it sets up a local Python environment and installs its dependency
     automatically. This may take a minute; a console window shows progress.
   - Your browser then opens the control page at  http://127.0.0.1:6969

3. Before you drive anything, open the controls page, set YOUR limits, and read
   the top of README.md. Do not exceed the ceiling in limits.json.
   The stop word is:  red

4. Close the console window (or press Ctrl+C) to stop the launcher.

Command-line tools (same folder, once Start-K250 has built the venv):
    venv\Scripts\python k250_status.py        see the box: battery, channels, speed
    venv\Scripts\python k250_play.py --list   list all patterns
    venv\Scripts\python k250_stop.py          STOP NOW -- kills the pattern, zeroes every channel

Bluetooth note: wake the box to its remote-control screen before scanning,
and only ever run ONE pattern at a time -- the box takes one BLE connection.

Full docs, patterns, and configuration: README.md in this folder.