import xml.etree.ElementTree as ET
from pathlib import Path

def export_fcp7_xml(assignments, audio_path, output_path, width=1080, height=1920, fps=30):
    """
    Export a Final Cut Pro 7 XML timeline that DaVinci Resolve can import.
    assignments: list of dicts with clip_path, trim_start, trim_end, speed, duration_needed
    audio_path: the voiceover audio file
    """
    timebase = str(fps)

    def to_frames(seconds):
        return round(seconds * fps)

    root = ET.Element("xmeml", version="5")
    project = ET.SubElement(root, "project")
    ET.SubElement(project, "name").text = Path(output_path).stem

    children = ET.SubElement(project, "children")
    sequence = ET.SubElement(children, "sequence", id="sequence-1")
    ET.SubElement(sequence, "name").text = Path(output_path).stem
    
    total_frames = sum(
        round((a["trim_end"] - a["trim_start"]) / a.get("speed", 1.0) * fps) 
        for a in assignments
    )
    ET.SubElement(sequence, "duration").text = str(total_frames)

    rate = ET.SubElement(sequence, "rate")
    ET.SubElement(rate, "timebase").text = timebase
    ET.SubElement(rate, "ntsc").text = "FALSE"

    # Sequence level timecode structure fixes the 00:00:00:00 duration error
    tc = ET.SubElement(sequence, "timecode")
    rate_tc = ET.SubElement(tc, "rate")
    ET.SubElement(rate_tc, "timebase").text = timebase
    ET.SubElement(rate_tc, "ntsc").text = "FALSE"
    ET.SubElement(tc, "string").text = "00:00:00:00"
    ET.SubElement(tc, "frame").text = "0"
    ET.SubElement(tc, "displayformat").text = "NDF"

    media = ET.SubElement(sequence, "media")

    # --- VIDEO TRACK ---
    video = ET.SubElement(media, "video")
    
    # Canvas format layout resolves the "still image" import interpretation bug
    fmt = ET.SubElement(video, "format")
    sc = ET.SubElement(fmt, "samplecharacteristics")
    ET.SubElement(sc, "width").text = str(width)
    ET.SubElement(sc, "height").text = str(height)
    ET.SubElement(sc, "pixelaspect").text = "square"
    rate_fmt = ET.SubElement(sc, "rate")
    ET.SubElement(rate_fmt, "timebase").text = timebase
    ET.SubElement(rate_fmt, "ntsc").text = "FALSE"
    
    video_track = ET.SubElement(video, "track")

    timeline_frame = 0
    for i, a in enumerate(assignments):
        src_in = to_frames(a["trim_start"])
        src_out = to_frames(a["trim_end"])
        speed = a.get("speed", 1.0)
        clip_frames = round((a["trim_end"] - a["trim_start"]) / speed * fps)

        clip_path = Path(a["clip_path"]).resolve()

        clipitem = ET.SubElement(video_track, "clipitem", id=f"clipitem-{i}")
        ET.SubElement(clipitem, "name").text = clip_path.name
        
        # Safe high static target duration prevents file clip clipping bounds logic
        ET.SubElement(clipitem, "duration").text = "100000"
        
        rate_clip = ET.SubElement(clipitem, "rate")
        ET.SubElement(rate_clip, "timebase").text = timebase
        ET.SubElement(rate_clip, "ntsc").text = "FALSE"
        
        ET.SubElement(clipitem, "start").text = str(timeline_frame)
        ET.SubElement(clipitem, "end").text = str(timeline_frame + clip_frames)
        ET.SubElement(clipitem, "in").text = str(src_in)
        ET.SubElement(clipitem, "out").text = str(src_out)

        file_elem = ET.SubElement(clipitem, "file", id=f"file-{clip_path.stem}")
        ET.SubElement(file_elem, "name").text = clip_path.name
        ET.SubElement(file_elem, "pathurl").text = clip_path.as_uri()
        ET.SubElement(file_elem, "duration").text = "100000"
        
        file_rate = ET.SubElement(file_elem, "rate")
        ET.SubElement(file_rate, "timebase").text = timebase
        ET.SubElement(file_rate, "ntsc").text = "FALSE"

        # Explicitly tag type context
        ET.SubElement(clipitem, "mediatype").text = "video"

        # Speed filter calculation mapping
        if abs(speed - 1.0) > 0.001:
            filter_elem = ET.SubElement(clipitem, "filter")
            effect = ET.SubElement(filter_elem, "effect")
            ET.SubElement(effect, "name").text = "Time Remap"
            ET.SubElement(effect, "effectid").text = "timeremap"
            ET.SubElement(effect, "effecttype").text = "motion"
            param = ET.SubElement(effect, "parameter")
            ET.SubElement(param, "parameterid").text = "speed"
            ET.SubElement(param, "name").text = "speed"
            ET.SubElement(param, "value").text = f"{speed * 100:.2f}"

        timeline_frame += clip_frames

    # --- AUDIO TRACK ---
    audio = ET.SubElement(media, "audio")
    audio_track = ET.SubElement(audio, "track")

    audio_path = Path(audio_path).resolve()

    audio_clipitem = ET.SubElement(audio_track, "clipitem", id="audio-vo")
    ET.SubElement(audio_clipitem, "name").text = audio_path.name
    ET.SubElement(audio_clipitem, "duration").text = str(total_frames)
    
    rate_audio = ET.SubElement(audio_clipitem, "rate")
    ET.SubElement(rate_audio, "timebase").text = timebase
    ET.SubElement(rate_audio, "ntsc").text = "FALSE"
    
    ET.SubElement(audio_clipitem, "start").text = "0"
    ET.SubElement(audio_clipitem, "end").text = str(total_frames)
    ET.SubElement(audio_clipitem, "in").text = "0"
    ET.SubElement(audio_clipitem, "out").text = str(total_frames)

    audio_file = ET.SubElement(audio_clipitem, "file", id=f"file-{audio_path.stem}")
    ET.SubElement(audio_file, "name").text = audio_path.name
    ET.SubElement(audio_file, "pathurl").text = audio_path.as_uri()
    ET.SubElement(audio_file, "duration").text = str(total_frames)
    
    audio_file_rate = ET.SubElement(audio_file, "rate")
    ET.SubElement(audio_file_rate, "timebase").text = timebase
    ET.SubElement(audio_file_rate, "ntsc").text = "FALSE"
    
    ET.SubElement(audio_clipitem, "mediatype").text = "audio"

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    with open(output_path, "wb") as f:
        f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n')
        tree.write(f, encoding="utf-8", xml_declaration=False)

    return str(output_path)