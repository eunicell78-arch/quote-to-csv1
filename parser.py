import re
from typing import List, Dict, Tuple
from dateutil import parser as dateparser


OUT_COLS = [
    "Date", "Customer", "Planner", "Product",
    "Rated Current", "Cable Length", "Description",
    "Delivery Term", "MOQ", "Price", "L/T", "Remark"
]

DELIVERY_TERMS = [
    "FOB SH",
    "DAP KR BY SEA/FERRY",
    "DAP KR BY AIR",
]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _flat(text: str) -> str:
    # 줄바꿈/탭을 공백으로 치환해서 패턴 매칭을 쉽게 함
    return _norm(text.replace("\t", " ").replace("\r", "\n"))


def _to_date(raw: str) -> str:
    raw = _norm(raw)
    if not raw:
        return ""
    try:
        dt = dateparser.parse(raw, fuzzy=True, dayfirst=True)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return raw


def _extract_date(text: str) -> str:
    # 16-Dec-25
    m = re.search(r"\b(\d{1,2}-[A-Za-z]{3}-\d{2})\b", text)
    if m:
        return _to_date(m.group(1))

    # Nov. 19, 2025 / Dec. 22, 2025
    m = re.search(r"\b([A-Za-z]{3}\.? \d{1,2}, \d{4})\b", text)
    if m:
        return _to_date(m.group(1))

    # 2025-12-16
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if m:
        return _to_date(m.group(1))

    return ""


def _extract_customer_planner(text: str) -> Tuple[str, str]:
    t = text or ""

    # Customer: Co., Ltd. 중 SINBON/Jiangyin 제외하고 첫 번째를 고객으로
    customer = ""
    candidates = re.findall(r"([A-Za-z0-9 ,.&()\-]+Co\., Ltd\.)", t)
    for c in candidates:
        cu = c.upper()
        if "SINBON" in cu or "JIANGYIN" in cu:
            continue
        customer = _norm(c)
        break

    # Planner: Sherry Liu가 있으면 그걸로 고정(대부분 그렇습니다)
    planner = "Sherry Liu" if re.search(r"\bSherry Liu\b", t) else ""

    return customer, planner


def _extract_product_and_specs(text: str) -> Tuple[str, str, str, List[str]]:
    t = text or ""

    # Product: NACS Charging Cable 또는 NACS Charging Cable_J3400 등
    product = ""
    m = re.search(r"\b(NACS Charging Cable(?:_[A-Za-z0-9]+)?)\b", t)
    if m:
        product = _norm(m.group(1))

    # Rated Current
    rated = ""
    m = re.search(r"(?i)Rated\s*Current\s*:\s*([^\n\r\-•]+)", t)
    if m:
        rated = _norm(m.group(1))

    # Cable Length
    cable = ""
    m = re.search(r"(?i)Cable\s*Length\s*:\s*([^\n\r\-•]+)", t)
    if m:
        cable = _norm(m.group(1))

    # 기타 스펙(Description에 넣을 것)
    others: List[str] = []

    # Production Site
    m = re.search(r"(?i)Production\s*Site\s*:\s*([^\n\r\-•]+)", t)
    if m:
        others.append("Production Site: " + _norm(m.group(1)))

    # KC Certification (문구만 있는 타입)
    if re.search(r"(?i)\bKC\s*Certification\b", t):
        others.append("KC Certification")

    return product, rated, cable, others


def _extract_moq_price_pairs(text: str) -> List[Tuple[int, float]]:
    pairs = re.findall(r"\b(\d{1,6})\s+\$([\d,]+\.\d{2})\b", text)
    out: List[Tuple[int, float]] = []
    for moq, price in pairs:
        try:
            out.append((int(moq), float(price.replace(",", ""))))
        except Exception:
            continue
    return out


def _extract_lts(text: str) -> List[str]:
    # 6-8 / 8-10 / 4-6 등
    lts = re.findall(r"\b(\d{1,2}\s*-\s*\d{1,2})\b", text)
    out: List[str] = []
    for x in lts:
        x = x.replace(" ", "")
        if x not in out:
            out.append(x)
    return out


def _is_sample(text: str) -> bool:
    # "Sample"이 들어가면 샘플로 취급 (두 샘플 PDF 모두 해당)
    return bool(re.search(r"(?i)\bSample\b", text))


def _extract_sample_rows(text: str) -> List[Dict]:
    """
    Sample 견적 2가지 패턴 지원:
    1) 한 줄: "1 FOB SH Sample $535.62 4-6"
    2) 운임별: "FOB SH $535.62 4-6 ... DAP KR BY SEA/FERRY $610.01 6-8 ... 1 Sample"
    """
    f = _flat(text)

    rows: List[Dict] = []

    # (패턴 1) 1 FOB SH Sample $535.62 4-6
    m = re.search(r"\b1\s+(FOB SH)\s+Sample\s+\$([\d,]+\.\d{2})\s+(\d{1,2}-\d{1,2})\b", f, flags=re.I)
    if m:
        rows.append({
            "Delivery Term": "FOB SH",
            "MOQ": 1,
            "Price": float(m.group(2).replace(",", "")),
            "L/T": m.group(3).replace(" ", "")
        })
        return rows

    # (패턴 1-변형) 1 FOB SH Sample $535.62 4-6 (중간 공백/문구 흔들림)
    m = re.search(r"\b1\s+(FOB SH)\s+.*?\bSample\b\s+\$([\d,]+\.\d{2})\s+(\d{1,2}-\d{1,2})\b", f, flags=re.I)
    if m:
        rows.append({
            "Delivery Term": "FOB SH",
            "MOQ": 1,
            "Price": float(m.group(2).replace(",", "")),
            "L/T": m.group(3).replace(" ", "")
        })
        return rows

    # (패턴 2) term별 가격/리드타임
    term_patterns = [
        ("FOB SH", r"\bFOB SH\s+\$([\d,]+\.\d{2})\s+(\d{1,2}-\d{1,2})\b"),
        ("DAP KR BY SEA/FERRY", r"\bDAP KR BY SEA/FERRY\s+\$([\d,]+\.\d{2})\s+(\d{1,2}-\d{1,2})\b"),
        ("DAP KR BY AIR", r"\bDAP KR BY AIR\s+\$([\d,]+\.\d{2})\s+(\d{1,2}-\d{1,2})\b"),
    ]

    for term, pat in term_patterns:
        m = re.search(pat, f, flags=re.I)
        if m:
            rows.append({
                "Delivery Term": term,
                "MOQ": 1,
                "Price": float(m.group(1).replace(",", "")),
                "L/T": m.group(2).replace(" ", "")
            })

    return rows


def parse_sinbon_quote(text: str) -> List[Dict]:
    if not text:
        return []

    date = _extract_date(text)
    customer, planner = _extract_customer_planner(text)
    product, rated, cable, other_specs = _extract_product_and_specs(text)

    description = "; ".join([s for s in other_specs if _norm(s)])

    # Sample
    if _is_sample(text):
        sample_rows = _extract_sample_rows(text)
        out: List[Dict] = []
        for r in sample_rows:
            out.append({
                "Date": date,
                "Customer": customer,
                "Planner": planner,
                "Product": product,
                "Rated Current": rated,
                "Cable Len
