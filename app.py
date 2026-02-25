from flask import Flask, request, jsonify
from flask_cors import CORS
import mysql.connector
import json
import os

# ------------------ ENV LOADING ------------------
from dotenv import load_dotenv, dotenv_values

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
ENV_PATH = os.path.join(BASE_DIR, '.env')
load_dotenv(ENV_PATH)
ENV = dotenv_values(ENV_PATH)

# ------------------ GEMINI CONFIG (NEW SDK) ------------------
from google import genai

GEMINI_API_KEY = ENV.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY not set in .env")

client = genai.Client(api_key=GEMINI_API_KEY)

print("[backend] Gemini API configured: YES (google.genai)")

# ------------------ FLASK APP ------------------
app = Flask(__name__)
CORS(app)

# ---------- TEST DB CONNECTION ----------
@app.route("/test-db")
def test_db():
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT 1")
        return jsonify({"success": True, "message": "✅ Database connected successfully!"})
    except Exception as e:
        return jsonify({"success": False, "message": f"❌ DB Connection failed: {str(e)}"}), 500

# ------------------ DATABASE CONNECTION ------------------
def get_db():
    return mysql.connector.connect(
        host=os.environ.get("DB_HOST"),
        user=os.environ.get("DB_USER"),
        password=os.environ.get("DB_PASSWORD"),
        database=os.environ.get("DB_NAME"),
        port=int(os.environ.get("DB_PORT", 3306))
    )

# ------------------ AI LOGIC (UPDATED GEMINI) ------------------
def ai_answer(question, language="English"):
    # Map language names to language codes
    lang_map = {
        'English': 'English',
        'Hindi': 'Hindi',
        'Marathi': 'Marathi',
        'en': 'English',
        'hi': 'Hindi',
        'mr': 'Marathi'
    }
    
    target_language = lang_map.get(language, 'English')
    
    prompt = f"""
You are an agriculture assistant for Indian farmers.

IMPORTANT: Respond ONLY in {target_language}.

Rules:
- Use simple language appropriate for Indian farmers
- Do NOT give pesticide dosage, chemical quantities, or medical advice
- If unsure, suggest consulting a local agriculture officer
- ALL response text must be in {target_language}

Return ONLY valid JSON with these fields:
- fertilizers: array of objects, each with "name" (fertilizer type) and "quantity_per_acre" (amount needed per acre in kg or liters)
- overall_analysis: string (general recommendation summary in {target_language})
- soil_analysis_and_tips: array of strings with soil-specific tips (each tip in {target_language})

Example format:
{{
  "fertilizers": [
    {{"name": "DAP", "quantity_per_acre": "50 kg"}},
    {{"name": "Urea", "quantity_per_acre": "100 kg"}}
  ],
  "overall_analysis": "Based on soil pH and nutrient levels...",
  "soil_analysis_and_tips": ["Tip 1", "Tip 2", "Tip 3"]
}}

Question:
{question}
"""

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=prompt
        )

        raw = response.text.strip()

        if raw.startswith("```"):
            raw = raw.replace("```json", "").replace("```", "").strip()

        try:
            return json.loads(raw)
        except Exception:
            return {"overall_analysis": raw}

    except Exception as e:
        print("Gemini API error:", e)
        return {"overall_analysis": "AI service temporarily unavailable."}
# ------------------ ROUTES ------------------

@app.route("/")
def home():
    return "Kisan AI Backend Running (Gemini – Updated)"

# ---------- SIGNUP ----------
@app.route("/signup", methods=["POST"])
def signup():
    data = request.json
    db = get_db()
    cursor = db.cursor()

    try:
        cursor.execute(
            "INSERT INTO farmers (name, phone, password, village) VALUES (%s,%s,%s,%s)",
            (data["name"], data["phone"], data["password"], data.get("village"))
        )
        db.commit()
        return jsonify({"success": True})
    except Exception as e:
        print(e)
        return jsonify({"success": False, "message": "Phone already registered"}), 400

# ---------- LOGIN ----------
@app.route("/login", methods=["POST"])
def login():
    data = request.json
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute(
        "SELECT id, name FROM farmers WHERE phone=%s AND password=%s",
        (data["phone"], data["password"])
    )

    farmer = cursor.fetchone()

    if farmer:
        return jsonify({
            "success": True,
            "farmer_id": farmer["id"],
            "name": farmer["name"]
        })
    else:
        return jsonify({"success": False, "message": "Invalid credentials"}), 401

# ---------- ASK QUESTION ----------
@app.route("/ask", methods=["POST"])
def ask():
    data = request.json

    farmer_id = data.get("farmer_id")
    question = data.get("question", "").strip()
    language = data.get("language", "en")

    if not farmer_id or not question:
        return jsonify({"success": False, "message": "Missing fields"}), 400

    answer_obj = ai_answer(question, language)

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        "INSERT INTO queries (farmer_id, question, answer, language) VALUES (%s,%s,%s,%s)",
        (farmer_id, question, json.dumps(answer_obj), language)
    )
    db.commit()

    return jsonify({"success": True, "answer": answer_obj})

# ---------- SESSION (FARM DATA + RECOMMENDATIONS) ----------
@app.route("/session", methods=["POST"])
def session():
    data = request.json

    farmer_id = data.get("farmer_id")
    crop = data.get("crop", "").strip()
    soil_type = data.get("soil_type", "").strip()
    ph = data.get("ph")
    nitrogen_ppm = data.get("nitrogen_ppm")
    phosphorus_ppm = data.get("phosphorus_ppm")
    potassium_ppm = data.get("potassium_ppm")
    water_availability = data.get("water_availability", "").strip()
    budget_range = data.get("budget_range", "").strip()
    language = data.get("language", "en")

    # Validation
    if not farmer_id or not crop or not soil_type or ph is None:
        return jsonify({"success": False, "message": "Missing required fields"}), 400

    # Compose recommendation question for AI
    farm_details = f"Crop: {crop}; Soil type: {soil_type}; Soil pH: {ph}; Nitrogen (ppm): {nitrogen_ppm}; Phosphorus (ppm): {phosphorus_ppm}; Potassium (ppm): {potassium_ppm}; Water availability: {water_availability}; Budget: {budget_range}"
    
    recommendation_question = f"Provide fertilizer recommendations per acre for the following farm: {farm_details}. Return recommended N-P-K amounts per acre, suggested fertilizer products, application timing, and a short justification. Keep result concise."

    # Get AI recommendation in selected language
    recommendation_obj = ai_answer(recommendation_question, language)

    # Save session to database
    db = get_db()
    cursor = db.cursor()

    try:
        # Save to sessions table
        cursor.execute(
            """INSERT INTO sessions 
            (farmer_id, crop, soil_type, ph, nitrogen_ppm, phosphorus_ppm, potassium_ppm, water_availability, budget_range, recommendation) 
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (farmer_id, crop, soil_type, float(ph), int(nitrogen_ppm or 0), int(phosphorus_ppm or 0), int(potassium_ppm or 0), water_availability, budget_range, json.dumps(recommendation_obj))
        )
        
        # Also save to queries table so it shows up in history
        cursor.execute(
            "INSERT INTO queries (farmer_id, question, answer, language) VALUES (%s,%s,%s,%s)",
            (farmer_id, recommendation_question, json.dumps(recommendation_obj), language)
        )
        
        db.commit()
        return jsonify({"success": True, "answer": recommendation_obj})
    except Exception as e:
        print("Session error:", e)
        return jsonify({"success": False, "message": str(e)}), 400

# ---------- HISTORY ----------
@app.route("/history/<int:farmer_id>", methods=["GET"])
def history(farmer_id):
    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute(
        "SELECT question, answer, created_at FROM queries WHERE farmer_id=%s ORDER BY created_at DESC",
        (farmer_id,)
    )

    rows = cursor.fetchall()

    for r in rows:
        try:
            r["answer"] = json.loads(r["answer"])
        except Exception:
            pass

    return jsonify(rows)

# ------------------ RUN SERVER ------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)