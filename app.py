from flask import Flask, render_template, request, jsonify, send_file
import lead_hunter  # il tuo script esistente
import csv
import json
from io import StringIO
import subprocess
import os

# ---------------- PLAYWRIGHT FIX ----------------
# se i browser non sono installati, li installa automaticamente
if not os.path.exists(os.path.expanduser("~/.cache/ms-playwright")):
    print("Playwright browsers non trovati, installo Chromium...")
    subprocess.run(["playwright", "install", "chromium"], check=True)

app = Flask(__name__)

# memorizziamo gli ultimi lead raccolti in memoria
latest_leads = []

# ---------------------- ROUTES ----------------------

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/scrape', methods=['POST'])
def api_scrape():
    global latest_leads
    data = request.get_json()
    query = data.get('query', '').strip()
    if not query:
        return jsonify({"error": "Query vuota"}), 400

    try:
        leads = lead_hunter.run_pipeline(query)
        print(f"[DEBUG] Lead trovati: {len(leads)}")  # <-- aggiungi questa riga
        latest_leads = leads
        return jsonify(leads)
    except Exception as e:
        print(f"[ERROR] {str(e)}")
        return jsonify({"error": f"Errore durante l'analisi: {str(e)}"}), 500

@app.route('/export/csv')
def export_csv():
    global latest_leads
    if not latest_leads:
        return "Nessun lead da esportare", 400

    si = StringIO()
    writer = csv.writer(si)
    # header
    writer.writerow(["Platform", "Source Title", "Landing", "Domain", "Emails", "Phones", "Contact Page", "Score"])

    for lead in latest_leads:
        analysis = lead.get('analysis', {})
        writer.writerow([
            lead.get('platform', ''),
            lead.get('source_title', ''),
            lead.get('landing', ''),
            lead.get('domain', ''),
            ", ".join(analysis.get('emails', [])),
            ", ".join(analysis.get('phones', [])),
            analysis.get('contact_page', ''),
            analysis.get('score', 0)
        ])

    si.seek(0)
    return send_file(
        StringIO(si.getvalue()),
        mimetype='text/csv',
        as_attachment=True,
        download_name='leads.csv'
    )


@app.route('/export/json')
def export_json():
    global latest_leads
    if not latest_leads:
        return "Nessun lead da esportare", 400

    return jsonify(latest_leads)


if __name__ == '__main__':
    # usa gunicorn se disponibile, altrimenti flask
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
