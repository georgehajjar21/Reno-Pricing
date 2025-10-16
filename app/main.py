
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Literal
import json, os, math

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

class EstimateIn(BaseModel):
    job_type: str = Field(..., description="Any type of renovation or construction work (e.g., painting, drywall, flooring, plumbing, framing, etc.)")
    inputs: Dict[str, float] = Field(
        default_factory=dict,
        description="Numeric parameters such as area_sqft, length_ft, rooms, fixtures, or any measurable units relevant to the job."
    )

    region: Optional[str] = Field(default=None, description="York, Durham, GTA, Ontario, Canada")
    include_tax: bool = True

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

@app.get("/health")
def health():
    return {"ok": True, "version": "1.0.0"}

def round2(x: float) -> float:
    return float(f"{x:.2f}")

def get_region_multiplier(region: Optional[str]) -> float:
    region_key = (region or DEFAULT_REGION)
    return float(PRICE_CFG["region_multipliers"].get(region_key, 1.0))

def compute_estimate(job_type: str, inputs: Dict[str, float], region: Optional[str], include_tax: bool) -> EstimateOut:
    base = PRICE_CFG["base_rates"].get(job_type)
    if not base:
        raise HTTPException(status_code=400, detail="Unknown job_type")

    multiplier = get_region_multiplier(region)
    labor = 0.0
    materials = 0.0

    # Simple models for each job_type (grade-6 friendly, predictable)
    if job_type == "painting":
        # area_sqft is wall/ceiling paintable area; coats increases labor a bit
        area = float(inputs.get("area_sqft", 0))
        coats = max(1.0, float(inputs.get("coats", 1)))
        base_cost = area * base["per_sqft"]
        labor = base_cost * (0.8 + 0.1 * (coats - 1))  # extra coats add time
        materials = base_cost * base["materials_pct"] * coats

    elif job_type == "flooring":
        area = float(inputs.get("area_sqft", 0))
        base_cost = area * base["per_sqft"]
        labor = base_cost * (1.0 - base["materials_pct"])
        materials = base_cost * base["materials_pct"]

    elif job_type == "plumbing":
        fixtures = float(inputs.get("fixtures", 1))
        difficulty = max(1.0, float(inputs.get("difficulty", 1)))  # 1 = normal
        base_cost = fixtures * base["per_fixture"] * difficulty
        labor = base_cost * (1.0 - base["materials_pct"])
        materials = base_cost * base["materials_pct"]

    elif job_type == "electrical":
        points = float(inputs.get("points", 1))  # outlets/switches/lights
        base_cost = points * base["per_point"]
        labor = base_cost * (1.0 - base["materials_pct"])
        materials = base_cost * base["materials_pct"]

    elif job_type == "cleaning":
        area = float(inputs.get("area_sqft", 0))
        base_cost = area * base["per_sqft"]
        labor = base_cost * (1.0 - base["materials_pct"])
        materials = base_cost * base["materials_pct"]

    # Apply region multiplier to labor+materials
    subtotal = (labor + materials) * multiplier
    tax = subtotal * HST_RATE if include_tax else 0.0
    total = subtotal + tax

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
        notes="Simple price list model. HST 13% when include_tax=true."
    )

@app.post("/estimate", response_model=EstimateOut)
def estimate(body: EstimateIn):
    return compute_estimate(body.job_type, body.inputs, body.region, body.include_tax)

@app.post("/workorder")
def workorder(body: EstimateIn):
    est = compute_estimate(body.job_type, body.inputs, body.region, body.include_tax)
    # Very simple printable HTML
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
    .mono {{ font-family: Consolas, monospace; }}
    @media print {{ .no-print {{ display:none; }} }}
    .btn {{ display:inline-block; padding:8px 12px; border:1px solid #333; border-radius:6px; text-decoration:none; color:#333; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>Work Order — {est.job_type.title()}</h1>
    <div class="row"><div>Region</div><div class="mono">{est.region}</div></div>
    <div class="row"><div>Labor</div><div class="mono">CAD {est.labor:.2f}</div></div>
    <div class="row"><div>Materials</div><div class="mono">CAD {est.materials:.2f}</div></div>
    <div class="row"><div>Subtotal</div><div class="mono">CAD {est.subtotal:.2f}</div></div>
    <div class="row"><div>HST (13%)</div><div class="mono">CAD {est.tax:.2f}</div></div>
    <div class="row total"><div>Total</div><div class="mono">CAD {est.total:.2f}</div></div>
    <p class="muted">Notes: {est.notes}</p>
    <p class="no-print">
      <a class="btn" href="#" onclick="window.print()">Print</a>
    </p>
  </div>
</body>
</html>
"""
    return Response(content=html, media_type="text/html")
