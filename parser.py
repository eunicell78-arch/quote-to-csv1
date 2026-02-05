import re
from typing import List, Dict, Tuple
from dateutil import parser as dateparser

VERSION = "v2026-02-05-sample3termfix4"

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


def N(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def FLAT(t: str) -> str:
    return N(t.replace("\n", " ").replace("\r", " ").replace("\t", " "))


# ---------------- DATE ----------------

def get_date(t: str) -> str:
    for p in [
        r"\d{1,2}-[A-Za-z]{3}-\d{2}",
        r"[A-Za-z]{3}\.? \d{1,2}, \d{4}",
        r"\d{4}-\d{2}-\d{2}"
    ]:
        m = re.search(p, t)
        if m:
            try:
                d = dateparser.parse(m.group(0), fuzzy=True)
                return d.strftime("%Y-%m-%d")
            except:
                return m.group(0)
    return ""


# ---------------- CUSTOMER / PLANNER ----------------

def get_customer_planner(t: str) -> Tuple[str, str]:
    customer = ""
    for c in re.findall(r"[A-Za-z0-9 ,.&()\-]+Co\., Ltd\.", t):
        if "SINBON" not in c.upper() and "JIANGYIN" not in c.upper():
            customer = N(c)
            break

    planner = "Sherry Liu" if "Sherry Liu" in t else ""
    return customer, planner


# ---------------- PRODUCT / SPEC ----------------

def _clean_rated(x: str) -> str:
    m = re.search(r"\d+\s*A\s*/\s*\d+\s*A", x, re.I)
    if m:
        return m.group(0).replace(" ", "")
    m = re.search(r"\d+\s*A", x, re.I)
    if m:
        return m.group(0).replace(" ", "")
    return N(x)


def _clean_cable(x: str) -> str:
    m = re.search(r"\d+(?:\.\d+)?\s*M", x, re.I)
    if m:
        return m.group(0).replace(" ", "")
    return N(x)


def get_product_specs(t: str) -> Tuple[str, str, str, List[str]]:
    product = ""
    m = re.search(r"\b(NACS Charging Cable(?:_[A-Za-z0-9]+)?)\b", t)
    if m:
        product = N(m.group(1))

    rated = ""
    m = re.search(r"Rated\s*Current\s*:\s*([^\n\-•]+)", t, re.I)
    if m:
        rated = _clean_rated(m.group(1))

    cable = ""
    m = re.search(r"Cable\s*Length\s*:\s*([^\n\-•]+)", t, re.I)
    if m:
        cable = _clean_cable(m.group(1))

    desc = []
    m = re.search(r"Production\s*Site\s*:\s*([^\n\-•]+)", t, re.I)
    if m:
        desc.append("Production Site: " + N(m.group(1)))

    if re.search(r"KC\s*Certification", t, re.I):
        desc.append("KC Certification")

    return product, rated, cable, desc


# ---------------- LT FILTER (전화번호 방지) ----------------

def _valid_lt(a: int, b: int) -> bool:
    return 1 <= a <= 30 and 1 <= b <= 30


# ---------------- SAMPLE ----------------

def is_sample(t: str) -> bool:
    return bool(re.search(r"\bSample\b", t, re.I))


def parse_sample(t: str) -> List[Tuple[str, int, float, str]]:
    """
    핵심 아이디어:
    - PDF 텍스트는 term 앞/뒤로 가격이 뒤집혀 나올 수 있음
    - 그래서 term(FOB/SEA/AIR)의 위치를 잡고,
      가장 가까운 ($price + lt) 토큰을 '앞/뒤' 모두에서 매칭함
    """
    f = FLAT(t)

    # 1줄형 샘플(FOB만 있는 형태) 먼저 처리
    m = re.search(
        r"\b1\b.*?\bFOB\s*SH\b.*?\bSample\b.*?\$?\s*([\d,]+\.\d{2})\s+(\d{1,2})\s*-\s*(\d{1,2})\b",
        f, re.I
    )
    if m:
        price = float(m.group(1).replace(",", ""))
        a = int(m.group(2)); b = int(m.group(3))
        if _valid_lt(a, b):
            return [("FOB SH", 1, price, f"{a}-{b}")]

    # 가격+LT 토큰들을 전부 수집 (위치 포함)
    price_tokens = []
    for pm in re.finditer(r"\$?\s*([\d,]+\.\d{2})\s+(\d{1,2})\s*-\s*(\d{1,2})\b", f):
        price = float(pm.group(1).replace(",", ""))
        a = int(pm.group(2)); b = int(pm.group(3))
        if not _valid_lt(a, b):
            continue
        price_tokens.append((pm.start(), price, f"{a}-{b}"))

    if not price_tokens:
        return []

    # term 앵커 위치들
    term_anchors = {
        "FOB SH": [],
        "DAP KR BY SEA/FERRY": [],
        "DAP KR BY AIR": [],
    }

    for am in re.finditer(r"\bFOB\s*SH\b", f, re.I):
        term_anchors["FOB SH"].append(am.start())

    for am in re.finditer(r"\bSEA\s*/\s*FERRY\b", f, re.I):
        term_anchors["DAP KR BY SEA/FERRY"].append(am.start())

    # AIR는 단독 등장만으로는 오매칭 위험이 낮지만, 그래도 word boundary로 제한
    for am in re.finditer(r"\bAIR\b", f, re.I):
        term_anchors["DAP KR BY AIR"].append(am.start())

    def pick_nearest(anchor_positions: List[int]) -> Tuple[float, str] | Tuple[None, None]:
        if not anchor_positions:
            return None, None

        best = None  # (distance, price, lt)
        for a_pos in anchor_positions:
            for p_pos, price, lt in price_tokens:
                dist = abs(p_pos - a_pos)
                # 너무 멀면 엉뚱한 매칭이 될 수 있어서 제한 (넉넉하게 220)
                if dist > 220:
                    continue
                cand = (dist, price, lt)
                if best is None or cand[0] < best[0]:
                    best = cand

        if best is None:
            return None, None
        return best[1], best[2]

    rows = {}
    for term in ["FOB SH", "DAP KR BY SEA/FERRY", "DAP KR BY AIR"]:
        price, lt = pick_nearest(term_anchors[term])
        if price is not None and lt is not None:
            rows[term] = (term, 1, price, lt)

    ordered = []
    for term in ["FOB SH", "DAP KR BY SEA/FERRY", "DAP KR BY AIR"]:
        if term in rows:
            ordered.append(rows[term])

    return ordered


# ---------------- MASS ----------------

def parse_mass(t: str):
    pairs = re.findall(r"(\d+)\s+\$([\d,]+\.\d{2})", t)
    data = []
    for q, p in pairs:
        try:
            data.append((int(q), float(p.replace(",", ""))))
        except:
            pass

    raw_lts = re.findall(r"\b(\d{1,2})\s*-\s*(\d{1,2})\b", t)
    lts = []
    for a, b in raw_lts:
        a = int(a); b = int(b)
        if _valid_lt(a, b):
            lt = f"{a}-{b}"
            if lt not in lts:
                lts.append(lt)

    if len(lts) == 2:
        lts = [lts[0], lts[1], lts[0]]
    if len(lts) == 1:
        lts = [lts[0], lts[0], lts[0]]
    if not lts:
        lts = ["", "", ""]

    return data, lts[:3]


# ---------------- MAIN ----------------

def parse_sinbon_quote(text: str) -> List[Dict]:
    if not text:
        return []

    date = get_date(text)
    customer, planner = get_customer_planner(text)
    product, rated, cable, desc = get_product_specs(text)
    description = "; ".join(desc)

    out: List[Dict] = []

    if is_sample(text):
        rows = parse_sample(text)
        for term, q, p, lt in rows:
            out.append({
                "Date": date,
                "Customer": customer,
                "Planner": planner,
                "Product": product,
                "Rated Current": rated,
                "Cable Length": cable,
                "Description": description,
                "Delivery Term": term,
                "MOQ": q,
                "Price": p,
                "L/T": lt + "wks",
                "Remark": "",
            })
        return out

    data, lts = parse_mass(text)
    for i, term in enumerate(DELIVERY_TERMS):
        chunk = data[i*2:(i+1)*2]
        for q, p in chunk:
            out.append({
                "Date": date,
                "Customer": customer,
                "Planner": planner,
                "Product": product,
                "Rated Current": rated,
                "Cable Length": cable,
                "Description": description,
                "Delivery Term": term,
                "MOQ": q,
                "Price": p,
                "L/T": (lts[i] + "wks") if lts[i] else "",
                "Remark": "",
            })

    return out
