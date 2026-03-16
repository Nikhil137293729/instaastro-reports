import os
from fastapi import FastAPI, BackgroundTasks, Request
from groq import Groq
import requests
from metabase_api import Metabase

app = FastAPI()

# --- CONFIGURATION ---
METABASE_URL = "https://metabase.instaastro.com"
METABASE_USER = os.getenv("METABASE_USER")
METABASE_PASSWORD = os.getenv("METABASE_PASSWORD")
QUESTION_ID = 7635  # From your screenshot
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GAS_WEBHOOK_URL = os.getenv("GAS_WEBHOOK_URL")

client = Groq(api_key=GROQ_API_KEY)

def run_full_audit(astro_id: str):
    try:
        # 1. Connect to Metabase
        mb = Metabase(METABASE_URL, METABASE_USER, METABASE_PASSWORD)
        
        # 2. Run Question 7635 with the dynamic ID
        # The 'target' name must match your Metabase variable exactly
        data = mb.get_card_data(card_id=QUESTION_ID, parameters=[
            {'type': 'category', 'target': ['variable', ['template-tag', 'target_astro_id']], 'value': astro_id}
        ])

        if not data or len(data) == 0:
            print(f"No data found for Astro ID: {astro_id}")
            return

        chat_content = data[0].get('all_content', '')
        astrologer_name = data[0].get('astrologer_name', 'Unknown')

        # 3. The 61-point System Prompt
        system_prompt = """
        You are a professional QA Auditor. Evaluate the chat against these 61 points:
        
        OPENING (1-5): Ask direct question in first 5 msgs; Use short predictions (3+ msgs); Use hooks like 'ek aur baat'.
        URGENCY (6-8, 28-29, 41-43): Use 'today/abhi'; Mention specific timeframes (7 days/1 month); Use short/long term windows.
        EXTENSION (9-11, 21-22, 56-57): Mention remedy word in last 5 msgs before end; Delay explanation; Max 2 remedies.
        STRUCTURE (12-13, 18-20, 30-31, 52-54): 5+ short msgs (<15 words); No gaps >2 mins; Reply <30s in back-and-forth.
        PERSONALIZATION (14-15, 33-35, 49-50): Use user's name; Mirror emotional words; Acknowledge emotional state.
        CLOSURE (16-17, 26-27, 36, 55): Avoid 'bas itna hi'; Final msg must be a question or continuation.
        VOLUME (38-40, 47-48, 54): Min 8 total messages; Use probabilistic language (no '100% sure').
        
        OUTPUT: Provide Score (1-10), List of FAILED points, and a 2-sentence summary.
        """

        # 4. Call Groq
        completion = client.chat.completions.create(
            model="llama3-70b-8192",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Astrologer: {astrologer_name}\nTranscript: {chat_content}"}
            ],
            temperature=0.1
        )
        report = completion.choices[0].message.content

        # 5. Send results to Google Sheets
        requests.post(GAS_WEBHOOK_URL, json={
            "astro_id": astro_id,
            "name": astrologer_name,
            "report": report
        })

    except Exception as e:
        print(f"Audit Error: {str(e)}")

@app.post("/trigger-audit")
async def trigger(info: Request, background_tasks: BackgroundTasks):
    data = await info.json()
    astro_id = data.get("astro_id")
    background_tasks.add_task(run_full_audit, str(astro_id))
    return {"status": "success", "message": "Audit triggered in background"}

@app.get("/health")
def health():
    return {"status": "ok"}
