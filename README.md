Disclaimer: This software can damage your hardware if used carelessly use at your OWN RISK! Author is not responsible for any errors or damages caused by this software

# RyzenAdj GUI

AMD Ryzen (Alienware M16 R1 AMD) güç yönetimi, GPU V/F eğrisi (nvcurve) ve
oyun/sistem optimizasyonları için PySide6 GUI + sistem tepsisi.

## Kurulum

```bash
chmod +x install.sh
sudo ./install.sh
```

Eski (bu projenin önceki, `SCRIPT_DIR`'e göreceli dizin kullanan) bir
kurulumdan geliyorsanız ve gerçek profil verileriniz farklı bir yerdeyse:

```bash
sudo ./install.sh --migrate-profiles /home/KULLANICI/Ryzen/profiles
```

`install.sh`, `$SUDO_USER`'ın ev dizini altında `Ryzen/profiles` klasörünü
de otomatik algılamaya çalışır; `--migrate-profiles` sadece farklı bir yol
belirtmeniz gerektiğinde kullanılır.

Tray'in KDE/GNOME oturum açılışında otomatik başlamasını istemiyorsanız:

```bash
sudo ./install.sh --no-autostart
```

Güç modu değişimlerinde parola sorulmasını hiç istemiyorsanız (varsayılan
davranış budur, `wheel`/`sudo` grubundaki kullanıcılar için), hiçbir şey
yapmanıza gerek yok — kurulum bunu otomatik ayarlar. Bunun yerine her
seferinde ya da kısa süreli önbellekli parola sorulmasını tercih
ediyorsanız:

```bash
sudo ./install.sh --no-passwordless
```

## Kaldırma

```bash
sudo ./uninstall.sh            # uygulamayı kaldırır, profilleri korur
sudo ./uninstall.sh --purge    # profilleri de siler
```

## Bilinen sorunlar ve düzeltmeleri (bu sürümde çözüldü)

**"Tray, GUI'den değiştirilen profili göstermiyor / bazen çarpı işareti
hiç görünmüyor" (kök neden ve asıl çözüm):** Önceki düzeltme (okuma sırası
+ "yakın zamanda biz uyguladık" damgası) sorunu tam çözmüyordu, çünkü
tray'in profil değişimini öğrenmesinin TEK yolu ACPI `platform_profile`
sysfs'ini izlemekti. Gerçek yarış durumu şuydu:

1. `alienfx_cli`'nin ACPI'yi değiştirmesi `apply_profile()`'ın EN BAŞINDA
   olur.
2. GUI'nin gerçek profil ismini durum dosyasına yazması ise işlem
   TAMAMEN bittikten SONRA olur (ryzenadj limitleri, fan boost, vs.
   uygulandıktan sonra).
3. Tray'in ACPI-izleyicisi olayı (1)'de, yani doğru isim henüz
   yazılmadan ÖNCE görüyordu; o an durum dosyasında hâlâ ESKİ profil
   ismi vardı. Daha sonra (3)'te GUI doğru ismi yazdığında ise ACPI bir
   daha değişmediği için tray'i tetikleyen HİÇBİR ŞEY kalmıyordu — tray
   sonsuza kadar bir adım geride kalıyordu.

**Çözüm — push tabanlı, olay güdümlü senkronizasyon:** `ryzenadj_common.
write_active_profile()` artık durum dosyasını yazar yazmaz, bir Unix
domain socket üzerinden tray'e (çalışıyorsa) DOĞRUDAN ve ANINDA haber
veriyor (`notify_profile_changed()`). Tray tarafında (`ryzenadj_tray.py`
içindeki yeni `ProfileNotifyListener` sınıfı) bloklayan bir
`socket.accept()` bu mesajı bekliyor — **polling yok, CPU maliyeti
sıfıra yakın** (yalnızca kapanış kontrolü için 1 saniyede bir uyanıyor,
tıpkı mevcut ACPI izleyicisinin 500 ms'lik poll deseni gibi). Mesaj
geldiği an tray menüsünün önbelleği zorla geçersiz kılınıp anında
tazeleniyor (bu, "bazen çarpı işareti hiç görünmüyor" sorununu da
çözüyor) ve bir `notify-send` bildirimi gösteriliyor. Eski ACPI tabanlı
izleyici, gerçek harici değişiklikleri (örn. bir Fn tuşu kombinasyonu)
yakalamak için ikincil bir yol olarak duruyor, ama artık senkronizasyonun
tek kaynağı değil.

Tray çalışmıyorsa (ya da soket yoksa) `notify_profile_changed()` sessizce
hiçbir şey yapmaz — profil uygulama akışını asla geciktirmez ya da
kesintiye uğratmaz.

**Çift bildirim (aynı "profil değişti" popup'ı iki kez çıkıyordu):** Hem
GUI hem tray kendi `notify-send` çağrısını yapıyordu. Artık
`write_active_profile()` / `set_active_profile_state()` tray'e mesajın
gerçekten ulaşıp ulaşmadığını (`bool`) döndürüyor: tray çalışıyorsa
(mesaj teslim edildiyse) yalnızca **tray** bildirimi gösterir; GUI kendi
popup'ını göstermez. Tray çalışmıyorsa GUI, kullanıcı hiç haberdar
olmasın diye, kendi bildirimini fallback olarak gösterir. Yani normal
kullanımda (tray açıkken) tam olarak **bir** bildirim çıkar.

**Custom/G-MODE'daki "Extra Tools" ayarları (THP, sysctl'ler, lru_gen,
sched_*) diğer profillere geçince eski haliyle kalıyordu:** quiet/cool/
balanced/balanced-performance profillerinde bu ayarlar için hiç "extra"
verisi yoktu, yani bunlara geçildiğinde custom/gmode'un bıraktığı
değerler system'de aynen kalıyordu. Artık:
- `custom`/`gmode`'a geçilmeden HEMEN ÖNCE, bu önyükleme için henüz bir
  anlık görüntü alınmadıysa (`/run/ryzenadj-gui/boot_defaults.json` —
  `/run` tmpfs olduğundan bu otomatik olarak "önyükleme başına bir kez"
  anlamına gelir), mevcut (temiz) değerler `root_helper`'ın yeni
  `capture_boot_defaults` op'u ile kaydediliyor.
- `quiet`/`cool`/`balanced`/`balanced-performance`'a dönüldüğünde,
  `restore_boot_defaults` op'u bu değerleri geri yüklüyor.
- Hiç custom/gmode kullanılmadıysa capture hiç tetiklenmez, restore da
  no-op'tur (değerler zaten hâlâ önyükleme durumunda).
- GUI'deki gaming-tunables listesi (`self.gaming_settings`) artık
  `ryzenadj_wrapper.GAMING_TUNABLES`'a taşındı — tek kaynak, capture/
  restore ile UI listesi arasında sapma riski yok.

**"lru_gen" durumunun UI'da "?" göstermesi (teşhis iyileştirmesi):**
`root_helper.py`'nin `op_read_gaming_status`'ı (ve GUI'nin root'suz
ön-kontrolü) artık belirsiz bir "?" yerine ayırt edici bir sebep
gösteriyor: `(no file)` (bu path bu kernelde yok), `(no sysctl)`,
`(perm denied)`, ya da `(err: ...)`. Asıl uygulama bug'ı için aşağıdaki
maddeye bakın.

**"lru_gen" durumu UI'da "?" gösteriyordu — ve asıl önemlisi, custom/
gmode uygulanırken hiç yazılmıyordu:** Verdiğiniz komut
(`echo 5 > /sys/kernel/mm/lru_gen/enabled`) zaten koddaki path/değerle
birebir eşleşiyordu — yani yazma komutunun kendisi hep doğruydu. Asıl
bug şuydu: `extra.gaming` sözlüğü `{"lru_gen": "5", "vm.swappiness":
"10", ...}` gibi **İSİM**→değer eşlemesi kullanıyor, anahtar gerçek
sysfs path'i DEĞİL. `root_helper.py` (ve script önizlemesi) bu anahtarın
`vm.`/`kernel.` ile başlayıp başlamadığına ya da `/` ile başlayıp
başlamadığına bakarak sysctl/dosya ayrımı yapmaya çalışıyordu — ama
`"lru_gen"` ve `"sched_min_base_slice"`/`"sched_migration_cost"`/
`"sched_nr_migrate"` bu üç kalıptan hiçbirine uymuyor. Sonuç: UI'da
"recommended: 5" görünüyordu ama profil uygulandığında bu değer **hiçbir
zaman gerçekten yazılmıyordu** — sessizce atlanıyordu.

Artık `root_helper.py`'ye, `ryzenadj_wrapper.GAMING_TUNABLES` şemasından
(isim → gerçek path/type) türetilen bir `gaming_schema` gönderiliyor;
her gaming ayarı artık isimle değil, bu şemadan bulunan gerçek path/type
ile yazılıyor. Aynı düzeltme script-önizleme koduna (`_build_shell_
script_content`) da uygulandı. Ayrıca bilinmeyen bir gaming anahtarı
(şemada karşılığı olmayan) artık sessizce yutulmuyor, log'da açıkça
"unknown gaming setting (no schema)" olarak işaretleniyor.

**"Her güç modu değişiminde parola soruyor":** `com.ryzenadj.gui.policy`
dosyasında `<allow_active>auth_admin_keep_always</allow_active>` değeri
kullanılıyordu — bu, GEÇERSİZ bir polkit değeridir (polkit yalnızca `no`,
`yes`, `auth_self`, `auth_self_keep`, `auth_admin`, `auth_admin_keep`
değerlerini tanır). Geçersiz değer polkit'in bu action'ı reddetmesine ve
pkexec'in varsayılan (önbelleksiz, her seferinde parola isteyen) action'a
düşmesine yol açıyordu. Artık:
- `.policy` dosyasında geçerli `auth_admin_keep` bir yedek olarak duruyor,
- asıl yetkilendirme, yeni eklenen `/etc/polkit-1/rules.d/
  49-ryzenadj-gui.rules` JavaScript kuralından geliyor: `wheel`/`sudo`
  grubundaki yerel, aktif kullanıcılar için **tamamen parolasız** izin
  veriyor (root_helper.py'nin 0700 + allowlist + doğrulama ile zaten
  kilitli olması bunu güvenli kılıyor).

**Ayrıca (bu sürümde de düzeltildi):** `ryzenadj_wrapper.py::
apply_profile()` — asıl "profil uygula" işlevi — eskiden bir bash script'i
`sudo bash script` ile çalıştırıyordu; bu hem pkexec/Polkit mimarisini
tamamen atlıyordu hem de merkezi (root sahipli) dizinlerle çalışamazdı.
Artık `root_helper.py`'nin yeni `apply_power_profile` op'u üzerinden,
native Python ile (ara bash script'i olmadan) uygulanıyor.

## Dizin yapısı (kurulum sonrası)

Uygulama artık **Linux Filesystem Hierarchy Standard (FHS)**'e uygun,
kurulum dizininden tamamen bağımsız sabit sistem yollarını kullanır:

| Yol | İçerik | Sahiplik |
|---|---|---|
| `/usr/local/lib/ryzenadj-gui/` | Uygulama kodu (`.py`, ikonlar, `nvcurve/` paketi) | root:root, 0755 |
| `/usr/local/lib/ryzenadj-gui/root_helper.py` | Root yardımcı süreç | root:root, **0700** |
| `/usr/local/bin/ryzenadj-gui` | Başlatıcı | root:root, 0755 |
| `/usr/local/bin/ryzenadj-tray` | Başlatıcı | root:root, 0755 |
| `/etc/ryzenadj-gui/profiles/` | Güç profilleri (`.json`) | root:root, 0755 (root_helper yazar, GUI okur) |
| `/etc/nvcurve/profiles/` | nvcurve GPU V/F eğri profilleri | root:root, 0755 |
| `/var/lib/ryzenadj-gui/scripts/` | Kalıcı, üretilmiş profil aktivasyon script'leri | root:root, 0755 |
| `/run/ryzenadj-gui/scripts/` | root_helper'ın geçici script çalıştırma alanı (tmpfs) | root:root, 0700 |
| `/run/ryzenadj-gui/boot_defaults.json` | Önyükleme-anı gaming/THP ayar anlık görüntüsü (tmpfs) | root:root, 0644 |
| `/usr/share/polkit-1/actions/com.ryzenadj.gui.policy` | Polkit action | root:root, 0644 |
| `/etc/polkit-1/rules.d/49-ryzenadj-gui.rules` | Parolasız yetkilendirme kuralı (JS) | root:root, 0644 |
| `/usr/share/applications/ryzenadj-gui.desktop` | Masaüstü girişi | root:root, 0644 |
| `~/.cache/ryzenadj-gui/scripts/` | "Script Oluştur" butonunun **yerel önizleme** kopyası | kullanıcı |
| `~/.local/state/ryzenadj/` | Dönen (rotating) log dosyası | kullanıcı |
| `$XDG_RUNTIME_DIR/ryzenadj-gui/notify.sock` | GUI→tray anlık profil-değişikliği bildirimi (Unix socket) | kullanıcı, 0600 |
| `~/.config/autostart/ryzenadj-tray.desktop` | Tray oturum-açılış otomatik başlatma | kullanıcı |

Kurulum yerinden bağımsız olması sayesinde, `/usr/local/lib/ryzenadj-gui`'yi
elle farklı bir yere kopyalayıp `ryzenadj_gui.py`'yi doğrudan çalıştırmak
**artık desteklenmiyor** — tüm yollar bu sabit konumlara göre kodlanmıştır.
Geliştirme/test için `install.sh`'ı tekrar tekrar çalıştırmak en güvenlisi.

## Mimari notu: neden her şey `root_helper.py` üzerinden geçiyor

GUI hiçbir zaman root olarak çalışmaz. Donanım parametrelerini değiştiren
her işlem (`ryzenadj`, fan boost, CPU izolasyonu, GPU eğrisi, profil kaydetme)
`pkexec` ile `/usr/local/lib/ryzenadj-gui/root_helper.py`'ye devredilir.
`root_helper.py`:

- yalnızca sabit bir **allowlist**'teki operasyonları (`OPERATIONS` dict)
  kabul eder — keyfi komut/script yürütme yoktur,
- root:root sahipli ve `0700` izinlidir (kullanıcı tarafından okunamaz/
  değiştirilemez), bu yüzden Polkit `auth_admin_keep_always` (parolayı
  15 dakika önbellekte tutma) güvenle tanımlanabilir,
- tüm dosya yazımlarında path-traversal ve isim doğrulaması yapar.

Önceki bir sürümde profil **uygulama** işlemi (`ryzenadj_wrapper.py::
apply_profile`) bunun dışında kalıp bir bash script'i `sudo bash script`
ile çalıştırıyordu; bu hem bu mimariyi baypas ediyordu hem de GUI'den
başlatıldığında (TTY olmadan) genelde askıda kalıyor ya da sessizce
başarısız oluyordu. Artık o da `root_helper.py`'nin `apply_power_profile`
op'u üzerinden, native Python ile (ara bash script'i olmadan) yapılıyor.

## Projeden çıkarılan dosyalar

Aşağıdaki dosyalar hiçbir yerden import edilmiyordu / kullanılmıyordu ve
kaldırıldı:

- `tab.py` — `ryzenadj_gui.py`'de zaten var olan `_build_tab_extra_tools`
  metodunun kullanılmayan, hiç import edilmeyen bir kopyası/taslağıydı.
- `secure_qprocess.py`, `secure_privilege_escalation.py` — `ryzenadj_gui.py`
  bunları `try/except ImportError` ile "varsa" diye içe aktarmaya
  çalışıyordu ama `SECURE_MODE`/`SecureQProcess`/`run_as_root` kodun
  hiçbir yerinde kullanılmıyordu (tüm root çağrıları zaten pkexec +
  `root_helper.py`'ye taşınmıştı). Ölü kod + kafa karıştırıcı "unsafe
  mode" uyarısıyla birlikte kaldırıldı.
- `51-ryzenadj-gui.rules` — `/etc/polkit-1/rules.d/` için JavaScript
  kural dosyası **olması gerekirken** içeriği tam bir `<policyconfig>`
  XML'iydi (yani `.policy` formatı, yanlış dizin/format). `polkit`
  bunu yüklemeye çalışsaydı muhtemelen syntax hatasıyla reddederdi.
  Zaten doğru ve eksiksiz olan `com.ryzenadj.gui.policy` tek yetkilendirme
  kaynağı olarak bırakıldı.
- `scripts/` (statik, önceden üretilmiş `set_*.sh` dosyaları) — bunlar
  zaten çalışma anında `ryzenadj_wrapper.write_shell_script()` tarafından
  yeniden üretiliyor; kaynak ağacında taşımaya gerek yok.
- `redirect-tasks/` ve `redirector-*.zip` (önceki oturumda kaldırıldı) —
  CPU izolasyonunun orijinal bash uygulaması; artık tamamen
  `root_helper.py`'nin `apply_cpu_isolation`/`revert_cpu_isolation`
  op'larında (native Python, cgroup v2) yeniden yazılmış durumda.
- `root_helper.py` içindeki path-tabanlı `op_run_script` — GUI'nin tüm
  çağrıları `op_run_script_content`'e taşındığı için kullanılmayan bir
  root-yetkili kod yoluydu; saldırı yüzeyini azaltmak için kaldırıldı.

`patches/` (kernel patch'leri) geliştirici referansı olarak repoda
bırakıldı; kurulum tarafından sisteme kopyalanmaz.

## Güvenlik sertleştirmeleri (özet)

Önceki bir incelemede uygulanan ve bu ağaçta da geçerli olan düzeltmeler:
rastgele dosya yazımı/çalıştırılmasının whitelist edilmesi, path-traversal
kontrolleri, bash komut enjeksiyonuna karşı whitelist doğrulaması, CPU
izolasyonunda fork yerine `/proc` okuma, sessizce yutulan hataların
loglanması, ve callback'lerin her hata yolunda çağrılması. Ayrıntılar için
kod içindeki `K1`–`K5`, `O1`–`O7`, `Q1`–`Q10` etiketli yorumlara bakın.
