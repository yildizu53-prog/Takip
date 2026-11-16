import os, json, smtplib
from email.mime.text import MIMEText
from urllib.parse import urljoin, urlparse, urldefrag
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

INDEX = "https://www.adalet.gov.tr/Birimler/ACM"
STATE = "state.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AdliyeDuyuruTakip/1.0)"}
KEYWORDS = ("duyuru", "ilan", "personel", "sınav", "sinav", "alım", "alim", "mülakat", "mulakat", "sonuç", "sonuc")

def get(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=18)
        r.raise_for_status()
        return r.text if "text/html" in r.headers.get("content-type", "") else None
    except requests.RequestException as e:
        print("Erişim hatası:", url, str(e)[:160])
        return None

def clean(url):
    return urldefrag(url)[0].rstrip("/")

def justice(url):
    p = urlparse(url)
    return p.scheme == "https" and (p.hostname or "").lower().endswith("adalet.gov.tr")

def links(html, base):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.select("a[href]"):
        u = clean(urljoin(base, a.get("href", "").strip()))
        label = " ".join(a.get_text(" ", strip=True).split())
        if justice(u) and u != clean(base):
            out.append((u, label))
    return out

def discover():
    html = get(INDEX)
    if not html:
        raise RuntimeError("Adalet Bakanlığı adliye dizini açılamadı; tarama durduruldu.")
    hosts = {urlparse(u).hostname for u, _ in links(html, INDEX)}
    return sorted("https://" + h + "/" for h in hosts if h)[:700]

def relevant(label, url):
    value = (label + " " + url).lower()
    return any(k in value for k in KEYWORDS)

def title(html):
    s = BeautifulSoup(html, "html.parser")
    return " ".join(s.title.get_text(" ", strip=True).split())[:220] if s.title else ""

def send_mail(items):
    to_addr, user, password = os.environ["ALERT_TO"], os.environ["GMAIL_USER"], os.environ["GMAIL_APP_PASSWORD"]
    for item in items:
        msg = MIMEText("Adliye/kaynak: " + item["site"] + "\nBaşlık: " + item["title"] + "\nBağlantı: " + item["url"] + "\n\n" + item["snippet"][:500], "plain", "utf-8")
        msg["Subject"], msg["From"], msg["To"] = "Yeni adliye duyurusu: " + item["title"], user, to_addr
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(user, password)
            s.send_message(msg)
        print("E-posta gönderildi:", item["url"])

def main():
    try:
        with open(STATE, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    seen = set(state.get("seen", []))
    discovered, verified = set(), []
    roots = discover()
    print("Bulunan alan adları:", len(roots))
    for root in roots:
        html = get(root)
        if not html:
            continue
        candidates = {}
        for u, label in links(html, root):
            if relevant(label, u):
                candidates[u] = label
        for url, label in list(candidates.items())[:120]:
            discovered.add(url)
            if url in seen:
                continue
            detail = get(url)
            if not detail:
                continue
            body = BeautifulSoup(detail, "html.parser").get_text(" ", strip=True)
            if len(body) >= 80:
                verified.append({"url": url, "site": root, "title": title(detail) or label or url, "snippet": body[:500]})
    if not state.get("initialized"):
        state = {"initialized": True, "seen": sorted(discovered), "updated": datetime.now(timezone.utc).isoformat()}
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        print("İlk tarama: başlangıç envanteri kaydedildi; e-posta gönderilmedi.")
        return
    new_items = [x for x in verified if x["url"] not in seen]
    if new_items:
        send_mail(new_items)
    seen.update(discovered)
    seen.update(x["url"] for x in verified)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump({"initialized": True, "seen": sorted(seen), "updated": datetime.now(timezone.utc).isoformat()}, f, ensure_ascii=False, indent=2)
    print("Yeni doğrulanmış duyuru:", len(new_items))

if __name__ == "__main__":
    main()
