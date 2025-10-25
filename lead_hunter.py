# lead_hunter.py

import os
import re
import csv
import time
import requests
import textstat
import tldextract
from bs4 import BeautifulSoup
from urllib.parse import urlparse

# ----------------------------- CONFIG -----------------------------
MAX_LEADS_PER_SITE = 30
OUTPUT_TXT = "leads_report.txt"

# ----------------------------- FUNZIONI -----------------------------

def extract_emails(text):
    return list(set(re.findall(r"[\w\.-]+@[\w\.-]+", text)))

def extract_phones(text):
    return list(set(re.findall(r"(?:\+?\d{1,3})?[\s.-]?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}", text)))

def fetch_page(url):
    try:
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return ""

def score_lead(title, text, emails, phones):
    score = 0
    if len(emails) > 0:
        score += 2
    if len(phones) > 0:
        score += 2
    if "contattaci" in text.lower() or "lezione gratuita" in text.lower():
        score += 3
    readability = 100 - textstat.flesch_reading_ease(text)
    if readability < 60:
        score += 2
    if len(title.split()) > 3:
        score += 1
    return score

def analyze_url(url, position):
    html = fetch_page(url)
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    title = soup.title.string if soup.title else ""
    emails = extract_emails(text)
    phones = extract_phones(text)

    return {
        "url": url,
        "title": title,
        "emails": emails,
        "phones": phones,
        "score": score_lead(title, text, emails, phones),
        "position": position
    }

# ----------------------------- MAIN -----------------------------

def main():
    query = input("Inserisci la query di ricerca: ")
    urls = []
    with open("seed_urls.txt", "r") as f:
        urls = [u.strip() for u in f.readlines() if u.strip()]

    leads = []
    for position, url in enumerate(urls[:MAX_LEADS_PER_SITE]):
        print(f"Analizzo {url} ({position+1}/{MAX_LEADS_PER_SITE})...")
        lead = analyze_url(url, position+1)
        if lead:
            leads.append(lead)
        time.sleep(1)

    leads = sorted(leads, key=lambda x: x["score"], reverse=True)

    with open(OUTPUT_TXT, "w", encoding="utf-8") as f:
        for i, lead in enumerate(leads, 1):
            f.write(f"LEAD #{i}\n")
            f.write(f"Posizione: {lead['position']}\n")
            f.write(f"URL: {lead['url']}\n")
            f.write(f"Titolo: {lead['title']}\n")
            f.write(f"Email: {', '.join(lead['emails'])}\n")
            f.write(f"Telefono: {', '.join(lead['phones'])}\n")
            f.write(f"Score: {lead['score']}\n")
            f.write("-" * 60 + "\n\n")

    print(f"\nSalvato in {OUTPUT_TXT}")

if __name__ == "__main__":
    main()
