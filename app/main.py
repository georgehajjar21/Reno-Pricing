from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Any
import json, os, math, csv, io
from fastapi.responses import PlainTextResponse

# -----------------------------
# Configuration
# -----------------------------
APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(os.path.dirname(APP_DIR), "data", "prices.json")

with open(DATA_PATH, "r", encoding="utf-8") as f:
    PRICE_CFG = json.load(f)

DEFAULT_REGION = PRICE_CFG.get("default_region", "Durham")
HST_RATE = PRICE_CFG.get("hst_rate", 0.13)

app = FastAPI(
    title="Reno Pricing App",
    version="1.0.0",
    servers=[{"url": "https://reno-pricing.onrender.com"}]
)

# -----------------------------
# Models
# -----------------------------
class Modifier(BaseModel):
    name: str
    factor: float

class EstimateIn(BaseModel):
    job_type: str
    inputs: Dict[str, float] = Field(default_factory=dict)
    complexity_modifiers: Dict[str, float] = Field(
        default_factory=dict,
        description="Optional modifiers such as furniture_moving:1.1, tight_access:1.15, premium_finish:1.2, etc."
    )
    region: Optional[str] = None
    include_tax: bool = True
    notes: Optional[str] = None

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

class LineItem(BaseModel):
    job_type: str
    inputs: Dict[str, float] = {}
    region: Optional[str] = None
    include_tax: bool = True

class BatchOut(BaseModel):
    lines: List[EstimateOut]
    total_subtotal: float
    total_tax: float
    total: float
    est_days_low: float
    est_days_high: float

# -----------------------------
# Utility functions
# -----------------------------
def round2(x: float) -> float:
    return float(f"{x:.2f}")

def get_region_multiplier(region: Optional[str]) -> float:
    region_key = (region or DEFAULT_REGION)
    return float(PRICE_CFG["region_multipliers"].get(region_key, 1.0))

def _days_for_line(job_type: str, inputs: Dict[str, float]) -> float:
    area = float(inputs.get("area_sqft", 0))
    coats = max(1.0, float(inputs.get("coats", 1)))
    fixtures = float(inputs.get("fixtures", 0))
    points = float(inputs.get("points", 0))
    hours = 0.0

    if "paint" in job_type.lower():
        days = max(0.25, (area / 350.0) * (1 + 0.1 * (coats - 1)))
    elif "floor" in job_type.lower():
        days = max(0.25, area / 200.0)
    elif "plumb" in job_type.lower():
        days = max(0.25, fixtures / 2.0)
    elif "elect" in job_type.lower():
        days = max(0.25, points / 6.0)
    elif "clean" in job_type.lower():
        hours = max(0.5, area / 500.0)
        days = hours / 8.0
    else:
        days = max(0.5, area / 250.0) if area > 0 else 0.5

    return float(f"{days:.2f}")

# -----------------------------
# Core Estimation Logic
# -----------------------------
def compute_estimate(job_type: str, inputs: Dict[str, float], region: Optional[str], include_tax: bool, complexity_modifiers: Optional[Dict[str, float]] = None) -> EstimateOut:
    base = PRICE_CFG["base_rates"].get(job_type)
    if not base:
        raise HTTPException(status_code=400, detail=f"Unknown job_type '{job_type}'")

    multiplier = get_region_multiplier(region)
    labor = 0.0
    materials = 0.0

    # Simple pricing logic based on available keys
    if "per_sqft" in base:
        area = float(inputs.get("area_sqft", 0))
        base_cost = area * base["per_sqft"]
        labor = base_cost * (1.0 - base["materials_pct"])
        materials = base_cost * base["materials_pct"]
    elif "per_fixture" in base:
        fixtures = float(inputs.get("fixtures", 1))
        base_cost = fixtures * base["per_fixture"]
        labor = base_cost * (1.0 - base["materials_pct"])
        materials = base_cost * base["materials_pct"]
    elif "per_point" in base:
        points = float(inputs.get("points", 1))
        base_cost = points * base["per_point"]
        labor = base_cost * (1.0 - base["materials_pct"])
        materials = base_cost * base["materials_pct"]
    elif "per_unit" in base:
        units = float(inputs.get("units", 1))
        base_cost = units * base["per_unit"]
        labor = base_cost * (1.0 - base["materials_pct"])
        materials = base_cost * base["materials_pct"]
    elif "per_linear_ft" in base:
        length = float(inputs.get("linear_ft", 0))
        base_cost = length * base["per_linear_ft"]
        labor = base_cost * (1.0 - base["materials_pct"])
        materials = base_cost * base["materials_pct"]
    elif "per_hr" in base:
        hours = float(inputs.get("hours", 1))
        base_cost = hours * base["per_hr"]
        labor = base_cost * (1.0 - base["materials_pct"])
        materials = base_cost * base["materials_pct"]
    elif "per_project" in base:
        base_cost = base["per_project"]
        labor = base_cost * (1.0 - base["materials_pct"])
        materials = base_cost * base["materials_pct"]
    else:
        raise HTTPException(status_code=400, detail=f"No recognized rate type for {job_type}")

    # Apply complexity modifiers if present
    complexity_factor = math.prod((complexity_modifiers or {}).values()) if complexity_modifiers else 1.0
    labor *= complexity_factor
    materials *= complexity_factor

    subtotal = (labor + materials) * multiplier
    tax = subtotal * HST_RATE if include_tax else 0.0
    total = subtotal + tax

    notes = f"Complexity modifiers: {', '.join([f'{k}×{v}' for k,v in (complexity_modifiers or {}).items()]) or 'None'}."

    return EstimateOut(
        job_type=job_type,
        region=(region or DEFAULT_REGION),
        labor=round2(labor * multiplier),
        materials=round2(materials * multiplier),
        modifiers=[Modifier(name="region_multiplier", factor=round2(multiplier))],
        subtotal=round2(subtotal),
        tax=round2(tax),
        total=round2(total),
        currency="CAD",
        notes=notes
    )

# -----------------------------
# Routes
# -----------------------------
@app.get("/health")
def health():
    return {"ok": True, "version": "1.0.0"}

@app.get("/version")
def version():
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {"version": "1.0.0", "last_refreshed": data.get("last_refreshed", "unknown")}

@app.post("/estimate", response_model=EstimateOut)
def estimate(body: EstimateIn):
    return compute_estimate(body.job_type, body.inputs, body.region, body.include_tax, body.complexity_modifiers)

@app.post("/estimate_batch", response_model=BatchOut)
def estimate_batch(body: Dict[str, Any]):
    items = body.get("items", [])
    if not isinstance(items, list) or not items:
        raise HTTPException(400, "Provide 'items': [ {job_type, inputs, region, include_tax}, ... ]")

    lines: List[EstimateOut] = []
    day_list: List[float] = []

    for it in items:
        est = compute_estimate(
            it.get("job_type", ""),
            it.get("inputs", {}),
            it.get("region"),
            bool(it.get("include_tax", True)),
            it.get("complexity_modifiers", {})
        )
        lines.append(est)
        day_list.append(_days_for_line(it.get("job_type",""), it.get("inputs", {})))

    total_subtotal = float(f"{sum(x.subtotal for x in lines):.2f}")
    total_tax = float(f"{sum(x.tax for x in lines):.2f}")
    total = float(f"{sum(x.total for x in lines):.2f}")

    trade_count = max(1, len(day_list))
    raw_days = sum(day_list)
    overlap_factor = max(0.65, 1.0 - 0.12*(trade_count-1))
    scaled = raw_days * overlap_factor

    return BatchOut(
        lines=lines,
        total_subtotal=total_subtotal,
        total_tax=total_tax,
        total=total,
        est_days_low=float(f"{scaled*0.9:.1f}"),
        est_days_high=float(f"{scaled*1.1:.1f}")
    )

@app.post("/workorder")
def workorder(body: EstimateIn):
    est = compute_estimate(body.job_type, body.inputs, body.region, body.include_tax, body.complexity_modifiers)
    html = f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Work Order - {est.job_type.title()}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; }}
    .card {{ border: 1px solid #ddd; padding: 16px; border-radius: 8px; max-width: 720px; }}
    h1 {{ margin-top: 0; }}
    .row {{ display: flex; justify-content: space-between; margin: 6px 0; }}
    .total {{ font-weight: bold; font-size: 1.2em; }}
    .muted {{ color: #666; font-size: 0.9em; }}
    @media print {{ .no-print {{ display:none; }} }}
    .btn {{ display:inline-block; padding:8px 12px; border:1px solid #333; border-radius:6px; text-decoration:none; color:#333; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Work Order — {est.job_type.title()}</h1>
    <div class="row"><div>Region</div><div>{est.region}</div></div>
    <div class="row"><div>Labor</div><div>CAD {est.labor:.2f}</div></div>
    <div class="row"><div>Materials</div><div>CAD {est.materials:.2f}</div></div>
    <div class="row"><div>Subtotal</div><div>CAD {est.subtotal:.2f}</div></div>
    <div class="row"><div>HST (13%)</div><div>CAD {est.tax:.2f}</div></div>
    <div class="row total"><div>Total</div><div>CAD {est.total:.2f}</div></div>
    <p class="muted">{est.notes}</p>
    <p class="no-print"><a class="btn" href="#" onclick="window.print()">Print</a></p>
  </div>
</body>
</html>
"""
    return Response(content=html, media_type="text/html")

@app.post("/export_csv")
def export_csv(body: Dict[str, Any]):
    items = body.get("items", [])
    if not isinstance(items, list) or not items:
        raise HTTPException(400, "Provide 'items': [...]")

    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["job_type","region","labor","materials","subtotal","tax","total","currency"])

    total_sub, total_tax, total_all = 0.0, 0.0, 0.0
    for it in items:
        est = compute_estimate(
            it.get("job_type",""), it.get("inputs", {}),
            it.get("region"), bool(it.get("include_tax", True)),
            it.get("complexity_modifiers", {})
        )
        w.writerow([est.job_type, est.region, est.labor, est.materials, est.subtotal, est.tax, est.total, est.currency])
        total_sub += est.subtotal; total_tax += est.tax; total_all += est.total

    w.writerow([])
    w.writerow(["TOTALS","","","",f"{total_sub:.2f}",f"{total_tax:.2f}",f"{total_all:.2f}","CAD"])
    return PlainTextResponse(content=output.getvalue(), media_type="text/csv")
@app.get("/refresh")
def force_refresh():
    import auto_refresh
    auto_refresh.refresh_prices()
    return {"status": "ok", "message": "Prices refreshed manually."}

# -----------------------------------------------------
# Auto refresh on startup (free Render-tier workaround)
# -----------------------------------------------------
@app.on_event("startup")
def auto_refresh_on_start():
    try:
        import auto_refresh
        auto_refresh.refresh_prices()
        print("✅ Auto-refresh executed at startup.")
    except Exception as e:
        print(f"⚠️ Auto-refresh failed: {e}")
