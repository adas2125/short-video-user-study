# RQ1 continuation-intention prototype

This is a PostgreSQL-backed prototype for the short-video RQ1 study. It uses
YouTube embeds from the annotated catalog, pauses each video at every ordered,
annotation-informed probe time, and records each response immediately.

## Run it locally

```bash
cd rq1_prototype
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL="postgresql://USER:PASSWORD@HOST:5432/DATABASE"
export ADMIN_PASSWORD="choose-a-long-private-password"
flask --app app run --debug
```

Open `http://127.0.0.1:5000`. The tables are created in the PostgreSQL database
on the first request. To view saved pilot responses, open:

```text
http://127.0.0.1:5000/admin/summary
```

The browser will ask for username `admin` and the `ADMIN_PASSWORD` value.

For a fresh test database, create a separate empty PostgreSQL database and set
`DATABASE_URL` to its connection URL.

## What is stored

- A random anonymous browser-session token.
- The shuffled video order and every ordered probe time assigned per trial.
- Player events, including the probe's actual video time.
- The `continue` or `next` response and response latency in milliseconds.

`stimuli.json` contains the six YouTube Shorts from the catalog and a small
set of candidate probe moments derived from the annotations. The participant
never sees the structural category.

## Important prototype limits

- This is for local piloting only. Before collecting research data, replace the
  development secret, add an authenticated export route, deploy over HTTPS,
  and obtain the appropriate study approval.
- The YouTube player reports time at roughly 150 ms polling resolution. The
  database logs both scheduled and actual pause times.
- The endpoint `/admin/summary` requires the `ADMIN_PASSWORD` environment
  variable. Do not share that password with participants.
