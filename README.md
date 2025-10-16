
# Reno Pricing App (CAD, York/Durham/GTA)

A simple web app that gives price estimates for home jobs (painting, flooring, plumbing, electrical, cleaning). 
It is built with FastAPI and ready for **Render.com**.

## What you get
- A web link where you can send details and get a price.
- JSON results that are easy to print or paste into Excel.
- A printable Work Order page.

## Quick Start (Render — Option B)

1. Go to https://render.com and **Sign Up / Log In**.
2. Click **New +** → **Web Service**.
3. Choose **"Public Git repository"** → then **Switch to "Manual Deploy"** (Upload from a folder/zip).
4. Click **Upload** and upload the ZIP of this folder.
5. For **Environment** choose **Python** (Render will detect it).
6. Render will auto-run:
   - Build: `pip install -r requirements.txt`
   - Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
7. Wait until it shows **Live**. You’ll get a public URL like: 
   `https://reno-pricing-app.onrender.com`
8. Try the docs page: **/docs** (for testing).
9. Try a price estimate with curl or the docs UI.

## API Endpoints

- `GET /health` → check if it runs.
- `POST /estimate` → send job details, get price JSON back.
- `POST /workorder` → send job details, get a printable HTML page.

### Example Request (curl)
```
curl -X POST https://YOUR-RENDER-URL/estimate   -H "Content-Type: application/json"   -d '{
        "job_type": "painting",
        "inputs": {"area_sqft": 400, "rooms": 2, "coats": 2},
        "region": "GTA",
        "include_tax": true
      }'
```

### Example Response
```json
{
  "job_type": "painting",
  "region": "GTA",
  "labor": 920.0,
  "materials": 230.0,
  "modifiers": [{"name": "region_multiplier", "factor": 1.05}],
  "subtotal": 1150.0,
  "tax": 149.5,
  "total": 1299.5,
  "currency": "CAD",
  "notes": "Simple price list model. HST 13% when include_tax=true."
}
```

## Taxes
- Uses **Ontario HST = 13%** when `include_tax=true`.

## Local Areas Covered
- York, Durham, GTA (Ontario, Canada) — all **CAD**.

## Support
- If you see an error on Render, open **Logs**. 
- Common fixes: make sure `requirements.txt` is present and the start command matches above.
