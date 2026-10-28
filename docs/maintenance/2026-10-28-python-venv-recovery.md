# 2026-10-28 Clef-Flash API recovery after Python upgrade

## Incident

On 2026-10-28, the production API on `10.9.0.99` (`david-ubuntu-2404`) was unavailable after the host moved to Ubuntu 26.04.1 LTS and Python 3.14.4. The `clef-flash.service` unit repeatedly restarted, and port `8099` had no listener.

The model container, `clef-flash-gguf-server`, remained healthy. Its `/health` endpoint and a direct `/v1/systemone` inference request returned successfully, which isolated the outage to the FastAPI facade.

## Cause

The API virtual environment had been created with Python 3.12. Its `pyvenv.cfg` recorded Python 3.12.3 and its installed packages were under `lib/python3.12/site-packages`. After the OS upgrade, `.venv/bin/python3` resolved to the system Python 3.14.4. The running interpreter therefore could not load the Python 3.12 packages.

The service journal showed:

```text
ModuleNotFoundError: No module named 'httpx'
```

The environment also lacked an importable `pip` module for the new interpreter. The API dependency pins were already present in `requirements.txt`.

## Recovery performed

The existing virtual environment and its old package directory were preserved. Bootstrap pip for the active interpreter, then install the service's pinned dependencies:

```bash
cd /home/david/clef-flash-service
.venv/bin/python -m ensurepip --upgrade --default-pip
.venv/bin/python -m pip install -r requirements.txt
sudo systemctl restart clef-flash.service
```

## Verification

- `systemctl is-active clef-flash.service` returned `active`.
- The API listened on `0.0.0.0:8099`.
- `http://127.0.0.1:8099/health` returned `status: healthy` and `backend_status: healthy`.
- A real `POST /v1/systemone` through port `8099` returned HTTP 200 in approximately 2.0 seconds.
- The service journal contained no error-level entries after recovery.

## Follow-up

The recovery repaired the live virtual environment. The repository's install/startup workflow should also bootstrap pip and reinstall pinned dependencies when a host Python upgrade makes the existing environment's packages unavailable. This record documents the production recovery; it does not claim that the startup workflow has been changed.
