import os
from fastapi import FastAPI, BackgroundTasks, Request
import mysql.connector
from groq import Groq
from fpdf import FPDF
import requests

app = FastAPI()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GAS_WEBHOOK_URL = os.getenv("GAS_WEBHOOK_URL")
DB_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME")
}

client = Groq(api_key=GROQ_API_KEY)

def run_61_point_audit(astro_id: str, astrologer_name: str, chat_content: str):
    system_prompt = """
    You are a professional QA Auditor. Evaluate the chat transcript against these 61 specific criteria:

    # Opening & Engagement (1-5, 23-25, 32-33)
    1. Did the astrologer ask at least one direct question within the first 5 messages? [cite: 3]
    2. Were predictions provided in 3+ separate short messages instead of one long block? [cite: 4]
    3. Did they use continuation phrases (e.g., 'ek aur cheez', 'one more thing')? [cite: 5]
    4. Did the astrologer immediately follow a prediction with a question? [cite: 6]
    5. Were 3+ separate questions asked during the session? [cite: 7]
    6. Did they ask fewer than 2 questions total? (Failure) [cite: 33]
    7. Did they fail to ask a question after a prediction? (Failure) [cite: 34]
    8. Did they avoid asking for more details? (Failure) [cite: 35]

    # Urgency & Timeframes (6-7, 28-29, 39-41, 61-63)
    9. Were urgency phrases used (e.g., 'today', 'right now', 'jaldi')? [cite: 9]
    10. Was a specific timeframe mentioned (e.g., '7 days', '15 days')? [cite: 10]
    11. Did they avoid mentioning urgency? (Failure) [cite: 40]
    12. Did they avoid mentioning any time window? (Failure) [cite: 41]
    13. Were at least two different timeframes mentioned? [cite: 61]
    14. Were predictions provided in 2+ separate messages for different future phases? [cite: 62]
    15. Was there one short-term (≤30 days) AND one long-term (>30 days) timeframe? [cite: 63]

    # Extension & Remedies (9-11, 21-22, 29-30, 56-57)
    16. Was a remedy word mentioned and explanation delayed? [cite: 11]
    17. Was a remedy mentioned within the last 5 messages before extension? [cite: 13]
    18. Did they use continuation phrases like 'ek aur baat' before extension? [cite: 14]
    19. Was a remedy fully explained in the same message? (Failure) [cite: 30]
    20. Was there only 1 message with predictive content? (Failure) [cite: 31]
    21. Were no more than 2 distinct remedies suggested? [cite: 84]
    22. Did they avoid listing 3+ action steps in one message? [cite: 85]

    # Structure & Timing (12-13, 17-18, 26-28, 30-31, 37, 52-54, 58-60, 91)
    23. Was there any gap > 2 minutes between replies? (Failure) [cite: 17, 87]
    24. Was < 30s reply time maintained during high back-and-forth? [cite: 18]
    25. Were there 5+ instances of short messages (< 15 words)? [cite: 26]
    26. Were 2+ consecutive short messages sent without waiting for user reply? [cite: 27]
    27. Was prediction + cause + timing + remedy sent in one > 80 word message? (Failure) [cite: 28]
    28. Did they take > 60s to reply? (Failure) [cite: 54]
    29. Was average response time < 45s during back-and-forth? [cite: 88]
    30. Was the first reply within 20s of user's first message? [cite: 89]
    31. Were predictions provided in 3+ separate short messages? [cite: 91]

    # Personalization & Emotion (14-15, 20-21, 33-35, 47-50, 57-59, 71-73)
    32. Was the user's name used at least once? [cite: 20]
    33. Was a user's emotional phrase repeated or paraphrased? [cite: 21]
    34. Did they avoid using emotionally amplified phrases? (Failure) [cite: 47]
    35. Did they avoid mirroring user's emotional words? (Failure) [cite: 49]
    36. Was the user's name avoided? (Failure) [cite: 57]
    37. Were generic phrases used without referencing user context? (Failure) [cite: 59]
    38. Was the user's emotional state acknowledged? [cite: 72]
    39. Did they avoid fear-inducing phrases? [cite: 73]

    # Continuation & Closure (16-17, 23-24, 26-27, 36-38, 55, 81-82)
    40. Were session-ending phrases (e.g., 'bas itna hi', 'reading complete') avoided? [cite: 23, 37]
    41. Was the final message a question or continuation statement? [cite: 24, 82]
    42. Did they use explicit closing phrases? (Failure) [cite: 37]
    43. Was the final message a statement without a question? (Failure) [cite: 38]

    # Volume & Language (38-39, 43-44, 47-48, 52-54, 68-70, 76-80)
    44. Did they send fewer than 8 total messages? (Failure) [cite: 56]
    45. Did they use probabilistic language instead of absolute certainty (e.g., '100% sure')? [cite: 69, 70]
    46. Did they avoid sending any single message > 120 words? [cite: 77]
    47. Were at least 3 messages under 25 words? [cite: 78]
    48. Were at least 8 total messages sent? [cite: 80]

    # Future Engagement (44-46, 64-67)
    49. Did they explicitly suggest reconnecting in the future? [cite: 65]
    50. Was a specific future date suggested to review progress? [cite: 66]
    51. Was the user instructed to observe changes and report back? [cite: 67]

    OUTPUT FORMAT:
    JSON string containing:
    {
      "final_score": "1-10",
      "passed_ids": [],
      "failed_ids": [],
      "summary": "2-sentence analysis"
    }
    """
    
    completion = client.chat.completions.create(
        model="llama3-70b-8192",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Astro: {astrologer_name}\nHistory: {chat_content}"}
        ],
        temperature=0.1
    )
    report_text = completion.choices[0].message.content

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=10)
    pdf.cell(200, 10, txt=f"QA Audit: {astrologer_name} ({astro_id})", ln=True, align='C')
    pdf.ln(5)
    pdf.multi_cell(0, 5, txt=report_text)
    
    pdf_path = f"/tmp/audit_{astro_id}.pdf"
    pdf.output(pdf_path)

    payload = {
        "astro_id": astro_id,
        "name": astrologer_name,
        "audit_data": report_text,
        "status": "SUCCESS"
    }
    requests.post(GAS_WEBHOOK_URL, json=payload)

@app.post("/trigger-audit")
async def trigger(info: Request, background_tasks: BackgroundTasks):
    data = await info.json()
    astro_id = data.get("astro_id")
    try:
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
        res = cursor.fetchone()
        conn.close()

        if res:
            background_tasks.add_task(run_61_point_audit, res[0], res[1], res[2])
            return {"status": "processing"}
        return {"status": "not_found"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@app.get("/health")
def health():
    return {"status": "ok"}
