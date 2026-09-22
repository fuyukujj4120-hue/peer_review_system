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


st.set_page_config(
    page_title="課堂報告互評系統",
    page_icon="📝",
    layout="wide",
)

# 讓老師查看學生互評內容時，停用的文字框仍以黑色顯示。
st.markdown(
    """
    <style>
    div[data-testid="stTextArea"] label,
    div[data-testid="stTextArea"] label p,
    div[data-testid="stTextArea"] textarea,
    div[data-testid="stTextArea"] textarea:disabled {
        color: #000000 !important;
        -webkit-text-fill-color: #000000 !important;
        opacity: 1 !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

TZ = ZoneInfo("Asia/Taipei")
REVIEW_SECONDS = 10 * 60
# 修改 Google 試算表欄位或新增工作表時遞增，避免沿用舊的連線快取。
STORE_SCHEMA_VERSION = 2

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
def connect_store(schema_version):
    del schema_version
    credentials = Credentials.from_service_account_info(
        dict(st.secrets["gcp_service_account"]),
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ],
    )
    spreadsheet = gspread.authorize(credentials).open_by_key(
        st.secrets["spreadsheet_id"]
    )
    existing = {sheet.title: sheet for sheet in spreadsheet.worksheets()}
    sheets = {}

    for sheet_name, headers in HEADERS.items():
        worksheet = existing.get(sheet_name)
        if worksheet is None:
            worksheet = spreadsheet.add_worksheet(
                title=sheet_name,
                rows=2000,
                cols=max(len(headers), 10),
            )
            worksheet.update(range_name="A1", values=[headers])
        elif not worksheet.row_values(1):
            worksheet.update(range_name="A1", values=[headers])
        elif worksheet.row_values(1) != headers:
            raise ValueError(
                f"Google 試算表的「{sheet_name}」第一列欄位不符合新版程式。"
                f"若尚無正式資料，請刪除該工作表後重新啟動網站。"
            )
        sheets[sheet_name] = worksheet

    return sheets, threading.RLock()


SHEETS, DATA_LOCK = connect_store(STORE_SCHEMA_VERSION)


def api_call(operation, attempts=5):
    for attempt in range(attempts):
        try:
            return operation()
        except gspread.exceptions.APIError as error:
            status = getattr(getattr(error, "response", None), "status_code", 0)
            if status not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise
            time.sleep(min(2 ** attempt, 8))


@st.cache_data(ttl=20, show_spinner=False)
def records(sheet_name):
    with DATA_LOCK:
        values = api_call(lambda: SHEETS[sheet_name].get_all_values())
    if len(values) <= 1:
        return []
    headers = values[0]
    output = []
    for row in values[1:]:
        padded = row + [""] * (len(headers) - len(row))
        output.append(dict(zip(headers, padded)))
    return output


def append_record(sheet_name, data):
    api_call(
        lambda: SHEETS[sheet_name].append_row(
            [str(data.get(column, "")) for column in HEADERS[sheet_name]],
            value_input_option="RAW",
        )
    )
    records.clear()


def update_record(sheet_name, row_number, data):
    api_call(
        lambda: SHEETS[sheet_name].update(
            range_name=f"A{row_number}",
            values=[
                [str(data.get(column, "")) for column in HEADERS[sheet_name]]
            ],
        )
    )
    records.clear()


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


def register_student(student_id, password, confirmation):
    student_id = student_id.strip()
    if not student_id:
        raise ValueError("請輸入學號。")
    if password != confirmation:
        raise ValueError("兩次密碼不一致。")
    if len(password) < 4:
        raise ValueError("密碼至少需要 4 個字元。")

    roster = roster_map()
    if student_id not in roster:
        raise ValueError("學號不在學生名單中。")

    with DATA_LOCK:
        if any(row["student_id"] == student_id for row in records("users")):
            raise ValueError("這個學號已註冊，請直接登入。")
        append_record(
            "users",
            {
                "student_id": student_id,
                "name": roster[student_id],
                "password_hash": password_hash(password),
                "created_at": now_text(),
            },
        )


def login_student(student_id, password):
    student_id = student_id.strip()
    user = next(
        (row for row in records("users") if row["student_id"] == student_id),
        None,
    )
    if user is None or not password_matches(password, user["password_hash"]):
        raise ValueError("學號或密碼錯誤。")
    return {"student_id": user["student_id"], "name": user["name"]}


def student_authentication():
    login_tab, register_tab = st.tabs(["學生登入", "首次註冊"])

    with login_tab:
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

    with register_tab:
        with st.form("student_registration"):
            student_id = st.text_input("學號", key="register_id")
            password = st.text_input("自設密碼", type="password", key="register_pw")
            confirmation = st.text_input(
                "再次輸入密碼", type="password", key="register_confirm"
            )
            submitted = st.form_submit_button("完成註冊", type="primary")
        if submitted:
            try:
                register_student(student_id, password, confirmation)
                st.success("註冊完成，請切換至學生登入。")
            except ValueError as error:
                st.error(str(error))


def submit_review(session_id, score, comment):
    reviewer = st.session_state["user"]
    comment = comment.strip()
    if not comment:
        raise ValueError("請輸入評語。")

    with DATA_LOCK:
        session = next(
            (
                row
                for row in records("sessions")
                if row["session_id"] == session_id
            ),
            None,
        )
        if not session_is_active(session):
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
            "score": score,
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
        score = st.slider(
            "整體評分",
            1,
            5,
            int(existing_review["score"]) if existing_review else 3,
        )
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
            result = submit_review(session["session_id"], score, comment)
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
                    "互評分數": review["score"],
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
            "老師分數": row["teacher_score"],
            "老師回饋": row["teacher_feedback"],
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


@st.fragment(run_every="5s")
def admin_timer():
    session = active_session()
    if not session:
        st.info("目前沒有進行中的互評。")
        return
    remaining = max(0, math.ceil(float(session["ends_at"]) - time.time()))
    review_count = sum(
        row["session_id"] == session["session_id"] for row in records("reviews")
    )
    st.warning(
        f"互評中：{session['presenter_name']}｜"
        f"剩餘 {remaining // 60:02d}:{remaining % 60:02d}｜"
        f"已收到 {review_count} 份"
    )


def admin_session_control():
    st.header("場次控制")
    st.write("每次只開放一位報告者，時間固定為 10 分鐘。")
    admin_timer()

    current = active_session()
    if current:
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
            "互評分數": row["score"],
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
            "互評分數": row["score"],
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
            value=float(existing["teacher_score"]) if existing else 0.0,
            step=1.0,
        )
        teacher_feedback = st.text_area(
            "老師給互評內容的回饋",
            value=existing["teacher_feedback"] if existing else "",
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
            "老師分數": row["teacher_score"],
            "老師回饋": row["teacher_feedback"],
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
    page = st.sidebar.radio("學生功能", ["進行互評", "我的互評結果", "報告順序"])
    if page == "進行互評":
        st.title("進行互評")
        student_review_panel()
    elif page == "我的互評結果":
        st.title("我的互評結果")
        student_results()
    else:
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

