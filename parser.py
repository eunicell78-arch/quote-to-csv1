import re
import pdfplumber
from dateutil import parser as dateparser

VERSION = "v2026-02-05-sinbon-v3-fixedheader-productspec"

OUT_COLS = [
    "Date", "Customer", "Planner", "Product",
    "Rated Current", "Cable Length", "Description",
    "Delivery Term", "MOQ", "Price", "L/T", "Remark"
]

# ---------------- helpers ----------------

def N(s):
    return re.sub(r"\s+", " ", (s or "").strip())

def strip_bullet(s):
    return N(re.sub(r"^[\-\u2022•]+", "", (s or "").strip()))

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

def parse_date_any(s):
    # Date: Oct. 14, 2025 / Sep. 22, 2025 / 2025-10-14 등
    for p in [
        r"[A-Za-z]{3}\.?\s+\d{1,2},\s+\d{4}",
        r"\d{4}-\d{2}-\d{2}",
        r"\d{1,2}-[A-Za-z]{3}-\d{2}",
    ]:
        m = re.search(p, s)
        if m:
            try:
                d = dateparser.parse(m.group(0), fuzzy=True)
                return d.strftime("%Y-%m-%d")
            except:
                return m.group(0)
    return ""

def first_nonempty_after_label(lines, label):
    """
    표 형식에서:
      To:   (다음 줄에 값)
    또는
      To: Daeyoung ...
    둘 다 지원
    """
    lab_re = re.compile(rf"^\s*{re.escape(label)}\s*:\s*(.*)$", re.I)
    for i, ln in enumerate(lines):
        m = lab_re.match(ln)
        if m:
            tail = N(m.group(1))
            if tail:
                # 같은 줄에 값
                return tail
            # 다음 줄(들)에서 값 찾기
            for j in range(i + 1, min(i + 4, len(lines))):
                v = N(lines[j])
                if v and not re.match(r"^(To|From|Attn|CC|Date|Ref)\s*:", v, re.I):
                    return v
    return ""

def parse_header_fields(text):
    """
    ✅ 조건 1~3의 핵심: Date/To/From은 고정 라벨에서만 뽑는다.
    """
    lines = [N(x) for x in (text or "").splitlines() if N(x)]

    customer = first_nonempty_after_label(lines, "To")
    planner  = first_nonempty_after_label(lines, "From")

    # Date는 "Date:" 라벨에서 우선 찾고, 못 찾으면 전체 텍스트에서 날짜 패턴 탐색
    date_raw = first_nonempty_after_label(lines, "Date")
    date_val = parse_date_any(date_raw) if date_raw else parse_date_any(text)

    return date_val, customer, planner

# ---------------- Product cell parsing by your rules ----------------

def parse_product_spec_from_block(block_lines):
    """
    조건 4~8:
    4.Product = Rated Current 기준 윗줄
    5.Rated Current = 'Rated Current:' 값
    6.Cable Length  = 'Cable Length:' 값
    7.Description   = Cable Length 아래 내용
    8. Rated/Cable은 Description에 반복 금지
    """
    clean = [strip_bullet(x) for x in block_lines if strip_bullet(x)]
    if not clean:
        return "", "", "", ""

    rated_idx = None
    cable_idx = None
    rated = ""
    cable = ""

    for i, ln in enumerate(clean):
        if re.search(r"Rated\s*Current", ln, re.I):
            rated_idx = i
            parts = ln.split(":", 1)
            rated = N(parts[1]) if len(parts) == 2 else ""
        if re.search(r"Cable\s*Length", ln, re.I):
            cable_idx = i
            parts = ln.split(":", 1)
            cable = N(parts[1]) if len(parts) == 2 else ""

    # Product = rated 기준 윗줄(없으면 첫 줄)
    if rated_idx is not None and rated_idx - 1 >= 0:
        product = clean[rated_idx - 1]
    else:
        product = clean[0]

    # Description = cable 아래
    desc_parts = []
    if cable_idx is not None:
        for ln in clean[cable_idx + 1:]:
            if re.search(r"Rated\s*Current|Cable\s*Length", ln, re.I):
                continue
            # 가격표/모큐 같은 숫자열 섞임 방지(아주 중요)
            if re.search(r"\$\s*[\d,]+\.\d{2}", ln) or re.fullmatch(r"\d+(?:,\d+)?", ln):
                continue
            desc_parts.append(ln)
    else:
        # cable이 없으면 rated 아래를 desc로
        start = rated_idx + 1 if rated_idx is not None else 1
        for ln in clean[start:]:
            if re.search(r"Rated\s*Current|Cable\s*Length", ln, re.I):
                continue
            if re.search(r"\$\s*[\d,]+\.\d{2}", ln):
                continue
            desc_parts.append(ln)

    desc = "; ".join([x for x in desc_parts if x])
    return product, rated, cable, desc

# ---------------- Main table parsing (text-based, robust) ----------------

def extract_text(pdf_file):
    with pdfplumber.open(pdf_file) as pdf:
        pages = [(p.extract_text() or "") for p in pdf.pages]
    return "\n".join(pages)

def split_item_blocks(text):
    """
    SINBON Quotation은 Item 1,2,3.. 또는 1-1/1-2/1-3 형태가 있음.
    - 우선 1-1/1-2/.. 를 블록으로 자르고,
    - 없으면 Item 숫자(1,2,3..)로 자른다.
    """
    lines = [x.rstrip() for x in (text or "").splitlines()]

    if re.search(r"\b\d+\-\d+\b", text):
        # 1-1, 1-2...
        chunks = re.split(r"(?m)^\s*(?=\d+\-\d+\b)", "\n".join(lines))
        blocks = []
        for c in chunks:
            c = c.strip("\n")
            if re.match(r"^\d+\-\d+\b", N(c)):
                blocks.append(c)
        return blocks

    # fallback: Item 1,2,3...
    chunks = re.split(r"(?m)^\s*(?=\d+\s*$)", "\n".join(lines))
    blocks = []
    for c in chunks:
        c = c.strip("\n")
        if re.match(r"^\d+\s*$", N(c)):
            blocks.append(c)
    return blocks

def parse_delivery_from_block(block_text):
    """
    Delivery Term은 FOB/DAP/EXW... + 뒤의 위치(Shanghai/Korea by Sea...)가 줄바꿈으로 찢어질 수 있음.
    -> FOB가 있으면 그 줄 + 다음 1~2줄을 이어붙인다 (L/T 숫자 나오기 전까지)
    """
    lines = [strip_bullet(x) for x in block_text.splitlines() if strip_bullet(x)]
    if not lines:
        return ""

    for i, ln in enumerate(lines):
        if re.search(r"\b(FOB|DAP|EXW|CIF|DDP)\b", ln, re.I):
            parts = [ln]
            for j in range(i + 1, min(i + 4, len(lines))):
                if re.search(r"\b\d{1,2}\s*-\s*\d{1,2}\b", lines[j]):
                    break
                # MOQ/가격 라인 방지
                if re.search(r"\$\s*[\d,]+\.\d{2}", lines[j]):
                    break
                parts.append(lines[j])
            return N(" ".join(parts))
    return ""

def parse_lt_from_block(block_text):
    # L/T(wks): 8-10 같은 패턴
    m = re.search(r"\b(\d{1,2}\s*-\s*\d{1,2})\b", block_text)
    return add_wks(m.group(1)) if m else ""

def parse_pricing_groups(text):
    """
    ✅ 핵심: Delivery Term별 MOQ/Unit Price/LT를 '그 칸에 있는 텍스트'처럼 읽어야 함.
    구현:
    - 텍스트를 위에서 아래로 스캔하면서 delivery term을 만나면 current_delivery를 갱신
    - 이후 나오는 (MOQ, $Price, LT) 패턴들을 해당 delivery에 귀속
    """
    lines = [N(x) for x in (text or "").splitlines() if N(x)]
    groups = {}  # delivery -> list of (moq, price, lt)

    current_delivery = ""
    for ln in lines:
        # delivery 후보 라인: FOB/DAP/EXW 포함, $가격은 없는 라인
        if re.search(r"\b(FOB|DAP|EXW|CIF|DDP)\b", ln, re.I) and not re.search(r"\$", ln):
            current_delivery = ln
            # 다음 줄에 위치만 있는 경우가 있어 groups 키는 나중에 합쳐짐
            groups.setdefault(current_delivery, [])
            continue

        # MOQ+Price+LT 한 줄 (예: 30 $511.88 8-10)
        m = re.search(r"\b(\d{1,6})\b\s+\$([\d,]+\.\d{2})(?:\s+(\d{1,2}\s*-\s*\d{1,2}))?", ln)
        if m and current_delivery:
            moq = int(m.group(1))
            price = money_to_float(m.group(2))
            lt = add_wks(m.group(3)) if m.group(3) else ""
            if price is not None:
                groups[current_delivery].append((moq, price, lt))

    # LT fill-down within each delivery group (병합 대응)
    fixed = {}
    for d, rows in groups.items():
        last_lt = ""
        out = []
        for moq, price, lt in rows:
            if lt:
                last_lt = lt
            out.append((moq, price, lt or last_lt))
        fixed[d] = out

    # delivery 문구가 "FOB" 한 단어로만 잡힌 경우가 있으니,
    # 인접한 delivery 키를 합치는 간단 보정(FOB + Shanghai 같은 줄분리 대응)
    # -> 실제론 parse_delivery_from_block이 더 정확하므로, 여기서는 그대로 두고
    #    item 블록의 delivery와 best-match로 매칭한다.
    return fixed

def best_match_delivery(item_delivery, delivery_keys):
    """
    item 블록에서 뽑은 delivery 문구(item_delivery)와
    pricing table에서 뽑은 delivery_keys 중 가장 비슷한 것을 선택.
    """
    if not item_delivery:
        return ""

    cand = ""
    score_best = -1
    it = item_delivery.lower()
    for k in delivery_keys:
        kk = k.lower()
        score = 0
        for token in ["fob", "dap", "exw", "cif", "ddp", "shanghai", "korea", "sea", "air", "ferry"]:
            if token in it and token in kk:
                score += 2
        # 공통 단어 수
        for w in set(it.split()):
            if w in kk:
                score += 1
        if score > score_best:
            score_best = score
            cand = k
    return cand

def parse_nre(text):
    """
    조건(d):
    - NRE List 있으면:
      Delivery Term='NRE List'
      MOQ=Qty
      Price=Unit Price
      Product=Item Description
      Description에 Cavity 포함
      Amount 제외
    """
    if not re.search(r"\bNRE\s*List\b", text, re.I):
        return None

    tail = re.split(r"\bNRE\s*List\b", text, flags=re.I)[-1]
    lines = [N(x) for x in tail.splitlines() if N(x)]

    # cavity
    cavity = ""
    for ln in lines[:20]:
        if re.fullmatch(r"\d+", ln):
            cavity = ln
            break

    # qty/unit price/amount/lt/remark가 같이 있는 라인 찾기
    # 예: "1 3 $3,000 $9,000 4-6 Necessary for MP"
    key = None
    for ln in lines:
        if re.search(r"\$\s*[\d,]+\s+\$\s*[\d,]+", ln):
            key = ln
            break
    if not key:
        return None

    m = re.search(r"\b(\d+)\s+(\d+)\s+\$([\d,]+)\s+\$([\d,]+)\s+(\d{1,2}\-\d{1,2})\s*(.*)$", key)
    if not m:
        return None

    qty = int(m.group(2))
    unit_price = float(m.group(3).replace(",", ""))
    lt = add_wks(m.group(5))
    remark = N(m.group(6))

    # description(아이템 설명) = key 라인 전까지의 텍스트 중 의미있는 부분
    # 헤더 단어 제거
    desc_lines = []
    for ln in lines:
        if ln == key:
            break
        if re.search(r"\b(Item|Description|Qty|Unit Price|Amount|Remark|L/T)\b", ln, re.I):
            continue
        desc_lines.append(ln)
    product_desc = N(" ".join(desc_lines)) or "NRE"

    description = f"Cavity {cavity}".strip() if cavity else ""

    return {
        "Product": product_desc,
        "Rated Current": "",
        "Cable Length": "",
        "Description": description,
        "Delivery Term": "NRE List",
        "MOQ": qty,
        "Price": unit_price,
        "L/T": lt,
        "Remark": remark
    }

# ---------------- entry ----------------

def parse_quote_file(file_obj):
    debug = {}

    text = extract_text(file_obj)

    # ✅ 1~3: header fields
    date, customer, planner = parse_header_fields(text)
    debug["date"] = date
    debug["customer"] = customer
    debug["planner"] = planner

    # Item blocks: product spec + delivery term + lt (from item area)
    item_blocks = split_item_blocks(text)
    debug["item_blocks"] = len(item_blocks)

    item_infos = []
    for blk in item_blocks:
        # 제품 스펙은 Rated Current/Cable Length가 나오는 구간만 사용
        lines = [x for x in blk.splitlines() if N(x)]
        # Rated Current가 포함된 주변 20줄만 잘라서 가격표 섞임 제거
        idxs = [i for i, ln in enumerate(lines) if re.search(r"Rated\s*Current", ln, re.I)]
        if idxs:
            i0 = idxs[0]
            cut = lines[max(0, i0 - 3): min(len(lines), i0 + 25)]
        else:
            cut = lines[:30]

        product, rated, cable, desc = parse_product_spec_from_block(cut)

        delivery = parse_delivery_from_block(blk)
        lt = parse_lt_from_block(blk)

        # item block에 제품 스펙이 없는 경우는 skip (예: 가격표만 있는 조각)
        if not (product or rated or cable or desc or delivery):
            continue

        item_infos.append({
            "product": product,
            "rated": rated,
            "cable": cable,
            "desc": desc,
            "delivery_item": delivery,
            "lt_item": lt
        })

    debug["item_infos"] = item_infos[:3]

    # Pricing groups by delivery term
    pricing = parse_pricing_groups(text)
    debug["pricing_keys"] = list(pricing.keys())[:6]

    out = []

    # ✅ 핵심: “Delivery Term별 MOQ별 row 생성”
    # item_infos 안의 delivery와 pricing의 delivery 키를 best-match로 연결
    for info in item_infos:
        item_delivery = info["delivery_item"]
        key = best_match_delivery(item_delivery, pricing.keys())

        # delivery term이 item에서만 잡히고 pricing이 없으면(표 누락) skip
        if not key or key not in pricing or not pricing[key]:
            continue

        for moq, price, lt in pricing[key]:
            remark = ""

            # Sample 처리 (조건 9/12): MOQ에 Sample이라고 적혀있으면 -> 여기서는 pricing가 숫자 기반이므로
            # Sample 견적서는 별도 케이스에서 처리됨(기존 샘플 형식). 필요시 확장 가능.
            moq_val = moq

            out.append({
                "Date": date,
                "Customer": customer,
                "Planner": planner,
                "Product": info["product"],
                "Rated Current": info["rated"],
                "Cable Length": info["cable"],
                "Description": info["desc"],
                "Delivery Term": N(key),
                "MOQ": moq_val,
                "Price": price,
                "L/T": lt or info["lt_item"],
                "Remark": remark
            })

    # NRE row append
    nre = parse_nre(text)
    debug["nre"] = bool(nre)
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
