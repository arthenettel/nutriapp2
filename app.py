import os
import re
import sqlite3
from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai
from PIL import Image
import io
import base64
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

# --- CONFIGURACIÓN ---
load_dotenv()
app = Flask(__name__)
CORS(app)
DATABASE_FILE = 'nutriapp_final_local.db'

# --- LÓGICA DE BASE DE DATOS SQLITE ---
def get_db_connection():
    conn = sqlite3.connect(DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            age INTEGER,
            weight REAL,
            height REAL,
            gender TEXT,
            activity_level TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS image_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            analysis_type TEXT NOT NULL,
            result_text TEXT NOT NULL,
            calories INTEGER DEFAULT 0,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ingredient_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            ingredients_text TEXT NOT NULL,
            result_text TEXT NOT NULL,
            calories INTEGER DEFAULT 0,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    conn.commit()
    conn.close()
    print(f"Base de datos SQLite '{DATABASE_FILE}' inicializada.")

# --- CONFIGURACIÓN DE GEMINI API ---
try:
    api_key = os.getenv("GOOGLE_API_KEY")
    genai.configure(api_key=api_key)
    print("Google Gemini API configurado correctamente.")
except Exception as e:
    print(f"ERROR al configurar la API de Google: {e}")

# --- LÓGICA DE CÁLCULOS Y IA ---
def calculate_profile_data(user_data):
    if not all(key in user_data and user_data[key] is not None for key in ['weight', 'height', 'age', 'gender', 'activity_level']):
        return {"imc": None, "tdee": None, "imc_recommendation": "Completa tu perfil"}
    height_m = user_data['height'] / 100
    imc = round(user_data['weight'] / (height_m ** 2), 2)
    if user_data['gender'].lower() == 'masculino':
        bmr = 10 * user_data['weight'] + 6.25 * user_data['height'] - 5 * user_data['age'] + 5
    else: # Femenino
        bmr = 10 * user_data['weight'] + 6.25 * user_data['height'] - 5 * user_data['age'] - 161
    activity_multipliers = {'sedentario': 1.2, 'ligero': 1.375, 'moderado': 1.55, 'activo': 1.725, 'muy activo': 1.9}
    multiplier = activity_multipliers.get(user_data['activity_level'].lower(), 1.2)
    tdee = round(bmr * multiplier)
    if imc < 18.5: imc_recommendation = "Bajo peso"
    elif 18.5 <= imc < 25: imc_recommendation = "Peso saludable"
    elif 25 <= imc < 30: imc_recommendation = "Sobrepeso"
    else: imc_recommendation = "Obesidad"
    return {"imc": imc, "tdee": tdee, "imc_recommendation": imc_recommendation}

def get_gemini_response(content):
    model = genai.GenerativeModel('gemini-1.5-flash-latest')
    try:
        response = model.generate_content(content)
        return response.text
    except Exception as e:
        raise Exception(f"No se pudo obtener respuesta de la IA: {e}")

PROMPTS = {
    "analyze_elements": "Eres un nutricionista experto. Analiza la imagen y describe en formato Markdown: - Una lista de los alimentos que identificas. - Un resumen general del platillo.",
    "how_to_cook": "Eres un chef. Basado en la imagen del platillo, proporciona una receta sencilla en formato Markdown para prepararlo, incluyendo: - Ingredientes. - Pasos de preparación.",
    "nutritional_value": "Eres un nutricionista. Proporciona un análisis nutricional detallado del platillo en la imagen. Usa formato Markdown y debe incluir: - Una tabla con las columnas: Alimento, Calorías (kcal), Proteínas (g), Grasas (g), Carbohidratos (g). - Importante: Al final, añade una línea separada con el texto 'Total Calorías: [número]' con el total de la columna de calorías. - Un resumen del perfil nutricional general.",
    "similar_dishes": "Eres un recomendador de comida. Viendo el platillo en la imagen, sugiere 3 platillos alternativos con un valor nutricional similar. Describe brevemente cada uno en formato Markdown.",
    "recommend_recipe": "Eres un chef creativo. Con los siguientes ingredientes, crea una receta deliciosa y fácil de preparar. La respuesta debe estar en formato Markdown e incluir: - Nombre del platillo. - Ingredientes (usando los proporcionados). - Instrucciones paso a paso. - Un cálculo estimado del valor nutricional total del platillo, terminando con la línea 'Total Calorías: [número]'."
}

def extract_total_calories(text):
    match = re.search(r"Total Calorías:\s*(\d+)", text, re.IGNORECASE)
    return int(match.group(1)) if match else 0

# --- API ENDPOINTS ---

@app.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    if not data or 'email' not in data or 'password' not in data or 'name' not in data:
        return jsonify({"error": "Faltan email, nombre o contraseña"}), 400
    password_hash = generate_password_hash(data['password'])
    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO users (email, name, password_hash) VALUES (?, ?, ?)", (data['email'], data['name'], password_hash))
        conn.commit()
        return jsonify({"message": "Usuario registrado con éxito"}), 201
    except sqlite3.IntegrityError:
        return jsonify({"error": "El email ya está registrado"}), 409
    finally:
        conn.close()

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (data['email'],)).fetchone()
    conn.close()
    if user and check_password_hash(user['password_hash'], data['password']):
        user_data = dict(user)
        user_data.pop('password_hash') # No enviar el hash al frontend
        return jsonify({"message": "Inicio de sesión exitoso", "user": user_data}), 200
    return jsonify({"error": "Email o contraseña incorrectos"}), 401

@app.route('/profile', methods=['POST'])
def update_profile():
    data = request.get_json()
    user_id = data.pop('userId', None)
    if not user_id: return jsonify({"error": "Falta userId"}), 400
    conn = get_db_connection()
    try:
        set_clause = ", ".join([f"{key} = ?" for key in data.keys()])
        values = list(data.values())
        values.append(user_id)
        conn.execute(f"UPDATE users SET {set_clause} WHERE id = ?", tuple(values))
        conn.commit()
        return jsonify({"message": "Perfil actualizado con éxito"}), 200
    except Exception as e:
        return jsonify({"error": f"Error al actualizar el perfil: {e}"}), 500
    finally:
        conn.close()

@app.route('/profile/<int:user_id>', methods=['GET'])
def get_profile(user_id):
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    if not user: return jsonify({"error": "Usuario no encontrado"}), 404
    user_data = dict(user)
    user_data.pop('password_hash')
    health_data = calculate_profile_data(user_data)
    user_data.update(health_data)
    return jsonify(user_data)

@app.route('/analyze', methods=['POST'])
def analyze():
    data = request.get_json()
    user_id = data.get('userId')
    analysis_type = data.get('analysisType')
    if not user_id or not analysis_type: return jsonify({"error": "Faltan userId o analysisType"}), 400
    try:
        if 'image' in data:
            image_b64 = data['image']
            image = Image.open(io.BytesIO(base64.b64decode(image_b64)))
            prompt = PROMPTS.get(analysis_type, "")
            result_text = get_gemini_response([prompt, image])
            calories = extract_total_calories(result_text) if analysis_type == 'nutritional_value' else 0
            conn = get_db_connection()
            conn.execute("INSERT INTO image_history (user_id, analysis_type, result_text, calories) VALUES (?, ?, ?, ?)", (user_id, analysis_type, result_text, calories))
            conn.commit()
            conn.close()
            return jsonify({"result": result_text})
        elif 'ingredients' in data:
            ingredients = data['ingredients']
            prompt = PROMPTS['recommend_recipe'] + ingredients
            result_text = get_gemini_response(prompt)
            calories = extract_total_calories(result_text)
            conn = get_db_connection()
            conn.execute("INSERT INTO ingredient_history (user_id, ingredients_text, result_text, calories) VALUES (?, ?, ?, ?)", (user_id, ingredients, result_text, calories))
            conn.commit()
            conn.close()
            return jsonify({"result": result_text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/history/<int:user_id>', methods=['GET'])
def get_history(user_id):
    conn = get_db_connection()
    image_rows = conn.execute("SELECT * FROM image_history WHERE user_id = ? ORDER BY timestamp DESC", (user_id,)).fetchall()
    ingredient_rows = conn.execute("SELECT * FROM ingredient_history WHERE user_id = ? ORDER BY timestamp DESC", (user_id,)).fetchall()
    conn.close()
    return jsonify({"imageHistory": [dict(row) for row in image_rows], "ingredientHistory": [dict(row) for row in ingredient_rows]})

# --- EJECUCIÓN DEL SERVIDOR ---
if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)