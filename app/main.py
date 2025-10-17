from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field
from typing import Dict, List, Optional
import json, os, datetime, io, csv

# --------------------------------------------------------------------
# CONFIGURATION
# --------------------------------------------------------------------
APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(os.path.dirname(APP_DIR), "data", "prices.json")

def load_prices():
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

PRICE_CFG = load_prices()
DEFAULT_REGION = PRICE_CFG.get("default_region", "Durham")
HST_RATE = PRICE_CFG.get("hst_rate", 0.13)

# --------------------------------------------------------------------
# FASTAPI APP
# --------------------------------------------------------------------
app = FastAPI(
    title="Reno Pricing App",
    version="1.0.0",
    servers=[{"url": "https://reno-pricing.onrender.com"}],
    description="Dynamic internal pricing API for full home renovations."
)

# --------------------------------------------------------------------
# MODELS
# --------------------------------------------------------------------
class EstimateIn(BaseModel):
    job_type: str = Field(..., description="Any renovation or trade type.")
    inputs: Dict[str, float] = Field(default_factory=dict)
    region: Optional[str] = Field(default=None, description="Durham, York, GTA, Ontario, Canada")
    include_tax: bool = True
    complexity_modifiers: Optional[Dict[str, float]] = None

class Modifier(BaseModel):
    name: str
    factor: float

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

# --------------------------------------------------------------------
# UTILITIES
# --------------------------------------------------------------------
def round2(x: float) -> float:
    return float(f"{x:.2f}")

def get_region_multiplier(region: Optional[str]) -> float:
    return float(PRICE_CFG["region_multipliers"].get(region or DEFAULT_REGION, 1.0))

# --------------------------------------------------------------------
# MAIN ESTIMATOR
# --------------------------------------------------------------------
def compute_estimate(job_type: str, inputs: Dict[str, float], region: Optional[str],
                     include_tax: bool, complexity_modifiers: Optional[Dict[str, float]] = None) -> EstimateOut:

    # Handle invalid or list-type complexity_modifiers
    if complexity_modifiers is None or isinstance(complexity_modifiers, list):
        complexity_modifiers = {}

    base = PRICE_CFG["base_rates"].get(job_type)

    # Allow ANY trade — use defaults if not found
    if not base:
        job_lower = job_type.lower()
        if "plumb" in job_lower: base = {"per_fixture": 250, "materials_pct": 0.4}
        elif "elect" in job_lower: base = {"per_point": 175, "materials_pct": 0.35}
        elif "paint" in job_lower: base = {"per_sqft": 3.5, "materials_pct": 0.2}
        elif "tile" in job_lower: base = {"per_sqft": 10.0, "materials_pct": 0.4}
        else: base = {"per_sqft": 10.0, "materials_pct": 0.35}

    # Select the first cost key automatically
    key = next(iter(base.keys() - {"materials_pct"}))
    qty = float(inputs.get(key, 0))
    base_cost = qty * base[key]
    labor = base_cost * (1.0 - base["materials_pct"])
    materials = base_cost * base["materials_pct"]

    subtotal = (labor + materials) * get_region_multiplier(region)
    modifiers = [Modifier(name="region_multiplier", factor=get_region_multiplier(region))]

    # Apply numeric modifiers
    for name, factor in complexity_modifiers.items():
        if isinstance(factor, (int, float)):
            subtotal *= factor
            modifiers.append(Modifier(name=name, factor=round2(factor)))

    tax = subtotal * HST_RATE if include_tax else 0.0
    total = subtotal + tax

    # Duration scaling
    base_days = max(1, qty / 300)
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
        notes="Estimate generated for internal management.",
        est_days_low=round(base_days),
        est_days_high=round(base_days * 1.5)
    )

# --------------------------------------------------------------------
# ROUTES
# --------------------------------------------------------------------
@app.get("/health")
def health():
    return {"ok": True, "version": app.version}

@app.get("/version")
def version():
    return {"last_refreshed": PRICE_CFG.get("last_refreshed", "unknown")}

@app.get("/refresh")
def refresh():
    global PRICE_CFG
    PRICE_CFG = load_prices()
    return {"ok": True, "refreshed_at": str(datetime.datetime.now())}

@app.post("/estimate", response_model=EstimateOut)
def estimate(body: EstimateIn):
    return compute_estimate(body.job_type, body.inputs, body.region,
                            body.include_tax, body.complexity_modifiers)

@app.post("/estimate_batch", response_model=BatchOut)
def estimate_batch(body: BatchIn):
    results = [compute_estimate(i.job_type, i.inputs, i.region,
                                i.include_tax, i.complexity_modifiers) for i in body.items]
    return BatchOut(
        estimates=results,
        total_subtotal=round2(sum(e.subtotal for e in results)),
        total_tax=round2(sum(e.tax for e in results)),
        total_total=round2(sum(e.total for e in results)),
        est_days_low=sum(e.est_days_low for e in results),
        est_days_high=sum(e.est_days_high for e in results)
    )

@app.post("/workorder")
def workorder(body: EstimateIn):
    est = compute_estimate(body.job_type, body.inputs, body.region,
                           body.include_tax, body.complexity_modifiers)
    html = f"""
    <!doctype html><html><head><meta charset="utf-8">
    <title>Work Order - {est.job_type.title()}</title></head>
    <body style='font-family:Arial;padding:24px;'>
      <h1>Work Order — {est.job_type.title()}</h1>
      <p><b>Region:</b> {est.region}</p>
      <p><b>Labor:</b> CAD {est.labor:.2f}<br>
         <b>Materials:</b> CAD {est.materials:.2f}<br>
         <b>Subtotal:</b> CAD {est.subtotal:.2f}<br>
         <b>Tax:</b> CAD {est.tax:.2f}<br>
         <b>Total:</b> CAD {est.total:.2f}<br>
         <b>Duration:</b> {est.est_days_low}–{est.est_days_high} days</p>
      <p><b>Modifiers:</b> {[m.name for m in est.modifiers]}</p>
      <hr><button onclick="window.print()">Print</button>
    </body></html>"""
    return Response(content=html, media_type="text/html")

@app.post("/export_csv")
def export_csv(body: BatchIn):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Trade", "Region", "Labor", "Materials", "Subtotal", "Tax", "Total", "Duration (days)"])
    for e in [compute_estimate(i.job_type, i.inputs, i.region,
                               i.include_tax, i.complexity_modifiers) for i in body.items]:
        writer.writerow([e.job_type, e.region, e.labor, e.materials,
                         e.subtotal, e.tax, e.total, f"{e.est_days_low}-{e.est_days_high}"])
    return Response(content=output.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=estimates.csv"})
