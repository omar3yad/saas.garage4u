# خطة العمل التنفيذية الذرية للتحول إلى منصة SaaS متعددة المستأجرين
# Phase 5: Multi-Owner, Multi-Garage SaaS — Atomic Task Checklist

> **الإصدار:** 2.3 (محدث ومعتمد هندسياً بالتحسينات الخمسة: القفل الاستشاري المزدوج، تمييز BILLING_PAST_DUE، استثناءات CSRF، توحيد VARCHAR(128)، والفهرس الجزئي للبوابات النشطة)  
> **المرجع الهندسي المعتمد:** `docs/specs/phase-5-saas/spec.md` (مسار نسبي للمستودع)  
> **قاعدة التنفيذ الصارمة:** الترتيب الهرمي للمجموعات إلزامي. لا يُبدأ في أي مجموعة قبل اكتمال واختبار كافة مهام المجموعة السابقة لها.  
> **طبيعة الهجرات:** كافة هجرات Alembic تُكتب **يدوياً** بصيغة DDL صريحة، ويُمنع منعاً باتاً الاعتماد على `autogenerate`.  
> **معيار الذرية:** كل مهمة تمثل وحدة برمجية أو DDL مستقلة وقابلة للتحقق الفوري مع ذكر مسار الملف ومعيار القبول الدقيق وحقل الاعتماديات الصريح (`يعتمد على:`).  
> **توزيع جداول قاعدة البيانات الـ 16 بالكامل (Database Tables Partitioning):**
> - **الجداول الـ 11 الخاضعة لـ RLS إجبارياً:** `parking_sessions`, `parking_cards`, `shifts`, `pricing_rules`, `subscribers`, `subscription_plans`, `subscriptions`, `garage_gates`, `edge_device_gates`, `garage_configs`, `audit_logs`.
> - **الجداول الـ 5 المستثناة من RLS والمعزولة على مستوى طبقة التطبيق:**
>   1. `owners`: كيان المنصة العام، يُدار بالسوبر أدمن ومقيد بالاستعلامات المباشرة بـ `owner_id`.
>   2. `garages`: بيانات وصفية للقراءة قبل إنشاء سياق الجراج وفحص `garage.status` وحل `garage_code`.
>   3. `users`: مصادقة الدخول والبحث بالاسم قبل معرفة الجراج؛ معزول برمجياً بـ `WHERE garage_id = :gid`.
>   4. `garage_counters`: تُدار بقفل ذري حصري داخل دالة الخدمة داخل المعاملة.
>   5. `edge_devices`: مصادقة الكاميرات في WebSocket والـ Upload بتجزئة التوكن الفريدة `token_hash` قبل معرفة الجراج؛ معزول برمجياً بـ `WHERE garage_id = :gid` في استعلامات الإدارة، وفور المصادقة يتم ضبط سياق الجراج على الفور لتخضع كافة العمليات التالية للـ RLS الكامل.

---

## فهرس المراحل التنفيذية (Execution Phases Overview)

1. **المجموعة 1:** أدوار قاعدة البيانات والامتيازات الافتراضية الصارمة (Database Roles & Default Privileges)
2. **المجموعة 2:** هجرات Alembic اليدوية الخمس وسكريبت تهيئة الحسابات (Expand / Seed / Backfill / Contract Migrations)
3. **المجموعة 3:** تحديث وتطوير نماذج SQLAlchemy (ORM Models Refactoring & Constraints Inspection)
4. **المجموعة 4:** مخططات التحقق ونقل البيانات (Pydantic Schemas & DTOs)
5. **المجموعة 5:** مستودعات الكيانات الجديدة وتدقيق المستودعات القائمة (Scoped Repositories Refactoring)
6. **المجموعة 6:** محرك سياق المستأجر وتوحيد التبعيات والمصادقة (Tenant Context Engine, RBAC & Auth)
7. **المجموعة 7:** خدمات الأعمال ومحرك رخص المنصة وعدادات الإيصالات (Core Business Services)
8. **المجموعة 8:** وكلاء الكاميرات وتخزين الصور المعزول (Multi-Tenant Edge & Secure Media)
9. **المجموعة 9:** مسارات الـ API الإدارية والميدانية وفرض الحصص (Scoped API Routes & Quota Enforcement)
10. **المجموعة 10:** واجهات المستخدم (SSR Jinja2) ومبدل الجراجات وحماية CSRF
11. **المجموعة 11:** حزمة اختبارات PostgreSQL الشاملة واختبارات العزل الصارمة (Test Suite & RLS Verification)
12. **المجموعة 12:** إجراءات النشر الميداني وقياس نافذة الصيانة (Production Rollout & Maintenance)

---

## المجموعة 1: أدوار قاعدة البيانات والامتيازات الافتراضية الصارمة (Database Roles & Default Privileges)

- [x] **المهمة 1.1:** إنشاء سكريبت تهيئة أدوار قاعدة البيانات والامتيازات الافتراضية للجداول المستقبلية  
  - **مسار الملف:** `scripts/setup_db_roles.sql`  
  - **يعتمد على:** لا يوجد (نقطة البداية الأساسية).  
  - **التفاصيل:** إنشاء دورين مخصصين مع ضبط الامتيازات الافتراضية لتفادي أخطاء الجداول المستقبلية (حل B1):
    1. دور `pgms_migrator`: دور إداري بدون صلاحية Superuser، يمتلك `BYPASSRLS` و `CREATEDB` لتنفيذ هجرات Alembic وسكريبتات الـ Seed وعمليات النسخ الاحتياطي `pg_dump`.
    2. دور `pgms_app`: دور التشغيل اليومي لتطبيق FastAPI، يمتلك `NOSUPERUSER` و `NOBYPASSRLS` وخاضع لسياسات الأمان RLS إجبارياً.
    3. ضبط الامتيازات الافتراضية للجداول والتسلسلات المستقبلية:
       ```sql
       GRANT USAGE ON SCHEMA public TO pgms_app;
       ALTER DEFAULT PRIVILEGES FOR ROLE pgms_migrator IN SCHEMA public 
           GRANT SELECT, INSERT, UPDATE ON TABLES TO pgms_app;
       ALTER DEFAULT PRIVILEGES FOR ROLE pgms_migrator IN SCHEMA public 
           GRANT USAGE, SELECT ON SEQUENCES TO pgms_app;
       ```
    4. **تنبيه حاسم:** هذا السكريبت **لا ينفذ أي أوامر DDL على جداول محددة** (مثل `owners` أو `garages`) لأنها غير موجودة بعد. بل يقتصر على إنشاء الأدوار وضبط `DEFAULT PRIVILEGES`.  
  - **معيار القبول والاختبار:** تنفيذ السكريبت عبر `psql -U postgres`، والتأكد من ظهور الدورين في `\du` بالخصائص المطلوبة (`pgms_app` بـ `No bypass RLS`)، والتأكد من ظهور الامتيازات الافتراضية عبر `\ddp`.

- [x] **المهمة 1.2:** توثيق حدود صلاحيات الدور `pgms_app` على الجداول المستثناة من RLS  
  - **مسار الملف:** `scripts/setup_db_roles.sql`  
  - **يعتمد على:** المهمة 1.1.  
  - **التفاصيل:** (حل B2): توثيق القرار المعماري في ترويسة ملف الأدوار بأن `pgms_app` يمتلك `SELECT, INSERT, UPDATE` على الجداول المستثناة من RLS (`owners`, `garages`, `users`, `garage_counters`, `edge_devices`) لأن السوبر أدمن والمالك يحتاجان إنشاء وتعديل هذه السجلات، وأن **العزل المنطقي ومنع العبث العابر للجراجات (Cross-Tenant Tampering) على هذه الجداول محكوم بصرامة على مستوى طبقة التطبيق** عبر دوال التحقق والمستودعات المعزولة (Scoped Repositories). وتوثيق أن أوامر `REVOKE DELETE, TRUNCATE` عليها ستُنفذ في سكريبت ما بعد الهجرة `scripts/grant_app_privileges.sql` بعد إنشاء الجداول فعلياً.  
  - **معيار القبول والاختبار:** فحص ملف الـ SQL والتأكد من وجود التوثيق المعماري الشامل ووضوح حدود الصلاحيات.

- [x] **المهمة 1.3:** تحديث إعدادات الاتصال في `config.py` لدعم دور المهاجر ودور التطبيق  
  - **مسار الملف:** `config.py`  
  - **يعتمد على:** المهمة 1.1.  
  - **التفاصيل:** إضافة متغير تكوين `MIGRATION_DATABASE_URL: str | None = None` في صنف `Settings` مع التحقق التلقائي في `validate_settings`: إذا لم يُعين في بيئة التطوير، يعتمد افتراضياً على `DATABASE_URL`، أما في بيئة الإنتاج فيلزم توفيره بحساب `pgms_migrator`.  
  - **معيار القبول والاختبار:** اختبار تحميل الإعدادات عبر `pytest tests/unit/test_config.py` وتأكيد قراءة المتغير بنجاح.

- [x] **المهمة 1.4:** تحديث تكوين Alembic لاستخدام دور المهاجر في `alembic/env.py`  
  - **مسار الملف:** `alembic/env.py`  
  - **يعتمد على:** المهمة 1.3.  
  - **التفاصيل:** (حل D1): تعديل دالة الحصول على الـ URL لتقرأ `settings.MIGRATION_DATABASE_URL or settings.DATABASE_URL` لضمان تشغيل هجرات قاعدة البيانات بصلاحية `BYPASSRLS` دائماً، والتأكيد على أن الهجرات تُكتب يدوياً عبر DDL صريح ولا تعتمد على مقارنة النماذج التلقائية (`compare_type=True`).  
  - **معيار القبول والاختبار:** تشغيل `alembic current` والتأكد من اتصاله بالرابط المخصص دون أخطاء صلاحيات.

---

## المجموعة 2: هجرات Alembic اليدوية الخمس وسكريبت تهيئة الحسابات (Expand / Seed / Backfill / Contract Migrations)

> **تنبيه إلزامي:** جميع هجرات هذه المجموعة تُكتب **يدوياً** بدون استخدام `--autogenerate`، وتُنفذ واحدة تلو الأخرى في معاملات مستقلة ومحكومة.

- [x] **المهمة 2.1:** المراجعة 1 — توسيع قيم `userrole` وإسقاط قيد الفحص القديم (Revision 1)  
  - **مسار الملف:** `alembic/versions/e01_phase5_rev1_enums_cleanup.py`  
  - **يعتمد على:** المهمة 1.4.  
  - **التفاصيل:** 
    1. (حل B4): تشغيل كتلة إضافة قيم Enum في معاملة مستقلة تماماً ذاتية التثبيت (`autocommit=True`):
       ```sql
       ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'SUPER_ADMIN';
       ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'OWNER';
       ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'MANAGER';
       ```
    2. توثيق أن القيمة القديمة `'ADMIN'` تظل كدين تقني معروف (Known Technical Debt) داخل نوع الـ Enum لأنه لا يمكن حذف قيم من الـ Enums في PostgreSQL دون إعادة بناء النوع.
    3. إسقاط القيد المقيد القديم فوراً في نفس الهجرة:
       ```sql
       ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_role_gate_consistency;
       ```
  - **معيار القبول والاختبار:** تشغيل `alembic upgrade head` بنجاح، والتأكد من ارتكاب المعاملة (Commit) بالكامل قبل الانتقال للخطوة التالية، والتحقق عبر `\dT+ userrole` من وجود القيم الجديدة وإسقاط القيد من جدول `users`.

- [x] **المهمة 2.2:** المراجعة 2 — إنشاء جداول SaaS وإضافة الأعمدة كـ Nullable (Revision 2 - Expand)  
  - **مسار الملف:** `alembic/versions/e02_phase5_rev2_expand_tables.py`  
  - **يعتمد على:** المهمة 2.1.  
  - **التفاصيل:** 
    1. إنشاء الجداول الجديدة السبعة: `owners`, `garages`, `garage_counters`, `garage_gates`, `edge_devices`, `edge_device_gates`, `garage_configs` بالحقول والأنواع المحددة في القسم 4.2 من `spec.md`.
    2. إضافة الأعمدة الجديدة كـ `NULLABLE` على الجداول القائمة:
       - `users`: `owner_id INTEGER REFERENCES owners(id) ON DELETE RESTRICT`, `garage_id INTEGER REFERENCES garages(id) ON DELETE RESTRICT`, `must_change_password BOOLEAN NOT NULL DEFAULT FALSE`.
       - `parking_cards`: `garage_id INTEGER REFERENCES garages(id) ON DELETE RESTRICT`.
       - `shifts`: `garage_id INTEGER REFERENCES garages(id) ON DELETE RESTRICT`.
       - `pricing_rules`: `garage_id INTEGER REFERENCES garages(id) ON DELETE RESTRICT`.
       - `parking_sessions`: `garage_id INTEGER REFERENCES garages(id) ON DELETE RESTRICT`, `receipt_no INTEGER`, `ticket_code VARCHAR(50)`. تحويل `card_id` و `card_code` إلى `DROP NOT NULL`.
       - `subscribers`: `garage_id INTEGER REFERENCES garages(id) ON DELETE RESTRICT`.
       - `subscription_plans`: `garage_id INTEGER REFERENCES garages(id) ON DELETE RESTRICT`.
       - `subscriptions`: `garage_id INTEGER REFERENCES garages(id) ON DELETE RESTRICT`.
       - `audit_logs`: `garage_id INTEGER REFERENCES garages(id) ON DELETE RESTRICT`, `owner_id INTEGER REFERENCES owners(id) ON DELETE RESTRICT`, `actor_type VARCHAR(20) NOT NULL DEFAULT 'user'`, تحويل `actor_id` إلى `DROP NOT NULL`.
  - **معيار القبول والاختبار:** تشغيل `alembic upgrade head` والتحقق من إنشاء الجداول السبعة وإضافة كافة الأعمدة بنجاح دون أي خطأ في الجداول التي تحوي بيانات قائمة.

- [x] **المهمة 2.3:** المراجعة 3 — إدراج الكيانات الافتراضية، ضبط Sequences، وإنشاء بوابات ديناميكية (Revision 3 - Seed & Backfill)  
  - **مسار الملف:** `alembic/versions/e03_phase5_rev3_seed_and_backfill.py`  
  - **يعتمد على:** المهمة 2.2.  
  - **التفاصيل:** (حل البلوكير 4 والنقطة 7):
    1. إدراج المالك الافتراضي: `INSERT INTO owners (id, name, company_name, contact_phone, contact_email) VALUES (1, 'شركة باب اللوق', 'Babelooq Parking Co', '01000000000', 'owner@babelooq.com');`.
    2. إدراج جراج باب اللوق: `INSERT INTO garages (id, owner_id, code, name, capacity, timezone, currency, status, can_use_anpr, can_use_subscriptions, max_gates) VALUES (1, 1, 'BABELOOQ', 'جراج باب اللوق', 500, 'Africa/Cairo', 'EGP', 'ACTIVE', FALSE, TRUE, 5);`.
    3. ضبط تسلسل العدادات فوراً بعد الإدراج اليدوي للمعرفات لتجنب خطأ Duplicate Key لاحقاً:
       ```sql
       SELECT setval('owners_id_seq', (SELECT COALESCE(MAX(id), 1) FROM owners));
       SELECT setval('garages_id_seq', (SELECT COALESCE(MAX(id), 1) FROM garages));
       ```
    4. **إنشاء بوابات باب اللوق ديناميكياً:** إدراج البوابات في `garage_gates` استناداً إلى كافة أرقام البوابات الفعلية الموجودة في الجلسات والورديات القديمة (مع ضمان وجود البوابتين 1 و 2 كحد أدنى) لمنع فشل المفاتيح الأجنبية المركبة:
       ```sql
       INSERT INTO garage_gates (garage_id, gate_number, name, gate_type, default_terminal_profile, paper_width_mm)
       SELECT 
           1 AS garage_id,
           g.gate_number,
           'بوابة ' || g.gate_number AS name,
           'MANUAL_BIDIRECTIONAL' AS gate_type,
           'DESKTOP_GATE_KIOSK' AS default_terminal_profile,
           80 AS paper_width_mm
       FROM (
           SELECT 1 AS gate_number UNION SELECT 2
           UNION
           SELECT DISTINCT gate_number FROM parking_sessions WHERE gate_number IS NOT NULL
           UNION
           SELECT DISTINCT gate_number FROM shifts WHERE gate_number IS NOT NULL
       ) g
       WHERE g.gate_number BETWEEN 1 AND 10
       ON CONFLICT (garage_id, gate_number) DO NOTHING;
       ```
    5. ترحيل توكن الكاميرا الحالي: قراءة `settings.EDGE_API_TOKEN` (إن وُجد) وحساب تجزئة SHA-256 وإدراج الجهاز باسم `'Gate 1-2 Default Workstation'` في `edge_devices` وإسناده للبوابتين 1 و 2 في `edge_device_gates` باتجاه `BIDIRECTIONAL`.
    6. إدراج التكوين الافتراضي لجراج باب اللوق في `garage_configs`.
    7. تحديث الجداول التشغيلية السابقة لربطها بـ `garage_id = 1`:
       ```sql
       UPDATE parking_cards SET garage_id = 1 WHERE garage_id IS NULL;
       UPDATE shifts SET garage_id = 1 WHERE garage_id IS NULL;
       UPDATE pricing_rules SET garage_id = 1 WHERE garage_id IS NULL;
       UPDATE parking_sessions SET garage_id = 1 WHERE garage_id IS NULL;
       UPDATE subscribers SET garage_id = 1 WHERE garage_id IS NULL;
       UPDATE subscription_plans SET garage_id = 1 WHERE garage_id IS NULL;
       UPDATE subscriptions SET garage_id = 1 WHERE garage_id IS NULL;
       UPDATE audit_logs SET garage_id = 1 WHERE garage_id IS NULL;
       ```
    8. ملء أرقام الإيصالات التسلسلية للجلسات القديمة وتعيين العداد الذري مع دعم وجود فجوات في المعرفات:
       ```sql
       UPDATE parking_sessions SET receipt_no = id WHERE receipt_no IS NULL;
       INSERT INTO garage_counters (garage_id, last_receipt_no) 
       VALUES (1, (SELECT COALESCE(MAX(id), 0) FROM parking_sessions))
       ON CONFLICT (garage_id) DO UPDATE SET last_receipt_no = EXCLUDED.last_receipt_no;
       ```
  - **معيار القبول والاختبار:** الاستعلام والتأكد من أن `SELECT count(*) FROM parking_sessions WHERE garage_id IS NULL;` تعيد 0، وأن `garage_counters.last_receipt_no` يطابق أعلى `id` في جلسات باب اللوق، وأن بوابات `garage_gates` تغطي كافة البوابات المستخدمة تاريخياً في الورديات والجلسات.

- [x] **المهمة 2.4:** المراجعة 4 — تطهير بيانات المستخدمين، التحذير من البوابات غير المسندة، وفرض القيد الهرمي (Revision 4 - Role Mapping)  
  - **مسار الملف:** `alembic/versions/e04_phase5_rev4_role_mapping.py`  
  - **يعتمد على:** المهمة 2.3.  
  - **التفاصيل:** 
    1. (حل B3 و B4 والنقطة 11): تطهير شامل لكافة صفوف المستخدمين القائمة مع تسجيل التحذيرات:
       ```sql
       -- تحويل حسابات الإدارة القائمة إلى MANAGER
       UPDATE users SET role = 'MANAGER', owner_id = 1, garage_id = 1, gate_number = NULL 
       WHERE role = 'ADMIN' OR username = 'admin';
       ```
    2. فحص المشغلين بدون بوابة صالحة وتسجيل تحذير صريح بعددهم لمنع التعيين التلقائي الصامت:
       - تنفيذ استعلام للتحقق من عدد المشغلين بدون بوابة:
         `SELECT count(*) FROM users WHERE role = 'OPERATOR' AND (gate_number IS NULL OR gate_number NOT BETWEEN 1 AND 10);`
       - طباعة تحذير صريح في السجلات `logger.warning("Found unassigned operators...")`.
       - إذا كانت بيئة التشغيل إنتاجية (`ENVIRONMENT == 'production'`) والعدد > 0: رمي استثناء تنبيهي لمنع تحويل مشغل لبوابة خاطئة دون تدقيق إداري؛ وفي بيئات التطوير/الاختبار يتم إسنادهم إلى البوابة 1:
         ```sql
         UPDATE users SET owner_id = 1, garage_id = 1, gate_number = 1 
         WHERE role = 'OPERATOR' AND (gate_number IS NULL OR gate_number NOT BETWEEN 1 AND 10);

         UPDATE users SET owner_id = 1, garage_id = 1 
         WHERE role = 'OPERATOR' AND gate_number BETWEEN 1 AND 10;
         ```
    3. التحقق البرمجي قبل إضافة القيد عبر استعلام تحقق:
       `SELECT count(*) FROM users WHERE NOT (...);` ويجب أن تعيد 0.
    4. تطبيق القيد الهرمي بنمط Two-Phase Safe Constraint لضمان عدم توقف الهجرة:
       ```sql
       ALTER TABLE users ADD CONSTRAINT ck_users_saas_hierarchy CHECK (
           (role = 'SUPER_ADMIN' AND owner_id IS NULL AND garage_id IS NULL AND gate_number IS NULL) OR
           (role = 'OWNER' AND owner_id IS NOT NULL AND garage_id IS NULL AND gate_number IS NULL) OR
           (role = 'MANAGER' AND owner_id IS NOT NULL AND garage_id IS NOT NULL AND gate_number IS NULL) OR
           (role = 'OPERATOR' AND owner_id IS NOT NULL AND garage_id IS NOT NULL AND gate_number IS NOT NULL AND gate_number BETWEEN 1 AND 10)
       ) NOT VALID;
       ALTER TABLE users VALIDATE CONSTRAINT ck_users_saas_hierarchy;
       ```
  - **معيار القبول والاختبار:** التحقق من نجاح الهجرة دون خطأ فحص قيد، والتأكد من أن حساب `admin` أصبح دوره `MANAGER` ومربوط بـ `owner_id=1, garage_id=1`.

- [x] **المهمة 2.5:** المراجعة 5 — تحويل الأعمدة لـ NOT NULL، والمفاتيح المركبة، وقيود audit_logs، وتفعيل RLS على الجداول الـ 11 (Revision 5 - Contract & RLS)  
  - **مسار الملف:** `alembic/versions/e05_phase5_rev5_contract_rls.py`  
  - **يعتمد على:** المهمة 2.4.  
  - **التفاصيل:** (حل البلوكير 3 و 5 و 6):
    1. تحويل الأعمدة إلى `NOT NULL`:
       `garage_id` في `parking_cards`, `shifts`, `pricing_rules`, `parking_sessions`, `subscribers`, `subscription_plans`, `subscriptions`.
       `receipt_no` في `parking_sessions`.
    2. إسقاط القيود الفريدة القديمة وتطبيق القيود الفريدة المركبة:
       - `uq_users_garage_username (garage_id, username)`
       - `uq_garage_card_code (garage_id, card_code)`
       - `uq_garage_subscriber_plate (garage_id, plate_number)`
       - `uq_garage_subscriber_card ON subscribers(garage_id, card_code) WHERE card_code IS NOT NULL`
       - `uq_garage_plan_label (garage_id, label)`
       - `uq_garage_pricing_label (garage_id, label)`
       - `uq_garage_receipt_no (garage_id, receipt_no)`
       - `uq_garage_ticket_code (garage_id, ticket_code)`
    3. تطبيق القيود الفريدة `UNIQUE (id, garage_id)` والمفاتيح الخارجية المركبة مع `ON DELETE RESTRICT`.
    4. تطبيق الفهارس الجزئية الذرية:
       - `idx_garage_active_card_id ON parking_sessions(garage_id, card_id) WHERE status = 'ACTIVE' AND card_id IS NOT NULL`.
       - `idx_subscribers_active_card ON subscribers(garage_id, card_code) WHERE card_code IS NOT NULL`.
    5. إنشاء قيود وفهارس سجل التدقيق صراحةً:
       ```sql
       ALTER TABLE audit_logs ADD CONSTRAINT fk_audit_garage FOREIGN KEY (garage_id) REFERENCES garages(id) ON DELETE RESTRICT;
       ALTER TABLE audit_logs ADD CONSTRAINT fk_audit_owner FOREIGN KEY (owner_id) REFERENCES owners(id) ON DELETE RESTRICT;
       CREATE INDEX idx_audit_logs_garage_id ON audit_logs(garage_id);
       CREATE INDEX idx_audit_logs_owner_id ON audit_logs(owner_id);
       ALTER TABLE audit_logs ADD CONSTRAINT ck_audit_garage_context CHECK (
           (garage_id IS NOT NULL) OR
           (garage_id IS NULL AND (
               entity_type IN ('platform', 'owner_account', 'system') OR
               actor_type = 'system' OR
               action IN ('USER_LOGIN', 'USER_LOGOUT', 'LOGIN_FAILED', 'SUPER_ADMIN_GARAGE_ACCESS')
           ))
       );
       ```
    6. **تفعيل وفرض سياسات الـ RLS على الجداول الـ 11 التشغيلية المحددة بالاسم صراحة:**
       `parking_sessions`, `parking_cards`, `shifts`, `pricing_rules`, `subscribers`, `subscription_plans`, `subscriptions`, `garage_gates`, `edge_device_gates`, `garage_configs`, `audit_logs`.
       عبر تنفيذ `ENABLE ROW LEVEL SECURITY` و `FORCE ROW LEVEL SECURITY` لكل جدول، وإنشاء السياسات المسماة صراحة:
       `rls_policy_sessions`, `rls_policy_cards`, `rls_policy_shifts`, `rls_policy_rates`, `rls_policy_subscribers`, `rls_policy_plans`, `rls_policy_subs`, `rls_policy_gates`, `rls_policy_edge_gates`, `rls_policy_configs`, `rls_policy_audit`.
       *(ملاحظة: استثناء `edge_devices` لتمكين مصافحة الكاميرات بالـ token_hash قبل معرفة الجراج)*.  
  - **معيار القبول والاختبار:** تشغيل `alembic upgrade head` بنجاح، والاستعلام عن `pg_tables` والتأكد من أن الجداول الـ 11 تحمل `rowsecurity = true` و `forcerowsecurity = true`.

- [x] **المهمة 2.6:** تنفيذ سكريبت الامتيازات النهائية المشددة بعد اكتمال الجداول (Post-Migration Permissions Grant)  
  - **مسار الملف:** `scripts/grant_app_privileges.sql`  
  - **يعتمد على:** المهمة 2.5.  
  - **التفاصيل:** (حل B1 و B2): سكريبت يُنفذ بصلاحية `postgres` أو `pgms_migrator` فور اكتمال المراجعة 5 لتأكيد الصلاحيات المشددة على الجداول الفعلية بعد وجودها في قاعدة البيانات:
    ```sql
    GRANT USAGE ON SCHEMA public TO pgms_app;
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO pgms_app;
    GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO pgms_app;
    REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM pgms_app;
    REVOKE DELETE, TRUNCATE ON owners, garages, users FROM pgms_app;
    REVOKE CREATE, ALTER, DROP ON SCHEMA public FROM pgms_app;
    ```
  - **معيار القبول والاختبار:** تنفيذ السكريبت، ثم الاتصال بحساب `pgms_app` والتحقق من قدرته على القراءة والكتابة في الجداول التشغيلية وفشله في `DELETE FROM audit_logs` أو `DELETE FROM owners` أو `DROP TABLE`.

- [x] **المهمة 2.7:** إنشاء سكريبت تهيئة حسابات المنصة الأولية السوبر أدمن والمالك  
  - **مسار الملف:** `scripts/seed_platform_accounts.py`  
  - **يعتمد على:** المهمة 2.6.  
  - **التفاصيل:** (حل النقطة 12): سكريبت CLI يُنفذ لمرة واحدة بعد اكتمال الميجريشن:
    1. توليد حساب Super Admin مركزي للمنصة: اسم مستخدم `superadmin`، كلمة مرور عشوائية آمنة مطبوعة لمرة واحدة في الطرفية، `role = 'SUPER_ADMIN'`, `owner_id = NULL, garage_id = NULL, gate_number = NULL`، مع تعيين `must_change_password = TRUE`.
    2. توليد حساب المالك لجراج باب اللوق: اسم مستخدم `owner_babelooq`، كلمة مرور عشوائية مطبوعة في الطرفية، `role = 'OWNER'`, `owner_id = 1, garage_id = NULL, gate_number = NULL`، مع تعيين `must_change_password = TRUE`.
    3. تسجيل عملية التهيئة في `audit_logs` بسياق المنصة العام (`entity_type='platform'`, `actor_type='system'`, `action='PLATFORM_INIT'`).  
  - **معيار القبول والاختبار:** تشغيل `python scripts/seed_platform_accounts.py` والتأكد من إدراج الحسابين بنجاح وظهور كلمات المرور المؤقتة وتسجيل الحدث في `audit_logs`.

---

## المجموعة 3: تحديث وتطوير نماذج SQLAlchemy (ORM Models Refactoring & Constraints Inspection)

- [x] **المهمة 3.1:** تحديث نموذج المستخدم في `models/user.py`  
  - **مسار الملف:** `models/user.py`  
  - **يعتمد على:** المهمة 2.5.  
  - **التفاصيل:** 
    - تحديث `UserRole` ليشمل: `SUPER_ADMIN = "SUPER_ADMIN"`, `OWNER = "OWNER"`, `MANAGER = "MANAGER"`, `OPERATOR = "OPERATOR"`.
    - إضافة الحقول: `owner_id: Mapped[int | None]`, `garage_id: Mapped[int | None]`, `must_change_password: Mapped[bool] = mapped_column(default=False)`.
    - استبدال القيد القديم بالقيد الجديد `ck_users_saas_hierarchy`.
    - إضافة علاقة `owner = relationship("Owner", back_populates="users")` وعلاقة `garage = relationship("Garage", back_populates="users")`.
  - **معيار القبول والاختبار:** فحص قيود النموذج برمجياً عبر `inspect(engine).get_check_constraints("users")` والتأكد من وجود `ck_users_saas_hierarchy` و `uq_users_id_garage`.

- [x] **المهمة 3.2:** إنشاء نموذج المالك في `models/owner.py`  
  - **مسار الملف:** `models/owner.py`  
  - **يعتمد على:** المهمة 2.5.  
  - **التفاصيل:** تعريف صنف `Owner(Base, TimestampMixin)` بجدول `owners`.
    الحقول: `id`, `name`, `company_name`, `contact_phone`, `contact_email`, `subscription_plan`, `billing_status`, `max_garages_allowed`. إضافة علاقات: `garages`, `users`.
  - **معيار القبول والاختبار:** فحص أعمدة وقيد المفتاح الأساسي عبر `inspect(engine).get_pk_constraint("owners")`.

- [x] **المهمة 3.3:** إنشاء نموذج الجراج وحالة الجراج الموحدة في `models/garage.py`  
  - **مسار الملف:** `models/garage.py`  
  - **يعتمد على:** المهمة 3.2.  
  - **التفاصيل:** (حل النقطة 13): تعريف صنف `Garage(Base, TimestampMixin)` بجدول `garages`.
    الحقول: `id`, `owner_id`, `code`, `name`, `address`, `city`, `capacity`, `timezone`, `currency`, `status` خاضعة لقيد الفحص `status IN ('ACTIVE', 'SUSPENDED', 'TERMINATED')`، `can_use_anpr`, `can_use_subscriptions`, `max_gates`.
    العلاقات: `owner`, `gates`, `config`, `users`, `counter`.
  - **معيار القبول والاختبار:** فحص القيد الفريد المركب `inspect(engine).get_unique_constraints("garages")` وتأكيد وجود `uq_garages_id_owner (id, owner_id)`.

- [x] **المهمة 3.4:** إنشاء نموذج عداد الإيصالات في `models/garage_counter.py`  
  - **مسار الملف:** `models/garage_counter.py`  
  - **يعتمد على:** المهمة 3.3.  
  - **التفاصيل:** تعريف صنف `GarageCounter(Base)` بجدول `garage_counters`.
    الحقول: `garage_id: Mapped[int] = mapped_column(ForeignKey("garages.id"), primary_key=True)`, `last_receipt_no: Mapped[int] = mapped_column(default=0)`.
  - **معيار القبول والاختبار:** التحقق من أن المفتاح الأساسي للعداد هو `garage_id` بدون autoincrement عبر فحص تعريف الأعمدة.

- [x] **المهمة 3.5:** إنشاء نموذج بوابات الجراج في `models/garage_gate.py`  
  - **مسار الملف:** `models/garage_gate.py`  
  - **يعتمد على:** المهمة 3.3.  
  - **التفاصيل:** تعريف صنف `GarageGate(Base, TimestampMixin)` بجدول `garage_gates`.
    الحقول: `id`, `garage_id`, `gate_number`, `name`, `gate_type`, `default_terminal_profile`, `paper_width_mm`.
    إضافة القيود: `UniqueConstraint("garage_id", "gate_number", name="uq_garage_gate_number")`, `UniqueConstraint("id", "garage_id", name="uq_garage_gates_id_garage")`.
  - **معيار القبول والاختبار:** فحص القيود المركبة عبر `inspect(engine).get_unique_constraints("garage_gates")`.

- [x] **المهمة 3.6:** إنشاء نماذج أجهزة الكاميرات والبوابات في `models/edge_device.py`  
  - **مسار الملف:** `models/edge_device.py`  
  - **يعتمد على:** المهمة 3.5.  
  - **التفاصيل:** 
    - تعريف `EdgeDevice(Base, TimestampMixin)`: الحقول `id`, `garage_id`, `device_name`, `token_hash`, `revoked_at`, `last_seen_at`.
    - تعريف `EdgeDeviceGate(Base)`: الحقول `id`, `device_id`, `garage_id`, `gate_number`, `direction`, `is_active`, `created_at`.
    - إضافة القيود الفريدة المركبة والمفاتيح الخارجية مع `ON DELETE RESTRICT`.
  - **معيار القبول والاختبار:** فحص المفاتيح الخارجية المركبة عبر `inspect(engine).get_foreign_keys("edge_device_gates")`.

- [x] **المهمة 3.7:** إنشاء نموذج تكوين الجراج في `models/garage_config.py`  
  - **مسار الملف:** `models/garage_config.py`  
  - **يعتمد على:** المهمة 3.3.  
  - **التفاصيل:** تعريف صنف `GarageConfig(Base, TimestampMixin)` بجدول `garage_configs`.
    الحقول: `id`, `garage_id`, `ticket_system`, `anpr_enabled`, `exit_photo_enabled`, `subscriptions_enabled`, `require_shift_cash_reconciliation`, `min_session_duration_seconds`, `receipt_settings` (JSONB / JSON), `version`.
  - **معيار القبول والاختبار:** التحقق من وجود القيد الفريد `UniqueConstraint("id", "garage_id", name="uq_garage_configs_id_garage")`.

- [x] **المهمة 3.8:** تحديث النماذج التشغيلية القائمة لإضافة الحقول والمفاتيح المركبة  
  - **مسار الملفات:** 
    - `models/parking_card.py`: إضافة `garage_id`.
    - `models/shift.py`: إضافة `garage_id`.
    - `models/pricing_rule.py`: إضافة `garage_id`. (ملاحظة: `lost_card_penalty` موجود بالفعل في السطر 17 كحقل أصيل).
    - `models/parking_session.py`: إضافة `garage_id`, `receipt_no`, `ticket_code`, وتعديل `card_id` و `card_code` ليقبلا `None`.
    - `models/subscriber.py`: إضافة `garage_id` (ملاحظة: `card_code` موجود بالفعل في السطر 15).
    - `models/subscription_plan.py`: إضافة `garage_id`.
    - `models/subscription.py`: إضافة `garage_id`.
    - `models/audit_log.py`: إضافة `garage_id`, `owner_id`, `actor_type`, وجعل `actor_id` يقبل `None`.
  - **يعتمد على:** المهام 3.1 إلى 3.7.  
  - **معيار القبول والاختبار:** تشغيل اختبار فحص النماذج عبر `pytest tests/unit/test_models.py` والتأكد من مطابقة كافة القيود والمفاتيح المركبة بنسبة 100%.

- [x] **المهمة 3.9:** تحديث مصفوفة الصادرات في `models/__init__.py`  
  - **مسار الملف:** `models/__init__.py`  
  - **يعتمد على:** المهمة 3.8.  
  - **التفاصيل:** تصدير كافة النماذج الجديدة (`Owner`, `Garage`, `GarageCounter`, `GarageGate`, `EdgeDevice`, `EdgeDeviceGate`, `GarageConfig`) وتحديث قائمة `__all__`.  
  - **معيار القبول والاختبار:** استيراد الحزمة الكاملة `from models import Owner, Garage, GarageGate, GarageConfig, EdgeDevice`.

---

## المجموعة 4: مخططات التحقق ونقل البيانات (Pydantic Schemas & DTOs)

- [x] **المهمة 4.1:** تحديث مخططات المستخدم في `schemas/user.py`  
  - **مسار الملف:** `schemas/user.py`  
  - **يعتمد على:** المهمة 3.1.  
  - **التفاصيل:** 
    - تعديل قيد `gate_number` من `le=5` إلى `le=10`.
    - إضافة حقول `owner_id: int | None`, `garage_id: int | None`, `must_change_password: bool` في مخططات `UserCreate`, `UserUpdate`, `UserResponse`.
  - **معيار القبول والاختبار:** اختبار وحدة Pydantic يؤكد قبول `gate_number=8` ورفض `gate_number=11`.

- [x] **المهمة 4.2:** إنشاء مخططات المالك في `schemas/owner.py`  
  - **مسار الملف:** `schemas/owner.py`  
  - **يعتمد على:** المهمة 3.2.  
  - **التفاصيل:** تعريف المخططات: `OwnerCreate`, `OwnerUpdate`, `OwnerResponse` مع التحقق من البريد والهاتف والباقة وحد الجراجات `max_garages_allowed >= 1`.  
  - **معيار القبول والاختبار:** اختبار التحقق ببيانات صالحة وبيانات غير صالحة.

- [x] **المهمة 4.3:** إنشاء مخططات الجراج والرخص في `schemas/garage.py`  
  - **مسار الملف:** `schemas/garage.py`  
  - **يعتمد على:** المهمة 3.3.  
  - **التفاصيل:** 
    - تعريف `GarageCreate`, `GarageUpdate`, `GarageResponse`.
    - تعريف `GarageEntitlementsUpdate` (خاص بالسوبر أدمن حصراً لتعديل `can_use_anpr`, `can_use_subscriptions`, `max_gates`).
  - **معيار القبول والاختبار:** اختبار التحقق من كود الجراج (Uppercase alphanumeric) وسعة الجراج > 0 و `max_gates` بين 1 و 10 وقيم الحالة المعتمدة (`ACTIVE`, `SUSPENDED`, `TERMINATED`).

- [x] **المهمة 4.4:** إنشاء مخططات تكوين الجراج ونصوص الإيصال في `schemas/garage_config.py`  
  - **مسار الملف:** `schemas/garage_config.py`  
  - **يعتمد على:** المهمة 3.7.  
  - **التفاصيل:** 
    - تعريف `ReceiptSettingsSchema` (نصوص الترويسة والتذييل وخيار الـ QR).
    - تعريف `GarageConfigUpdate` مع فحص اتساق: `exit_photo_enabled` لا يمكن أن تكون True إذا كانت `anpr_enabled` تساوي False، و `min_session_duration_seconds` بين 30 و 600.
    - تعريف `GarageConfigResponse`.
  - **معيار القبول والاختبار:** اختبار رمي خطأ التحقق عند محاولة تفعيل `exit_photo_enabled` مع تعطيل ANPR.

- [x] **المهمة 4.5:** إنشاء مخططات بوابات الجراج في `schemas/garage_gate.py`  
  - **مسار الملف:** `schemas/garage_gate.py`  
  - **يعتمد على:** المهمة 3.5.  
  - **التفاصيل:** تعريف `GarageGateCreate`, `GarageGateUpdate`, `GarageGateResponse` مع فحص `gate_number` بين 1 و 10، و `paper_width_mm` في (58, 80).  
  - **معيار القبول والاختبار:** اختبار رفض مقاس ورق غير مدعوم مثل 70mm.

- [x] **المهمة 4.6:** إنشاء مخططات أجهزة الكاميرات في `schemas/edge_device.py`  
  - **مسار الملف:** `schemas/edge_device.py`  
  - **يعتمد على:** المهمة 3.6.  
  - **التفاصيل:** تعريف `EdgeDeviceCreate`, `EdgeDeviceResponse`, `EdgeDeviceTokenResponse`, `EdgeDeviceGateAssign` مع دعم حقل الاتجاه `direction` في (`ENTRY`, `EXIT`, `BIDIRECTIONAL`).  
  - **معيار القبول والاختبار:** اختبار إسناد جهاز لبوابة بالاتجاهات الصحيحة.

- [x] **المهمة 4.7:** تحديث مخططات التقارير الإدارية `schemas/admin_reports.py`  
  - **مسار الملف:** `schemas/admin_reports.py`  
  - **يعتمد على:** لا يوجد اعتماد مباشر.  
  - **التفاصيل:** تحديث السطرين 78 و 97 لتغيير قيد `gate_number` من `le=5` إلى `le=10`.  
  - **معيار القبول والاختبار:** اختبار تمرير `gate_number=7` في تقارير الإدارة دون خطأ تحقق.

- [x] **المهمة 4.8:** تحديث مخططات الجلسات والورديات  
  - **مسار الملفات:** `schemas/parking_session.py` و `schemas/shift.py`  
  - **يعتمد على:** المهمة 3.8.  
  - **التفاصيل:** إضافة `receipt_no`, `ticket_code`, `garage_id` لمخططات الاستجابة `SessionResponse` و `ShiftResponse`.  
  - **معيار القبول والاختبار:** التأكد من ظهور `receipt_no` كـ integer متسلسل في الـ Schema.

---

## المجموعة 5: مستودعات الكيانات الجديدة وتدقيق المستودعات القائمة (Scoped Repositories Refactoring)

> **ملاحظة معمارية (حل D2 وتحديد المسؤوليات):** تم تقديم هذه المجموعة قبل محرك سياق المستأجر (المجموعة 6). مستودع العداد `counter_repo` مسؤول حصراً عن تنفيذ استعلام الـ SQL الذري (`SELECT ... FOR UPDATE` و `UPDATE ... RETURNING`)، بينما خدمة العداد `counter_service` تنظم المعاملة وفحص رخصة الجراج وحالته.

- [x] **المهمة 5.1:** إنشاء المستودعات الجديدة للجداول السبعة وتأمين الجداول المستثناة  
  - **مسار الملفات:** 
    - `repositories/owner_repo.py`: استعلام الملاك، التحقق من البريد والحالة وعدد الجراجات المسموحة `max_garages_allowed`.
    - `repositories/garage_repo.py`: استعلام الجراجات بالكود والمعرف والمالك والرخص التعاقدية وحساب عدد الجراجات الحالية للمالك.
    - `repositories/garage_gate_repo.py`: استعلام بوابات الجراج الفعلية بالرقم والنوع وعدد البوابات المفتوحة والتحقق من عدم تجاوز `max_gates`.
    - `repositories/garage_config_repo.py`: قراءة وتحديث التكوين مع دعم التعديل المتزامن (Optimistic Locking عبر فحص `version`).
    - `repositories/edge_device_repo.py`: 
      1. دالة مصادقة سريعة عامة `get_by_token_hash(token_hash)` (تعمل بدون سياق جراج لمصافحة الـ WebSocket والـ Upload).
      2. دوال الإدارة المقيدة بسياق الجراج `get_by_garage(garage_id)` مع إلزام `WHERE garage_id = :gid` لحماية بيانات الأجهزة من الـ IDOR.
      3. تدوير التوكن وتحديث `last_seen_at`.
    - `repositories/counter_repo.py`: تنفيذ استعلام القفل الحصري الذري:
      `SELECT last_receipt_no FROM garage_counters WHERE garage_id = :gid FOR UPDATE;`
      `UPDATE garage_counters SET last_receipt_no = last_receipt_no + 1 WHERE garage_id = :gid RETURNING last_receipt_no;`
  - **يعتمد على:** المجموعة 3 (النماذج).  
  - **معيار القبول والاختبار:** اختبارات وحدة لكافة دوال الـ CRUD الأساسية في المستودعات الجديدة والتأكد من عملها بنجاح.

- [x] **المهمة 5.2:** تدقيق وتحديث مستودع التسعير `repositories/rate_repo.py`  
  - **مسار الملف:** `repositories/rate_repo.py`  
  - **يعتمد على:** المهمة 3.8.  
  - **التفاصيل:** 
    - تعديل `set_active(rule_id, garage_id)`: لتصبح `UPDATE pricing_rules SET is_active=False WHERE garage_id = :garage_id AND id != :rule_id`.
    - تعديل `get_active(garage_id)`: لتصبح `SELECT ... WHERE garage_id = :garage_id AND is_active = True LIMIT 1`.
  - **معيار القبول والاختبار:** كتابة اختبار وحدة ينشئ تسعيرتين لجراجين، وتفعيل تسعيرة الجراج الأول والتأكد من بقاء تسعيرة الجراج الثاني نشطة ومستقلة.

- [x] **المهمة 5.3:** تدقيق وتحديث مستودع التقارير `repositories/report_repo.py`  
  - **مسار الملف:** `repositories/report_repo.py`  
  - **يعتمد على:** المهمة 3.8.  
  - **التفاصيل:** 
    - إلزام تمرير `garage_id` في كافة دوال الإحصائيات (`count_active_sessions`, `get_daily_summary`, إلخ).
    - حقن فلترة `garage_id = :garage_id` في الاستعلامات النصية الخام `text(...)` داخل مصفوفات الشروط `ticket_clauses` و `sub_clauses`.
  - **معيار القبول والاختبار:** اختبار استخراج التقرير اليومي لجراج والتأكد من أن الأرقام والإيرادات تخص الجراج المحدد حصراً.

- [x] **المهمة 5.4:** تدقيق وتحديث مستودع البطاقات `repositories/card_repo.py`  
  - **مسار الملف:** `repositories/card_repo.py`  
  - **يعتمد على:** المهمة 3.8.  
  - **التفاصيل:** تعديل `get_by_code(card_code, garage_id)` لتلزم مطابقة `WHERE garage_id = :garage_id AND card_code = :card_code`.  
  - **معيار القبول والاختبار:** إنشاء كارت بنفس الكود في جراجين، والاستعلام بكود أحدهما والتأكد من عودة كارت الجراج المطلوب فقط.

- [x] **المهمة 5.5:** تدقيق وتحديث مستودعي الجلسات والورديات  
  - **مسار الملفات:** `repositories/session_repo.py` و `repositories/shift_repo.py`  
  - **يعتمد على:** المهمة 3.8.  
  - **التفاصيل:** حصر استعلامات الجلسات النشطة والبحث عن وردية المشغل المفتوحة باشتراط تطابق `garage_id`.  
  - **معيار القبول والاختبار:** التأكد من عدم عودة وردية مشغل في جراج آخر إذا تشابه رقم البوابة أو المشغل.

- [x] **المهمة 5.6:** حماية استعلامات المستخدمين والجراجات غير الخاضعة لـ RLS  
  - **مسار الملفات:** `repositories/user_repo.py` و `repositories/garage_repo.py`  
  - **يعتمد على:** المهمة 3.1 والمهمة 3.3.  
  - **التفاصيل:** (حل B2): بما أن جدولي `users` و `garages` مستثنيان من الـ RLS:
    - إلزام شرط `WHERE garage_id = :garage_id` في كافة دوال استرجاع موظفي الجراج في `user_repo.py` لمنع استعلام أو تعديل أي مستخدم بجراج آخر برمجياً.
    - إلزام شرط `WHERE owner_id = :owner_id` في استعلامات المالك للجراجات في `garage_repo.py`.
    - منع أي ثغرة IDOR عند استرجاع مستخدم أو جراج بالمعرف الرقمي عبر المطابقة الثنائية `(id, garage_id)`.  
  - **معيار القبول والاختبار:** محاولة استعلام مستخدم بجراج آخر بالـ ID والتأكد من عودة `None` ورمي 404 في طبقة التوجيه.

---

## المجموعة 6: محرك سياق المستأجر وتوحيد التبعيات والمصادقة (Tenant Context Engine, RBAC & Auth)

> **ملاحظة معمارية (حل D4):** تم دمج كافة أعمال ملف التبعيات `dependencies.py` في هذه المجموعة لتكون متجاورة ومنطقية وتمنع أي تعارضات دمج لاحقة.

- [x] **المهمة 6.1:** إنشاء هيكل بيانات سياق المستأجر في `utils/tenant_context.py`  
  - **مسار الملف:** `utils/tenant_context.py`  
  - **يعتمد على:** لا يوجد اعتماد خارجي.  
  - **التفاصيل:** تعريف صنف البيانات غير القابل للتعديل `TenantContext`:
    ```python
    @dataclass(frozen=True)
    class TenantContext:
        garage_id: int | None = None
        owner_id: int | None = None
        is_super_admin: bool = False
        is_system: bool = False
        user_id: int | None = None
        role: str | None = None
    ```
  - **معيار القبول والاختبار:** اختبار إنشاء كائنات سياق مختلفة للأنواع الأربعة من المستخدمين وسياق النظام والتأكد من أنها `frozen`.

- [x] **المهمة 6.2:** تسجيل مستمع أحداث الجلسة المتزامن `after_begin` في `database.py`  
  - **مسار الملف:** `database.py`  
  - **يعتمد على:** المهمة 6.1.  
  - **التفاصيل:** (حل البلوكير 6): تسجيل مستمع أحداث متزامن على `engine.sync_engine`:
    عند بدء أي معاملة، يقرأ بيانات السياق من `session.info` مع تحويل آمن للقيم الخالية إلى نصوص فارغة `''` صراحةً لتجنب أخطاء asyncpg عند تمرير NULL، وتوحيد التسمية البرمجية `is_system` مع المتغير البيئي `app.is_system_context`:
    ```python
    garage_id_str = str(info.get("garage_id")) if info.get("garage_id") is not None else ""
    owner_id_str = str(info.get("owner_id")) if info.get("owner_id") is not None else ""
    is_super_admin_str = "true" if info.get("is_super_admin") else "false"
    is_system_str = "true" if (info.get("is_system") or info.get("is_system_context")) else "false"

    connection.execute(
        text("""
            SELECT 
                set_config('app.current_garage_id', :gid, true),
                set_config('app.current_owner_id', :oid, true),
                set_config('app.is_super_admin', :isa, true),
                set_config('app.is_system_context', :sys, true);
        """),
        {"gid": garage_id_str, "oid": owner_id_str, "isa": is_super_admin_str, "sys": is_system_str}
    )
    ```
    والتأكد من أن المعامل الأخير هو `true` لتكون الإعدادات محلية على مستوى المعاملة الحالية فقط وتُصفَّر تلقائياً فور الـ Commit أو الـ Rollback.  
  - **معيار القبول والاختبار:** اختبار فتح جلسة تجريبية وتعيين `session.info["garage_id"] = 1` والتأكد من أن `SELECT current_setting('app.current_garage_id', true)` تعيد `'1'` داخل المعاملة، والتأكد من تصفير القيمة وعودة `NULL` فوراً بعد `commit`.

- [x] **المهمة 6.3:** إنشاء تبعية استخراج سياق المستأجر وحقنه في الجلسة في `dependencies.py`  
  - **مسار الملف:** `dependencies.py`  
  - **يعتمد على:** المهمة 6.2 والمهمة 5.1 (`garage_repo`).  
  - **التفاصيل:** (حل D2 و D5): دالة التبعية `get_tenant_context`:
    1. استخراج `current_user` الموثق.
    2. إذا كان الدور `SUPER_ADMIN`: ضبط `is_super_admin = True`.
    3. إذا كان الدور `OWNER`: ضبط `owner_id = user.owner_id`. واستخراج `garage_id` إما من مسار الـ API الصريح أو من كوكيز المتصفح `pgms_active_garage` بعد التحقق الإلزامي عبر `garage_repo` من أن الجراج مملوك له فعلياً.
    4. إذا كان الدور `MANAGER` أو `OPERATOR`: ضبط `garage_id = user.garage_id` و `owner_id = user.owner_id`.
    5. منع تمرير `is_super_admin` من أي مدخل مستخدم أو ترويسة خارجية.
    6. حقن القيم في `session.info`.  
  - **معيار القبول والاختبار:** اختبار طلب وهمي لمالك يمرر كوكيز `pgms_active_garage=1` والتأكد من التحقق من الملكية وحقن `garage_id` الصحيح في `session.info`.

- [x] **المهمة 6.4:** توحيد دوال الصلاحيات ومنع تصعيد الصلاحيات (RBAC & Escalation Guard)  
  - **مسار الملف:** `dependencies.py`  
  - **يعتمد على:** المهمة 6.3.  
  - **التفاصيل:** (حل D4 والنقطة 14): 
    1. دوال فحص الأدوار:
       - `require_super_admin`: قصر الوصول على `UserRole.SUPER_ADMIN`.
       - `require_owner_or_admin`: قصر الوصول على `SUPER_ADMIN` و `OWNER`.
       - `require_manager_or_above`: قصر الوصول على `SUPER_ADMIN`, `OWNER`, `MANAGER`.
       - `require_operator_or_above`: دعم كافة الأدوار التشغيلية.
    2. **دالة منع تصعيد الصلاحيات `validate_role_assignment`:**
       تمنع أي مستخدم من إنشاء أو ترقية مستخدم آخر لرتبة تعلوه أو تماثله، أو إسناده لجراج خارج اختصاصه:
       - إذا كان المنشئ `MANAGER` وحاول إنشاء/تعديل مستخدم بدور `SUPER_ADMIN`, `OWNER`, أو `MANAGER`: رمي خطأ `403 PRIVILEGE_ESCALATION_DENIED`.
       - إذا كان المنشئ `OWNER` وحاول إنشاء `SUPER_ADMIN`: رمي خطأ `403 PRIVILEGE_ESCALATION_DENIED`.
       - إذا كان المنشئ `MANAGER` وحاول إسناد `garage_id` مختلف عن جراجه: رمي خطأ `403 PRIVILEGE_ESCALATION_DENIED`.  
  - **معيار القبول والاختبار:** اختبار محاولة مدير جراج إنشاء مدير آخر والتأكد من رفض العملية فوراً بكود `403 PRIVILEGE_ESCALATION_DENIED`.

- [x] **المهمة 6.5:** حظر المستخدمين الملزمين بتغيير كلمة المرور `must_change_password` وتوفير مسارات التغيير  
  - **مسار الملفات:** `dependencies.py`, `routes/auth.py`, `templates/auth/change_password.html`  
  - **يعتمد على:** المهمة 6.4.  
  - **التفاصيل:** (حل النقص 3 والنقطة 8): 
    1. إنشاء تبعية `enforce_password_change` يتم استدعاؤها في `get_current_user`:
       إذا كان `user.must_change_password == True` والمسار المطلوب ليس مسار تغيير كلمة المرور، يتم:
       - في طلبات الـ API: رمي خطأ `403 Forbidden` بكود `PASSWORD_CHANGE_REQUIRED`.
       - في طلبات الـ UI عبر المتصفح: إعادة التوجيه الفوري إلى `/ui/auth/change-password`.
    2. إنشاء نقطة نهاية `POST /api/v1/auth/change-password`: تستقبل كلمة المرور القديمة والجديدة، تتحقق من الشروط، تُحدث `hashed_password`، وتضبط `must_change_password = False`.
    3. إنشاء مسار وقالب SSR `GET/POST /ui/auth/change-password` محمي بـ CSRF لتمكين المستخدمين من تغيير كلمة المرور عبر المتصفح.  
  - **معيار القبول والاختبار:** محاولة مستخدم بـ `must_change_password=True` طلب `/api/v1/sessions` والتأكد من حظره بـ 403، والسماح له بتغيير كلمة المرور وإلغاء الحظر بنجاح.

- [x] **المهمة 6.6:** تسجيل حدث وصول السوبر أدمن للجراجات تلقائياً في معاملة مستقلة  
  - **مسار الملف:** `dependencies.py`  
  - **يعتمد على:** المهمة 6.3 والمهمة 5.1.  
  - **التفاصيل:** (حل النقص 3 والنقطة 16): داخل تبعية `get_tenant_context`: عندما يقوم مستخدم بدور `SUPER_ADMIN` بطلب أي مسار يحتوي على `{garage_id}`، يتم تسجيل محاولة الوصول فوراً كحدث `SUPER_ADMIN_GARAGE_ACCESS` على مستوى المنصة (`entity_type='platform'`, `actor_id=user.id`, `garage_id=None`, `payload_after={"target_garage_id": garage_id, "endpoint": request.url.path, "method": request.method}`)، ويتم الحفظ عبر جلسة سريعة مستقلة لضمان تسجيل الحدث حتى لو فشل الطلب لاحقاً أو تم التراجع عنه (Rollback).  
  - **معيار القبول والاختبار:** إرسال طلب من سوبر أدمن إلى `/api/v1/garages/1/config` والتأكد من قيد حدث التدقيق تلقائياً في `audit_logs` دون استدعاء يدوي في الراوت.

- [x] **المهمة 6.7:** تحديث مسار تسجيل الدخول وحل الهوية وتأمين تسلسل RLS  
  - **مسار الملفات:** `routes/auth.py` و `services/auth_service.py`  
  - **يعتمد على:** المهمة 6.3 و 6.5.  
  - **التفاصيل:** 
    - دعم حقل `garage_code` اختياري للمشغلين والمديرين وإلزامي في حال تشابه أسماء المستخدمين عبر الجراجات.
    - تنفيذ المصادقة بسياق القراءة العام (`users` و `garages` مستثنيان من RLS).
    - **التسلسل الحرج قبل الـ Audit:** فور التحقق من صحة المستخدم، ضبط سياق المعاملة فوراً على الجلسة (`set_config`) قبل استدعاء `audit_service.log(..., action="USER_LOGIN")`.
    - تسجيل محاولات الدخول الفاشلة بسياق المنصة العام (`entity_type='platform'`, `actor_id=None`, `actor_type='system'`).  
  - **معيار القبول والاختبار:** اختبار تسجيل دخول ناجح لسوبر أدمن ولمشغل، والتأكد من تسجيل حدث الـ audit دون أي اعتراض من الـ RLS.

- [x] **المهمة 6.8:** تحديث سياق مهام الـ Lifespan في `main.py`  
  - **مسار الملف:** `main.py`  
  - **يعتمد على:** المهمة 6.2.  
  - **التفاصيل:** في دالة `lifespan`، عند تشغيل مزامنة انتهاء الاشتراكات:
    ضبط `session.info["is_system"] = True` قبل استدعاء خدمات الاشتراكات والتدقيق، واستخدام `actor_id = None, actor_type = 'system'` في الـ Audit Log.  
  - **معيار القبول والاختبار:** تشغيل التطبيق والتأكد من إتمام المزامنة في بدء التشغيل دون خطأ Foreign Key أو حجب RLS.

---

## المجموعة 7: خدمات الأعمال ومحرك رخص المنصة وعدادات الإيصالات (Core Business Services)

- [ ] **المهمة 7.1:** إنشاء خدمة عداد الإيصالات الذري في `services/counter_service.py`  
  - **مسار الملف:** `services/counter_service.py`  
  - **يعتمد على:** المهمة 5.1 (`counter_repo`).  
  - **التفاصيل:** (تحديد المسؤوليات): تنظيم استدعاء `counter_repo.increment_and_get(...)` داخل المعاملة الجارية قبل عملية الـ INSERT، مع التحقق من أن الجراج نشط ومصرح له بالعمليات، لضمان حجز القفل في أقل من 3 ملي ثانية.  
  - **معيار القبول والاختبار:** اختبار توليد متزامن عبر 20 اتصالاً منفصلاً حقيقياً والتأكد من تسلسل الأرقام وعدم تكرار أي رقم تحت قيد الفرادة `UNIQUE(garage_id, receipt_no)`.

- [ ] **المهمة 7.2:** إنشاء خدمة التكوين والتحقق ومصفوفة الرخص في `services/config_service.py`  
  - **مسار الملف:** `services/config_service.py`  
  - **يعتمد على:** المهمة 5.1 (`garage_config_repo`, `garage_repo`).  
  - **التفاصيل:** (حل النقص 3 والنقطة 7 و 8): 
    - **حساب الصلاحية الفعلية بدقة:** $\text{Effective Permission} = \text{Entitlement} \land \text{Setting}$. إذا أزال السوبر أدمن رخصة `can_use_anpr` من الجراج، تظل القيمة مخزنة كـ `anpr_enabled = True` في سجل التكوين، **لكن الصلاحية الفعلية المحسوبة تصبح False تشغيلياً**، وتتعطل الكاميرات فوراً ويرفض مسار الكاميرات برمز `422 FEATURE_NOT_ENTITLED` أو يغلق الـ WebSocket بكود `1008`.
    - **فحص الاتساق التشغيلي في طبقة الخدمة:** فحص الدمج بين القديم والجديد حتى في حالة `PUT` الجزئي؛ وحظر تفعيل `exit_photo_enabled` إذا كان ANPR معطلاً برمي `422 EXIT_PHOTO_REQUIRES_ANPR`.
    - قفل تعديل `ticket_system` أو نمط البوابة في حال وجود جلسات `ACTIVE` برمي خطأ `409 CONFIG_LOCKED_ACTIVE_SESSIONS`.
    - إدارة قفل التعارض المتزامن (Optimistic Locking عبر رفع `version = version + 1`).  
  - **معيار القبول والاختبار:** اختبار محاولة تعديل إعداد غير مرخص والتأكد من رفضه بـ 422، واختبار إلغاء رخصة ANPR من الجراج والتأكد من تعطل الكاميرات فوراً.

- [ ] **المهمة 7.3:** تحديث خدمة الجلسات لدعم التذاكر الورقية والتحقق من المدة في `services/session_service.py`  
  - **مسار الملف:** `services/session_service.py`  
  - **يعتمد على:** المهمة 7.1 والمهمة 7.2.  
  - **التفاصيل:** (حل النقص 3 والنقطة 8): 
    - فحص حالة الجراج: إذا كان `SUSPENDED` يُعاد `403 TENANT_SUSPENDED`، وإذا كانت الفاتورة `PAST_DUE` يُعاد `403 BILLING_PAST_DUE`، لمنع حركات الدخول وفتح الورديات مع السماح بحركات الخروج وتصفية الحساب حصراً (Exit-Only Grace Mode).
    - دعم التذاكر الورقية (`ticket_code`): توليد كود عشوائي غير متسلسل وطباعته، ومسح الباركود يفسر دائماً كخروج.
    - دعم نافذة تسوية التذكرة المفقودة (`LOST_CARD`) برقم اللوحة وتطبيق غرامة `lost_card_penalty` المستخرجة من جدول `pricing_rules`.
    - **فحص مدة الجلسة والتأكيد اليدوي الديناميكي:** إذا كانت المدة المنقضية أقل من `garage_config.min_session_duration_seconds` ولم يُمرر خيار التأكيد `confirm_short_session=True`، رمي خطأ `409 SHORT_SESSION_CONFIRMATION_REQUIRED` مع تفاصيل المدة المنقضية والمطلوبة.
    - **منع المسح المزدوج:** استخدام القفل الاستشاري المزدوج الأصلي `SELECT pg_try_advisory_xact_lock(:garage_id, :card_id)`؛ وفي حال فشل الحصول على القفل يرمي فوراً خطأ `429 RAPID_DOUBLE_SCAN`.
    - فصل التقاط صورة الخروج ليكون خارج المعاملة المصرفية.  
  - **معيار القبول والاختبار:** اختبار محاولة تسجيل خروج بعد 25 ثانية والتأكد من عودة 409 مع تفاصيل التأكيد، ثم إعادة الإرسال بـ `confirm_short_session=True` وتأكيد اكتمال الخروج بنجاح.

- [ ] **المهمة 7.4:** تحديث خدمة التقارير لدعم البوابات الديناميكية في `services/report_service.py`  
  - **مسار الملف:** `services/report_service.py`  
  - **يعتمد على:** المهمة 5.1 (`garage_gate_repo`).  
  - **التفاصيل:** استبدال الحلقة الثابتة بالاستعلام الديناميكي عن بوابات الجراج الفعلية من مستودع `garage_gate_repo`.  
  - **معيار القبول والاختبار:** إنشاء جراج بـ 8 بوابات والتأكد من ظهور أعمدة البوابات الثمانية في تقرير لوحة البوابات.

- [ ] **المهمة 7.5:** تحديث خدمة التدقيق لدعم سياق المنصة والوصول الإداري في `services/audit_service.py`  
  - **مسار الملف:** `services/audit_service.py`  
  - **يعتمد على:** المهمة 3.8.  
  - **التفاصيل:** دعم وسائط `actor_type`, `garage_id`, `owner_id` وإتاحة `actor_id=None` للأحداث المؤتمتة.  
  - **معيار القبول والاختبار:** اختبار تسجيل حدث من النظام بـ `actor_id=None` وتأكيد نجاح كتابته في قاعدة البيانات دون كسر القيد `ck_audit_garage_context`.

---

## المجموعة 8: وكلاء الكاميرات وتخزين الصور المعزول (Multi-Tenant Edge & Secure Media)

- [ ] **المهمة 8.1:** إنشاء طبقة التخزين المعزول في `services/storage.py`  
  - **مسار الملف:** `services/storage.py`  
  - **يعتمد على:** لا يوجد اعتماد خارجي.  
  - **التفاصيل:** 
    - تعريف الواجهة التجريدية `StorageProvider`.
    - تطبيق `LocalStorageProvider` في Phase 5 لحفظ الصور في مسار معزول:
      `media/garages/{garage_id}/plates/{YYYY}/{MM}/{session_id}_{type}.webp`
    - فحص حجم الملف المشدد: الحد الأقصى **1,500,000 بايت** مطابقاً لـ `config.py:30`.
  - **معيار القبول والاختبار:** اختبار حفظ صورة والتأكد من وجودها في المسار الهيكلي الصحيح ورفض ملف يتجاوز 1.5 ميجابايت برمز 413.

- [ ] **المهمة 8.2:** تحديث مصافحة الـ WebSocket للكاميرات بجدول الأجهزة المستثنى من RLS في `routes/edge_ws.py`  
  - **مسار الملف:** `routes/edge_ws.py`  
  - **يعتمد على:** المهمة 5.1 (`edge_device_repo`) والمهمة 7.2.  
  - **التفاصيل:** (حل البلوكير 3): 
    - استخراج التوكن من رأس الطلب `X-Edge-Token`.
    - التحقق من تجزئة SHA-256 عبر `edge_device_repo.get_by_token_hash` (والذي يعمل بنجاح بدون سياق مسبق لأن جدول `edge_devices` مستثنى من RLS).
    - التحقق من رخصة ANPR للجراج (`can_use_anpr` و `anpr_enabled`)؛ وإذا كانت معطلة إغلاق الاتصال بكود `1008 Policy Violation`.
    - **إدارة جلسة قاعدة البيانات:** فتح جلسة خفيفة للتحقق وإغلاقها فوراً قبل إتمام ترقية الـ WebSocket لمنع استنزاف الاتصالات في الـ Connection Pool.  
  - **معيار القبول والاختبار:** اختبار الاتصال بتوكن غير صالح أو لجراج غير مرخص بـ ANPR والتأكد من إغلاق الاتصال بكود `1008`، واختبار الاتصال بتوكن صالح والتأكد من قبول المصافحة.

- [ ] **المهمة 8.3:** إنشاء مسار رفع الصور المصادق وضبط السياق الفوري في `routes/edge_api.py`  
  - **مسار الملف:** `routes/edge_api.py`  
  - **يعتمد على:** المهمة 8.1 والمهمة 8.2.  
  - **التفاصيل:** (حل البلوكير 3): مسار `POST /api/v1/edge/upload-capture` محمي بـ `X-Edge-Token`:
    - التحقق من التوكن عبر `edge_device_repo.get_by_token_hash` واستخراج `device.garage_id`.
    - **ضبط سياق الجراج فوراً على الجلسة:** `set_config('app.current_garage_id', device.garage_id, true)` قبل إجراء أي استعلام لاحق.
    - التحقق من تبعية `session_id` لجراج الجهاز تحت حماية RLS (منع العبث العابر للجراجات برمي 404).
    - التحقق من أن البوابة مسندة للجهاز في `edge_device_gates`.
    - حفظ الملف عبر `StorageProvider`.
    - إرسال إشعار نصي خفيف فقط عبر `EventBus` القائم في `services/event_bus.py` (أقل من 8KB).  
  - **معيار القبول والاختبار:** رفع صورة صالحة والتأكد من حفظها وتحديث مسار الصورة في الجلسة `sessions.image_path`.

- [ ] **المهمة 8.4:** إنشاء مسار استعراض الصور المصادق وتأكيد حظر التثبيت المفتوح  
  - **مسار الملفات:** `routes/garage_management_api.py` و `main.py`  
  - **يعتمد على:** المهمة 8.1.  
  - **التفاصيل:** (حل النقص 4): 
    1. إنشاء مسار `GET /api/v1/garages/{garage_id}/sessions/{id}/plate-image`:
       التحقق من صلاحية المستخدم للجراج وتقديم الصورة عبر `FileResponse`.
    2. التأكد من أن `main.py` لا يحتوي على أي `app.mount("/media", ...)` عام ومفتوح، وقصر الوصول للصور على المسار المصادق فقط لمنع تسريب صور السيارات دون تفويض.  
  - **معيار القبول والاختبار:** محاولة مستخدم من جراج 2 طلب صورة جلسة تابعة لجراج 1 والتأكد من إرجاع `404 TENANT_NOT_FOUND`، والتأكد من تعذر الوصول للصورة عبر رابط static مباشر.

- [ ] **المهمة 8.5:** إنشاء مهمة تنظيف الصور الدورية (Media Retention Job) في `tasks/media_retention.py`  
  - **مسار الملف:** `tasks/media_retention.py`  
  - **يعتمد على:** المهمة 8.1.  
  - **التفاصيل:** (حل النقص 4 - مطابق للقسم 16 من spec.md): مهمة دورية مجدولة تحذف ملفات الصور التي تجاوزت 90 يوماً من المجلد المشترك مع الإبقاء الكامل على سجلات الجلسات في قاعدة البيانات لأغراض المحاسبة والتدقيق.  
  - **معيار القبول والاختبار:** اختبار تشغيل الدالة بملفات وهمية قديمة (>90 يوماً) وجديدة (<90 يوماً) والتأكد من حذف القديمة فقط وبقاء سجلات قاعدة البيانات سليمة.

---

## المجموعة 9: مسارات الـ API الإدارية والميدانية وفرض الحصص (Scoped API Routes & Quota Enforcement)

- [ ] **المهمة 9.1:** إنشاء مسارات السوبر أدمن وفرض حصة الجراجات في `routes/platform_admin.py`  
  - **مسار الملف:** `routes/platform_admin.py`  
  - **يعتمد على:** المجموعة 6 (الصلاحيات) والمهمة 5.1.  
  - **التفاصيل:** (حل النقص 3): 
    - `POST /api/v1/platform/owners`: إنشاء حساب مالك جديد وتحديد باقته وحصته `max_garages_allowed`.
    - `GET /api/v1/platform/owners`: استعراض قائمة الملاك.
    - `POST /api/v1/platform/garages`: إنشاء جراج وترخيص الـ Entitlements (`can_use_anpr`, `can_use_subscriptions`, `max_gates`). **التحقق من عدم تجاوز عدد جراجات المالك لحصته `max_garages_allowed` برمي `422 MAX_GARAGES_EXCEEDED`**.
    - `GET /api/v1/platform/audit-trail`: استعراض سجل التدقيق العام للمنصة.  
  - **معيار القبول والاختبار:** محاولة إضافة جراج ثانٍ لمالك بحصة 1 والتأكد من رفض الطلب بـ 422.

- [ ] **المهمة 9.2:** إنشاء مسارات المالك وتوحيد التسمية في `routes/owner_api.py`  
  - **مسار الملف:** `routes/owner_api.py`  
  - **يعتمد على:** المجموعة 6 والمهمة 5.1.  
  - **التفاصيل:** (حل النقص 4): 
    - `GET /api/v1/owner/dashboard`: لوحة التحكم المجمعة لإيرادات ونسب إشغال أسطول الجراجات المملوكة.
    - `GET /api/v1/owner/garages`: استعراض قائمة الجراجات التابعة للمالك الحالي فقط.  
  - **معيار القبول والاختبار:** اختبار استدعاء المسار من مالك والتأكد من عودة جراجاته فقط دون جراجات الملاك الآخرين.

- [ ] **المهمة 9.3:** إنشاء مسارات إدارة الجراج، تدوير التوكن، ومصفوفة صلاحية تعديل اللوحة في `routes/garage_management_api.py`  
  - **مسار الملف:** `routes/garage_management_api.py`  
  - **يعتمد على:** المجموعة 6 والمهمة 7.2.  
  - **التفاصيل:** (حل النقص 3 والنقطة 14): مسارات تحت البادئة `/api/v1/garages/{garage_id}/*`:
    - `GET/PUT /config`: قراءة وتعديل تكوين الجراج (بما يتوافق مع الـ Entitlements).
    - `GET/POST /gates`: استعراض وإضافة بوابات الجراج، مع **التحقق من عدم تجاوز `max_gates` برمي `422 MAX_GATES_EXCEEDED`**.
    - `GET/POST /edge-devices`: إدارة أجهزة الكاميرات واستعراضها مقيدة بشرط `WHERE garage_id = :garage_id`.
    - `POST /edge-devices/{id}/rotate-token`: تدوير توكن الجهاز؛ توليد توكن عشوائي جديد، إرجاع التوكن الخام لمرة واحدة فقط في الاستجابة، حفظ تجزئة SHA-256، وإلغاء صلاحية التوكن القديم فوراً.
    - `GET /sessions/{id}`: استعراض تفاصيل جلسة.
    - `POST /sessions/{id}/plate-override`: تصحيح لوحة السيارة يدوياً (متاح للـ OWNER، وللـ MANAGER، وللـ OPERATOR بشرط وجود وردية مفتوحة له).  
  - **معيار القبول والاختبار:** اختبار محاولة إضافة بوابة تتجاوز حد الجراج والتأكد من رفضها بـ 422، واختبار تدوير التوكن والتأكد من تعطل الاتصال بالتوكن القديم.

- [ ] **المهمة 9.4:** تأكيد العزل الضمني لمسارات التشغيل الميداني  
  - **مسار الملفات:** `routes/sessions.py`, `routes/shifts.py`, `routes/cards.py`, `routes/rates.py`  
  - **يعتمد على:** المجموعة 6.  
  - **التفاصيل:** مراجعة كافة نقاط النهاية الميدانية والتأكد من أنها لا تعتمد على معرف جراج ممرر في الرابط، بل تستخرجه حصراً من سجل المشغل الموثق في قاعدة البيانات `users.garage_id`، مع تطبيق دالة حظر تصعيد الصلاحيات `validate_role_assignment` عند إدارة أو تعديل أي مستخدم في `routes/users.py`.  
  - **معيار القبول والاختبار:** اختبار تسجيل دخول سيارة والتأكد من قيدها في جراج المشغل تلقائياً.

- [ ] **المهمة 9.5:** تسجيل الموجهات الجديدة في `main.py`  
  - **مسار الملف:** `main.py`  
  - **يعتمد على:** المهام 9.1 إلى 9.3.  
  - **التفاصيل:** استيراد وتسجيل `platform_admin.router`, `owner_api.router`, `garage_management_api.router`, `edge_api.router`.  
  - **معيار القبول والاختبار:** فحص تشغيل السيرفر والتأكد من ظهور كافة المسارات في مخطط التوجيه `app.routes`.

---

## المجموعة 10: واجهات المستخدم (SSR Jinja2) ومبدل الجراجات وحماية CSRF

- [ ] **المهمة 10.1:** إنشاء وسيط الحماية ضد التزوير في `middlewares/csrf.py`  
  - **مسار الملف:** `middlewares/csrf.py`  
  - **يعتمد على:** لا يوجد اعتماد خارجي.  
  - **التفاصيل:** وسيط يتحقق من وجود وصحة رمز `_csrf_token` في النماذج أو `X-CSRF-Token` في الترويسات لكافة طلبات التعديل (`POST`, `PATCH`, `DELETE`) الصادرة من المتصفح عبر الكوكيز لمسارات واجهة المستخدم (`/ui/*`). مع **استثناء صريح** لمسارات الـ REST API عديمة الحالة (`/api/v1/*`)، ومسارات وكلاء الكاميرات المحلية (`/api/v1/edge/*`)، ومصافحة اتصالات الـ WebSocket (`/ws/*`).  
  - **معيار القبول والاختبار:** إرسال طلب POST من المتصفح بدون رمز لمسار `/ui/*` والتحقق من رفضه بكود `403 CSRF_TOKEN_INVALID`، والتأكد من قبول طلبات POST على `/api/v1/*` بدون اشتراط CSRF Token.

- [ ] **المهمة 10.2:** إنشاء مسار تبديل الجراج للمالك في `routes/ui_admin.py`  
  - **مسار الملف:** `routes/ui_admin.py`  
  - **يعتمد على:** المهمة 10.1 والمهمة 6.3.  
  - **التفاصيل:** مسار `POST /ui/admin/switch-garage` محمي بـ CSRF Token:
    يستقبل `garage_id`، يتحقق من ملكية المالك للجراج عبر `garage_repo`، ثم يضبط كوكيز `pgms_active_garage=<garage_id>; HttpOnly; SameSite=Lax` ويعيد التوجيه إلى الصفحة السابقة.  
  - **معيار القبول والاختبار:** اختبار التبديل لجراج مملوك والتأكد من تحديث الكوكيز، ومحاولة التبديل لجراج غير مملوك والتأكد من رفضه بـ 404.

- [ ] **المهمة 10.3:** تحديث القالب الأساسي للإدارة في `templates/admin/base_admin.html`  
  - **مسار الملف:** `templates/admin/base_admin.html`  
  - **يعتمد على:** المهمة 10.2.  
  - **التفاصيل:** 
    - إضافة مكون منسدل `Garage Switcher` في الشريط العلوي يظهر للمالك حصراً يعرض جراجه النشط وقائمة جراجاته المصرح له بها مع نموذج إرسال سريع محمي بـ CSRF.
    - عرض شارة ثابتة باسم الجراج للمدير والمشغل وإخفاء القائمة المنسدلة.
    - حقن حقل الـ CSRF المخفي في كافة النماذج.  
  - **معيار القبول والاختبار:** تسجيل الدخول كمالك ورؤية المبدل، وتسجيل الدخول كمدير ورؤية الشارة الثابتة.

- [ ] **المهمة 10.4:** إنشاء قالب ومسار لوحة تحكم أسطول المالك في `templates/owner/dashboard.html`  
  - **مسار الملفات:** `templates/owner/dashboard.html` و `routes/ui_admin.py`  
  - **يعتمد على:** المهمة 10.3.  
  - **التفاصيل:** (حل النقص 4): توحيد المسار تحت `/ui/owner/dashboard` مع دعم التحويل التلقائي من `/owner/dashboard`. عرض بطاقات إحصائية لكل جراج (الإيرادات اليومية، عدد السيارات بالداخل، نسبة الإشغال) مع أزرار انتقال مباشر لإدارة كل جراج.  
  - **معيار القبول والاختبار:** فتح صفحة `/ui/owner/dashboard` والتأكد من عرض بيانات جراجات المالك بصرياً بتنسيق متجاوب.

- [ ] **المهمة 10.5:** تحديث قوالب الإدارة لدعم البوابات الديناميكية (1..10)  
  - **مسار الملفات:** 
    - `templates/admin/sessions.html`
    - `templates/admin/shifts.html`
    - `templates/admin/reports/revenue.html`
  - **يعتمد على:** المهمة 10.3.  
  - **التفاصيل:** استبدال القوائم المنسدلة ذات الأرقام الثابتة من 1 إلى 5 بتصيير ديناميكي من مصفوفة `garage_gates` الممررة مع القالب.  
  - **معيار القبول والاختبار:** فتح شاشة الورديات لجراج به 8 بوابات والتأكد من ظهور البوابات الثمانية في خيارات الفلترة.

- [ ] **المهمة 10.6:** تحديث قوالب الطباعة الحرارية للإيصالات في `templates/receipts/thermal.html`  
  - **مسار الملف:** `templates/receipts/thermal.html`  
  - **يعتمد على:** المهمة 4.8.  
  - **التفاصيل:** تعديل موضع رقم الإيصال ليطبع `session.receipt_no` بدلاً من المعرف الداخلي `session.id`.  
  - **معيار القبول والاختبار:** معاينة طباعة الإيصال والتأكد من ظهور الرقم المتسلسل الخاص بالجراج.

---

## المجموعة 11: حزمة اختبارات PostgreSQL الشاملة واختبارات العزل الصارمة (Test Suite & RLS Verification)

- [ ] **المهمة 11.1:** تحديث إعدادات الاختبارات `tests/conftest.py` لدعم PostgreSQL الحقيقي  
  - **مسار الملف:** `tests/conftest.py`  
  - **يعتمد على:** المجموعة 2 (الميجريشن) والمجموعة 1.  
  - **التفاصيل:** إضافة دعم اختبارات PostgreSQL حقيقية (عبر Testcontainers أو قاعدة بيانات اختبار مخصصة محددة عبر `TEST_DATABASE_URL`) مع تشغيل الاختبارات بدور `pgms_app` غير المميز لاختبار الـ RLS فعلياً.  
  - **معيار القبول والاختبار:** تشغيل اختبار قاعدة بيانات والتأكد من الاتصال بمحرك PostgreSQL بدور `pgms_app`.

- [ ] **المهمة 11.2:** إنشاء اختبارات العزل المتبادل وحماية IDOR الجدولية المطابقة للمسارات الفعلية للكود  
  - **مسار الملف:** `tests/integration/test_cross_tenant_isolation.py`  
  - **يعتمد على:** المهمة 11.1 والمجموعة 9.  
  - **التفاصيل:** (حل النقطة 5 والملاحظة 17): 
    - إنشاء جراجين (جراج أ وجراج ب).
    - اختبارات جدولية عبر `@pytest.mark.parametrize` على المسارات الفعلية القائمة في الكود:
      (`GET/PUT /api/v1/garages/{id}/config`, `GET/POST /api/v1/garages/{id}/gates`, `GET/POST /api/v1/garages/{id}/edge-devices`, `GET /api/v1/garages/{id}/sessions/{sid}`, `GET /api/v1/cards/{card_code}`, `PUT/PATCH /api/v1/rates/{rule_id}`, `GET /api/v1/shifts/{shift_id}/summary`, `GET/PATCH /api/v1/users/{user_id}`).
    - **التحقق الحاسم:** طلب مستخدم من جراج أ لمورد يخص جراج ب يعيد دائماً **`404 TENANT_NOT_FOUND`** (وليس 403 لمنع كشف وجود المورد أو التخمين).
    - اختبارات حماية الجداول غير الخاضعة لـ RLS (`users`, `garages`, `edge_devices`): التأكد من أن مدير جراج أ لا يستطيع استعراض أو تعديل مستخدمي جراج ب عبر `/api/v1/users/{user_id}` ويعيد 404.  
  - **معيار القبول والاختبار:** نجاح كافة الاختبارات بنسبة 100% عبر `pytest tests/integration/test_cross_tenant_isolation.py`.

- [ ] **المهمة 11.3:** إنشاء اختبارات النزاهة المرجعية المركبة في `tests/integration/test_composite_fk.py`  
  - **مسار الملف:** `tests/integration/test_composite_fk.py`  
  - **يعتمد على:** المهمة 11.1 والمهمة 2.5.  
  - **التفاصيل:** محاولة إدخال سجل جلسة يشير إلى `shift_id` أو `card_id` تابع لجراج آخر، والتحقق من رفض PostgreSQL الفوري للمعاملة برمي خطأ النزاهة المرجعية `IntegrityError` بسبب كسر المفتاح الأجنبي المركب.  
  - **معيار القبول والاختبار:** تأكيد رمي `IntegrityError` من المحرك عند أي محاولة ربط عابرة للجراجات على مستوى قاعدة البيانات مباشرة.

- [ ] **المهمة 11.4:** إنشاء اختبارات سياسات الـ RLS الإيجابية والسلبية و `WITH CHECK` في `tests/integration/test_rls_policies.py`  
  - **مسار الملف:** `tests/integration/test_rls_policies.py`  
  - **يعتمد على:** المهمة 11.1 والمهمة 2.5.  
  - **التفاصيل:** (حل النقطة 5 و B6 والبلوكير 3): 
    - فحص الجداول الـ 11 الخاضعة لـ RLS بالاسم: `parking_sessions`, `parking_cards`, `shifts`, `pricing_rules`, `subscribers`, `subscription_plans`, `subscriptions`, `garage_gates`, `edge_device_gates`, `garage_configs`, `audit_logs`.
    - التحقق من حجب كافة السجلات وعودة 0 صفوف عند غياب سياق الجلسة للجداول الـ 11 بالكامل.
    - **اختبار إيجابي كامل:** سياق جراج 1 يرى صفوف جراج 1 فقط ولا يرى صفوف جراج 2 لكل جدول من الجداول الـ 11.
    - **اختبار `WITH CHECK` الصارم:** محاولة تنفيذ `INSERT` بسياق جراج 1 مع تمرير `garage_id = 2` في البيانات، والتأكد من رفض قاعدة البيانات للعملية برمي استثناء `RLS Policy Violation`.
    - **فحص ملكية الجداول:** التحقق عبر استعلام `pg_class.relowner` والتأكد من أن مالك الجداول هو `pgms_migrator` وأن `pgms_app` ليس مالكاً لأي جدول ويمتلك `rolbypassrls = false`.
    - **اختبار سياق المالك ومؤشر الفهرس:** اختبار أداء استعلام المالك عبر الـ Subquery والتأكد من استخدام الفهرس `idx_garages_owner_id`.  
  - **معيار القبول والاختبار:** نجاح كافة اختبارات الـ RLS الإيجابية والسلبية واختبارات الـ WITH CHECK بنسبة 100%.

- [ ] **المهمة 11.5:** إنشاء اختبار منع تسرب السياق عبر مجمع الاتصالات (Connection Pool Leakage Test)  
  - **مسار الملف:** `tests/integration/test_pool_leakage.py`  
  - **يعتمد على:** المهمة 11.1 والمهمة 6.2.  
  - **التفاصيل:** (حل النقطة 5): اختبار حاسم لمنع تسرب السياق عبر اتصالات الـ pool:
    1. ضبط `pool_size = 1, max_overflow = 0`.
    2. تنفيذ طلب أول على الاتصال بسياق جراج 1 واسترجاع بياناته وإتمام الطلب.
    3. تنفيذ طلب ثانٍ على **نفس الاتصال الفيزيائي المعاد استخدامه من المجمع** بدون سياق، والتحقق القاطع من أن الاستعلام الثاني يعيد 0 صفوف وأن `current_setting('app.current_garage_id', true)` غير مضبوطة.
    4. تنفيذ مهمة نظام خلفية (Lifespan Task) بـ `is_system_context = 'true'`، ثم تنفيذ طلب عادي لمشغل بعدها والتأكد من عدم توارث سياق النظام إطلاقاً.  
  - **معيار القبول والاختبار:** نجاح اختبار عدم تسرب السياق بنسبة 100% دون أي تسرب عبر الاتصالات المتتالية.

- [ ] **المهمة 11.6:** إنشاء اختبارات مصادقة SaaS وسجل التدقيق ومنع تصعيد الصلاحيات  
  - **مسار الملف:** `tests/integration/test_saas_auth_audit.py`  
  - **يعتمد على:** المهمة 11.1 والمهمة 6.4 و 6.5 و 6.6 و 6.7.  
  - **التفاصيل:** (حل النقطة 14): 
    - اختبار تسجيل الدخول بكود الجراج وبدونه.
    - اختبار حجب مستخدم بـ `must_change_password=True` من كافة المسارات وإرجاع 403، والسماح له بمسار التغيير واستعادة الوصول.
    - اختبار التسجيل التلقائي لحدث `SUPER_ADMIN_GARAGE_ACCESS` في معاملة مستقلة عند استعراض السوبر أدمن لجراج معين وتأكيد قيده حتى لو تم التراجع عن العملية اللاحقة.
    - **اختبار منع تصعيد الصلاحيات:** محاولة مدير جراج إنشاء مستخدم برتبة مدير أو مالك أو سوبر أدمن، والتأكد من رفض الطلب بكود `403 PRIVILEGE_ESCALATION_DENIED`.  
  - **معيار القبول والاختبار:** التأكد من قيد كافة الأحداث بنجاح ورمي 403 عند محاولة تصعيد الصلاحيات.

- [ ] **المهمة 11.7:** إنشاء اختبارات وكلاء الكاميرات وعزلها واختبارات الحافة في `tests/integration/test_edge_multi_tenant.py`  
  - **مسار الملف:** `tests/integration/test_edge_multi_tenant.py`  
  - **يعتمد على:** المهمة 11.1 والمجموعة 8.  
  - **التفاصيل:** (حل البلوكير 3 والنقطة 5): 
    - اختبار نجاح مصافحة WebSocket عبر التوكن بالبحث في `edge_devices` بدون سياق مسبق.
    - اختبار اتصال WebSocket بتوكن ملغى والتأكد من إغلاقه بكود 1008.
    - محاولة جهاز من جراج أ رفع صورة لجلسة تابعة لجراج ب (التأكد من عودة 404).
    - محاولة جهاز رفع صورة لبوابة غير مسندة له في `edge_device_gates` (التأكد من عودة 403).
    - محاولة رفع ملف صورة يتجاوز 1.5 ميجابايت (التأكد من رفضه بـ 413 Payload Too Large).  
  - **معيار القبول والاختبار:** نجاح كافة اختبارات أمان وكلاء الكاميرات بنسبة 100%.

- [ ] **المهمة 11.8:** إنشاء اختبار هجرة وترحيل البيانات واختبار التراجع في `tests/integration/test_saas_migrations.py`  
  - **مسار الملف:** `tests/integration/test_saas_migrations.py`  
  - **يعتمد على:** المهمة 11.1 والمجموعة 2.  
  - **التفاصيل:** (حل النقطة 5 و 7 والبلوكير 4): 
    - تشغيل المراجعات الخمسة على قاعدة تحوي بيانات سابقة لجراج باب اللوق تتضمن فجوات في المعرفات (Non-contiguous IDs مثل 1, 3, 7, 10)، وبوابات متعددة تاريخية (مثل 1, 2, 3, 5).
    - التحقق من إنشاء كافة البوابات التاريخية في `garage_gates` بنجاح دون كسر المفاتيح الخارجية المركبة.
    - التحقق من عدم فقدان أي جلسة أو كارت وتطابق `receipt_no = id`.
    - التحقق من أن العداد في `garage_counters` ضُبط على `MAX(id)` وأن الإدخال الجديد يأخذ الرقم التالي المتسلسل دون تكرار تحت `UNIQUE(garage_id, receipt_no)`.
    - اختبار التراجع الكامل `alembic downgrade base` ثم إعادة الترقية والتأكد من سلامة التراجع والترقية.  
  - **معيار القبول والاختبار:** اكتمال الميجريشن والتراجع بنجاح تام وبقاء البيانات متسقة.

- [ ] **المهمة 11.9:** معيار قياس الأداء وحجز قفل العداد (Concurrency & Lock Benchmark)  
  - **مسار الملف:** `tests/integration/test_counter_performance.py`  
  - **يعتمد على:** المهمة 11.1 والمهمة 7.1.  
  - **التفاصيل:** (حل النقطة 9): اختبار متزامن عبر 20 اتصالاً منفصلاً حقيقياً يولد 500 حركة دخول متزامنة:
    - **معيار الفشل الصارم (Hard Assertion):** تسلسل الأرقام بنسبة 100%، عدم تكرار أي رقم إيصال، عدم حدوث deadlocks، وعدم فقدان أي حركة دخول.
    - **المعيار التشخيصي (Diagnostic Benchmark):** قياس زمن حجز القفل الحصري وطباعة تحذير تشخيصي (Warning) إذا تجاوز P99 حاجز الـ 3 ملي ثانية أو تجاوز زمن الاستجابة 150 ملي ثانية نتيجة بيئات الـ CI الافتراضية المشتركة، دون التسبب في فشل الاختبار إذا تحققت صحة البيانات بنسبة 100%.  
  - **معيار القبول والاختبار:** تقرير أداء يؤكد صحة تسلسل العداد بنسبة 100% وتقرير زمن الاستجابة.

---

## المجموعة 12: إجراءات النشر الميداني وقياس نافذة الصيانة (Production Rollout & Maintenance)

- [ ] **المهمة 12.1:** إنشاء سكريبت النسخ الاحتياطي الاستباقي في `scripts/pre_migration_backup.sh`  
  - **مسار الملف:** `scripts/pre_migration_backup.sh`  
  - **يعتمد على:** المهمة 1.1.  
  - **التفاصيل:** سكريبت يأخذ نسخة احتياطية كاملة بصيغة مخصصة عبر دور يمتلك `BYPASSRLS`:  
    `pg_dump -h localhost -U pgms_migrator -Fc -d pgms_prod > pre_phase5_backup.dump`  
  - **معيار القبول والاختبار:** تشغيل السكريبت والتحقق من سلامة ملف الـ dump عبر `pg_restore -l`.

- [ ] **المهمة 12.2:** سكريبت قياس نافذة الصيانة والتنفيذ التجريبي في `scripts/benchmark_migration.sh`  
  - **مسار الملف:** `scripts/benchmark_migration.sh`  
  - **يعتمد على:** المهمة 12.1.  
  - **التفاصيل:** سكريبت يستعيد نسخة الإنتاج على بيئة Staging ويشغل المراجعات الخمسة وسكريبت تهيئة الحسابات مع قياس الزمن المستغرق بالملي ثانية لحساب نافذة الصيانة بدقة قبل التنفيذ الفعلي على الإنتاج.  
  - **معيار القبول والاختبار:** إخراج تقرير زمني صريح بمدة كل مراجعة بالملي ثانية.

- [ ] **المهمة 12.3:** سكريبت التراجع السريع عند الطوارئ قبل نقطة اللا عودة في `scripts/rollback_phase5.sh`  
  - **مسار الملف:** `scripts/rollback_phase5.sh`  
  - **يعتمد على:** المهمة 12.1.  
  - **التفاصيل:** سكريبت طوارئ يُنفذ في حال حدوث أي عائق أثناء نافذة الصيانة وقبل إنشاء جراج ثانٍ؛ يقوم بإسقاط الجداول واستعادة ملف الـ dump وإعادة تشغيل الحاويات في زمن تعافي مستهدف محدد صراحة بـ: **RTO < 15 دقيقة**.  
  - **معيار القبول والاختبار:** تجربة الاستعادة على بيئة الاختبار وتأكيد إتمامها في أقل من 15 دقيقة وعودة النظام لحالته الأصلية.

- [ ] **المهمة 12.4:** قائمة الفحص والتحقق بعد الترحيل (`Post-Migration Health Checklist`)  
  - **مسار الملف:** `docs/specs/phase-5-saas/post_migration_checklist.md`  
  - **يعتمد على:** اكتمال المجموعات السابقة.  
  - **التفاصيل:** وثيقة تشغيلية تتضمن 10 فحوصات ميدانية تجرى فور انتهاء الهجرة (التحقق من تسجيل دخول مشغل باب اللوق، فتح وردية، مسح كارت، طباعة إيصال برقم متسلسل، التقاط صورة، فحص سجل التدقيق، فحص عمل الكوكيز وحماية CSRF).  
  - **معيار القبول والاختبار:** اعتماد الوثيقة كدليل تسليم نهائي للمرحلة الخامسة.

---

## إقرار المطابقة والجاهزية (Definition of Done)

تعتبر المرحلة الخامسة (Phase 5) مكتملة ومؤهلة للإطلاق فقط عند:
1. اكتمال كافة المهام المذكورة في المجموعات الـ 12 ووضع علامة `[x]`.
2. نجاح كافة اختبارات الـ Unit والـ Integration بنسبة 100% دون أي تجاوز (`0 failures, 0 errors`).
3. اجتياز الفحص الصارم للأنماط والأنواع المتوافق مع خط الأساس الحالي للمشروع: `black --check .`, `ruff check .`, `mypy`.
4. التحقق العملي من استمرار تشغيل جراج باب اللوق الميداني بكافة ميزاته السابقة دون أي انقطاع.
