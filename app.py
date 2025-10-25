from flask import Flask, render_template, request, jsonify, send_file
import lead_hunter  # il tuo script esistente
import csv
import json
from io import StringIO

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
        # avvia pipeline scraping
        leads = lead_hunter.run_pipeline(query)
        latest_leads = leads  # salva in memoria per esportazioni
        return jsonify(leads)
    except Exception as e:
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
    app.run(debug=True)
