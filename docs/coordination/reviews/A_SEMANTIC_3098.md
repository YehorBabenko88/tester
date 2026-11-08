# Проверка A — исправление Decimal semantics

Точный HEAD: `3098d55f2bb41c5389f83b955d87a3050ca52f85`; родитель — `39645a64ed424270401de3b1f35767b1fd7c3d74`. Только tester, read-only исходники/Git. GitHub, сеть, торговые операции и PostgreSQL не использовались. Полный HEAD извлечён `git archive --format=zip` в `work/a-semantic-3098`.

**Предыдущий P2 закрыт на независимо проверенных сценариях. Новых блокеров в этом узком diff не найдено.**

Diff содержит 9 файлов: handoff.py, два тестовых модуля и документацию/логи. В production-коде два целевых изменения:

- `semantic_hash` для trades сравнивает точные price/size/side, сохраняя исходные payload отдельно. Decimal создаётся из строки без float; `format(n,'f')` не округляет по context.prec; удаляются только незначащие дробные нули. Не используется потенциально округляющий Decimal.normalize. Проверяются finite/positive и границы представления B; непригодные значения сохраняют исходную семантику/ошибки. Время, precision, ID и схема не исключены из сравнения.
- Эквивалентные исходные версии одного stable ID распределяются по разным publication batches: в каждом уникальные ID, но обе source observations и разные исходные SHA сохраняются. Это устраняет прежнюю потерю второй provenance-ссылки без удвоения канонической identity. Фактическое поведение Store/PG в этой проверке не выполнялось; проверены его настоящие normalize/validate_evidence на обоих pins.

## Независимые проверки, не авторский лог

Scratch `work/a-decimal-independent.py` использует реальные маленькие gzip/CSV, iter_trade_records, Journal.export и synthetic explicit input. Четыре проверки: эквивалентные строки с двумя source hashes; реальная другая цена; реальный другой объём; точность 29-значного числа/очень малого объёма при context.prec=3.

- Против старого `work/a-architecture-check` (39645, ранее проверенные source hashes): **4 теста, 2 ожидаемых FAIL**, exit 1. Реальные изменения цены/объёма уже правильно давали конфликт; equivalence и scale ошибочно конфликтовали.
- Против `work/a-semantic-3098`: **4 PASS**, exit 0. Сохраняются 2 raw_record_versions, 2 records, 2 отдельные source hashes и 2 batches по одному тому же exchange ID; conflicts пуст. При price1.01 вместо1.00 либо size2.01 вместо2.0: один конфликт, обе raw versions сохранены, batches пусты.
- `../venv/Scripts/python.exe` с workspace `tempfile.tempdir` и unittest discovery: **42 PASS**, exit 0.
- Реальный `tests.ingest.verify_handoff` + shared `9766c0093e2608280875ac56db074414a3d62c69` + B `5154efab05d44b96f4aa83819672396d75679f2e`: **5 PASS**, exit 0.
- Тот же consumer gate + B `47fd36f32389174377682a13862f5b36c7a886da`: **5 PASS**, exit 0. Новый consumer-тест подтверждает одинаковые B record_key/content_hash, но разные source SHA, и quarantine при реальной другой цене.

## Команды

В `work/master-supervisor`: `git show -s --format=%P 3098d55...`, `git diff 39645... 3098... --stat`, `git show --format= 3098... -- src/ingest/handoff.py`; архив через Python subprocess/zipfile, без checkout или изменения refs. Сначала base object не был доступен; после появления fetched parent выполнен настоящий Git diff и проверен parent SHA, а не только файловое сравнение.

В корне workspace:

`work/venv/Scripts/python.exe work/a-decimal-independent.py work/a-architecture-check`

`work/venv/Scripts/python.exe work/a-decimal-independent.py work/a-semantic-3098`

В `work/a-semantic-3098`:

`../venv/Scripts/python.exe -c "import os,tempfile,unittest; tempfile.tempdir=os.getcwd(); r=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.discover('.')); raise SystemExit(not r.wasSuccessful())"`

Consumer tests запускались через `tests.ingest.verify_handoff.main()`, с `--references references-baseline` и отдельно `--references references-b47 --storage-sha 47fd36f32389174377682a13862f5b36c7a886da`. GIT_DIR указывал на уже fetched root object database; чужие pinned files копировались только в изолированные references-директории.

Авторский `docs/ingest/decimal-regression.json` сообщает PostgreSQL18.4, одну economic execution/две source observations и неизменность replay. Это **авторское свидетельство**, не независимый PG-прогон. Здесь не проверены реальные INSERT/read/replay/commit/locking в PostgreSQL, весь A–G runtime или live acquisition. Не делался вывод о COMPLETE: существующая PARTIAL/UNVERIFIED политика сохранена. Никаких source/product/remote edits, commit/push/comment не сделано.
