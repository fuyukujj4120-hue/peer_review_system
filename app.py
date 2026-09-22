import hashlib
import hmac
import math
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


# =========================================================
# 基本設定
# =========================================================

st.set_page_config(
    page_title="課堂報告互評系統",
    page_icon="📝",
    layout="wide",
)

TAIWAN_TIMEZONE = ZoneInfo("Asia/Taipei")
REVIEW_DURATION_SECONDS = 10 * 60


# =========================================================
# Google 試算表欄位
# 程式第一次執行時會自動建立這些工作表
# =========================================================

SHEET_HEADERS = {
    "roster": [
        "student_id",
        "name",
    ],
    "schedule": [
        "date",
        "order",
        "student_id",
    ],
    "users": [
        "student_id",
        "name",
        "password_hash",
        "created_at",
    ],
    "sessions": [
        "session_id",
        "date",
        "started_at",
        "ends_at",
        "status",
    ],
    "session_presenters": [
        "session_id",
        "order",
        "student_id",
        "name",
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
}


# =========================================================
# Google 試算表連線
# =========================================================

@st.cache_resource
def connect_google_sheets():
    credentials = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )

    client = gspread.authorize(credentials)

    spreadsheet = client.open_by_key(
        st.secrets["spreadsheet_id"]
    )

    worksheets = {}

    for sheet_name, headers in SHEET_HEADERS.items():
        try:
            worksheet = spreadsheet.worksheet(sheet_name)
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(
                title=sheet_name,
                rows=2000,
                cols=max(len(headers), 10),
            )
            worksheet.update(
                range_name="A1",
                values=[headers],
            )

        first_row = worksheet.row_values(1)

        if not first_row:
            worksheet.update(
                range_name="A1",
                values=[headers],
            )
        elif first_row != headers:
            raise ValueError(
                f"工作表 {sheet_name} 的第一列欄位不正確。\n"
                f"正確欄位應為：{headers}"
            )

        worksheets[sheet_name] = worksheet

    return worksheets, threading.RLock()


WORKSHEETS, DATA_LOCK = connect_google_sheets()


# =========================================================
# 資料讀寫功能
# =========================================================

def read_records(sheet_name):
    """讀取指定工作表，所有內容均視為文字。"""
    values = WORKSHEETS[sheet_name].get_all_values()

    if len(values) <= 1:
        return []

    headers = values[0]
    records = []

    for row in values[1:]:
        padded_row = row + [""] * (len(headers) - len(row))
        records.append(dict(zip(headers, padded_row)))

    return records


def append_record(sheet_name, record):
    headers = SHEET_HEADERS[sheet_name]

    WORKSHEETS[sheet_name].append_row(
        [str(record.get(header, "")) for header in headers],
        value_input_option="RAW",
    )


def update_record(sheet_name, row_number, record):
    headers = SHEET_HEADERS[sheet_name]

    WORKSHEETS[sheet_name].update(
        range_name=f"A{row_number}",
        values=[
            [str(record.get(header, "")) for header in headers]
        ],
    )


def taiwan_now_text():
    return datetime.now(TAIWAN_TIMEZONE).isoformat(
        timespec="seconds"
    )


def format_time(timestamp):
    return datetime.fromtimestamp(
        float(timestamp),
        TAIWAN_TIMEZONE,
    ).strftime("%Y-%m-%d %H:%M:%S")


# =========================================================
# 密碼功能
# =========================================================

def create_password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)

    password_digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        300_000,
    ).hex()

    return f"{salt}${password_digest}"


def verify_password(password, saved_hash):
    try:
        salt, _ = saved_hash.split("$", 1)

        calculated_hash = create_password_hash(
            password,
            salt,
        )

        return hmac.compare_digest(
            calculated_hash,
            saved_hash,
        )
    except (ValueError, TypeError):
        return False


# =========================================================
# 名單與報告順序
# =========================================================

def roster_dictionary():
    return {
        row["student_id"].strip(): row["name"].strip()
        for row in read_records("roster")
        if row["student_id"].strip()
    }


def all_schedule_rows():
    roster = roster_dictionary()
    result = []

    for row in read_records("schedule"):
        student_id = row["student_id"].strip()
        date = row["date"].strip()

        try:
            order = int(row["order"])
            datetime.strptime(date, "%Y-%m-%d")
        except (ValueError, TypeError):
            continue

        if student_id not in roster:
            continue

        result.append({
            "date": date,
            "order": order,
            "student_id": student_id,
            "name": roster[student_id],
        })

    return sorted(
        result,
        key=lambda row: (
            row["date"],
            row["order"],
        ),
    )


def schedule_for_date(selected_date):
    return [
        row
        for row in all_schedule_rows()
        if row["date"] == selected_date
    ]


def recommended_schedule_date(dates):
    """
    自動選擇最新報告日期：
    1. 優先選擇今天或今天以前最新的日期
    2. 若第一週尚未開始，選擇最早日期
    """
    today = datetime.now(
        TAIWAN_TIMEZONE
    ).date().isoformat()

    previous_dates = [
        date for date in dates if date <= today
    ]

    if previous_dates:
        return max(previous_dates)

    return min(dates)


# =========================================================
# 互評場次
# =========================================================

def is_session_active(session):
    if not session:
        return False

    try:
        return (
            session["status"] == "OPEN"
            and time.time() < float(session["ends_at"])
        )
    except (ValueError, KeyError):
        return False


def get_active_session():
    active_sessions = [
        session
        for session in read_records("sessions")
        if is_session_active(session)
    ]

    if not active_sessions:
        return None

    return max(
        active_sessions,
        key=lambda session: float(session["started_at"]),
    )


def session_presenters(session_id):
    rows = [
        row
        for row in read_records("session_presenters")
        if row["session_id"] == session_id
    ]

    return sorted(
        rows,
        key=lambda row: int(row["order"]),
    )


def start_review_session(selected_date):
    with DATA_LOCK:
        if get_active_session():
            raise ValueError("目前已有進行中的互評。")

        presenters = schedule_for_date(selected_date)

        if not presenters:
            raise ValueError(
                "這個日期沒有有效的報告者。"
            )

        student_ids = [
            row["student_id"] for row in presenters
        ]

        if len(student_ids) != len(set(student_ids)):
            raise ValueError(
                "同一天的報告者學號不可重複。"
            )

        session_id = str(uuid.uuid4())
        started_at = time.time()
        ends_at = started_at + REVIEW_DURATION_SECONDS

        append_record("sessions", {
            "session_id": session_id,
            "date": selected_date,
            "started_at": started_at,
            "ends_at": ends_at,
            "status": "OPEN",
        })

        for presenter in presenters:
            append_record("session_presenters", {
                "session_id": session_id,
                "order": presenter["order"],
                "student_id": presenter["student_id"],
                "name": presenter["name"],
            })

        return session_id


def close_review_session(session_id):
    with DATA_LOCK:
        sessions = read_records("sessions")

        for row_number, session in enumerate(
            sessions,
            start=2,
        ):
            if session["session_id"] == session_id:
                session["status"] = "CLOSED"

                update_record(
                    "sessions",
                    row_number,
                    session,
                )
                return

        raise ValueError("找不到這個互評場次。")


# =========================================================
# 學生註冊與登入
# =========================================================

def register_student(student_id, password, confirm_password):
    student_id = student_id.strip()

    if not student_id:
        raise ValueError("請輸入學號。")

    if password != confirm_password:
        raise ValueError("兩次輸入的密碼不一致。")

    if len(password) < 4:
        raise ValueError("密碼至少需要 4 個字元。")

    roster = roster_dictionary()

    if student_id not in roster:
        raise ValueError(
            "這個學號不在學生名單中。"
        )

    with DATA_LOCK:
        users = read_records("users")

        if any(
            row["student_id"] == student_id
            for row in users
        ):
            raise ValueError(
                "這個學號已註冊，請直接登入。"
            )

        append_record("users", {
            "student_id": student_id,
            "name": roster[student_id],
            "password_hash": create_password_hash(password),
            "created_at": taiwan_now_text(),
        })


def login_student(student_id, password):
    student_id = student_id.strip()

    users = read_records("users")

    user = next(
        (
            row
            for row in users
            if row["student_id"] == student_id
        ),
        None,
    )

    if not user:
        raise ValueError("找不到帳號，請先註冊。")

    if not verify_password(
        password,
        user["password_hash"],
    ):
        raise ValueError("學號或密碼錯誤。")

    return {
        "student_id": user["student_id"],
        "name": user["name"],
    }


def student_login_page():
    login_tab, register_tab = st.tabs([
        "學生登入",
        "首次註冊",
    ])

    with login_tab:
        with st.form("student_login_form"):
            student_id = st.text_input("學號")
            password = st.text_input(
                "密碼",
                type="password",
            )

            login_button = st.form_submit_button(
                "登入",
                type="primary",
            )

        if login_button:
            try:
                user = login_student(
                    student_id,
                    password,
                )

                st.session_state["role"] = "student"
                st.session_state["user"] = user
                st.rerun()

            except ValueError as error:
                st.error(str(error))

    with register_tab:
        st.write(
            "第一次使用時，請輸入名單中的學號並自行設定密碼。"
        )

        with st.form("student_register_form"):
            student_id = st.text_input(
                "學號",
                key="register_student_id",
            )

            password = st.text_input(
                "自設密碼",
                type="password",
                key="register_password",
            )

            confirm_password = st.text_input(
                "再次輸入密碼",
                type="password",
                key="register_confirm_password",
            )

            register_button = st.form_submit_button(
                "完成註冊",
                type="primary",
            )

        if register_button:
            try:
                register_student(
                    student_id,
                    password,
                    confirm_password,
                )

                st.success(
                    "註冊完成，請切換到「學生登入」。"
                )

            except ValueError as error:
                st.error(str(error))


# =========================================================
# 學生提交互評
# =========================================================

def submit_review(
    session_id,
    presenter_id,
    score,
    comment,
):
    if st.session_state.get("role") != "student":
        raise ValueError("請先登入。")

    reviewer = st.session_state["user"]
    reviewer_id = reviewer["student_id"]
    reviewer_name = reviewer["name"]

    comment = comment.strip()

    if not comment:
        raise ValueError("請輸入評語。")

    if len(comment) > 1000:
        raise ValueError("評語不可超過 1000 字。")

    with DATA_LOCK:
        session = next(
            (
                row
                for row in read_records("sessions")
                if row["session_id"] == session_id
            ),
            None,
        )

        if not is_session_active(session):
            raise ValueError(
                "互評尚未開始或已經結束。"
            )

        presenter = next(
            (
                row
                for row in session_presenters(session_id)
                if row["student_id"] == presenter_id
            ),
            None,
        )

        if not presenter:
            raise ValueError(
                "找不到這位報告者。"
            )

        if reviewer_id == presenter_id:
            raise ValueError("不能評價自己。")

        existing_reviews = read_records("reviews")

        already_submitted = any(
            row["session_id"] == session_id
            and row["reviewer_id"] == reviewer_id
            and row["presenter_id"] == presenter_id
            for row in existing_reviews
        )

        if already_submitted:
            raise ValueError(
                "你已經評價過這位報告者。"
            )

        if not is_session_active(session):
            raise ValueError(
                "互評時間已經結束。"
            )

        append_record("reviews", {
            "session_id": session_id,
            "date": session["date"],
            "reviewer_id": reviewer_id,
            "reviewer_name": reviewer_name,
            "presenter_id": presenter_id,
            "presenter_name": presenter["name"],
            "score": score,
            "comment": comment,
            "submitted_at": taiwan_now_text(),
        })


@st.fragment(run_every="5s")
def student_review_panel():
    session = get_active_session()

    if not session:
        st.info(
            "目前尚未開放互評，請等待管理員按下「開始互評」。"
        )
        return

    remaining_seconds = max(
        0,
        math.ceil(
            float(session["ends_at"]) - time.time()
        ),
    )

    st.subheader(
        f"{session['date']} 報告互評"
    )

    st.warning(
        f"剩餘時間："
        f"{remaining_seconds // 60:02d}:"
        f"{remaining_seconds % 60:02d}"
    )

    reviewer_id = st.session_state[
        "user"
    ]["student_id"]

    submitted_presenters = {
        row["presenter_id"]
        for row in read_records("reviews")
        if row["session_id"] == session["session_id"]
        and row["reviewer_id"] == reviewer_id
    }

    presenters = session_presenters(
        session["session_id"]
    )

    for presenter in presenters:
        presenter_id = presenter["student_id"]

        st.markdown(
            f"### {presenter['order']}. "
            f"{presenter['name']}"
        )

        if presenter_id == reviewer_id:
            st.caption("這是你的報告，不需要評價自己。")
            st.divider()
            continue

        if presenter_id in submitted_presenters:
            st.success("你已完成這位報告者的互評。")
            st.divider()
            continue

        form_key = (
            f"{session['session_id']}_"
            f"{presenter_id}"
        )

        with st.form(f"review_form_{form_key}"):
            score = st.slider(
                "整體評分",
                min_value=1,
                max_value=5,
                value=3,
                key=f"score_{form_key}",
            )

            comment = st.text_area(
                "評語",
                placeholder=(
                    "請填寫報告優點、改善建議或想提問的內容。"
                ),
                max_chars=1000,
                key=f"comment_{form_key}",
            )

            submit_button = st.form_submit_button(
                "提交這筆互評",
                type="primary",
            )

        if submit_button:
            try:
                submit_review(
                    session["session_id"],
                    presenter_id,
                    score,
                    comment,
                )

                st.success("互評已提交。")
                st.rerun()

            except ValueError as error:
                st.error(str(error))

        st.divider()


# =========================================================
# CSV 匯出
# =========================================================

def safe_csv_value(value):
    value = str(value)

    if value.lstrip().startswith(
        ("=", "+", "-", "@")
    ):
        return "'" + value

    return value


def show_and_download(
    records,
    filename,
    button_key,
):
    if not records:
        st.info("目前沒有資料。")
        return

    dataframe = pd.DataFrame(records)

    st.dataframe(
        dataframe,
        hide_index=True,
        use_container_width=True,
    )

    safe_dataframe = dataframe.map(
        safe_csv_value
    )

    csv_data = safe_dataframe.to_csv(
        index=False
    ).encode("utf-8-sig")

    st.download_button(
        label="下載 CSV 表格",
        data=csv_data,
        file_name=filename,
        mime="text/csv",
        key=button_key,
    )


# =========================================================
# 學生查看自己收到的互評
# =========================================================

def student_results_page():
    student_id = st.session_state[
        "user"
    ]["student_id"]

    sessions = read_records("sessions")

    finished_session_ids = {
        row["session_id"]
        for row in sessions
        if not is_session_active(row)
    }

    my_reviews = []

    for row in read_records("reviews"):
        if (
            row["presenter_id"] == student_id
            and row["session_id"] in finished_session_ids
        ):
            my_reviews.append({
                "報告日期": row["date"],
                "評價者學號": row["reviewer_id"],
                "評價者姓名": row["reviewer_name"],
                "評分": row["score"],
                "評語": row["comment"],
                "提交時間": row["submitted_at"],
            })

    st.subheader("我收到的互評")

    st.caption(
        "互評結束後會顯示評價者姓名、學號、分數與評語。"
    )

    show_and_download(
        my_reviews,
        "我的互評結果.csv",
        "download_my_reviews",
    )

    st.divider()
    st.subheader("管理員評分")

    my_grades = []

    for row in read_records("grades"):
        if row["presenter_id"] == student_id:
            my_grades.append({
                "報告日期": row["date"],
                "管理員分數": row["grade"],
                "管理員回饋": row["feedback"],
                "更新時間": row["updated_at"],
            })

    show_and_download(
        my_grades,
        "我的管理員評分.csv",
        "download_my_grades",
    )


# =========================================================
# 管理員登入
# =========================================================

def admin_login_page():
    st.subheader("管理員登入")

    with st.form("admin_login_form"):
        password = st.text_input(
            "管理員密碼",
            type="password",
        )

        login_button = st.form_submit_button(
            "登入管理介面",
            type="primary",
        )

    if login_button:
        correct_password = str(
            st.secrets["admin_password"]
        )

        if hmac.compare_digest(
            password,
            correct_password,
        ):
            st.session_state["role"] = "admin"
            st.rerun()
        else:
            st.error("管理員密碼錯誤。")


# =========================================================
# 管理員評分
# =========================================================

def save_admin_grade(
    session_id,
    presenter_id,
    grade,
    feedback,
):
    session = next(
        (
            row
            for row in read_records("sessions")
            if row["session_id"] == session_id
        ),
        None,
    )

    presenter = next(
        (
            row
            for row in session_presenters(session_id)
            if row["student_id"] == presenter_id
        ),
        None,
    )

    if not session or not presenter:
        raise ValueError("找不到場次或報告者。")

    grade_record = {
        "session_id": session_id,
        "date": session["date"],
        "presenter_id": presenter_id,
        "presenter_name": presenter["name"],
        "grade": grade,
        "feedback": feedback.strip(),
        "updated_at": taiwan_now_text(),
    }

    with DATA_LOCK:
        grades = read_records("grades")

        for row_number, row in enumerate(
            grades,
            start=2,
        ):
            if (
                row["session_id"] == session_id
                and row["presenter_id"] == presenter_id
            ):
                update_record(
                    "grades",
                    row_number,
                    grade_record,
                )
                return

        append_record(
            "grades",
            grade_record,
        )


@st.fragment(run_every="5s")
def admin_timer_panel():
    session = get_active_session()

    if not session:
        st.info("目前沒有進行中的互評。")
        return

    remaining_seconds = max(
        0,
        math.ceil(
            float(session["ends_at"]) - time.time()
        ),
    )

    st.warning(
        f"目前場次：{session['date']}｜"
        f"剩餘時間："
        f"{remaining_seconds // 60:02d}:"
        f"{remaining_seconds % 60:02d}"
    )


# =========================================================
# 管理員頁面
# =========================================================

def admin_page():
    st.title("互評管理介面")

    admin_timer_panel()

    st.subheader("1. 開始互評")

    schedules = all_schedule_rows()
    available_dates = sorted({
        row["date"] for row in schedules
    })

    if not available_dates:
        st.info(
            "請先到 Google 試算表的 schedule 工作表"
            "填入報告日期與學號。"
        )
    else:
        suggested_date = recommended_schedule_date(
            available_dates
        )

        selected_date = st.selectbox(
            "選擇報告日期",
            available_dates,
            index=available_dates.index(
                suggested_date
            ),
        )

        selected_presenters = schedule_for_date(
            selected_date
        )

        preview = [
            {
                "順序": row["order"],
                "學號": row["student_id"],
                "姓名": row["name"],
            }
            for row in selected_presenters
        ]

        st.dataframe(
            pd.DataFrame(preview),
            hide_index=True,
            use_container_width=True,
        )

        if st.button(
            "開始互評（10 分鐘）",
            type="primary",
        ):
            try:
                start_review_session(
                    selected_date
                )

                st.success(
                    "互評已開始，學生現在可以填寫。"
                )

                st.rerun()

            except ValueError as error:
                st.error(str(error))

    active_session = get_active_session()

    if active_session:
        st.write("若需要，可以提前結束互評。")

        confirm_close = st.checkbox(
            "我確認要提前結束目前場次"
        )

        if st.button(
            "提前結束互評",
            disabled=not confirm_close,
        ):
            close_review_session(
                active_session["session_id"]
            )
            st.rerun()

    st.divider()
    st.subheader("2. 查看全部互評")

    sessions = read_records("sessions")

    if not sessions:
        st.info("目前沒有互評場次。")
        return

    sessions = sorted(
        sessions,
        key=lambda row: float(row["started_at"]),
        reverse=True,
    )

    session_options = {
        row["session_id"]: (
            f"{row['date']}｜"
            f"{format_time(row['started_at'])}"
        )
        for row in sessions
    }

    selected_session_id = st.selectbox(
        "選擇互評場次",
        options=list(session_options.keys()),
        format_func=lambda session_id: (
            session_options[session_id]
        ),
    )

    session_reviews = [
        {
            "報告日期": row["date"],
            "評價者學號": row["reviewer_id"],
            "評價者姓名": row["reviewer_name"],
            "報告者學號": row["presenter_id"],
            "報告者姓名": row["presenter_name"],
            "互評分數": row["score"],
            "評語": row["comment"],
            "提交時間": row["submitted_at"],
        }
        for row in read_records("reviews")
        if row["session_id"] == selected_session_id
    ]

    show_and_download(
        session_reviews,
        "本場全部互評.csv",
        "download_session_reviews",
    )

    st.divider()
    st.subheader("3. 管理員評分")

    presenters = session_presenters(
        selected_session_id
    )

    if presenters:
        presenter_options = {
            row["student_id"]: (
                f"{row['order']}. "
                f"{row['name']} "
                f"（{row['student_id']}）"
            )
            for row in presenters
        }

        selected_presenter_id = st.selectbox(
            "選擇報告者",
            options=list(presenter_options.keys()),
            format_func=lambda student_id: (
                presenter_options[student_id]
            ),
        )

        existing_grade = next(
            (
                row
                for row in read_records("grades")
                if row["session_id"] == selected_session_id
                and row["presenter_id"]
                == selected_presenter_id
            ),
            None,
        )

        old_grade = (
            float(existing_grade["grade"])
            if existing_grade
            and existing_grade["grade"]
            else 0.0
        )

        old_feedback = (
            existing_grade["feedback"]
            if existing_grade
            else ""
        )

        with st.form("admin_grade_form"):
            grade = st.number_input(
                "管理員分數",
                min_value=0.0,
                max_value=100.0,
                value=old_grade,
                step=1.0,
            )

            feedback = st.text_area(
                "管理員回饋",
                value=old_feedback,
                max_chars=1000,
            )

            save_button = st.form_submit_button(
                "儲存或更新評分",
                type="primary",
            )

        if save_button:
            save_admin_grade(
                selected_session_id,
                selected_presenter_id,
                grade,
                feedback,
            )

            st.success("管理員評分已儲存。")
            st.rerun()

    st.divider()
    st.subheader("4. 匯出全學期資料")

    all_reviews = [
        {
            "報告日期": row["date"],
            "評價者學號": row["reviewer_id"],
            "評價者姓名": row["reviewer_name"],
            "報告者學號": row["presenter_id"],
            "報告者姓名": row["presenter_name"],
            "互評分數": row["score"],
            "評語": row["comment"],
            "提交時間": row["submitted_at"],
        }
        for row in read_records("reviews")
    ]

    show_and_download(
        all_reviews,
        "全學期互評資料.csv",
        "download_all_reviews",
    )

    all_grades = [
        {
            "報告日期": row["date"],
            "報告者學號": row["presenter_id"],
            "報告者姓名": row["presenter_name"],
            "管理員分數": row["grade"],
            "管理員回饋": row["feedback"],
            "更新時間": row["updated_at"],
        }
        for row in read_records("grades")
    ]

    show_and_download(
        all_grades,
        "全學期管理員評分.csv",
        "download_all_grades",
    )


# =========================================================
# 學生主頁
# =========================================================

def student_page():
    user = st.session_state["user"]

    st.sidebar.write(
        f"姓名：{user['name']}"
    )
    st.sidebar.write(
        f"學號：{user['student_id']}"
    )

    selected_page = st.sidebar.radio(
        "功能",
        [
            "進行互評",
            "我的互評結果",
            "報告順序",
        ],
    )

    if selected_page == "進行互評":
        st.title("進行互評")
        student_review_panel()

    elif selected_page == "我的互評結果":
        st.title("我的互評結果")

        if st.button("重新整理結果"):
            st.rerun()

        student_results_page()

    else:
        st.title("報告順序")

        schedules = all_schedule_rows()

        display_rows = [
            {
                "報告日期": row["date"],
                "順序": row["order"],
                "學號": row["student_id"],
                "姓名": row["name"],
            }
            for row in schedules
        ]

        if display_rows:
            st.dataframe(
                pd.DataFrame(display_rows),
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.info("目前尚未設定報告順序。")


# =========================================================
# 主程式
# =========================================================

def main():
    admin_route = (
        st.query_params.get("page") == "admin"
    )

    role = st.session_state.get("role")

    if role:
        if st.sidebar.button("登出"):
            st.session_state.clear()
            st.rerun()

    if admin_route:
        if role == "admin":
            admin_page()
        else:
            st.title("互評管理介面")
            admin_login_page()
        return

    if role == "admin":
        admin_page()
        return

    if role == "student":
        student_page()
        return

    st.title("課堂報告互評系統")

    st.write(
        "第一次使用請先註冊，之後使用學號與自設密碼登入。"
    )

    student_login_page()


main()
