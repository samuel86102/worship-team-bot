import os
import pandas as pd
import requests
from urllib.parse import urlparse
from dotenv import load_dotenv
import random
import json


from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from calendar import monthrange # Used for getting days in month

# === Load .env ===
load_dotenv()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL")
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
LINE_CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
RUN_LOCAL_TEST = os.getenv("RUN_LOCAL_TEST", "").lower() in ("1", "true", "yes")




if not all([OPENROUTER_API_KEY, GOOGLE_SHEET_URL, LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET]):
    print("ERROR: Missing one or more environment variables. Please check your .env file.")
    print(f"OPENROUTER_API_KEY: {'Set' if OPENROUTER_API_KEY else 'Not Set'}")
    print(f"GOOGLE_SHEET_URL: {'Set' if GOOGLE_SHEET_URL else 'Not Set'}")
    print(f"LINE_CHANNEL_ACCESS_TOKEN: {'Set' if LINE_CHANNEL_ACCESS_TOKEN else 'Not Set'}")
    print(f"LINE_CHANNEL_SECRET: {'Set' if LINE_CHANNEL_SECRET else 'Not Set'}")
    exit()

# === Flask App Initialization ===
app = Flask(__name__)

configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# === Convert Google Sheet URL to CSV Download Link ===
def convert_to_csv_url(sheet_url):
    parsed = urlparse(sheet_url)
    path_parts = parsed.path.split("/")
    if "spreadsheets" in parsed.path and len(path_parts) >= 4 : # Ensure 'd' and sheet_id are present
        sheet_id = path_parts[3]
        return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
    else:
        if "export?format=csv" in sheet_url:
            return sheet_url
        raise ValueError(f"無法解析的 Google Sheet 連結格式: {sheet_url}")

CSV_URL = convert_to_csv_url(GOOGLE_SHEET_URL)

# === Load Data ===
df_data = None
last_data_load_time = 0
#DATA_CACHE_TTL = 3600 # Cache for 1 hour
DATA_CACHE_TTL = 60 # Cache for 1 hour

def load_data_from_sheet():
    global df_data, last_data_load_time
    current_time = pd.Timestamp.now().timestamp()
    if df_data is None or (current_time - last_data_load_time > DATA_CACHE_TTL):
        try:
            print(f"Fetching data from: {CSV_URL}")
            df = pd.read_csv(CSV_URL, header=1) # header=1 means use the second row as column names
            df_data = df
            last_data_load_time = current_time
            print("Data loaded successfully.")
            print(f"Columns: {df_data.columns.tolist()}")
            if "日期" not in df_data.columns:
                print("WARNING: '日期' column not found in the loaded data. Please check Google Sheet header (should be in the second row).")
                print(f"Available columns: {df_data.columns.tolist()}")
        except Exception as e:
            print(f"Error loading data: {e}")
            if df_data is None:
                raise
    return df_data



def read_system_prompt():
    with open("system_prompt.txt", "r", encoding="utf-8") as f:
        return f.read()

# === Call LLM to Classify User Input ===

def question_classifier(user_input):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://your-app-name.com",  # Optional
        "X-Title": "Church Service Lookup"            # Optional
    }

    category = ["服事表", "歌曲"]
    category_text = "\n- " + "\n- ".join(category)
    system_prompt = f"""
    你是一個分類模型，負責判斷使用者的輸入屬於哪個類別。請根據使用者的輸入，在以下類別中選出一個最符合的類別並回傳該類別名稱：{category_text}
    請只回傳類別名稱，不要回傳其他文字。
    """

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
        llm_response_content = response.json()["choices"][0]["message"]["content"].strip()
        llm_response_content = llm_response_content.replace('"', '').replace("'", "").strip()
        return llm_response_content
    except requests.exceptions.RequestException as e:
        print(f"Error calling OpenRouter API: {e}")
        if response is not None:
            print(f"Response content: {response.text}")
        return None
    except (KeyError, IndexError) as e:
        print(f"Error parsing OpenRouter response: {e}")
        print(f"Response content: {response.text if response else 'No response'}")
        return None

def get_random_greeting(json_path: str) -> str:
    """
    從 JSON 檔案中隨機選取一段範例內容。

    Args:
        json_path (str): JSON 檔案路徑，格式為 list[str]

    Returns:
        str: 隨機選取的範例文字
    """
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            examples = json.load(f)
        
        if not isinstance(examples, list) or not examples:
            return "JSON 檔案格式錯誤或為空。"

        return random.choice(examples)
    
    except FileNotFoundError:
        return f"找不到檔案：{json_path}"
    except json.JSONDecodeError:
        return "JSON 格式錯誤。"
    except Exception as e:
        return f"發生錯誤：{str(e)}"





# === Call LLM to Get Greeting Text ===
def get_greeting_text(user_input):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://your-app-name.com", # Optional: Replace with your app's URL
        "X-Title": "Church Service Lookup" # Optional: Replace with your app's name
    }


    system_prompt = f"""
    zh-tw 你是石門浸信會的敬拜團小幫手，負責幫助使用者查詢某一天的服事表。我們已經接收到的日期是「{user_input}」。請回應一段有溫度、親切的話，像是一位關心弟兄姊妹的服事同工，確認你幫他找到了 {user_input} 的服事表。

    請使用繁體中文回應，最後請加上一句：「這是我找到的：」
    請按照這個範例給出差異不大的回答:
    平安！我是石浸敬拜團的小幫手😊
    我幫你確認了 5/18 的服事表，讓我把詳細的內容提供給你。非常感謝你在服事上的擺上，願上帝大大祝福你！
    這是我找到的：
    """

    data = {
        #"model": "google/gemini-flash-1.5-8b", # Ensure this model is suitable for the task
        "model": "google/gemini-2.0-flash-lite-001", # Ensure this model is suitable for the task
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input}
        ]
    }
    try:
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data)
        response.raise_for_status() # Raise an exception for HTTP errors
        llm_response_content = response.json()["choices"][0]["message"]["content"].strip()
        # Further sanitize to remove any accidental quotes if the LLM adds them
        llm_response_content = llm_response_content.replace('"', '').replace("'", "")
        return llm_response_content
    except requests.exceptions.RequestException as e:
        print(f"Error calling OpenRouter API: {e}")
        if response is not None:
            print(f"Response content: {response.text}")
        return None # Indicate failure
    except (KeyError, IndexError) as e:
        print(f"Error parsing OpenRouter response: {e}")
        print(f"Response content: {response.text if response else 'No response'}")
        return None # Indicate failure


# === Call LLM to Convert Date Text ===
def get_formatted_date(user_input):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://your-app-name.com", # Optional: Replace with your app's URL
        "X-Title": "Church Service Lookup" # Optional: Replace with your app's name
    }
    # Get current date for the prompt
    # Current time is Tuesday, May 13, 2025.
    # The pd.Timestamp.now() will reflect the execution environment's current date.
    # For testing consistency with your example:
    # today_obj = pd.Timestamp("2025-05-13") # For forcing a specific date for testing prompt logic
    today_obj = pd.Timestamp.now()
    today_str = today_obj.strftime('%Y年%m月%d日')
    today_weekday_str = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"][today_obj.weekday()]



    system_prompt_template = read_system_prompt()
    system_prompt = system_prompt_template.replace("{{today_str}}", today_str).replace("{{today_weekday_str}}", today_weekday_str)


    data = {
        "model": "google/gemini-flash-1.5-8b", # Ensure this model is suitable for the task
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input}
        ]
    }
    try:
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data)
        response.raise_for_status() # Raise an exception for HTTP errors
        llm_response_content = response.json()["choices"][0]["message"]["content"].strip()
        # Further sanitize to remove any accidental quotes if the LLM adds them
        llm_response_content = llm_response_content.replace('"', '').replace("'", "")
        return llm_response_content
    except requests.exceptions.RequestException as e:
        print(f"Error calling OpenRouter API: {e}")
        if response is not None:
            print(f"Response content: {response.text}")
        return None # Indicate failure
    except (KeyError, IndexError) as e:
        print(f"Error parsing OpenRouter response: {e}")
        print(f"Response content: {response.text if response else 'No response'}")
        return None # Indicate failure



# === Line Webhook Endpoint ===
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.error("Invalid signature. Please check your channel access token/secret.")
        abort(400)
    except Exception as e:
        app.logger.error(f"Error handling request: {e}")
        abort(500)
    return 'OK'

def process_input(user_input):
    reply_text = ""
    found = False
    try:
        # 1. Get formatted date string from LLM
        llm_output = get_formatted_date(user_input)
        print(f"User input: {user_input}, LLM output: {llm_output}")

        if not llm_output:
            reply_text = "抱歉!我無法理解您輸入的日期，請再試一次~"
        else:
            # 2. Load data (uses cached version if available)
            df = load_data_from_sheet()
            if df is None or "日期" not in df.columns:
                reply_text = "抱歉!服事表資料暫時無法載入，請稍後再試~"
            else:
                df["日期"] = df["日期"].astype(str).str.strip()
                search_dates = []
                query_description = "" # For the reply header

                if llm_output.startswith("MONTH:"):
                    try:
                        year_month_str = llm_output.split(":")[1]
                        year, month = map(int, year_month_str.split('/'))
                        # Generate all M/D for that month
                        num_days = monthrange(year, month)[1] # (weekday of first day, num_days)
                        for day in range(1, num_days + 1):
                            search_dates.append(f"{month}/{day}")
                        query_description = f"{year}年{month}月"
                        print(f"Month query for {year}-{month}, generated {len(search_dates)} dates.")
                    except Exception as e:
                        print(f"Error parsing month string '{llm_output}': {e}")
                        reply_text = f"抱歉，解析月份資訊 '{llm_output}' 時出錯，請確認格式或重新提問。"
                else:
                    search_dates = [date_str.strip() for date_str in llm_output.split(',') if date_str.strip()]
                    query_description = llm_output
                    print(f"Date(s) query for: {search_dates}")

                if not reply_text: # Proceed if no error during month parsing
                    all_results_text = []
                    found_any_data = False
                    if not search_dates: # If after parsing, search_dates is empty (e.g. LLM returned just a comma)
                            reply_text = "抱歉，我無法解析您輸入的日期，請再試一次。"
                    else:
                        for search_date_str in search_dates:
                            result = df[df["日期"] == search_date_str]

                            if not result.empty:
                                found_any_data = True
                                found = True
                                date_reply_parts = [f"📅 {search_date_str} {result['星期'].iloc[0]}\n\n💡服事人員💡"]
                                for index, row in result.iterrows():
                                    row_details = []
                                    for col in result.columns:
                                        if col in ['季度','日期','星期','']:
                                            continue
                                        if pd.notna(row[col]) and str(row[col]).strip() != "":
                                            row_details.append(f"🔹{col}: {row[col]}")
                                    if row_details:
                                        date_reply_parts.append("\n".join(row_details))
                                all_results_text.append("\n".join(date_reply_parts))
                        
                        if found_any_data:
                            reply_text = f"以下為 {query_description} 的服事表:\n\n" + "\n\n".join(all_results_text)
                        else:
                            reply_text = f"抱歉！找不到 {query_description} 的服事表🙁\n請確認日期或月份是否有安排服事"

    except Exception as e:
        print(f"Error processing message: {e}")
        reply_text = f"❌ 查詢失敗，發生內部錯誤。請稍後再試或聯絡管理員。"

    final_reply = ""
    if found:
        greeting_text = get_random_greeting("greeting_templates.json")
        final_reply += greeting_text + "\n\n"
    
    final_reply += reply_text
    return final_reply

# === Line Message Handler ===
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        user_input = event.message.text
        
        reply_text = process_input(user_input)

        messages = [TextMessage(text=reply_text)]

        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=messages
            )
        )

if __name__ == "__main__":
    # Load data once at startup to ensure it's available and check for "日期" column
    try:
        load_data_from_sheet()
        if df_data is None:
            print("CRITICAL ERROR: Data (df_data) is None after initial load. Exiting.")
            exit(1)
        if "日期" not in df_data.columns:
            print("CRITICAL ERROR: '日期' column not found in the loaded data during startup. Please check Google Sheet header. Exiting.")
            exit(1)
        print(f"Initial data load successful. Columns: {df_data.columns.tolist()}")
        print(f"Sample '日期' values: {df_data['日期'].dropna().unique()[:5] if '日期' in df_data.columns else 'Column not found'}")

        # Check if running in local test mode
        if RUN_LOCAL_TEST:
            print("\n--- Local Test Mode ---")
            print("Enter your message to test the bot. Type 'exit' to quit.")
            while True:
                user_input = input("> ")
                if user_input.lower() == 'exit':
                    break
                response = process_input(user_input)
                print(f"Bot: {response}")
        else:
            port = int(os.environ.get('PORT', 5001))
            app.run(host='0.0.0.0', port=port, debug=False)

    except Exception as e:
        print(f"CRITICAL ERROR during startup: {e}. Exiting.")
        exit(1)
