import os
from fastapi import FastAPI, BackgroundTasks, Request
from groq import Groq
import requests
from metabase_api import Metabase # Ensure this is in requirements.txt

app = FastAPI()

# --- CONFIGURATION ---
METABASE_URL = "https://metabase.instaastro.com"
METABASE_USER = os.getenv("METABASE_USER")
METABASE_PASSWORD = os.getenv("METABASE_PASSWORD")
QUESTION_ID = 2657 # Replace with your actual Metabase Question ID
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GAS_WEBHOOK_URL = os.getenv("GAS_WEBHOOK_URL")

client = Groq(api_key=GROQ_API_KEY)

def fetch_and_audit(astro_id: str):
    try:
        # 1. Login to Metabase
        mb = Metabase(METABASE_URL, METABASE_USER, METABASE_PASSWORD)
        
        # 2. Run the specific question with the astro_id parameter
        # Note: Your Metabase question must have a variable named 'target_astro_id'
        data = mb.get_card_data(card_id=QUESTION_ID, parameters=[
            {'type': 'category', 'target': ['variable', ['template-tag', 'target_astro_id']], 'value': astro_id}
        ])

        if not data:
            return

        chat_content = data[0]['all_content']
        astrologer_name = data[0]['astrologer_name']

        # 3. The 61-point AI Audit Logic
        system_prompt = "Evaluate this chat against the 61 behavioral points... (Include full list here)"
        
        completion = client.chat.completions.create(
            model="llama3-70b-8192",
            messages=[{"role": "system", "content": system_prompt},
                      {"role": "user", "content": chat_content}],
            temperature=0.1
        )

        # 4. Send report back to Google Sheets
        requests.post(GAS_WEBHOOK_URL, json={
            "astro_id": astro_id,
            "name": astrologer_name,
            "report": completion.choices[0].message.content
        })

    except Exception as e:
        print(f"Error: {e}")

@app.post("/trigger-audit")
async def trigger(info: Request, background_tasks: BackgroundTasks):
    data = await info.json()
    astro_id = data.get("astro_id")
    background_tasks.add_task(fetch_and_audit, astro_id)
    return {"status": "Processing started in background"}

@app.get("/health")
def health():
    return {"status": "ok"}
