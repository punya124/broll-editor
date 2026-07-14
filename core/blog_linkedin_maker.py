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

GEMMA_MODEL = "gemma-4-31b-it"
GEMMA_ENDPOINT = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMMA_MODEL}:generateContent"
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

STRICT OUTPUT RULES:
- Output raw MDX only. No markdown code fences, no explanation before or after.
- Start the file with a metadata export block exactly like this:

export const metadata = {{
  title: "...",
  description: "...",
  date: "{{DATE}}",
  categories: ["..."],
  keywords: ["...", "..."],
  image: "..." (an Unsplash URL, or omit this key entirely if none fits)
}};

- Do NOT include an H1 title anywhere in the body — the page template renders the title from metadata automatically. Start the body content directly (e.g. with an intro paragraph or an H2).
- You MUST include at least one clear call-to-action encouraging the reader to try resuka.com (a resume tailoring AI app). Mention resuka.com by name at least once, woven naturally into the content (not just a bare link with no context). This is mandatory, not optional.
- If relevant existing blog posts are provided to you, link to at most 2-3 of them naturally within the body using standard markdown links, only where they're genuinely relevant to the point being made. Do not force links into unrelated posts.
- Never use these words or close variants: {", ".join(BANNED_WORDS)}.
- Never use em dashes. Use commas, periods, or parentheses instead.
- Vary sentence length. Write like a knowledgeable peer, not a corporate blog.
- Use H2/H3 headings for structure.
"""

LINKEDIN_SYSTEM_INSTRUCTION = f"""You write a LinkedIn post that teases a blog post without repeating it in full.

STRICT OUTPUT RULES:
- Plain text only, no markdown formatting, no code fences.
- Concise, friendly, peer-to-peer tone. Deliver one genuinely useful nugget of value.
- End with a soft nudge encouraging readers to check out the full guide (mention resuka.com naturally, since that's the product behind the guide).
- Add 3-5 relevant hashtags on their own line at the end.
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
        f"{GEMMA_ENDPOINT}?key={api_key}",
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
    return "resuka.com" in mdx_text.lower()


def generate_blog_mdx(blog_prompt, existing_context, api_key, today_str, force_cta=False):
    filled_prompt = (
        f"{blog_prompt}\n\n"
        f"Use today's date ({today_str}) as the date field in metadata.\n\n"
        f"{existing_context}"
    )
    if force_cta:
        filled_prompt += (
            "\n\nIMPORTANT: Your previous attempt did not mention resuka.com. "
            "You must explicitly mention resuka.com by name at least once in the body, "
            "as a natural call-to-action."
        )

    blog_mdx = call_gemma(BLOG_SYSTEM_INSTRUCTION, filled_prompt, api_key)
    return strip_code_fences(blog_mdx)


def generate_blog_and_linkedin(pattern, existing_posts, api_key, today_str):
    theme = pattern.get("theme", "Untitled")
    blog_prompt = pattern.get("blog_post_prompt", "")
    if not blog_prompt:
        raise ValueError(f"Pattern '{theme}' has no blog_post_prompt")

    existing_context = build_existing_posts_context(existing_posts)

    blog_mdx = generate_blog_mdx(blog_prompt, existing_context, api_key, today_str)

    if not has_resuka_mention(blog_mdx):
        print("  resuka.com mention missing, retrying once with a stronger instruction...")
        blog_mdx = generate_blog_mdx(
            blog_prompt, existing_context, api_key, today_str, force_cta=True
        )
        if not has_resuka_mention(blog_mdx):
            print("  Still no resuka.com mention after retry. Appending a fallback CTA manually.")
            blog_mdx += (
                "\n\n---\n\nWant to put this into practice? "
                "[resuka.com](https://resuka.com) helps you tailor your resume to each job in minutes."
            )

    linkedin_prompt = (
        f"Here is the full blog post this LinkedIn post should tease:\n\n{blog_mdx}\n\n"
        "Write the LinkedIn post now, following your instructions."
    )
    linkedin_post = call_gemma(LINKEDIN_SYSTEM_INSTRUCTION, linkedin_prompt, api_key)
    linkedin_post = strip_code_fences(linkedin_post)

    return theme, blog_mdx, linkedin_post


def extract_metadata_field(mdx_text, field, fallback=""):
    match = re.search(rf'{field}:\s*["\'](.+?)["\']', mdx_text)
    return match.group(1) if match else fallback


def main():
    api_key = getattr(config, "GEMINI_API_KEY", None) or os.environ.get("GOOGLE_AI_STUDIO_API_KEY")
    if not api_key:
        print("No API key found (expected config.GEMINI_API_KEY or GOOGLE_AI_STUDIO_API_KEY env var).")
        sys.exit(1)

    config.ensure_dirs()
    suggestions_path = config.OUTPUTS_DIR / "reddit_blog_prompt_suggestions.json"
    if not suggestions_path.exists():
        print(f"No suggestions file found at {suggestions_path}. Run the Reddit script first.")
        sys.exit(1)

    with open(suggestions_path) as f:
        suggestions = json.load(f)

    patterns = suggestions.get("patterns", [])
    if not patterns:
        print("No patterns found in suggestions file.")
        sys.exit(1)

    existing_posts = load_existing_paths()

    today_str = datetime.date.today().isoformat()
    content_dir = config.OUTPUTS_DIR / "generated_content"
    os.makedirs(content_dir, exist_ok=True)

    for i, pattern in enumerate(patterns, start=1):
        theme = pattern.get("theme", f"post-{i}")
        print(f"[{i}/{len(patterns)}] Generating content for: {theme}")
        try:
            theme, blog_mdx, linkedin_post = generate_blog_and_linkedin(
                pattern, existing_posts, api_key, today_str
            )
        except Exception as exc:
            print(f"  Failed to generate content for '{theme}': {exc}")
            continue

        title = extract_metadata_field(blog_mdx, "title", fallback=theme)
        description = extract_metadata_field(blog_mdx, "description", fallback="")

        # filename == slug, no date prefix, since /blog/{filename} is the live URL
        filename = slugify(title)

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
