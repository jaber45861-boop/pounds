# نقل وتشغيل البوت والـ API على Bela

هذا المشروع لا يحتاج إلى Replit لتشغيله. شغّل `python main.py` على Bela؛
سيبدأ البوت وخادم Flask/Waitress في نفس العملية وعلى نفس المنفذ.

## 1. ملفات البوت المطلوبة

انقل الملفات التالية مع قاعدة البيانات الأصلية:

```text
main.py
attached_assets/bot_(3)_1785867111284.py
attached_assets/reward_api.py
attached_assets/users_(3)_1785867154340.db
requirements-bela.txt
Procfile
```

يمكن نقل `pyproject.toml` و`uv.lock` بدلاً من `requirements-bela.txt` إذا كان Bela
يستخدم `uv`. لا تنقل `.replit` أو ملفات `artifacts` الخاصة بـ Replit لتشغيل البوت.

ثبّت الحزم وشغّل البوت:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-bela.txt
python main.py
```

استخدم مدير العمليات في Bela (systemd أو supervisor أو الخدمة المدمجة) لتشغيل
الأمر نفسه وإعادة تشغيله تلقائياً. لا تشغّل نسخة polling ثانية من البوت بنفس Token.

## 2. متغيرات البيئة

انسخ `.env.bela.example` إلى `.env` أو أضف المتغيرات إلى إعدادات الخدمة. ضع
الأسرار في مدير أسرار Bela، وليس في Git:

- `TELEGRAM_BOT_TOKEN`
- `SMM_API_KEY`
- `SESSION_SECRET`
- `API_SECRET`

القيم المهمة للرابط:

- `PORT`: المنفذ الداخلي الذي توفره Bela.
- `API_HOST`: عنوان الاستماع، والافتراضي `0.0.0.0`.
- `TELEGRAM_MINI_APP_URL`: رابط HTTPS العام للـ Mini App.
- `REWARD_API_ORIGINS`: أصل Mini App فقط، مثل `https://mini-app.example.com`.
- `BOT_DB_PATH`: مسار قاعدة SQLite الأصلية.

بعد التشغيل يجب أن يعيد:

```bash
curl https://api.example.com/healthz
```

```json
{"ok":true,"service":"telegram-bot-reward-api"}
```

## 3. بناء Mini App خارج Replit

انقل مجلد `artifacts/telegram-rewarded-ads` إلى مشروع الواجهة أو ابنِه
مستقلاً. استخدم الملفات التالية من هذا المجلد:

```text
package.json
vite.config.ts
tsconfig.json
index.html
public/
src/App.tsx
src/main.tsx
src/index.css
```

الحزمة لا تستورد `@replit/*` أو `@workspace/*` في الكود أو في قائمة
التبعيات. اسم الحزمة الحالي مجرد اسم محلي ولا يتطلب pnpm workspace.
لا تنقل `.replit-artifact/artifact.toml` إلى Bela.

```bash
cd artifacts/telegram-rewarded-ads
npm install
VITE_REWARD_API_URL=https://api.example.com \
VITE_REWARD_TASK_ID=monetag_rewarded_ad \
VITE_MONETAG_ZONE_ID=YOUR_ZONE_ID \
VITE_MONETAG_SDK_URL=https://libtl.com/sdk.js \
VITE_MONETAG_SDK_NAME=show_YOUR_ZONE_ID \
npm run build
```

انشر محتويات `dist/public` عبر Bela أو أي استضافة HTTPS تختارها. لا تضع
`API_SECRET` أو `SESSION_SECRET` في متغيرات `VITE_*`؛ كل ما يبدأ بـ `VITE_`
يصبح مرئياً داخل JavaScript المتصفح.

لا تحتاج نسخة الواجهة إلى `pnpm-workspace.yaml` أو `package.json` الموجود في
جذر هذا المستودع؛ ثبّت dependencies من `artifacts/telegram-rewarded-ads` فقط.

## 4. Monetag Postback

بعد معرفة رابط API العام من Bela، استخدم:

```text
https://api.example.com/api/rewards/postback
```

أرسل Secret في `X-API-Secret` أو في متغير `token` الذي يدعمه الإعداد. مرّر
`ymid`, `zone_id`, `event_type`, `reward_event_type`, و`telegram_id` إن كان
متاحاً. يجب أن تكون `reward_event_type=valued` و`zone_id` مطابقاً لـ
`MONETAG_ZONE_ID`.

## 5. ربط البوت بالـ Mini App

بعد نشر الواجهة، ضع رابطها HTTPS في:

```text
TELEGRAM_MINI_APP_URL=https://mini-app.example.com
```

ثم أعد تشغيل خدمة البوت. لا تستخدم رابط Replit أو `localhost`.

## 6. فحص ما بعد النشر

1. افتح `/healthz` من الإنترنت.
2. افتح Mini App من زر البوت داخل Telegram، لا من متصفح عادي.
3. تأكد أن `/api/rewards/balance` يعيد الرصيد للمستخدم الموثق.
4. نفّذ مشاهدة حقيقية وتحقق من Postback.
5. تحقق من `users.balance_cents` و`reward_ledger`.
6. أعد إرسال نفس `ymid` وتأكد أنه لا يضيف مكافأة ثانية.