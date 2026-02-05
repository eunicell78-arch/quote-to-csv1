import re
from typing import List, Dict, Tuple, Optional, Any
import pdfplumber
from dateutil import parser as dateparser

VERSION = "v2026-02-05-quote-layout-v1"

OUT_COLS = [
    "Date", "Customer", "Planner", "Product",
    "Rated Current", "Cable Length", "Description",
    "Delivery Term", "MOQ", "Price", "L/T", "Remark"
]


# ---------------- utils ----------------

def N(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def add_wks(lt: str) -> str:
    lt = N(lt)
    if not lt:
        return ""
    if re.search(r"\bwks\b", lt, re.I):
        return lt
    m = re.search(r"\b(\d{1,2})\s*-\s*(\d{1,2})\b", lt)
    if m:
        return f"{m.group(1)}-{m.group(2)}wks"
    return lt

def money_to_float(s: str) -> Optional[float]:
    if not s:
        return None
    m = re.search(r"\$?\s*([\d,]+(?:\.\d{2})?)", s)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except:
        return None

def parse_date_from_text(t: str) -> str:
    for p in [
        r"\d{1,2}-[A-Za-z]{3}-\d{2}",
        r"[A-Za-z]{3}\.? \d{1,2}, \d{4}",
        r"\d{4}-\d{2}-\d{2}",
    ]:
        m = re.search(p, t)
        if m:
            try:
                d = dateparser.parse(m.group(0), fuzzy=True)
                return d.strftime("%Y-%m-%d")
            except:
                return m.group(0)
    return ""

def parse_to_from(t: str) -> Tuple[str, str]:
    cust = ""
    planner = ""

    m = re.search(r"\bTo:\s*([^\n]+)", t, re.I)
    if m:
        cust = N(m.group(1))

    m = re.search(r"\bFrom:\s*([^\n]+)", t, re.I)
    if m:
        planner = N(m.group(1))

    return cust, planner

def is_sample_token(s: str) -> bool:
    return bool(re.search(r"\bSample\b", s, re.I))

def is_nre_token(s: str) -> bool:
    return bool(re.search(r"\bNRE\s*List\b", s, re.I))

def strip_bullet(s: str) -> str:
    return N(re.sub(r"^[\-\u2022•]+", "", (s or "").strip()))

def cluster_lines(words: List[dict], y_tol: float = 3.0) -> List[List[dict]]:
    """
    같은 셀 안에서 y(top)가 비슷한 단어들을 한 줄로 묶음
    """
    if not words:
        return []
    ws = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines: List[List[dict]] = []
    for w in ws:
        if not lines:
            lines.append([w])
            continue
        if abs(w["top"] - lines[-1][0]["top"]) <= y_tol:
            lines[-1].append(w)
        else:
            lines.append([w])
    return lines

def words_to_text(words: List[dict]) -> str:
    """
    단어들을 줄 단위로 합쳐서 텍스트 생성 (줄바꿈 포함)
    """
    lines = cluster_lines(words, y_tol=3.0)
    out_lines = []
    for ln in lines:
        ln_sorted = sorted(ln, key=lambda w: w["x0"])
        out_lines.append(N(" ".join(w["text"] for w in ln_sorted)))
    return "\n".join([x for x in out_lines if x])


# ---------------- Product cell splitting (당신 규칙) ----------------

def split_product_cell_by_rules(product_cell_text: str) -> Tuple[str, str, str, str]:
    """
    조건:
    4) Product= Rated current 기준으로 윗줄
    5) Rated Current= Product칸의 Rated Current
    6) Cable Length= Product칸의 Cable Length
    7) Description= Cable length 아래 내용
    e) Rated/Cable은 Description에 반복 금지
    """
    raw = product_cell_text or ""
    lines = [strip_bullet(x) for x in raw.split("\n") if strip_bullet(x)]
    if not lines:
        return "", "", "", ""

    rated_idx = None
    cable_idx = None
    rated_val = ""
    cable_val = ""

    for i, ln in enumerate(lines):
        if re.search(r"Rated\s*Current", ln, re.I):
            rated_idx = i
            parts = ln.split(":", 1)
            rated_val = N(parts[1]) if len(parts) == 2 else N(ln)
        if re.search(r"Cable\s*Length", ln, re.I):
            cable_idx = i
            parts = ln.split(":", 1)
            cable_val = N(parts[1]) if len(parts) == 2 else N(ln)

    product_name = ""
    if rated_idx is not None and rated_idx - 1 >= 0:
        product_name = lines[rated_idx - 1]
    else:
        # fallback: 첫 줄
        product_name = lines[0]

    # Description: cable length 아래 라인들만
    desc_parts: List[str] = []
    if cable_idx is not None:
        after = lines[cable_idx + 1:]
        for ln in after:
            # Rated/Cable 반복 제거
            if re.search(r"Rated\s*Current", ln, re.I):
                continue
            if re.search(r"Cable\s*Length", ln, re.I):
                continue
            if ln:
                desc_parts.append(ln)
    else:
        # cable length 없으면 rated 아래를 desc로
        start = (rated_idx + 1) if rated_idx is not None else 1
        for ln in lines[start:]:
            if re.search(r"Rated\s*Current|Cable\s*Length", ln, re.I):
                continue
            desc_parts.append(ln)

    desc = "; ".join([x for x in desc_parts if x])
    return product_name, rated_val, cable_val, desc


# ---------------- Table reconstruction by coordinates ----------------

def find_header_y_and_columns(words: List[dict]) -> Tuple[Optional[float], Optional[float], Optional[Dict[str, Tuple[float, float]]]]:
    """
    헤더 키워드들(Item, Product, Delivery, MOQ, Unit Price, L/T, Remark) 위치로
    표 영역과 컬럼 x-range 추정
    """
    # 키워드 후보(대소문자 변형 대응)
    keys = {
        "item": ["item"],
        "product": ["product"],
        "delivery": ["delivery", "term"],
        "moq": ["moq", "qty"],
        "price": ["unit", "price"],
        "lt": ["l/t", "wks", "weeks", "lt"],
        "remark": ["remark"],
    }

    # 헤더 단어들을 찾고, 같은 y대(줄)에 있는지 판단
    candidates = []
    for w in words:
        t = w["text"].lower()
        for k, toks in keys.items():
            if t in toks:
                candidates.append((k, w))
                break

    if not candidates:
        return None, None, None

    # 가장 많이 겹치는 y대를 헤더로 선택
    # y를 5pt 단위로 버킷팅
    buckets: Dict[int, List[Tuple[str, dict]]] = {}
    for k, w in candidates:
        b = int(w["top"] // 5)
        buckets.setdefault(b, []).append((k, w))

    # 버킷 중에서 item+product+moq+price 조합이 있는 것을 우선
    def score(bucket_items):
        present = {k for k, _ in bucket_items}
        base = 0
        for need in ["item", "product", "moq", "price"]:
            if need in present:
                base += 2
        for opt in ["delivery", "lt", "remark"]:
            if opt in present:
                base += 1
        return base

    best_bucket = max(buckets.items(), key=lambda kv: score(kv[1]))[0]
    header_items = buckets[best_bucket]

    header_top = min(w["top"] for _, w in header_items)
    header_bottom = max(w["bottom"] for _, w in header_items)

    # 각 키의 x 위치 추정(왼쪽 x0)
    x_positions: Dict[str, float] = {}
    for k, w in header_items:
        # 'unit'과 'price'가 분리되어 있을 수 있으니 price는 둘 다 있을 때 더 왼쪽 사용
        if k not in x_positions:
            x_positions[k] = w["x0"]
        else:
            x_positions[k] = min(x_positions[k], w["x0"])

    # 최소 필요 컬럼
    if "product" not in x_positions or "moq" not in x_positions or "price" not in x_positions:
        return None, None, None

    # 컬럼 순서대로 정렬하여 range 구성
    # 누락된 컬럼은 나중에 None 처리 가능
    order = ["item", "product", "delivery", "moq", "price", "lt", "remark"]
    xs = [(k, x_positions[k]) for k in order if k in x_positions]
    xs.sort(key=lambda kv: kv[1])

    # range는 인접 컬럼의 중간값으로 자름
    col_ranges: Dict[str, Tuple[float, float]] = {}
    for i, (k, x) in enumerate(xs):
        left = x - 2
        right = (xs[i + 1][1] - 2) if i + 1 < len(xs) else 1e9
        if i > 0:
            left = (xs[i - 1][1] + x) / 2
        if i + 1 < len(xs):
            right = (x + xs[i + 1][1]) / 2
        col_ranges[k] = (left, right)

    return header_bottom, header_top, col_ranges


def collect_table_region_words(words: List[dict], header_bottom: float) -> List[dict]:
    # 헤더 아래 단어들
    return [w for w in words if w["top"] > header_bottom + 1]


def find_notes_top(words: List[dict]) -> Optional[float]:
    for w in words:
        if w["text"].strip().lower().startswith("notes"):
            return w["top"]
    return None


def extract_rows_by_item_anchor(table_words: List[dict], col_ranges: Dict[str, Tuple[float, float]], notes_top: Optional[float]) -> List[Dict[str, str]]()_
