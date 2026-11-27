# Запуск на сервере пилота

Всё работает на одном сервере в российском облаке (152-ФЗ: персональные данные хранятся в РФ).
Наружу открыты только порты 80 и 443. HTTPS-сертификат Caddy получает и продлевает сам.

## Что нужно заранее

| Что | Где взять |
|---|---|
| Виртуальная машина: 4 vCPU, 8 ГБ памяти, 80 ГБ SSD, Ubuntu 24.04 | Яндекс Облако (ru-central1) или Selectel (Москва) |
| Домен, A-запись на IP сервера | регистратор домена |
| Бакет для фото (приватный) и статический ключ доступа | Yandex Object Storage или S3 Selectel |
| Договор с SMS-шлюзом | SMS.ru или МТС Exolve. До подключения коды входа видны только в журнале сервера |

## Первый запуск

```bash
# на сервере
curl -fsSL https://get.docker.com | sh
git clone https://github.com/sunra218-lgtm/umny-remont.git && cd umny-remont
cp deploy/.env.example deploy/.env
nano deploy/.env          # заполнить; пароли и ключи: openssl rand -hex 32
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env up -d --build
```

Через 2–3 минуты сайт открывается по `https://ДОМЕН`. Первый вход с телефона из `BOOTSTRAP_ADMIN_PHONES`
даёт права администратора платформы.

Сервер сам откажется запускаться, если забыты обязательные настройки: свой `JWT_SECRET`, свой
`BANK_WEBHOOK_SECRET`, адрес с `https://`, выключенный показ кода на экране.

## Обновление

```bash
cd umny-remont && git pull
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env up -d --build
```

Миграции базы применяются сами при старте backend.

## Резервные копии

Контейнер `backup` каждую ночь в 03:00 МСК делает сжатую копию базы в том `backups` и хранит 14 дней.
Фото лежат в Object Storage, у бакета включите версионирование.

Сделать копию сейчас и скопировать её с сервера:

```bash
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env exec backup sh /backup.sh
docker cp umny-backup-1:/backups ./backups
```

Восстановить в пустую базу (проверено на копии):

```bash
C="docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env"
$C stop backend worker
$C exec db psql -U umny -d postgres -c "DROP DATABASE umny" -c "CREATE DATABASE umny"
$C exec backup sh -c 'gunzip -c /backups/umny-ГГГГ-ММ-ДД-ЧЧММ.sql.gz | psql -h db -U umny -d umny'
$C start backend worker
```

Раз в месяц восстанавливайте копию в отдельную базу и проверяйте, что она читается.

## Журналы и проверка

```bash
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env ps
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env logs -f backend worker
curl https://ДОМЕН/api/v1/health      # {"status":"ok"}
```

## Защита

- Заголовки безопасности (CSP, HSTS, запрет встраивания) ставят сайт, API и Caddy.
- Не больше 300 запросов в минуту и 60 изменяющих с одного IP (`RATE_LIMIT_PER_MINUTE`,
  `RATE_LIMIT_WRITES_PER_MINUTE`). IP берётся из заголовка, который ставит Caddy; подделать его снаружи нельзя.
- Описание API (`/api/docs`) на сервере выключено.
- Банк на пилоте — имитация, пока нет тестового доступа Т-Банка. Боевые платежи — спринт 8.
