# DejaOps: the on-call copilot that remembers what actually fixed it

When an alert fires, DejaOps recalls similar past incidents and ranks fixes by what *actually worked*, then learns from the outcome. A restart that fails twice stops being recommended.

## Run
```bash
pip install -r requirements.txt
cp .env.example .env        # add Hindsight Cloud URL + key, or run `hindsight-api` locally (needs an LLM key)
uvicorn app:app --reload    # http://localhost:8000
```
The header pill shows **Hindsight live** or **local fallback** (dev only; the submission must run on Hindsight).

## How Hindsight is used
| Step | Hindsight |
|---|---|
| Post-mortem / alert logged | `retain` with `document_id` = incident ID, `context`, `timestamp`, `tags` |
| Engineer marks a fix Worked/Failed | `retain` with `update_mode="append"` on the same incident document |
| New alert | `recall` for similar incidents, then `reflect` for a cited recommendation |
| Fresh demo | New bank per reset |

A small belief ledger (Laplace-smoothed success rates per fix) sits beside Hindsight to drive the ranking bars and the learning curve deterministically.

## 90-second demo
1. Click **Reset demo**. Fire the **Pool** alert: generic advice ("restart"). Click **Failed**.
2. Fire **Pool** again: restart drops, rollback rises. Click **Worked**.
3. Click **Load 8 past incidents**: confidence jumps across all families.
4. Fire **Unknown**: the agent admits it has no history instead of guessing.
5. Point at the learning curve.

## To verify before submitting
Exact `recall` tag filters, `reflect` parameters and bank mission/directives (see hindsight.vectorize.io/developer/api), and how Band AI desktop fits (required by the brief).
