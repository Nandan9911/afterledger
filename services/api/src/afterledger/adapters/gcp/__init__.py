"""Google Cloud adapters: the same ports as the local adapters, backed by Google Cloud services.

    Firestore          case state and researched knowledge          firestore.py
    Cloud Storage      papers, page images and filled forms         gcs.py
    Document AI        the OCR reading path                          documentai.py
    Cloud Translation  family copies and server-written words        translation.py
    Check grounding    claims against their sources                  grounding.py
    Google Search      live research search (through Gemini)         web.py
    Agent Search       search over a curated data store (optional)   web.py
    Cloud Tasks        durable background work (optional)            tasks.py
    BigQuery           agent and check traces for evaluation         bigquery.py

wiring.apply_gcp_adapters() swaps them in according to the settings; anything not configured keeps its local
adapter, and every swap is listed in deps.extras["services"] so the app can say what is live.
"""
