# lead_hunter.py
# Single-file scraper for Meta Ads Library, Reddit, LinkedIn
# - uses Playwright to render JS
# - outputs leads.json per Flask
#
# Requirements:
# pip install playwright requests beautifulsoup4 textstat tldextract fake-useragent
# playwright install

import time, re, json
from urllib.parse import urlparse, urljoin, quote_plus
from collections import defaultdict

import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
import tldextract
import textstat
from playwright.sync_api import sync_playwright, TimeoutError as PlayTimeoutError

# ---------------- CONFIG ----------------
QUERY_PROMPT = "Inserisci la query di ricerca: "
PLATFORMS = ["meta", "reddit", "linkedin"]
MAX_PER_PLATFORM = 200
MAX_PER_DOMAIN = 200
POLITE_SLEEP = 1.0
PAGE_TIMEOUT = 20000

SOCIAL_BLOCK_DOMAINS = {"reddit.com", "redditinc.com", "redditblog.com", "metastatus.com",
                        "reddithelp.com", "facebook.com","m.facebook.com","whatsapp.com",
                        "wa.me","t.me","t.co","bit.ly","instagram.com","docs.google.com",
                        "forms.gle","fb.com", "zoom.us"}

ua = UserAgent()
HEADERS = {"User-Agent": ua.random}

# Scoring weights
WEIGHTS = {
    "has_email": 30,
    "has_phone": 20,
    "has_contact_page": 10,
    "has_contact_form": 8,
    "has_schema": 8,
    "good_copy": 12,
    "has_cta": 12,
}

EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
PHONE_RE = re.compile(r"(?:\+?\d{1,3})?[\s\-\.\(]*\d{2,4}[\)\s\-\.\d]{5,20}")

# ---------------- HELPERS ----------------
def is_blocked_social(url):
    try:
        net = urlparse(url).netloc.lower()
        return any(s in net for s in SOCIAL_BLOCK_DOMAINS)
    except:
        return False

def extract_emails(text):
    # prendi solo la prima email valida "normale", ignora quelle con caratteri strani
    emails = EMAIL_RE.findall(text or "")
    for e in emails:
        if "@" in e and "..." not in e:
            return [e]  # ritorna subito la prima valida
    return []

def extract_phones(text):
    """
    Estrae un solo numero italiano valido dal testo.
    Numeri mobili: 3XXXXXXXXX
    Numeri fissi: 0XXXXXXXXX
    Ritorna solo il primo numero valido.
    """
    # trova numeri che iniziano con 0 o 3 e hanno almeno 9 cifre consecutive
    candidates = re.findall(r"(?:0|3)\d{8,9}", re.sub(r"\D", "", text))
    
    for p in candidates:
        if len(p) == 10:
            if p.startswith("0"):  # fisso
                return ["+39" + p]
            elif p.startswith("3"):  # mobile
                return ["+39" + p]
    return []


def domain_of(url):
    try:
        return tldextract.extract(url).registered_domain or urlparse(url).netloc
    except:
        return urlparse(url).netloc

def safe_text(soup):
    return (soup.get_text(" ", strip=True) if soup else "")

def copy_quality_score(text):
    if not text or len(text.split()) < 20:
        return 0.0
    try:
        flesch = textstat.flesch_reading_ease(text)
        return 1.0 if flesch >= 60 else 0.6 if flesch >= 40 else 0.3
    except:
        return 0.0

def has_schema_org(soup):
    if not soup: return False
    return bool(soup.find_all(attrs={"itemscope": True}) or soup.find_all("script", type="application/ld+json"))

def has_cta(soup):
    if not soup: return False
    cta_words = ["iscriviti","prenota","contattaci","scopri","richiedi","invia","book","sign up","subscribe","free","ottieni"]
    for el in soup.find_all(["a","button"]):
        txt = (el.get_text() or "").lower()
        if any(w in txt for w in cta_words):
            return True
    return False

def find_contact_page(soup, base_url):
    if not soup: return None
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        txt = (a.get_text() or "").lower()
        if any(k in href for k in ["contact","contatt","contatti","contatto","contacts","contact-us"]) or \
           any(k in txt for k in ["contatt","contact","contatti","contatto"]):
            return urljoin(base_url, a["href"])
    return None

def normalize_score(raw):
    return max(0, min(100, int(round(raw))))

# ---------------- PLATFORM SCRAPERS ----------------
def scrape_meta_ads(page, query, max_items):
    out = []
    q = quote_plus(query)
    url = f"https://www.facebook.com/ads/library/?active_status=all&ad_type=all&country=IT&q={q}"
    try: page.goto(url, timeout=PAGE_TIMEOUT)
    except: pass
    time.sleep(2)
    soup = BeautifulSoup(page.content(), "html.parser")
    candidates = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "l.facebook.com/l.php" in href:
            m = re.search(r"u=(https%3A%2F%2F[^&]+)", href)
            landing = requests.utils.unquote(m.group(1)) if m else href
        else:
            landing = href
        if landing.startswith("http") and not is_blocked_social(landing):
            candidates.append((a.get_text() or "", landing))
    seen = set()
    for txt, landing in candidates:
        landing_norm = landing.split("?")[0]
        if landing_norm in seen: continue
        seen.add(landing_norm)
        out.append({"platform":"meta","title":txt.strip()[:200],"text":txt.strip(),"landing":landing})
        if len(out)>=max_items: break
    return out

def scrape_reddit(page, query, max_items):
    out=[]
    url=f"https://www.reddit.com/search/?q={quote_plus(query)}&type=link"
    try: page.goto(url, timeout=PAGE_TIMEOUT)
    except: pass
    time.sleep(1)
    soup=BeautifulSoup(page.content(), "html.parser")
    seen=set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        txt = a.get_text(" ",strip=True)[:200]
        if href.startswith("http") and "reddit.com" not in href and not is_blocked_social(href):
            if href in seen: continue
            seen.add(href)
            out.append({"platform":"reddit","title":txt,"text":txt,"landing":href})
            if len(out)>=max_items: break
    return out

def scrape_linkedin(page, query, max_items):
    out=[]
    url=f"https://www.linkedin.com/search/results/content/?keywords={quote_plus(query)}"
    try: page.goto(url, timeout=PAGE_TIMEOUT)
    except: pass
    time.sleep(1)
    soup=BeautifulSoup(page.content(),"html.parser")
    seen=set()
    for a in soup.find_all("a", href=True):
        href=a["href"]
        txt=a.get_text(" ",strip=True)[:200]
        if href.startswith("http") and "linkedin.com" not in href and not is_blocked_social(href):
            if href in seen: continue
            seen.add(href)
            out.append({"platform":"linkedin","title":txt,"text":txt,"landing":href})
            if len(out)>=max_items: break
    return out

# ---------------- LANDING ANALYZER ----------------
def analyze_landing(page, landing_url):
    info={"url":landing_url,"emails":[],"phones":[],"contact_page":None,"has_contact_form":False,
          "has_schema":False,"has_cta":False,"copy_quality":0.0,"title":"","score":0}
    try:
        page.goto(landing_url, timeout=PAGE_TIMEOUT)
        time.sleep(1)
        html=page.content()
    except PlayTimeoutError:
        try: html=requests.get(landing_url, headers=HEADERS, timeout=8).text
        except: html=""
    except: html=""
    if not html: return info
    soup=BeautifulSoup(html,"html.parser")
    info["title"]=soup.title.string.strip() if soup.title else ""
    txt=safe_text(soup)
    info["emails"]=extract_emails(html+" "+txt)
    info["phones"]=extract_phones(html+" "+txt)
    info["contact_page"]=find_contact_page(soup, landing_url)
    info["has_contact_form"]=bool(soup.find("form"))
    info["has_schema"]=has_schema_org(soup)
    info["has_cta"]=has_cta(soup)
    info["copy_quality"]=copy_quality_score(" ".join([p.get_text(" ",strip=True) for p in soup.find_all(["p","h1","h2","h3"])]))
    # compute score
    raw=0
    if info["emails"]: raw+=WEIGHTS["has_email"]
    if info["phones"]: raw+=WEIGHTS["has_phone"]
    if info["contact_page"]: raw+=WEIGHTS["has_contact_page"]
    if info["has_contact_form"]: raw+=WEIGHTS["has_contact_form"]
    if info["has_schema"]: raw+=WEIGHTS["has_schema"]
    raw+=int(info["copy_quality"]*WEIGHTS["good_copy"])
    if info["has_cta"]: raw+=WEIGHTS["has_cta"]
    info["score"]=normalize_score(raw)
    return info

# ---------------- PIPELINE ----------------
def run_pipeline(query):
    results=[]
    per_platform=defaultdict(int)
    per_domain_count=defaultdict(int)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        page=browser.new_page()
        try: page.set_extra_http_headers({"User-Agent":ua.random})
        except: pass
        for platform in PLATFORMS:
            if per_platform[platform]>=MAX_PER_PLATFORM: continue
            if platform=="meta": candidates=scrape_meta_ads(page, query, MAX_PER_PLATFORM)
            elif platform=="reddit": candidates=scrape_reddit(page, query, MAX_PER_PLATFORM)
            elif platform=="linkedin": candidates=scrape_linkedin(page, query, MAX_PER_PLATFORM)
            else: candidates=[]
            for cand in candidates:
                if per_platform[platform]>=MAX_PER_PLATFORM: break
                landing=cand.get("landing")
                if not landing or is_blocked_social(landing): continue
                dom=domain_of(landing)
                if per_domain_count[dom]>=MAX_PER_DOMAIN: continue
                try: analysis=analyze_landing(page, landing)
                except: continue
                lead={"platform":platform,"source_title":cand.get("title") or cand.get("text",""),
                      "landing":landing,"domain":dom,"analysis":analysis,
                      "collected_at":time.strftime("%Y-%m-%d %H:%M:%S")}
                results.append(lead)
                per_platform[platform]+=1
                per_domain_count[dom]+=1
                time.sleep(POLITE_SLEEP)
        browser.close()
    # sort by score
    sorted_results=sorted(results,key=lambda r:r["analysis"].get("score",0),reverse=True)
    # save JSON
    with open("leads.json","w",encoding="utf-8") as f:
        json.dump(sorted_results,f,ensure_ascii=False,indent=2)
    print(f"Saved {len(sorted_results)} leads to leads.json")
    return sorted_results

# ---------------- ENTRY ----------------
if __name__=="__main__":
    q=input(QUERY_PROMPT).strip()
    if not q: print("Query vuota, esco.")
    else:
        print("Avvio pipeline scraping...")
        run_pipeline(q)
        print("Finito.")
