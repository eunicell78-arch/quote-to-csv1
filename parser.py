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

def get_customer_planner(t: str):

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


def get_product_specs(t: str):

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


# ---------------- SAMPLE ----------------

def is_sample(t: str):
    return bool(re.search(r"\bSample\b", t, re.I))


def parse_sample(t: str):

    f = FLAT(t)
    rows = []

    # Case 1: 1 FOB SH Sample $535.62 4-6
    m = re.search(
        r"\b1\s+FOB SH\s+.*?\bSample\b\s+\$([\d,]+\.\d{2})\s+(\d{1,2}-\d{1,2})\b",
        f, re.I
    )
    if m:
        rows.append(("FOB SH", 1, float(m.group(1).replace(",", "")), m.group(2)))
        return rows

    # Case 2: FOB / SEA / AIR blocks
    patterns = [
        ("FOB SH", r"\bFOB SH\s+\$([\d,]+\.\d{2})\s+(\d{1,2}-\d{1,2})\b"),
        ("DAP KR BY SEA/FERRY", r"\bSEA/FERRY\s+\$([\d,]+\.\d{2})\s+(\d{1,2}-\d{1,2})\b"),
        ("DAP KR BY AIR", r"\bAIR\s+\$([\d,]+\.\d{2})\s+(\d{1,2}-\d{1,2})\b"),
    ]

    for term, p in patterns:
        m = re.search(p, f, re.I)
        if m:
            rows.append((term, 1, float(m.group(1).replace(",", "")), m.group(2)))

    return rows


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

    # ✅ L/T는 "작은 숫자 범위"만 허용해서 전화번호(8640-4098 등) 제외
    # 허용 범위: 1~30 (필요시 40까지 늘려도 됨)
    raw_lts = re.findall(r"\b(\d{1,2})\s*-\s*(\d{1,2})\b", t)
    lts = []
    for a, b in raw_lts:
        a, b = int(a), int(b)
        if 1 <= a <= 30 and 1 <= b <= 30:
            lt = f"{a}-{b}"
            if lt not in lts:
                lts.append(lt)

    # 관측된 템플릿 보정(FOB=첫번째, SEA=두번째, AIR=첫번째)
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
    out = []

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
