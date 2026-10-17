# Polymarket Intelligence

פלטפורמת מחקר לשווקי Polymarket: איסוף נתונים, היסטוריה, ניתוח כיול ובדיקת אסטרטגיות.
**מחקר בלבד** — אין ארנק, אין מפתחות מסחר, אין ביצוע עסקאות.

## מה יש כרגע (גרסה 0.1)

| רכיב | מה עושה | מצב |
|---|---|---|
| `collector` | כל 15 דק׳ סורק את **כל** השווקים הפתוחים (pagination מלא, retries, dedupe) ושומר snapshot לשווקים עם נזילות ≥ $1K | נבדק מול DB אמיתי עם API מדומה |
| `backfill` | מוריד שווקים שנפתרו + היסטוריית מחירים מ-CLOB. ניתן להמשיך מאיפה שנעצר | נבדק מול DB אמיתי עם API מדומה |
| `research` | כיול (Brier, טבלת "מחיר מול מה שקרה בפועל"), ובדיקת 8 אסטרטגיות פשוטות אחרי עלויות | נבדק |
| `api` | FastAPI לקריאה בלבד | נבדק |
| `web` | דשבורד עברית RTL, מותאם אייפון: סקירה, סורק, מחקר, מערכת | נבדק ב-390px |
| `db` | PostgreSQL + TimescaleDB, דחיסה אוטומטית | |
| `backup` | pg_dump יומי, שומר 7 ימים ב-`./backups` | |
| `web` (Caddy) | HTTPS אוטומטי + סיסמה (Basic Auth) | |

**לא נבדק עדיין מול Polymarket אמיתי** — סביבת הפיתוח חסומה לדומיינים שלהם. הבדיקה האמיתית הראשונה תהיה על השרת.

### כללי יושר שבנויים בקוד
- כל שוק מקבל **WAIT**. אין "הסתברות עצמאית" עד שיהיה מודל שעבר בדיקה.
- מחיר נלקח רק מנקודה **לפני** (מועד הסגירה פחות האופק), ומועד ההתייחסות הוא המוקדם מבין סגירה ו-endDate, כדי לא "לרמות" עם מידע מהעתיד.
- תשואות מאוגדות **לפי אירוע**: 30 שווקים של אותו אירוע נספרים פעם אחת.
- "יתרון נתמך" רק אם כל רווח הסמך 95% מעל 0 ויש לפחות 30 אירועים.
- מחירי היסטוריה אינם מחירי ביצוע → מנוכה slippage (ברירת מחדל 1 סנט).

## הקמה על Oracle Cloud (חינם)

### 1. יצירת המכונה (בדפדפן, מחשב)
1. הרשמה ב-cloud.oracle.com (מבקשים כרטיס אשראי לאימות בלבד). Home region: **Israel Central (Jerusalem)** או **Germany Central (Frankfurt)**.
2. Compute → Instances → Create instance:
   - Image: **Canonical Ubuntu 24.04**
   - Shape: **VM.Standard.A1.Flex**, **2 OCPU, 12 GB RAM** (המכסה החינמית הנוכחית)
   - הורד את מפתח ה-SSH ששומרים בשלב הזה.
   - אם יש "Out of capacity" — לנסות שוב מאוחר יותר או Availability Domain אחר.
3. Networking → Virtual Cloud Networks → ה-VCN → Security Lists → Default → **Add Ingress Rules**:
   Source `0.0.0.0/0`, TCP, Destination port **80**, ושוב ל-**443**.

### 2. התקנה (SSH למכונה)
```bash
ssh -i key.pem ubuntu@<IP>
git clone https://github.com/<user>/polymarket-intel.git && cd polymarket-intel
bash scripts/oracle-setup.sh          # Docker, firewall, .env עם סיסמת DB אקראית וכתובת sslip.io
exit                                   # להתחבר מחדש כדי שקבוצת docker תיכנס לתוקף
ssh -i key.pem ubuntu@<IP> && cd polymarket-intel
bash scripts/set-password.sh          # סיסמה לדשבורד
docker compose up -d --build
```
הדשבורד: `https://<IP-עם-מקפים>.sslip.io` (למשל `https://140-238-1-2.sslip.io`). שם משתמש: `shlomo`.
באייפון: Safari → שיתוף → "הוסף למסך הבית" וזה נפתח כמו אפליקציה.

### 3. הורדת ההיסטוריה (פעם אחת, אפשר להריץ שוב — ממשיך מאיפה שעצר)
```bash
# ניסיון קטן קודם:
docker compose run --rm backfill --limit 200
# מלא (יכול לקחת שעות):
docker compose run --rm backfill
docker compose run --rm research       # יוצר דוח; מופיע בלשונית "מחקר"
```
`research` רץ גם אוטומטית כל לילה (03:00 UTC) יחד עם סנכרון שווקים שנסגרו.

### פעולות שוטפות
```bash
docker compose ps                      # מצב
docker compose logs -f collector       # לוגים
git pull && docker compose up -d --build   # עדכון גרסה
```
שחזור מגיבוי: `docker compose exec -T db pg_restore -U pmintel -d pmintel --clean < backups/pmintel-YYYY-MM-DD.dump`

> Oracle כבר קיצצו את המכסה החינמית פעם אחת בלי הודעה. הגיבויים ב-`./backups` הם מה שמאפשר לעבור לשרת אחר תוך שעה — כדאי מדי פעם להעתיק אותם החוצה (`scp`).

## פיתוח מקומי
```bash
cp .env.example .env    # מלא BASIC_AUTH_HASH (ראה הוראות בקובץ)
docker compose up -d --build     # http://localhost
cd app && pip install -r requirements.txt pytest && python -m pytest
# בדיקות אינטגרציה מול Postgres:  PMINTEL_TEST_DB=postgresql://... python -m pytest
```

## מפת דרכים
1. **מחקר היסטורי** ← אנחנו כאן. 🚦 אם אף אסטרטגיה לא מראה יתרון אחרי עלויות — עוצרים או משנים כיוון.
2. תשתית רציפה על Oracle (קיים בקוד, מחכה להקמה).
3. דשבורד (קיים, יורחב לפי מה שהמחקר יגלה).
4. Paper trading לאסטרטגיות שעברו את שלב 1. 🚦 2–3 חודשי מדידה.
5. התראות (טלגרם) ושיפורים.

## מבנה
```
app/pmintel/   sources.py (Gamma/CLOB)  normalize.py  collector.py  backfill.py  research.py  api.py  schema.sql
app/tests/     unit + integration (Postgres)
web/           דשבורד סטטי (vanilla JS, ללא build)
caddy/         HTTPS + auth + reverse proxy
scripts/       oracle-setup.sh, set-password.sh
```

## מעקב טיפים מטלגרם (קריאה בלבד)

המערכת קוראת קבוצות טלגרם שבחרת, שולפת סימולי מניות, ומודדת מה קרה אחרי יום / 5 / 20 ימי מסחר.
נשמרים רק: סימול, מחיר שנאמר, שם הקבוצה, זמן. בלי תוכן הודעות ובלי פרטי חברי הקבוצה. המערכת אף פעם לא כותבת.

1. היכנס ל-https://my.telegram.org ← API development tools ← צור אפליקציה. תקבל `api_id` ו-`api_hash`.
2. בשרת (השרת לא כולל nano; הפקודה שואלת ושומרת ב-.env):
   ```
   read -p "api_id: " A; read -p "api_hash: " B; printf 'TG_API_ID=%s\nTG_API_HASH=%s\n' "$A" "$B" >> ~/polymarket-intel/.env
   ```
3. התחברות חד-פעמית (טלגרם ישלח לך קוד):
   ```
   cd ~/polymarket-intel && sudo docker compose build tips && sudo docker compose run --rm tips python -m pmintel.tips_telegram --login
   ```
4. רשימת הקבוצות שלך: `sudo docker compose run --rm tips python -m pmintel.tips_telegram --list-chats`
5. הוסף ל-`.env` את הקבוצות (שמות או מספרים, מופרדים בפסיק): `TG_CHATS=-1001234567890,שם קבוצה`
6. ייבוא 30 הימים האחרונים והפעלה:
   ```
   sudo docker compose run --rm tips python -m pmintel.tips_telegram --history 30
   sudo docker compose up -d tips
   ```
ניתוק בכל רגע: טלגרם ← הגדרות ← מכשירים ← סיים את ה-session.
