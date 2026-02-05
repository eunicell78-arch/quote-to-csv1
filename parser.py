import re
import pdfplumber
from dateutil import parser as dateparser

VERSION = "v2026-02-05-sinbon-quote-v2"

OUT_COLS = [
    "Date", "Customer", "Planner", "Product",
    "Rated Current", "Cable Length", "Description",
    "Delivery Term", "MOQ", "Price", "L/T", "Remark"
]

# ---------------- utils ----------------

def N(s):
    return re.sub(r"\s+", " ", (s or "").strip())

def add_wks(lt):
    lt = N(lt)
    if not lt:
        return ""
    if re.search(r"\bwks\b", lt, re.I):
        return lt
    m = re.search(r"\b(\d{1,2})\s*-\s*(\d{1,2})\b", lt)
    if m:
        return f"{m.group(1)}-{m.group(2)}wks"
    return lt

def money_to_float(s):
    if not s:
        return None
    m = re.search(r"\$?\s*([\d,]+(?:\.\d{2})?)", s)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except:
        return None

def parse_date(t):
    for p in [
        r"[A-Za-z]{3}\.? \d{1,2}, \d{4}",
        r"\d{4}-\d{2}-\d{2}",
        r"\d{1,2}-[A-Za-z]{3}-\d{2}",
    ]:
        m = re.search(p, t)
        if m:
            try:
                d = dateparser.parse(m.group(0), fuzzy=True)
                return d.strftime("%Y-%m-%d")
            except:
                return m.group(0)
    return ""

def parse_to_from_attn_line(t):
    """
    이 양식은 To/From/Attn/Date가 표에 있고, 실제 값은 아래쪽 한 줄에
    'Daeyoung ... Sherry Liu' / 'Cha Se Nyoung Oct. 14, 2025' 처럼 나오는 경우가 많음.
    """
    customer = ""
    planner = ""

    # 가장 흔한: "Daeyoung Chaevi Co., Ltd. Sherry Liu"
    m = re.search(r"\n([A-Za-z0-9 ,.&()\-]+Co\., Ltd\.)\s+([A-Za-z][A-Za-z .'-]+)\n", t)
    if m:
        customer = N(m.group(1))
        planner = N(m.group(2))

    return customer, planner

def strip_bullet(s):
    return N(re.sub(r"^[\-\u2022•]+", "", (s or "").strip()))

def is_sample_token(s):
    return bool(re.search(r"\bSample\b", s or "", re.I))

def is_nre_token(s):
    return bool(re.search(r"\bNRE\s*List\b", s or "", re.I))

# ---------------- Product block parsing (조건 4~8) ----------------

def split_product_block(lines):
    """
    조건:
    Product= Rated Current 기준 윗줄
    Rated Current / Cable Length 추출
    Description = Cable Length 아래 내용(나머지 스펙)
    """
    clean = [strip_bullet(x) for x in lines if strip_bullet(x)]
    if not clean:
        return "", "", "", ""

    rated_idx = None
    cable_idx = None
    rated_val = ""
    cable_val = ""

    for i, ln in enumerate(clean):
        if re.search(r"Rated\s*Current", ln, re.I):
            rated_idx = i
            parts = ln.split(":", 1)
            rated_val = N(parts[1]) if len(parts) == 2 else N(ln)
        if re.search(r"Cable\s*Length", ln, re.I):
            cable_idx = i
            parts = ln.split(":", 1)
            cable_val = N(parts[1]) if len(parts) == 2 else N(ln)

    if rated_idx is not None and rated_idx - 1 >= 0:
        product_name = clean[rated_idx - 1]
    else:
        product_name = clean[0]

    desc_parts = []
    if cable_idx is not None:
        for ln in clean[cable_idx + 1:]:
            if re.search(r"Rated\s*Current|Cable\s*Length", ln, re.I):
                continue
            desc_parts.append(ln)
    else:
        start = (rated_idx + 1) if rated_idx is not None else 1
        for ln in clean[start:]:
            if re.search(r"Rated\s*Current|Cable\s*Length", ln, re.I):
                continue
            desc_parts.append(ln)

    desc = "; ".join([x for x in desc_parts if x])
    return product_name, rated_val, cable_val, desc

# ---------------- Main parsing ----------------

def extract_text(pdf_file):
    with pdfplumber.open(pdf_file) as pdf:
        pages = [(p.extract_text() or "") for p in pdf.pages]
    return "\n".join(pages)

def parse_delivery_terms_and_lts(text):
    """
    1-1 / 1-2 / 1-3 블록에서 Delivery Term + LT를 뽑아 순서대로 반환
    """
    terms = []

    # "Product" 이후에 "1-1 ... FOB Shanghai 8-10" 같이 나오는 부분 사용
    m = re.search(r"\bProduct\b([\s\S]+?)(?:\bNRE\s*List\b|$)", text, re.I)
    if not m:
        return terms

    blk = m.group(1)

    # item id 기준 분리 (예: 1-1, 1-2, 1-3)
    parts = re.split(r"\n(?=\d+\-\d+\b)", "\n" + blk)
    for part in parts:
        part = part.strip()
        if not re.match(r"^\d+\-\d+\b", part):
            continue

        lines = [x.rstrip() for x in part.splitlines() if N(x)]
        # Delivery term: FOB/DAP/EXW 등으로 시작하는 줄들을 아래에서 위로 조립
        # 이 문서는 "FOB" 줄 다음에 "Shanghai" 줄 처럼 찢어져 있음
        dt = ""
        lt = ""

        # LT는 보통 마지막에 "8-10" 같은 숫자
        for ln in lines[::-1]:
            mlt = re.search(r"\b(\d{1,2}\s*-\s*\d{1,2})\b", ln)
            if mlt:
                lt = add_wks(mlt.group(1))
                break

        # Delivery term 단어 모으기: FOB / DAP ... 가 있는 위치부터 다음 1~3줄 합치기
        for i, ln in enumerate(lines):
            if re.search(r"\b(FOB|DAP|EXW|CIF|DDP)\b", ln, re.I):
                dt_words = [strip_bullet(ln)]
                # 다음 줄들 중 LT 숫자 나오기 전까지 붙임
                for j in range(i + 1, min(i + 5, len(lines))):
                    if re.search(r"\b\d{1,2}\s*-\s*\d{1,2}\b", lines[j]):
                        break
                    dt_words.append(strip_bullet(lines[j]))
                dt = N(" ".join([w for w in dt_words if w]))
                break

        if dt:
            terms.append({"delivery_term": dt, "lt": lt})

    return terms

def parse_moq_price_groups(text, expected_groups):
    """
    상단의 MOQ/가격 리스트를 30으로 시작하는 그룹 단위로 묶는다.
    각 그룹은 (MOQ, price, lt(optional)) row들.
    - LT는 표에서 병합되어 있을 수 있으니 그룹 내 fill-down
    """
    # "30 $511.88" 같은 라인들 뽑기 (줄바꿈이 어색해도 공백 기준)
    tokens = re.findall(r"\b(\d{1,3}(?:,\d{3})?)\s+\$([\d,]+\.\d{2})(?:\s+(\d{1,2}\s*-\s*\d{1,2}))?\b", text)
    rows = []
    for moq_s, price_s, lt_s in tokens:
        moq = int(moq_s.replace(",", ""))
        price = money_to_float(price_s)
        lt = add_wks(lt_s) if lt_s else ""
        rows.append((moq, price, lt))

    # 그룹화: MOQ=30을 새 그룹 시작으로
    groups = []
    cur = []
    for moq, price, lt in rows:
        if moq == 30 and cur:
            groups.append(cur)
            cur = []
        cur.append((moq, price, lt))
    if cur:
        groups.append(cur)

    # 기대 그룹 수에 맞추기(부족하면 그대로 반환)
    if expected_groups and len(groups) > expected_groups:
        groups = groups[:expected_groups]

    # 그룹별 lt fill-down
    fixed = []
    for g in groups:
        out = []
        last_lt = ""
        for moq, price, lt in g:
            if lt:
                last_lt = lt
            out.append((moq, price, lt or last_lt))
        fixed.append(out)

    return fixed

def parse_nre_list_item(text):
    """
    NRE List 아래에 있는 치구 1건 파싱(현재 파일 기준).
    규칙(d):
    - Delivery Term = NRE List
    - MOQ = Qty
    - Price = Unit Price
    - Product = Description(품목 설명)
    - Description = Cavity 포함
    - Amount 제거
    """
    if not re.search(r"\bNRE\s*List\b", text, re.I):
        return None

    # NRE List 이후부터 끝까지
    tail = re.split(r"\bNRE\s*List\b", text, flags=re.I)[-1]
    lines = [x.rstrip() for x in tail.splitlines() if N(x)]

    # 파일 구조(현재): 첫 줄에 cavity "1"만 나오고,
    # 다음 여러 줄이 description,
    # 마지막에 "1 3 $3,000 $9,000 4-6 Necessary for MP"
    cavity = ""
    desc_lines = []
    qty = None
    unit_price = None
    lt = ""
    remark = ""

    # cavity 후보: 단독 숫자 라인
    for i, ln in enumerate(lines[:10]):
        if re.fullmatch(r"\d+", N(ln)):
            cavity = N(ln)
            start = i + 1
            break
    else:
        start = 0

    # 마지막 핵심 라인 찾기(Amount 포함)
    key_idx = None
    for i in range(len(lines)-1, max(-1, len(lines)-30), -1):
        ln = lines[i]
        if re.search(r"\$\s*[\d,]+\s+\$\s*[\d,]+", ln):
            key_idx = i
            break

    if key_idx is None:
        return None

    # description은 start~key_idx-1
    desc_lines = lines[start:key_idx]
    desc_text = N(" ".join(desc_lines))

    # key line 파싱
    kl = N(lines[key_idx])
    # 예: "1 3 $3,000 $9,000 4-6 Necessary for MP"
    m = re.search(r"\b(\d+)\s+(\d+)\s+\$([\d,]+)\s+\$([\d,]+)\s+(\d{1,2}\-\d{1,2})\s*(.*)$", kl)
    if not m:
        return None

    # 첫 숫자는 item/cavity로 보이기도 해서 cavity가 비면 채움
    if not cavity:
        cavity = m.group(1)

    qty = int(m.group(2))
    unit_price = float(m.group(3).replace(",", ""))
    lt = add_wks(m.group(5))
    remark = N(m.group(6))

    return {
        "Product": desc_text,
        "Rated Current": "",
        "Cable Length": "",
        "Description": f"Cavity {cavity}".strip(),
        "Delivery Term": "NRE List",
        "MOQ": qty,
        "Price": unit_price,
        "L/T": lt,
        "Remark": remark
    }

def parse_quote_file(file_obj):
    debug = {}
    text = extract_text(file_obj)

    date = parse_date(text)
    customer, planner = parse_to_from_attn_line(text)

    debug["date"] = date
    debug["customer"] = customer
    debug["planner"] = planner

    # 1) Delivery term + LT (1-1/1-2/1-3 순서)
    terms = parse_delivery_terms_and_lts(text)
    debug["delivery_terms_found"] = terms

    # 2) MOQ/price 그룹 (delivery term 개수만큼)
    groups = parse_moq_price_groups(text, expected_groups=len(terms))
    debug["moq_price_groups"] = [len(g) for g in groups]

    out = []

    # 3) 메인 Product 상세(현재 파일은 1-1에만 제품 스펙이 있고 1-2/1-3도 동일 제품)
    #    -> Product 블록에서 첫 번째 제품 스펙만 추출해서 공통 적용
    product_name = ""
    rated = ""
    cable = ""
    desc = ""

    mprod = re.search(r"\b1\-1\b([\s\S]+?)(?:\b1\-2\b|\bNRE\s*List\b|$)", text)
    if mprod:
        lines = [x for x in mprod.group(1).splitlines() if N(x)]
        # 1-1 다음 첫 줄이 제품명인 경우가 많아서 앞부분 포함
        # (split_product_block이 Rated Current 기준 위줄을 제품명으로 잡음)
        product_name, rated, cable, desc = split_product_block(lines)

    debug["main_product"] = {"Product": product_name, "Rated": rated, "Cable": cable, "Desc": desc}

    # 4) DeliveryTerm별 MOQ별 row 생성 + 병합 LT fill-down
    #    - 그룹 수가 term 수보다 적으면 가능한 만큼만 생성
    for i, term in enumerate(terms):
        dt = term.get("delivery_term", "")
        base_lt = term.get("lt", "")

        if i >= len(groups):
            continue

        for moq, price, lt in groups[i]:
            if price is None:
                continue
            out.append({
                "Date": date,
                "Customer": customer,
                "Planner": planner,
                "Product": product_name,
                "Rated Current": rated,
                "Cable Length": cable,
                "Description": desc,
                "Delivery Term": dt,
                "MOQ": moq,
                "Price": price,
                "L/T": lt or base_lt,
                "Remark": ""
            })

    # 5) NRE List 품목(있으면 추가)
    nre = parse_nre_list_item(text)
    debug["nre_parsed"] = bool(nre)

    if nre:
        out.append({
            "Date": date,
            "Customer": customer,
            "Planner": planner,
            "Product": nre["Product"],
            "Rated Current": nre["Rated Current"],
            "Cable Length": nre["Cable Length"],
            "Description": nre["Description"],
            "Delivery Term": nre["Delivery Term"],
            "MOQ": nre["MOQ"],
            "Price": nre["Price"],
            "L/T": nre["L/T"],
            "Remark": nre["Remark"]
        })

    debug["out_count"] = len(out)
    return out, debug
