import re
from typing import List, Dict, Tuple
from dateutil import parser as dateparser


# 출력 컬럼 순서
OUT_COLS = [
    "Date", "Customer", "Planner", "Product",
    "Rated Current", "Cable Length", "Description",
    "Delivery Term", "MOQ", "Price", "L/T", "Remark"
]


# 표준 Delivery Term 순서
DELIVERY_TERMS = [
    "FOB SH",
    "DAP KR BY SEA/FERRY",
    "DAP KR BY AIR",
]


# -------------------------
# 기본 유틸
# -------------------------

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _to_date(raw: str) -> str:
    raw = _norm(raw)

    if not raw:
        return ""

    try:
        dt = dateparser.parse(raw, fuzzy=True, dayfirst=True)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return raw


# -------------------------
# 날짜 추출
# -------------------------

def _extract_date(text: str) -> str:
    # 16-Dec-25
    m = re.search(r"\b\d{1,2}-[A-Za-z]{3}-\d{2}\b", text)
    if m:
        return _to_date(m.group(0))

    # Nov. 19, 2025 / Dec. 22, 2025
    m = re.search(r"[A-Za-z]{3}\.? \d{1,2}, \d{4}", text)
    if m:
        return _to_date(m.group(0))

    # 2025-12-16
    m = re.search(r"\b\d{4}-\d{2}-\d{2}\b", text)
    if m:
        return _to_date(m.group(0))

    return ""


# -------------------------
# Customer / Planner 추출
# -------------------------

def _extract_customer_planner(text: str) -> Tuple[str, str]:

    lines = [
        _norm(l)
        for l in (text or "").splitlines()
        if _norm(l)
    ]

    # 상단 30줄만 사용
    top = lines[:30]
    joined = " ".join(top)

    # Customer: Co., Ltd. 중 SINBON 제외
    customer = ""
    matches = re.findall(r"[A-Za-z0-9 ,.&()\-]+Co\., Ltd\.", joined)

    for m in matches:
        if "SINBON" not in m.upper() and "JIANGYIN" not in m.upper():
            customer = _norm(m)
            break

    # Planner: 대부분 Sherry Liu
    planner = ""
    if "Sherry Liu" in joined:
        planner = "Sherry Liu"
    else:
        m = re.search(r"From:\s*([A-Za-z ,.\-]+)", joined, re.I)
        if m:
            planner = _norm(m.group(1))

    return customer, planner


# -------------------------
# Product / Spec 추출
# -------------------------

def _extract_product_and_specs(text: str) -> Tuple[str, str, str, List[str]]:

    t = text or ""

    # Product (NACS Charging Cable 계열)
    product = ""
    m = re.search(r"(NACS Charging Cable(?:[_][A-Za-z0-9]+)?)", t)
    if m:
        product = _norm(m.group(1))

    # Rated Current
    rated = ""
    m = re.search(r"(?i)Rated\s*Current:\s*([^\n\r\-•]+)", t)
    if m:
        rated = _norm(m.group(1))

    # Cable Length
    cable = ""
    m = re.search(r"(?i)Cable\s*Length:\s*([^\n\r\-•]+)", t)
    if m:
        cable = _norm(m.group(1))

    # 기타 스펙
    others = []

    # Production Site
    m = re.search(r"(?i)Production\s*Site:\s*([^\n\r\-•]+)", t)
    if m:
        others.append("Production Site: " + _norm(m.group(1)))

    # KC Certification
    if re.search(r"(?i)\bKC\s*Certification\b", t):
        others.append("KC Certification")

    return product, rated, cable, others


# -------------------------
# MOQ / Price 추출
# -------------------------

def _extract_moq_price(text: str) -> List[Tuple[int, float]]:

    pairs = re.findall(r"\b(\d+)\s+\$([\d,]+\.\d{2})\b", text)

    out = []

    for q, p in pairs:
        try:
            out.append((int(q), float(p.replace(",", ""))))
        except Exception:
            pass

    return out


# -------------------------
# Lead Time 추출
# -------------------------

def _extract_lt(text: str) -> List[str]:

    lts = re.findall(r"\b(\d{1,2}\s*-\s*\d{1,2})\b", text)

    out = []

    for x in lts:
        x = x.replace(" ", "")
        if x not in out:
            out.append(x)

    return out


# -------------------------
# Sample 여부 판단
# -------------------------

def _is_sample(text: str) -> bool:
    t = text.lower()

    if "sample" in t and re.search(r"\b1\b", text):
        return True

    return False


# -------------------------
# Sample Row 추출
# -------------------------

def _extract_sample_rows(text: str) -> List[Dict]:

    flat = _norm(text)

    rows = []

    patterns = [
        ("FOB SH", r"FOB SH\s+\$([\d,]+\.\d{2})\s+(\d{1,2}-\d{1,2})"),
        ("DAP KR BY SEA/FERRY", r"DAP KR BY SEA/FERRY\s+\$([\d,]+\.\d{2})\s+(\d{1,2}-\d{1,2})"),
        ("DAP KR BY AIR", r"DAP KR BY AIR\s+\$([\d,]+\.\d{2})\s+(\d{1,2}-\d{1,2})"),
    ]

    for term, pat in patterns:
        m = re.search(pat, flat, re.I)

        if m:
            price = float(m.group(1).replace(",", ""))
            lt = m.group(2).replace(" ", "")

            rows.append({
                "Delivery Term": term,
                "MOQ": 1,
                "Price": price,
                "L/T": lt,
            })

    return rows


# -------------------------
# 메인 파서
# -------------------------

def parse_sinbon_quote(text: str) -> List[Dict]:

    if not text:
        return []

    date = _extract_date(text)
    customer, planner = _extract_customer_planner(text)
    product, rated, cable, others = _extract_product_and_specs(text)

    description = "; ".join(others)

    # ---------------------
    # Sample 견적
    # ---------------------
    if _is_sample(text):

        rows = _extract_sample_rows(text)

        out = []

        for r in rows:
            out.append({
                "Date": date,
                "Customer": customer,
                "Planner": planner,
                "Product": product,
                "Rated Current": rated,
                "Cable Length": cable,
                "Description": description,
                "Delivery Term": r["Delivery Term"],
                "MOQ": r["MOQ"],
                "Price": r["Price"],
                "L/T": r["L/T"] + "wks",
                "Remark": "",
            })

        return out


    # ---------------------
    # 양산 견적
    # ---------------------

    pairs = _extract_moq_price(text)
    lts = _extract_lt(text)

    # LT 보정
    if len(lts) >= 3:
        term_lts = lts[:3]
    elif len(lts) == 2:
        term_lts = [lts[0], lts[1], lts[0]]
    elif len(lts) == 1:
        term_lts = [lts[0], lts[0], lts[0]]
    else:
        term_lts = ["", "", ""]

    out = []

    for i, term in enumerate(DELIVERY_TERMS):

        chunk = pairs[i * 2:(i * 2) + 2]

        for moq, price in chunk:

            lt = term_lts[i]

            out.append({
                "Date": date,
                "Customer": customer,
                "Planner": planner,
                "Product": product,
                "Rated Current": rated,
                "Cable Length": cable,
                "Description": description,
                "Delivery Term": term,
                "MOQ": moq,
                "Price": price,
                "L/T": (lt + "wks") if lt else "",
                "Remark": "",
            })

    return out
