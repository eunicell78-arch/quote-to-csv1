import streamlit as st
import pdfplumber
import pandas as pd
from parser import parse_sinbon_quote, OUT_COLS

st.set_page_config(page_title="견적서 PDF → CSV 변환기", layout="centered")

st.title("견적서 PDF → CSV 변환기")
st.caption("PDF 파일을 업로드하면 샘플 양식 컬럼 구조로 CSV를 생성합니다. (개인용/무료)")

uploaded = st.file_uploader("견적서 PDF 업로드", type=["pdf"])

if uploaded:
    with st.spinner("PDF 읽는 중..."):
        with pdfplumber.open(uploaded) as pdf:
            # 현재 업로드된 견적서는 1페이지라 page[0]로 충분하지만, 다페이지도 대비해서 전체 합침
            texts = []
            for page in pdf.pages:
                t = page.extract_text() or ""
                texts.append(t)
            extracted_text = "\n".join(texts)

    if not extracted_text.strip():
        st.error("PDF에서 텍스트를 읽지 못했습니다. (스캔본 이미지 PDF일 수 있음)")
        st.stop()

    rows = parse_sinbon_quote(extracted_text)

    if not rows:
        st.error("변환 결과가 비어 있습니다. 이 PDF 템플릿이 다른 형식일 수 있어요.")
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

    with st.expander("문제 해결(자주 발생)"):
        st.markdown(
            "- **스캔본(이미지) PDF**는 텍스트 추출이 안 될 수 있어요. 그 경우 OCR 기능을 추가해야 합니다.\n"
            "- 견적서 템플릿이 조금이라도 달라지면(표 구조/문구 변경) 파서 규칙을 업데이트해야 합니다.\n"
        )
else:
    st.info("PDF를 업로드하면 변환이 시작됩니다.")
