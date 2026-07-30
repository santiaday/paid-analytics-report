import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SERVICE = os.path.dirname(HERE)
# app/ modules (handler, brain_client, spend_to_clicks, emit) import as top-level.
sys.path.insert(0, os.path.join(SERVICE, "app"))
