# ============================================================
#  GLEBANY RENO PRICING APP — FINAL (v1.1.0)
#  - Health: /health, /healthz, /readyz
#  - One /estimate accepts single trade OR items[]
#  - API key (x-api-key) optional; enable via env
#  - Rate limit (RPS) optional; enable via env
#  - Absolute PRICES_PATH fallback for Render
#  - Unlimited trades; region & HST; modifiers; timelines
#  - HTML Work Order; CSV export; GPT-ready OpenAPI
# ============================================================

from fastapi import FastAPI, HTTPException, Response, Header, Request
from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Union
import json, os, datetime, io, csv, time
from threading import Lock

# ---------------------- CONFIG ------------------------------
ENV_PRICES_PATH = os.getenv("PRICES_PATH")
DATA_PATH = ENV_PRICES_PATH or "/opt/render/project/src/data/prices.json"  # safe for Render
API_KEYS_RAW = os.getenv("API_KEYS", "").strip()
VALID_KEYS = {k.strip() for k in API_KEYS_RAW.split(",")} - {""}
RATE_LIMIT_RPS = float(os.getenv("RATE_LIMIT_RPS", "0"))  # 0 = off

def load_prices():
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] Could not load prices.json at {DATA_PATH}: {e}")
        return {
            "schema_version": "1.0",
            "version": "fallback",
            "updated_at": str(datetime.date.today()),
            "sources": [],
            "default_region": "Durham",
            "hst_rate": 0.13,
            "region_multipliers": {"Durham": 1.0},
            "base_rates": {},
            "productivity": {},
            "crew_size": {}
        }

PRICE_CFG = load_prices()
DEFAULT_REGION = PRICE_CFG.get("default_region", "Durham")
HST_RATE = PRICE_CFG.get("hst_rate", 0.13)

# simple in-process rate limiter
_last_req_times = []
_times_lock = Lock()

def rate_limit_ok() -> bool:
    if RATE_LIMIT_RPS <= 0:
        return True
    now = time.time()
    window = 1.0
    with _times_lock:
        # drop old
        while _last_req_times and (now - _last_req_times[0]) > window:
            _last_req_times.pop(0)
        if len(_last_req_times) >= RATE_LIMIT_RPS:
            return False
        _last_req_times.append(now)
    return True

# ---------------------- FASTAPI APP --------------------------
app = FastAPI(
    title="Reno Pricing App",
    version="1.1.0",
    servers=[{"url": "https://reno-pricing.onrender.com"}],
    description="Internal Renovation Pricing API (Glebany)."
)

# ---------------------- MODELS -------------------------------
class EstimateIn(BaseModel):
    job_type: str = Field(..., description="Any renovation trade.")
    inputs: Dict[str, float] = Field(default_factory=dict, description="Numbers only (e.g., area_sqft, per_point, per_fixture).")
    region: Optional[str] = Field(default=None, description="Durham, York, GTA, Ontario, Canada")
    include_tax: bool = True
    complexity_modifiers: Optional[Dict[str, float]] = None  # numeric multipliers

class Modifier(BaseModel):
    name: str; factor: float

class EstimateOut(BaseModel):
    job_type: str
    region: str
    labor: float
    materials: float
    modifiers: List[Modifier]
    subtotal: float
    tax: float
    total: float
    currency: str = "CAD"
    notes: Optional[str] = None
    est_days_low: Optional[int] = None
    est_days_high: Optional[int] = None

class BatchIn(BaseModel):
    items: List[EstimateIn]

class BatchOut(BaseModel):
    estimates: List[EstimateOut]
    total_subtotal: float
    total_tax: float
    total_total: float
    est_days_low: int
    est_days_high: int

# ---------------------- HELPERS ------------------------------
def round2(x: float) -> float: return float(f"{x:.2f}")

def get_region_multiplier(region: Optional[str]) -> float:
    return float(PRICE_CFG.get("region_multipliers", {}).get(region or DEFAULT_REGION, 1.0))

def choose_cost_key(base: Dict[str, float]) -> str:
    for k in base.keys():
        if k != "materials_pct":
            return k
    return "per_sqft"  # fallback

def compute_estimate(job_type: str, inputs: Dict[str, float], region: Optional[str],
                     include_tax: bool, complexity_modifiers: Optional[Dict[str, float]] = None) -> EstimateOut:
    # modifiers: ensure dict
    if complexity_modifiers is None or isinstance(complexity_modifiers, list):
        complexity_modifiers = {}

    # base lookup or smart defaults
    base = PRICE_CFG.get("base_rates", {}).get(job_type)
    if not base:
        j = job_type.lower()
        if "plumb" in j: base = {"per_fixture": 275.0, "materials_pct": 0.40}
        elif "elect" in j: base = {"per_point": 195.0, "materials_pct": 0.35}
        elif "paint" in j: base = {"per_sqft": 3.5, "materials_pct": 0.20}
        elif "tile"  in j: base = {"per_sqft": 10.0, "materials_pct": 0.40}
        elif "drywall" in j: base = {"per_sqft": 6.0, "materials_pct": 0.30}
        else:             base = {"per_sqft": 10.0, "materials_pct": 0.35}

    key = choose_cost_key(base)
    qty = float(inputs.get(key, 0))
    base_cost = qty * float(base[key])
    labor = base_cost * (1.0 - float(base["materials_pct"]))
    materials = base_cost * float(base["materials_pct"])

    subtotal = (labor + materials) * get_region_multiplier(region)
    modifiers = [Modifier(name="region_multiplier", factor=get_region_multiplier(region))]

    # apply numeric modifiers
    for name, factor in (complexity_modifiers or {}).items():
        if isinstance(factor, (int, float)):
            subtotal *= float(factor)
            modifiers.append(Modifier(name=name, factor=round2(float(factor))))

    tax = subtotal * HST_RATE if include_tax else 0.0
    total = subtotal + tax

    # duration scaling (simple, grade-6: ~300 sqft/day or equivalent units/day)
    base_days = max(1, qty / 300.0) if qty > 0 else 1

    return EstimateOut(
        job_type=job_type,
        region=region or DEFAULT_REGION,
        labor=round2(labor),
        materials=round2(materials),
        modifiers=modifiers,
        subtotal=round2(subtotal),
        tax=round2(tax),
        total=round2(total),
        currency="CAD",
        notes="Internal estimate.",
        est_days_low=round(base_days),
        est_days_high=round(base_days * 1.5)
    )

def auth_check(x_api_key: Optional[str]):
    if not VALID_KEYS:
        return  # auth disabled
    if x_api_key not in VALID_KEYS:
        raise HTTPException(status_code=401, detail="Unauthorized: invalid x-api-key")

def rate_check():
    if not rate_limit_ok():
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

# ---------------------- ROUTES -------------------------------
@app.get("/health")
def health(): return {"ok": True, "version": app.version}

@app.get("/healthz")
def healthz(): return {"status": "ok", "version": app.version}

@app.get("/readyz")
def readyz():
    # ready when prices loaded
    ready = bool(PRICE_CFG.get("region_multipliers")) and bool(PRICE_CFG.get("base_rates") is not None)
    return {"status": "ready" if ready else "not_ready"}

@app.get("/version")
def version():
    return {
        "schema_version": PRICE_CFG.get("schema_version", "1.0"),
        "version": PRICE_CFG.get("version", "unknown"),
        "updated_at": PRICE_CFG.get("updated_at", "unknown"),
        "sources": PRICE_CFG.get("sources", [])
    }

@app.get("/refresh")
def refresh():
    global PRICE_CFG
    PRICE_CFG = load_prices()
    return {"ok": True, "refreshed_at": str(datetime.datetime.now())}

# Accept single-trade or batch under the SAME endpoint (per PDF)
@app.post("/estimate", response_model=Union[EstimateOut, BatchOut])
def estimate(
    body: Union[EstimateIn, BatchIn],
    request: Request,
    x_api_key: Optional[str] = Header(default=None)
):
    auth_check(x_api_key)
    rate_check()

    # batch
    if isinstance(body, dict) or hasattr(body, "items"):  # FastAPI passes model, be safe
        pass  # handled by typing

    if isinstance(body, BatchIn):
        results = [
            compute_estimate(i.job_type, i.inputs, i.region, i.include_tax, i.complexity_modifiers)
            for i in body.items
        ]
        return BatchOut(
            estimates=results,
            total_subtotal=round2(sum(e.subtotal for e in results)),
            total_tax=round2(sum(e.tax for e in results)),
            total_total=round2(sum(e.total for e in results)),
            est_days_low=sum(e.est_days_low for e in results),
            est_days_high=sum(e.est_days_high for e in results),
        )

    # single
    if isinstance(body, EstimateIn):
        return compute_estimate(body.job_type, body.inputs, body.region, body.include_tax, body.complexity_modifiers)

    # If FastAPI sent a dict-like unexpectedly, try to detect items
    payload = body  # type: ignore
    if isinstance(payload, dict) and "items" in payload:
        items = [EstimateIn(**it) for it in payload["items"]]
        return estimate(BatchIn(items=items), request, x_api_key)

    raise HTTPException(status_code=400, detail="Invalid request body")

@app.post("/workorder")
def workorder(body: EstimateIn, x_api_key: Optional[str] = Header(default=None)):
    auth_check(x_api_key); rate_check()
    e = compute_estimate(body.job_type, body.inputs, body.region, body.include_tax, body.complexity_modifiers)
    html = f"""<!doctype html><html><head><meta charset="utf-8">
    <title>Work Order - {e.job_type.title()}</title></head>
    <body style='font-family:Arial;padding:24px;'>
      <h1>Work Order — {e.job_type.title()}</h1>
      <p><b>Region:</b> {e.region}</p>
      <p><b>Labor:</b> CAD {e.labor:.2f}<br>
         <b>Materials:</b> CAD {e.materials:.2f}<br>
         <b>Subtotal:</b> CAD {e.subtotal:.2f}<br>
         <b>Tax:</b> CAD {e.tax:.2f}<br>
         <b>Total:</b> CAD {e.total:.2f}<br>
         <b>Duration:</b> {e.est_days_low}–{e.est_days_high} days</p>
      <p><i>Internal work order.</i></p>
      <hr><button onclick="window.print()">Print</button>
    </body></html>"""
    return Response(content=html, media_type="text/html")

# PDF mentions /workorder/html — keep an alias for clarity
@app.post("/workorder/html")
def workorder_html(body: EstimateIn, x_api_key: Optional[str] = Header(default=None)):
    return workorder(body, x_api_key)

@app.post("/export_csv")
def export_csv(body: BatchIn, x_api_key: Optional[str] = Header(default=None)):
    auth_check(x_api_key); rate_check()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Trade", "Region", "Labor", "Materials", "Subtotal", "Tax", "Total", "Duration (days)"])
    for e in [
        compute_estimate(i.job_type, i.inputs, i.region, i.include_tax, i.complexity_modifiers)
        for i in body.items
    ]:
        writer.writerow([e.job_type, e.region, e.labor, e.materials, e.subtotal, e.tax, e.total,
                         f"{e.est_days_low}-{e.est_days_high}"])
    return Response(content=output.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=estimates.csv"})
