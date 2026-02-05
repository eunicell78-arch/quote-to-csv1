import re
import pdfplumber
from dateutil import parser as dateparser

VERSION = "v2026-02-05-quote-layout-v1a"

OUT_COLS = [
    "Date", "Customer", "Planner", "Product",
    "Rated Current", "Cable Length", "Description",
    "Delivery Term", "MOQ", "Price", "L/T", "Remark"
]

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

def parse_date_from_text(t):
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

def parse_to_from(t):
    cust = ""
    planner = ""
    m = re.search(r"\bTo:\s*([^\n]+)", t, re.I)
    if m:
        cust = N(m.group(1))
    m = re.search(r"\bFrom:\s*([^\n]+)", t, re.I)
    if m:
        planner = N(m.group(1))
    return cust, planner

def is_sample_token(s):
    return bool(re.search(r"\bSample\b", s, re.I))

def is_nre_token(s):
    return bool(re.search(r"\bNRE\s*List\b", s, re.I))

def strip_bullet(s):
    return N(re.sub(r"^[\-\u2022•]+", "", (s or "").strip()))

def cluster_lines(words, y_tol=3.0):
    if not words:
        return []
    ws = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines = []
    for w in ws:
        if not lines:
            lines.append([w])
            continue
        if abs(w["top"] - lines[-1][0]["top"]) <= y_tol:
            lines[-1].append(w)
        else:
            lines.append([w])
    return lines

def words_to_text(words):
    lines = cluster_lines(words, y_tol=3.0)
    out_lines = []
    for ln in lines:
        ln_sorted = sorted(ln, key=lambda w: w["x0"])
        out_lines.append(N(" ".join(w["text"] for w in ln_sorted)))
    return "\n".join([x for x in out_lines if x])

def split_product_cell_by_rules(product_cell_text):
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

    if rated_idx is not None and rated_idx - 1 >= 0:
        product_name = lines[rated_idx - 1]
    else:
        product_name = lines[0]

    desc_parts = []
    if cable_idx is not None:
        after = lines[cable_idx + 1:]
        for ln in after:
            if re.search(r"Rated\s*Current", ln, re.I):
                continue
            if re.search(r"Cable\s*Length", ln, re.I):
                continue
            if ln:
                desc_parts.append(ln)
    else:
        start = (rated_idx + 1) if rated_idx is not None else 1
        for ln in lines[start:]:
            if re.search(r"Rated\s*Current|Cable\s*Length", ln, re.I):
                continue
            desc_parts.append(ln)

    desc = "; ".join([x for x in desc_parts if x])
    return product_name, rated_val, cable_val, desc

def find_header_y_and_columns(words):
    keys = {
        "item": ["item"],
        "product": ["product"],
        "delivery": ["delivery", "term"],
        "moq": ["moq", "qty"],
        "price": ["unit", "price"],
        "lt": ["l/t", "wks", "weeks", "lt"],
        "remark": ["remark"],
    }

    candidates = []
    for w in words:
        t = w["text"].lower()
        for k, toks in keys.items():
            if t in toks:
                candidates.append((k, w))
                break

    if not candidates:
        return None, None, None

    buckets = {}
    for k, w in candidates:
        b = int(w["top"] // 5)
        buckets.setdefault(b, []).append((k, w))

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

    header_bottom = max(w["bottom"] for _, w in header_items)

    x_positions = {}
    for k, w in header_items:
        if k not in x_positions:
            x_positions[k] = w["x0"]
        else:
            x_positions[k] = min(x_positions[k], w["x0"])

    if "product" not in x_positions or "moq" not in x_positions or "price" not in x_positions:
        return None, None, None

    order = ["item", "product", "delivery", "moq", "price", "lt", "remark"]
    xs = [(k, x_positions[k]) for k in order if k in x_positions]
    xs.sort(key=lambda kv: kv[1])

    col_ranges = {}
    for i, (k, x) in enumerate(xs):
        left = x - 2
        right = (xs[i + 1][1] - 2) if i + 1 < len(xs) else 1e9
        if i > 0:
            left = (xs[i - 1][1] + x) / 2
        if i + 1 < len(xs):
            right = (x + xs[i + 1][1]) / 2
        col_ranges[k] = (left, right)

    return header_bottom, None, col_ranges

def collect_table_region_words(words, header_bottom):
    return [w for w in words if w["top"] > header_bottom + 1]

def find_notes_top(words):
    for w in words:
        if w["text"].strip().lower().startswith("notes"):
            return w["top"]
    return None

def extract_rows_by_item_anchor(table_words, col_ranges, notes_top):
    if notes_top is not None:
        table_words = [w for w in table_words if w["top"] < notes_top - 2]

    item_range = col_ranges.get("item")
    if not item_range:
        return []

    def in_col(w, rng):
        cx = (w["x0"] + w["x1"]) / 2
        return rng[0] <= cx <= rng[1]

    item_words = []
    for w in table_words:
        if in_col(w, item_range) and re.fullmatch(r"\d{1,3}", w["text"].strip()):
            item_words.append(w)

    if not item_words:
        return []

    item_words = sorted(item_words, key=lambda w: w["top"])

    spans = []
    for i, iw in enumerate(item_words):
        start_y = iw["top"] - 2
        end_y = (item_words[i + 1]["top"] - 2) if i + 1 < len(item_words) else 1e9
        spans.append((start_y, end_y))

    rows = []
    for (sy, ey) in spans:
        row_words = [w for w in table_words if sy <= w["top"] < ey]
        row = {}
        for k, rng in col_ranges.items():
            cell_words = [w for w in row_words if in_col(w, rng)]
            row[k] = words_to_text(cell_words)
        rows.append(row)

    return rows

def fill_down(rows, keys):
    prev = {k: "" for k in keys}
    out = []
    for r in rows:
        rr = dict(r)
        for k in keys:
            if not N(rr.get(k, "")):
                rr[k] = prev.get(k, "")
            else:
                prev[k] = rr[k]
        out.append(rr)
    return out

def parse_quote_file(file_obj):
    debug = {}

    with pdfplumber.open(file_obj) as pdf:
        full_text = []
        all_words = []
        for page in pdf.pages:
            full_text.append(page.extract_text() or "")
            ws = page.extract_words(
                keep_blank_chars=False,
                use_text_flow=True,
                extra_attrs=["x0", "x1", "top", "bottom"]
            )
            all_words.extend(ws)

    text = "\n".join(full_text)
    date = parse_date_from_text(text)
    customer, planner = parse_to_from(text)

    debug["date"] = date
    debug["customer"] = customer
    debug["planner"] = planner
    debug["words_count"] = len(all_words)

    header_bottom, _, col_ranges = find_header_y_and_columns(all_words)
    debug["header_bottom"] = header_bottom
    debug["col_ranges"] = col_ranges

    if header_bottom is None or col_ranges is None:
        return [], debug

    notes_top = find_notes_top(all_words)
    debug["notes_top"] = notes_top

    table_words = collect_table_region_words(all_words, header_bottom)
    debug["table_words_count"] = len(table_words)

    rows_raw = extract_rows_by_item_anchor(table_words, col_ranges, notes_top)
    debug["rows_raw_count"] = len(rows_raw)

    if not rows_raw:
        return [], debug

    rows_filled = fill_down(rows_raw, keys=["delivery", "moq", "lt", "remark"])
    debug["rows_filled_count"] = len(rows_filled)

    out = []

    for r in rows_filled:
        product_cell = r.get("product", "")
        delivery = N(r.get("delivery", ""))
        moq_cell = N(r.get("moq", ""))
        price_cell = N(r.get("price", ""))
        lt_cell = N(r.get("lt", ""))
        remark_cell = N(r.get("remark", ""))

        if not any([product_cell, delivery, moq_cell, price_cell, lt_cell, remark_cell]):
            continue

        product_name, rated, cable, desc = split_product_cell_by_rules(product_cell)
        lt = add_wks(lt_cell)

        remark_add = ""
        if is_sample_token(moq_cell):
            moq_val = 1
            remark_add = "Sample"
        else:
            mqty = re.search(r"\b(\d+)\b", moq_cell)
            if not mqty:
                continue
            moq_val = int(mqty.group(1))

        price = money_to_float(price_cell)
        if price is None:
            continue

        # NRE List special
        if is_nre_token(delivery) or is_nre_token(product_cell) or is_nre_token(text):
            delivery = "NRE List"
            mcav = re.search(r"\bCavity\s*\d+\b", product_cell, re.I)
            if mcav:
                cav = mcav.group(0)
                if cav and cav not in desc:
                    desc = (desc + "; " + cav).strip("; ").strip()

        remark = "; ".join([x for x in [remark_cell, remark_add] if N(x)])

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

    debug["out_count"] = len(out)
    return out, debug
