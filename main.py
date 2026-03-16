import os
from fastapi import FastAPI, BackgroundTasks, Request
import mysql.connector
from groq import Groq
from fpdf import FPDF
import requests

app = FastAPI()

# Configuration (Use Render Environment Variables for these)
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DB_CONFIG = {
    "host": "YOUR_DB_IP",
    "user": "YOUR_DB_USER",
    "password": "YOUR_DB_PASSWORD",
    "database": "YOUR_DB_NAME"
}
# URL of your Google Apps Script Web App (for callback)
GAS_WEBHOOK_URL = "YOUR_APPS_SCRIPT_URL"

client = Groq(api_key=GROQ_API_KEY)

def run_61_point_audit(astro_id: str, astrologer_name: str, chat_content: str):
    # 1. The 61-point Prompt Logic
    system_prompt = """
    You are a professional QA Auditor. Evaluate the following chat transcript 
    against the 61 sentiment/behavioral points provided. 
    Focus on Opening Engagement, Urgency, Extension Triggers, and Structure.
    Output a structured report with a Final Score (1-10) and Summary.
    """
    
    # 2. Call Groq (Llama 3 70B recommended for complex audits)
    completion = client.chat.completions.create(
        model="llama3-70b-8192",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Astro ID: {astro_id}\nContent: {chat_content}"}
        ],
        temperature=0.1
    )
    report_text = completion.choices[0].message.content

    # 3. Generate PDF Locally
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    pdf.multi_cell(0, 10, txt=f"Audit Report for Astro ID: {astro_id}\n\n{report_text}")
    pdf_filename = f"report_{astro_id}.pdf"
    pdf.output(pdf_filename)

    # 4. Callback to Google Sheets
    # Here you would ideally upload to Drive first, then send the link to GAS.
    # For now, we send the report summary back to the sheet.
    payload = {
        "astro_id": astro_id,
        "name": astrologer_name,
        "report": report_text[:1000] # Sending summary back to Sheet
    }
    requests.post(GAS_WEBHOOK_URL, json=payload)

@app.post("/trigger-audit")
async def trigger(info: Request, background_tasks: BackgroundTasks):
    data = await info.json()
    astro_id = data.get("astro_id")

    # 1. Fetch Data from DB (Render won't timeout)
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    cursor.execute("SET SESSION group_concat_max_len = 1000000")
    
    query = """
        SELECT ucd.astro_id, aav.user_name, GROUP_CONCAT(f.content SEPARATOR ' || ')
        FROM astrochat_firebasemessage f
        JOIN users_calldetail ucd ON f.call_detail_id = ucd.id
        LEFT JOIN astro_astrologer_view aav ON ucd.astro_id = aav.astroId
        WHERE ucd.astro_id = %s AND ucd.created_on_ymd >= CURDATE() - INTERVAL 3 DAY
        GROUP BY ucd.astro_id, aav.user_name
    """
    cursor.execute(query, (astro_id,))
    result = cursor.fetchone()
    conn.close()

    if result:
        # Start the heavy audit in the background
        background_tasks.add_task(run_61_point_audit, result[0], result[1], result[2])
        return {"status": "Processing", "message": "Audit started for " + result[1]}
    
    return {"status": "Error", "message": "No data found"}
