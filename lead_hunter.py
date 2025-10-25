# leads_hunter.py
# Single-file scraper for Meta Ads Library, Reddit, LinkedIn
# - uses Playwright to render JS
# - no APIs, only public scraping
# - outputs leads_report.txt, leads_sorted_by_score.txt, top25.txt
#
# Requirements:
# pip install playwright requests beautifulsoup4 textstat tldextract fake-useragent
# playwright install

import time
import re
import json
import math
from urllib.parse import quote_plus, urlparse, urljoin
from collections import defaultdict
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
import tldextract
import textstat

from playwright.sync_api import sync_playwright, TimeoutError as PlayTimeoutError

# ------------------------ CONFIG ------------------------
QUERY_PROMPT = "Inserisci la query di ricerca (es. lezione gratuita): "
PLATFORMS = ["meta", "reddit", "linkedin"]
MAX_PER_PLATFORM = 100            # massimo lead raccolti per piattaforma
MAX_PER_DOMAIN = 100              # massimo lead per singolo dominio
POLITE_SLEEP = 1.0               # pausa fra richieste (s)
PAGE_TIMEOUT = 20000             # ms playwrght page load timeout

OUTPUT_REPORT = "leads_report.txt"
OUTPUT_SORTED = "leads_sorted_by_score.txt"
OUTPUT_TOP25 = "top25.txt"

SOCIAL_BLOCK_DOMAINS = {"facebook.com", "m.facebook.com", "whatsapp.com", "wa.me", "t.me", "t.co", "bit.ly", "instagram.com", "docs.google.com", "forms.gle", "fb.com", "metastatus.com"}

ua = UserAgent()
HEADERS = {"User-Agent": ua.random}

# Scoring weights (customizzabili)
WEIGHTS = {
    "has_email": 30,
    "has_phone": 20,
    "has_contact_page": 10,
    "has_contact_form": 8,
    "has_schema": 8,
    "good_copy": 12,   # readability/length
    "has_cta": 12,
}


# ------------------------ HELPERS ------------------------
def is_blocked_social(url):
    try:
        net = urlparse(url).netloc.lower()
        for s in SOCIAL_BLOCK_DOMAINS:
            if s in net:
                return True
    except Exception:
        return False
    return False


EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
PHONE_RE = re.compile(r"(?:\+?\d{1,3})?[\s\-\.\(]*\d{2,4}[\)\s\-\.\d]{5,20}")

def extract_emails(text):
    return list(set(EMAIL_RE.findall(text or "")))

def extract_phones(text):
    phones = list(set([p.strip() for p in PHONE_RE.findall(text or "") if len(re.sub(r"\D","",p))>=6]))
    return phones

def domain_of(url):
    try:
        return tldextract.extract(url).registered_domain or urlparse(url).netloc
    except Exception:
        return urlparse(url).netloc

def safe_text(soup):
    return (soup.get_text(" ", strip=True) if soup else "")

def copy_quality_score(text):
    if not text or len(text.split()) < 20:
        return 0.0
    try:
        flesch = textstat.flesch_reading_ease(text)
        # normalize: 0..1 where higher is better (easier to read)
        if flesch >= 60:
            return 1.0
        elif flesch >= 40:
            return 0.6
        else:
            return 0.3
    except Exception:
        # fallback by avg sentence length
        words = re.findall(r"\w+", text)
        sentences = re.split(r'[.!?]+', text)
        if not sentences:
            return 0.0
        avg = len(words)/max(1,len(sentences))
        if avg <= 15:
            return 1.0
        elif avg <= 20:
            return 0.6
        else:
            return 0.3

def has_schema_org(soup):
    if not soup: return False
    if soup.find_all(attrs={"itemscope": True}): return True
    if soup.find_all("script", type="application/ld+json"): return True
    return False

def has_cta(soup):
    if not soup: return False
    cta_words = ["iscriviti","prenota","contattaci","scopri","richiedi","invia","book","sign up","subscribe","free","ottieni"]
    for el in soup.find_all(["a","button"]):
        txt = (el.get_text() or "").lower()
        for w in cta_words:
            if w in txt:
                return True
    return False

def find_contact_page(soup, base_url):
    if not soup: return None
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        txt = (a.get_text() or "").lower()
        if any(k in href for k in ["contact","contatt","contatti","contatto","contacts","contact-us"]):
            return urljoin(base_url, a["href"])
        if any(k in txt for k in ["contatt","contact","contatti","contatto"]):
            return urljoin(base_url, a["href"])
    return None

def normalize_score(raw):
    # raw is positive; map to 0..100
    # choose a soft cap
    return max(0, min(100, int(round(raw))))

# ------------------------ PLATFORM SCRAPERS ------------------------
# Each scraper returns a list of candidate landing URLs (and minimal metadata)
# Max per platform = MAX_PER_PLATFORM

def scrape_meta_ads(page, query, max_items):
    """Scrapes Meta Ads Library page for the query and returns list of dicts:
       {'platform':'meta','ad_title':..,'ad_text':..,'landing':..}"""
    out = []
    q = quote_plus(query)
    # Meta Ads Library - country IT and all active status
    url = f"https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=IT&q={q}"
    try:
        page.goto(url, timeout=PAGE_TIMEOUT)
        time.sleep(2)
    except Exception:
        pass

    # wait a bit for JS to render
    time.sleep(2)
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")

    # Heuristic: look for anchors that point to external landing (not facebook domain)
    candidates = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("https://") or href.startswith("http://") or "l.facebook.com/l.php" in href:
            # try to get absolute or redirect link
            if "l.facebook.com/l.php" in href:
                # sometimes the target is in 'u=' param - attempt to extract
                m = re.search(r"u=(https%3A%2F%2F[^&]+)", href)
                if m:
                    landing = requests.utils.unquote(m.group(1))
                else:
                    landing = href
            else:
                landing = href
            if not is_blocked_social(landing):
                candidates.append((a.get_text() or "", landing))
    # dedupe maintaining order
    seen = set()
    for txt, landing in candidates:
        landing_norm = landing.split("?")[0]
        if landing_norm in seen: continue
        seen.add(landing_norm)
        out.append({"platform":"meta","title":txt.strip()[:200],"text":txt.strip(),"landing":landing})
        if len(out) >= max_items: break

    return out

def scrape_reddit(page, query, max_items):
    """Search Reddit for the query and return promoted-ish posts and their links"""
    out = []
    q = quote_plus(query)
    url = f"https://www.reddit.com/search/?q={q}&type=link"
    try:
        # request with proper UA header
        page.goto(url, timeout=PAGE_TIMEOUT)
        time.sleep(2)
    except Exception:
        pass
    time.sleep(1)
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")

    # Reddit structure may mark sponsored/promoted posts or may require detection:
    posts = soup.find_all("a", href=True)
    seen = set()
    for a in posts:
        href = a["href"]
        txt = a.get_text(" ", strip=True)[:200]
        # heuristics: external links or links containing 'promoted' in ancestor
        # Accept external links (not reddit.com links) and those with 'promoted' text
        is_promoted = False
        # check ancestor text for 'promoted'
        anc = a.find_parent()
        if anc and "promoted" in anc.get_text(" ").lower():
            is_promoted = True
        # external landing
        if href.startswith("http") and "reddit.com" not in href:
            landing = href
            if is_blocked_social(landing): 
                continue
            landing_norm = landing.split("?")[0]
            if landing_norm in seen: 
                continue
            seen.add(landing_norm)
            out.append({"platform":"reddit","title":txt,"text":txt,"landing":landing})
            if len(out) >= max_items: break

    # fallback: if none found, also collect reddit-post links that may point to external landing via outbound anchors
    return out[:max_items]

def scrape_linkedin(page, query, max_items):
    """Attempt to get public LinkedIn content results for query (no login)"""
    out = []
    q = quote_plus(query)
    url = f"https://www.linkedin.com/search/results/content/?keywords={q}"
    try:
        page.goto(url, timeout=PAGE_TIMEOUT)
        time.sleep(2)
    except Exception:
        pass
    time.sleep(1)
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")

    # Look for anchors pointing to external websites (company pages often have external links)
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        txt = a.get_text(" ", strip=True)[:200]
        if href.startswith("http") and "linkedin.com" not in href:
            landing = href
            if is_blocked_social(landing): continue
            landing_norm = landing.split("?")[0]
            if landing_norm in seen: continue
            seen.add(landing_norm)
            out.append({"platform":"linkedin","title":txt,"text":txt,"landing":landing})
            if len(out) >= max_items: break

    # LinkedIn is usually behind login; this is best-effort for public assets
    return out[:max_items]


# ------------------------ LANDING ANALYZER ------------------------
def analyze_landing(page, landing_url):
    info = {"url": landing_url, "emails": [], "phones": [], "contact_page": None,
            "has_contact_form": False, "has_schema": False, "has_cta": False,
            "copy_quality": 0.0, "title": "", "score": 0}
    try:
        page.goto(landing_url, timeout=PAGE_TIMEOUT)
        time.sleep(1.2)
    except PlayTimeoutError:
        # try a requests fallback
        try:
            r = requests.get(landing_url, headers=HEADERS, timeout=8)
            html = r.text if r.status_code==200 else ""
        except Exception:
            html = ""
    except Exception:
        html = ""
    else:
        html = page.content()

    if not html:
        return info

    soup = BeautifulSoup(html, "html.parser")
    info["title"] = (soup.title.string.strip() if soup.title and soup.title.string else "")
    txt = safe_text(soup)
    info["emails"] = extract_emails(html + " " + txt)
    info["phones"] = extract_phones(html + " " + txt)
    info["contact_page"] = find_contact_page(soup, landing_url)
    info["has_contact_form"] = bool(soup.find("form"))
    info["has_schema"] = has_schema_org(soup)
    info["has_cta"] = has_cta(soup)
    info["copy_quality"] = copy_quality_score(" ".join([p.get_text(" ",strip=True) for p in soup.find_all(["p","h1","h2","h3"])]))

    # compute raw score with weights
    raw = 0
    if info["emails"]: raw += WEIGHTS["has_email"]
    if info["phones"]: raw += WEIGHTS["has_phone"]
    if info["contact_page"]: raw += WEIGHTS["has_contact_page"]
    if info["has_contact_form"]: raw += WEIGHTS["has_contact_form"]
    if info["has_schema"]: raw += WEIGHTS["has_schema"]
    raw += int(info["copy_quality"] * WEIGHTS["good_copy"])
    if info["has_cta"]: raw += WEIGHTS["has_cta"]

    info["score"] = normalize_score(raw)
    return info


# ------------------------ PIPELINE ------------------------
def run_pipeline(query):
    results = []  # flat list of dicts {platform, landing, analysis...}
    per_platform = defaultdict(int)
    per_domain_count = defaultdict(int)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        # set random UA
        try:
            page.set_extra_http_headers({"User-Agent": ua.random})
        except Exception:
            pass

        # Platform scrapers in order
        for platform in PLATFORMS:
            if per_platform[platform] >= MAX_PER_PLATFORM:
                continue

            print(f"\n--- Scraping {platform} for \"{query}\" ---")
            if platform == "meta":
                candidates = scrape_meta_ads(page, query, MAX_PER_PLATFORM)
            elif platform == "reddit":
                candidates = scrape_reddit(page, query, MAX_PER_PLATFORM)
            elif platform == "linkedin":
                candidates = scrape_linkedin(page, query, MAX_PER_PLATFORM)
            else:
                candidates = []

            # process candidates
            for cand in candidates:
                if per_platform[platform] >= MAX_PER_PLATFORM:
                    break
                landing = cand.get("landing")
                if not landing:
                    continue
                if is_blocked_social(landing):
                    continue
                dom = domain_of(landing)
                if per_domain_count[dom] >= MAX_PER_DOMAIN:
                    # skip excessive leads from same domain
                    continue

                print(f"[{platform}] analyzing landing: {landing}")
                try:
                    analysis = analyze_landing(page, landing)
                except Exception as e:
                    print("analysis error", e)
                    continue

                lead = {
                    "platform": platform,
                    "source_title": cand.get("title") or cand.get("text",""),
                    "landing": landing,
                    "domain": dom,
                    "analysis": analysis,
                    "collected_at": time.strftime("%Y-%m-%d %H:%M:%S")
                }
                results.append(lead)
                per_platform[platform] += 1
                per_domain_count[dom] += 1
                time.sleep(POLITE_SLEEP)

        browser.close()

    # sort global
    flat = []
    for r in results:
        sc = r["analysis"].get("score", 0)
        flat.append((sc, r))
    flat_sorted = sorted(flat, key=lambda x: x[0], reverse=True)
    sorted_results = [r for s,r in flat_sorted]

    # write outputs
    write_reports(sorted_results)
    return sorted_results

# ------------------------ OUTPUT ------------------------
def write_reports(sorted_results):
    # grouped by platform
    by_platform = defaultdict(list)
    for r in sorted_results:
        by_platform[r["platform"]].append(r)

    # human readable report per platform
    with open(OUTPUT_REPORT, "w", encoding="utf-8") as f:
        f.write(f"Leads report generated: {time.ctime()}\nQuery file: {time.ctime()}\n\n")
        for platform in PLATFORMS:
            f.write("="*50 + "\n")
            f.write(f"PLATFORM: {platform.upper()}\n")
            f.write("="*50 + "\n\n")
            items = by_platform.get(platform, [])
            if not items:
                f.write("No leads found\n\n")
                continue
            for idx, it in enumerate(items, 1):
                a = it["analysis"]
                f.write(f"[{idx}] Landing: {it['landing']}\n")
                f.write(f"Source title/text: {it['source_title']}\n")
                f.write(f"Domain: {it['domain']}\n")
                f.write(f"Score: {a.get('score',0)}\n")
                f.write(f"Title: {a.get('title','')}\n")
                f.write(f"Emails: {', '.join(a.get('emails',[]))}\n")
                f.write(f"Phones: {', '.join(a.get('phones',[]))}\n")
                f.write(f"Contact page: {a.get('contact_page')}\n")
                f.write(f"Has form: {a.get('has_contact_form')}\n")
                f.write(f"Has schema.org: {a.get('has_schema')}\n")
                f.write(f"Has CTA: {a.get('has_cta')}\n")
                f.write(f"Copy quality: {a.get('copy_quality')}\n")
                f.write("-"*40 + "\n")
            f.write("\n\n")

    # global sorted by score
    with open(OUTPUT_SORTED, "w", encoding="utf-8") as f:
        f.write("Global leads sorted by score (highest -> lowest)\n\n")
        for idx, it in enumerate(sorted_results, 1):
            a = it["analysis"]
            f.write(f"#{idx} | Score: {a.get('score',0)} | Platform: {it['platform']} | {it['landing']}\n")
            f.write(f"Emails: {', '.join(a.get('emails',[]))}\n")
            f.write("-"*30 + "\n")

    # top25
    with open(OUTPUT_TOP25, "w", encoding="utf-8") as f:
        f.write("Top 25 hottest leads:\n\n")
        for idx, it in enumerate(sorted_results[:25], 1):
            a = it["analysis"]
            f.write(f"#{idx} | Score: {a.get('score',0)} | Platform: {it['platform']} | {it['landing']}\n")
            f.write(f"Emails: {', '.join(a.get('emails',[]))}\n")
            f.write("-"*30 + "\n")

    print(f"\nSaved reports: {OUTPUT_REPORT}, {OUTPUT_SORTED}, {OUTPUT_TOP25}")

# ------------------------ ENTRY ------------------------
if __name__ == "__main__":
    q = input(QUERY_PROMPT).strip()
    if not q:
        print("Query vuota, esco.")
    else:
        print("Avvio pipeline. Questo può impiegare qualche minuto...")
        sorted_results = run_pipeline(q)
        print("Finito.")
