"""
Generic Chatbot QA Runner
Runs questions from a CSV through any chatbot, captures responses, validates with Claude API.

Usage:
    python chatbot_qa_runner.py --config chatbot_configs/config_item24.json --input questions.csv
    python chatbot_qa_runner.py --config chatbot_configs/config_item24.json --input questions.csv --resume
    python chatbot_qa_runner.py --config chatbot_configs/config_item24.json --input questions.csv --validate-only
    python chatbot_qa_runner.py --config chatbot_configs/config_item24.json --input questions.csv --limit 5

CSV format expected (any encoding — utf-8, cp1252, latin-1):
    input, expected_output, Agent answer (optional), Validation (optional)
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)

import csv
import re
import time
import os
import json
import argparse
import glob
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter
import anthropic

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KEY_FILE   = os.path.join(SCRIPT_DIR, 'anthropic_key.txt')
FIELDS     = ['input', 'expected_output', 'Agent answer', 'Validation']

# ─── Streaming detection ──────────────────────────────────────────────────────

STREAMING_END_RE = re.compile(
    r'(Loading|Searching|Checking|Fetching|Looking up|Getting|Retrieving|'
    r'Let me (check|look|search|pull|find|review|open|locate|browse|verify))\s*[\w\s,]*\.\.\.\s*$',
    re.IGNORECASE | re.DOTALL
)
PLACEHOLDER_RE = re.compile(
    r'^(Reasoning|Thinking|Searching|Loading|\.{1,5})\s*$',
    re.IGNORECASE
)

def is_still_streaming(text: str) -> bool:
    if not text or not text.strip():
        return True
    stripped = text.strip()
    if PLACEHOLDER_RE.match(stripped):
        return True
    if STREAMING_END_RE.search(stripped):
        return True
    return False


# ─── Config-driven JS builders ────────────────────────────────────────────────

def build_js(config: dict):
    """Return (extract_js, count_js) based on config selectors (Shadow DOM or regular)."""
    shadow_host = config.get('shadow_host', '').strip()
    bot_sel     = config['bot_message_selector']

    if shadow_host:
        extract_js = f"""() => {{
            const host = document.querySelector('{shadow_host}');
            if (!host || !host.shadowRoot) return '';
            const msgs = Array.from(host.shadowRoot.querySelectorAll('{bot_sel}'));
            if (msgs.length === 0) return '';
            return msgs[msgs.length - 1].innerText.trim();
        }}"""
        count_js = f"""() => {{
            const host = document.querySelector('{shadow_host}');
            if (!host || !host.shadowRoot) return 0;
            return host.shadowRoot.querySelectorAll('{bot_sel}').length;
        }}"""
    else:
        extract_js = f"""() => {{
            const msgs = Array.from(document.querySelectorAll('{bot_sel}'));
            if (msgs.length === 0) return '';
            return msgs[msgs.length - 1].innerText.trim();
        }}"""
        count_js = f"""() => {{
            return document.querySelectorAll('{bot_sel}').length;
        }}"""

    return extract_js, count_js


# ─── Browser helpers ──────────────────────────────────────────────────────────

def open_chat(page, config: dict) -> bool:
    try:
        bubble = page.wait_for_selector(config['chat_bubble_selector'], timeout=12000)
        bubble.click()
        page.wait_for_selector(config['textarea_selector'], state="visible", timeout=10000)
        time.sleep(1.5)
        return True
    except PWTimeout:
        return False


def wait_for_response(page, pre_count: int, extract_js: str, count_js: str, config: dict) -> str:
    timeout      = config.get('response_timeout', 120)
    stability    = config.get('stability_secs', 6.0)
    min_len      = config.get('min_response_len', 50)

    start        = time.time()
    stable_text  = ""
    stable_since = None

    while time.time() - start < timeout:
        time.sleep(1.5)
        count = page.evaluate(count_js)
        if count <= pre_count:
            continue
        current = page.evaluate(extract_js)
        if is_still_streaming(current):
            stable_text  = ""
            stable_since = None
            continue
        if current != stable_text:
            stable_text  = current
            stable_since = None
            continue
        if stable_since is None:
            stable_since = time.time()
        elapsed = time.time() - stable_since
        if elapsed >= stability and len(current) >= min_len:
            return current
        if elapsed >= stability * 2 and current:
            return current

    return stable_text or "[Timeout — no response received]"


# ─── Claude validation ────────────────────────────────────────────────────────

def validate_with_claude(client, question: str, expected: str, agent_answer: str, config: dict) -> str:
    if not agent_answer or agent_answer.startswith("[Timeout") or agent_answer.startswith("ERROR"):
        return "Incorrecto - El chatbot no respondió o hubo un error técnico al obtener la respuesta"

    bot_name       = config.get('name', 'el chatbot')
    expected_block = (
        expected.strip()
        if expected.strip()
        else "(Sin referencia específica — evalúa basándote en buenas prácticas de atención al cliente)"
    )

    prompt = f"""Eres un evaluador de calidad para un chatbot de atención al cliente ({bot_name}).

Evalúa si la respuesta del chatbot cumple con el comportamiento esperado.

PREGUNTA DEL USUARIO:
{question}

COMPORTAMIENTO ESPERADO / RESPUESTA DE REFERENCIA:
{expected_block}

RESPUESTA REAL DEL CHATBOT:
{agent_answer}

Clasifica la respuesta del chatbot en UNA de estas categorías:
- Correcto: Cubre los puntos clave del comportamiento esperado de forma precisa y completa
- Parcialmente correcto: Cubre algunos puntos pero falta información importante, hay imprecisiones, o el tono/enfoque es incorrecto
- Incorrecto: Respuesta incorrecta, engañosa, incompleta de forma grave, o falla en cubrir los aspectos clave esperados

RESPONDE EXACTAMENTE en este formato (sin texto adicional antes ni después):
[Correcto|Parcialmente correcto|Incorrecto] - [Explicación breve en 1-2 oraciones]"""

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text.strip()
    except Exception as e:
        return f"Validación pendiente - Error API: {str(e)[:120]}"


# ─── CSV I/O ──────────────────────────────────────────────────────────────────

def load_csv(path: str) -> list:
    for enc in ['utf-8-sig', 'cp1252', 'latin-1', 'utf-8']:
        try:
            with open(path, encoding=enc, newline='') as f:
                rows = list(csv.DictReader(f))
            return [{k.strip(): v for k, v in r.items()} for r in rows]
        except (UnicodeDecodeError, LookupError):
            continue
    raise ValueError(f"Could not read {path} with any known encoding")


def save_csv(rows: list, output_path: str):
    with open(output_path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction='ignore')
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, '') for k in FIELDS})


def find_latest_results(chatbot_slug: str, output_dir: str) -> str | None:
    files = glob.glob(os.path.join(output_dir, f'results_{chatbot_slug}_*.csv'))
    return max(files, key=os.path.getmtime) if files else None


# ─── Excel report ─────────────────────────────────────────────────────────────

HEADER_BLUE = "1F3864"
SUB_BLUE    = "2E75B6"
GREEN       = "C6EFCE"
YELLOW      = "FFEB9C"
RED         = "FFC7CE"
ALT_ROW     = "EEF3F8"

def _fill(hex_color):
    return PatternFill("solid", fgColor=hex_color)

def _border():
    s = Side(style='thin', color='B8CCE4')
    return Border(left=s, right=s, top=s, bottom=s)

def build_excel(rows: list, output_xlsx: str, config: dict):
    wb  = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "Resultados"
    ws2 = wb.create_sheet("Resumen")

    headers    = ['Pregunta', 'Comportamiento Esperado', 'Respuesta del Agente', 'Validación']
    col_widths = [40, 45, 55, 45]

    for col, (h, w) in enumerate(zip(headers, col_widths), 1):
        c = ws1.cell(row=1, column=col, value=h)
        c.fill      = _fill(HEADER_BLUE)
        c.font      = Font(bold=True, color="FFFFFF", size=11)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border    = _border()
        ws1.column_dimensions[get_column_letter(col)].width = w
    ws1.row_dimensions[1].height = 30

    for i, r in enumerate(rows, 2):
        verdict = (r.get('Validation') or '').lower()
        if verdict.startswith('correcto'):        bg = GREEN
        elif verdict.startswith('parcialmente'):  bg = YELLOW
        elif verdict.startswith('incorrecto'):    bg = RED
        else: bg = ALT_ROW if i % 2 == 0 else "FFFFFF"

        for col, key in enumerate(FIELDS, 1):
            c = ws1.cell(row=i, column=col, value=r.get(key, ''))
            c.fill      = _fill(bg)
            c.alignment = Alignment(vertical="top", wrap_text=True)
            c.border    = _border()
            c.font      = Font(size=10)
        ws1.row_dimensions[i].height = 60

    ws1.freeze_panes = "A2"

    # Summary sheet
    ws2.column_dimensions['A'].width = 30
    ws2.column_dimensions['B'].width = 12
    ws2.column_dimensions['C'].width = 10

    title = ws2.cell(row=1, column=1, value=f"QA Results — {config.get('name', 'Chatbot')}")
    title.font      = Font(bold=True, size=13, color="FFFFFF")
    title.fill      = _fill(HEADER_BLUE)
    title.alignment = Alignment(horizontal="center")
    ws2.merge_cells('A1:C1')
    ws2.row_dimensions[1].height = 28

    for col, h in enumerate(['Categoría', 'Cantidad', '%'], 1):
        c = ws2.cell(row=2, column=col, value=h)
        c.fill      = _fill(SUB_BLUE)
        c.font      = Font(bold=True, color="FFFFFF", size=10)
        c.alignment = Alignment(horizontal="center")
        c.border    = _border()
    ws2.row_dimensions[2].height = 22

    verdicts = [
        ("Correcto",              GREEN,    lambda v: v.startswith('correcto')),
        ("Parcialmente correcto", YELLOW,   lambda v: v.startswith('parcialmente')),
        ("Incorrecto",            RED,      lambda v: v.startswith('incorrecto')),
        ("Sin validar",           ALT_ROW,  lambda v: not v),
        ("TOTAL",                 SUB_BLUE, lambda v: True),
    ]
    for row_num, (label, color, fn) in enumerate(verdicts, 3):
        count = sum(1 for r in rows if fn((r.get('Validation') or '').lower()))
        pct   = f"{count / len(rows) * 100:.1f}%" if rows else "—"
        for col, val in enumerate([label, count, pct], 1):
            c = ws2.cell(row=row_num, column=col, value=val)
            c.alignment = Alignment(horizontal="center", vertical="center")
            c.border    = _border()
            c.font      = Font(size=10, bold=(label == "TOTAL"),
                               color="FFFFFF" if color == SUB_BLUE else "000000")
            c.fill      = _fill(color)
        ws2.row_dimensions[row_num].height = 22

    wb.save(output_xlsx)
    print(f"  Excel → {output_xlsx}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generic Chatbot QA Runner")
    parser.add_argument('--config',           required=True, help='Path to chatbot config JSON (see chatbot_configs/)')
    parser.add_argument('--input',            required=True, help='Path to CSV with questions (columns: input, expected_output)')
    parser.add_argument('--output-dir',       default=SCRIPT_DIR, help='Directory for result files (default: script directory)')
    parser.add_argument('--resume',           action='store_true', help='Resume from latest results file for this chatbot')
    parser.add_argument('--validate-only',    action='store_true', help='Skip browser; only run Claude validation on existing answers')
    parser.add_argument('--limit',            type=int, default=0,  help='Process only first N incomplete rows (smoke test)')
    parser.add_argument('--clean-incomplete', action='store_true', help='Clear rows with streaming artifacts and re-run them')
    parser.add_argument('--rerun-incorrect',  action='store_true', help='Clear Incorrecto rows and re-run them')
    args = parser.parse_args()

    # Load config
    config_path = os.path.abspath(args.config)
    with open(config_path, encoding='utf-8') as f:
        config = json.load(f)

    chatbot_slug = re.sub(r'[^a-z0-9]+', '_', config.get('name', 'chatbot').lower()).strip('_')
    output_dir   = os.path.abspath(args.output_dir)
    timestamp    = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_csv   = os.path.join(output_dir, f'results_{chatbot_slug}_{timestamp}.csv')
    output_xlsx  = os.path.join(output_dir, f'results_{chatbot_slug}_{timestamp}.xlsx')

    print(f"\n{'='*65}")
    print(f"  Chatbot QA Runner")
    print(f"  Bot:    {config.get('name', '?')}")
    print(f"  URL:    {config['url']}")
    print(f"  Input:  {args.input}")
    print(f"  Output: {output_csv}")
    print(f"{'='*65}\n")

    # Load rows
    if args.resume:
        latest = find_latest_results(chatbot_slug, output_dir)
        if latest:
            print(f"  Resuming from: {os.path.basename(latest)}")
            rows = load_csv(latest)
        else:
            print(f"  No resume file found — starting fresh")
            rows = load_csv(args.input)
    else:
        rows = load_csv(args.input)
    print(f"  Loaded {len(rows)} rows")

    # Ensure all columns exist
    for r in rows:
        r.setdefault('Agent answer', '')
        r.setdefault('Validation', '')

    # Flags to clear rows
    if args.clean_incomplete:
        cleaned = 0
        for r in rows:
            if r.get('Agent answer','').strip() and is_still_streaming(r['Agent answer']):
                r['Agent answer'] = ''
                r['Validation']   = ''
                cleaned += 1
        print(f"  Cleaned {cleaned} incomplete/streaming rows")

    if args.rerun_incorrect:
        cleared = 0
        for r in rows:
            if (r.get('Validation') or '').lower().startswith('incorrecto'):
                r['Agent answer'] = ''
                r['Validation']   = ''
                cleared += 1
        print(f"  Cleared {cleared} Incorrecto rows")

    need_browser    = sum(1 for r in rows if not r.get('Agent answer','').strip())
    need_validation = sum(1 for r in rows if not r.get('Validation','').strip())
    already_done    = sum(1 for r in rows if r.get('Agent answer','').strip() and r.get('Validation','').strip())
    print(f"  Complete (skip):  {already_done}")
    print(f"  Need browser:     {need_browser}")
    print(f"  Need validation:  {need_validation}\n")

    # Anthropic API key
    api_key = os.environ.get('ANTHROPIC_API_KEY', '').strip()
    if not api_key and os.path.isfile(KEY_FILE):
        with open(KEY_FILE) as kf:
            api_key = kf.read().strip()
        if api_key:
            print("  Anthropic API: key loaded from anthropic_key.txt")
    if not api_key:
        api_key = input("  Enter your Anthropic API key: ").strip()

    client = anthropic.Anthropic(api_key=api_key)
    try:
        client.models.list()
        print("  Anthropic API: OK\n")
    except Exception as e:
        print(f"  Anthropic API: WARNING — {e}\n")

    # Build JS extractors from config
    extract_js, count_js = build_js(config)

    # Save initial CSV so partial results are never lost
    os.makedirs(output_dir, exist_ok=True)
    save_csv(rows, output_csv)
    print(f"Saved initial CSV → {output_csv}\n")

    # ── Main loop ────────────────────────────────────────────────────────────
    processed = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)

        for i, row in enumerate(rows):
            has_answer     = bool(row.get('Agent answer','').strip())
            has_validation = bool(row.get('Validation','').strip())
            question       = (row.get('input') or '').strip()

            if has_answer and has_validation:
                print(f"[{i+1:3}/{len(rows)}] SKIP")
                continue

            if args.limit and processed >= args.limit:
                print(f"\n--limit {args.limit} reached, stopping early.")
                break

            tag = "CHAT+VALIDATE" if not has_answer else "VALIDATE"
            print(f"[{i+1:3}/{len(rows)}] {tag}")
            print(f"  Q: {question[:90]}")

            page = None
            try:
                if not has_answer and not args.validate_only:
                    page = browser.new_page()
                    page.goto(config['url'], wait_until='domcontentloaded', timeout=30000)
                    time.sleep(2)

                    if not open_chat(page, config):
                        raise RuntimeError("Could not open chat bubble")

                    pre_count = page.evaluate(count_js)
                    page.fill(config['textarea_selector'], question)
                    page.press(config['textarea_selector'], 'Enter')

                    answer = wait_for_response(page, pre_count, extract_js, count_js, config)
                    page.close()
                    page = None

                    row['Agent answer'] = answer
                    print(f"  → {len(answer)} chars: {answer[:100]}...")

                if not row.get('Validation','').strip():
                    verdict = validate_with_claude(
                        client, question,
                        row.get('expected_output',''),
                        row.get('Agent answer',''),
                        config
                    )
                    row['Validation'] = verdict
                    print(f"  → {verdict[:90]}")

            except Exception as e:
                print(f"  ERROR: {e}")
                if not row.get('Agent answer','').strip():
                    row['Agent answer'] = f"ERROR: {str(e)[:200]}"
                row['Validation'] = "Incorrecto - Error técnico"
                if page:
                    try: page.close()
                    except Exception: pass

            save_csv(rows, output_csv)
            processed += 1

        browser.close()

    # ── Report ────────────────────────────────────────────────────────────────
    print(f"\n{'='*65}")
    print("Building Excel report...")
    build_excel(rows, output_xlsx, config)

    total      = len(rows)
    correcto   = sum(1 for r in rows if (r.get('Validation') or '').lower().startswith('correcto'))
    parcial    = sum(1 for r in rows if (r.get('Validation') or '').lower().startswith('parcialmente'))
    incorrecto = sum(1 for r in rows if (r.get('Validation') or '').lower().startswith('incorrecto'))

    print(f"\n  Correcto:              {correcto:3}  ({correcto/total*100:.1f}%)")
    print(f"  Parcialmente correcto: {parcial:3}  ({parcial/total*100:.1f}%)")
    print(f"  Incorrecto:            {incorrecto:3}  ({incorrecto/total*100:.1f}%)")
    print(f"  {'─'*40}")
    print(f"  Total:                 {total}")
    print(f"\n  CSV  → {output_csv}")
    print(f"  XLSX → {output_xlsx}\n")


if __name__ == '__main__':
    main()
