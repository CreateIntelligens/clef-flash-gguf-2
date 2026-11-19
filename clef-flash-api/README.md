# Clef-Flash Decision API

This service exposes the Cloudflare Clef-Flash model through a stable API facade and interactive API documentation. The model backend is llama.cpp; this facade publishes only the health and System One decision routes.

Active service endpoints:
- Primary GPU API: 10.9.0.32 at https://clef.create360.ai.
- Secondary CPU API: 10.9.0.99 at https://clef.aiurl.tw, with inference telemetry.

Both deployments expose the same System One request schema. Check their public
OpenAPI documents after changing a contract.

## Documentation and endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/docs` | Swagger UI with Try it out |
| `GET` | `/redoc` | ReDoc reference |
| `GET` | `/openapi.json` | OpenAPI 3.1 JSON document |
| `GET` | `/health` | API and model readiness |
| `POST` | `/v1/systemone` | Typed decision inference |

The OpenAPI server URL is relative (`/`) so the same document works behind a hostname or reverse proxy.

Primary live documentation: https://clef.create360.ai/openapi.json
Secondary live documentation: https://clef.aiurl.tw/openapi.json

## Request

`POST /v1/systemone` accepts JSON with a `state` and a non-empty `questions` map. `model` defaults to `clef-flash`. A question type is `noul`, `choice`, or `score`:

```json
{
  "model": "clef-flash",
  "state": {
    "invoice": {
      "vendor": "Acme",
      "total": 1250,
      "currency": "USD",
      "status": "overdue"
    }
  },
  "questions": {
    "status": {
      "type": "choice",
      "instructions": "What is the invoice status?",
      "criteria": {
        "paid": "Invoice is paid.",
        "overdue": "Invoice is past due.",
        "draft": "Invoice was not sent."
      }
    },
    "large": {
      "type": "noul",
      "instructions": "Is the total above 1000 USD?"
    },
    "priority": {
      "type": "score",
      "instructions": "How urgent is this invoice?",
      "criteria": ["Can wait", "This week", "Today"]
    }
  }
}
```

For image input, pass one or more `data:image/...;base64,...` values in `images`. This installation includes the multimodal projector. The server is configured with a 16,384-token total context across two parallel slots (8,192 tokens per slot) and 4,096-token logical and physical batches.

## Prompt token limit

Clef evaluates all questions jointly in one forward pass. The complete rendered prompt—including `state`, every question's instructions and criteria, and image tokens—must fit within the 4,096-token physical batch. The API does not split oversized requests automatically. Leave room for model-template and multimodal overhead; a large state plus many detailed options can exceed the limit before reaching 4,096 visible text tokens.

## Response

The response contains model, answers keyed by the submitted question IDs, and
usage. A noul answer includes the probability of true. A choice answer includes
the selected option, probabilities, and confidence. A score answer includes the
probability-weighted score, legend, probabilities, and confidence. Each legend
value preserves its original string, object, or array criterion.

Example structured score answer:

```json
{
  "type": "score",
  "score": 1.8,
  "legend": {"0": "Can wait", "1": {"level": "soon", "description": "This week"}, "2": ["Today"]},
  "probabilities": {"0": 0.1, "1": 0.0, "2": 0.9},
  "confidence": 0.8
}
```

## Errors

| Status | Meaning |
| --- | --- |
| 400 | The model rejected the request. |
| 422 | The request body failed API validation. |
| 500 | Backend inference failed; this can include input beyond the 4,096-token physical batch. |
| 501 | The model does not support the requested input. |
| 502 | The model backend returned an invalid response. |
| 503 | The model backend is unavailable. |
| 504 | The model backend timed out. |

## Local deployment

Release 1.0.2-legend builds on the deployed local/clef-flash-api:1.0.1-telemetry image. This preserves its telemetry module and runtime dependencies.

The API facade is reachable at `http://10.9.0.32:18100` from the private network and at `http://127.0.0.1:18100` on the host. The model service remains on the Docker network and is not exposed by this facade. The public `clef.create360.ai` hostname redirects HTTP requests to HTTPS; its ACME HTTP-01 token path is served from the mounted challenge webroot during certificate issuance.

Browser CORS allows all origins for `GET`, `POST`, and `OPTIONS`; credentialed browser requests and cookies are disabled. The API is public and does not require an API key.

```sh
docker network create clef-flash-api
docker network connect clef-flash-api clef-flash-service
docker build -t local/clef-flash-api:1.0.2-legend .
docker run -d \
  --name clef-flash-api \
  --restart unless-stopped \
  --network clef-flash-api \
  -e LLAMA_SERVER_URL=http://clef-flash-service:8080 \
  -v /usr/share/nginx/html:/acme-root:ro \
  -p 127.0.0.1:18100:8000 \
  -p 10.9.0.32:18100:8000 \
  local/clef-flash-api:1.0.2-legend
```

The public endpoint currently has no API-key authentication. Place it behind an access policy when callers need to be restricted.
