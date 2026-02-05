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
    # 200A/250A 형태 우선, 아니면 250A 같은 단일값
    m = re.search(r"\d+\s*A\s*/\s*\d+\s*A", x, re.I)
    if m:
        return m.group(0).replace(" ", "")
    m = re.search(r"\d+\s*A", x, re.I)
    if m:
        return m.group(0).replace(" ", "")
    return N(x)


def _clean_cable(x: str) -> str:
    # 3.5M / 4M / 6.5M / 7.62M 등만 남김
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


# ---------------- COMMON: LT FILTER (전화번호 방지) ----------------

def _valid_lt(a: int, b: int) -> bool:
    # 리드타임은 보통 1~30주 범위. (전화번호 8640-4098 같은 건 자동 제외)
    return 1 <= a <= 30 and 1 <= b <= 30


# ---------------- SAMPLE ----------------

def is_sample(t: str) -> bool:
    return bool(re.search(r"\bSample\b", t, re.I))


def parse_sample(t: str) -> List[Tuple[str, int, float, str]]:
    """
    샘플 견적은 구조가 흔들려도 아래 패턴을 '모두' 긁어오게 만듦.
    - 1줄형: 1 FOB SH Sample $535.62 4-6
    - 3줄형: FOB SH $... 4-6 / DAP KR BY SEA/FERRY $... 6-8 / DAP KR BY AIR $... 4-6
    - 줄바꿈/순서 뒤섞임 대응: FLAT 텍스트에서 finditer로 전부 수집
    """
    f = FLAT(t)

    # term 표준화 맵
    def canon_term(raw: str) -> str:
        r = N(raw).upper()
        if r == "FOB SH":
            return "FOB SH"
        if r in ["SEA/FERRY", "DAP KR BY SEA/FERRY"]:
            return "DAP KR BY SEA/FERRY"
        if r in ["AIR", "DAP KR BY AIR"]:
            return "DAP KR BY AIR"
        return r

    results: Dict[str, Tuple[str, int, float, str]] = {}

    # (1) term + $price + lt 를 통으로 전부 긁기 (여러개 가능)
    # DAP KR BY SEA/FERRY 문구가 일부만 나오기도 해서 (SEA/FERRY / AIR)도 허용
    pattern = re.compile(
        r"\b(FOB SH|DAP KR BY SEA/FERRY|DAP KR BY AIR|SEA/FERRY|AIR)\b"
        r"\s+\$([\d,]+\.\d{2})\s+(\d{1,2})\s*-\s*(\d{1,2})\b",
        re.I
    )

    for m in pattern.finditer(f):
        term = canon_term(m.group(1))
        price = float(m.group(2).replace(",", ""))
        a = int(m.group(3))
        b = int(m.group(4))
        if not _valid_lt(a, b):
            continue
        lt = f"{a}-{b}"
        results[term] = (term, 1, price, lt)

    # (2) 1줄형이 term/price/lt를 못 잡는 특이 케이스 대비:
    # "Sample $price lt"만 있으면 FOB로 가정
    if "FOB SH" in f.upper() and "SAMPLE" in f.upper() and not results:
        m = re.search(r"\bSample\b\s+\$([\d,]+\.\d{2})\s+(\d{1,2})\s*-\s*(\d{1,2})\b", f, re.I)
        if m:
            price = float(m.group(1).replace(",", ""))
            a = int(m.group(2))
            b = int(m.group(3))
            if _valid_lt(a, b):
                results["FOB SH"] = ("FOB SH", 1, price, f"{a}-{b}")

    # 표준 출력 순서로 정렬
    ordered = []
    for term in ["FOB SH", "DAP KR BY SEA/FERRY", "DAP KR BY AIR"]:
        if term in results:
            ordered.append(results[term])

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

    # LT 후보 전부 수집 후 1~30 범위만 남김(전화번호 제거)
    raw_lts = re.findall(r"\b(\d{1,2})\s*-\s*(\d{1,2})\b", t)
    lts = []
    for a, b in raw_lts:
        a = int(a); b = int(b)
        if _valid_lt(a, b):
            lt = f"{a}-{b}"
            if lt not in lts:
                lts.append(lt)

    # 관측 템플릿 보정: FOB=첫번째, SEA=두번째, AIR=첫번째
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
