import hashlib
import hmac
import math
import random
import logging
import requests
import secrets
import threading
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials


st.set_page_config(
    page_title="課堂報告互評系統",
    page_icon="📝",
    layout="wide",
)

# 全站視覺美化（僅調整外觀，不影響任何功能與資料內容）。
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;600;700;900&display=swap');

    :root {
        --brand-1: #6C5CE7;
        --brand-2: #8E7BFF;
        --brand-3: #00C2A8;
        --ink: #1F2430;
        --muted: #6B7280;
        --card-bg: #FFFFFF;
        --card-border: rgba(108, 92, 231, 0.14);
        --shadow: 0 4px 18px rgba(31, 36, 48, 0.06);
    }

    html, body, [class*="css"] {
        font-family: 'Noto Sans TC', -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif !important;
    }

    /* 整體背景：柔和漸層，避免死板的純白 */
    [data-testid="stAppViewContainer"] {
        background: radial-gradient(1200px 600px at 10% -10%, #F3F1FF 0%, rgba(243,241,255,0) 60%),
                    radial-gradient(1000px 500px at 100% 0%, #E8FBF7 0%, rgba(232,251,247,0) 55%),
                    #FAFAFC;
    }

    [data-testid="stHeader"] {
        background: rgba(255, 255, 255, 0.0);
    }

    .block-container {
        padding-top: 2.2rem;
        max-width: 1180px;
    }

    /* 標題群組 */
    h1 {
        color: var(--ink) !important;
        font-weight: 900 !important;
        letter-spacing: 0.02em;
        padding-bottom: 0.3rem;
        border-bottom: 4px solid transparent;
        border-image: linear-gradient(90deg, var(--brand-1), var(--brand-3)) 1;
        margin-bottom: 1.1rem !important;
    }

    h2 {
        color: var(--ink) !important;
        font-weight: 700 !important;
        border-left: 6px solid var(--brand-1);
        padding-left: 0.6rem;
        margin-top: 1.4rem !important;
    }

    h3 {
        color: var(--ink) !important;
        font-weight: 600 !important;
        position: relative;
        padding-left: 0.9rem;
    }
    h3::before {
        content: "";
        position: absolute;
        left: 0;
        top: 0.35em;
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: var(--brand-3);
    }

    /* 側邊欄 */
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #2A2356 0%, #362B7A 100%);
    }
    section[data-testid="stSidebar"] * {
        color: #F1EEFF !important;
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label {
        background: rgba(255, 255, 255, 0.06);
        border-radius: 10px;
        padding: 0.5rem 0.75rem;
        margin-bottom: 0.35rem;
        transition: background 0.15s ease, transform 0.1s ease;
        border: 1px solid rgba(255, 255, 255, 0.08);
    }
    section[data-testid="stSidebar"] div[role="radiogroup"] label:hover {
        background: rgba(255, 255, 255, 0.14);
        transform: translateX(2px);
    }
    section[data-testid="stSidebar"] button {
        background: rgba(255, 255, 255, 0.1) !important;
        border: 1px solid rgba(255, 255, 255, 0.25) !important;
        color: #FFFFFF !important;
        border-radius: 10px !important;
    }
    section[data-testid="stSidebar"] button:hover {
        background: rgba(255, 255, 255, 0.22) !important;
    }

    /* 表單卡片化 */
    div[data-testid="stForm"] {
        background: var(--card-bg);
        border: 1px solid var(--card-border);
        border-radius: 16px;
        padding: 1.4rem 1.4rem 0.8rem 1.4rem;
        box-shadow: var(--shadow);
    }

    /* 分頁 Tabs */
    button[data-baseweb="tab"] {
        font-weight: 600;
        border-radius: 10px 10px 0 0 !important;
    }
    div[data-baseweb="tab-highlight"] {
        background-color: var(--brand-1) !important;
        height: 3px !important;
    }

    /* 一般按鈕 */
    .stButton > button, .stDownloadButton > button {
        border-radius: 10px !important;
        border: 1px solid var(--card-border) !important;
        font-weight: 600 !important;
        transition: transform 0.1s ease, box-shadow 0.15s ease !important;
        box-shadow: 0 2px 8px rgba(31, 36, 48, 0.05);
    }
    .stButton > button:hover, .stDownloadButton > button:hover {
        transform: translateY(-1px);
        box-shadow: 0 6px 16px rgba(108, 92, 231, 0.18);
    }
    button[kind="primary"], button[kind="primaryFormSubmit"] {
        background: linear-gradient(90deg, var(--brand-1), var(--brand-2)) !important;
        border: none !important;
        color: #FFFFFF !important;
    }
    button[kind="primary"]:hover, button[kind="primaryFormSubmit"]:hover {
        filter: brightness(1.05);
    }

    /* 輸入元件 */
    div[data-testid="stTextInput"] input,
    div[data-testid="stNumberInput"] input,
    div[data-testid="stTextArea"] textarea,
    div[data-baseweb="select"] > div {
        border-radius: 10px !important;
        border-color: var(--card-border) !important;
    }
    div[data-testid="stTextInput"] input:focus,
    div[data-testid="stNumberInput"] input:focus,
    div[data-testid="stTextArea"] textarea:focus {
        border-color: var(--brand-1) !important;
        box-shadow: 0 0 0 2px rgba(108, 92, 231, 0.15) !important;
    }

    /* 讓老師查看學生互評內容時，停用的文字框仍以黑色顯示。 */
    div[data-testid="stTextArea"] label,
    div[data-testid="stTextArea"] label p,
    div[data-testid="stTextArea"] textarea,
    div[data-testid="stTextArea"] textarea:disabled {
        color: #000000 !important;
        -webkit-text-fill-color: #000000 !important;
        opacity: 1 !important;
    }

    /* 提示訊息（info / success / warning / error）*/
    div[data-testid="stAlert"] {
        border-radius: 12px !important;
        box-shadow: var(--shadow);
        border-left-width: 6px !important;
        border-left-style: solid !important;
        font-weight: 500;
    }

    /* 表格 / DataFrame */
    div[data-testid="stDataFrame"] {
        border-radius: 12px;
        overflow: hidden;
        box-shadow: var(--shadow);
        border: 1px solid var(--card-border);
    }

    /* 摺疊分隔線 */
    hr {
        border-color: var(--card-border) !important;
    }

    /* Caption 文字 */
    .stCaption, [data-testid="stCaptionContainer"] {
        color: var(--muted) !important;
    }

    /* Checkbox / Slider 強調色 */
    div[data-testid="stCheckbox"] label span[aria-checked="true"] {
        background-color: var(--brand-1) !important;
        border-color: var(--brand-1) !important;
    }
    div[data-baseweb="slider"] div[role="slider"] {
        background-color: var(--brand-1) !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

TZ = ZoneInfo("Asia/Taipei")
REVIEW_SECONDS = 10 * 60
STORE_SCHEMA_VERSION = 4

HEADERS = {
    "roster": ["student_id", "name"],
    "schedule": ["date", "order", "student_id"],
    "users": ["student_id", "name", "password_hash", "created_at"],
    "sessions": [
        "session_id",
        "date",
        "presenter_order",
        "presenter_id",
        "presenter_name",
        "started_at",
        "ends_at",
        "status",
    ],
    "reviews": [
        "session_id",
        "date",
        "reviewer_id",
        "reviewer_name",
        "presenter_id",
        "presenter_name",
        "score",
        "comment",
        "submitted_at",
    ],
    "grades": [
        "session_id",
        "date",
        "presenter_id",
        "presenter_name",
        "grade",
        "feedback",
        "updated_at",
    ],
    "review_grades": [
        "session_id",
        "date",
        "presenter_id",
        "presenter_name",
        "reviewer_id",
        "reviewer_name",
        "teacher_score",
        "teacher_feedback",
        "updated_at",
    ],
}


@st.cache_resource
def api_runtime():
    return {"lock": threading.RLock(), "last_call": 0.0}


def api_call(operation, attempts=5):
    """全使用者共用節流；寫入均為指定儲存格更新，可安全重試同一範圍。"""
    runtime = api_runtime()
    for attempt in range(attempts):
        try:
            with runtime["lock"]:
                delay = 1.1 - (time.monotonic() - runtime["last_call"])
                if delay > 0:
                    time.sleep(delay)
                runtime["last_call"] = time.monotonic()
                return operation()
        except gspread.exceptions.APIError as error:
            status = getattr(getattr(error, "response", None), "status_code", 0)
            if status not in (429, 500, 502, 503, 504):
                logging.exception("Google Sheets request failed")
                raise ValueError("試算表存取失敗，請通知管理者檢查權限與伺服器紀錄。") from error
            if attempt == attempts - 1:
                raise ValueError("試算表忙碌，尚未確認儲存成功；請保留評語，稍後重新提交。") from error
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as error:
            if attempt == attempts - 1:
                raise ValueError("連線中斷，尚未確認儲存成功；請保留評語，稍後重新提交。") from error
        time.sleep(min(2 ** attempt, 16) + random.uniform(0, 1))


def review_grade_score(row):
    """相容舊版 review_grades 的分數欄位名稱。"""
    for key in (
        "teacher_score",
        "grade",
        "score",
        "review_grade",
        "content_grade",
    ):
        value = row.get(key, "")
        if value != "":
            return value
    return "0"


def review_grade_feedback(row):
    """相容舊版 review_grades 的回饋欄位名稱。"""
    for key in (
        "teacher_feedback",
        "feedback",
        "review_feedback",
        "content_feedback",
    ):
        value = row.get(key, "")
        if value != "":
            return value
    return ""


def migrate_review_grades_sheet(worksheet, new_headers):
    """保留既有資料，將舊版 review_grades 欄位轉為目前欄位。"""
    current_headers = api_call(lambda: worksheet.row_values(1))
    if current_headers == new_headers:
        return

    values = api_call(worksheet.get_all_values)
    if len(values) <= 1:
        api_call(lambda: worksheet.update(range_name="A1", values=[new_headers]))
        return

    old_headers = values[0]
    aliases = {
        "teacher_score": [
            "teacher_score",
            "grade",
            "score",
            "review_grade",
            "content_grade",
        ],
        "teacher_feedback": [
            "teacher_feedback",
            "feedback",
            "review_feedback",
            "content_feedback",
        ],
    }

    migrated_rows = []
    for source_row in values[1:]:
        padded = source_row + [""] * (len(old_headers) - len(source_row))
        old_record = dict(zip(old_headers, padded))
        migrated = []
        for position, column in enumerate(new_headers):
            candidates = aliases.get(column, [column])
            value = next(
                (
                    old_record[candidate]
                    for candidate in candidates
                    if candidate in old_record
                    and old_record[candidate] != ""
                ),
                "",
            )
            # 舊版只改過欄位名稱時，仍可依原欄位位置保留資料。
            if value == "" and position < len(source_row):
                value = source_row[position]
            migrated.append(value)
        migrated_rows.append(migrated)

    api_call(
        lambda: worksheet.update(
            range_name="A1",
            values=[new_headers, *migrated_rows],
        )
    )


@st.cache_resource
def connect_store(schema_version):
    # schema_version 用來讓 Streamlit 在新增工作表後放棄舊的資源快取。
    del schema_version
    credentials = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    client = gspread.authorize(credentials)
    spreadsheet = api_call(
        lambda: client.open_by_key(st.secrets["spreadsheet_id"])
    )
    sheets = {
        sheet.title: sheet
        for sheet in api_call(spreadsheet.worksheets)
    }

    for sheet_name, headers in HEADERS.items():
        worksheet = sheets.get(sheet_name)
        if worksheet is None:
            worksheet = api_call(
                lambda name=sheet_name, columns=headers: spreadsheet.add_worksheet(
                    title=name,
                    rows=2000,
                    cols=max(len(columns), 10),
                )
            )
            api_call(
                lambda target=worksheet, columns=headers: target.update(
                    range_name="A1",
                    values=[columns],
                )
            )
        elif sheet_name == "review_grades":
            pass  # 舊欄位須先備份後人工遷移；啟動時不覆寫既有資料。
        sheets[sheet_name] = worksheet

    return spreadsheet, sheets, threading.RLock()


try:
    SPREADSHEET, SHEETS, DATA_LOCK = connect_store(STORE_SCHEMA_VERSION)
except ValueError as error:
    st.error(str(error))
    st.stop()
except gspread.exceptions.APIError as error:
    status = getattr(getattr(error, "response", None), "status_code", 0)
    if status in (401, 403):
        st.error(
            "Google 試算表授權失敗。請確認試算表已分享給 Secrets 中的服務帳號 email。"
        )
    else:
        st.error(
            "目前無法連線到 Google 試算表，可能是 API 配額暫時用完或 Google 服務忙碌。"
            "請等待約 1 分鐘後重新整理頁面。"
        )
    st.stop()


def ensure_sheet(sheet_name):
    """避免舊 cache_resource 中沒有 review_grades 而產生 KeyError。"""
    if sheet_name in SHEETS:
        return SHEETS[sheet_name]

    with DATA_LOCK:
        if sheet_name in SHEETS:
            return SHEETS[sheet_name]

        headers = HEADERS[sheet_name]
        existing = {
            sheet.title: sheet
            for sheet in api_call(SPREADSHEET.worksheets)
        }
        worksheet = existing.get(sheet_name)
        if worksheet is None:
            worksheet = api_call(
                lambda: SPREADSHEET.add_worksheet(
                    title=sheet_name,
                    rows=2000,
                    cols=max(len(headers), 10),
                )
            )
            api_call(lambda: worksheet.update(range_name="A1", values=[headers]))
        else:
            first_row = api_call(lambda: worksheet.row_values(1))
            if not first_row:
                api_call(lambda: worksheet.update(range_name="A1", values=[headers]))
            elif first_row != headers:
                raise ValueError(
                    f"Google 試算表的「{sheet_name}」第一列欄位不符合新版程式。"
                )

        SHEETS[sheet_name] = worksheet
        return worksheet


@st.cache_resource
def record_cache():
    return {}


def read_checked(sheet_name):
    worksheet = ensure_sheet(sheet_name)
    values = api_call(worksheet.get_all_values)
    headers = HEADERS[sheet_name]
    if not values or values[0][:len(headers)] != headers:
        raise ValueError(f"工作表 {sheet_name} 標題不符，請先備份並修正欄位，系統不會自動覆寫。")
    for number, row in enumerate(values, start=1):
        if any(str(v).strip() for v in row[len(headers):]):
            raise ValueError(f"工作表 {sheet_name} 第 {number} 列超出預期欄位，請先修復錯位資料。")
        if number > 1 and any(str(v).strip() for v in row) and not str(row[0]).strip():
            raise ValueError(f"工作表 {sheet_name} 第 {number} 列缺少識別值，請先修復資料。")
    return values


def records(sheet_name):
    # 快取與資料鎖使用一致的鎖順序，避免多人提交時互相等待快取鎖。
    with DATA_LOCK:
        cached = record_cache().get(sheet_name)
        if cached and time.monotonic() - cached[0] < 20:
            return [dict(row) for row in cached[1]]
        values = read_checked(sheet_name)
        headers = HEADERS[sheet_name]
        result = [dict(zip(headers, row + [""] * (len(headers) - len(row))))
                  for row in values[1:]]  # 保留空白列的位置，更新列號不偏移。
        record_cache()[sheet_name] = (time.monotonic(), result)
        return [dict(row) for row in result]


def clear_records(sheet_name=None):
    with DATA_LOCK:
        if sheet_name is None:
            record_cache().clear()
        else:
            record_cache().pop(sheet_name, None)


records.clear = clear_records


def append_record(sheet_name, data):
    # 完全不使用 append 的自動表格偵測，明確從 A 欄寫入下一列。
    # 限單一 app 執行個體，且上課期間不得手動排序、刪列或另開寫入程式。
    with DATA_LOCK:
        values = read_checked(sheet_name)
        update_record(sheet_name, len(values) + 1, data)


def update_record(sheet_name, row_number, data):
    with DATA_LOCK:
        headers = HEADERS[sheet_name]
        worksheet = ensure_sheet(sheet_name)
        last_column = gspread.utils.rowcol_to_a1(1, len(headers)).rstrip("1")
        if row_number < 2:
            raise ValueError("拒絕覆寫標題列。")
        if row_number > worksheet.row_count:
            api_call(lambda: worksheet.resize(rows=max(row_number, worksheet.row_count + 1000)))
        payload = [[str(data.get(column, "")) for column in headers]]
        try:
            api_call(lambda: worksheet.update(
                range_name=f"A{row_number}:{last_column}{row_number}",
                values=payload, value_input_option="RAW"))
        finally:
            records.clear(sheet_name)


def now_text():
    return datetime.now(TZ).isoformat(timespec="seconds")


def format_timestamp(timestamp):
    return datetime.fromtimestamp(float(timestamp), TZ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        300_000,
    ).hex()
    return f"{salt}${digest}"


def password_matches(password, stored_hash):
    try:
        salt, _ = stored_hash.split("$", 1)
        return hmac.compare_digest(
            password_hash(password, salt), stored_hash
        )
    except (ValueError, TypeError):
        return False


def roster_map():
    return {
        row["student_id"].strip(): row["name"].strip()
        for row in records("roster")
        if row["student_id"].strip() and row["name"].strip()
    }


def schedule_rows():
    roster = roster_map()
    output = []
    for row in records("schedule"):
        try:
            date = row["date"].strip()
            datetime.strptime(date, "%Y-%m-%d")
            order = int(row["order"])
            student_id = row["student_id"].strip()
        except (ValueError, TypeError, KeyError):
            continue
        if student_id not in roster:
            continue
        output.append(
            {
                "date": date,
                "order": order,
                "student_id": student_id,
                "name": roster[student_id],
            }
        )
    return sorted(output, key=lambda row: (row["date"], row["order"]))


def recommended_date(dates):
    today = datetime.now(TZ).date().isoformat()
    arrived = [date for date in dates if date <= today]
    return max(arrived) if arrived else min(dates)


def session_is_active(session):
    if not session:
        return False
    try:
        return (
            session["status"] == "OPEN"
            and time.time() < float(session["ends_at"])
        )
    except (KeyError, ValueError, TypeError):
        return False


def active_session():
    active = [row for row in records("sessions") if session_is_active(row)]
    if not active:
        return None
    return max(active, key=lambda row: float(row["started_at"]))


def session_finished(session):
    return not session_is_active(session)


def start_session(date, presenter_id):
    with DATA_LOCK:
        if active_session():
            raise ValueError("目前已有一位報告者正在互評，請先等待結束。")

        presenter = next(
            (
                row
                for row in schedule_rows()
                if row["date"] == date and row["student_id"] == presenter_id
            ),
            None,
        )
        if presenter is None:
            raise ValueError("找不到這位報告者的排程。")

        started_at = time.time()
        session_id = str(uuid.uuid4())
        append_record(
            "sessions",
            {
                "session_id": session_id,
                "date": date,
                "presenter_order": presenter["order"],
                "presenter_id": presenter["student_id"],
                "presenter_name": presenter["name"],
                "started_at": started_at,
                "ends_at": started_at + REVIEW_SECONDS,
                "status": "OPEN",
            },
        )
        return session_id


def close_session(session_id):
    with DATA_LOCK:
        for row_number, session in enumerate(records("sessions"), start=2):
            if session["session_id"] == session_id:
                session["status"] = "CLOSED"
                update_record("sessions", row_number, session)
                return
    raise ValueError("找不到互評場次。")


def extend_session(session_id, extra_minutes):
    extra_minutes = int(extra_minutes)
    if extra_minutes < 1:
        raise ValueError("延長時間至少需要 1 分鐘。")

    with DATA_LOCK:
        for row_number, session in enumerate(records("sessions"), start=2):
            if session["session_id"] != session_id:
                continue
            if not session_is_active(session):
                raise ValueError("這個互評場次已結束，無法延長。")
            session["ends_at"] = str(
                float(session["ends_at"]) + extra_minutes * 60
            )
            update_record("sessions", row_number, session)
            return
    raise ValueError("找不到互評場次。")


def login_student(student_id, password):
    student_id = student_id.strip()
    user = next(
        (row for row in records("users") if row["student_id"] == student_id),
        None,
    )
    if user is None or not password_matches(password, user["password_hash"]):
        raise ValueError("學號或密碼錯誤。")
    return {"student_id": user["student_id"], "name": user["name"]}


def change_student_password(current_password, new_password, confirmation):
    if new_password != confirmation:
        raise ValueError("兩次輸入的新密碼不一致。")
    if len(new_password) < 4:
        raise ValueError("新密碼至少需要 4 個字元。")
    if current_password == new_password:
        raise ValueError("新密碼不可與目前密碼相同。")

    student_id = st.session_state["user"]["student_id"]
    with DATA_LOCK:
        records.clear("users")
        for row_number, user in enumerate(records("users"), start=2):
            if user["student_id"] != student_id:
                continue
            if not password_matches(current_password, user["password_hash"]):
                raise ValueError("目前密碼錯誤。")
            user["password_hash"] = password_hash(new_password)
            update_record("users", row_number, user)
            return
    raise ValueError("找不到這個學生帳號。")


def student_authentication():
    with st.form("student_login"):
        student_id = st.text_input("學號")
        password = st.text_input("密碼", type="password")
        submitted = st.form_submit_button("登入", type="primary")
    if submitted:
        try:
            st.session_state["user"] = login_student(student_id, password)
            st.session_state["role"] = "student"
            st.rerun()
        except ValueError as error:
            st.error(str(error))


@st.cache_resource
def submission_queue():
    # 僅供同一執行個體共用。資料鎖與佇列鎖不交叉持有。
    return {"lock": threading.RLock(), "items": [], "active": None}


def queue_snapshot(session_id=None):
    queue = submission_queue()
    with queue["lock"]:
        items = [item for item in queue["items"]
                 if session_id is None or item["session_id"] == session_id]
        active = queue["active"]
        return {
            "waiting": sum(item["ticket"] != active for item in items),
            "processing": int(any(item["ticket"] == active for item in items)),
        }


def submit_review_queued(session_id, comment):
    comment = comment.strip()
    if not comment:
        raise ValueError("請輸入評語。")
    queue = submission_queue()
    ticket = uuid.uuid4().hex
    student_id = st.session_state["user"]["student_id"]
    submitted_at = time.time()
    with queue["lock"]:
        if any(item["session_id"] == session_id and item["student_id"] == student_id
               for item in queue["items"]):
            raise ValueError("你的上一筆提交仍在處理，請等待原頁面顯示結果。")
        queue["items"].append({"ticket": ticket, "session_id": session_id,
                               "student_id": student_id})
    message = st.empty()
    try:
        while True:
            with queue["lock"]:
                index = next(i for i, item in enumerate(queue["items"])
                             if item["ticket"] == ticket)
                if index == 0 and queue["active"] is None:
                    queue["active"] = ticket
                    ready = True
                else:
                    ready = False
            if ready:
                break
            message.info(f"⏳ 等待儲存中，你前面還有 {index} 人。請保持頁面開啟。")
            time.sleep(0.5)
        message.info("💾 正在處理你的提交，確認寫入成功後會顯示結果。")
        result = submit_review(session_id, comment, submitted_at=submitted_at)
        message.success("✅ 儲存成功。")
        return result
    finally:
        # 含錯誤與 Streamlit 中斷，均釋放順位，避免下一位永遠等候。
        with queue["lock"]:
            queue["items"][:] = [item for item in queue["items"] if item["ticket"] != ticket]
            if queue["active"] == ticket:
                queue["active"] = None


def submit_review(session_id, comment, submitted_at=None):
    submitted_at = time.time() if submitted_at is None else submitted_at
    reviewer = st.session_state["user"]
    comment = comment.strip()
    if not comment:
        raise ValueError("請輸入評語。")

    with DATA_LOCK:
        records.clear("reviews")
        records.clear("sessions")
        # 以點擊送出時間驗證，避免排隊期間超時。
        session = next(
            (
                row
                for row in records("sessions")
                if row["session_id"] == session_id
            ),
            None,
        )
        if not session or session.get("status") != "OPEN" or submitted_at >= float(session["ends_at"]):
            raise ValueError("互評尚未開始或時間已結束。")
        if reviewer["student_id"] == session["presenter_id"]:
            raise ValueError("報告者不能評價自己。")
        data = {
            "session_id": session_id,
            "date": session["date"],
            "reviewer_id": reviewer["student_id"],
            "reviewer_name": reviewer["name"],
            "presenter_id": session["presenter_id"],
            "presenter_name": session["presenter_name"],
            "score": "",
            "comment": comment,
            "submitted_at": now_text(),
        }
        for row_number, row in enumerate(records("reviews"), start=2):
            if (
                row["session_id"] == session_id
                and row["reviewer_id"] == reviewer["student_id"]
            ):
                update_record("reviews", row_number, data)
                return "updated"

        append_record("reviews", data)
        return "created"


@st.fragment(run_every="5s")
def student_review_panel():
    session = active_session()
    if not session:
        st.info("目前沒有開放中的互評，請等待老師開始。")
        return

    remaining = max(0, math.ceil(float(session["ends_at"]) - time.time()))
    st.subheader(f"目前報告者：{session['presenter_name']}")
    st.caption(
        f"報告日期：{session['date']}｜當週第 {session['presenter_order']} 位"
    )
    st.warning(f"剩餘時間：{remaining // 60:02d}:{remaining % 60:02d}")

    user = st.session_state["user"]
    if user["student_id"] == session["presenter_id"]:
        st.info("你是本場報告者，不需要填寫自己的互評。")
        return

    existing_review = next(
        (
            row
            for row in records("reviews")
            if row["session_id"] == session["session_id"]
            and row["reviewer_id"] == user["student_id"]
        ),
        None,
    )

    if existing_review:
        st.success("本場互評已提交；倒數結束前仍可修改並重新儲存。")

    with st.form(f"review_{session['session_id']}"):
        comment = st.text_area(
            "評語",
            value=existing_review["comment"] if existing_review else "",
            placeholder="請填寫優點、改善建議或想提問的內容。",
            max_chars=1000,
        )
        send = st.form_submit_button(
            "更新互評" if existing_review else "提交互評",
            type="primary",
        )
    if send:
        try:
            result = submit_review_queued(session["session_id"], comment)
            st.success("互評已更新。" if result == "updated" else "互評已提交。")
            st.rerun()
        except ValueError as error:
            st.error(str(error))


def csv_safe(value):
    text = str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def show_export(data, filename, key):
    if not data:
        st.info("目前沒有資料。")
        return
    dataframe = pd.DataFrame(data)
    st.dataframe(dataframe, hide_index=True, use_container_width=True)
    safe_dataframe = dataframe.map(csv_safe)
    st.download_button(
        "下載 CSV",
        safe_dataframe.to_csv(index=False).encode("utf-8-sig"),
        filename,
        "text/csv",
        key=key,
    )


def student_results():
    student_id = st.session_state["user"]["student_id"]
    sessions = {row["session_id"]: row for row in records("sessions")}
    result = []
    for review in records("reviews"):
        session = sessions.get(review["session_id"])
        if review["presenter_id"] == student_id and session_finished(session):
            result.append(
                {
                    "報告日期": review["date"],
                    "評價者學號": review["reviewer_id"],
                    "評價者姓名": review["reviewer_name"],
                    "評語": review["comment"],
                    "提交時間": review["submitted_at"],
                }
            )
    st.subheader("我收到的互評")
    show_export(result, "我的互評結果.csv", "my_reviews")

    st.subheader("老師對報告的評分")
    grade_rows = [
        {
            "報告日期": row["date"],
            "老師分數": row["grade"],
            "老師回饋": row["feedback"],
            "更新時間": row["updated_at"],
        }
        for row in records("grades")
        if row["presenter_id"] == student_id
    ]
    show_export(grade_rows, "我的老師評分.csv", "my_grades")

    st.subheader("老師對我填寫之互評的評分")
    review_grade_rows = [
        {
            "報告日期": row["date"],
            "報告者姓名": row["presenter_name"],
            "老師分數": review_grade_score(row),
            "老師回饋": review_grade_feedback(row),
            "更新時間": row["updated_at"],
        }
        for row in records("review_grades")
        if row["reviewer_id"] == student_id
    ]
    show_export(
        review_grade_rows,
        "我的互評內容評分.csv",
        "my_review_grades",
    )


def student_review_history():
    student_id = st.session_state["user"]["student_id"]
    history = [
        {
            "報告日期": row["date"],
            "報告者學號": row["presenter_id"],
            "報告者姓名": row["presenter_name"],
            "我填寫的互評內容": row["comment"],
            "提交／更新時間": row["submitted_at"],
        }
        for row in records("reviews")
        if row["reviewer_id"] == student_id
    ]
    history.sort(key=lambda row: row["提交／更新時間"], reverse=True)
    if history:
        st.dataframe(pd.DataFrame(history), hide_index=True, use_container_width=True)
    else:
        st.info("目前沒有過往互評紀錄。")


def student_change_password():
    with st.form("student_change_password"):
        current_password = st.text_input("目前密碼", type="password")
        new_password = st.text_input("新密碼", type="password")
        confirmation = st.text_input("再次輸入新密碼", type="password")
        submitted = st.form_submit_button("更改密碼", type="primary")
    if submitted:
        try:
            change_student_password(
                current_password,
                new_password,
                confirmation,
            )
            st.success("密碼已更改，下次登入請使用新密碼。")
        except ValueError as error:
            st.error(str(error))


def admin_login():
    with st.form("admin_login"):
        password = st.text_input("老師密碼", type="password")
        submitted = st.form_submit_button("登入", type="primary")
    if submitted:
        if hmac.compare_digest(password, str(st.secrets["admin_password"])):
            st.session_state["role"] = "admin"
            st.rerun()
        else:
            st.error("老師密碼錯誤。")


@st.fragment(run_every="2s")
def admin_timer():
    stats = queue_snapshot()
    st.info(f"等待處理：{stats['waiting']} 人｜正在處理：{stats['processing']} 人")
    # 提交期間只讀記憶體快照，不等待資料鎖或新增 Google API 請求。
    cached_sessions = record_cache().get("sessions")
    cached_reviews = record_cache().get("reviews")
    if DATA_LOCK.acquire(blocking=False):
        try:
            if not cached_sessions or time.monotonic() - cached_sessions[0] >= 20:
                records("sessions")
            if not cached_reviews or time.monotonic() - cached_reviews[0] >= 20:
                records("reviews")
            cached_sessions = record_cache().get("sessions")
            cached_reviews = record_cache().get("reviews")
            st.session_state["admin_timer_sessions"] = cached_sessions
            st.session_state["admin_timer_reviews"] = cached_reviews
        except ValueError as error:
            st.warning(str(error))
        finally:
            DATA_LOCK.release()
    cached_sessions = cached_sessions or st.session_state.get("admin_timer_sessions")
    cached_reviews = cached_reviews or st.session_state.get("admin_timer_reviews")
    sessions = cached_sessions[1] if cached_sessions else []
    active = [row for row in sessions if session_is_active(row)]
    if not active:
        st.caption("目前沒有進行中的互評；排隊數包含所有場次。")
        return
    session = max(active, key=lambda row: float(row["started_at"]))
    remaining = max(0, math.ceil(float(session["ends_at"]) - time.time()))
    reviews = cached_reviews[1] if cached_reviews else []
    count = len({row["reviewer_id"] for row in reviews
                 if row.get("session_id") == session["session_id"]})
    st.progress(min(1.0, remaining / max(1.0, float(session["ends_at"]) - float(session["started_at"]))),
                text=f"{session['presenter_name']}｜剩餘 {remaining // 60:02d}:{remaining % 60:02d}")
    st.caption(f"已儲存：{count} 人（最近一次查詢結果，約每 20 秒更新；忙碌時延後）")


def admin_session_control():
    st.header("場次控制")
    st.write("每次只開放一位報告者，預設時間為 10 分鐘。")
    admin_timer()

    current = active_session()
    if current:
        st.subheader("延長互評時間")
        extra_minutes = st.number_input(
            "延長幾分鐘",
            min_value=1,
            max_value=60,
            value=5,
            step=1,
        )
        if st.button("確認延長時間", type="primary"):
            try:
                extend_session(current["session_id"], extra_minutes)
                st.success(f"已延長 {int(extra_minutes)} 分鐘。")
                st.rerun()
            except ValueError as error:
                st.error(str(error))

        st.divider()
        confirm = st.checkbox("確認提前結束目前互評")
        if st.button("提前結束", disabled=not confirm):
            close_session(current["session_id"])
            st.rerun()
        return

    schedule = schedule_rows()
    dates = sorted({row["date"] for row in schedule})
    if not dates:
        st.info("請先在 Google 試算表填入 roster 與 schedule。")
        return

    selected_date = st.selectbox(
        "報告日期",
        dates,
        index=dates.index(recommended_date(dates)),
    )
    presenters = [row for row in schedule if row["date"] == selected_date]
    completed_counts = {}
    for session in records("sessions"):
        key = (session["date"], session["presenter_id"])
        completed_counts[key] = completed_counts.get(key, 0) + 1

    presenter_ids = [row["student_id"] for row in presenters]
    labels = {
        row["student_id"]: (
            f"第 {row['order']} 位｜{row['name']}（{row['student_id']}）｜"
            f"已開 {completed_counts.get((selected_date, row['student_id']), 0)} 場"
        )
        for row in presenters
    }
    selected_presenter = st.selectbox(
        "選擇本次報告者",
        presenter_ids,
        format_func=lambda student_id: labels[student_id],
    )
    if st.button("開始這位報告者的互評", type="primary"):
        try:
            start_session(selected_date, selected_presenter)
            st.rerun()
        except ValueError as error:
            st.error(str(error))


def session_label(session):
    return (
        f"{session['date']}｜第 {session['presenter_order']} 位｜"
        f"{session['presenter_name']}｜{format_timestamp(session['started_at'])}"
    )


def session_selector(key):
    sessions = sorted(
        records("sessions"),
        key=lambda row: float(row["started_at"]),
        reverse=True,
    )
    if not sessions:
        st.info("尚未建立互評場次。")
        return None
    session_map = {row["session_id"]: row for row in sessions}
    session_id = st.selectbox(
        "選擇場次",
        list(session_map),
        format_func=lambda value: session_label(session_map[value]),
        key=key,
    )
    return session_map[session_id]


def admin_review_records():
    st.header("互評紀錄")
    selected = session_selector("review_session")
    if selected is None:
        return
    rows = [
        {
            "報告日期": row["date"],
            "報告者學號": row["presenter_id"],
            "報告者姓名": row["presenter_name"],
            "評價者學號": row["reviewer_id"],
            "評價者姓名": row["reviewer_name"],
            "評語": row["comment"],
            "提交時間": row["submitted_at"],
        }
        for row in records("reviews")
        if row["session_id"] == selected["session_id"]
    ]
    show_export(rows, "本場互評紀錄.csv", "one_session_reviews")

    st.subheader("全學期互評")
    all_rows = [
        {
            "報告日期": row["date"],
            "報告者學號": row["presenter_id"],
            "報告者姓名": row["presenter_name"],
            "評價者學號": row["reviewer_id"],
            "評價者姓名": row["reviewer_name"],
            "評語": row["comment"],
            "提交時間": row["submitted_at"],
        }
        for row in records("reviews")
    ]
    show_export(all_rows, "全學期互評紀錄.csv", "all_reviews")


def save_grade(session, grade, feedback):
    data = {
        "session_id": session["session_id"],
        "date": session["date"],
        "presenter_id": session["presenter_id"],
        "presenter_name": session["presenter_name"],
        "grade": grade,
        "feedback": feedback.strip(),
        "updated_at": now_text(),
    }
    with DATA_LOCK:
        for row_number, row in enumerate(records("grades"), start=2):
            if row["session_id"] == session["session_id"]:
                update_record("grades", row_number, data)
                return
        append_record("grades", data)


def admin_grading():
    st.header("報告者評分")
    selected = session_selector("grading_session")
    if selected is None:
        return
    existing = next(
        (
            row
            for row in records("grades")
            if row["session_id"] == selected["session_id"]
        ),
        None,
    )
    with st.form("grade_form"):
        grade = st.number_input(
            "老師給報告的分數",
            min_value=0.0,
            max_value=100.0,
            value=float(existing["grade"]) if existing else 0.0,
            step=1.0,
        )
        feedback = st.text_area(
            "老師給報告的回饋",
            value=existing["feedback"] if existing else "",
            max_chars=1000,
        )
        submitted = st.form_submit_button("儲存或更新評分", type="primary")
    if submitted:
        save_grade(selected, grade, feedback)
        st.success("評分已儲存。")
        st.rerun()

    grade_rows = [
        {
            "報告日期": row["date"],
            "報告者學號": row["presenter_id"],
            "報告者姓名": row["presenter_name"],
            "老師分數": row["grade"],
            "老師回饋": row["feedback"],
            "更新時間": row["updated_at"],
        }
        for row in records("grades")
    ]
    st.subheader("全部老師評分")
    show_export(grade_rows, "全學期老師評分.csv", "all_grades")


def save_review_grade(review, teacher_score, teacher_feedback):
    data = {
        "session_id": review["session_id"],
        "date": review["date"],
        "presenter_id": review["presenter_id"],
        "presenter_name": review["presenter_name"],
        "reviewer_id": review["reviewer_id"],
        "reviewer_name": review["reviewer_name"],
        "teacher_score": teacher_score,
        "teacher_feedback": teacher_feedback.strip(),
        "updated_at": now_text(),
    }
    with DATA_LOCK:
        for row_number, row in enumerate(records("review_grades"), start=2):
            if (
                row["session_id"] == review["session_id"]
                and row["reviewer_id"] == review["reviewer_id"]
            ):
                update_record("review_grades", row_number, data)
                return
        append_record("review_grades", data)


def admin_review_grading():
    st.header("互評內容評分")
    selected_session = session_selector("review_grading_session")
    if selected_session is None:
        return

    session_reviews = [
        row
        for row in records("reviews")
        if row["session_id"] == selected_session["session_id"]
    ]
    if not session_reviews:
        st.info("這個場次目前沒有互評資料。")
        return

    review_map = {row["reviewer_id"]: row for row in session_reviews}
    reviewer_id = st.selectbox(
        "選擇互評者",
        list(review_map),
        format_func=lambda value: (
            f"{review_map[value]['reviewer_name']}｜{value}"
        ),
    )
    selected_review = review_map[reviewer_id]

    st.write(
        f"報告者：{selected_review['presenter_name']}｜"
        f"{selected_review['presenter_id']}"
    )
    st.write(
        f"互評者：{selected_review['reviewer_name']}｜"
        f"{selected_review['reviewer_id']}"
    )
    st.write(f"學生給報告者的分數：{selected_review['score']} 分")
    st.text_area(
        "學生填寫的互評內容",
        value=selected_review["comment"],
        height=130,
        disabled=True,
        key=f"student_review_{selected_review['session_id']}_{reviewer_id}",
    )

    existing = next(
        (
            row
            for row in records("review_grades")
            if row["session_id"] == selected_review["session_id"]
            and row["reviewer_id"] == reviewer_id
        ),
        None,
    )
    form_key = f"review_grade_{selected_review['session_id']}_{reviewer_id}"
    with st.form(form_key):
        teacher_score = st.number_input(
            "老師給互評內容的分數",
            min_value=0.0,
            max_value=100.0,
            value=float(review_grade_score(existing)) if existing else 0.0,
            step=1.0,
        )
        teacher_feedback = st.text_area(
            "老師給互評內容的回饋",
            value=review_grade_feedback(existing) if existing else "",
            max_chars=1000,
        )
        submitted = st.form_submit_button(
            "儲存或更新互評評分",
            type="primary",
        )
    if submitted:
        save_review_grade(selected_review, teacher_score, teacher_feedback)
        st.success("互評內容評分已儲存。")
        st.rerun()

    all_review_grades = [
        {
            "報告日期": row["date"],
            "報告者學號": row["presenter_id"],
            "報告者姓名": row["presenter_name"],
            "互評者學號": row["reviewer_id"],
            "互評者姓名": row["reviewer_name"],
            "老師分數": review_grade_score(row),
            "老師回饋": review_grade_feedback(row),
            "更新時間": row["updated_at"],
        }
        for row in records("review_grades")
    ]
    st.subheader("全部互評內容評分")
    show_export(
        all_review_grades,
        "全學期互評內容評分.csv",
        "all_review_grades",
    )


def admin_roster_schedule():
    st.header("名單與排程")
    st.subheader("學生名單")
    roster_rows = [
        {"學號": student_id, "姓名": name}
        for student_id, name in roster_map().items()
    ]
    show_export(roster_rows, "學生名單.csv", "roster_export")

    st.subheader("報告順序")
    schedule_data = [
        {
            "報告日期": row["date"],
            "當週順序": row["order"],
            "學號": row["student_id"],
            "姓名": row["name"],
        }
        for row in schedule_rows()
    ]
    show_export(schedule_data, "報告順序.csv", "schedule_export")


def admin_page():
    st.title("老師互評管理系統")
    page = st.sidebar.radio(
        "老師功能",
        [
            "場次控制",
            "互評紀錄",
            "報告者評分",
            "互評內容評分",
            "名單與排程",
        ],
    )
    if page == "場次控制":
        admin_session_control()
    elif page == "互評紀錄":
        admin_review_records()
    elif page == "報告者評分":
        admin_grading()
    elif page == "互評內容評分":
        admin_review_grading()
    else:
        admin_roster_schedule()


def student_page():
    user = st.session_state["user"]
    st.sidebar.write(f"{user['name']}｜{user['student_id']}")
    page = st.sidebar.radio(
        "學生功能",
        [
            "進行互評",
            "我的互評結果",
            "我過往填寫的互評",
            "報告順序",
            "更改密碼",
        ],
    )
    if page == "進行互評":
        st.title("進行互評")
        student_review_panel()
    elif page == "我的互評結果":
        st.title("我的互評結果")
        student_results()
    elif page == "我過往填寫的互評":
        st.title("我過往填寫的互評")
        student_review_history()
    elif page == "報告順序":
        st.title("報告順序")
        display = [
            {
                "報告日期": row["date"],
                "當週順序": row["order"],
                "學號": row["student_id"],
                "姓名": row["name"],
            }
            for row in schedule_rows()
        ]
        if display:
            st.dataframe(pd.DataFrame(display), hide_index=True, use_container_width=True)
        else:
            st.info("尚未設定報告順序。")
    else:
        st.title("更改密碼")
        student_change_password()


def main():
    role = st.session_state.get("role")
    teacher_route = st.query_params.get("page") in ("teacher", "admin")

    if role and st.sidebar.button("登出"):
        st.session_state.clear()
        st.rerun()

    if teacher_route:
        if role == "admin":
            admin_page()
        else:
            st.title("老師登入")
            admin_login()
        return

    if role == "admin":
        admin_page()
    elif role == "student":
        student_page()
    else:
        st.title("課堂報告互評系統")
        st.write("第一次使用請先註冊，之後以學號與自設密碼登入。")
        student_authentication()


main()
