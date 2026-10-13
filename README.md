# Clef-Flash Decision API

This repository documents the primary production Decision API. The public API
is exposed through the external hostname `https://clef.create360.ai`.

`10.9.0.32` is the private internal host that runs the production containers.
It is not the public API address.

## Public API

- Swagger: https://clef.create360.ai/docs
- ReDoc: https://clef.create360.ai/redoc
- OpenAPI: https://clef.create360.ai/openapi.json
- Health: https://clef.create360.ai/health
- Inference: POST https://clef.create360.ai/v1/systemone

The public hostname routes to the API facade. Clients should use the public
hostname and should not depend on the private host address or loopback ports.

## Internal production deployment

The current production path on the private host is:

```text
External client
    |
    v
https://clef.create360.ai
    |
    v
clef-flash-api facade       127.0.0.1:18100 -> container:8000
    |
    v
clef-flash-service llama.cpp 127.0.0.1:18080 -> container:8080
```

Current runtime facts:

- Private host: `10.9.0.32`
- API facade container: `clef-flash-api`
- Model backend container: `clef-flash-service`
- API facade loopback port: `18100`
- Model backend loopback port: `18080`
- Current model: D3 `vllm-d3-flash-Q4_K_M.gguf`
- Current projector: `mmproj-vllm-d3-flash-Q8_0.gguf`
- GPU: NVIDIA RTX A4000

## Model switching

The host has a guarded switch helper. It keeps the public hostname and both
internal ports unchanged, waits for backend and API health, sends a System One
smoke request, and restores the previous model automatically when validation
fails.

```sh
ssh 10.9.0.32 /home/human/bin/clef-model-switch status
ssh 10.9.0.32 /home/human/bin/clef-model-switch d3
ssh 10.9.0.32 /home/human/bin/clef-model-switch clef
```

Use `d3` for the current production model and `clef` to switch back to the
rollback model.

## API contract

`POST /v1/systemone` accepts a JSON object with `state` and a non-empty
`questions` map. The question types are `choice`, `noul`, and `score`.

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
    "urgent": {
      "type": "noul",
      "instructions": "Does this require action today?"
    },
    "priority": {
      "type": "score",
      "instructions": "How urgent is this invoice?",
      "criteria": ["Routine", "Soon", "Today"]
    }
  }
}
```

The response contains `model`, `answers`, and `usage`. Choice answers include
`choice`, `probabilities`, and `confidence`. Noul answers include the
probability of true. Score answers include a probability-weighted score,
legend, probabilities, and confidence.

## Runtime limits

The model server uses 16,384 total context tokens across two parallel slots,
with 4,096-token logical and physical batches. The complete rendered request,
including state, instructions, criteria, and image tokens, must fit within one
4,096-token physical batch. The API does not split oversized requests.

## Repository notes

Model weights and virtual environments remain external to Git. Keep runtime
secrets outside the repository. Validate the public health endpoint and a
representative `/v1/systemone` request after deployment or model switching.
