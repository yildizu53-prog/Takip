import os, json, smtplib
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.mime.text import MIMEText
from urllib.parse import urljoin, urlparse, urldefrag
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

INDEXES = {
    "Adliyeler": "https://www.adalet.gov.tr/Birimler/ACM",
    "Bölge Adliye Mahkemeleri": "https://www.adalet.gov.tr/Birimler/BAM",
    "Bölge İdare Mahkemeleri": "https://www.adalet.gov.tr/Birimler/B%C4%B0M",
}
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
    hosts = set()
    failed = []
    for category, index_url in INDEXES.items():
        html = get(index_url)
        if not html:
            failed.append(category)
            continue
        category_hosts = {
            urlparse(url).hostname
            for url, _ in links(html, index_url)
            if urlparse(url).hostname
        }
        hosts.update(category_hosts)
        print(category + " dizininden bulunan site:", len(category_hosts), flush=True)

    if not hosts:
        raise RuntimeError("Adalet Bakanlığı adliye/BAM/BİM dizinleri açılamadı; tarama durduruldu.")
    if failed:
        print("Açılamayan dizinler:", ", ".join(failed), flush=True)

    roots = sorted("https://" + host + "/" for host in hosts)
    print("Toplam benzersiz adliye/BAM/BİM sitesi:", len(roots), flush=True)
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


def parse_published_date(soup):
    selectors = [
        ('meta[property="article:published_time"]', 'content'),
        ('meta[property="og:published_time"]', 'content'),
        ('meta[name="date"]', 'content'),
        ('meta[name="publishdate"]', 'content'),
        ('meta[itemprop="datePublished"]', 'content'),
        ('time[datetime]', 'datetime'),
    ]
    for selector, attr in selectors:
        node = soup.select_one(selector)
        if node:
            value = node.get(attr) or node.get_text(' ', strip=True)
            try:
                parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc)
            except (ValueError, TypeError):
                continue
    return None


def verify_candidate(candidate):
    url, root, label = candidate
    html = get(url)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    body = " ".join(soup.get_text(" ", strip=True).split())
    if len(body) < 80:
        return None
    published_at = parse_published_date(soup)
    return {
        "url": url,
        "site": root,
        "title": page_title(html) or label or url,
        "snippet": body[:500],
        "published_at": published_at.isoformat() if published_at else None,
    }


def send_mail(items):
    recipients = ["yildizu53@gmail.com", "yooe773@gmail.com"]
    user = os.environ.get("GMAIL_USER", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    if not all((user, password)):
        raise RuntimeError("E-posta gönderimi için GMAIL_USER ve GMAIL_APP_PASSWORD GitHub Secrets alanları dolu olmalı.")

    for item in items:
        for to_addr in recipients:
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
            print("E-posta gönderildi:", to_addr, "|", item["url"], flush=True)


def send_telegram(items):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Telegram ayarları eksik; TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID GitHub Secrets alanlarına eklenmeli.", flush=True)
        return

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    for item in items:
        text = (
            "🔔 Yeni adliye duyurusu\n"
            f"Kaynak: {item['site']}\n"
            f"Başlık: {item['title']}\n"
            f"Bağlantı: {item['url']}\n\n"
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



def send_scan_success(site_count, candidate_count, verified_count, new_count):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("Başarılı tarama bildirimi için Telegram Secrets eksik.")

    message = (
        "✅ Adliye taraması başarılı!\n"
        f"Kontrol edilen adliye sitesi: {site_count}\n"
        f"Bulunan duyuru bağlantısı: {candidate_count}\n"
        f"İçeriği doğrulanan: {verified_count}\n"
        f"Yeni duyuru: {new_count}\n"
        "Sonraki tarama planı: yaklaşık 5 dakika sonra."
    )
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message},
        timeout=20,
    )
    response.raise_for_status()
    if not response.json().get("ok"):
        raise RuntimeError("Telegram tarama başarı bildirimi gönderilemedi.")
    print("Tarama başarı bildirimi Telegram'a gönderildi.", flush=True)


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
        send_scan_success(len(roots), len(candidates), len(verified), 0)
        return

    # Tarihi okunamayan veya son 24 saatten eski sayfalar bildirilmez.
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    new_items = []
    for item in verified:
        if item["url"] in seen:
            continue
        # Tarih bilgisi yoksa yeni keşfedilen bağlantıyı da bildir:
        # bazı adliye sayfaları yayın tarihini makine tarafından okunabilir biçimde sunmuyor.
        if not item.get("published_at"):
            item["snippet"] = "[Yayın tarihi sayfadan okunamadı; yeni keşfedilen bağlantı] " + item["snippet"]
            new_items.append(item)
            print("Tarihsiz yeni bağlantı bildirilecek:", item["url"], flush=True)
            continue
        try:
            published = datetime.fromisoformat(item["published_at"].replace("Z", "+00:00"))
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            if published >= cutoff:
                new_items.append(item)
            else:
                print("Eski duyuru bildirime alinmadi:", item["url"], flush=True)
        except ValueError:
            item["snippet"] = "[Yayın tarihi çözümlenemedi; yeni keşfedilen bağlantı] " + item["snippet"]
            new_items.append(item)
            print("Tarih çözümlenemedi; yeni bağlantı bildirilecek:", item["url"], flush=True)
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
    send_scan_success(len(roots), len(candidates), len(verified), len(new_items))


if __name__ == "__main__":
    main()
