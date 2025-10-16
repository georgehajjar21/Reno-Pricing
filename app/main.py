from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Literal, Any
import json, os, math

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(os.path.dirname(APP_DIR), "data", "prices.json")

with open(DATA_PATH, "r", encoding="utf-8") as f:
    PRICE_CFG = json.load(f)

DEFAULT_REGION = PRICE_CFG.get("default_region", "Durham")
HST_RATE = PRICE_CFG.get("hst_rate", 0.13)

app = FastAPI(title="Reno Pricing App", version="1.0.0")

# -------------------- MODELS --------------------
class Modifier(BaseModel):
    name: str
    factor: float

class EstimateIn(BaseModel):
    job_type: str
    inputs: Dict[str, float] = Field(default_factory=dict)
    complexity_modifiers: Dict[str, float] = Field(default_factory=dict)
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

class BatchIn(BaseModel):
    items: List[EstimateIn]

class BatchOut(BaseModel):
    lines: List[EstimateOut]
    total_subtotal: float
    total_tax: float
    total: float
    est_days_low: float
    est_days_high: float

# -------------------- HELPERS --------------------
def round2(x: float) -> float:
    return float(f"{x:.2f}")

def get_region_multiplier(region: Optional[str]) -> float:
    region_key = (region or DEFAULT_REGION)
    return float(PRICE_CFG["region_multipliers"].get(region_key, 1.0))

def _days_for_line(job_type: str, inputs: Dict[str, float]) -> float:
    base = PRICE_CFG["base_rates"].get(job_type, {})
    if not base:
        return 1.0
    area = inputs.get("area_sqft", 100)
    if area < 50: return 0.5
    elif area < 200: return 1.0
    elif area < 500: return 1.5
    elif area < 1000: return 2.5
    elif area < 2000: return 4.0
    else: return 6.0

def compute_estimate(job_type: str, inputs: Dict[str, float], region: Optional[str], include_tax: bool, complexity_modifiers: Optional[Dict[str, float]] = None) -> EstimateOut:
    base = PRICE_CFG["base_rates"].get(job_type)
    if not base:
        raise HTTPException(status_code=400, detail=f"Unknown job_type: {job_type}")

    multiplier = get_region_multiplier(region)
    labor = 0.0
    materials = 0.0

    if job_type in ("painting", "flooring", "tiling", "drywall", "siding", "roofing", "deck_building", "fence_install", "patio_stone", "landscaping"):
        area = float(inputs.get("area_sqft", 0))
        unit_price = base.get("per_sqft", 0)
        base_cost = area * unit_price
        labor = base_cost * (1 - base["materials_pct"])
        materials = base_cost * base["materials_pct"]

    elif "linear_ft" in inputs:
        lf = float(inputs["linear_ft"])
        unit_price = base.get("per_linear_ft", 0)
        base_cost = lf * unit_price
        labor = base_cost * (1 - base["materials_pct"])
        materials = base_cost * base["materials_pct"]

    elif "fixtures" in inputs:
        fix = float(inputs["fixtures"])
        unit_price = base.get("per_fixture", 0)
        base_cost = fix * unit_price
        labor = base_cost * (1 - base["materials_pct"])
        materials = base_cost * base["materials_pct"]

    elif "points" in inputs:
        pts = float(inputs["points"])
        unit_price = base.get("per_point", 0)
        base_cost = pts * unit_price
        labor = base_cost * (1 - base["materials_pct"])
        materials = base_cost * base["materials_pct"]

    elif "per_unit" in base:
        units = float(inputs.get("units", 1))
        unit_price = base["per_unit"]
        base_cost = units * unit_price
        labor = base_cost * (1 - base["materials_pct"])
        materials = base_cost * base["materials_pct"]

    elif "per_project" in base:
        base_cost = base["per_project"]
        labor = base_cost * (1 - base["materials_pct"])
        materials = base_cost * base["materials_pct"]

    else:
        raise HTTPException(status_code=400, detail=f"Unsupported input type for {job_type}")

    # Apply complexity modifiers
    total_modifier = 1.0
    if complexity_modifiers:
        for val in complexity_modifiers.values():
            total_modifier *= val

    labor *= total_modifier
    materials *= total_modifier
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
        notes=notes
    )

# -------------------- ROUTES --------------------
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
def estimate_batch(body: BatchIn):
    items = body.items
    if not items:
        raise HTTPException(400, "Provide 'items': [...]")
    lines: List[EstimateOut] = []
    day_list: List[float] = []
    for it in items:
        est = compute_estimate(it.job_type, it.inputs, it.region, it.include_tax, it.complexity_modifiers)
        lines.append(est)
        day_list.append(_days_for_line(it.job_type, it.inputs))
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
    <html><head><meta charset='utf-8'><title>Work Order - {est.job_type}</title></head>
    <body style='font-family:Arial;margin:24px'>
    <h2>Work Order — {est.job_type.title()}</h2>
    <p>Region: {est.region}</p>
    <p>Labor: CAD {est.labor:.2f}</p>
    <p>Materials: CAD {est.materials:.2f}</p>
    <p>Subtotal: CAD {est.subtotal:.2f}</p>
    <p>HST (13%): CAD {est.tax:.2f}</p>
    <p><b>Total: CAD {est.total:.2f}</b></p>
    <p>{est.notes}</p></body></html>
    """
    return Response(content=html, media_type="text/html")

@app.post("/export_csv")
def export_csv(body: BatchIn):
    import io, csv
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Job Type", "Region", "Labor", "Materials", "Subtotal", "Tax", "Total"])
    for line in body.items:
        est = compute_estimate(line.job_type, line.inputs, line.region, line.include_tax, line.complexity_modifiers)
        writer.writerow([est.job_type, est.region, est.labor, est.materials, est.subtotal, est.tax, est.total])
    return PlainTextResponse(content=output.getvalue(), media_type="text/csv")

@app.get("/refresh")
def force_refresh():
    import auto_refresh
    auto_refresh.refresh_prices()
    return {"status": "ok", "message": "Prices refreshed manually."}

@app.on_event("startup")
def auto_refresh_on_start():
    try:
        import auto_refresh
        auto_refresh.refresh_prices()
        print("✅ Auto-refresh executed at startup.")
    except Exception as e:
        print(f"⚠️ Auto-refresh failed: {e}")
