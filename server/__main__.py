"""Run the API:  python -m server   (HOST and PORT from the environment; defaults 0.0.0.0:8000)."""
import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run("server.main:app", host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8000")),
                proxy_headers=True,
                # whose X-Forwarded-For/-Proto to believe: only a proxy on this machine unless told otherwise
                # (compose.yaml sets "*", since there only nginx can reach the app and it overwrites the header)
                forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"),
                server_header=False, access_log=False)
