import datetime
import json
import os
import re
import sys

import requests

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

from core import config

GEMINI_MODEL = "gemini-3.1-flash-lite"  # or "gemini-2.5-flash-lite" for cheaper/faster
GEMINI_ENDPOINT = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
)

BANNED_WORDS = [
    "delve", "leverage", "landscape", "crucial", "robust",
    "game-changer", "tapestry", "furthermore", "moreover",
]

BLOG_BASE_URL_PATH = "/blog"

# Lives OUTSIDE the outputs folder on purpose — outputs get pushed to GitHub
# and wiped, but this file must persist across runs as the permanent record
# of every post ever generated.
DATA_DIR = os.path.join(ROOT_DIR, "data")
EXISTING_PATHS_FILE = os.path.join(DATA_DIR, "existing_paths.json")


def load_existing_paths():
    if not os.path.exists(EXISTING_PATHS_FILE):
        return []
    with open(EXISTING_PATHS_FILE) as f:
        return json.load(f)


def save_existing_paths(entries):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(EXISTING_PATHS_FILE, "w") as f:
        json.dump(entries, f, indent=2)


def build_existing_posts_context(existing_posts, max_items=15):
    if not existing_posts:
        return "No existing blog posts yet. This will be the first one, so no internal links are needed."

    recent = existing_posts[-max_items:]
    lines = [
        "Existing blog posts you can link to internally where relevant (do not force it, only link if genuinely relevant to the topic):"
    ]
    for post in recent:
        lines.append(
            f"- \"{post['title']}\" — {post.get('description', '')} — URL: {BLOG_BASE_URL_PATH}/{post['filename']}"
        )
    return "\n".join(lines)


BLOG_SYSTEM_INSTRUCTION = f"""You are a senior tech writer producing a blog post in Next.js MDX format.

CRITICAL FORMAT REQUIREMENT:
You may think through your approach first if you need to. However, immediately before the actual 
MDX content, you MUST write the exact separator line:

MDX FILE:

on its own line, with nothing else on that line. Everything after that separator must be ONLY the 
raw MDX file content, starting with the metadata export block. Nothing after the separator except 
the MDX itself.

STRICT OUTPUT RULES FOR THE MDX ITSELF:
- Start with a metadata export block exactly like this:

export const metadata = {{
  title: "...",
  slug: "...",
  description: "...",
  date: "{{DATE}}",
  categories: ["..."],
  keywords: ["...", "..."],
  image: "..." (an Unsplash URL, or omit this key entirely if none fits)
}};

- The "slug" field must be a short, URL-friendly identifier for this post: lowercase, hyphen-separated, 2-5 words maximum.
- Do NOT include an H1 title anywhere in the body.
- You MUST include at least one call-to-action that links to resuka.com using a proper markdown link 
  pointing to https://www.resuka.com, formatted like [descriptive anchor text](https://www.resuka.com). 
  Do NOT just write the bare text "resuka.com" without it being a clickable markdown link. The anchor 
  text should read naturally in the sentence (e.g. "tools like [resuka](https://www.resuka.com) can help 
  you tailor your resume"), not just the raw URL as the visible text.
- If relevant existing blog posts are provided to you, link to at most 2-3 of them naturally.
- Never use these words or close variants: {", ".join(BANNED_WORDS)}.
- Never use em dashes.
- Vary sentence length.
- Use H2/H3 headings for structure.
"""

LINKEDIN_SYSTEM_INSTRUCTION = f"""You write a LinkedIn post that teases a blog post without repeating it in full.

CRITICAL FORMAT REQUIREMENT:
You may think through your approach first if you need to. However, immediately before the actual 
LinkedIn post content, you MUST write the exact separator line:

LINKEDIN POST:

on its own line, with nothing else on that line. Everything after that separator must be ONLY the 
final LinkedIn post text, ready to copy-paste as-is. Nothing after the separator except the post itself.

STRICT OUTPUT RULES FOR THE POST ITSELF:
- Plain text only, no markdown formatting, no code fences.
- Concise, friendly, peer-to-peer tone. Deliver one genuinely useful nugget of value.
- You will be given an exact URL to end the post with. Use that URL exactly as given, do not shorten it, 
  do not replace it with just "resuka.com" or a generic mention. The URL itself is the CTA.
- Add 3-5 relevant hashtags on their own line after the URL.
- Never use these words or close variants: {", ".join(BANNED_WORDS)}.
- Never use em dashes.
"""


def call_gemma(system_instruction, user_prompt, api_key):
    payload = {
        "system_instruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0.9,
            "maxOutputTokens": 4096,
        },
    }
    resp = requests.post(
        f"{GEMINI_ENDPOINT}?key={api_key}",
        headers={"Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    try:
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"Unexpected Gemma response shape: {data}") from exc


def strip_code_fences(text):
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\n", "", text)
    text = re.sub(r"\n```$", "", text)
    return text.strip()


def slugify(title):
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:60] or "post"


def has_resuka_mention(mdx_text):
    """Checks for a proper markdown link to resuka.com, not just the bare text."""
    pattern = r"\[([^\]]+)\]\((https?://(?:www\.)?resuka\.com[^\)]*)\)"
    return re.search(pattern, mdx_text) is not None

def extract_mdx_from_output(text):
    """Strip everything up to and including the 'MDX FILE:' separator."""
    marker = "MDX FILE:"
    idx = text.find(marker)
    if idx == -1:
        # fall back to the old heuristic in case the separator gets dropped
        fallback_marker = "export const metadata"
        fidx = text.find(fallback_marker)
        return text[fidx:].strip() if fidx != -1 else text.strip()
    return text[idx + len(marker):].strip()


def generate_blog_mdx(blog_prompt, existing_context, api_key, today_str, force_cta=False):
    filled_prompt = (
        f"{blog_prompt}\n\n"
        f"Use today's date ({today_str}) as the date field in metadata.\n\n"
        f"{existing_context}"
    )
    if force_cta:
        filled_prompt += (
            "\n\nIMPORTANT: Your previous attempt did not mention resuka.com. "
            "You must explicitly mention resuka.com by name at least once in the body."
        )

    blog_mdx = call_gemma(BLOG_SYSTEM_INSTRUCTION, filled_prompt, api_key)
    blog_mdx = strip_code_fences(blog_mdx)
    blog_mdx = extract_mdx_from_output(blog_mdx)
    return blog_mdx


def normalize_patterns(suggestions):
    if isinstance(suggestions, dict):
        patterns = suggestions.get("patterns", [])
        if isinstance(patterns, dict):
            return [patterns]
        return patterns if isinstance(patterns, list) else []
    if isinstance(suggestions, list):
        return suggestions
    return []


def build_blog_prompt_from_pattern(pattern):
    """Assembles a flat prompt string from a structured pattern object,
    following the same shape as BLOG_POST_PROMPT_SYSTEM_INSTRUCTION expects."""
    title = pattern.get("title", "Untitled")
    persona = pattern.get("persona", "Senior Software Architect")
    target_audience = pattern.get("target_audience", "")
    primary_keyword = pattern.get("primary_keyword", "")
    lsi_keywords = pattern.get("lsi_keywords", [])
    tone = pattern.get("tone", [])
    banned_content = pattern.get("banned_content", [])
    outline = pattern.get("outline", {})

    lines = [
        f"Act as a {persona}.",
        f'Write a highly actionable, peer-to-peer blog post titled "{title}."',
        f"The target audience is: {target_audience}",
        "",
        "Structure the post using clear H2/H3 headings covering:",
    ]

    if outline.get("introduction"):
        lines.append(f"- Introduction: {outline['introduction']}")
    if outline.get("section_1"):
        lines.append(f"- Section 1: {outline['section_1']}")
    if outline.get("section_2"):
        lines.append(f"- Section 2: {outline['section_2']}")
    if outline.get("section_3"):
        lines.append(f"- Section 3: {outline['section_3']}")

    lines += [
        "",
        f'Optimize naturally for the primary keyword "{primary_keyword}" '
        f'and secondary phrases: {", ".join(lsi_keywords)}. Do not keyword stuff.',
        "",
        f"Tone: {', '.join(tone)}.",
        f"Explicitly avoid: {', '.join(banned_content)}.",
    ]

    return "\n".join(lines)

def extract_linkedin_from_output(text):
    """Strip everything up to and including the 'LINKEDIN POST:' separator."""
    marker = "LINKEDIN POST:"
    idx = text.find(marker)
    if idx == -1:
        # separator missing, fall back to returning as-is
        return text.strip()
    return text[idx + len(marker):].strip()

def generate_blog_and_linkedin(pattern, existing_posts, api_key, today_str):
    theme = pattern.get("topic", "Untitled")
    blog_prompt = build_blog_prompt_from_pattern(pattern)
    existing_context = build_existing_posts_context(existing_posts)
    blog_mdx = generate_blog_mdx(blog_prompt, existing_context, api_key, today_str)

    if not blog_prompt:
        raise ValueError(f"Pattern '{title}' has no blog_post_prompt")

    existing_context = build_existing_posts_context(existing_posts)

    blog_mdx = generate_blog_mdx(blog_prompt, existing_context, api_key, today_str)

    if not has_resuka_mention(blog_mdx):
        print("  Still no resuka.com mention after retry. Appending a fallback CTA manually.")
        blog_mdx += (
            "\n\n---\n\nWant to put this into practice? "
            "[resuka.com](https://resuka.com) helps you tailor your resume to each job in minutes."
        )

    # extract slug now, before building the LinkedIn prompt, so we can hand Gemma the real URL
    raw_slug = extract_metadata_field(blog_mdx, "slug", fallback="")
    title = extract_metadata_field(blog_mdx, "title", fallback=theme)
    filename = slugify(raw_slug) if raw_slug else slugify(title)
    blog_url = f"https://www.resuka.com/blog/{filename}"

    linkedin_prompt = (
        f"Here is the full blog post this LinkedIn post should tease:\n\n{blog_mdx}\n\n"
        f"The exact URL to end the post with is: {blog_url}\n\n"
        "Write the LinkedIn post now, following your instructions."
    )
    linkedin_post = call_gemma(LINKEDIN_SYSTEM_INSTRUCTION, linkedin_prompt, api_key)
    linkedin_post = strip_code_fences(linkedin_post)
    linkedin_post = extract_linkedin_from_output(linkedin_post)

    return theme, blog_mdx, linkedin_post, filename

def extract_metadata_field(mdx_text, field, fallback=""):
    match = re.search(rf'{field}:\s*["\'](.+?)["\']', mdx_text)
    return match.group(1) if match else fallback


def main():
    settings = config.load_settings()
    api_key = config.get_api_key(settings)
    if not api_key:
        raise RuntimeError(
            "No Gemini API key found. Set the GEMINI_API_KEY environment variable "
            "before starting the app."
        )
        sys.exit(1)

    config.ensure_dirs()
    suggestions_path = config.OUTPUTS_DIR / "reddit_blog_prompt_suggestions.json"
    if not suggestions_path.exists():
        print(f"No suggestions file found at {suggestions_path}. Run the Reddit script first.")
        sys.exit(1)

    with open(suggestions_path) as f:
        suggestions = json.load(f)

    # File is a bare JSON list of pattern objects, not {"patterns": [...]}
    if isinstance(suggestions, list):
        patterns = suggestions
    else:
        patterns = suggestions.get("patterns", [])

    if not patterns:
        print("No patterns found in suggestions file.")
        sys.exit(1)

    existing_posts = load_existing_paths()

    today_str = datetime.date.today().isoformat()
    content_dir = config.OUTPUTS_DIR / "generated_content"
    os.makedirs(content_dir, exist_ok=True)

    for i, pattern in enumerate(patterns, start=1):
        if isinstance(pattern, dict):
            theme = pattern.get("topic") or pattern.get("title") or f"post-{i}"
        else:
            theme = str(pattern or f"post-{i}")
        print(f"[{i}/{len(patterns)}] Generating content for: {theme}")
        try:
            theme, blog_mdx, linkedin_post, filename = generate_blog_and_linkedin(
                pattern, existing_posts, api_key, today_str
            )
        except Exception as exc:
            print(f"  Failed to generate content for '{theme}': {exc}")
            continue

        title = extract_metadata_field(blog_mdx, "title", fallback=theme)
        description = extract_metadata_field(blog_mdx, "description", fallback="")
        
        # avoid clobbering a same-slug file generated earlier the same day/run
        existing_filenames = {p["filename"] for p in existing_posts}
        original_filename = filename
        suffix = 2
        while filename in existing_filenames:
            filename = f"{original_filename}-{suffix}"
            suffix += 1

        blog_path = content_dir / f"{filename}.mdx"
        linkedin_path = content_dir / f"{filename}-linkedin.txt"

        with open(blog_path, "w") as f:
            f.write(blog_mdx)
        with open(linkedin_path, "w") as f:
            f.write(linkedin_post)

        print(f"  Saved blog: {blog_path}")
        print(f"  Saved LinkedIn post: {linkedin_path}")

        # Register this post permanently so future runs (even after this
        # file gets pushed to GitHub and deleted locally) can still link to it
        new_entry = {
            "filename": filename,
            "title": title,
            "description": description,
            "date": today_str,
        }
        existing_posts.append(new_entry)
        save_existing_paths(existing_posts)

    print("Done.")


if __name__ == "__main__":
    main()
