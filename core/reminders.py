import subprocess
import platform


def add_apple_reminder(title, notes="", list_name="Reminders"):
    """Adds a reminder to Apple Reminders using osascript. macOS only."""
    if platform.system() != "Darwin":
        print(f"Skipping reminder (not on macOS): {title}")
        return False

    title_escaped = title.replace('"', '\\"')
    notes_escaped = notes.replace('"', '\\"')
    list_escaped = list_name.replace('"', '\\"')
    applescript = f'''
    tell application "Reminders"
        if not (exists list "{list_escaped}") then
            make new list with properties {{name:"{list_escaped}"}}
        end if
        tell list "{list_escaped}"
            make new reminder with properties {{name:"{title_escaped}", body:"{notes_escaped}"}}
        end tell
    end tell
    '''

    try:
        subprocess.run(["osascript", "-e", applescript], check=True, capture_output=True, text=True)
        print(f"Added reminder: '{title}' to list '{list_name}'")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Failed to add reminder '{title}': {e.stderr}")
        return False


def add_missing_footage_reminders(missing_shots, list_name="B-Roll To Film"):
    added = 0
    for item in missing_shots:
        shot = item["shot"]  # missing entries are now {"index":.., "shot":..}
        title, notes = shot_to_reminder_fields(shot)
        if add_apple_reminder(title=title, notes=notes, list_name=list_name):
            added += 1
    return added

def shot_to_reminder_fields(shot):
    purpose = shot.get("purpose", "Untitled shot")
    duration = shot.get("duration", "?")
    required = shot.get("required", {})
    preferred = shot.get("preferred", {})

    notes_lines = [f"Duration: {duration}s"]
    if required:
        notes_lines.append(f"Required: {required}")
    if preferred:
        notes_lines.append(f"Preferred: {preferred}")

    return f"Film: {purpose}", "\n".join(notes_lines)