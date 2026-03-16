# PDF Mini App - Production Plus

Questa versione aggiunge:
- storage S3/R2 opzionale
- basic auth opzionale
- logging middleware
- cleanup locale schedulabile
- limiti upload configurabili

## Avvio locale
```bash
pip install -r requirements.txt
python run.py
```

## Docker
```bash
docker build -t pdf-miniapp .
docker run -p 8000:8000 --env-file .env pdf-miniapp
```

## Storage cloud
Per usare S3 o R2:
- imposta `USE_S3=true`
- configura bucket, endpoint e credenziali in `.env`

## Cleanup schedulato
Script incluso:
```bash
python scripts/cleanup_local_uploads.py
```

## Auth
Per proteggere l'app:
- `ENABLE_BASIC_AUTH=true`
- imposta `BASIC_AUTH_USER` e `BASIC_AUTH_PASS`

## Nota
Il frontend è volutamente semplice; qui l'obiettivo è la base deployabile e operativa.
