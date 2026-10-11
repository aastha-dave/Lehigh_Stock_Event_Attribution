from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from app.domain.news_v2_02_claims import ClaimRecord
from app.infrastructure.llm import get_gemini_client

VERIFICATION_MODEL = "gemini-2.5-flash"


def verify_claims_for_episode(claims: tuple[ClaimRecord, ...]) -> tuple[ClaimRecord, ...]:
    """Batch fact-check all claims for one episode in a single Gemini call."""
    if not claims:
        return claims

    prompt = _build_verification_prompt(claims)
    client = get_gemini_client()

    try:
        response = client.chat.completions.create(
            model=VERIFICATION_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
            response_format={"type": "json_object"},
            reasoning_effort="none",
        )
        payload = json.loads(response.choices[0].message.content)
    except Exception as exc:  # noqa: BLE001
        print(f"[verification] Gemini batch verification failed, skipping: {exc}")
        return claims

    verdicts = {
        item.get("claim_id"): item
        for item in payload.get("verdicts", [])
        if isinstance(item, dict) and item.get("claim_id")
    }

    verified: list[ClaimRecord] = []
    for claim in claims:
        verdict = verdicts.get(claim.claim_id)
        if verdict is None:
            verified.append(claim)
            continue
        verified.append(
            replace(
                claim,
                verification_status=str(verdict.get("status") or "unreviewed"),
                verification_notes=str(verdict.get("notes") or "") or None,
            )
        )
    return tuple(verified)


def _build_verification_prompt(claims: tuple[ClaimRecord, ...]) -> str:
    claim_lines = []
    for claim in claims:
        claim_lines.append(
            f"- claim_id: {claim.claim_id}\n"
            f"  claim_text: {claim.claim_text}\n"
            f"  evidence_span: {claim.evidence_span}"
        )
    claims_block = "\n".join(claim_lines)

    return f"""
You are a fact-checking reviewer for financial-news claims that were already
extracted from articles by another system.

For each claim below, check whether the evidence_span actually supports the
claim_text. Flag anything that looks unsupported, exaggerated, or misattributed.

Return JSON only, in this exact shape:
{{
  "verdicts": [
    {{
      "claim_id": "string, must match one of the claim_id values below exactly",
      "status": "supported" | "unsupported" | "needs_review",
      "notes": "short string explaining the verdict, or empty string if supported"
    }}
  ]
}}

Claims to review:
{claims_block}
""".strip()