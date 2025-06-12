import streamlit as st
import requests
import json
import os
from datetime import datetime
from google.cloud import documentai_v1 as documentai
from dotenv import load_dotenv

load_dotenv()

# ---------------------- CONFIG ----------------------
# 환경 설정
# 주석변경 히히
PROJECT_ID = os.getenv("PROJECT_ID")
LOCATION = os.getenv("LOCATION")
PROCESSOR_ID = os.getenv("PROCESSOR_ID")
CREDENTIALS_PATH = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = CREDENTIALS_PATH

# ---------------------- HELPER FUNCTIONS ----------------------
def process_invoice_with_document_ai(file, project_id, location, processor_id):
    client = documentai.DocumentProcessorServiceClient()

    name = f"projects/{project_id}/locations/{location}/processors/{processor_id}"
    raw_document = documentai.RawDocument(content=file.read(), mime_type="application/pdf")

    request = documentai.ProcessRequest(
        name=name,
        raw_document=raw_document
    )

    result = client.process_document(request=request)
    document = result.document

    extracted_fields = {}
    for entity in document.entities:
        extracted_fields[entity.type_] = entity.mention_text

    return extracted_fields, document.text

def refine_invoice_fields_with_openrouter(fields_dict, raw_text, api_key):
    prompt = f"""
다음은 OCR로 추출한 인보이스 항목들입니다:
{json.dumps(fields_dict, ensure_ascii=False, indent=2)}

전체 문서 내용:
{raw_text[:3000]}

누락되거나 불명확한 필드를 문맥에 따라 추론해서 다음 JSON 형식으로 보완해줄래:
{{
  "공급자명": "...",
  "총금액": "...",
  "세금": "...",
  "발행일": "...",
  "품목": ["...", "..."]
}}
    """

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": "posco-hackathon",  # optional, 수정 가능
        "HTTP-Referer": "http://localhost:8501"  # optional, 로컬테스트용
    }

    payload = {
        "model": "deepseek/deepseek-chat-v3-0324:free",  # 또는 정확히 지원되는 모델명 사용
        "messages": [
            {"role": "system", "content": "너는 인보이스 문서의 회계 정보를 정리하는 전문가야."},
            {"role": "user", "content": prompt}
        ]
    }

    response = requests.post(
        url="https://openrouter.ai/api/v1/chat/completions",
        headers=headers,
        json=payload
    )

    try:
        response.raise_for_status()
        data = response.json()
        if "choices" in data:
            return data["choices"][0]["message"]["content"]
        else:
            print("❌ GPT 응답 형식 오류. 전체 응답 로그:")
            print(json.dumps(data, indent=2, ensure_ascii=False))
            return "GPT 응답에 'choices' 키가 없습니다. 응답 확인 필요."
    except Exception as e:
        print("❌ GPT 호출 실패:", str(e))
        print("응답 본문:", response.text)
        return f"GPT 호출 실패: {str(e)}"

# ---------------------- STREAMLIT UI ----------------------
st.set_page_config(page_title="AI 인보이스 처리 대시보드", layout="wide")
st.title("📄 AI 인보이스 자동처리 대시보드")
st.markdown("""
업로드된 인보이스를 Google Document AI와 OpenRouter LLM으로 자동 처리합니다.\
결과를 JSON/CSV 포맷으로 확인하고, 사용자 수정을 반영한 후 확정할 수 있습니다.
""")

uploaded_file = st.file_uploader("1️⃣ 인보이스 파일 업로드 (PDF)", type="pdf")

if uploaded_file:
    st.info("⏳ 문서 분석 중입니다... 잠시만 기다려주세요.")
    extracted_fields, raw_text = process_invoice_with_document_ai(
        uploaded_file, PROJECT_ID, LOCATION, PROCESSOR_ID
    )

    st.subheader("2️⃣ Google Document AI 추출 결과")
    st.json(extracted_fields)

    st.subheader("3️⃣ GPT 기반 필드 보완")
    gpt_result_raw = refine_invoice_fields_with_openrouter(extracted_fields, raw_text, OPENROUTER_API_KEY)
    try:
        gpt_fields = json.loads(gpt_result_raw)
    except json.JSONDecodeError:
        st.error("GPT 응답을 파싱할 수 없습니다. 응답 내용:")
        st.text(gpt_result_raw)
        st.stop()

    # 사용자 수정용 폼 생성
    st.subheader("4️⃣ 사용자 검토 및 수정")
    with st.form("edit_form"):
        supplier = st.text_input("공급자명", gpt_fields.get("공급자명", ""))
        date = st.text_input("발행일", gpt_fields.get("발행일", ""))
        amount = st.text_input("총금액", gpt_fields.get("총금액", ""))
        tax = st.text_input("세금", gpt_fields.get("세금", ""))
        items = st.text_area("품목 (쉼표로 구분)", ", ".join(gpt_fields.get("품목", [])))
        submit_btn = st.form_submit_button("✅ 확정 및 저장")

    if submit_btn:
        result = {
            "공급자명": supplier,
            "발행일": date,
            "총금액": amount,
            "세금": tax,
            "품목": [item.strip() for item in items.split(",") if item.strip()]
        }
        st.success("🎉 결과가 확정되어 저장되었습니다.")
        st.download_button("📁 JSON 다운로드", json.dumps(result, ensure_ascii=False, indent=2), file_name="invoice_result.json")

        import pandas as pd
        df = pd.DataFrame({"항목": list(result.keys()), "값": list(result.values())})
        csv = df.to_csv(index=False)
        st.download_button("📁 CSV 다운로드", csv, file_name="invoice_result.csv", mime="text/csv")
