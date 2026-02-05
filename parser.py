import re
from typing import List, Dict, Tuple, Optional
from dateutil import parser as dateparser

VERSION = "v2026-02-05-generic-table1"

OUT_COLS = [
    "Date", "Customer", "Planner", "Product",
    "Rated Current", "Cable Length", "Description",
    "Delivery Term", "MOQ", "Price", "L/T", "Remark"
]

# ---------------- util ----------------

def N(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def flat(s: str) -> str:
    return N((s or "").replace("\n", " ").replace("\r", " ").replace("\t", " "))

def add_wks(lt: str) -> str:
    lt = N(lt)
    if not lt:
        return ""
    # 이미 wks 있으면 그대로
    if re.search(r"\bwks\b", lt, re.I):
        return lt
    # 6-8 형태면 wks 추가
    m = re.search(r"\b(\d{1,2})\s*-\s*(\d{1,2})\b", lt)
    if m:
        return f"{m.group(1)}-{m.group(2)}wks"
    return lt

def parse_date(text: str) -> str:
    for p in [
        r"\d{1,2}-[A-Za-z]{3}-\d{2}",
        r"[A-Za-z]{3}\.? \d{1,2}, \d{4}",
        r"\d{4}-\d{2}-\d{2}",
    ]:
        m = re.search(p, text)
        if m:
            try:
                d = dateparser.parse(m.group(0), fuzzy=True)
                return d.strftime("%Y-%m-%d")
            except Exception:
                return m.group(0)
    return ""

def parse_customer(text: str) -> str:
    # "To:" 라인에서 회사명 우선
    m = re.search(r"\bTo:\s*([^\n]+)", text, re.I)
    if m:
        return N(m.group(1))
    # fallback: Co., Ltd.
    m = re.search(r"([A-Za-z0-9 ,.&()\-]+Co\., Ltd\.)", text)
    return N(m.group(1)) if m else ""

def parse_planner(text: str) -> str:
    m = re.search(r"\bFrom:\s*([^\n]+)", text, re.I)
    if m:
        return N(m.group(1))
    # fallback
    return "Sherry Liu" if "Sherry Liu" in text else ""

def money_to_float(s: str) -> Optional[float]:
    if not s:
        return None
    m = re.search(r"\$?\s*([\d,]+(?:\.\d{2})?)", s)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except Exception:
        return None

def is_sample_token(s: str) -> bool:
    return bool(re.search(r"\bSample\b", s, re.I))

def is_nre_token(s: str) -> bool:
    return bool(re.search(r"\bNRE\s*List\b", s, re.I))

# ---------------- spec splitting (Product cell) ----------------

def split_product_cell(product_cell: str) -> Tuple[str, str, str, str]:
    """
    Product 칸에서
    - Product name
    - Rated Current
    - Cable Length
    - Description(나머지 스펙)
    분리

    규칙:
    - Rated Current / Cable Length는 별도 컬럼으로 분리
    - Description에는 그 외 스펙만
    - "Cable Length" 아래에 붙는 내용도 Description으로 포함
    """
    raw = product_cell or ""
    lines = [N(x) for x in raw.split("\n") if N(x)]
    if not lines:
        return "", "", "", ""

    # 첫 줄이 품목명인 경우가 대부분
    product_name = lines[0]
    rest = lines[1:]

    rated = ""
    cable = ""
    desc_parts: List[str] = []

    # bullet/스펙 라인 파싱
    for ln in rest:
        ln2 = ln.lstrip("-•").strip()
        if re.search(r"Rated\s*Current", ln2, re.I):
            v = re.split(r":", ln2, maxsplit=1)
            rated = N(v[1]) if len(v) == 2 else N(ln2)
            continue
        if re.search(r"Cable\s*Length", ln2, re.I):
            v = re.split(r":", ln2, maxsplit=1)
            cable = N(v[1]) if len(v) == 2 else N(ln2)
            continue

        # Rated/Cable이 아니면 Description으로
        if ln2:
            desc_parts.append(ln2)

    # Description에는 Rated/Cable 반복 금지
    desc = "; ".join([p for p in desc_parts if p])

    return product_name, rated, cable, desc

# ---------------- table normalization ----------------

def normalize_table(table: List[List[Optional[str]]]) -> List[List[str]]:
    out = []
    for row in table:
        out.append([N(c or "") for c in row])
    return out

def find_header_row(rows: List[List[str]]) -> Optional[int]:
    """
    Item / Product / Delivery Term / MOQ / Unit Price / L/T 같은 헤더 찾기
    """
    for i, r in enumerate(rows):
        joined = " ".join(r).lower()
        if ("item" in joined and "product" in joined and "moq" in joined and ("unit price" in joined or "price" in joined)):
            return i
    return None

def repeat_merged_down(rows: List[List[str]]) -> List[List[str]]:
    """
    병합된 부분(빈칸)을 위 행 값으로 채우기(컬럼 단위)
    """
    if not rows:
        return rows
    cols = max(len(r) for r in rows)
    prev = [""] * cols
    out = []
    for r in rows:
        rr = r + [""] * (cols - len(r))
        filled = []
        for j in range(cols):
            v = rr[j]
            if v == "":
                v = prev[j]
            filled.append(v)
        out.append(filled)
        prev = filled
    return out

def detect_col_idx(header: List[str]) -> Dict[str, int]:
    """
    헤더에서 각 컬럼 인덱스 추정
    """
    idx = {}
    for j, c in enumerate(header):
        cl = c.lower()
        if "item" == cl or cl.startswith("item"):
            idx["item"] = j
        if "product" in cl:
            idx["product"] = j
        if "delivery" in cl:
            idx["delivery"] = j
        if "moq" in cl or "qty" in cl:
            idx["moq"] = j
        if "unit price" in cl or (cl == "price"):
            idx["price"] = j
        if "l/t" in cl or "lt" == cl or "wks" in cl:
            idx["lt"] = j
        if "remark" in cl:
            idx["remark"] = j
    return idx

# ---------------- main parsing ----------------

def parse_quote_pdf(extracted_text: str, extracted_tables: List[List[List[str]]]) -> List[Dict]:
    """
    범용(표 기반) 변환:
    - 표에서 Item row들을 읽고
    - Product cell에서 Rated/Cable/Description 분리
    - Delivery Term / MOQ / Price / LT를 row별로 구성
    - 조건(샘플/NRE/Amount 제거 등) 반영
    """
    text = extracted_text or ""
    date = parse_date(text)
    customer = parse_customer(text)
    planner = parse_planner(text)

    # 표 후보들을 돌면서 "Quotation 메인 표" 찾기
    best_rows = None
    for tb in extracted_tables or []:
        rows = normalize_table(tb)
        hidx = find_header_row(rows)
        if hidx is None:
            continue
        body = rows[hidx+1:]
        if len(body) >= 1:
            best_rows = rows[hidx:]  # header 포함
            break

    if not best_rows:
        return []

    header = best_rows[0]
    body = best_rows[1:]

    # 병합칸 반복 채우기
    body = repeat_merged_down(body)

    col = detect_col_idx(header)

    # 필수 컬럼 없으면 실패
    if "product" not in col or "moq" not in col or "price" not in col:
        return []

    out: List[Dict] = []

    # 표 밖에서 공통 LT / 공통 Delivery Term이 따로 있을 수도 있으니 fallback 준비
    global_lt = ""
    m = re.search(r"\b(\d{1,2})\s*-\s*(\d{1,2})\b", text)
    if m:
        global_lt = add_wks(f"{m.group(1)}-{m.group(2)}")

    global_delivery = ""
    md = re.search(r"\bFOB\b\s*[A-Za-z]+", text, re.I)
    if md:
        global_delivery = N(md.group(0))

    for r in body:
        product_cell = r[col["product"]] if col["product"] < len(r) else ""
        moq_cell = r[col["moq"]] if col["moq"] < len(r) else ""
        price_cell = r[col["price"]] if col["price"] < len(r) else ""

        delivery_cell = r[col["delivery"]] if ("delivery" in col and col["delivery"] < len(r)) else ""
        lt_cell = r[col["lt"]] if ("lt" in col and col["lt"] < len(r)) else ""
        remark_cell = r[col["remark"]] if ("remark" in col and col["remark"] < len(r)) else ""

        # 빈 row 스킵
        if not any([product_cell, moq_cell, price_cell, delivery_cell, lt_cell]):
            continue

        # Amount는 애초에 컬럼을 안 쓰므로 제거 조건 충족

        product_name, rated, cable, desc = split_product_cell(product_cell)

        # Delivery Term
        delivery = delivery_cell or global_delivery

        # L/T
        lt = add_wks(lt_cell) if lt_cell else global_lt

        # MOQ 처리
        remark_add = ""
        moq_val: Optional[int] = None

        # MOQ가 "Sample"이면 MOQ=1, Remark에 Sample
        if is_sample_token(moq_cell):
            moq_val = 1
            remark_add = "Sample"
        else:
            # 숫자만 추출
            mqty = re.search(r"\b(\d+)\b", moq_cell)
            moq_val = int(mqty.group(1)) if mqty else None

        # 가격
        price = money_to_float(price_cell)

        # NRE List special
        # - Delivery Term에 NRE List 표시
        # - MOQ = Qty
        # - Unit Price만 사용
        # - Product 칸에 Description, Cavity 정보는 Description 포함
        if is_nre_token(delivery_cell) or is_nre_token(product_cell) or is_nre_token(text):
            delivery = "NRE List"
            # Product를 “설명성 텍스트”로 두는 경우가 많아서, product_name이 빈/의미없으면 raw를 넣음
            if not product_name:
                product_name = N(product_cell)
            # Cavity 같은 키워드가 있으면 Description으로
            cav = ""
            mcav = re.search(r"\bCavity\s*\d+\b", product_cell, re.I)
            if mcav:
                cav = mcav.group(0)
            if cav and cav not in desc:
                desc = (desc + "; " + cav).strip("; ").strip()
            # MOQ는 Qty에서
            if moq_val is None:
                moq_val = 1

        # Remark 합치기
        remark = "; ".join([x for x in [remark_cell, remark_add] if N(x)])

        # MOQ/Price가 없는 row는 스킵
        if moq_val is None or price is None:
            continue

        out.append({
            "Date": date,
            "Customer": customer,
            "Planner": planner,
            "Product": product_name,
            "Rated Current": rated,
            "Cable Length": cable,
            "Description": desc,
            "Delivery Term": delivery,
            "MOQ": moq_val,
            "Price": price,
            "L/T": lt,
            "Remark": remark,
        })

    return out
