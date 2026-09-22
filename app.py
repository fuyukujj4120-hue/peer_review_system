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

TZ = ZoneInfo("Asia/Taipei")
REVIEW_SECONDS = 10 * 60

HEADERS = {
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
        "reviewer_id",
        "reviewer_name",
        "presenter_id",
        "presenter_name",
        "review_grade",
        "review_feedback",
        "updated_at",
    ],
}


@st.cache_resource
def connect_store():
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

    existing = {
        worksheet.title: worksheet
        for worksheet in spreadsheet.worksheets()
    }

    worksheets = {}

    for sheet_name, headers in HEADERS.items():
        worksheet = existing.get(sheet_name)

        if worksheet is None:
            worksheet = spreadsheet.add_worksheet(
                title=sheet_name,
                rows=2000,
                cols=max(len(headers), 10),
            )

            worksheet.update(
                range_name="A1",
                values=[headers],
            )

        elif not worksheet.row_values(1):
            worksheet.update(
                range_name="A1",
                values=[headers],
            )

        elif worksheet.row_values(1) != headers:
            raise ValueError(
                f"Google 試算表的「{sheet_name}」第一列欄位不符合程式。"
                f"若尚無正式資料，請刪除該工作表後重新啟動網站。"
            )

        worksheets[sheet_name] = worksheet

    return worksheets, threading.RLock()


SHEETS, DATA_LOCK = connect_store()


def records(sheet_name):
    values = SHEETS[sheet_name].get_all_values()

    if len(values) <= 1:
        return []

    headers = values[0]
    output = []

    for row in values[1:]:
        padded = row + [""] * (len(headers) - len(row))
        output.append(dict(zip(headers, padded)))

    return output


def append_record(sheet_name, data):
    SHEETS[sheet_name].append_row(
        [
            str(data.get(column, ""))
            for column in HEADERS[sheet_name]
        ],
        value_input_option="RAW",
    )


def update_record(sheet_name, row_number, data):
    SHEETS[sheet_name].update(
        range_name=f"A{row_number}",
        values=[
            [
                str(data.get(column, ""))
                for column in HEADERS[sheet_name]
            ]
        ],
    )


def now_text():
    return datetime.now(TZ).isoformat(
        timespec="seconds"
    )


def format_timestamp(timestamp):
    return datetime.fromtimestamp(
        float(timestamp),
        TZ,
    ).strftime("%Y-%m-%d %H:%M:%S")


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
            password_hash(password, salt),
            stored_hash,
        )

    except (ValueError, TypeError):
        return False


def roster_map():
    return {
        row["student_id"].strip(): row["name"].strip()
        for row in records("roster")
        if row["student_id"].strip()
        and row["name"].strip()
    }


def schedule_rows():
    roster = roster_map()
    output = []

    for row in records("schedule"):
        try:
            date = row["date"].strip()

            datetime.strptime(
                date,
                "%Y-%m-%d",
            )

            order = int(row["order"])
            student_id = row["student_id"].strip()

        except (
            ValueError,
            TypeError,
            KeyError,
        ):
            continue

        if student_id not in roster:
            continue

        output.append({
            "date": date,
            "order": order,
            "student_id": student_id,
            "name": roster[student_id],
        })

    return sorted(
        output,
        key=lambda row: (
            row["date"],
            row["order"],
        ),
    )


def recommended_date(dates):
    today = datetime.now(
        TZ
    ).date().isoformat()

    arrived_dates = [
        date
        for date in dates
        if date <= today
    ]

    if arrived_dates:
        return max(arrived_dates)

    return min(dates)


def session_is_active(session):
    if not session:
        return False

    try:
        return (
            session["status"] == "OPEN"
            and time.time()
            < float(session["ends_at"])
        )

    except (
        KeyError,
        ValueError,
        TypeError,
    ):
        return False


def active_session():
    active_sessions = [
        row
        for row in records("sessions")
        if session_is_active(row)
    ]

    if not active_sessions:
        return None

    return max(
        active_sessions,
        key=lambda row: float(
            row["started_at"]
        ),
    )


def session_finished(session):
    return not session_is_active(session)


def start_session(date, presenter_id):
    with DATA_LOCK:
        if active_session():
            raise ValueError(
                "目前已有一位報告者正在互評，"
                "請先等待結束。"
            )

        presenter = next(
            (
                row
                for row in schedule_rows()
                if row["date"] == date
                and row["student_id"]
                == presenter_id
            ),
            None,
        )

        if presenter is None:
            raise ValueError(
                "找不到這位報告者的排程。"
            )

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
                "ends_at": (
                    started_at
                    + REVIEW_SECONDS
                ),
                "status": "OPEN",
            },
        )

        return session_id


def close_session(session_id):
    with DATA_LOCK:
        for row_number, session in enumerate(
            records("sessions"),
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

    raise ValueError("找不到互評場次。")


def register_student(
    student_id,
    password,
    confirmation,
):
    student_id = student_id.strip()

    if not student_id:
        raise ValueError("請輸入學號。")

    if password != confirmation:
        raise ValueError(
            "兩次密碼不一致。"
        )

    if len(password) < 4:
        raise ValueError(
            "密碼至少需要 4 個字元。"
        )

    roster = roster_map()

    if student_id not in roster:
        raise ValueError(
            "學號不在學生名單中。"
        )

    with DATA_LOCK:
        existing_user = any(
            row["student_id"] == student_id
            for row in records("users")
        )

        if existing_user:
            raise ValueError(
                "這個學號已註冊，請直接登入。"
            )

        append_record(
            "users",
            {
                "student_id": student_id,
                "name": roster[student_id],
                "password_hash": password_hash(
                    password
                ),
                "created_at": now_text(),
            },
        )


def login_student(student_id, password):
    student_id = student_id.strip()

    user = next(
        (
            row
            for row in records("users")
            if row["student_id"]
            == student_id
        ),
        None,
    )

    if (
        user is None
        or not password_matches(
            password,
            user["password_hash"],
        )
    ):
        raise ValueError(
            "學號或密碼錯誤。"
        )

    return {
        "student_id": user["student_id"],
        "name": user["name"],
    }


def student_authentication():
    login_tab, register_tab = st.tabs([
        "學生登入",
        "首次註冊",
    ])

    with login_tab:
        with st.form("student_login"):
            student_id = st.text_input("學號")

            password = st.text_input(
                "密碼",
                type="password",
            )

            submitted = (
                st.form_submit_button(
                    "登入",
                    type="primary",
                )
            )

        if submitted:
            try:
                user = login_student(
                    student_id,
                    password,
                )

                st.session_state["user"] = user
                st.session_state["role"] = "student"

                st.rerun()

            except ValueError as error:
                st.error(str(error))

    with register_tab:
        with st.form(
            "student_registration"
        ):
            student_id = st.text_input(
                "學號",
                key="register_id",
            )

            password = st.text_input(
                "自設密碼",
                type="password",
                key="register_password",
            )

            confirmation = st.text_input(
                "再次輸入密碼",
                type="password",
                key="register_confirmation",
            )

            submitted = (
                st.form_submit_button(
                    "完成註冊",
                    type="primary",
                )
            )

        if submitted:
            try:
                register_student(
                    student_id,
                    password,
                    confirmation,
                )

                st.success(
                    "註冊完成，請切換至學生登入。"
                )

            except ValueError as error:
                st.error(str(error))


def submit_review(
    session_id,
    score,
    comment,
):
    reviewer = st.session_state["user"]
    comment = comment.strip()

    if not comment:
        raise ValueError("請輸入評語。")

    with DATA_LOCK:
        session = next(
            (
                row
                for row in records("sessions")
                if row["session_id"]
                == session_id
            ),
            None,
        )

        if not session_is_active(session):
            raise ValueError(
                "互評尚未開始或時間已結束。"
            )

        if (
            reviewer["student_id"]
            == session["presenter_id"]
        ):
            raise ValueError(
                "報告者不能評價自己。"
            )

        already_submitted = any(
            row["session_id"] == session_id
            and row["reviewer_id"]
            == reviewer["student_id"]
            for row in records("reviews")
        )

        if already_submitted:
            raise ValueError(
                "你已經提交過這一場互評。"
            )

        append_record(
            "reviews",
            {
                "session_id": session_id,
                "date": session["date"],
                "reviewer_id": reviewer["student_id"],
                "reviewer_name": reviewer["name"],
                "presenter_id": session["presenter_id"],
                "presenter_name": session["presenter_name"],
                "score": score,
                "comment": comment,
                "submitted_at": now_text(),
            },
        )


@st.fragment(run_every="5s")
def student_review_panel():
    session = active_session()

    if not session:
        st.info(
            "目前沒有開放中的互評，"
            "請等待管理員開始。"
        )
        return

    remaining = max(
        0,
        math.ceil(
            float(session["ends_at"])
            - time.time()
        ),
    )

    st.subheader(
        f"目前報告者："
        f"{session['presenter_name']}"
    )

    st.caption(
        f"報告日期：{session['date']}｜"
        f"當週第 "
        f"{session['presenter_order']} 位"
    )

    st.warning(
        f"剩餘時間："
        f"{remaining // 60:02d}:"
        f"{remaining % 60:02d}"
    )

    user = st.session_state["user"]

    if (
        user["student_id"]
        == session["presenter_id"]
    ):
        st.info(
            "你是本場報告者，"
            "不需要填寫自己的互評。"
        )
        return

    submitted = any(
        row["session_id"]
        == session["session_id"]
        and row["reviewer_id"]
        == user["student_id"]
        for row in records("reviews")
    )

    if submitted:
        st.success(
            "本場互評已提交。"
        )
        return

    with st.form(
        f"review_{session['session_id']}"
    ):
        score = st.slider(
            "整體評分",
            min_value=1,
            max_value=5,
            value=3,
        )

        comment = st.text_area(
            "評語",
            placeholder=(
                "請填寫優點、改善建議"
                "或想提問的內容。"
            ),
            max_chars=1000,
        )

        send = st.form_submit_button(
            "提交互評",
            type="primary",
        )

    if send:
        try:
            submit_review(
                session["session_id"],
                score,
                comment,
            )

            st.success("互評已提交。")
            st.rerun()

        except ValueError as error:
            st.error(str(error))


def csv_safe(value):
    text = str(value)

    if text.lstrip().startswith(
        ("=", "+", "-", "@")
    ):
        return "'" + text

    return text


def show_export(
    data,
    filename,
    key,
):
    if not data:
        st.info("目前沒有資料。")
        return

    dataframe = pd.DataFrame(data)

    st.dataframe(
        dataframe,
        hide_index=True,
        use_container_width=True,
    )

    safe_dataframe = dataframe.map(
        csv_safe
    )

    st.download_button(
        "下載 CSV",
        safe_dataframe.to_csv(
            index=False
        ).encode("utf-8-sig"),
        filename,
        "text/csv",
        key=key,
    )


def student_results():
    student_id = st.session_state[
        "user"
    ]["student_id"]

    sessions = {
        row["session_id"]: row
        for row in records("sessions")
    }

    received_reviews = []

    for review in records("reviews"):
        session = sessions.get(
            review["session_id"]
        )

        if (
            review["presenter_id"]
            == student_id
            and session_finished(session)
        ):
            received_reviews.append({
                "報告日期": review["date"],
                "評價者學號": review["reviewer_id"],
                "評價者姓名": review["reviewer_name"],
                "互評分數": review["score"],
                "評語": review["comment"],
                "提交時間": review["submitted_at"],
            })

    st.subheader("我收到的互評")

    show_export(
        received_reviews,
        "我的互評結果.csv",
        "my_received_reviews",
    )

    st.divider()
    st.subheader("我的報告成績")

    presentation_grades = [
        {
            "報告日期": row["date"],
            "報告者姓名": row["presenter_name"],
            "報告成績": row["grade"],
            "報告回饋": row["feedback"],
            "更新時間": row["updated_at"],
        }
        for row in records("grades")
        if row["presenter_id"]
        == student_id
    ]

    show_export(
        presentation_grades,
        "我的報告成績.csv",
        "my_presentation_grades",
    )

    st.divider()
    st.subheader("我的互評內容成績")

    review_lookup = {
        (
            row["session_id"],
            row["reviewer_id"],
        ): row
        for row in records("reviews")
    }

    my_review_grades = []

    for row in records("review_grades"):
        if row["reviewer_id"] != student_id:
            continue

        original_review = review_lookup.get(
            (
                row["session_id"],
                row["reviewer_id"],
            ),
            {},
        )

        my_review_grades.append({
            "報告日期": row["date"],
            "報告者姓名": row["presenter_name"],
            "我給的互評分數": (
                original_review.get(
                    "score",
                    "",
                )
            ),
            "我的互評內容": (
                original_review.get(
                    "comment",
                    "",
                )
            ),
            "互評內容成績": row["review_grade"],
            "管理員回饋": row["review_feedback"],
            "更新時間": row["updated_at"],
        })

    show_export(
        my_review_grades,
        "我的互評內容成績.csv",
        "my_review_content_grades",
    )


def admin_login():
    with st.form("admin_login"):
        password = st.text_input(
            "管理員密碼",
            type="password",
        )

        submitted = (
            st.form_submit_button(
                "登入",
                type="primary",
            )
        )

    if submitted:
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


@st.fragment(run_every="5s")
def admin_timer():
    session = active_session()

    if not session:
        st.info(
            "目前沒有進行中的互評。"
        )
        return

    remaining = max(
        0,
        math.ceil(
            float(session["ends_at"])
            - time.time()
        ),
    )

    review_count = sum(
        row["session_id"]
        == session["session_id"]
        for row in records("reviews")
    )

    st.warning(
        f"互評中："
        f"{session['presenter_name']}｜"
        f"剩餘 "
        f"{remaining // 60:02d}:"
        f"{remaining % 60:02d}｜"
        f"已收到 {review_count} 份"
    )


def admin_session_control():
    st.header("場次控制")

    st.write(
        "每次只開放一位報告者，"
        "時間固定為 10 分鐘。"
    )

    admin_timer()

    current = active_session()

    if current:
        confirm = st.checkbox(
            "確認提前結束目前互評"
        )

        if st.button(
            "提前結束",
            disabled=not confirm,
        ):
            close_session(
                current["session_id"]
            )

            st.rerun()

        return

    schedule = schedule_rows()

    dates = sorted({
        row["date"]
        for row in schedule
    })

    if not dates:
        st.info(
            "請先在 Google 試算表填入 "
            "roster 與 schedule。"
        )
        return

    selected_date = st.selectbox(
        "報告日期",
        dates,
        index=dates.index(
            recommended_date(dates)
        ),
    )

    presenters = [
        row
        for row in schedule
        if row["date"] == selected_date
    ]

    session_counts = {}

    for session in records("sessions"):
        key = (
            session["date"],
            session["presenter_id"],
        )

        session_counts[key] = (
            session_counts.get(key, 0)
            + 1
        )

    presenter_ids = [
        row["student_id"]
        for row in presenters
    ]

    presenter_labels = {
        row["student_id"]: (
            f"第 {row['order']} 位｜"
            f"{row['name']}｜"
            f"{row['student_id']}｜"
            f"已開 "
            f"{session_counts.get((selected_date, row['student_id']), 0)} 場"
        )
        for row in presenters
    }

    selected_presenter = st.selectbox(
        "選擇本次報告者",
        presenter_ids,
        format_func=lambda student_id: (
            presenter_labels[student_id]
        ),
    )

    if st.button(
        "開始這位報告者的互評",
        type="primary",
    ):
        try:
            start_session(
                selected_date,
                selected_presenter,
            )

            st.rerun()

        except ValueError as error:
            st.error(str(error))


def session_label(session):
    return (
        f"{session['date']}｜"
        f"第 {session['presenter_order']} 位｜"
        f"{session['presenter_name']}｜"
        f"{format_timestamp(session['started_at'])}"
    )


def session_selector(key):
    sessions = sorted(
        records("sessions"),
        key=lambda row: float(
            row["started_at"]
        ),
        reverse=True,
    )

    if not sessions:
        st.info("尚未建立互評場次。")
        return None

    session_map = {
        row["session_id"]: row
        for row in sessions
    }

    session_id = st.selectbox(
        "選擇場次",
        list(session_map),
        format_func=lambda value: (
            session_label(
                session_map[value]
            )
        ),
        key=key,
    )

    return session_map[session_id]


def admin_review_records():
    st.header("互評紀錄")

    selected_session = session_selector(
        "review_record_session"
    )

    if selected_session is None:
        return

    session_reviews = [
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
        if row["session_id"]
        == selected_session["session_id"]
    ]

    show_export(
        session_reviews,
        "本場互評紀錄.csv",
        "one_session_reviews",
    )

    st.divider()
    st.subheader("全學期互評")

    all_reviews = [
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

    show_export(
        all_reviews,
        "全學期互評紀錄.csv",
        "all_reviews",
    )


def save_presentation_grade(
    session,
    grade,
    feedback,
):
    grade_data = {
        "session_id": session["session_id"],
        "date": session["date"],
        "presenter_id": session["presenter_id"],
        "presenter_name": session["presenter_name"],
        "grade": grade,
        "feedback": feedback.strip(),
        "updated_at": now_text(),
    }

    with DATA_LOCK:
        for row_number, row in enumerate(
            records("grades"),
            start=2,
        ):
            if (
                row["session_id"]
                == session["session_id"]
            ):
                update_record(
                    "grades",
                    row_number,
                    grade_data,
                )

                return

        append_record(
            "grades",
            grade_data,
        )


def save_review_grade(
    review,
    review_grade,
    review_feedback,
):
    grade_data = {
        "session_id": review["session_id"],
        "date": review["date"],
        "reviewer_id": review["reviewer_id"],
        "reviewer_name": review["reviewer_name"],
        "presenter_id": review["presenter_id"],
        "presenter_name": review["presenter_name"],
        "review_grade": review_grade,
        "review_feedback": (
            review_feedback.strip()
        ),
        "updated_at": now_text(),
    }

    with DATA_LOCK:
        for row_number, row in enumerate(
            records("review_grades"),
            start=2,
        ):
            same_review = (
                row["session_id"]
                == review["session_id"]
                and row["reviewer_id"]
                == review["reviewer_id"]
            )

            if same_review:
                update_record(
                    "review_grades",
                    row_number,
                    grade_data,
                )

                return

        append_record(
            "review_grades",
            grade_data,
        )


def presentation_grading_panel():
    st.subheader("報告者的報告評分")

    selected_session = session_selector(
        "presentation_grading_session"
    )

    if selected_session is None:
        return

    st.info(
        f"報告者："
        f"{selected_session['presenter_name']}｜"
        f"{selected_session['presenter_id']}"
    )

    existing_grade = next(
        (
            row
            for row in records("grades")
            if row["session_id"]
            == selected_session["session_id"]
        ),
        None,
    )

    with st.form(
        "presentation_grade_form"
    ):
        presentation_grade = st.number_input(
            "報告成績",
            min_value=0.0,
            max_value=100.0,
            value=(
                float(existing_grade["grade"])
                if existing_grade
                and existing_grade["grade"]
                else 0.0
            ),
            step=1.0,
        )

        presentation_feedback = st.text_area(
            "報告回饋",
            value=(
                existing_grade["feedback"]
                if existing_grade
                else ""
            ),
            max_chars=1000,
        )

        submitted = (
            st.form_submit_button(
                "儲存或更新報告成績",
                type="primary",
            )
        )

    if submitted:
        save_presentation_grade(
            selected_session,
            presentation_grade,
            presentation_feedback,
        )

        st.success("報告成績已儲存。")
        st.rerun()

    st.subheader("全部報告成績")

    all_presentation_grades = [
        {
            "報告日期": row["date"],
            "報告者學號": row["presenter_id"],
            "報告者姓名": row["presenter_name"],
            "報告成績": row["grade"],
            "報告回饋": row["feedback"],
            "更新時間": row["updated_at"],
        }
        for row in records("grades")
    ]

    show_export(
        all_presentation_grades,
        "全學期報告成績.csv",
        "all_presentation_grades",
    )


def review_content_grading_panel():
    st.subheader("每位學生的互評內容評分")

    selected_session = session_selector(
        "review_content_grading_session"
    )

    if selected_session is None:
        return

    session_reviews = [
        row
        for row in records("reviews")
        if row["session_id"]
        == selected_session["session_id"]
    ]

    if not session_reviews:
        st.info(
            "這個場次目前沒有互評內容。"
        )
        return

    reviewer_options = {
        row["reviewer_id"]: row
        for row in session_reviews
    }

    selected_reviewer_id = st.selectbox(
        "選擇要評分的互評者",
        list(reviewer_options),
        format_func=lambda student_id: (
            f"{reviewer_options[student_id]['reviewer_name']}｜"
            f"{student_id}"
        ),
    )

    selected_review = reviewer_options[
        selected_reviewer_id
    ]

    st.markdown("#### 學生提交的互評")

    st.write(
        f"互評者："
        f"{selected_review['reviewer_name']}｜"
        f"{selected_review['reviewer_id']}"
    )

    st.write(
        f"報告者："
        f"{selected_review['presenter_name']}｜"
        f"{selected_review['presenter_id']}"
    )

    st.write(
        f"學生給報告者的分數："
        f"{selected_review['score']} 分"
    )

    st.text_area(
        "學生填寫的互評內容",
        value=selected_review["comment"],
        disabled=True,
        height=150,
    )

    existing_review_grade = next(
        (
            row
            for row in records("review_grades")
            if row["session_id"]
            == selected_review["session_id"]
            and row["reviewer_id"]
            == selected_review["reviewer_id"]
        ),
        None,
    )

    form_key = (
        f"review_grade_form_"
        f"{selected_review['session_id']}_"
        f"{selected_review['reviewer_id']}"
    )

    with st.form(form_key):
        review_grade = st.number_input(
            "互評內容成績",
            min_value=0.0,
            max_value=100.0,
            value=(
                float(
                    existing_review_grade[
                        "review_grade"
                    ]
                )
                if existing_review_grade
                and existing_review_grade[
                    "review_grade"
                ]
                else 0.0
            ),
            step=1.0,
        )

        review_feedback = st.text_area(
            "對互評內容的回饋",
            value=(
                existing_review_grade[
                    "review_feedback"
                ]
                if existing_review_grade
                else ""
            ),
            max_chars=1000,
        )

        submitted = (
            st.form_submit_button(
                "儲存或更新互評內容成績",
                type="primary",
            )
        )

    if submitted:
        save_review_grade(
            selected_review,
            review_grade,
            review_feedback,
        )

        st.success(
            "互評內容成績已儲存。"
        )

        st.rerun()

    st.divider()
    st.subheader("全部互評內容成績")

    all_review_grades = [
        {
            "報告日期": row["date"],
            "報告者學號": row["presenter_id"],
            "報告者姓名": row["presenter_name"],
            "互評者學號": row["reviewer_id"],
            "互評者姓名": row["reviewer_name"],
            "互評內容成績": row["review_grade"],
            "管理員回饋": row["review_feedback"],
            "更新時間": row["updated_at"],
        }
        for row in records("review_grades")
    ]

    show_export(
        all_review_grades,
        "全學期互評內容成績.csv",
        "all_review_content_grades",
    )


def admin_grading():
    st.header("評分管理")

    presentation_tab, review_tab = st.tabs([
        "報告評分",
        "互評內容評分",
    ])

    with presentation_tab:
        presentation_grading_panel()

    with review_tab:
        review_content_grading_panel()


def admin_roster_schedule():
    st.header("名單與排程")

    st.subheader("學生名單")

    roster_data = [
        {
            "學號": student_id,
            "姓名": name,
        }
        for student_id, name
        in roster_map().items()
    ]

    show_export(
        roster_data,
        "學生名單.csv",
        "roster_export",
    )

    st.divider()
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

    show_export(
        schedule_data,
        "報告順序.csv",
        "schedule_export",
    )


def admin_page():
    st.title("互評管理系統")

    page = st.sidebar.radio(
        "管理功能",
        [
            "場次控制",
            "互評紀錄",
            "評分管理",
            "名單與排程",
        ],
    )

    if page == "場次控制":
        admin_session_control()

    elif page == "互評紀錄":
        admin_review_records()

    elif page == "評分管理":
        admin_grading()

    else:
        admin_roster_schedule()


def student_page():
    user = st.session_state["user"]

    st.sidebar.write(
        f"{user['name']}｜"
        f"{user['student_id']}"
    )

    page = st.sidebar.radio(
        "學生功能",
        [
            "進行互評",
            "我的互評結果",
            "報告順序",
        ],
    )

    if page == "進行互評":
        st.title("進行互評")
        student_review_panel()

    elif page == "我的互評結果":
        st.title("我的互評結果")
        student_results()

    else:
        st.title("報告順序")

        schedule_data = [
            {
                "報告日期": row["date"],
                "當週順序": row["order"],
                "學號": row["student_id"],
                "姓名": row["name"],
            }
            for row in schedule_rows()
        ]

        if schedule_data:
            st.dataframe(
                pd.DataFrame(
                    schedule_data
                ),
                hide_index=True,
                use_container_width=True,
            )

        else:
            st.info(
                "尚未設定報告順序。"
            )


def main():
    role = st.session_state.get("role")

    admin_route = (
        st.query_params.get("page")
        == "admin"
    )

    if role:
        if st.sidebar.button("登出"):
            st.session_state.clear()
            st.rerun()

    if admin_route:
        if role == "admin":
            admin_page()

        else:
            st.title("管理員登入")
            admin_login()

        return

    if role == "admin":
        admin_page()

    elif role == "student":
        student_page()

    else:
        st.title("課堂報告互評系統")

        st.write(
            "第一次使用請先註冊，"
            "之後以學號與自設密碼登入。"
        )

        student_authentication()


main()
