#!/usr/bin/env python3
"""User settings for the source-fidelity skills.

Settings never go in the venv, a skill directory, or a review artifact: the venv
is rebuilt and shared, and the other two are committed or copied. One JSON file
under the user's DSH home holds the contact address and any API key.

    python -I user_settings.py --show
    python -I user_settings.py --prompt

Keys are optional and free:
  email            Crossref polite pool, OpenAlex, Unpaywall contact (no signup)
  google_books_key Google Cloud console, enable Books API (free quota rise)
"""

import argparse
import json
import os
import sys

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".dsh", "sourcefidelity.json")
ENV_NAMES = {"email": "SOURCEFIDELITY_EMAIL",
             "google_books_key": "GOOGLE_BOOKS_API_KEY",
             "openalex_api_key": "OPENALEX_API_KEY",
             "semantic_scholar_api_key": "SEMANTIC_SCHOLAR_API_KEY"}
SIGNUP = {
    "email": "your own address (no signup needed)",
    "google_books_key": "https://console.cloud.google.com/apis/library/books.googleapis.com",
    "openalex_api_key": "https://help.openalex.org/auth (free key; raises the daily budget "
                        "and the per-second limit)",
    "semantic_scholar_api_key": "https://www.semanticscholar.org/product/api#api-key-form "
                                "(free key; 1 request/second)",
}


def load(path=None):
    try:
        with open(path or CONFIG_PATH, encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def get(name, path=None):
    """Environment first, then the config file."""
    value = (os.environ.get(ENV_NAMES.get(name, "")) or "").strip()
    if value:
        return value
    return str(load(path).get(name) or "").strip()


def email(path=None):
    return get("email", path)


def google_books_key(path=None):
    return get("google_books_key", path)


def save(updates, path=None):
    target = path or CONFIG_PATH
    data = load(target)
    for key, value in updates.items():
        if value:
            data[key] = value
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, indent=1, sort_keys=True)
        handle.write("\n")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target


def prompt(path=None):
    """Ask for the two settings and save them. Empty input keeps the current value."""
    data = load(path)
    print("Contact email for Crossref, OpenAlex and Unpaywall.")
    print("Crossref gives a higher rate to a request that carries a contact address.")
    print(f"  current: {data.get('email') or '(unset)'}  source: {SIGNUP['email']}")
    value = input("  email: ").strip()
    if value:
        save({"email": value}, path)
    print("Google Books API key (optional; raises the free quota).")
    print(f"  create one at {SIGNUP['google_books_key']}")
    print(f"  current: {'set' if data.get('google_books_key') else '(unset)'}")
    value = input("  key (enter to keep): ").strip()
    if value:
        save({"google_books_key": value}, path)
    for name, label in (("openalex_api_key", "OpenAlex"),
                        ("semantic_scholar_api_key", "Semantic Scholar")):
        print(f"{label} API key (optional but recommended; free).")
        print(f"  create one at {SIGNUP[name]}")
        print(f"  current: {'set' if data.get(name) else '(unset)'}")
        value = input("  key (enter to keep): ").strip()
        if value:
            save({name: value}, path)
    print(f"saved to {path or CONFIG_PATH}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--prompt", action="store_true")
    parser.add_argument("--file", default="")
    args = parser.parse_args(argv)
    if args.prompt:
        return prompt(args.file or None)
    data = load(args.file or None)
    redacted = {key: ("set" if data.get(key) else "unset") for key in ENV_NAMES}
    print(json.dumps({"path": args.file or CONFIG_PATH, "settings": redacted,
                      "email_now": email(args.file or None) or "(unset)"}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
