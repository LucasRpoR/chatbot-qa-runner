"""
Chatbot Config Wizard
Creates a config JSON for a new chatbot to use with chatbot_qa_runner.py

Usage:
    python setup_chatbot.py
    python setup_chatbot.py --test-only chatbot_configs/config_item24.json
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)

import json
import os
import re
import time
import argparse

SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
CONFIGS_DIR = os.path.join(SCRIPT_DIR, 'chatbot_configs')

# ─── Common chatbot presets ───────────────────────────────────────────────────
# Add entries here as you discover new chatbot patterns.

PRESETS = {
    "reshape": {
        "label": "ReshapeX (custom web component with Shadow DOM)",
        "chat_bubble_selector": "button[class*='rounded-full']",
        "textarea_selector":    "reshape-chat >> textarea",
        "shadow_host":          "reshape-chat",
        "bot_message_selector": "[class*='self-start'] div.markdown",
    },
    "intercom": {
        "label": "Intercom",
        "chat_bubble_selector": ".intercom-launcher",
        "textarea_selector":    ".intercom-composer-input",
        "shadow_host":          "",
        "bot_message_selector": ".intercom-block-paragraph",
    },
    "drift": {
        "label": "Drift",
        "chat_bubble_selector": "#drift-widget-container button",
        "textarea_selector":    ".drift-composer-input",
        "shadow_host":          "",
        "bot_message_selector": ".drift-ui-message-text",
    },
    "crisp": {
        "label": "Crisp Chat",
        "chat_bubble_selector": ".crisp-client .cc-unoo",
        "textarea_selector":    ".cc-imbb input",
        "shadow_host":          "",
        "bot_message_selector": ".cc-itde .cc-lnk",
    },
    "custom": {
        "label": "Custom / Other (I'll enter the selectors manually)",
        "chat_bubble_selector": "",
        "textarea_selector":    "",
        "shadow_host":          "",
        "bot_message_selector": "",
    },
}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val if val else default


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    default_str = "Y/n" if default else "y/N"
    val = input(f"{prompt} ({default_str}): ").strip().lower()
    if not val:
        return default
    return val.startswith('y')


def slug(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')


# ─── Selector test via Playwright ─────────────────────────────────────────────

def test_config(config: dict) -> bool:
    """Open the chatbot, send one test message, and verify we can capture a response."""
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        print("\n  [!] playwright not installed — skipping browser test")
        print("      Run: pip install playwright && playwright install chromium")
        return True

    print("\n  Opening browser for selector test...")
    ok = True
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        page    = browser.new_page()
        try:
            page.goto(config['url'], wait_until='domcontentloaded', timeout=30000)
            time.sleep(2)

            # Test chat bubble
            try:
                bubble = page.wait_for_selector(config['chat_bubble_selector'], timeout=8000)
                print(f"  [✓] Chat bubble found: {config['chat_bubble_selector']}")
                bubble.click()
            except PWTimeout:
                print(f"  [✗] Chat bubble NOT found: {config['chat_bubble_selector']}")
                ok = False

            # Test textarea
            try:
                page.wait_for_selector(config['textarea_selector'], state="visible", timeout=8000)
                print(f"  [✓] Textarea found: {config['textarea_selector']}")
            except PWTimeout:
                print(f"  [✗] Textarea NOT found: {config['textarea_selector']}")
                ok = False

            if ok:
                # Send a test message and check bot reply selector
                time.sleep(1.5)
                shadow_host = config.get('shadow_host', '').strip()
                bot_sel     = config['bot_message_selector']

                if shadow_host:
                    count_js = f"""() => {{
                        const host = document.querySelector('{shadow_host}');
                        if (!host || !host.shadowRoot) return -1;
                        return host.shadowRoot.querySelectorAll('{bot_sel}').length;
                    }}"""
                else:
                    count_js = f"() => document.querySelectorAll('{bot_sel}').length"

                pre = page.evaluate(count_js)
                if pre == -1:
                    print(f"  [✗] Shadow host NOT found: {shadow_host}")
                    ok = False
                else:
                    print(f"  [✓] Bot message selector found (pre-send count: {pre})")
                    # Send test message
                    page.fill(config['textarea_selector'], "hola")
                    page.press(config['textarea_selector'], 'Enter')
                    print("  Waiting up to 30s for bot to reply...")
                    deadline = time.time() + 30
                    while time.time() < deadline:
                        time.sleep(2)
                        count = page.evaluate(count_js)
                        if count > pre:
                            print(f"  [✓] Bot replied! Selector working correctly.")
                            break
                    else:
                        print(f"  [?] No reply detected in 30s — check bot_message_selector")

        except Exception as e:
            print(f"  [!] Error during test: {e}")
            ok = False
        finally:
            input("\n  Press Enter to close the browser and continue...")
            browser.close()

    return ok


# ─── Main wizard ──────────────────────────────────────────────────────────────

def wizard():
    os.makedirs(CONFIGS_DIR, exist_ok=True)

    print("\n" + "="*60)
    print("  Chatbot QA — Config Setup Wizard")
    print("="*60)
    print("\nThis wizard creates a config JSON for a new chatbot.")
    print("The config tells the runner how to interact with the chatbot UI.\n")

    # 1. Basic info
    name = ask("Chatbot name (e.g. 'My Company Support Bot')")
    if not name:
        print("Name is required.")
        return
    url = ask("Chatbot URL (full URL where the chat is embedded)")
    if not url:
        print("URL is required.")
        return
    description = ask("Short description (optional)", default="")

    # 2. Preset selection
    print("\n--- Chatbot Type ---")
    print("Select the chatbot platform, or 'custom' to enter selectors manually:\n")
    preset_keys = list(PRESETS.keys())
    for i, key in enumerate(preset_keys, 1):
        print(f"  {i}. {PRESETS[key]['label']}")

    choice = ask("\nEnter number", default="5")
    try:
        preset_key = preset_keys[int(choice) - 1]
    except (ValueError, IndexError):
        preset_key = "custom"

    preset = PRESETS[preset_key]
    print(f"\n  Using preset: {preset['label']}")

    # 3. Selectors — show preset defaults, allow override
    print("\n--- Selectors ---")
    print("These are CSS selectors for the chatbot UI elements.")
    print("Press Enter to keep the preset value, or type a new one.\n")

    print("HOW TO FIND SELECTORS:")
    print("  1. Open the chatbot URL in Chrome")
    print("  2. Right-click the element → Inspect")
    print("  3. In DevTools: right-click the element → Copy → Copy selector\n")

    chat_bubble = ask("Chat bubble selector (the button to open the chat)",
                      default=preset['chat_bubble_selector'])
    textarea    = ask("Textarea selector (where the user types)",
                      default=preset['textarea_selector'])

    print("\nShadow DOM: some chatbots use custom HTML elements (e.g. <reshape-chat>)")
    print("that hide their content inside a 'shadow root'.")
    uses_shadow = ask_yes_no("Does this chatbot use Shadow DOM?",
                              default=bool(preset.get('shadow_host')))

    shadow_host = ""
    if uses_shadow:
        shadow_host = ask("Shadow host tag (the custom element name, e.g. 'reshape-chat')",
                          default=preset.get('shadow_host', ''))

    bot_msg_sel = ask("Bot message selector (CSS selector inside shadow root if applicable)",
                      default=preset.get('bot_message_selector', ''))

    # 4. Timing settings
    print("\n--- Response Timing ---")
    print("These control how long to wait for the bot to finish responding.\n")
    timeout     = int(ask("Response timeout in seconds", default="120"))
    stability   = float(ask("Stability window in seconds (text unchanged = done)", default="6"))
    min_len     = int(ask("Minimum response length in characters", default="50"))

    # 5. Build and save config
    config = {
        "name":                  name,
        "description":           description,
        "url":                   url,
        "chat_bubble_selector":  chat_bubble,
        "textarea_selector":     textarea,
        "shadow_host":           shadow_host,
        "bot_message_selector":  bot_msg_sel,
        "response_timeout":      timeout,
        "stability_secs":        stability,
        "min_response_len":      min_len,
    }

    config_filename = f"config_{slug(name)}.json"
    config_path     = os.path.join(CONFIGS_DIR, config_filename)

    print(f"\n--- Config Preview ---")
    print(json.dumps(config, indent=2, ensure_ascii=False))

    if not ask_yes_no(f"\nSave to {config_path}?", default=True):
        print("Aborted.")
        return

    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    print(f"\n  [✓] Saved: {config_path}")

    # 6. Optional browser test
    if ask_yes_no("\nRun a quick browser test to verify the selectors?", default=True):
        ok = test_config(config)
        if ok:
            print("\n  [✓] Selectors look good!")
        else:
            print("\n  [!] Some selectors may need adjustment.")
            print(f"      Edit the config file: {config_path}")

    print("\n--- All done! ---")
    print(f"\nTo run QA on this chatbot:")
    print(f"  python chatbot_qa_runner.py --config {config_path} --input your_questions.csv")
    print(f"\nTo run a smoke test (first 3 questions only):")
    print(f"  python chatbot_qa_runner.py --config {config_path} --input your_questions.csv --limit 3\n")


def test_only(config_path: str):
    """Test an existing config file without going through the full wizard."""
    with open(config_path, encoding='utf-8') as f:
        config = json.load(f)
    print(f"\nTesting config: {config_path}")
    print(f"  Bot: {config.get('name')}")
    print(f"  URL: {config.get('url')}")
    ok = test_config(config)
    if ok:
        print("\n  [✓] Config is working!")
    else:
        print(f"\n  [!] Issues found — edit {config_path} and try again")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Chatbot Config Wizard")
    parser.add_argument('--test-only', metavar='CONFIG_PATH',
                        help='Test an existing config without running the wizard')
    args = parser.parse_args()

    if args.test_only:
        test_only(args.test_only)
    else:
        wizard()
