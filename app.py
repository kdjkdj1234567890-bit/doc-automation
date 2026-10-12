#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
업무 자동화 데모 — 견적서·보고서 자동 생성기 (시연용 프로토타입)
Flask + SQLite + pandas + matplotlib + WeasyPrint. 외부 API 없이 로컬 동작.
"""
import base64
import csv
import io
import json
import os
import sqlite3
from datetime import date, datetime, timedelta

import pandas as pd
from flask import (Flask, abort, g, redirect, render_template, request,
                   send_file, url_for)

# ---------------------------------------------------------------- 경로 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
CHART_DIR = os.path.join(DATA_DIR, "charts")
DB_PATH = os.path.join(DATA_DIR, "app.db")
SAMPLE_DIR = os.path.join(BASE_DIR, "sample_data")

for d in (DATA_DIR, UPLOAD_DIR, CHART_DIR):
    os.makedirs(d, exist_ok=True)

FONT_REG = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB
app.config["JSON_AS_ASCII"] = False

VAT_RATE = 0.10


# ---------------------------------------------------------------- DB
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.execute(
        """CREATE TABLE IF NOT EXISTS settings(
            id INTEGER PRIMARY KEY CHECK (id = 1),
            company_name TEXT DEFAULT '',
            ceo TEXT DEFAULT '',
            biz_no TEXT DEFAULT '',
            address TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            email TEXT DEFAULT '',
            terms TEXT DEFAULT '',
            valid_days INTEGER DEFAULT 30,
            logo_path TEXT DEFAULT '')"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS documents(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_type TEXT NOT NULL,      -- 'quote' | 'report'
            title TEXT NOT NULL,
            created_at TEXT NOT NULL,
            payload TEXT NOT NULL)"""
    )
    cur = db.execute("SELECT COUNT(*) c FROM settings WHERE id=1")
    if cur.fetchone()["c"] == 0:
        default_terms = ("1. 본 견적의 유효기간은 견적일로부터 30일입니다.\n"
                         "2. 부가세 10% 별도이며, 결제 조건은 계약 시 협의합니다.\n"
                         "3. 견적 외 추가 작업 발생 시 별도 협의합니다.")
        db.execute(
            "INSERT INTO settings(id, company_name, ceo, biz_no, address, phone,"
            " email, terms, valid_days) VALUES(1,?,?,?,?,?,?,?,?)",
            ("데모 주식회사", "김동진", "123-45-67890",
             "광주광역시 광산구 풍영로170번길 39-25", "010-8529-5570",
             "demo@example.com", default_terms, 30),
        )
    db.commit()


def get_settings():
    db = get_db()
    cur = db.execute("SELECT * FROM settings WHERE id=1")
    row = cur.fetchone()
    return dict(row) if row else {}


def fmt_money(n):
    try:
        return f"{int(round(float(n))):,}"
    except (TypeError, ValueError):
        return "0"


app.jinja_env.filters["money"] = fmt_money


# ---------------------------------------------------------------- 유틸
def next_quote_no():
    db = get_db()
    today = date.today().strftime("%Y%m%d")
    cur = db.execute(
        "SELECT COUNT(*) c FROM documents WHERE doc_type='quote' "
        "AND substr(created_at,1,10)=?",
        (date.today().isoformat(),),
    )
    n = cur.fetchone()["c"] + 1
    return f"Q-{today}-{n:03d}"


def save_document(doc_type, title, payload):
    db = get_db()
    cur = db.execute(
        "INSERT INTO documents(doc_type, title, created_at, payload) "
        "VALUES(?,?,?,?)",
        (doc_type, title, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         json.dumps(payload, ensure_ascii=False)),
    )
    db.commit()
    return cur.lastrowid


def get_document(doc_id):
    db = get_db()
    cur = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,))
    row = cur.fetchone()
    if not row:
        return None
    d = dict(row)
    d["payload"] = json.loads(d["payload"])
    return d


# ---------------------------------------------------------------- 오류 처리
def friendly_error(message, code=400):
    return render_template("error.html", code=f"요청 처리 실패 ({code})",
                           message=message), code


@app.errorhandler(400)
def _bad_request(e):
    return render_template("error.html", code="잘못된 요청 (400)",
                           message=getattr(e, "description", "요청 형식이 올바르지 않습니다.")), 400


@app.errorhandler(404)
def _not_found(e):
    return render_template("error.html", code="페이지를 찾을 수 없습니다 (404)",
                           message="요청한 문서나 페이지가 존재하지 않습니다. "
                                   "삭제되었거나 주소가 바뀌었을 수 있습니다."), 404


@app.errorhandler(413)
def _too_large(e):
    return render_template("error.html", code="파일이 너무 큽니다 (413)",
                           message="16MB 이하의 파일만 업로드할 수 있습니다."), 413


@app.errorhandler(500)
def _server_error(e):
    return render_template(
        "error.html", code="일시적인 오류 (500)",
        message="처리 중 문제가 발생했습니다. 입력 내용을 확인하고 다시 시도해 주세요. "
                "같은 문제가 반복되면 파일 형식을 확인해 주세요."), 500


# ---------------------------------------------------------------- 라우트: 공통
@app.route("/")
def index():
    init_db()
    db = get_db()
    total = db.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
    quotes = db.execute(
        "SELECT COUNT(*) c FROM documents WHERE doc_type='quote'").fetchone()["c"]
    reports = db.execute(
        "SELECT COUNT(*) c FROM documents WHERE doc_type='report'").fetchone()["c"]
    recent = db.execute(
        "SELECT * FROM documents ORDER BY id DESC LIMIT 8").fetchall()
    return render_template("index.html", total=total, quotes=quotes,
                           reports=reports, recent=recent)


@app.route("/docs")
def docs():
    init_db()
    db = get_db()
    rows = db.execute("SELECT * FROM documents ORDER BY id DESC").fetchall()
    return render_template("docs.html", docs=rows)


@app.route("/docs/<int:doc_id>/delete", methods=["POST"])
def delete_doc(doc_id):
    init_db()
    db = get_db()
    db.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    db.commit()
    return redirect(url_for("docs"))


# ---------------------------------------------------------------- 라우트: 견적서
SAMPLE_ITEMS = [
    {"name": "반응형 홈페이지 제작", "spec": "5페이지 내외", "qty": 1, "unit_price": 3000000, "note": ""},
    {"name": "관리자 페이지 개발", "spec": "상품/주문 관리", "qty": 1, "unit_price": 2000000, "note": ""},
    {"name": "검색엔진 최적화(SEO) 기본 세팅", "spec": "메타태그·사이트맵", "qty": 1, "unit_price": 500000, "note": ""},
]


@app.route("/quote/new")
def quote_new():
    init_db()
    settings = get_settings()
    sample = request.args.get("sample") == "1"
    items = SAMPLE_ITEMS if sample else [{"name": "", "spec": "", "qty": 1, "unit_price": 0, "note": ""}]
    today = date.today().isoformat()
    valid_until = (date.today() + timedelta(days=settings.get("valid_days", 30) or 30)).isoformat()
    return render_template("quote_new.html", settings=settings, items=items,
                           today=today, valid_until=valid_until,
                           quote_no=next_quote_no(), sample=sample)


@app.route("/quote/create", methods=["POST"])
def quote_create():
    init_db()
    settings = get_settings()
    try:
        items = json.loads(request.form.get("items_json", "[]"))
    except json.JSONDecodeError:
        items = []
    items = [it for it in items if (it.get("name") or "").strip()]
    if not items:
        return friendly_error("품목을 1개 이상 입력하세요. 품명 입력 후 다시 시도해 주세요.")

    cleaned = []
    for it in items:
        def _num(v):
            try:
                return int(float(str(v).replace(",", "").strip() or 0))
            except (TypeError, ValueError):
                return 0
        qty, price = _num(it.get("qty", 0)), _num(it.get("unit_price", 0))
        cleaned.append({
            "name": str(it.get("name", ""))[:80],
            "spec": str(it.get("spec", ""))[:80],
            "qty": max(qty, 0),
            "unit_price": max(price, 0),
            "amount": max(qty, 0) * max(price, 0),
            "note": str(it.get("note", ""))[:80],
        })
    subtotal = sum(i["amount"] for i in cleaned)
    vat = int(round(subtotal * VAT_RATE))
    total = subtotal + vat

    payload = {
        "quote_no": request.form.get("quote_no") or next_quote_no(),
        "quote_date": request.form.get("quote_date") or date.today().isoformat(),
        "valid_until": request.form.get("valid_until") or "",
        "client_name": request.form.get("client_name", ""),
        "client_contact": request.form.get("client_contact", ""),
        "project_title": request.form.get("project_title", ""),
        "items": cleaned,
        "subtotal": subtotal,
        "vat": vat,
        "total": total,
        "company": settings,  # 생성 시점 템플릿 스냅샷
    }
    title = payload["project_title"] or f"견적서 {payload['quote_no']}"
    doc_id = save_document("quote", title, payload)
    return redirect(url_for("quote_view", doc_id=doc_id))


@app.route("/quote/<int:doc_id>")
def quote_view(doc_id):
    init_db()
    doc = get_document(doc_id)
    if not doc or doc["doc_type"] != "quote":
        abort(404)
    return render_template("quote_view.html", doc=doc, p=doc["payload"],
                           logo_uri=logo_data_uri())


@app.route("/quote/<int:doc_id>/html")
def quote_download_html(doc_id):
    init_db()
    doc = get_document(doc_id)
    if not doc or doc["doc_type"] != "quote":
        abort(404)
    html = render_template("quote_standalone.html", doc=doc, p=doc["payload"])
    buf = io.BytesIO(html.encode("utf-8"))
    return send_file(buf, as_attachment=True,
                     download_name=f"견적서_{doc['payload']['quote_no']}.html",
                     mimetype="text/html")


def logo_data_uri():
    """설정에 저장된 로고를 standalone HTML/PDF용 data URI로 변환."""
    path = get_settings().get("logo_path", "")
    if path and os.path.exists(path):
        ext = os.path.splitext(path)[1].lower().lstrip(".") or "png"
        mime = {"png": "image/png", "jpg": "image/jpeg",
                "jpeg": "image/jpeg", "gif": "image/gif",
                "svg": "image/svg+xml"}.get(ext, "image/png")
        with open(path, "rb") as f:
            return f"data:{mime};base64," + base64.b64encode(f.read()).decode()
    return ""


def html_to_pdf(html):
    """WeasyPrint로 HTML → PDF 변환 (완전 로컬, 외부 API 없음).

    시스템 폰트(Noto Sans CJK KR)를 사용하므로 한글이 정상 출력된다.
    """
    try:
        from weasyprint import HTML
        return HTML(string=html).write_pdf()
    except Exception:
        return None


@app.route("/quote/<int:doc_id>/pdf")
def quote_download_pdf(doc_id):
    init_db()
    doc = get_document(doc_id)
    if not doc or doc["doc_type"] != "quote":
        abort(404)
    html = render_template("quote_standalone.html", doc=doc,
                           p=doc["payload"], logo_uri=logo_data_uri())
    pdf = html_to_pdf(html)
    if pdf is None:
        return render_template(
            "error.html", code="PDF 생성 불가",
            message="이 서버에서 PDF 변환기를 찾을 수 없습니다. "
                    "HTML 다운로드 또는 브라우저 인쇄 기능을 이용하세요."), 501
    return send_file(io.BytesIO(pdf), as_attachment=True,
                     download_name=f"견적서_{doc['payload']['quote_no']}.pdf",
                     mimetype="application/pdf")


# ---------------------------------------------------------------- 라우트: 보고서
ALLOWED_EXT = {".csv", ".xlsx", ".xls"}


def read_upload(file_storage):
    ext = os.path.splitext(file_storage.filename.lower())[1]
    if ext not in ALLOWED_EXT:
        raise ValueError("CSV 또는 Excel 파일만 업로드할 수 있습니다. "
                         f"(올린 파일: {file_storage.filename})")
    raw = file_storage.read()
    bio = io.BytesIO(raw)
    if ext == ".csv":
        for enc in ("utf-8-sig", "cp949", "euc-kr"):
            try:
                return pd.read_csv(io.BytesIO(raw), encoding=enc)
            except (UnicodeDecodeError, pd.errors.ParserError):
                continue
        raise ValueError("CSV 인코딩을 인식할 수 없습니다. "
                         "UTF-8 또는 CP949(엑셀 기본) 형식으로 저장해 주세요.")
    try:
        return pd.read_excel(bio)
    except Exception:
        raise ValueError("Excel 파일을 읽을 수 없습니다. "
                         "파일이 손상되지 않았는지 확인해 주세요.")


def make_chart_base64(labels, values, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties
    # TTC는 addfont로 KR 서브폰트가 등록되지 않으므로 fname 직접 지정
    kr_font = FontProperties(fname=FONT_REG)
    kr_font.set_size(10)
    kr_font_b = FontProperties(fname=FONT_REG)
    kr_font_b.set_weight("bold")
    kr_font_b.set_size(13)
    fig, ax = plt.subplots(figsize=(8, max(2.6, 0.55 * len(labels))))
    y = list(range(len(labels)))
    ax.barh(y, values, color="#1a73e8", height=0.55)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontproperties=kr_font)
    ax.set_title(title, fontproperties=kr_font_b, pad=12)
    ax.invert_yaxis()
    for i, v in enumerate(values):
        ax.text(v, i, f"  {v:,.0f}", va="center", fontsize=9)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


def analyze_dataframe(df):
    df = df.dropna(how="all").copy()
    df.columns = [str(c).strip() for c in df.columns]
    n_rows = int(len(df))
    numeric_cols = [c for c in df.columns
                    if pd.api.types.is_numeric_dtype(df[c])]
    summary = {}
    for c in numeric_cols:
        s = df[c].dropna()
        summary[c] = {
            "sum": float(s.sum()), "mean": float(s.mean()),
            "max": float(s.max()), "min": float(s.min()),
        }
    label_col = next((c for c in df.columns if c not in numeric_cols), None)
    value_col = numeric_cols[0] if numeric_cols else None
    top_items, chart_b64 = [], ""
    if label_col and value_col:
        grp = (df.groupby(label_col, dropna=False)[value_col]
                 .sum().sort_values(ascending=False).head(8))
        top_items = [{"label": str(k), "value": float(v)}
                     for k, v in grp.items()]
        chart_b64 = make_chart_base64(
            [t["label"] for t in top_items][:6][::-1] or ["-"],
            [t["value"] for t in top_items][:6][::-1] or [0],
            f"{label_col}별 {value_col} 상위")
    preview_html = df.head(12).to_html(classes="tbl", index=False,
                                       border=0, justify="center")
    return {
        "rows": n_rows, "columns": list(df.columns),
        "numeric_summary": summary, "label_col": label_col,
        "value_col": value_col, "top_items": top_items,
        "chart_b64": chart_b64, "preview_html": preview_html,
    }


@app.route("/report/new")
def report_new():
    init_db()
    return render_template("report_new.html")


@app.route("/report/upload", methods=["POST"])
def report_upload():
    init_db()
    f = request.files.get("datafile")
    if not f or not f.filename:
        return friendly_error("파일을 선택하세요. CSV 또는 Excel 파일을 올려 주세요.")
    try:
        df = read_upload(f)
    except ValueError as e:
        return friendly_error(str(e))
    if df.empty or len(df.columns) == 0:
        return friendly_error("파일에 데이터가 없습니다. 내용이 있는 파일을 올려 주세요.")
    analysis = analyze_dataframe(df)
    analysis["filename"] = f.filename
    title = request.form.get("title") or f"데이터 요약 보고서 ({f.filename})"
    doc_id = save_document("report", title, analysis)
    return redirect(url_for("report_view", doc_id=doc_id))


@app.route("/report/<int:doc_id>")
def report_view(doc_id):
    init_db()
    doc = get_document(doc_id)
    if not doc or doc["doc_type"] != "report":
        abort(404)
    return render_template("report_view.html", doc=doc, p=doc["payload"])


@app.route("/report/<int:doc_id>/pdf")
def report_download_pdf(doc_id):
    init_db()
    doc = get_document(doc_id)
    if not doc or doc["doc_type"] != "report":
        abort(404)
    html = render_template("report_standalone.html", doc=doc, p=doc["payload"])
    pdf = html_to_pdf(html)
    if pdf is None:
        return render_template(
            "error.html", code="PDF 생성 불가",
            message="이 서버에서 PDF 변환기를 찾을 수 없습니다. "
                    "HTML 다운로드 또는 브라우저 인쇄 기능을 이용하세요."), 501
    return send_file(io.BytesIO(pdf), as_attachment=True,
                     download_name=f"보고서_{doc_id}.pdf",
                     mimetype="application/pdf")


@app.route("/report/<int:doc_id>/html")
def report_download_html(doc_id):
    init_db()
    doc = get_document(doc_id)
    if not doc or doc["doc_type"] != "report":
        abort(404)
    html = render_template("report_standalone.html", doc=doc, p=doc["payload"])
    buf = io.BytesIO(html.encode("utf-8"))
    return send_file(buf, as_attachment=True,
                     download_name=f"보고서_{doc_id}.html",
                     mimetype="text/html")


# ---------------------------------------------------------------- 라우트: 템플릿 설정
@app.route("/settings", methods=["GET", "POST"])
def settings():
    init_db()
    db = get_db()
    if request.method == "POST":
        logo_path = get_settings().get("logo_path", "")
        logo = request.files.get("logo")
        if logo and logo.filename:
            ext = os.path.splitext(logo.filename.lower())[1]
            if ext in (".png", ".jpg", ".jpeg", ".svg", ".gif"):
                logo_path = os.path.join(UPLOAD_DIR, "logo" + ext)
                logo.save(logo_path)
        try:
            valid_days = int(request.form.get("valid_days", 30))
        except (TypeError, ValueError):
            valid_days = 30
        db.execute(
            """UPDATE settings SET company_name=?, ceo=?, biz_no=?, address=?,
               phone=?, email=?, terms=?, valid_days=?, logo_path=? WHERE id=1""",
            (request.form.get("company_name", ""),
             request.form.get("ceo", ""),
             request.form.get("biz_no", ""),
             request.form.get("address", ""),
             request.form.get("phone", ""),
             request.form.get("email", ""),
             request.form.get("terms", ""),
             valid_days, logo_path),
        )
        db.commit()
        return redirect(url_for("settings"))
    s = get_settings()
    has_logo = bool(s.get("logo_path")) and os.path.exists(s["logo_path"])
    return render_template("settings.html", s=s, has_logo=has_logo)


@app.route("/settings/logo")
def settings_logo():
    init_db()
    path = get_settings().get("logo_path", "")
    if path and os.path.exists(path):
        return send_file(path)
    abort(404)


@app.route("/sample/<name>")
def sample_file(name):
    allowed = {"sample_quote_items.csv": "text/csv",
               "sample_sales.csv": "text/csv"}
    if name not in allowed:
        abort(404)
    path = os.path.join(SAMPLE_DIR, name)
    return send_file(path, as_attachment=True, download_name=name)


if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(host="127.0.0.1", port=5000, debug=False)
