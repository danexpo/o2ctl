"""Commands bound to buttons. The mouse only reports the press; the watcher
(o2ctl watch) looks the button up in the active profile and runs its command.
"""

import os
import subprocess
import threading

from . import config


def command_for(state, button):
    binding = config.profile(state, state["active_profile"])["buttons"].get(button, {})
    return binding.get("command") if binding.get("type") == "command" else None


def run(command):
    """Start `command` with sh, detached; its output goes to the watcher's log."""
    proc = subprocess.Popen(["sh", "-c", command], cwd=os.path.expanduser("~"),
                            stdin=subprocess.DEVNULL, start_new_session=True)
    threading.Thread(target=proc.wait, daemon=True).start()  # reap it


def handle(event, log=print, load=config.load, save=config.save, start=run):
    """Act on a "button" or "profile" event from the mouse."""
    try:
        state = load()
    except (OSError, ValueError) as e:
        log(f"Could not read the config: {e}")
        return
    if event["event"] == "profile":
        # Keep the saved active profile right, so a press runs that profile's command.
        if state["active_profile"] != event["profile"]:
            state["active_profile"] = event["profile"]
            save(state)
            log(f"Mouse switched to profile {event['profile']}.")
    elif event["event"] == "button":
        command = command_for(state, event["button"])
        if command:
            log(f"{event['button'].capitalize()} button: running {command}")
            try:
                start(command)
            except OSError as e:
                log(f"Could not run {command}: {e}")
