#!/usr/bin/env python3
import uvicorn

from log import setup_logging

setup_logging()

from app import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765, log_config=None)
