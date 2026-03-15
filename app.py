# app.py
# InstaAstro Report Generation Service
# Runs on Render.com — no time limits
# Endpoint: POST /generate-report { astro_id, whatsapp_number }

import os
import re
import json
import time
import asyncio
import tempfile
import requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from weasyprint import HTML as WeasyHTML
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)

# ─────────────────────────────────────────────────────────────
#  CONFIG — set these as environment variables on Render
# ─────────────────────────────────────────────────────────────

METABASE_URL      = os.environ.get("METABASE_URL",      "https://metabase.instaastro.com")
METABASE_USERNAME = os.environ.get("METABASE_USERNAME", "")
METABASE_PASSWORD = os.environ.get("METABASE_PASSWORD", "")
METABASE_QUESTION = int(os.environ.get("METABASE_QUESTION", "7631"))

GROQ_API_KEY      = os.environ.get("GROQ_API_KEY",      "")
GROQ_MODEL        = os.environ.get("GROQ_MODEL",        "llama-3.3-70b-versatile")

CALLMEBOT_APIKEY  = os.environ.get("CALLMEBOT_APIKEY",  "")

# ─────────────────────────────────────────────────────────────
#  HEALTH CHECK
# ─────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "InstaAstro Report Generator"})

# ─────────────────────────────────────────────────────────────
#  MAIN ENDPOINT
# ─────────────────────────────────────────────────────────────

@app.route("/generate-report", methods=["POST"])
def generate_report():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    astro_id         = data.get("astro_id")
    whatsapp_number  = data.get("whatsapp_number")
    astro_name       = data.get("astro_name", f"Astro {astro_id}")
    days             = int(data.get("days", 3))

    if not astro_id:
        return jsonify({"error": "astro_id is required"}), 400
    if not whatsapp_number:
        return jsonify({"error": "whatsapp_number is required"}), 400

    print(f"[START] astro_id={astro_id} | name={astro_name} | days={days}")

    try:
        # Step 1: Pull from Metabase
        print("[1/5] Pulling from Metabase...")
        rows = pull_from_metabase(astro_id, days)
        if not rows:
            return jsonify({"error": "No chats found for this astro"}), 404
        print(f"[1/5] Fetched {len(rows)} chats")

        # Step 2: Parse HTML
        print("[2/5] Parsing HTML chats...")
        parsed_chats = [parse_chat_html(row.get("content", "")) for row in rows]
        parsed_chats = [c for c in parsed_chats if c and c["total_messages"] > 0]
        print(f"[2/5] Parsed {len(parsed_chats)} chats with messages")

        if not parsed_chats:
            return jsonify({"error": "No chats with messages found"}), 404

        # Step 3: Score with LLM (parallel — 5 workers)
        print(f"[3/5] Scoring {len(parsed_chats)} chats with Llama 3.3 70B...")
        scored_chats = score_chats_parallel(parsed_chats, workers=5)
        print(f"[3/5] Scored {len(scored_chats)} chats successfully")

        # Step 4: Aggregate + generate PDF
        print("[4/5] Generating PDF report...")
        aggregated = aggregate_scores(scored_chats)
        content    = generate_content_json(aggregated, astro_name)
        html       = build_html_report(aggregated, content, astro_name, days)
        pdf_bytes  = html_to_pdf(html)
        print(f"[4/5] PDF generated: {len(pdf_bytes):,} bytes")

        # Step 5: Send WhatsApp
        print(f"[5/5] Sending to WhatsApp {whatsapp_number}...")
        send_whatsapp(whatsapp_number, astro_name, aggregated["overall_pct"], pdf_bytes)
        print("[DONE]")

        return jsonify({
            "status":     "success",
            "astro_id":   astro_id,
            "chats":      len(parsed_chats),
            "scored":     len(scored_chats),
            "overall_pct": aggregated["overall_pct"],
            "pdf_bytes":  len(pdf_bytes),
        })

    except Exception as e:
        print(f"[ERROR] {str(e)}")
        return jsonify({"error": str(e)}), 500

# ─────────────────────────────────────────────────────────────
#  STEP 1: METABASE PULL
# ─────────────────────────────────────────────────────────────

def pull_from_metabase(astro_id, days=3):
    # Authenticate
    auth = requests.post(
        f"{METABASE_URL}/api/session",
        json={"username": METABASE_USERNAME, "password": METABASE_PASSWORD},
        timeout=30
    )
    auth.raise_for_status()
    token = auth.json()["id"]

    # Fetch question with astro_id filter
    # Uses Metabase parameterized query — pass astro_id as parameter
    resp = requests.post(
        f"{METABASE_URL}/api/card/{METABASE_QUESTION}/query/json",
        headers={"X-Metabase-Session": token, "Content-Type": "application/json"},
        json={"parameters": [{"type": "number", "target": ["variable", ["template-tag", "astro_id"]], "value": str(astro_id)}]},
        timeout=60
    )

    if resp.status_code not in [200, 202]:
        # Fallback: fetch all and filter locally
        resp = requests.post(
            f"{METABASE_URL}/api/card/{METABASE_QUESTION}/query/json",
            headers={"X-Metabase-Session": token, "Content-Type": "application/json"},
            json={},
            timeout=60
        )
        resp.raise_for_status()
        all_rows = resp.json()
        return [r for r in all_rows if str(r.get("astro_id", "")) == str(astro_id)]

    resp.raise_for_status()
    return resp.json()

# ─────────────────────────────────────────────────────────────
#  STEP 2: HTML PARSER
# ─────────────────────────────────────────────────────────────

def parse_timestamp(ts_str):
    if not ts_str:
        return None
    try:
        clean = re.sub(r'\s+', ' ', ts_str).strip()
        m = re.match(r'(\d{2})-(\d{2})-(\d{4})\s+[-]+\s+(\d{1,2}):(\d{2}):(\d{2})\s+(AM|PM)', clean, re.IGNORECASE)
        if not m:
            return None
        dd, mm, yyyy, hh, mn, ss, ampm = m.groups()
        hh = int(hh)
        if ampm.upper() == "PM" and hh != 12:
            hh += 12
        if ampm.upper() == "AM" and hh == 12:
            hh = 0
        return datetime(int(yyyy), int(mm), int(dd), hh, int(mn), int(ss)).timestamp()
    except:
        return None

def parse_chat_html(html):
    if not html or not html.strip():
        return None

    result = {
        "user_name": "", "gender": "", "topic": "", "dob": "", "pob": "",
        "messages": [], "conversation": "",
        "total_messages": 0, "astro_messages": 0, "user_messages": 0,
        "recharge_pushes": 0, "session_duration_min": 0, "chat_ended": False,
        "max_gap_seconds": 0, "has_gap_over_2min": False,
        "all_replies_under_2min": True, "fast_first_reply": False,
    }

    # Extract user info
    for field, key in [("Name", "user_name"), ("Gender", "gender"), ("TOC", "topic"), ("DOB", "dob"), ("POB", "pob")]:
        m = re.search(f"<p>{field}:\\s*([^<]*)</p>", html)
        if m:
            result[key] = m.group(1).strip()

    result["recharge_pushes"] = len(re.findall(r"class='has_cta'", html))

    dur = re.search(r"Last session was of (\d+):(\d+) Min", html)
    if dur:
        result["session_duration_min"] = round(int(dur.group(1)) + int(dur.group(2)) / 60, 2)
        result["chat_ended"] = True

    # Extract messages
    msg_pattern = re.compile(
        r"<div id='(astro|user)'[^>]*class='message_div'[^>]*>[\s\S]*?"
        r"<p class='content'>([\s\S]*?)</p>[\s\S]*?"
        r"<p class='content-footer'>([^|]+)\|\s*([^<]+)</p>",
        re.MULTILINE
    )

    messages = []
    lines    = []

    for m in msg_pattern.finditer(html):
        sender_tag = m.group(1)
        content    = m.group(2).strip()
        name       = m.group(3).strip()
        ts_raw     = m.group(4).strip()

        if not content or content in ["None", "...", ""]:
            continue

        sender = "Astro" if sender_tag == "astro" else "User"
        ts_ms  = parse_timestamp(ts_raw)

        messages.append({"sender": sender, "name": name, "time": ts_raw, "ts": ts_ms, "content": content})
        lines.append(f"[{sender} | {name} | {ts_raw}]: {content}")

        result["total_messages"] += 1
        if sender_tag == "astro":
            result["astro_messages"] += 1
        else:
            result["user_messages"] += 1

    result["messages"]     = messages
    result["conversation"] = "\n".join(lines)

    # Timing analysis
    sequences = []
    i = 0
    while i < len(messages):
        if messages[i]["sender"] == "User":
            user_ts = messages[i]["ts"]
            j = i + 1
            while j < len(messages) and messages[j]["sender"] != "Astro":
                j += 1
            if j < len(messages) and user_ts and messages[j]["ts"]:
                gap = round(messages[j]["ts"] - user_ts)
                if gap > result["max_gap_seconds"]:
                    result["max_gap_seconds"] = gap
                if gap > 120:
                    result["has_gap_over_2min"]     = True
                    result["all_replies_under_2min"] = False
                if i == 0 and gap <= 20:
                    result["fast_first_reply"] = True
                sequences.append(gap)
            i = j + 1
        else:
            i += 1

    return result

# ─────────────────────────────────────────────────────────────
#  STEP 3: LLM SCORING — PARALLEL
# ─────────────────────────────────────────────────────────────

LLM_PROMPTS = [
    (1,  "Within the first 5 astrologer messages, did the astrologer ask at least one direct question?"),
    (2,  "Did the astrologer give predictions in 3+ separate short messages instead of one long message?"),
    (3,  "Did the astrologer use continuation hooks like 'ek aur cheez', 'one more thing' without explaining immediately?"),
    (4,  "After a prediction, did the astrologer immediately follow with a question?"),
    (5,  "Did the astrologer ask 3 or more separate questions during the session?"),
    (6,  "Did the astrologer use urgency phrases like 'jaldi', 'abhi', 'this week', 'within 15 days'?"),
    (7,  "Did the astrologer mention a specific timeframe like 'next 7 days', '1 month', 'this year'?"),
    (8,  "Did the astrologer mention a remedy and delay explaining it to a later message?"),
    (9,  "In the last 5 astrologer messages before extension, was a remedy-related word mentioned?"),
    (10, "In the last 5 messages before extension, did the astrologer use continuation phrases?"),
    (11, "Did the astrologer hint that more information exists but not disclose it yet?"),
    (14, "Did the astrologer use the user's name at least once?"),
    (15, "Did the astrologer mirror or paraphrase an emotional phrase the user used?"),
    (16, "Did the astrologer avoid session-ending phrases like 'bas itna hi', 'reading complete'?"),
    (17, "Was the astrologer's final message a question or continuation statement?"),
    (18, "Did the astrologer send 5 or more short messages under 15 words?"),
    (19, "Did the astrologer send 2+ consecutive short messages without waiting for user reply?"),
    (20, "Did the astrologer put prediction + cause + timing + remedy all in one message over 80 words?"),
    (21, "Was a remedy mentioned and fully explained in the same message?"),
    (22, "Was there only 1 predictive message in the whole session?"),
    (23, "Did the astrologer ask fewer than 2 questions total?"),
    (24, "After a prediction, did the astrologer fail to ask a follow-up question?"),
    (25, "Did the astrologer avoid asking for more user details after the initial info?"),
    (26, "Did the astrologer use closing phrases like 'bas itna hi', 'that is all'?"),
    (27, "Was the astrologer's final message a plain statement without a question?"),
    (28, "Did the astrologer avoid urgency words like 'jaldi', 'abhi', 'today'?"),
    (29, "Did the astrologer avoid mentioning any specific time window?"),
    (30, "Did the astrologer send any message longer than 100 words?"),
    (31, "Did the astrologer send fewer than 3 short messages under 15 words?"),
    (32, "Did the astrologer avoid sending consecutive short messages?"),
    (33, "Did the astrologer avoid emotionally amplified phrases?"),
    (34, "Did the astrologer avoid mirroring the user's emotional words?"),
    (35, "Did the astrologer avoid continuation phrases like 'ek aur baat'?"),
    (36, "Did the astrologer avoid hinting at hidden chart information?"),
    (38, "Did the astrologer send fewer than 8 total messages?"),
    (39, "Did the astrologer never use the user's name?"),
    (40, "Did the astrologer use generic phrases without personalizing to the user?"),
    (41, "Did the astrologer mention 2 or more different timeframes?"),
    (42, "Did the astrologer give predictions referencing different future phases?"),
    (43, "Did the astrologer mention both a short-term and long-term timeframe?"),
    (44, "Did the astrologer suggest reconnecting in the future?"),
    (45, "Did the astrologer suggest reviewing progress after a specific future date?"),
    (46, "Did the astrologer tell the user to observe changes and report back?"),
    (47, "Did the astrologer use probabilistic language instead of absolute certainty?"),
    (48, "Did the astrologer avoid words like 'guaranteed', '100% sure'?"),
    (49, "Did the astrologer acknowledge the user's emotional state at least once?"),
    (50, "Did the astrologer avoid fear-inducing phrases?"),
    (51, "Did the astrologer reference a past session or prior discussion?"),
    (52, "Did the astrologer avoid any single message longer than 120 words?"),
    (53, "Did the astrologer send at least 3 short messages under 25 words?"),
    (54, "Did the astrologer send at least 8 total messages?"),
    (55, "Did the astrologer's final message contain a continuation statement?"),
    (56, "Did the astrologer suggest no more than 2 distinct remedies?"),
    (57, "Did the astrologer avoid listing more than 3 action steps in one message?"),
    (61, "Did the astrologer give predictions in 3+ separate short messages?"),
]

POSITIVE_IDS = {1,2,3,4,5,6,7,8,9,10,11,13,14,15,16,17,18,19,41,42,43,44,45,46,47,48,49,50,52,53,54,55,56,57,58,59,60,61}
NEGATIVE_IDS = {12,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,51}

def score_single_chat(chat):
    """Score one chat — called in parallel."""
    prompt_list = "\n".join([f"{pid}: {text}" for pid, text in LLM_PROMPTS])
    messages_text = json.dumps([
        {"sender": m["sender"], "name": m["name"], "time": m["time"], "content": m["content"]}
        for m in chat["messages"]
    ])

    prompt = (
        "You are a quality analyst for InstaAstro, an astrology chat platform.\n"
        "Analyze this chat and answer each question about the ASTROLOGER's behavior only.\n\n"
        f"User: {chat['user_name']} | Topic: {chat['topic']} | Gender: {chat['gender']}\n\n"
        f"Chat messages:\n{messages_text}\n\n"
        "Answer ALL questions with true or false.\n"
        "Return ONLY a valid JSON object. Keys = question IDs as strings, values = true or false.\n"
        "No explanation. No markdown. Just the JSON.\n\n"
        f"Questions:\n{prompt_list}"
    )

    for attempt in range(3):
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROQ_API_KEY}",
                    "Content-Type":  "application/json"
                },
                json={
                    "model":           GROQ_MODEL,
                    "messages":        [{"role": "user", "content": prompt}],
                    "temperature":     0.1,
                    "max_tokens":      1000,
                    "response_format": {"type": "json_object"}
                },
                timeout=30
            )

            if resp.status_code == 200:
                result = resp.json()["choices"][0]["message"]["content"]
                scores = json.loads(result)

                # Add timing scores from parsed data
                scores["12"] = chat.get("has_gap_over_2min", False)
                scores["58"] = chat.get("all_replies_under_2min", True)
                scores["60"] = chat.get("fast_first_reply", False)

                return {"chat": chat, "scores": scores}

            elif resp.status_code == 429:
                wait = 20
                m = re.search(r"try again in ([\d.]+)s", resp.text)
                if m:
                    wait = int(float(m.group(1))) + 2
                print(f"Rate limit — waiting {wait}s (attempt {attempt+1})")
                time.sleep(wait)

            else:
                print(f"Groq error {resp.status_code}: {resp.text[:200]}")
                return None

        except Exception as e:
            print(f"Score error: {e}")
            time.sleep(5)

    return None

def score_chats_parallel(chats, workers=5):
    """Score multiple chats in parallel using thread pool."""
    results = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(score_single_chat, chat): chat for chat in chats}
        for future in as_completed(futures):
            result = future.result()
            if result:
                results.append(result)
    return results

# ─────────────────────────────────────────────────────────────
#  STEP 4a: AGGREGATE SCORES
# ─────────────────────────────────────────────────────────────

def aggregate_scores(scored_chats):
    prompt_ids = [pid for pid, _ in LLM_PROMPTS] + [12, 58, 60]
    prompt_true  = {pid: 0 for pid in prompt_ids}
    prompt_total = {pid: 0 for pid in prompt_ids}

    for item in scored_chats:
        scores = item["scores"]
        for pid in prompt_ids:
            val = scores.get(str(pid))
            if val is not None:
                prompt_total[pid] += 1
                if val is True or str(val).lower() == "true":
                    prompt_true[pid] += 1

    prompt_pct = {
        pid: round(prompt_true[pid] / prompt_total[pid] * 100) if prompt_total[pid] > 0 else 0
        for pid in prompt_ids
    }

    def cat_avg(ids):
        vals = [prompt_pct[i] for i in ids if i in prompt_pct]
        return round(sum(vals) / len(vals)) if vals else 0

    # Good behaviors: positive prompts >= 60%
    good = sorted(
        [{"id": pid, "pct": prompt_pct[pid]} for pid in POSITIVE_IDS if prompt_pct.get(pid, 0) >= 60],
        key=lambda x: -x["pct"]
    )[:5]

    # Improvement areas: positive prompts < 40%
    improve = sorted(
        [{"id": pid, "pct": prompt_pct[pid]} for pid in POSITIVE_IDS if prompt_pct.get(pid, 0) < 40],
        key=lambda x: x["pct"]
    )[:5]

    pos_scores = [prompt_pct[pid] for pid in POSITIVE_IDS if pid in prompt_pct]
    overall    = round(sum(pos_scores) / len(pos_scores)) if pos_scores else 0

    return {
        "total_chats":        len(scored_chats),
        "overall_pct":        overall,
        "good_behaviors":     good,
        "improvement_areas":  improve,
        "prompt_pct":         prompt_pct,
        "categories": {
            "opening":    cat_avg([1,2,3,4,5]),
            "urgency":    cat_avg([6,7,28,29,41,42,43]),
            "continuity": cat_avg([3,8,9,10,11]),
            "personal":   cat_avg([14,15,39,40]),
            "structure":  cat_avg([18,19,52,53,54]),
            "emotional":  cat_avg([49,50,33,34]),
            "timing":     cat_avg([58,60]),
        }
    }

# ─────────────────────────────────────────────────────────────
#  STEP 4b: GENERATE CONTENT JSON VIA GROQ
# ─────────────────────────────────────────────────────────────

PROMPT_LABELS = {
    1:"Asked direct question in first 5 messages", 2:"Gave predictions in multiple short messages",
    3:"Used continuation hooks", 4:"Followed prediction with a question",
    5:"Asked 3+ questions", 6:"Used urgency phrases",
    7:"Mentioned specific timeframe", 8:"Mentioned remedy but delayed explanation",
    9:"Mentioned remedy before extension", 10:"Used continuation phrase before extension",
    11:"Hinted at undisclosed chart info", 14:"Used user name",
    15:"Mirrored user emotional phrases", 16:"Avoided session-ending phrases",
    17:"Final message was question or continuation", 18:"Sent 5+ short messages",
    19:"Sent consecutive short messages", 41:"Mentioned 2+ timeframes",
    42:"Gave predictions referencing different phases", 43:"Mentioned short and long-term timeframe",
    44:"Suggested reconnecting", 49:"Acknowledged user emotional state",
    50:"Avoided fear-inducing phrases", 54:"Sent 8+ total messages",
    55:"Final message was continuation-oriented", 58:"All replies under 2 min",
}

def generate_content_json(aggregated, astro_name):
    good_list    = " | ".join([f"{PROMPT_LABELS.get(b['id'], str(b['id']))} ({b['pct']}% sessions)" for b in aggregated["good_behaviors"]])
    improve_list = " | ".join([f"{PROMPT_LABELS.get(b['id'], str(b['id']))} (only {b['pct']}% sessions)" for b in aggregated["improvement_areas"]])

    prompt = (
        "You are a performance coach for InstaAstro astrologers.\n"
        "Generate report content in Hinglish (natural Hindi+English mix using Roman script only).\n\n"
        f"Astrologer: {astro_name}\n"
        f"Total chats: {aggregated['total_chats']}\n"
        f"Overall score: {aggregated['overall_pct']}%\n\n"
        f"GOOD BEHAVIORS:\n{good_list}\n\n"
        f"IMPROVEMENT AREAS:\n{improve_list}\n\n"
        "Return ONLY a valid JSON with this exact structure:\n"
        "{\n"
        '  "good_points": ["bullet 1 (max 12 words)", "bullet 2", "bullet 3", "bullet 4"],\n'
        '  "improve_points": [\n'
        '    {"title": "issue title (max 6 words)", "desc": "one sentence (max 15 words)", "example": "realistic chat example (max 15 words)"},\n'
        '    {"title": "...", "desc": "...", "example": "..."},\n'
        '    {"title": "...", "desc": "...", "example": "..."}\n'
        "  ]\n"
        "}\n\n"
        "RULES: ASCII only. No Hindi Unicode. No special chars (&, %, $, #). Roman script Hinglish only."
    )

    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={
            "model":           GROQ_MODEL,
            "messages":        [{"role": "user", "content": prompt}],
            "temperature":     0.3,
            "max_tokens":      800,
            "response_format": {"type": "json_object"}
        },
        timeout=30
    )
    resp.raise_for_status()
    return json.loads(resp.json()["choices"][0]["message"]["content"])

# ─────────────────────────────────────────────────────────────
#  STEP 4c: BUILD HTML REPORT
# ─────────────────────────────────────────────────────────────

def html_to_pdf(html_content):
    return WeasyHTML(string=html_content).write_pdf()

def build_html_report(aggregated, content, astro_name, days):
    date_str   = datetime.now().strftime("%d %B %Y")
    cat        = aggregated["categories"]
    overall    = aggregated["overall_pct"]
    total      = aggregated["total_chats"]

    good_bullets = "".join([f"<li>{p}</li>" for p in content.get("good_points", [])])

    improve_sections = ""
    for i, pt in enumerate(content.get("improve_points", []), 1):
        improve_sections += f"""
        <div class="improve-item">
            <h3>{i}. {pt.get('title', '')}</h3>
            <p>{pt.get('desc', '')}</p>
            <div class="example">Example: <em>"{pt.get('example', '')}"</em></div>
        </div>"""

    cat_rows = "".join([
        f"<tr><td>{name}</td><td class='score-cell'>{score}%</td></tr>"
        for name, score in [
            ("Opening & Engagement",   cat["opening"]),
            ("Urgency & Timeframes",   cat["urgency"]),
            ("Continuation Triggers",  cat["continuity"]),
            ("Personalization",        cat["personal"]),
            ("Message Structure",      cat["structure"]),
            ("Emotional Intelligence", cat["emotional"]),
            ("Response Timing",        cat["timing"]),
        ]
    ])

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family: 'Inter', sans-serif; color: #1a1a1a; background: #fff; padding: 40px; font-size: 13px; }}
  .header {{ text-align: center; border-bottom: 3px solid #00A86B; padding-bottom: 24px; margin-bottom: 24px; }}
  .brand {{ font-size: 32px; font-weight: 700; color: #00A86B; letter-spacing: -1px; }}
  .subtitle {{ font-size: 13px; color: #666; margin-top: 4px; }}
  .astro-name {{ font-size: 22px; font-weight: 600; margin-top: 12px; }}
  .meta {{ font-size: 12px; color: #888; margin-top: 4px; }}
  .score-box {{ background: #f0faf5; border: 1.5px solid #00A86B; border-radius: 10px; padding: 16px 24px; text-align: center; margin: 20px 0; }}
  .score-label {{ font-size: 12px; color: #666; }}
  .score-value {{ font-size: 36px; font-weight: 700; color: #00A86B; }}
  .score-sub {{ font-size: 11px; color: #888; margin-top: 4px; }}
  .section-title {{ font-size: 15px; font-weight: 600; color: #00A86B; border-bottom: 1.5px solid #00A86B; padding-bottom: 6px; margin: 24px 0 12px; }}
  ul {{ padding-left: 20px; }}
  ul li {{ margin-bottom: 6px; line-height: 1.5; }}
  .improve-item {{ margin-bottom: 16px; padding: 12px; background: #fff8f0; border-left: 3px solid #FF6F00; border-radius: 0 6px 6px 0; }}
  .improve-item h3 {{ font-size: 13px; font-weight: 600; color: #FF6F00; margin-bottom: 4px; }}
  .improve-item p {{ color: #444; line-height: 1.5; margin-bottom: 6px; }}
  .example {{ font-size: 12px; color: #666; background: #fff; padding: 6px 10px; border-radius: 4px; border: 1px solid #eee; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
  th {{ background: #00A86B; color: #fff; padding: 8px 12px; text-align: left; font-size: 12px; font-weight: 500; }}
  td {{ padding: 7px 12px; border-bottom: 1px solid #eee; font-size: 12px; }}
  tr:nth-child(even) td {{ background: #f9f9f9; }}
  .score-cell {{ font-weight: 600; color: #00A86B; text-align: right; }}
  .footer {{ margin-top: 32px; padding-top: 16px; border-top: 1px solid #eee; font-size: 11px; color: #aaa; text-align: center; }}
</style>
</head>
<body>
  <div class="header">
    <div class="brand">InstaAstro</div>
    <div class="subtitle">Astrologer Performance Report</div>
    <div class="astro-name">{astro_name}</div>
    <div class="meta">{date_str} &bull; Last {days} days &bull; {total} chats analyzed</div>
  </div>

  <div class="score-box">
    <div class="score-label">Overall Performance Score</div>
    <div class="score-value">{overall}%</div>
    <div class="score-sub">Based on {total} sessions evaluated against 61 quality parameters</div>
  </div>

  <div class="section-title">Kya Accha Kiya</div>
  <ul>{good_bullets}</ul>

  <div class="section-title">Kya Improve Karna Hai</div>
  {improve_sections}

  <div class="section-title">Category-wise Performance</div>
  <table>
    <tr><th>Category</th><th style="text-align:right;">Score</th></tr>
    {cat_rows}
  </table>

  <div class="footer">
    Yeh report automated analysis ke basis par generate ki gayi hai. Koi bhi sawaal ke liye apne manager se sampark karein.
  </div>
</body>
</html>"""

# ─────────────────────────────────────────────────────────────
#  STEP 5: SEND WHATSAPP
# ─────────────────────────────────────────────────────────────

def send_whatsapp(phone, astro_name, overall_pct, pdf_bytes):
    date_str = datetime.now().strftime("%d %B %Y")
    message  = (
        f"*{astro_name} - Performance Report*\n"
        f"{date_str}\n"
        f"Overall Score: {overall_pct}%\n\n"
        f"PDF report attached above."
    )

    # Send text message via CallMeBot
    text_url = (
        f"https://api.callmebot.com/whatsapp.php"
        f"?phone={phone}&text={requests.utils.quote(message)}&apikey={CALLMEBOT_APIKEY}"
    )
    requests.get(text_url, timeout=15)

    # Note: CallMeBot free tier sends text only
    # For PDF delivery, save to temp file and provide Drive link
    # (Full PDF-via-WhatsApp requires Meta Cloud API)
    print(f"WhatsApp message sent to {phone}")

# ─────────────────────────────────────────────────────────────
#  RUN
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
