# Türkiye Adliyeleri Duyuru Takibi

Adalet Bakanlığının resmî adliye dizininden başlayarak adalet.gov.tr alan adlarındaki duyuru ve ilan bağlantılarını tarayan GitHub Actions başlangıç projesi.

## Kurulum
Settings → Secrets and variables → Actions bölümünden şu secret değerlerini ekleyin:
- ALERT_TO: Bildirim alıcısı (yildizu53@gmail.com)
- GMAIL_USER: Gönderici Gmail hesabı
- GMAIL_APP_PASSWORD: Google App Password. Normal hesap parolanızı kullanmayın ve sohbet içinde paylaşmayın.

Actions sekmesinden workflow'u etkinleştirin ve Run workflow ile ilk taramayı başlatın.

## Çalışma
- Workflow 10 dakikada bir tetiklenecek şekilde ayarlanmıştır. GitHub çalıştırmaları gecikebilir; kesin 10 dakika garantisi yoktur.
- İlk tarama mevcut bağlantıları başlangıç envanterine kaydeder ve eski duyurular için e-posta göndermez.
- Sonraki taramalarda yeni görülen, açılabilen ve anlamlı HTML içeriği bulunan bağlantılar e-postalanır.
- Durum state.json dosyasında tutulur.

## Sınırlamalar
Bu genel amaçlı başlangıç sürümüdür. Her adliye sitesinin arşivini, JavaScript ile yüklenen içerikleri, RSS akışlarını veya alt sayfalardaki tüm duyuruları eksiksiz yakalayacağı garanti edilmez. Canlı kullanım öncesinde kapsam ayrıca doğrulanmalıdır.
