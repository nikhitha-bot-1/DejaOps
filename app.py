"""DejaOps: an on-call copilot whose recommendations are shaped by remembered outcomes."""
import asyncio, json, os, re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from seed import FAMILIES, SEED

load_dotenv()
STATE_FILE = "state.json"
now = lambda days=0: (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def fresh():
    base = os.getenv("HINDSIGHT_BANK", "dejaops")
    return {"stats": {}, "incidents": {}, "hits": [], "seeded": False, "bank": f"{base}-{int(datetime.now().timestamp())}"}


S = json.load(open(STATE_FILE)) if os.path.exists(STATE_FILE) else fresh()
save = lambda: json.dump(S, open(STATE_FILE, "w"))


class Memory:
    """Hindsight when reachable; in-process keyword fallback otherwise (dev only).
    The Hindsight sync client drives asyncio internally, so every call runs on one dedicated
    thread that owns its own event loop (avoids "event loop is already running" under uvicorn)."""

    def __init__(self):
        self.hs, self.local, self.tried, self.banks = None, [], False, set()
        self.ex = ThreadPoolExecutor(1, initializer=lambda: asyncio.set_event_loop(asyncio.new_event_loop()))

    def _call(self, fn, *a, **k):
        return self.ex.submit(fn, *a, **k).result()

    def _ensure(self):
        if self.tried:
            return
        self.tried = True

        def connect():
            from hindsight_client import Hindsight
            url, key = os.getenv("HINDSIGHT_BASE_URL", "http://localhost:8888"), os.getenv("HINDSIGHT_API_KEY")
            hs = Hindsight(base_url=url, api_key=key) if key else Hindsight(base_url=url)
            try:
                hs.create_bank(bank_id=S["bank"], name=S["bank"])
            except Exception as e:  # already exists / auto-created: fine
                print("[dejaops] create_bank note:", repr(e))
            hs.retain(bank_id=S["bank"], content="DejaOps on-call memory initialised.", context="system", document_id="init")
            return hs

        try:
            self.hs = self._call(connect)
            self.banks.add(S["bank"])
        except Exception as e:
            print("[dejaops] Hindsight unavailable, using local fallback:", repr(e))

    def _bank(self):
        b = S["bank"]
        if self.hs and b not in self.banks:
            self.banks.add(b)
            try:
                self._call(self.hs.create_bank, bank_id=b, name=b)
            except Exception as e:
                print("[dejaops] create_bank note:", repr(e))

    @property
    def mode(self):
        self._ensure()
        return "hindsight" if self.hs else "local"

    def retain(self, text, doc, ctx, tags, ts, append=False):
        self.local.append(text)
        self._ensure()
        self._bank()
        if not self.hs:
            return
        try:
            try:
                self._call(self.hs.retain, bank_id=S["bank"], content=text, context=ctx, timestamp=ts, document_id=doc,
                           tags=tags, update_mode="append" if append else "replace")
            except TypeError:  # older client without tags/update_mode
                self._call(self.hs.retain, bank_id=S["bank"], content=text, context=ctx, timestamp=ts, document_id=doc)
        except Exception as e:
            print("[dejaops] retain failed:", repr(e))

    def recall(self, q, n=4):
        self._ensure()
        self._bank()
        if self.hs:
            try:
                r = self._call(self.hs.recall, bank_id=S["bank"], query=q)
                return [getattr(x, "text", str(x)) for x in r.results][:n]
            except Exception as e:
                print("[dejaops] recall failed:", repr(e))
        words = set(re.findall(r"\w+", q.lower()))
        overlap = lambda t: len(words & set(re.findall(r"\w+", t.lower())))
        return [t for t in sorted(self.local, key=lambda t: -overlap(t)) if overlap(t)][:n]

    def reflect(self, q):
        self._ensure()
        self._bank()
        if self.hs:
            try:
                return getattr(self._call(self.hs.reflect, bank_id=S["bank"], query=q), "text", None)
            except Exception as e:
                print("[dejaops] reflect failed:", repr(e))
        return None


mem = Memory()


# ---- belief ledger: structured view of "what worked", Laplace-smoothed ----
counts = lambda fam, fix: S["stats"].get(fam, {}).get(fix, [0, 0])
score = lambda fam, fix: (counts(fam, fix)[0] + 1) / (sum(counts(fam, fix)) + 2)


def record(fam, fix, ok):
    S["stats"].setdefault(fam, {}).setdefault(fix, [0, 0])[0 if ok else 1] += 1


def ranked(fam):
    order = sorted(FAMILIES[fam]["fixes"], key=lambda f: -score(fam, f))  # stable: playbook order breaks ties
    return [{"fix": f, "confidence": round(score(fam, f), 2), "worked": counts(fam, f)[0], "failed": counts(fam, f)[1]} for f in order]


def classify(text):
    t = text.lower()
    return next((k for k, d in FAMILIES.items() if any(w in t for w in d["kw"])), None)


def narrate(fixes):
    if not any(x["worked"] or x["failed"] for x in fixes):
        return "No history for this failure pattern yet, so I’m falling back to the standard playbook."
    bad = [x for x in fixes if x["failed"] > x["worked"]]
    s = f"Past incidents favour “{fixes[0]['fix']}” ({fixes[0]['worked']} worked, {fixes[0]['failed']} failed)."
    return s + (" Avoid " + ", ".join(f"“{x['fix']}” ({x['failed']} failed)" for x in bad) + "." if bad else "")


app = FastAPI(title="DejaOps")


class Alert(BaseModel):
    text: str
    service: str = "unknown-svc"


class Outcome(BaseModel):
    incident_id: str
    fix: str
    worked: bool


@app.post("/api/alert")
def alert(a: Alert):
    iid, fam = f"INC-{len(S['incidents']) + 201}", classify(a.text)
    recalled = mem.recall(a.text)  # recall BEFORE retaining so the alert can't recall itself
    S["incidents"][iid] = {"text": a.text, "service": a.service, "family": fam, "top": None, "judged": False}
    mem.retain(f"Alert on {a.service}: {a.text}", iid, "incident alert", [f"service:{a.service}"], now())
    if not fam:
        save()
        return {"id": iid, "family": None, "fixes": [], "recalled": recalled,
                "narrative": "Nothing in memory resembles this alert. I won’t guess: escalate, gather diagnostics, and log what fixes it so I learn."}
    fixes = ranked(fam)
    S["incidents"][iid]["top"] = fixes[0]["fix"]
    q = (f"New alert on {a.service}: {a.text}. From past incidents and their outcomes, which fix should the on-call try "
         "first and which should they avoid? Answer in two sentences and cite incident IDs.")
    save()
    return {"id": iid, "family": FAMILIES[fam]["name"], "fixes": fixes, "recalled": recalled, "narrative": mem.reflect(q) or narrate(fixes)}


@app.post("/api/outcome")
def outcome(o: Outcome):
    inc = S["incidents"].get(o.incident_id)
    if not inc or not inc["family"]:
        return {"ok": False}
    record(inc["family"], o.fix, o.worked)
    if not inc["judged"] and o.fix == inc["top"]:  # score the agent's first pick only
        S["hits"].append(int(o.worked))
        inc["judged"] = True
    verdict = "RESOLVED" if o.worked else "did NOT resolve"
    mem.retain(f"Outcome for {o.incident_id} ({inc['service']}): '{o.fix}' {verdict} the {FAMILIES[inc['family']]['name']} incident.",
               o.incident_id, "incident outcome", [f"service:{inc['service']}", f"family:{inc['family']}"], now(), append=True)
    save()
    return {"ok": True}


@app.post("/api/seed")
def seed():
    if not S["seeded"]:
        for i, (days, svc, fam, text, tries) in enumerate(SEED):
            iid = f"INC-{100 + i}"
            steps = "; ".join(f"{f} {'worked' if ok else 'failed'}" for f, ok in tries)
            mem.retain(f"Post-mortem {iid} on {svc} ({FAMILIES[fam]['name']}): {text}. Fix attempts: {steps}.",
                       iid, "incident postmortem", [f"service:{svc}", f"family:{fam}"], now(days))
            for f, ok in tries:
                record(fam, f, ok)
        S["seeded"] = True
        save()
    return {"ok": True}


@app.post("/api/reset")
def reset():
    S.clear(); S.update(fresh()); mem.local.clear(); save()  # new bank id = clean demo; old bank stays in Hindsight
    return {"ok": True}


@app.get("/api/state")
def state():
    curve, total = [], 0
    for i, h in enumerate(S["hits"], 1):
        total += h
        curve.append(round(total / i * 100))
    return {"mode": mem.mode, "bank": S["bank"], "seeded": S["seeded"], "curve": curve,
            "beliefs": {k: {"name": d["name"], "fixes": ranked(k)} for k, d in FAMILIES.items()},
            "incidents": [{"id": k, **v} for k, v in list(S["incidents"].items())[-6:]][::-1]}


app.mount("/static", StaticFiles(directory="static"), name="static")
app.get("/")(lambda: FileResponse("static/index.html"))
