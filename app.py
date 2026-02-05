import streamlit as st
import pandas as pd

from parser import parse_quote_file, OUT_COLS, VERSION

st.set_page_config(page_title="견적서 PDF → CSV 변환기", layout="centered")

st.title("견적서 PDF → CSV 변환기")
st.caption("PDF 파일을 업로드하면 샘플 양식 컬럼 구조로 CSV를 생성합니다. (개인용/무료)")
st.caption(f"Parser version: {VERSION}")

uploaded = st.file_uploader("견적서 PDF 업로드", type=["pdf"])

if uploaded:
    with st.spinner("PDF 분석 중..."):
        try:
            rows, debug = parse_quote_file(uploaded)
        except Exception as e:
            st.error(f"변환 중 오류가 발생했습니다: {e}")
            st.stop()

    if not rows:
        st.error("변환 결과가 비어 있습니다. (표 영역 탐지 실패 또는 텍스트 추출 실패)")
        with st.expander("디버깅: 탐지 정보(필요 시)"):
            st.write(debug)
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

    with st.expander("디버깅: 탐지 정보(필요 시)"):
        st.write(debug)

else:
    st.info("PDF를 업로드하면 변환이 시작됩니다.")
