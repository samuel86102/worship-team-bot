import os
import pandas as pd
import requests
from urllib.parse import urlparse
from dotenv import load_dotenv
import random
import json
import calendar
import re
from datetime import datetime, timedelta

from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import(
    Configuration, ApiClient, MessagingApi, 
    ReplyMessageRequest, TextMessage
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

# === Load .env ===
load_dotenv()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
RUN_LOCAL_TEST = os.getenv("RUN_LOCAL_TEST", "").lower() in ("1", "true", "yes")

# === Validate Environment Variables ===
if not all([OPENROUTER_API_KEY, GOOGLE_SHEET_URL, LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET]):
    # ... (error message remains the same)
    exit()

# === Flask App Initialization ===
app = Flask(__name__)
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# === Google Sheet URL to CSV Link Conversion ===
def convert_to_csv_url(sheet_url):
    # ... (function remains the same)
    parsed = urlparse(sheet_url)
    path_parts = parsed.path.split("/")
    if "spreadsheets" in parsed.path and len(path_parts) >= 4:
        sheet_id = path_parts[3]
        return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    elif "export?format=csv" in sheet_url:
        return sheet_url
    raise ValueError(f"無法解析的 Google Sheet 連結格式: {sheet_url}")

CSV_URL = convert_to_csv_url(GOOGLE_SHEET_URL)

# === Data Loading with Cache ===
df_data = None
last_data_load_time = 0
DATA_CACHE_TTL = 60 # Cache for 1 minute for frequent updates

def load_data_from_sheet():
    # ... (function remains the same)
    global df_data, last_data_load_time
    current_time = pd.Timestamp.now().timestamp()
    if df_data is None or (current_time - last_data_load_time > DATA_CACHE_TTL):
        try:
            print(f"Fetching data from: {CSV_URL}")
            df = pd.read_csv(CSV_URL, header=1)
            df_data = df
            last_data_load_time = current_time
            print("Data loaded successfully.")
            if "日期" not in df_data.columns:
                print("WARNING: '日期' column not found.")
        except Exception as e:
            print(f"Error loading data: {e}")
            if df_data is None: raise
    return df_data

# === Prompt and Greeting Utilities ===
def read_system_prompt():
    with open("system_prompt.txt", "r", encoding="utf-8") as f:
        return f.read()

def get_random_greeting(json_path: str) -> str:
    # ... (function remains the same)
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            examples = json.load(f)
        return random.choice(examples) if examples else ""
    except Exception:
        return ""

# === Core Logic: LLM Date Parsing and Data Processing ===

def get_structured_date_command(user_input: str) -> str:
    """Calls LLM to get a structured date command based on the new system prompt."""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://your-app-name.com",
        "X-Title": "Worship Team Bot"
    }

    today = datetime.now()
    # Calculate dates for the prompt
    tomorrow = today + timedelta(days=1)
    next_friday = today + timedelta(days=(4 - today.weekday() + 7) % 7)

    # Prepare prompt variables
    prompt_vars = {
        "{{current_year}}": str(today.year),
        "{{today_str}}": today.strftime('%Y/%m/%d'),
        "{{today_weekday_str}}": ["週一", "週二", "週三", "週四", "週五", "週六", "週日"][today.weekday()],
        "{{tomorrow_str}}": tomorrow.strftime('%-m/%-d'),
        "{{next_friday_str}}": next_friday.strftime('%-m/%-d'),
    }

    system_prompt_template = read_system_prompt()
    system_prompt = system_prompt_template
    for key, value in prompt_vars.items():
        system_prompt = system_prompt.replace(key, value)

    data = {
        "model": "google/gemini-2.0-flash-lite-001",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input}
        ]
    }

    try:
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data)
        response.raise_for_status()
        command = response.json()["choices"][0]["message"]["content"].strip()
        print(f"LLM returned command: {command}")
        return command
    except requests.exceptions.RequestException as e:
        print(f"Error calling LLM for date command: {e}")
        # Print response body if available, as it might contain error details from the API
        if e.response is not None:
            print(f"LLM API response body: {e.response.text}")
        return "ERROR:API_FAILURE"
    except Exception as e:
        print(f"Error processing LLM response: {e}")
        # This will catch other errors like JSON decoding or missing keys
        return "ERROR:RESPONSE_PROCESSING_FAILURE"

def parse_llm_command(command: str) -> tuple[list[str], str]:
    """Parses the structured command from LLM and returns a list of dates and a description."""
    current_year = datetime.now().year
    
    if command.startswith("DATE:"):
        dates_str = command.split(":", 1)[1]
        dates = [d.strip() for d in dates_str.split(',') if d.strip()]
        return dates, dates_str

    elif command.startswith("MONTH:"):
        month_str = command.split(":", 1)[1]
        try:
            month = int(month_str)
            _, num_days = calendar.monthrange(current_year, month)
            dates = [f"{month}/{day}" for day in range(1, num_days + 1)]
            return dates, f"{current_year}年{month}月"
        except ValueError:
            return [], ""

    elif command.startswith("PATTERN:"):
        pattern_str = command.split(":", 1)[1]
        try:
            params = dict(item.split('=') for item in pattern_str.split(';'))
            month = int(params["MONTH"])
            weekday_map = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}
            weekday = weekday_map[params["WEEKDAY"]]
            
            cal = calendar.Calendar()
            month_days = cal.itermonthdays2(current_year, month)
            dates = [f"{month}/{day}" for day, wd in month_days if day != 0 and wd == weekday]
            
            weekday_chinese = {"MON": "一", "TUE": "二", "WED": "三", "THU": "四", "FRI": "五", "SAT": "六", "SUN": "日"}[params["WEEKDAY"]]
            return dates, f"{month}月的每個週{weekday_chinese}"
        except Exception as e:
            print(f"Error parsing PATTERN command: {e}")
            return [], ""
            
    return [], ""

def process_input(user_input: str) -> str:
    """Main processing logic for a user query."""
    # 1. Get structured command from LLM
    command = get_structured_date_command(user_input)

    if command.startswith("ERROR:"):
        return "抱歉，我暫時無法處理您的請求，請稍後再試。"

    # 2. Parse the command to get dates and description
    search_dates, query_description = parse_llm_command(command)

    if not search_dates:
        return "抱歉，我無法理解您輸入的日期，請換個方式問問看，例如「下週三」或「八月的每個週日」。"

    # 3. Load data and search
    try:
        df = load_data_from_sheet()

        if df is None or "日期" not in df.columns:
            return "抱歉，服事表資料暫時無法載入，請稍後再試。"
        
        df = df.iloc[:, 0:24]  # 保留前 24 欄（A-X）
        df["日期"] = df["日期"].astype(str).str.strip()
        all_results_text = []
        found_any_data = False

        for date_str in search_dates:
            result = df[df["日期"] == date_str]
            if not result.empty:
                found_any_data = True
                weekday = result['星期'].iloc[0] if '星期' in result.columns else ''
                details = [f"📅 {date_str} {weekday}", "\n💡服事人員💡"]
                for _, row in result.iterrows():
                    for col, val in row.items():
                        if col not in ['季度', '日期', '星期', ''] and pd.notna(val) and str(val).strip():
                            details.append(f"🔹{col}: {val}")
                all_results_text.append("\n".join(details))

        if found_any_data:
            greeting = get_random_greeting("greeting_templates.json")
            results_str = "\n\n---\n\n".join(all_results_text)
            #return f"{greeting}\n\n以下為您查詢「{query_description}」的結果：\n\n{results_str}"
            return f"以下為您查詢「{query_description}」的結果：\n\n{results_str}"
        else:
            return f"抱歉，我找不到「{query_description}」的服事表資訊。請確認該日期或月份有安排服事。"

    except Exception as e:
        print(f"Error during data processing: {e}")
        return "❌ 查詢失敗，發生內部錯誤。"

# === LINE Webhook and Flask App Setup ===

@app.route("/callback", methods=['POST'])
def callback():
    # ... (webhook handler remains the same)
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    except Exception as e:
        app.logger.error(f"Error in callback: {e}")
        abort(500)
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    # ... (message handler remains the same)
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        reply_text = process_input(event.message.text)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        )

if __name__ == "__main__":
    try:
        load_data_from_sheet()
        if df_data is None or "日期" not in df_data.columns:
            print("CRITICAL ERROR: Initial data load failed. Exiting.")
            exit(1)
        
        if RUN_LOCAL_TEST:
            print("\n--- Local Test Mode ---")
            while True:
                user_input = input("> ")
                if user_input.lower() == 'exit': break
                print(f"Bot: {process_input(user_input)}")
        else:
            port = int(os.environ.get('PORT', 5001))
            app.run(host='0.0.0.0', port=port, debug=False)

    except Exception as e:
        print(f"CRITICAL ERROR during startup: {e}. Exiting.")
        exit(1)
