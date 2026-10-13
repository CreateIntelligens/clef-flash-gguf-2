# Clef-Flash GGUF Decision API

This repository contains the API service running at https://clef.aiurl.tw.
It is the secondary CPU-backed service; the primary GPU service runs at
https://clef.create360.ai.

## API

- Swagger: https://clef.aiurl.tw/docs
- ReDoc: https://clef.aiurl.tw/redoc
- OpenAPI: https://clef.aiurl.tw/openapi.json
- Health: https://clef.aiurl.tw/health
- Inference: POST https://clef.aiurl.tw/v1/systemone

The FastAPI facade forwards typed System One requests to the local llama.cpp
server at 127.0.0.1:18080. It supports the choice, noul, and score question
types. Instructions and criteria accept strings, objects, and arrays; Choice
descriptions also accept null. Score response legends preserve the submitted
level descriptions.

The model server has 16,384 tokens of total context across two parallel slots.
Its physical batch and ubatch are 4,096 tokens. Each complete rendered prompt
must fit within one 4,096-token batch. The API does not split requests.

## Install and run

Python 3.12 and the existing llama.cpp model server are required. The installer
downloads the GGUF files into models/; model files and .venv are excluded from
Git. For a new host:

    ./install.sh

The API runs under systemd as clef-flash.service on port 8099. It sends
inference telemetry to InfluxDB. Configure the token outside the repository:

    sudo install -m 600 -o root -g root /dev/null /etc/clef-flash.env
    sudoedit /etc/clef-flash.env
    sudo install -d -m 755 /etc/systemd/system/clef-flash.service.d
    sudo install -m 644 deploy/systemd/clef-flash.service.d/10-env.conf /etc/systemd/system/clef-flash.service.d/10-env.conf
    sudo systemctl daemon-reload
    sudo systemctl restart clef-flash.service

Set INFLUX_TOKEN, INFLUX_URL, and HOST_NAME in /etc/clef-flash.env. The .env.example file is a placeholder
only; never put a live token in Git. Backups, model weights, and the Python
virtual environment are ignored by .gitignore.

## Model switching on `10.9.0.32`

The GPU host keeps the public API and backend ports stable while switching the
decision model. The switch helper validates both health endpoints and sends a
System One smoke request. A failed startup or smoke check restores the previous
container automatically.

```sh
ssh 10.9.0.32 /home/human/bin/clef-model-switch status
ssh 10.9.0.32 /home/human/bin/clef-model-switch d3
ssh 10.9.0.32 /home/human/bin/clef-model-switch clef
```

The helper keeps backend port `18080`, facade port `18100`, and the existing
`clef.create360.ai` routes. The current production model is D3; Clef remains
available as the rollback model.

## Development

Install the pinned API dependencies from requirements.txt. Contract checks are
in test_api_contract.py; the model weights remain external to Git.
