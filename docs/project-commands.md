# Project commands

Save the commands you use to launch an app, start a server, run tests, or build
your project. The command control sits beside **Agents** in the top toolbar,
so it stays available when the workspace pane is hidden.
In a narrow window it collapses to a play/stop icon; hover or open the dropdown
to see the command name and status.

## Set up once

1. Open a project and click **Set up command**.
2. Choose a detected command, or enter your own name and command. Suggestions
   come from package scripts, Python module entry points, and Aura's existing
   test/build detection. They are suggestions to review; discovery runs nothing.
3. Expand **Folder and environment** if the command needs a subfolder or
   environment overrides. `.` means the project folder.
4. Choose **Save** to keep it without running, or **Save and run** to launch it.

The button now shows your command's name, such as **Start server** or **Run
tests**. Use its dropdown to select another saved command, add one, edit it, or
remove it. Choosing a command changes the primary button; click the button to
run it. The selection survives restarting Aura, and nothing starts automatically.

Commands are saved for this project on this computer, outside the repository.
Environment overrides are local settings, stored as plain text, and are not
included in chat. A project-local Python environment is used when present,
including one in the selected subfolder. Dependencies are not installed for you.

## While a command runs

- Output opens in Aura's existing terminal and keeps accumulating when hidden.
- The button becomes **Stop**. Its dropdown also offers **Restart** and
  **View output**. Restart uses the same command, folder, and environment.
- **Starting**, **Running**, **Stopping**, **Stopped**, **Succeeded**, and
  **Failed** reflect the process lifecycle. Running means the process started;
  a server's own output tells you when it is ready and which URL to open.
- One saved command runs at a time. Servers have no time limit. Keep the
  command in the foreground so Aura can own its process and child processes.
- Starting or loading a chat keeps the project command running. Changing
  projects stops it. Closing Aura stops it and cleans up its process tree.

These controls launch commands you explicitly choose. They are independent of
Aura's coding task: the composer's Stop button stops Aura's task, while the
project command's Stop button stops your app or server. Agents, Read Only, and
Approve continue to control Aura's coding behavior.

Windows commands use PowerShell; Linux/macOS commands use `/bin/sh`. Commands
requiring interactive input should be run in an external terminal.

## Verification and Windows walkthrough

Focused coverage uses actual subprocesses for output, exit codes, working
directories, environment selection, server lifetime, Stop, Restart, and child
cleanup. GUI tests exercise the real editor, command controller, and terminal:

```powershell
python -m pytest -q tests/test_project_commands.py tests/test_project_command_runner.py tests/test_project_command_gui.py tests/test_terminal_window_accumulation.py tests/test_workspace_visibility.py tests/test_turn_updates_gui.py tests/test_project_profile.py
```

For the Windows desktop check, use a separate scratch project under
`C:\Projects\workflow_agent`:

1. Add **Start server** with `python -m http.server 8765 --bind 127.0.0.1`.
   Choose Save and confirm it does not run. Click the named button, wait for the
   serving message, and open `http://127.0.0.1:8765` in a browser.
2. Hide the terminal and workspace. Reopen output from the toolbar. Start a new
   chat and confirm the server still responds.
3. Restart, then Stop. Confirm port 8765 is released and the terminal says
   Stopped. A program that returns a nonzero exit must show Failed and its
   actual exit code.
4. Add a second command, change the primary selection, edit its folder and
   environment, and restart Aura. Confirm those settings persist without
   launching anything. Confirm a local `.venv` supplies the Python executable.
5. Start the server again and change projects, then repeat while closing Aura.
   Confirm neither leaves a server process behind.
6. With **Agents ON** and **Approve OFF**, run an ordinary coding task while
   the server is active. Confirm chat steering and manual diff review still
   work, and that stopping either activity does not stop the other.

Linux offscreen checks cover the actual MainWindow at 1350 and 1100 pixels,
the editor, a real HTTP server, Stop, relaunch, and close cleanup. Native
Windows desktop and PowerShell execution still require the walkthrough above.
