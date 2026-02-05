import re
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
from dateutil import parser as dateparser


OUT_COLS = [
    "Date", "Customer", "Planner", "Product",
    "Rated Current", "Cable Length", "Description",
    "Delivery Term", "MOQ", "Price", "L/T", "Remark"
]

DELIVERY_TERMS_CANON = [
    "FOB SH",
    "DAP KR BY SEA/FERRY",
    "DAP KR BY AIR",
]

def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def _to_iso_date(raw: str) -> str:
    raw = _norm_ws(raw)
    if not raw:
        return ""
    # dateutil이 "16-Dec-25", "Dec. 22, 2025", "Nov. 19, 2025" 등 대부분 처리
    try:
        dt = dateparser.parse(raw, dayfirst=True, fuzzy=True)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return raw  # 실패 시 원문 유지

def _extract_date(text: str) -> str:
    # 패턴: 16-Dec-25
    m = re.search(r"\b(\d{1,2}-[A-Za-z]{3}-\d{2})\b", text)
    if m:
        return _to_iso_date(m.group(1))
    # 패턴: Nov. 19, 2025 / Dec. 22, 2025
    m = re.search(r"\b([A-Za-z]{3}\.? \d{1,2}, \d{4})\b", text)
    if m:
        return _to_iso_date(m.group(1))
    # 패턴: 2025-12-16 같은 것도 허용
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if m:
        return _to_iso_date(m.group(1))
    return ""

def _extract_customer_planner(text: str) -> Tuple[str, str]:
    # 텍스트에 "Daeyoung Chaevi Co., Ltd. Sherry Liu" 같이 한 줄로 있는 경우가 많음
    m = re.search(r"([A-Za-z0-9 ,.&()\-]+Co\., Ltd\.)\s+([A-Za-z ,.\-]+)", text)
    if m:
        customer = _norm_ws(m.group(1))
        planner = _norm_ws(m.group(2))
        # planner가 너무 길게 잡히면 'Sherry Liu' 우선
        if "Sherry Liu" in text:
            planner = "Sherry Liu"
        return customer, planner

    # 고객사: Co., Ltd. 중 SINBON 제외
    candidates = re.findall(r"([A-Za-z0-9 ,.&()\-]+Co\., Ltd\.)", text)
    customer = ""
    for c in candidates:
        if "SINBON" not in c.upper() and "JIANGYIN" not in c.upper():
            customer = _norm_ws(c)
            break

    planner = "Sherry Liu" if "Sherry Liu" in text else ""
    return customer, planner

def _extract_product_block_lines(text: str) -> List[str]:
    """
    'Product' 섹션 이후 스펙 bullet 포함 블록을 라인 단위로 추출
    """
    lines = [l.rstrip() for l in text.splitlines()]
    idx = None
    for i, l in enumerate(lines):
        if _norm_ws(l).lower() == "product":
            idx = i
            break
    if idx is None:
        return []
    # Product 다음 라인부터 아래로, Notes 전까지
    block = []
    for l in lines[idx+1:]:
        if _norm_ws(l).lower().startswith("notes:"):
            break
        block.append(l)
    # 공백 라인 정리
    return [x for x in block if _norm_ws(x)]

def _extract_product_and_specs(text: str) -> Tuple[str, str, str, List[str]]:
    """
    return: (product, rated_current, cable_length, other_specs_for_description)
    규칙:
    - Rated Current / Cable Length는 별도 컬럼
    - Description에는 그 외 스펙만 (Production Site, KC Certification 등)
    """
    block = _extract_product_block_lines(text)

    # 제품명 후보: 보통 item 번호(1) 다음 라인
    product = ""
    for l in block:
        t = _norm_ws(l)
        if t.isdigit():
            continue
        # bullet 시작 전 첫 라인을 제품명으로 채택
        if not t.startswith("-"):
            product = t
            break

    # 스펙 bullet 수집
    bullets = []
    for l in block:
        t = _norm_ws(l)
        if t.startswith("-"):
            bullets.append(t[1:].strip())

    rated = ""
    cable = ""
    others = []

    for b in bullets:
        if re.match(r"(?i)^rated\s*current\s*:", b):
            rated = _norm_ws(b.split(":", 1)[1])
        elif re.match(r"(?i)^cable\s*length\s*:", b):
            cable = _norm_ws(b.split(":", 1)[1])
        else:
            # Production Site, KC Certification 등
            others.append(b)

    return product, rated, cable, others

def _extract_lead_times(text: str) -> List[str]:
    # 예: 6-8, 8-10, 4-6 등
    lts = re.findall(r"\b(\d{1,2}\s*-\s*\d{1,2})\b", text)
    # 순서 보존하며 정리
    out = []
    for x in lts:
        x = x.replace(" ", "")
        if x not in out:
            out.append(x)
    return out

def _extract_moq_price_pairs(text: str) -> List[Tuple[int, float]]:
    # 예: "20 $228.91" 형태를 모두 추출
    pairs = re.findall(r"\b(\d{1,6})\s+\$([\d,]+\.\d{2})\b", text)
    out = []
    for moq, price in pairs:
        try:
            out.append((int(moq), float(price.replace(",", ""))))
        except Exception:
            continue
    return out

def _is_sample_quote(text: str) -> bool:
    # Sample 단어가 있고 MOQ 1이 포함되면 샘플로 간주
    t = text.lower()
    return ("sample" in t) and re.search(r"\b1\b", text) is not None

def _extract_sample_rows(text: str) -> List[Dict]:
    """
    Sample 견적:
    - 케이스1: "1 FOB SH Sample $535.62 4-6"
    - 케이스2: Delivery term별로 price/lt 있고 아래에 "1 Sample"
    """
    flat = _norm_ws(text)

    # 케이스1: 한 줄에 다 있는 형태
    m = re.search(r"\b1\s+(FOB SH)\s+Sample\s+\$([\d,]+\.\d{2})\s+(\d{1,2}-\d{1,2})\b", flat, flags=re.I)
    if m:
        term = _norm_ws(m.group(1)).upper()
        price = float(m.group(2).replace(",", ""))
        lt = m.group(3).replace(" ", "")
        return [{
            "Delivery Term": term,
            "MOQ": 1,
            "Price": price,
            "L/T": lt,
        }]

    # 케이스2: term별 라인(FOB / SEA/FERRY / AIR)
    rows = []
    # DAP KR BY SEA/FERRY 같은 건 PDF에서 줄바꿈이 섞여있어 flat에서 찾기 쉽게 처리
    pattern_map = [
        ("FOB SH", r"\bFOB SH\s+\$([\d,]+\.\d{2})\s+(\d{1,2}-\d{1,2})\b"),
        ("DAP KR BY SEA/FERRY", r"\bDAP KR BY SEA/FERRY\s+\$([\d,]+\.\d{2})\s+(\d{1,2}-\d{1,2})\b"),
        ("DAP KR BY AIR", r"\bDAP KR BY AIR\s+\$([\d,]+\.\d{2})\s+(\d{1,2}-\d{1,2})\b"),
    ]
    for term, pat in pattern_map:
        m = re.search(pat, flat, flags=re.I)
        if m:
            price = float(m.group(1).replace(",", ""))
            lt = m.group(2).replace(" ", "")
            rows.append({"Delivery Term": term, "MOQ": 1, "Price": price, "L/T": lt})

    # term을 못 찾으면 최소한 FOB만이라도 찾기
    if not rows:
        m = re.search(r"\bFOB SH\s+\$([\d,]+\.\d{2})\s+(\d{1,2}-\d{1,2})\b", flat, flags=re.I)
        if m:
            rows.append({"Delivery Term": "FOB SH", "MOQ": 1, "Price": float(m.group(1).replace(",", "")), "L/T": m.group(2).replace(" ", "")})

    return rows

def parse_sinbon_quote(extracted_text: str) -> List[Dict]:
    """
    SINBON Quotation PDF(텍스트 추출 결과) -> 샘플 양식 행 리스트
    """
    text = extracted_text or ""
    date = _extract_date(text)
    customer, planner = _extract_customer_planner(text)
    product, rated, cable, other_specs = _extract_product_and_specs(text)

    # Description 구성: Rated/Cable 제외, 나머지 스펙만
    description = "; ".join([_norm_ws(x) for x in other_specs if _norm_ws(x)])

    # Sample vs Mass
    if _is_sample_quote(text):
        sample_rows = _extract_sample_rows(text)
        out = []
        for r in sample_rows:
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
                "L/T": (r["L/T"] + "wks") if r.get("L/T") else "",
                "Remark": "",
            })
        return out

    # Mass(20/50 등) 견적
    pairs = _extract_moq_price_pairs(text)  # 기대: 6개(3 term * 2 MOQ)
    lts = _extract_lead_times(text)         # 기대: ["6-8","8-10"] 또는 ["6-8","8-10","6-8"] 형태

    # term별 LT 매핑
    # - 3개 LT가 있으면 그대로
    # - 2개 LT만 있으면 [lt1, lt2, lt1]로 가정(FOB와 AIR가 같은 LT인 케이스가 관측됨)
    term_lts = []
    if len(lts) >= 3:
        term_lts = lts[:3]
    elif len(lts) == 2:
        term_lts = [lts[0], lts[1], lts[0]]
    elif len(lts) == 1:
        term_lts = [lts[0], lts[0], lts[0]]
    else:
        term_lts = ["", "", ""]

    # pairs를 2개씩 끊어서 term에 매핑
    out = []
    # 방어: pairs가 6개 미만이면 가능한 만큼만 생성
    for term_idx, term in enumerate(DELIVERY_TERMS_CANON):
        start = term_idx * 2
        chunk = pairs[start:start+2]
        for moq, price in chunk:
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
                "L/T": (term_lts[term_idx] + "wks") if term_lts[term_idx] else "",
                "Remark": "",
            })

    return out
