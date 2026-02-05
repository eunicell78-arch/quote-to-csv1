import streamlit as st
import pdfplumber
import pandas as pd

# parser.py에서 가져옵니다
from parser import parse_sinbon_quote, OUT_COLS, VERSION

st.set_page_config(page_title="견적서 PDF → CSV 변환기", layout="centered")

st.title("견적서 PDF → CSV 변환기")
st.caption("PDF 파일을 업로드하면 샘플 양식 컬럼 구조로 CSV를 생성합니다. (개인용/무료)")
st.caption(f"Parser version: {VERSION}")  # ✅ 지금 반영된 parser.py 버전 확인용

uploaded = st.file_uploader("견적서 PDF 업로드", type=["pdf"])

if uploaded:
    with st.spinner("PDF 읽는 중..."):
        try:
            with pdfplumber.open(uploaded) as pdf:
                texts = []
                for page in pdf.pages:
                    t = page.extract_text() or ""
                    texts.append(t)
                extracted_text = "\n".join(texts)
        except Exception as e:
            st.error(f"PDF를 열 수 없습니다: {e}")
            st.stop()

    if not extracted_text.strip():
        st.error("PDF에서 텍스트를 읽지 못했습니다. (스캔본 이미지 PDF일 수 있음)")
        st.stop()

    # 변환
    rows = []
    try:
        rows = parse_sinbon_quote(extracted_text)
    except Exception as e:
        st.error(f"변환 중 오류가 발생했습니다: {e}")
        st.stop()

    if not rows:
        st.error("변환 결과가 비어 있습니다. 이 PDF 템플릿이 다른 형식이거나, 샘플/운임 표기 방식이 달라서 매칭이 실패했을 수 있어요.")
        # 디버깅용(원하면 켜기): 추출 텍스트 일부 보여주기
        with st.expander("디버깅용: 추출된 텍스트 일부 보기(필요 시)"):
            st.text(extracted_text[:2000])
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

    with st.expander("자주 발생하는 문제 해결"):
        st.markdown(
            "- **엑셀에서 Date가 #######로 보이면**: 열 너비가 좁아서입니다. A열을 넓히면 날짜가 보입니다.\n"
            "- **스캔본(이미지) PDF**는 텍스트 추출이 안 될 수 있습니다. OCR 기능을 추가해야 합니다.\n"
            "- **운임 표기/줄바꿈이 PDF마다 다르면**: parser 규칙을 조금 더 추가해야 할 수 있습니다.\n"
        )
else:
    st.info("PDF를 업로드하면 변환이 시작됩니다.")
