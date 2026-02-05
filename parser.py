import re
from typing import List, Dict, Tuple
from dateutil import parser as dateparser

VERSION = "v2026-02-05-sample3termfix2"

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


def _extract_price_lt(m) -> Tuple[float, str] | Tuple[None, None]:
    price = float(m.group(1).replace(",", ""))
    a = int(m.group(2)); b = int(m.group(3))
    if not _valid_lt(a, b):
        return None, None
    return price, f"{a}-{b}"


def parse_sample(t: str) -> List[Tuple[str, int, float, str]]:
    """
    샘플 2종 지원:
    - 1줄형: 1 FOB SH Sample $535.62 4-6
    - 3줄형: FOB SH ... / DAP KR BY SEA/FERRY ... / DAP KR BY AIR ...
      (줄바꿈으로 DAP KR BY / AIR / $... 형태로 쪼개져도 FLAT에서 직접 매칭)
    """
    f = FLAT(t)

    # (1) 1줄형: '1 ... FOB SH ... Sample ... $price lt'
    m = re.search(
        r"\b1\s+.*?\bFOB\s*SH\b.*?\bSample\b.*?\$?\s*([\d,]+\.\d{2})\s+(\d{1,2})\s*-\s*(\d{1,2})\b",
        f, re.I
    )
    if m:
        price, lt = _extract_price_lt(m)
        if price is not None:
            return [("FOB SH", 1, price, lt)]

    # (2) 3줄형: term별로 직접 매칭 (가장 안정적)
    # term 뒤에 $가 다음 줄로 내려가도 FLAT에서는 공백으로 붙으므로 \s*로 처리 가능
    term_patterns = [
        ("FOB SH",
         r"\bFOB\s*SH\b\s*\$?\s*([\d,]+\.\d{2})\s+(\d{1,2})\s*-\s*(\d{1,2})\b"),
        ("DAP KR BY SEA/FERRY",
         r"\bDAP\s*KR\s*BY\s*SEA\s*/\s*FERRY\b\s*\$?\s*([\d,]+\.\d{2})\s+(\d{1,2})\s*-\s*(\d{1,2})\b"),
        ("DAP KR BY AIR",
         r"\bDAP\s*KR\s*BY\s*AIR\b\s*\$?\s*([\d,]+\.\d{2})\s+(\d{1,2})\s*-\s*(\d{1,2})\b"),
    ]

    out: List[Tuple[str, int, float, str]] = []
    for term, pat in term_patterns:
        m = re.search(pat, f, re.I)
        if not m:
            continue
        price, lt = _extract_price_lt(m)
        if price is None:
            continue
        out.append((term, 1, price, lt))

    # 표준 순서 유지
    ordered = []
    for term in ["FOB SH", "DAP KR BY SEA/FERRY", "DAP KR BY AIR"]:
        for r in out:
            if r[0] == term:
                ordered.append(r)
                break
    return ordered


# ---------------- MASS ----------------

def parse_mass(t: str):
    # MOQ + Price
    pairs = re.findall(r"(\d+)\s+\$([\d,]+\.\d{2})", t)
    data = []
    for q, p in pairs:
        try:
            data.append((int(q), float(p.replace(",", ""))))
        except:
            pass

    # LT 후보: 1~30만
    raw_lts = re.findall(r"\b(\d{1,2})\s*-\s*(\d{1,2})\b", t)
    lts = []
    for a, b in raw_lts:
        a = int(a); b = int(b)
        if _valid_lt(a, b):
            lt = f"{a}-{b}"
            if lt not in lts:
                lts.append(lt)

    # 템플릿 보정: FOB=첫번째, SEA=두번째, AIR=첫번째
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

    # Sample
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

    # Mass
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
