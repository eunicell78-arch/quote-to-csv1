import re
from typing import List, Dict, Tuple
from dateutil import parser as dateparser

VERSION = "v2026-02-05-sample3termfix"

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


def _find_price_lt_after(term_regex: str, flat: str) -> Tuple[float, str] | Tuple[None, None]:
    """
    term_regex가 등장한 '이후' 구간에서 가격과 L/T를 찾는다.
    줄바꿈으로 term이 쪼개져도 FLAT에서 term_regex를 유연하게 매칭.
    """
    m = re.search(term_regex, flat, flags=re.I)
    if not m:
        return None, None

    # term 다음부터 120자 안에서 가격 + lt 찾기 (너무 멀리 가면 오매칭 위험)
    tail = flat[m.end(): m.end() + 200]

    # $가 사라져도 잡히게: $는 optional
    pm = re.search(r"\$?\s*([\d,]+\.\d{2})\s+(\d{1,2})\s*-\s*(\d{1,2})", tail)
    if not pm:
        return None, None

    price = float(pm.group(1).replace(",", ""))
    a = int(pm.group(2)); b = int(pm.group(3))
    if not _valid_lt(a, b):
        return None, None

    return price, f"{a}-{b}"


def parse_sample(t: str) -> List[Tuple[str, int, float, str]]:
    f = FLAT(t)
    results: List[Tuple[str, int, float, str]] = []

    # (A) 1줄형 샘플: "1 FOB SH Sample $xxx.xx 4-6"
    m = re.search(
        r"\b1\s+FOB\s*SH\b.*?\bSample\b.*?\$?\s*([\d,]+\.\d{2})\s+(\d{1,2})\s*-\s*(\d{1,2})\b",
        f, re.I
    )
    if m:
        price = float(m.group(1).replace(",", ""))
        a = int(m.group(2)); b = int(m.group(3))
        if _valid_lt(a, b):
            return [("FOB SH", 1, price, f"{a}-{b}")]

    # (B) 3줄형 샘플: term별로 각각 탐색 (줄바꿈으로 쪼개져도 OK)
    # 핵심: "DAP KR BY"가 줄바꿈으로 분리돼도 잡히도록 \s* 허용
    term_patterns = [
        ("FOB SH", r"\bFOB\s*SH\b"),
        ("DAP KR BY SEA/FERRY", r"\bDAP\s*KR\s*BY\s*SEA\s*/\s*FERRY\b|\bSEA\s*/\s*FERRY\b"),
        ("DAP KR BY AIR", r"\bDAP\s*KR\s*BY\s*AIR\b|\bAIR\b"),
    ]

    for term, treg in term_patterns:
        price, lt = _find_price_lt_after(treg, f)
        if price is not None and lt is not None:
            # AIR 패턴이 너무 넓어서(주소의 AIR 같은 오매칭) term이 DAP KR BY AIR로 실제 존재할 때 우선
            if term == "DAP KR BY AIR":
                # 가능하면 DAP KR BY AIR를 먼저 찾고, 없으면 AIR 단독도 허용
                p2, lt2 = _find_price_lt_after(r"\bDAP\s*KR\s*BY\s*AIR\b", f)
                if p2 is not None and lt2 is not None:
                    price, lt = p2, lt2
            results.append((term, 1, price, lt))

    # 표준 순서로 정렬 + 중복 제거
    ordered = []
    seen = set()
    for term in ["FOB SH", "DAP KR BY SEA/FERRY", "DAP KR BY AIR"]:
        for r in results:
            if r[0] == term and term not in seen:
                ordered.append(r)
                seen.add(term)
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


