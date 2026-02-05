import streamlit as st
import pdfplumber
import pandas as pd

from parser import parse_quote_pdf, OUT_COLS, VERSION

st.set_page_config(page_title="견적서 PDF → CSV 변환기", layout="centered")

st.title("견적서 PDF → CSV 변환기")
st.caption("PDF 파일을 업로드하면 샘플 양식 컬럼 구조로 CSV를 생성합니다. (개인용/무료)")
st.caption(f"Parser version: {VERSION}")

uploaded = st.file_uploader("견적서 PDF 업로드", type=["pdf"])

def extract_pdf_text_and_tables(file_obj):
    all_text = []
    all_tables = []

    table_settings = {
        "vertical_strategy": "lines",
        "horizontal_strategy": "lines",
        "intersection_tolerance": 5,
        "snap_tolerance": 4,
        "join_tolerance": 3,
        "edge_min_length": 3,
        "min_words_vertical": 1,
        "min_words_horizontal": 1,
        "keep_blank_chars": False,
    }

    with pdfplumber.open(file_obj) as pdf:
        for page in pdf.pages:
            # text
            t = page.extract_text() or ""
            all_text.append(t)

            # tables
            try:
                tables = page.extract_tables(table_settings) or []
                for tb in tables:
                    if tb and any(any((c or "").strip() for c in row) for row in tb):
                        all_tables.append(tb)
            except Exception:
                pass

    return "\n".join(all_text), all_tables


if uploaded:
    with st.spinner("PDF 읽는 중..."):
        try:
            extracted_text, extracted_tables = extract_pdf_text_and_tables(uploaded)
        except Exception as e:
            st.error(f"PDF를 열 수 없습니다: {e}")
            st.stop()

    if not extracted_text.strip() and not extracted_tables:
        st.error("PDF에서 텍스트/표를 읽지 못했습니다. (스캔본 이미지 PDF일 수 있음)")
        st.stop()

    try:
        rows = parse_quote_pdf(extracted_text, extracted_tables)
    except Exception as e:
        st.error(f"변환 중 오류가 발생했습니다: {e}")
        with st.expander("디버깅용: 추출 텍스트 일부"):
            st.text(extracted_text[:2500])
        st.stop()

    if not rows:
        st.error("변환 결과가 비어 있습니다. 이 PDF가 다른 표 구조이거나, 표 추출이 실패했을 수 있어요.")
        with st.expander("디버깅용: 추출 텍스트 일부(필요 시)"):
            st.text(extracted_text[:2500])
        with st.expander("디버깅용: 추출된 표(raw) 보기(필요 시)"):
            st.write(extracted_tables[:2])
        st.stop()

    df = pd.DataFrame(rows, columns=OUT_COLS)

    st.subheader("변환 결과 미리보기")
    st.dataframe(df, use_container_width=True)

    csv_bytes = df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    out_name = uploaded.name.replace(".pdf", "") + "_converted.csv"

    st.download_button(
        label="CSV 다운로드",
        data=csv_bytes,
        file_name=out_name,
        mime="text/csv",
        use_container_width=True
    )
else:
    st.info("PDF를 업로드하면 변환이 시작됩니다.")
