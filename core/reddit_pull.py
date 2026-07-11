import datetime
import json
import os
import sys
from difflib import SequenceMatcher
import time
import xml.etree.ElementTree as ET

import requests

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.append(ROOT_DIR)

from core import config
from core.gemini_client import GeminiClient


BLOG_POST_PROMPT_SYSTEM_INSTRUCTION = """[PART 1: ROLE & AUDIENCE ANCHORING]
Act as a [Specific Professional Personas, e.g., Tech Recruiter + Senior Software Architect]. 
Write a [Style/Format, e.g., highly actionable, peer-to-peer blog post] titled "[SEO-Optimized Title]."
The target audience is [Hyper-Specific ICP Profile, e.g., ambitious US CS students facing X pain point].

[PART 2: STRUCTURAL HIERARCHY (The Outline)]
Break the post down using clear headings (H2s and H3s) and include the following sections:
- Introduction: [The Psychological Hook + Validation of the specific user frustration].
- Section 1: [The Conceptual Reality Check or Myth-Busting core concept].
- Section 2: [The Practical Blueprint / Step-by-Step implementation framework].
- Section 3: [Concrete Examples / Before-and-After transformations or Scripts].

[PART 3: SEO & ENGINE INSTRUCTIONS]
SEO Instructions: Optimize naturally for the primary keyword "[Primary Head Keyword]" 
and secondary phrases "[LSI Keyword 1]" and "[LSI Keyword 2]." Do not keyword stuff.

[PART 4: FUNNEL FLOW & TONE CONTROLS]
Tone: [3-4 precise tonal modifiers, e.g., Unfiltered, highly practical, peer-to-peer, constructive]. 
Explicitly ban [Banned attributes, e.g., corporate fluff, generic advice].
"""


def get_similarity(str1, str2):
    """Returns a float between 0.0 and 1.0 representing string similarity."""
    return SequenceMatcher(None, str1.lower(), str2.lower()).ratio()


# Your favorited subreddits from the image
subreddits_list = [
    "cscareerquestions",
    "cscareerquestionsIN",
    "cscareers",
    "csInternships",
    "csMajors",
    "internships",
]

def build_reddit_prompt_payload(posts, max_items=8):
    relevant_posts = posts[-max_items:] if len(posts) > max_items else posts
    lines = []
    for index, post in enumerate(relevant_posts, start=1):
        lines.append(
            f"{index}. [{post['subreddit']}] {post['title']} | URL: {post['permalink']}"
        )
    return "\n".join(lines)


def generate_blog_post_prompts(posts, gemini_client=None):
    if not posts:
        return {"patterns": []}

    if gemini_client is None:
        try:
            gemini_client = GeminiClient()
        except Exception as exc:
            print(f"Skipping Gemini blog prompt generation: {exc}")
            return {"patterns": []}

    prompt = (
        "Recent Reddit posts for inspiration:\n"
        f"{build_reddit_prompt_payload(posts)}\n\n"
        "Analyse these posts and generate a list of prompts (strctured as shown in the brief) that allow me to pass them onto claude to create effective blog posts."
        "Focus on the strongest recurring patterns only and avoid using every post."
    )

    try:
        response_text = gemini_client.generate_text(
            prompt,
            system_instruction=BLOG_POST_PROMPT_SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
        )
        return gemini_client._parse_json(response_text)
    except Exception as exc:
        print(f"Gemini blog prompt generation failed: {exc}")
        return {"patterns": []}


# Generate the multi-subreddit RSS feed URL
target_subreddits = "+".join(subreddits_list)
url = f"https://www.reddit.com/r/{target_subreddits}/new/.rss"

# Use a specific, descriptive name for your script to comply with RSS guidelines
headers = {
    "User-Agent": "script:career_feed_deduplicator:v1.0 (Contact: my_email@domain.com)"
}

# Set the 24-hour cutoff time
time_boundary = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
    hours=24
)

print(f"--- Fetching from RSS Feed: {url} ---")
print("Processing unique, non-similar posts from the last 24 hours...\n")

try:
    response = requests.get(url, headers=headers)
    response.raise_for_status()

    # Parse the XML response
    root = ET.fromstring(response.content)

    # Namespaces used in Reddit's Atom RSS feed
    ns = {"atom": "http://www.w3.org/2005/Atom"}

    posts_found = 0
    seen_post_ids = set()
    approved_titles = []
    approved_posts = []

    # Iterate through each <entry> block inside the XML
    for entry in root.findall("atom:entry", ns):
        # Extract the post metadata
        post_id = entry.find("atom:id", ns).text if entry.find("atom:id", ns) is not None else None
        title = entry.find("atom:title", ns).text if entry.find("atom:title", ns) is not None else ""
        link_elem = entry.find("atom:link", ns)
        permalink = link_elem.attrib.get("href") if link_elem is not None else ""

        # Extract the community name from the entry category
        category_elem = entry.find("atom:category", ns)
        subreddit = (
            category_elem.attrib.get("label")
            if category_elem is not None
            else "r/unknown"
        )

        # Parse the raw updated time string (Format looks like: 2026-07-11T12:00:00+00:00)
        updated_text = (
            entry.find("atom:updated", ns).text
            if entry.find("atom:updated", ns) is not None
            else None
        )

        if not post_id or not updated_text:
            continue

        # 1. Skip exact ID duplicates
        if post_id in seen_post_ids:
            continue

        # Convert the ISO timestamp into a Python datetime object
        post_time = datetime.datetime.fromisoformat(updated_text)

        if post_time >= time_boundary:
            # 2. Skip if the title is > 75% similar to any approved post
            is_near_duplicate = False
            for existing_title in approved_titles:
                if get_similarity(title, existing_title) > 0.75:
                    is_near_duplicate = True
                    break

            if is_near_duplicate:
                continue

            # Log this post as approved
            seen_post_ids.add(post_id)
            approved_titles.append(title)
            approved_posts.append(
                {
                    "title": title,
                    "subreddit": subreddit,
                    "permalink": permalink,
                    "created_utc": post_time.isoformat(),
                }
            )
            posts_found += 1

            print(f"[{subreddit}] {title}")
            print(f"URL: {permalink}")
            print(f"Created: {post_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
            print("-" * 40)
        else:
            # RSS feeds are sorted chronologically; stop when older than 24 hours
            continue

    if posts_found == 0:
        print("No new unique posts found in these subreddits within the last 24 hours.")
    else:
        print(f"Done! Found {posts_found} unique, non-similar posts.")
        config.ensure_dirs()
        try:
            client = GeminiClient()
        except Exception as exc:
            print(f"Gemini is unavailable for blog prompt generation: {exc}")
            client = None

        if client is not None:
            suggestions = generate_blog_post_prompts(approved_posts, gemini_client=client)
            output_path = config.OUTPUTS_DIR / "reddit_blog_prompt_suggestions.json"
            with open(output_path, "w") as f:
                json.dump(suggestions, f, indent=2)

            print("Generated blog post prompt ideas from the most relevant Reddit themes:")
            # for pattern in suggestions.get("patterns", [])[:5]:
            #     print(f"- {pattern.get('theme', 'Untitled')}: {pattern.get('blog_post_prompt', '')}")
            print(f"Saved to: {output_path}")

except requests.exceptions.HTTPError as e:
    print(f"Failed to fetch data from Reddit: {e}")