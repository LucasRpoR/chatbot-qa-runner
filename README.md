# Chatbot QA Runner

Automated QA pipeline for any web-based chatbot. Sends questions from a CSV through the chatbot UI, captures full responses, and validates them against expected behavior using Claude AI.

## Setup (one time)

```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Install the browser
playwright install chromium

# 3. Add your Anthropic API key
# Create a file called anthropic_key.txt and paste your key inside
# (or set the ANTHROPIC_API_KEY environment variable)
```

## Running QA on a chatbot

### Step 1 — Prepare your CSV

The CSV needs these columns (any encoding — utf-8, cp1252, etc.):

| input | expected_output | Agent answer | Validation |
|-------|----------------|--------------|------------|
| Your question here | What the bot should ideally answer | *(auto-filled)* | *(auto-filled)* |

`Agent answer` and `Validation` can be empty — the script fills them automatically.

### Step 2 — Choose or create a config

Configs are in the `chatbot_configs/` folder. `config_item24.json` is included as an example.

To create a config for a new chatbot, run the interactive wizard:

```bash
python setup_chatbot.py
```

The wizard will ask for the chatbot URL, walk you through finding the right CSS selectors, and optionally test them in a live browser session.

### Step 3 — Run

```bash
python chatbot_qa_runner.py --config chatbot_configs/config_item24.json --input your_questions.csv
```

Results are saved after every question (safe to interrupt and resume):

```bash
# Resume an interrupted run
python chatbot_qa_runner.py --config chatbot_configs/config_item24.json --input your_questions.csv --resume

# Smoke test with only the first 3 questions
python chatbot_qa_runner.py --config chatbot_configs/config_item24.json --input your_questions.csv --limit 3

# Only run Claude validation (skip browser, validate already-captured answers)
python chatbot_qa_runner.py --config chatbot_configs/config_item24.json --input your_questions.csv --validate-only
```

## Output

Two files are created in the same directory:

- `results_<chatbot>_<timestamp>.csv` — all rows with Agent answer and Validation
- `results_<chatbot>_<timestamp>.xlsx` — color-coded Excel report (green/yellow/red) with a summary sheet

## Config file format

```json
{
  "name": "My Chatbot",
  "description": "Optional description",
  "url": "https://example.com/chat",

  "chat_bubble_selector": "button[class*='chat']",
  "textarea_selector": "textarea",
  "shadow_host": "",
  "bot_message_selector": ".bot-message",

  "response_timeout": 120,
  "stability_secs": 6.0,
  "min_response_len": 50
}
```

**Shadow DOM**: if the chatbot uses a custom HTML element (e.g. `<reshape-chat>`), set `shadow_host` to the element tag name and `bot_message_selector` to the selector *inside* the shadow root.

## How to find selectors

1. Open the chatbot URL in Chrome
2. Right-click the element → **Inspect**
3. In DevTools: right-click the highlighted element → **Copy → Copy selector**

Or run `python setup_chatbot.py` which guides you through this with a live browser session.
