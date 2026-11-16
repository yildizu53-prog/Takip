import os, json, smtplib
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.mime.text import MIMEText
from urllib.parse import urljoin, urlparse, urldefrag
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

INDEX = "https://www.adalet.gov.tr/Birimler/ACM"
STATE = "state.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AdliyeDuyuruTakip/1.1)"}
KEYWORDS = ("duyuru", "ilan", "personel", "sınav", "sinav", "alım", "alim", "mülakat", "mulakat", "sonuç", "sonuc")

# Paralel tarama: bir sitenin yanıt vermemesi tüm taramayı uzun süre bekletmesin.
REQUEST_TIMEOUT = 10
MAX_WORKERS = 32
MAX_ROOTS = 700
MAX_LINKS_PER_SITE = 40
MAX_CANDIDATES = 1200


def get(url):
    try:
        response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        if "text/html" not in response.headers.get("content-type", ""):
            return None
        return response.text
    except requests.RequestException as exc:
        print("Erişim hatası:", url, str(exc)[:140], flush=True)
        return None


def clean(url):
    return urldefrag(url)[0].rstrip("/")


def justice(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host == "adalet.gov.tr" or host.endswith(".adalet.gov.tr"))


def links(html, base):
    soup = BeautifulSoup(html, "html.parser")
    found = []
    for anchor in soup.select("a[href]"):
        url = clean(urljoin(base, anchor.get("href", "").strip()))
        label = " ".join(anchor.get_text(" ", strip=True).split())
        if justice(url) and url != clean(base):
            found.append((url, label))
    return found


def discover():
    html = get(INDEX)
    if not html:
        raise RuntimeError("Adalet Bakanlığı adliye dizini açılamadı; tarama durduruldu.")
    hosts = {urlparse(url).hostname for url, _ in links(html, INDEX)}
    roots = sorted("https://" + host + "/" for host in hosts if host)
    print("Dizinde bulunan adliye alan adı:", len(roots), flush=True)
    return roots[:MAX_ROOTS]


def relevant(label, url):
    value = (label + " " + url).lower()
    return any(keyword in value for keyword in KEYWORDS)


def page_title(html):
    soup = BeautifulSoup(html, "html.parser")
    return " ".join(soup.title.get_text(" ", strip=True).split())[:220] if soup.title else ""


def scan_root(root):
    html = get(root)
    if not html:
        return []
    candidates = {}
    for url, label in links(html, root):
        if relevant(label, url):
            candidates[url] = label
            if len(candidates) >= MAX_LINKS_PER_SITE:
                break
    return [(url, root, label) for url, label in candidates.items()]


def verify_candidate(candidate):
    url, root, label = candidate
    html = get(url)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    body = " ".join(soup.get_text(" ", strip=True).split())
    if len(body) < 80:
        return None
    return {
        "url": url,
        "site": root,
        "title": page_title(html) or label or url,
        "snippet": body[:500],
    }


def send_mail(items):
    to_addr = os.environ.get("ALERT_TO", "").strip()
    user = os.environ.get("GMAIL_USER", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    if not all((to_addr, user, password)):
        raise RuntimeError("E-posta gönderimi için ALERT_TO, GMAIL_USER ve GMAIL_APP_PASSWORD GitHub Secrets alanları dolu olmalı.")

    for item in items:
        message = MIMEText(
            "Adliye/kaynak: " + item["site"] + "\nBaşlık: " + item["title"] +
            "\nBağlantı: " + item["url"] + "\n\n" + item["snippet"][:500],
            "plain", "utf-8"
        )
        message["Subject"] = "Yeni adliye duyurusu: " + item["title"]
        message["From"] = user
        message["To"] = to_addr
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(user, password)
            smtp.send_message(message)
        print("E-posta gönderildi:", item["url"], flush=True)



def send_telegram(items):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Telegram ayarları eksik; TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID GitHub Secrets alanlarına eklenmeli.", flush=True)
        return

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    for item in items:
        text = (
            "🔔 Yeni adliye duyurusu\\n"
            f"Kaynak: {item['site']}\\n"
            f"Başlık: {item['title']}\\n"
            f"Bağlantı: {item['url']}\\n\\n"
            f"{item['snippet'][:700]}"
        )
        response = requests.post(
            api_url,
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": False},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError("Telegram API mesajı kabul etmedi.")
        print("Telegram mesajı gönderildi:", item["url"], flush=True)


def main():
    try:
        with open(STATE, encoding="utf-8") as state_file:
            state = json.load(state_file)
    except (OSError, ValueError):
        state = {}

    seen = set(state.get("seen", []))
    roots = discover()
    candidates = {}

    print("Adliye ana sayfaları paralel taranıyor...", flush=True)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(scan_root, root): root for root in roots}
        for future in as_completed(futures):
            try:
                for candidate in future.result():
                    candidates[candidate[0]] = candidate
            except Exception as exc:
                print("Site tarama hatası:", futures[future], str(exc)[:140], flush=True)

    print("Bulunan duyuru/ilan bağlantısı:", len(candidates), flush=True)
    pending = [item for url, item in candidates.items() if url not in seen][:MAX_CANDIDATES]
    print("Yeni bağlantıların içerikleri doğrulanıyor:", len(pending), flush=True)

    verified = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(verify_candidate, item) for item in pending]
        for future in as_completed(futures):
            try:
                result = future.result()
                if result:
                    verified.append(result)
            except Exception as exc:
                print("Duyuru doğrulama hatası:", str(exc)[:140], flush=True)

    if not state.get("initialized"):
        # İlk çalıştırmada yalnızca içeriği açılıp doğrulanan bağlantıları başlangıç kaydı yap.
        state = {
            "initialized": True,
            "seen": sorted(seen.union(item["url"] for item in verified)),
            "updated": datetime.now(timezone.utc).isoformat(),
        }
        with open(STATE, "w", encoding="utf-8") as state_file:
            json.dump(state, state_file, ensure_ascii=False, indent=2)
        print("İlk tarama tamamlandı: doğrulanmış başlangıç envanteri kaydedildi; e-posta gönderilmedi.", flush=True)
        return

    new_items = [item for item in verified if item["url"] not in seen]
    if new_items:
        send_mail(new_items)
        send_telegram(new_items)

    # Başarısız/erişilemeyen bağlantılar seen listesine eklenmez; sonraki taramada tekrar denenir.
    seen.update(item["url"] for item in verified)
    with open(STATE, "w", encoding="utf-8") as state_file:
        json.dump({
            "initialized": True,
            "seen": sorted(seen),
            "updated": datetime.now(timezone.utc).isoformat(),
        }, state_file, ensure_ascii=False, indent=2)
    print("Yeni doğrulanmış duyuru:", len(new_items), flush=True)


if __name__ == "__main__":
    main()
