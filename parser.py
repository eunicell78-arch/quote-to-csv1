import re
import pdfplumber
from dateutil import parser as dateparser

VERSION = "v2026-02-05-quote-layout-v1b-fallback"

OUT_COLS = [
    "Date", "Customer", "Planner", "Product",
    "Rated Current", "Cable Length", "Description",
    "Delivery Term", "MOQ", "Price", "L/T", "Remark"
]

# ---------------- utils ----------------

def N(s):
    return re.sub(r"\s+", " ", (s or "").strip())

def add_wks(lt):
    lt = N(lt)
    if not lt:
        return ""
    if re.search(r"\bwks\b", lt, re.I):
        return lt
    m = re.search(r"\b(\d{1,2})\s*-\s*(\d{1,2})\b", lt)
    if m:
        return f"{m.group(1)}-{m.group(2)}wks"
    return lt

def money_to_float(s):
    if not s:
        return None
    m = re.search(r"\$?\s*([\d,]+(?:\.\d{2})?)", s)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except:
        return None

def parse_date_from_text(t):
    for p in [
        r"\d{1,2}-[A-Za-z]{3}-\d{2}",
        r"[A-Za-z]{3}\.? \d{1,2}, \d{4}",
        r"\d{4}-\d{2}-\d{2}",
    ]:
        m = re.search(p, t)
        if m:
            try:
                d = dateparser.parse(m.group(0), fuzzy=True)
                return d.strftime("%Y-%m-%d")
            except:
                return m.group(0)
    return ""

def parse_to_from(t):
    """
    조건:
    Customer=To, Planner=From
    To/From가 같은 줄에 있을 수 있으니 non-greedy로 분리
    """
    cust = ""
    planner = ""

    m = re.search(r"\bTo:\s*(.*?)\s+From:\s*(.*?)(?:\n|$)", t, re.I)
    if m:
        cust = N(m.group(1))
        planner = N(m.group(2))
        return cust, planner

    m = re.search(r"\bTo:\s*([^\n]+)", t, re.I)
    if m:
        cust = N(m.group(1))

    m = re.search(r"\bFrom:\s*([^\n]+)", t, re.I)
    if m:
        planner = N(m.group(1))

    return cust, planner

def is_sample_token(s):
    return bool(re.search(r"\bSample\b", s or "", re.I))

def is_nre_token(s):
    return bool(re.search(r"\bNRE\s*List\b", s or "", re.I))

def strip_bullet(s):
    return N(re.sub(r"^[\-\u2022•]+", "", (s or "").strip()))

# ---------------- Product parsing by your rules ----------------

def split_product_block(lines):
    """
    조건
    4.Product = Rated Current 기준으로 윗줄
    5.Rated Current = Rated Current 라인 값
    6.Cable Length = Cable Length 라인 값
    7.Description = Cable Length 아래 라인들
    e.Description에 Rated/Cable 반복 금지
    """
    clean = [strip_bullet(x) for x in lines if strip_bullet(x)]
    if not clean:
        return "", "", "", ""

    rated_idx = None
    cable_idx = None
    rated_val = ""
    cable_val = ""

    for i, ln in enumerate(clean):
        if re.search(r"Rated\s*Current", ln, re.I):
            rated_idx = i
            parts = ln.split(":", 1)
            rated_val = N(parts[1]) if len(parts) == 2 else N(ln)
        if re.search(r"Cable\s*Length", ln, re.I):
            cable_idx = i
            parts = ln.split(":", 1)
            cable_val = N(parts[1]) if len(parts) == 2 else N(ln)

    if rated_idx is not None and rated_idx - 1 >= 0:
        product_name = clean[rated_idx - 1]
    else:
        product_name = clean[0]

    desc_parts = []
    if cable_idx is not None:
        after = clean[cable_idx + 1:]
        for ln in after:
            if re.search(r"Rated\s*Current|Cable\s*Length", ln, re.I):
                continue
            desc_parts.append(ln)
    else:
        # cable length가 없으면 rated 아래 라인을 desc로
        start = rated_idx + 1 if rated_idx is not None else 1
        for ln in clean[start:]:
            if re.search(r"Rated\s*Current|Cable\s*Length", ln, re.I):
                continue
            desc_parts.append(ln)

    desc = "; ".join([x for x in desc_parts if x])
    return product_name, rated_val, cable_val, desc

# ============================================================
#  FALLBACK TEXT PARSER (핵심)
# ============================================================

def parse_by_text_fallback(text):
    """
    extract_words가 글자 단위로 쪼개지는 PDF 대응:
    extract_text() 기반으로 Item row를 찾아 파싱한다.
    """
    lines = [x.rstrip() for x in (text or "").splitlines()]
    lines = [x for x in lines if N(x)]

    # 1) 글로벌 LT (샘플)
    lt = ""
    mlt = re.search(r"\b(\d{1,2})\s*-\s*(\d{1,2})\b", text)
    if mlt:
        lt = add_wks(f"{mlt.group(1)}-{mlt.group(2)}")

    # 2) 글로벌 Delivery Term: "FOB ... Shanghai" 같이 찢어져도 잡기
    delivery_global = ""
    mdel = re.search(r"\b(FOB|DAP|EXW|CIF|DDP)\b[\s\S]{0,120}?\b(Shanghai|SH)\b", text, re.I)
    if mdel:
        delivery_global = f"{mdel.group(1).upper()} {mdel.group(2)}"
        delivery_global = delivery_global.replace("SH", "SH").replace("Shanghai", "Shanghai")

    # 3) Item price line 찾기: "1 1 $497.83"
    item_lines = []
    for i, ln in enumerate(lines):
        # item, moq, $price 패턴
        if re.search(r"^\s*\d+\s+(?:\d+|Sample)\s+\$?[\d,]+\.\d{2}\s*$", ln):
            item_lines.append((i, ln))

    if not item_lines:
        return []

    out_rows = []

    for idx, (li, ln) in enumerate(item_lines):
        m = re.search(r"^\s*(\d+)\s+(\d+|Sample)\s+\$?([\d,]+\.\d{2})\s*$", ln)
        if not m:
            continue

        item_no = int(m.group(1))
        moq_token = m.group(2)
        price = money_to_float(m.group(3))

        # MOQ / Sample 처리
        remark_add = ""
        if is_sample_token(moq_token):
            moq_val = 1
            remark_add = "Sample"
        else:
            moq_val = int(moq_token)

        # 제품 블록 범위:
        # - 위로 올라가면서 "Charging Cable" 같은 제품명 라인 찾기
        p_start = li - 1
        while p_start >= 0:
            up = lines[p_start]
            if re.search(r"\bCharging\s+Cable\b", up, re.I) and not up.lower().startswith("item"):
                break
            p_start -= 1

        # 아래로는 다음 제품명(또는 다음 item price line 전)까지
        p_end = li + 1
        next_item_line_index = item_lines[idx + 1][0] if idx + 1 < len(item_lines) else len(lines)
        while p_end < next_item_line_index:
            # 다음 제품명 만나면 stop (다만 어떤 파일은 제품명이 반복되므로 item 경계가 더 신뢰됨)
            p_end += 1

        block_lines = lines[p_start:p_end] if p_start >= 0 else lines[max(0, li-10):p_end]

        product_name, rated, cable, desc = split_product_block(block_lines)

        # Delivery term: 블록 안에서 FOB/DAP/EXW 등 탐색, 없으면 글로벌
        delivery = ""
        btxt = "\n".join(block_lines)
        mdt = re.search(r"\b(FOB|DAP|EXW|CIF|DDP)\b[\s\S]{0,40}?\b(Shanghai|SH|Korea|Busan|Incheon)\b", btxt, re.I)
        if mdt:
            delivery = f"{mdt.group(1).upper()} {mdt.group(2)}"
        else:
            delivery = delivery_global

        # NRE List 케이스
        if is_nre_token(delivery) or is_nre_token(btxt) or is_nre_token(text):
            delivery = "NRE List"
            mcav = re.search(r"\bCavity\s*\d+\b", btxt, re.I)
            if mcav:
                cav = mcav.group(0)
                if cav and cav not in desc:
                    desc = (desc + "; " + cav).strip("; ").strip()

        out_rows.append({
            "Product": product_name,
            "Rated Current": rated,
            "Cable Length": cable,
            "Description": desc,
            "Delivery Term": delivery,
            "MOQ": moq_val,
            "Price": price,
            "L/T": lt,
            "Remark_add": remark_add
        })

    return out_rows

# ============================================================
#  MAIN: parse_quote_file (좌표 실패하면 fallback)
# ============================================================

def parse_quote_file(file_obj):
    debug = {}

    with pdfplumber.open(file_obj) as pdf:
        full_text = []
        all_words = []
        for page in pdf.pages:
            full_text.append(page.extract_text() or "")
            ws = page.extract_words(
                keep_blank_chars=False,
                use_text_flow=True,
                extra_attrs=["x0", "x1", "top", "bottom"]
            )
            all_words.extend(ws)

    text = "\n".join(full_text)
    date = parse_date_from_text(text)
    customer, planner = parse_to_from(text)

    debug["date"] = date
    debug["customer"] = customer
    debug["planner"] = planner
    debug["words_count"] = len(all_words)

    # ✅ 좌표 헤더 탐지(단어가 글자 단위로 쪼개지는 PDF는 실패할 수 있음)
    # -> 이번 케이스는 fallback으로 처리
    # (header 탐지 로직을 더 복잡하게 만들기보다, text 기반이 더 안정적)

    rows = parse_by_text_fallback(text)
    debug["fallback_rows"] = len(rows)

    if not rows:
        return [], debug

    out = []
    for r in rows:
        remark = N(r.get("Remark_add", ""))

        out.append({
            "Date": date,
            "Customer": customer,
            "Planner": planner,
            "Product": r.get("Product", ""),
            "Rated Current": r.get("Rated Current", ""),
            "Cable Length": r.get("Cable Length", ""),
            "Description": r.get("Description", ""),
            "Delivery Term": r.get("Delivery Term", ""),
            "MOQ": r.get("MOQ", ""),
            "Price": r.get("Price", ""),
            "L/T": r.get("L/T", ""),
            "Remark": remark
        })

    debug["out_count"] = len(out)
    return out, debug

