"""
Chatbot QA Runner — Simple Entry Point
Asks for chatbot URL, CSV file, and Anthropic API key. That's it.

Usage:
    python run.py
    python run.py --url https://... --csv questions.csv
    python run.py --url https://... --csv questions.csv --limit 5
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)

import os
import re
import json
import time
import argparse
import tempfile

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
KEY_FILE    = os.path.join(SCRIPT_DIR, 'anthropic_key.txt')
CONFIGS_DIR = os.path.join(SCRIPT_DIR, 'chatbot_configs')

# ─── Known chatbot presets ────────────────────────────────────────────────────

PRESETS = [
    {
        "name": "ReshapeX",
        "chat_bubble_selector": "button[class*='rounded-full']",
        "textarea_selector":    "reshape-chat >> textarea",
        "shadow_host":          "reshape-chat",
        "bot_message_selector": "[class*='self-start'] div.markdown",
    },
    {
        "name": "Intercom",
        "chat_bubble_selector": ".intercom-launcher",
        "textarea_selector":    ".intercom-composer-input",
        "shadow_host":          "",
        "bot_message_selector": ".intercom-block-paragraph",
    },
    {
        "name": "Drift",
        "chat_bubble_selector": "#drift-widget-container button",
        "textarea_selector":    ".drift-composer-input",
        "shadow_host":          "",
        "bot_message_selector": ".drift-ui-message-text",
    },
    {
        "name": "Crisp",
        "chat_bubble_selector": ".crisp-client .cc-unoo",
        "textarea_selector":    ".cc-imbb input",
        "shadow_host":          "",
        "bot_message_selector": ".cc-itde .cc-lnk",
    },
]

# Fallback selectors tried when no preset matches
FALLBACK_BUBBLES   = [
    "button[aria-label*='chat' i]", "button[aria-label*='message' i]",
    "[class*='chat-launcher']", "[class*='chat-bubble']", "[class*='chat-button']",
    "[id*='chat-button']", "[id*='chat-launcher']",
]
FALLBACK_TEXTAREAS = [
    "textarea[placeholder*='message' i]", "textarea[placeholder*='escribe' i]",
    "textarea[placeholder*='type' i]", "div[contenteditable='true']",
    "input[placeholder*='message' i]", "textarea",
]
FALLBACK_BOT_MSGS  = [
    "[class*='bot-message']", "[class*='assistant-message']",
    "[class*='bot'][class*='text']", "[class*='message'][class*='received']",
]


# ─── Auto-detection ───────────────────────────────────────────────────────────

def try_selector(page, sel: str, timeout_ms: int = 3000) -> bool:
    try:
        page.wait_for_selector(sel, timeout=timeout_ms)
        return True
    except Exception:
        return False


def autodetect(page) -> dict | None:
    """Try known presets first, then fallback selectors. Returns partial config or None."""
    print("  Trying known chatbot presets...")

    for preset in PRESETS:
        if try_selector(page, preset['chat_bubble_selector'], 3000):
            print(f"  [✓] Detected: {preset['name']}")
            return preset

    print("  No preset matched — trying generic selectors...")

    bubble = None
    for sel in FALLBACK_BUBBLES:
        if try_selector(page, sel, 1500):
            bubble = sel
            print(f"  [✓] Chat bubble: {sel}")
            break

    if not bubble:
        return None

    # Click bubble and look for textarea
    try:
        page.click(bubble)
        time.sleep(1.5)
    except Exception:
        pass

    textarea = None
    for sel in FALLBACK_TEXTAREAS:
        if try_selector(page, sel, 2000):
            textarea = sel
            print(f"  [✓] Textarea: {sel}")
            break

    bot_msg = None
    for sel in FALLBACK_BOT_MSGS:
        if page.query_selector(sel):
            bot_msg = sel
            print(f"  [✓] Bot message selector: {sel}")
            break

    if bubble and textarea and bot_msg:
        return {
            "name":                 "Auto-detected",
            "chat_bubble_selector": bubble,
            "textarea_selector":    textarea,
            "shadow_host":          "",
            "bot_message_selector": bot_msg,
        }

    return None


# ─── CSV column detection ─────────────────────────────────────────────────────

def detect_columns(rows: list) -> tuple[str, str]:
    """Return (question_col, expected_col) — detect from column names or ask user."""
    cols = list(rows[0].keys())

    # Try standard names first
    q_candidates = ['input', 'question', 'pregunta', 'query']
    e_candidates = ['expected_output', 'expected', 'esperado', 'reference', 'referencia']

    q_col = next((c for c in cols if c.strip().lower() in q_candidates), None)
    e_col = next((c for c in cols if c.strip().lower() in e_candidates), None)

    if q_col and e_col:
        return q_col, e_col

    # Ask user
    print(f"\n  Columns found in CSV: {cols}")
    if not q_col:
        q_col = input(f"  Which column has the QUESTIONS? ").strip()
    if not e_col:
        e_col = input(f"  Which column has the EXPECTED OUTPUT (or press Enter to skip)? ").strip()
        if not e_col:
            e_col = None

    return q_col, e_col


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Chatbot QA Runner — Simple Entry Point")
    parser.add_argument('--url',   help='Chatbot URL')
    parser.add_argument('--csv',   help='Path to CSV with questions')
    parser.add_argument('--key',   help='Anthropic API key (or save to anthropic_key.txt)')
    parser.add_argument('--limit', type=int, default=0, help='Only process first N questions (for testing)')
    args = parser.parse_args()

    print("\n" + "="*60)
    print("  Chatbot QA Runner")
    print("="*60 + "\n")

    # 1. Chatbot URL
    url = args.url or input("Chatbot URL: ").strip()
    if not url:
        print("URL is required."); return

    # 2. CSV path
    csv_path = args.csv or input("CSV file path: ").strip().strip('"')
    if not os.path.isfile(csv_path):
        print(f"File not found: {csv_path}"); return

    # 3. Anthropic API key
    api_key = (args.key or '').strip()
    if not api_key:
        api_key = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    if not api_key and os.path.isfile(KEY_FILE):
        with open(KEY_FILE) as f:
            api_key = f.read().strip()
        if api_key:
            print("Anthropic API key loaded from anthropic_key.txt")
    if not api_key:
        api_key = input("Anthropic API key: ").strip()
        if not api_key:
            print("API key is required."); return
        save = input("Save key to anthropic_key.txt for future runs? (Y/n): ").strip().lower()
        if save != 'n':
            with open(KEY_FILE, 'w') as f:
                f.write(api_key)
            print("  Key saved.")

    # 4. Load CSV and detect columns
    import csv as _csv
    rows = []
    for enc in ['utf-8-sig', 'cp1252', 'latin-1', 'utf-8']:
        try:
            with open(csv_path, encoding=enc, newline='') as f:
                rows = [{k.strip(): v for k, v in r.items()} for r in _csv.DictReader(f)]
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if not rows:
        print("Could not read CSV."); return

    print(f"\n  CSV loaded: {len(rows)} questions")

    q_col, e_col = detect_columns(rows)

    # Normalize column names to what chatbot_qa_runner.py expects
    needs_rename = (q_col != 'input') or (e_col and e_col != 'expected_output')
    if needs_rename:
        for r in rows:
            if q_col != 'input':
                r['input'] = r.get(q_col, '')
            if e_col and e_col != 'expected_output':
                r['expected_output'] = r.get(e_col, '')
            if not e_col:
                r['expected_output'] = ''
        # Save normalized CSV to a temp file
        import tempfile
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False,
                                          encoding='utf-8-sig', newline='')
        fields = ['input', 'expected_output', 'Agent answer', 'Validation']
        writer = _csv.DictWriter(tmp, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, '') for k in fields})
        tmp.close()
        csv_path = tmp.name
        print(f"  Columns mapped: '{q_col}' → input, '{e_col or '(none)'}' → expected_output")

    # 5. Auto-detect chatbot selectors
    print(f"\n  Detecting chatbot UI at {url}...")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("\n  [!] Playwright not installed. Run:")
        print("      pip install playwright && playwright install chromium")
        return

    detected_config = None
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        page    = browser.new_page()
        page.goto(url, wait_until='domcontentloaded', timeout=30000)
        time.sleep(2)
        detected_config = autodetect(page)
        browser.close()

    if not detected_config:
        print("\n  [!] Could not auto-detect the chatbot UI.")
        print("      Run 'python setup_chatbot.py' to configure it manually.")
        return

    # 6. Build full config
    config = {
        "name":                  detected_config.get("name", "Chatbot"),
        "url":                   url,
        "chat_bubble_selector":  detected_config["chat_bubble_selector"],
        "textarea_selector":     detected_config["textarea_selector"],
        "shadow_host":           detected_config.get("shadow_host", ""),
        "bot_message_selector":  detected_config["bot_message_selector"],
        "response_timeout":      120,
        "stability_secs":        6.0,
        "min_response_len":      50,
    }

    # Save config to chatbot_configs/ for reuse
    os.makedirs(CONFIGS_DIR, exist_ok=True)
    slug = re.sub(r'[^a-z0-9]+', '_', config['name'].lower()).strip('_')
    config_path = os.path.join(CONFIGS_DIR, f'config_{slug}_autodetected.json')
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    print(f"  Config saved → {config_path}")

    # 7. Confirm and run
    print(f"\n  Ready:")
    print(f"    Chatbot: {config['name']}  ({url})")
    print(f"    Questions: {len(rows)}")
    if args.limit:
        print(f"    Limit: first {args.limit} questions (test mode)")

    go = input("\nStart? (Y/n): ").strip().lower()
    if go == 'n':
        print("Aborted.")
        return

    # 8. Launch chatbot_qa_runner.py
    import subprocess
    runner = os.path.join(SCRIPT_DIR, 'chatbot_qa_runner.py')
    cmd = [sys.executable, runner,
           '--config', config_path,
           '--input',  csv_path]
    if args.limit:
        cmd += ['--limit', str(args.limit)]

    # Pass API key via env var so runner picks it up
    env = os.environ.copy()
    env['ANTHROPIC_API_KEY'] = api_key

    subprocess.run(cmd, env=env)


if __name__ == '__main__':
    main()
