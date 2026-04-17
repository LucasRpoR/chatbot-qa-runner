# Chatbot QA Runner

Automated QA pipeline for any web-based chatbot. Give it a chatbot URL, a CSV with questions, and your Anthropic API key — it handles the rest.

## Setup (one time)

```bash
# 1. Clone the repo
git clone https://github.com/LucasRpoR/chatbot-qa-runner.git
cd chatbot-qa-runner

# 2. Install dependencies
pip install -r requirements.txt
playwright install chromium
```

## Run

```bash
python run.py
```

The script will ask for three things:
1. **Chatbot URL** — the page where the chat is embedded
2. **CSV file path** — your questions file (see format below)
3. **Anthropic API key** — used to validate responses (saved locally for future runs)

It then auto-detects the chatbot UI, confirms with you, and runs all questions automatically.

### CSV format

At minimum, one column with the questions. A second column with expected behavior is optional but recommended for better validation:

| input | expected_output |
|-------|----------------|
| What materials do you sell? | Should list the main product categories |
| How do I place an order? | Should explain the ordering process |

Column names are detected automatically — if they're different, the script will ask you which column is which.

`Agent answer` and `Validation` columns are filled automatically.

## Output

Two files are saved after every question (safe to interrupt):

- `results_<chatbot>_<timestamp>.csv` — all questions with captured answers and validation scores
- `results_<chatbot>_<timestamp>.xlsx` — color-coded Excel report (green/yellow/red) + summary sheet

## Advanced usage

```bash
# Quick test with only the first 3 questions
python run.py --url https://... --csv questions.csv --limit 3

# Run directly with a saved config (skips auto-detection)
python chatbot_qa_runner.py --config chatbot_configs/config_item24.json --input questions.csv

# Resume an interrupted run
python chatbot_qa_runner.py --config chatbot_configs/config_item24.json --input questions.csv --resume

# Create a config manually (if auto-detection doesn't work for your chatbot)
python setup_chatbot.py
```

## How it works

1. Opens a browser (visible) and navigates to the chatbot URL
2. Auto-detects the chatbot type (supports ReshapeX, Intercom, Drift, Crisp, and generic chatbots)
3. For each question: opens a fresh chat session, sends the question, waits for the full response
4. Validates each response against the expected behavior using Claude AI
5. Saves results to CSV and Excel after every question
