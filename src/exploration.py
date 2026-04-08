import json
import random

from data.client import USPTOClient
from data.fetch import fetch_ipr_decisions, fetch_ipr_proceedings

client = USPTOClient()
proceedings = fetch_ipr_proceedings(client=client)
#decisions = fetch_ipr_decisions(client=client)

# Fetch documents for a random trial to explore the structure
#sample_trial = random.choice(proceedings["trial_number"].dropna().unique())
#print(f"\n=== Documents for {sample_trial} ===")
#docs_response = client.get_trial_documents(sample_trial)
#print(json.dumps(docs_response, indent=2, default=str)[:5000])

# Explore decisions mentioning key discretionary denial concepts
for term in ["Fintiv", "Sotera", "325(d)"]:
    print(f"\n=== Decisions mentioning '{term}' ===")
    resp = client.search_decisions(query=term, offset=0, limit=1)
    print(f"Total count: {resp.get('count', 0)}")
    docs = resp.get("patentTrialDocumentDataBag", [])
    if docs:
        print(json.dumps(docs[0], indent=2, default=str)[:3000])

breakpoint()
