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


def segment_to_reminder_fields(segment: dict):
    """Builds a reminder title/notes for a segment the user has chosen to
    film themselves, rather than use the plan's fallback for."""
    title = f"Film: {segment.get('main_suggestion', 'Untitled action')}"
    notes_lines = [f"Duration: {segment.get('duration', '?'):.2f}s"]
    if segment.get("text"):
        notes_lines.append(f'Narration: "{segment["text"]}"')
    if segment.get("fallback"):
        notes_lines.append(f"(Fallback available instead: {segment['fallback']})")
    return title, "\n".join(notes_lines)