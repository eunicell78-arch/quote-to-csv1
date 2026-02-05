import re
from typing import List, Dict, Tuple
from dateutil import parser as dateparser

VERSION = "v2026-02-05-sample3termfix3"

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


def _extract_price_lt_from_match(m) -> Tuple[float, str] | Tuple[None, None]:
    price = float(m.group(1).replace(",", ""))
    a = int(m.group(2))
    b = int(m.group(3))
    if not _valid_lt(a, b):
        return None, None
    return price, f"{a}-{b}"


# ---------------- SAMPLE ----------------

def is_sample(t: str) -> bool:
    return bool(re.search(r"\bSample\b", t, re.I))


def _find_term_price_lt(flat: str, anchor_pat: str) -> Tuple[float, str] | Tuple[None, None]:
    """
    anchor_pat(운임 키워드)을 찾은 뒤, 그 '근처'에서 $price + lt 를 찾는다.
    문구가 쪼개져도 되도록 anchor_pat은 느슨하게 작성.
    """
    am = re.search(anchor_pat, flat, flags=re.I)
    if not am:
        return None, None

    tail = flat[am.end(): am.end() + 260]

    # $는 없어도 되게
    pm = re.search(r"\$?\s*([\d,]+\.\d{2})\s+(\d{1,2})\s*-\s*(\d{1,2})", tail)
    if not pm:
        return None, None

    return _extract_price_lt_from_match(pm)


def parse_sample(t: str) -> List[Tuple[str, int, float, str]]:
    f = FLAT(t)

    # (1) 1줄형 샘플: "1 ... FOB SH ... Sample ... $xxx.xx 4-6"
    m = re.search(
        r"\b1\b.*?\bFOB\s*SH\b.*?\bSample\b.*?\$?\s*([\d,]+\.\d{2})\s+(\d{1,2})\s*-\s*(\d{1,2})\b",
        f, re.I
    )
    if m:
        price, lt = _extract_price_lt_from_match(m)
        if price is not None:
            return [("FOB SH", 1, price, lt)]

    rows: Dict[str, Tuple[str, int, float, str]] = {}

    # (2) 3줄형 샘플: 문구가 심하게 쪼개져도 잡히도록 "근접 키워드" 기반으로 탐색
    # FOB: "FOB" + "SH"
    price, lt = _find_term_price_lt(f, r"\bFOB\b\s*SH\b")
    if price is not None:
        rows["FOB SH"] = ("FOB SH", 1, price, lt)

    # SEA/FERRY: "SEA" 근처에 "FERRY"가 함께 등장하는 구간을 anchor로
    # DAP KR BY 가 있으면 더 좋지만 없을 수도 있어서 SEA+FERRY 위주로 찾음
    price, lt = _find_term_price_lt(f, r"\bSEA\b(?:.{0,40})\bFERRY\b")
    if price is not None:
        rows["DAP KR BY SEA/FERRY"] = ("DAP KR BY SEA/FERRY", 1, price, lt)

    # AIR: "DAP KR BY" 근처에 AIR가 등장하는 구간을 우선, 없으면 AIR 단독으로 보조
    price, lt = _find_term_price_lt(f, r"\bDAP\b(?:.{0,30})\bKR\b(?:.{0,30})\bBY\b(?:.{0,30})\bAIR\b")
    if price is not None:
        rows["DAP KR BY AIR"] = ("DAP KR BY AIR", 1, price, lt)
    else:
        price, lt = _find_term_price_lt(f, r"\bAIR\b")
        if price is not None:
            rows["DAP KR BY AIR"] = ("DAP KR BY AIR", 1, price, lt)

    # 표준 순서로 반환
    ordered: List[Tuple[str, int, float, str]] = []
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
        a = int(a)
        b = int(b)
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
