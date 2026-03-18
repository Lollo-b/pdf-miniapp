# PDF Mini App - Direct Upload R2

## Avvio locale
```bash
python3 -m venv venv
source venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
python3 run.py
```

Apri:
http://127.0.0.1:8000

## CORS R2
Per upload browser -> R2 configura il bucket con un CORS simile:

```json
[
  {
    "AllowedOrigins": [
      "http://127.0.0.1:8000",
      "http://localhost:8000",
      "https://tuodominio.it",
      "https://www.tuodominio.it"
    ],
    "AllowedMethods": ["PUT", "GET", "HEAD"],
    "AllowedHeaders": ["*"],
    "ExposeHeaders": ["ETag"],
    "MaxAgeSeconds": 3600
  }
]
```


## Deploy
- Dockerfile incluso
- render.yaml incluso
